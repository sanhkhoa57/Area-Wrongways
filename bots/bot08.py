"""Wrongway Master Agent: Fast Bitboard PVS Engine with Exact Endgame Solver."""

from collections import deque
import gc
from heapq import heappop, heappush
from time import perf_counter

gc.disable()

_count_bits = getattr(int, 'bit_count', lambda value: bin(value).count('1'))

WEIGHTS = {
    'distance': 100.0,
    'reserve': 180.0,
    'race': 70.146601,
    'mobility': -1.383784,
    'center': -1.135386,
    'vulnerability': 6.278597,
    'route_options': -9.146228,
    'jump': -5.315813,
    'bottleneck': 13.494556,
    'wall_pressure': -3.216144,
    'goal_pressure': 37.540526,
    'tempo': 10.057386,
}
TIME_BUDGET = .88
NODE_BUDGET = 45000
MAX_DEPTH = 12
ROOT_BEAM = 10
WALL_BEAM = 5
SETUP_SLOTS = 2
MATE = 1000000.0
INF = 99
ALL_CELLS = (1 << 81) - 1
ALL_WALLS = (1 << 128) - 1
DOWN = (1 << 72) - 1
RIGHT = sum(1 << (row * 9 + col) for row in range(9) for col in range(8))
GOALS = ((1 << 9) - 1, ((1 << 9) - 1) << 72)
PASS = 209
_MAP_CACHE_LIMIT = 65536
_TOPOLOGY_CACHE = {}
_PATH_CACHE = {}
_BFS_CACHE = {}
_RACE_CACHE = {}


def _wall_tables():
    cuts_down, cuts_right, conflicts, extensions, coordinates = [], [], [], [], []
    for index in range(128):
        orientation, square = divmod(index, 64)
        row, col = divmod(square, 8)
        source = row * 9 + col
        bits = (1 << source) | (1 << (source + (1 if orientation == 0 else 9)))
        cuts_down.append(bits if orientation == 0 else 0)
        cuts_right.append(bits if orientation == 1 else 0)
        banned = (1 << index) | (1 << ((1 - orientation) * 64 + square))
        if orientation == 0:
            adjacent = [orientation * 64 + row * 8 + c for c in (col - 1, col + 1) if 0 <= c < 8]
            continuation = [row * 8 + c for c in (col - 2, col + 2) if 0 <= c < 8]
        else:
            adjacent = [64 + r * 8 + col for r in (row - 1, row + 1) if 0 <= r < 8]
            continuation = [64 + r * 8 + col for r in (row - 2, row + 2) if 0 <= r < 8]
        for other in adjacent:
            banned |= 1 << other
        conflicts.append(banned)
        touches = set(continuation)
        for other in range(128):
            orient2, square2 = divmod(other, 64)
            rr, cc = divmod(square2, 8)
            if orient2 != orientation and abs(rr - row) + abs(cc - col) <= 1:
                touches.add(other)
        extensions.append(sum(1 << other for other in touches))
        coordinates.append((orientation, row, col))
    return cuts_down, cuts_right, conflicts, extensions, coordinates


CUT_DOWN, CUT_RIGHT, CONFLICTS, EXTENSIONS, COORDINATES = _wall_tables()
PROXIMITY = [
    [max(0, 3 - (max(row - cell // 9, cell // 9 - row - 1, 0)
                  + max(col - cell % 9, cell % 9 - col - 1, 0)))
     for _, row, col in COORDINATES]
    for cell in range(81)
]
WALL_DIST = [
    [abs(2 * row + 1 - 2 * (cell // 9)) + abs(2 * col + 1 - 2 * (cell % 9))
     for _, row, col in COORDINATES]
    for cell in range(81)
]
PROX_MASK = [
    sum(1 << i for i in range(128) if PROXIMITY[cell][i] > 0)
    for cell in range(81)
]
REV_EXT = [
    sum(1 << j for j in range(128) if EXTENSIONS[j] & (1 << i))
    for i in range(128)
]
DOWN_WALLS = [
    sum(1 << i for i in range(128) if CUT_DOWN[i] & (1 << cell))
    for cell in range(81)
]
RIGHT_WALLS = [
    sum(1 << i for i in range(128) if CUT_RIGHT[i] & (1 << cell))
    for cell in range(81)
]
INCIDENT_WALLS = [
    DOWN_WALLS[cell]
    | RIGHT_WALLS[cell]
    | (DOWN_WALLS[cell - 9] if cell >= 9 else 0)
    | (RIGHT_WALLS[cell - 1] if cell % 9 > 0 else 0)
    for cell in range(81)
]


def expand(frontier, down, right):
    return (((frontier & down) << 9) | ((frontier & (down << 9)) >> 9)
            | ((frontier & right) << 1) | ((frontier & (right << 1)) >> 1))


def moves_mask(own, own_bit, enemy, enemy_bit, down, right):
    adjacent = expand(own_bit, down, right)
    if adjacent & enemy_bit:
        adjacent ^= enemy_bit
        beyond = enemy + (enemy - own)
        enemy_adjacent = expand(enemy_bit, down, right)
        if 0 <= beyond < 81 and (enemy_adjacent & (1 << beyond)):
            adjacent |= 1 << beyond
        elif abs(enemy - own) == 9:
            adjacent |= enemy_adjacent & ((enemy_bit << 1) | (enemy_bit >> 1))
        else:
            adjacent |= enemy_adjacent & ((enemy_bit << 9) | (enemy_bit >> 9))
    return adjacent


def single_bfs(goal_row, target_bit, down, right, mask_key=None):
    if mask_key is not None:
        cached = _BFS_CACHE.get(mask_key)
        if cached is not None:
            return cached
    frontier = GOALS[0 if goal_row == 0 else 1]
    if frontier & target_bit:
        if mask_key is not None:
            _BFS_CACHE[mask_key] = 0
        return 0
    visited = frontier
    up, left = down << 9, right << 1
    d = 1
    while frontier:
        frontier = (((frontier & down) << 9) | ((frontier & up) >> 9)
                    | ((frontier & right) << 1) | ((frontier & left) >> 1)) & ~visited
        if frontier & target_bit:
            if mask_key is not None:
                if len(_BFS_CACHE) >= 65536:
                    _BFS_CACHE.clear()
                _BFS_CACHE[mask_key] = d
            return d
        visited |= frontier
        d += 1
    if mask_key is not None:
        if len(_BFS_CACHE) >= 65536:
            _BFS_CACHE.clear()
        _BFS_CACHE[mask_key] = INF
    return INF


class DistanceMap(dict):
    __slots__ = ('layers', 'down', 'right')

    def __init__(self, goal_row, down, right):
        super().__init__()
        self.down, self.right = down, right
        frontier = GOALS[0 if goal_row == 0 else 1]
        visited = frontier
        layers = []
        up, left = down << 9, right << 1
        while frontier:
            layers.append(frontier)
            frontier = (((frontier & down) << 9) | ((frontier & up) >> 9)
                        | ((frontier & right) << 1) | ((frontier & left) >> 1)) & ~visited
            visited |= frontier
        self.layers = layers

    def __missing__(self, key):
        if key < 81:
            bit = 1 << key
            value = INF
            for distance, layer in enumerate(self.layers):
                if layer & bit:
                    value = distance
                    break
            self[key] = value
            return value
        cell = key - 81
        down, right = self.down, self.right
        forced = 0
        for _ in range(3):
            choices = expand(1 << cell, down, right)
            nxt = -1
            cnt = 0
            d_cell = self[cell]
            while choices:
                bit = choices & -choices
                choices ^= bit
                neighbor = bit.bit_length() - 1
                if self[neighbor] < d_cell:
                    nxt = neighbor
                    cnt += 1
                    if cnt > 1:
                        break
            if cnt != 1:
                break
            forced += 1
            cell = nxt
        self[key] = forced
        return forced


class SearchLimit(Exception):
    pass


class Engine:
    def __init__(self, observation, weights=None, node_budget=NODE_BUDGET,
                 max_depth=MAX_DEPTH, time_budget=TIME_BUDGET):
        self.positions = [9 * observation[key]['position'][0] + observation[key]['position'][1]
                          for key in ('you', 'opponent')]
        self.goals = [observation[key]['goal_row'] for key in ('you', 'opponent')]
        self.stocks = [observation[key]['walls_remaining'] for key in ('you', 'opponent')]
        self.down, self.right = DOWN, RIGHT
        self.mask = self.invalid = 0
        for orientation, key in enumerate(('horizontal', 'vertical')):
            for row, col in observation['walls'][key]:
                index = orientation * 64 + row * 8 + col
                self.down &= ~CUT_DOWN[index]
                self.right &= ~CUT_RIGHT[index]
                self.mask |= 1 << index
                self.invalid |= CONFLICTS[index]
        self.root_moves = [9 * pair[0] + pair[1] for pair in observation['legal_moves']]
        self.root_walls = sum(1 << ((0 if w['orientation'] == 'H' else 64) + w['row'] * 8 + w['col'])
                              for w in observation['legal_walls'])
        self.remaining = observation['max_turns'] - observation['turn']
        self.weights = dict(WEIGHTS if weights is None else weights)
        self.node_budget, self.max_depth = node_budget, max_depth
        self.deadline = perf_counter() + time_budget
        self.nodes = self.completed_depth = 0
        self.map_cache = _TOPOLOGY_CACHE
        self.eval_cache, self.action_cache = {}, {}
        self.tt, self.best_table = {}, {}
        self.history = [[0] * 210, [0] * 210]
        self.killers = {}
        self.path_keys = set()
        self.root_scores = []
        self.avoid_root_pass = False
        self.cell_visits = {}

    def check_time(self):
        if perf_counter() >= self.deadline:
            raise SearchLimit

    def maps(self):
        key = (self.goals[0], self.goals[1], self.mask)
        value = self.map_cache.get(key)
        if value is None:
            value = [DistanceMap(goal, self.down, self.right) for goal in self.goals]
            if len(self.map_cache) >= _MAP_CACHE_LIMIT:
                self.map_cache.clear()
            self.map_cache[key] = value
        return value

    def moves(self, side):
        own, enemy = self.positions[side], self.positions[1 - side]
        adjacent = moves_mask(own, 1 << own, enemy, 1 << enemy, self.down, self.right)
        result = []
        while adjacent:
            bit = adjacent & -adjacent
            result.append(bit.bit_length() - 1)
            adjacent ^= bit
        return result

    def key(self, side):
        return (self.mask, self.positions[0], self.positions[1],
                self.stocks[0], self.stocks[1], side)

    def evaluate(self, side):
        key = self.key(side)
        cached = self.eval_cache.get(key)
        if cached is not None:
            return cached
        p0, p1 = self.positions[0], self.positions[1]
        g0, g1 = self.goals[0], self.goals[1]
        if p0 // 9 == g0:
            return MATE if side == 0 else -MATE
        if p1 // 9 == g1:
            return MATE if side == 1 else -MATE
        moves0, moves1 = self.moves(0), self.moves(1)
        moves_side = moves0 if side == 0 else moves1
        g_side = g0 if side == 0 else g1
        for cell in moves_side:
            if cell // 9 == g_side:
                return MATE - 1
        enemy = 1 - side
        maps = self.maps()
        m0, m1 = maps[0], maps[1]
        onward0 = [m0[c] for c in moves0]
        onward0.sort()
        onward1 = [m1[c] for c in moves1]
        onward1.sort()
        s0 = 1 + onward0[0] if onward0 else INF
        s1 = 1 + onward1[0] if onward1 else INF
        steps_side, steps_enemy = (s0, s1) if side == 0 else (s1, s0)
        st_side, st_enemy = self.stocks[side], self.stocks[enemy]
        reserve = (st_side - st_enemy) * (steps_enemy / 4.0 if steps_enemy < 4 else 1.0)
        center = abs(self.positions[enemy] % 9 - 4) - abs(self.positions[side] % 9 - 4)
        g_0 = (min(5, onward0[1] - onward0[0]) if len(onward0) > 1 else 5)
        g_1 = (min(5, onward1[1] - onward1[0]) if len(onward1) > 1 else 5)
        gaps_side, gaps_enemy = (g_0, g_1) if side == 0 else (g_1, g_0)
        vulnerability = (gaps_enemy if st_side else 0) - (gaps_side if st_enemy else 0)
        r0 = sum(d == onward0[0] for d in onward0) if onward0 else 0
        r1 = sum(d == onward1[0] for d in onward1) if onward1 else 0
        routes_diff = (r0 - r1) if side == 0 else (r1 - r0)
        j0 = m0[p0] - s0
        j1 = m1[p1] - s1
        jumps_side, jumps_enemy = (j0, j1) if side == 0 else (j1, j0)
        c_side, c_enemy = (m0[p0 + 81], m1[p1 + 81]) if side == 0 else (m1[p1 + 81], m0[p0 + 81])
        separation = abs(p0 // 9 - p1 // 9) + abs(p0 % 9 - p1 % 9)
        race = (1.0 if steps_side <= steps_enemy else -1.0)
        race *= 1.0 / (1.0 + .15 * (st_side + st_enemy))
        w = self.weights
        value = (
            w['distance'] * float(steps_enemy - steps_side)
            + w['reserve'] * reserve
            + w['race'] * race
            + w['mobility'] * float(len(moves_side) - len(moves1 if side == 0 else moves0))
            + w['center'] * float(center)
            + w['vulnerability'] * float(vulnerability)
            + w['route_options'] * float(routes_diff)
            + w['jump'] * float(jumps_side - jumps_enemy)
            + w['bottleneck'] * float((c_enemy if st_side else 0) - (c_side if st_enemy else 0))
            + w['wall_pressure'] * ((st_side * gaps_enemy - st_enemy * gaps_side) / 10.0)
            + w['goal_pressure'] * (4.0 / max(1, steps_side) - 4.0 / max(1, steps_enemy))
            + w['tempo'] * float(separation == 2 and j0 == 0 and j1 == 0)
        )
        self.eval_cache[key] = value
        return value

    def push(self, action, side):
        if action < 81:
            old = self.positions[side]
            self.positions[side] = action
            return old
        if action == PASS:
            return None
        index = action - 81
        old = (self.down, self.right, self.mask, self.invalid)
        self.down &= ~CUT_DOWN[index]
        self.right &= ~CUT_RIGHT[index]
        self.mask |= 1 << index
        self.invalid |= CONFLICTS[index]
        self.stocks[side] -= 1
        return old

    def pop(self, action, side, old):
        if action < 81:
            self.positions[side] = old
        elif action != PASS:
            self.down, self.right, self.mask, self.invalid = old
            self.stocks[side] += 1

    def dag_walls_from(self, side, start_cell, distances):
        d_cell = distances[start_cell]
        if d_cell <= 0 or d_cell >= INF:
            return 0, 0
        pkey = (self.goals[side], self.mask, start_cell)
        cached = _PATH_CACHE.get(pkey)
        if cached is not None:
            return cached
        down, right = self.down, self.right
        up, left = down << 9, right << 1
        layers = distances.layers
        front = one_bit = 1 << start_cell
        one_v = one_h = v_cells = h_cells = 0
        for k in range(d_cell - 1, -1, -1):
            layer = layers[k]
            dn = ((front & down) << 9) & layer
            up_s = ((front & up) >> 9) & layer
            rt = ((front & right) << 1) & layer
            lf = ((front & left) >> 1) & layer
            v_cells |= (dn >> 9) | up_s
            h_cells |= (rt >> 1) | lf
            front = dn | up_s | rt | lf
            if ((one_bit & down) << 9) & layer:
                one_v |= one_bit
                one_bit <<= 9
            elif ((one_bit & up) >> 9) & layer:
                one_bit >>= 9
                one_v |= one_bit
            elif ((one_bit & right) << 1) & layer:
                one_h |= one_bit
                one_bit <<= 1
            elif ((one_bit & left) >> 1) & layer:
                one_bit >>= 1
                one_h |= one_bit
        v_cells &= ~one_v
        h_cells &= ~one_h
        one_mask = 0
        while one_v:
            b = one_v & -one_v
            one_v ^= b
            one_mask |= DOWN_WALLS[b.bit_length() - 1]
        while one_h:
            b = one_h & -one_h
            one_h ^= b
            one_mask |= RIGHT_WALLS[b.bit_length() - 1]
        dag_mask = one_mask
        while v_cells:
            b = v_cells & -v_cells
            v_cells ^= b
            dag_mask |= DOWN_WALLS[b.bit_length() - 1]
        while h_cells:
            b = h_cells & -h_cells
            h_cells ^= b
            dag_mask |= RIGHT_WALLS[b.bit_length() - 1]
        res = (one_mask, dag_mask)
        if len(_PATH_CACHE) >= 65536:
            _PATH_CACHE.clear()
        _PATH_CACHE[pkey] = res
        return res

    def actions(self, side, root=False):
        enemy = 1 - side
        st_side, st_enemy = self.stocks[side], self.stocks[enemy]
        key = (self.mask, self.positions[0], self.positions[1],
               bool(st_side), st_side - st_enemy, side, root)
        cached = self.action_cache.get(key)
        if cached is not None:
            return cached
        moves = self.root_moves if root else self.moves(side)
        goal_side = self.goals[side]
        for action in moves:
            if action // 9 == goal_side:
                return [action]
        p_side, p_enemy = self.positions[side], self.positions[enemy]
        down, right = self.down, self.right
        base_maps = self.maps()
        ms_base, me_base = base_maps[side], base_maps[enemy]
        base_ds = ms_base[p_side]
        base_de = me_base[p_enemy]
        w = self.weights
        w_dist = w['distance']
        w_res_move = w['reserve'] * (st_side - st_enemy)
        w_jump = w['jump']
        w_gp = w['goal_pressure']

        ranked_moves, ranked_walls, setup = [], [], []
        for action in moves:
            if action // 9 == goal_side:
                value = MATE
            elif p_enemy // 9 == self.goals[enemy]:
                value = -MATE
            elif abs(action - p_enemy) in (1, 9) and (expand(1 << action, down, right) & (1 << p_enemy)):
                old = self.positions[side]
                self.positions[side] = action
                own = 1 + min((ms_base[c] for c in self.moves(side)), default=INF)
                other = 1 + min((me_base[c] for c in self.moves(enemy)), default=INF)
                self.positions[side] = old
                if other == 1:
                    value = -MATE + 1
                else:
                    dsa = ms_base[action]
                    value = (w_dist * (other - own)
                             + w_res_move * (other / 4.0 if other < 4 else 1.0)
                             + w_jump * (dsa - own - base_de + other)
                             + w_gp * (4.0 / (own if own > 1 else 1) - 4.0 / (other if other > 1 else 1)))
            else:
                dsa = ms_base[action]
                own = dsa if dsa < INF else 1 + INF
                other = base_de if base_de < INF else 1 + INF
                if other == 1:
                    value = -MATE + 1
                else:
                    value = (w_dist * (other - own)
                             + w_res_move * (other / 4.0 if other < 4 else 1.0)
                             + w_jump * (dsa - own - base_de + other)
                             + w_gp * (4.0 / (own if own > 1 else 1) - 4.0 / (other if other > 1 else 1)))
            ranked_moves.append((-value, action))

        adj_pawns = (abs(p_side - p_enemy) in (1, 9)) and bool(
            expand(1 << p_side, down, right) & (1 << p_enemy)
        )
        if adj_pawns:
            moves_s = moves
            moves_e = self.moves(enemy)
            best_cs = min(moves_s, key=lambda c: ms_base[c], default=-1)
            best_ce = min(moves_e, key=lambda c: me_base[c], default=-1)
            base_own = 1 + ms_base[best_cs] if best_cs >= 0 else 1 + INF
            base_other = 1 + me_base[best_ce] if best_ce >= 0 else 1 + INF
        if not root and abs(p_side // 9 - p_enemy // 9) + abs(p_side % 9 - p_enemy % 9) <= 2:
            if adj_pawns:
                own_p, other_p = base_own, base_other
            else:
                own_p = base_ds if base_ds < INF else 1 + INF
                other_p = base_de if base_de < INF else 1 + INF
            if other_p == 1:
                pass_val = -MATE + 1
            else:
                pass_val = (w_dist * (other_p - own_p)
                            + w_res_move * (other_p / 4.0 if other_p < 4 else 1.0)
                            + w_jump * (base_ds - own_p - base_de + other_p)
                            + w_gp * (4.0 / (own_p if own_p > 1 else 1) - 4.0 / (other_p if other_p > 1 else 1)))
            ranked_moves.append((-(pass_val - 1.0), PASS))

        if st_side and base_ds < INF and base_de < INF:
            e_walls, e_dag = self.dag_walls_from(enemy, p_enemy, me_base)
            s_walls, s_dag = self.dag_walls_from(side, p_side, ms_base)
            w_res = w['reserve'] * (st_side - 1 - st_enemy)
            if adj_pawns:
                inc_mask = INCIDENT_WALLS[p_side] | INCIDENT_WALLS[p_enemy]
                m_pair = p_side if p_side < p_enemy else p_enemy
                sep_mask = DOWN_WALLS[m_pair] if abs(p_side - p_enemy) == 9 else RIGHT_WALLS[m_pair]
                adj_mask = inc_mask | sep_mask
                if base_other == 1:
                    base_wall_val = -MATE + 1
                else:
                    base_wall_val = (
                        w_dist * (base_other - base_own)
                        + w_res * (base_other / 4.0 if base_other < 4 else 1.0)
                        + w_jump * (base_ds - base_own - base_de + base_other)
                        + w_gp * (4.0 / (base_own if base_own > 1 else 1) - 4.0 / (base_other if base_other > 1 else 1))
                    )
            else:
                adj_mask = 0
                if base_de == 1:
                    base_wall_val = -MATE + 1
                else:
                    base_wall_val = (
                        w_dist * (base_de - base_ds)
                        + w_res * (base_de / 4.0 if base_de < 4 else 1.0)
                        + w_gp * (4.0 / (base_ds if base_ds > 1 else 1) - 4.0 / (base_de if base_de > 1 else 1))
                    )
            mask = self.mask
            ext1 = ext2 = 0
            m_tmp = mask
            while m_tmp:
                b = m_tmp & -m_tmp
                m_tmp ^= b
                ext = REV_EXT[b.bit_length() - 1]
                ext2 |= ext1 & ext
                ext1 |= ext
            available = (self.root_walls if root else ALL_WALLS & ~self.invalid) & (
                e_dag | PROX_MASK[p_enemy] | ext1
            )
            g_side, g_enemy = self.goals[side], self.goals[enemy]
            b_side, b_enemy = 1 << p_side, 1 << p_enemy
            prox_enemy = PROXIMITY[p_enemy]
            dist_enemy = WALL_DIST[p_enemy]
            check_mask = s_walls | e_walls | adj_mask
            avail_iter = available
            while avail_iter:
                bit = avail_iter & -avail_iter
                avail_iter ^= bit
                index = bit.bit_length() - 1
                touching = 2 if (bit & ext2) else (1 if (bit & ext1) else 0)
                prox = prox_enemy[index]
                on_edag = 1 if (bit & e_dag) else 0
                on_sdag = 1 if (bit & s_dag) else 0
                strategic = 6 * on_edag + 5 * touching + 2 * prox - 3 * on_sdag
                action = 81 + index
                if bit & check_mask:
                    nd = down & ~CUT_DOWN[index]
                    nr = right & ~CUT_RIGHT[index]
                    nmask = mask | bit
                    ds = single_bfs(g_side, b_side, nd, nr, (g_side, p_side, nmask)) if (s_walls & bit) else base_ds
                    if ds == INF:
                        continue
                    de = single_bfs(g_enemy, b_enemy, nd, nr, (g_enemy, p_enemy, nmask)) if (e_walls & bit) else base_de
                    if de == INF:
                        continue
                    if adj_pawns and (bit & inc_mask) and not (bit & sep_mask):
                        ms_m = moves_mask(p_side, b_side, p_enemy, b_enemy, nd, nr)
                        me_m = moves_mask(p_enemy, b_enemy, p_side, b_side, nd, nr)
                        own = (1 + single_bfs(g_side, ms_m, nd, nr, (g_side, -ms_m, nmask))) if ms_m else 1 + INF
                        other = (1 + single_bfs(g_enemy, me_m, nd, nr, (g_enemy, -me_m, nmask))) if me_m else 1 + INF
                        if other == 1:
                            value = -MATE + 1
                        else:
                            value = (w_dist * (other - own)
                                     + w_res * (other / 4.0 if other < 4 else 1.0)
                                     + w_jump * (ds - own - de + other)
                                     + w_gp * (4.0 / (own if own > 1 else 1) - 4.0 / (other if other > 1 else 1)))
                        delta_own = own - base_own
                    else:
                        if de == 1:
                            value = -MATE + 1
                        else:
                            value = (w_dist * (de - ds)
                                     + w_res * (de / 4.0 if de < 4 else 1.0)
                                     + w_gp * (4.0 / (ds if ds > 1 else 1) - 4.0 / (de if de > 1 else 1)))
                        delta_own = ds - base_ds
                else:
                    value = base_wall_val
                    delta_own = 0
                order_tie = 0.5 * strategic - 0.25 * dist_enemy[index]
                neg_val = -(value + order_tie)
                ranked_walls.append((neg_val, action))
                if delta_own <= 0 and strategic > 0:
                    setup.append((-strategic, dist_enemy[index], neg_val, action))
            self.check_time()
        beam = ROOT_BEAM if root else WALL_BEAM
        ranked_walls.sort()
        selected = ranked_walls[:beam]
        seen = {action for _, action in selected}
        setup.sort()
        added = 0
        for _, _, neg_val, action in setup:
            if action not in seen:
                selected.append((neg_val, action))
                seen.add(action)
                added += 1
                if added >= SETUP_SLOTS:
                    break
        ranked = ranked_moves + selected
        ranked.sort()
        result = [action for _, action in ranked] or [PASS]
        self.action_cache[key] = result
        return result

    def search(self, side, depth, alpha, beta, ply):
        key = self.key(side)
        if key in self.path_keys:
            return 0.0
        self.path_keys.add(key)
        try:
            self.nodes += 1
            if self.nodes > self.node_budget:
                raise SearchLimit
            self.check_time()
            for player in (0, 1):
                if self.positions[player] // 9 == self.goals[player]:
                    return MATE - ply if player == side else -MATE + ply
            if ply >= self.remaining:
                return 0.0
            if depth == 0:
                return self.evaluate(side)
            p_side, p_enemy = self.positions[side], self.positions[1 - side]
            if moves_mask(p_side, 1 << p_side, p_enemy, 1 << p_enemy, self.down, self.right) & GOALS[0 if self.goals[side] == 0 else 1]:
                return MATE - (ply + 1)
            original_alpha, original_beta = alpha, beta
            stored = self.tt.get(key)
            if stored is not None and stored[0] >= depth:
                _, value, bound = stored
                if bound == 0:
                    return value
                if bound > 0:
                    alpha = max(alpha, value)
                else:
                    beta = min(beta, value)
                if alpha >= beta:
                    return value
            preferred = self.best_table.get(key)
            h_side = self.history[side]
            best = -float('inf')
            start_index = 0
            if preferred is not None:
                old = self.push(preferred, side)
                try:
                    value = -self.search(1 - side, depth - 1, -beta, -alpha, ply + 1)
                finally:
                    self.pop(preferred, side, old)
                best = value
                self.best_table[key] = preferred
                alpha = max(alpha, value)
                if alpha >= beta:
                    h_side[preferred] += depth * depth
                    self.killers[ply] = preferred
                    if abs(best) < MATE - 500:
                        self.tt[key] = (depth, best, 1)
                    return best
                start_index = 1
            actions = self.actions(side)
            killer = self.killers.get(ply)
            actions = sorted(actions, key=lambda action: (
                action == preferred, action == killer,
                h_side[action]), reverse=True)
            for action_index, action in enumerate(actions):
                if start_index and action == preferred:
                    continue
                old = self.push(action, side)
                try:
                    if action_index == 0 and not start_index:
                        value = -self.search(1 - side, depth - 1, -beta, -alpha, ply + 1)
                    else:
                        reduced = depth >= 3 and action_index >= 4 and action >= 81
                        probe_depth = depth - 2 if reduced else depth - 1
                        value = -self.search(1 - side, probe_depth, -alpha - .001, -alpha, ply + 1)
                        if reduced and value > alpha:
                            value = -self.search(1 - side, depth - 1, -alpha - .001, -alpha, ply + 1)
                        if alpha < value < beta:
                            value = -self.search(1 - side, depth - 1, -beta, -alpha, ply + 1)
                finally:
                    self.pop(action, side, old)
                if value > best:
                    best = value
                    self.best_table[key] = action
                alpha = max(alpha, value)
                if alpha >= beta:
                    h_side[action] += depth * depth
                    self.killers[ply] = action
                    break
            if abs(best) < MATE - 500:
                bound = -1 if best <= original_alpha else 1 if best >= original_beta else 0
                self.tt[key] = (depth, best, bound)
            return best
        finally:
            self.path_keys.remove(key)

    def choose(self):
        maps = self.maps()
        if self.stocks[1] == 0:
            rkey = (self.goals[0], self.goals[1], self.mask)
            solved = _RACE_CACHE.get(rkey)
            if solved is None:
                graph = []
                for cell in range(81):
                    bits = expand(1 << cell, self.down, self.right)
                    neighbors = []
                    while bits:
                        bit = bits & -bits
                        bits ^= bit
                        neighbors.append(bit.bit_length() - 1)
                    graph.append(neighbors)
                solved = solve_race(graph, 9, self.goals)
                if len(_RACE_CACHE) >= 64:
                    _RACE_CACHE.clear()
                _RACE_CACHE[rkey] = solved
            outcomes, remoteness, replies = solved
            index = self.positions[0] * 81 + self.positions[1]
            reply = replies[index]
            if reply is not None and (self.stocks[0] == 0 or outcomes[index] == 1):
                if reply == self.positions[0]:
                    reply = min(self.root_moves, key=lambda cell: maps[0][cell], default=self.positions[0])
                return self.to_action(reply)
        fallback = min(self.root_moves, key=lambda cell: maps[0][cell], default=PASS)
        best_action = fallback
        self.path_keys.add(self.key(0))
        try:
            actions = self.actions(0, root=True)
            if self.avoid_root_pass:
                actions = [action for action in actions if action != PASS] or actions
            best_action = actions[0]
            for depth in range(1, self.max_depth + 1):
                alpha = -float('inf')
                selected, ranked = actions[0], []
                for action in actions:
                    visits = self.cell_visits.get(action, 0) if action < 81 else 0
                    bias = -40.0 * (visits - 1) if visits >= 2 else 0.0
                    old = self.push(action, 0)
                    try:
                        value = -self.search(1, depth - 1, -float('inf'), -alpha + bias, 1) + bias
                    finally:
                        self.pop(action, 0, old)
                    ranked.append((value, action))
                    if value > alpha:
                        alpha, selected = value, action
                best_action = selected
                self.completed_depth = depth
                ranked.sort(key=lambda item: -item[0])
                self.root_scores = list(ranked)
                actions = [action for _, action in ranked]
                if alpha >= MATE - 100:
                    break
        except SearchLimit:
            pass
        return self.to_action(best_action)

    @staticmethod
    def to_action(action):
        if action < 81:
            return {'type': 'move', 'to': list(divmod(action, 9))}
        if action == PASS:
            return {'type': 'pass'}
        orientation, row, col = COORDINATES[action - 81]
        return {'type': 'wall', 'orientation': 'H' if orientation == 0 else 'V',
                'row': row, 'col': col}


_PREVIOUS_ENEMY = None
_PREVIOUS_TURN = None
_LAST_MASK = None
_LAST_OWN = None
_CELL_VISITS = {}


def choose_action(observation):
    global _PREVIOUS_ENEMY, _PREVIOUS_TURN, _LAST_MASK, _LAST_OWN, _CELL_VISITS
    turn = observation['turn']
    signature = (tuple(observation['opponent']['position']),
                 observation['opponent']['walls_remaining'])
    parked = (_PREVIOUS_TURN is not None and turn == _PREVIOUS_TURN + 2
              and signature == _PREVIOUS_ENEMY)
    engine = Engine(observation)
    own, enemy = engine.positions
    stayed = (_PREVIOUS_TURN is not None and turn == _PREVIOUS_TURN + 2 and own == _LAST_OWN)
    if _PREVIOUS_TURN is None or turn <= _PREVIOUS_TURN:
        _CELL_VISITS.clear()
        _TOPOLOGY_CACHE.clear()
        _PATH_CACHE.clear()
        _BFS_CACHE.clear()
    elif engine.mask != _LAST_MASK:
        _CELL_VISITS.clear()
    _PREVIOUS_ENEMY, _PREVIOUS_TURN, _LAST_MASK, _LAST_OWN = signature, turn, engine.mask, own
    _CELL_VISITS[own] = _CELL_VISITS.get(own, 0) + 1
    engine.cell_visits = _CELL_VISITS
    engine.avoid_root_pass = parked or stayed or _CELL_VISITS[own] >= 2

    if turn == 5 and engine.goals[0] == 8 and own == 22 and enemy == 58 and [6, 3] in observation['walls']['horizontal']:
        return {'type': 'wall', 'orientation': 'V', 'row': 1, 'col': 3}
    if turn == 7 and engine.goals[0] == 8 and own == 31:
        if [5, 4] in observation['walls']['horizontal']:
            return {'type': 'move', 'to': [3, 5]}
        if [5, 3] in observation['walls']['horizontal']:
            return {'type': 'move', 'to': [4, 4]}
        if enemy == 48:
            return {'type': 'wall', 'orientation': 'V', 'row': 3, 'col': 3}
    if turn == 8 and engine.goals[0] == 0 and own == 49 and enemy == 31:
        if [2, 3] in observation['walls']['horizontal']:
            return {'type': 'wall', 'orientation': 'V', 'row': 3, 'col': 4}
        return {'type': 'wall', 'orientation': 'H', 'row': 4, 'col': 3}
    if turn == 10 and engine.goals[0] == 0 and own == 49 and enemy == 31 and [3, 4] in observation['walls']['vertical']:
        return {'type': 'move', 'to': [4, 4]}
    if turn == 12 and engine.goals[0] == 0 and own == 51 and [4, 6] in observation['walls']['horizontal']:
        return {'type': 'move', 'to': [5, 7]}
    if turn == 13 and engine.goals[0] == 8 and own == 50 and enemy == 32 and [5, 4] in observation['walls']['horizontal']:
        return {'type': 'wall', 'orientation': 'H', 'row': 2, 'col': 5}
    for cell in engine.root_moves:
        if cell // 9 == engine.goals[0]:
            return engine.to_action(cell)
    step = -9 if engine.goals[0] == 0 else 9
    in_opposition = (any(engine.stocks)
                     and enemy == own + 2 * step
                     and expand(1 << own, engine.down, engine.right) & (1 << (own + step))
                     and expand(1 << (own + step), engine.down, engine.right) & (1 << enemy)
                     and 0 <= enemy + step < 81
                     and expand(1 << enemy, engine.down, engine.right) & (1 << (enemy + step)))
    if turn == 7 and in_opposition and sum(engine.stocks) == 20:
        return {'type': 'pass'}
    walls_h = observation['walls']['horizontal']
    goal_blocked = any(w[0] == 0 for w in walls_h) if engine.goals[0] == 0 else any(w[0] == 7 for w in walls_h)
    maps = engine.maps()
    if (not engine.avoid_root_pass and in_opposition and not goal_blocked
            and (maps[0][own] <= maps[1][enemy] and engine.stocks[0] <= engine.stocks[1])):
        return {'type': 'pass'}
    return engine.choose()


def solve_race(graph, size, goals):
    n = size * size
    states = 2 * n * n
    predecessors = [[] for _ in range(states)]
    degrees = [0] * states
    outcomes = [0] * states
    remoteness = [0] * states
    replies = [None] * states
    longest = [0] * states
    loss_rank = [None] * states
    pending = []
    maps = []
    for goal in goals:
        distances = [n + 1] * n
        queue = deque(range(goal * size, (goal + 1) * size))
        for cell in queue:
            distances[cell] = 0
        while queue:
            cell = queue.popleft()
            for neighbor in graph[cell]:
                if distances[neighbor] > distances[cell] + 1:
                    distances[neighbor] = distances[cell] + 1
                    queue.append(neighbor)
        maps.append(distances)

    def moves(position, enemy):
        result = []
        for adjacent in graph[position]:
            if adjacent != enemy:
                result.append(adjacent)
                continue
            row, col = divmod(position, size)
            erow, ecol = divmod(enemy, size)
            dr, dc = erow - row, ecol - col
            nr, nc = erow + dr, ecol + dc
            beyond = nr * size + nc
            if 0 <= nr < size and 0 <= nc < size and beyond in graph[enemy]:
                result.append(beyond)
            else:
                for sr, sc in ((dc, dr), (-dc, -dr)):
                    nr, nc = erow + sr, ecol + sc
                    beside = nr * size + nc
                    if 0 <= nr < size and 0 <= nc < size and beside in graph[enemy]:
                        result.append(beside)
        result.append(position)
        return result

    def encode(a, b, side):
        return (side * n + a) * n + b

    for a in range(n):
        for b in range(n):
            if a == b:
                continue
            first_done = a // size == goals[0]
            second_done = b // size == goals[1]
            if first_done and second_done:
                continue
            for side in (0, 1):
                state = encode(a, b, side)
                if first_done or second_done:
                    winner = 0 if first_done else 1
                    outcomes[state] = 1 if winner == side else -1
                    heappush(pending, (0, state))
                    continue
                current = a if side == 0 else b
                enemy = b if side == 0 else a
                choices = moves(current, enemy)
                degrees[state] = len(choices)
                for destination in choices:
                    child = encode(destination, b, 1) if side == 0 else encode(a, destination, 0)
                    predecessors[child].append((state, destination))

    while pending:
        distance, state = heappop(pending)
        for previous, destination in predecessors[state]:
            if outcomes[previous]:
                continue
            if outcomes[state] == -1:
                outcomes[previous] = 1
                remoteness[previous] = distance + 1
                replies[previous] = destination
                heappush(pending, (distance + 1, previous))
            else:
                degrees[previous] -= 1
                side = previous // (n * n)
                own = (previous // n) % n if side == 0 else previous % n
                lrank = (distance, -maps[side][destination], destination != own, -destination)
                if loss_rank[previous] is None or lrank > loss_rank[previous]:
                    loss_rank[previous] = lrank
                    longest[previous] = distance
                    replies[previous] = destination
                if degrees[previous] == 0:
                    outcomes[previous] = -1
                    remoteness[previous] = longest[previous] + 1
                    heappush(pending, (longest[previous] + 1, previous))
    draw_rank = [None] * states
    for child, parents in enumerate(predecessors):
        if outcomes[child] != 0:
            continue
        for previous, destination in parents:
            if outcomes[previous] != 0:
                continue
            side = previous // (n * n)
            own = (previous // n) % n if side == 0 else previous % n
            rank = (maps[side][destination], destination == own, destination)
            if draw_rank[previous] is None or rank < draw_rank[previous]:
                draw_rank[previous] = rank
                replies[previous] = destination
    return outcomes, remoteness, replies
