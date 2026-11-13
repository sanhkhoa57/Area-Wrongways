import gc
from collections import deque
from array import array
from time import perf_counter
SIZE = 9
BITS = tuple((1 << p for p in range(81)))
ROWS = tuple((511 << 9 * r for r in range(9)))
FULL = (1 << 81) - 1
LEFT_EDGE = sum((1 << 9 * r for r in range(9)))
EMPTY_OPEN = (FULL ^ ROWS[0], FULL ^ ROWS[8], FULL ^ LEFT_EDGE, FULL ^ LEFT_EDGE << 8)
DELTAS = (-9, 9, -1, 1)
PERPENDICULAR = ((2, 3), (2, 3), (0, 1), (0, 1))
ALL_WALLS = (1 << 128) - 1
PASS = 209
MATE = 1000000
MATE_BOUND = MATE - 1000
UNREACHABLE = 1000
TIME_BUDGET = 0.78
MAX_DEPTH = 40
WALL_VALUE = (0, 230, 410, 560, 690, 810, 930, 1050, 1170, 1290, 1410)
RESERVE = tuple((tuple((round(230 * (d + 2) * (1 - (1 - 1 / (d + 2)) ** w)) for d in range(81))) for w in range(11)))
PLAN_SLOTS = (3, 2, 1)
PLAN_PROBES = (3, 0, 0)
_popcount = getattr(int, 'bit_count', lambda n: bin(n).count('1'))

def _geometry():
    cuts, conflicts, connections, edge_walls = ([], [], [], {})
    endpoints = []
    coordinates = []
    for i in range(128):
        vertical = i >= 64
        r, c = divmod(i & 63, 8)
        p = r * 9 + c
        coordinates.append(('V' if vertical else 'H', r, c))
        if vertical:
            cuts.append((0, 0, BITS[p + 1] | BITS[p + 10], BITS[p] | BITS[p + 9]))
            edges = ((p, p + 1), (p + 9, p + 10))
            nearby = (i - 8 if r else i, i + 8 if r < 7 else i)
            endpoints.append({(r, c + 1), (r + 1, c + 1), (r + 2, c + 1)})
        else:
            cuts.append((BITS[p + 9] | BITS[p + 10], BITS[p] | BITS[p + 1], 0, 0))
            edges = ((p, p + 9), (p + 1, p + 10))
            nearby = (i - 1 if c else i, i + 1 if c < 7 else i)
            endpoints.append({(r + 1, c), (r + 1, c + 1), (r + 1, c + 2)})
        conflicts.append(1 << i | 1 << (i ^ 64) | 1 << nearby[0] | 1 << nearby[1])
        for a, b in edges:
            edge_walls[a, b] = edge_walls.get((a, b), 0) | 1 << i
            edge_walls[b, a] = edge_walls.get((b, a), 0) | 1 << i
    for i in range(128):
        connections.append(sum((1 << j for j in range(128) if not conflicts[i] & 1 << j and endpoints[i] & endpoints[j])))
    h_lookup = tuple((sum((1 << r * 8 + x for x in (c - 1, c) if 0 <= x < 8)) if r < 8 else 0 for r in range(9) for c in range(9)))
    v_lookup = tuple((sum((1 << 64 + x * 8 + c for x in (r - 1, r) if 0 <= x < 8)) if c < 8 else 0 for r in range(9) for c in range(9)))
    near = []
    for p in range(81):
        pr, pc = divmod(p, 9)
        near.append(sum((1 << i for i, (_, r, c) in enumerate(coordinates) if max(r - pr, pr - r - 1, 0) + max(c - pc, pc - c - 1, 0) <= 1)))
    return (tuple(cuts), tuple(conflicts), tuple(connections), tuple(coordinates), h_lookup, v_lookup, tuple(near))
CUTS, CONFLICTS, CONNECTED, COORDS, H_EDGE, V_EDGE, NEAR = _geometry()
ROW_TO_ANCHORS = tuple(((x | x >> 1) & 255 for x in range(512)))

class _Deadline(Exception):
    pass

def _cells(mask):
    result = []
    while mask:
        bit = mask & -mask
        result.append(bit.bit_length() - 1)
        mask ^= bit
    return result

def _spread(front, opened):
    u, d, l, r = opened
    return (front & u) >> 9 | (front & d) << 9 | (front & l) >> 1 | (front & r) << 1

class _Board:
    """Immutable wall topology; lazy mutable caches never contain pawn state."""
    __slots__ = ('mask', 'opened', 'available', 'goals', 'layers', 'seen', 'distances', 'dags', 'flexibility', 'graph', 'dominators', 'blockades')

    def __init__(self, mask, opened, available, goals):
        self.mask, self.opened, self.available, self.goals = (mask, opened, available, goals)
        self.layers = ([ROWS[goals[0]]], [ROWS[goals[1]]])
        self.seen = [ROWS[goals[0]], ROWS[goals[1]]]
        self.distances = ([-1] * 81, [-1] * 81)
        self.dags = ({}, {})
        self.flexibility = ({}, {})
        self.graph = None
        self.dominators = ({}, {})
        self.blockades = {}

    def distance(self, p, side):
        cached = self.distances[side][p]
        if cached >= 0:
            return cached
        target = BITS[p]
        layers = self.layers[side]
        seen = self.seen[side]
        while not seen & target and layers[-1]:
            front = _spread(layers[-1], self.opened) & ~seen
            layers.append(front)
            seen |= front
        self.seen[side] = seen
        value = UNREACHABLE
        if seen & target:
            for i, front in enumerate(layers):
                if front & target:
                    value = i
                    break
        self.distances[side][p] = value
        return value

    def path_walls(self, p, side):
        cached = self.dags[side].get(p)
        if cached is not None:
            return cached
        distance = self.distance(p, side)
        if distance == 0 or distance == UNREACHABLE:
            self.dags[side][p] = 0
            return 0
        u, d, l, r = self.opened
        layers = self.layers[side]
        while len(layers) <= distance and layers[-1]:
            front = _spread(layers[-1], self.opened) & ~self.seen[side]
            layers.append(front)
            self.seen[side] |= front
        front = BITS[p]
        vertical_edges = horizontal_edges = 0
        for k in range(distance - 1, -1, -1):
            layer = layers[k]
            up = (front & u) >> 9 & layer
            down = (front & d) << 9 & layer
            left = (front & l) >> 1 & layer
            right = (front & r) << 1 & layer
            vertical_edges |= up | down >> 9
            horizontal_edges |= left | right >> 1
            front = up | down | left | right
        horizontal_edges |= horizontal_edges >> 9
        result = 0
        for row in range(8):
            result |= ROW_TO_ANCHORS[vertical_edges >> 9 * row & 511] << 8 * row
            result |= (horizontal_edges >> 9 * row & 255) << 64 + 8 * row
        self.dags[side][p] = result
        return result

    def routes(self, p, side):
        cached = self.flexibility[side].get(p)
        if cached is not None:
            return cached
        distance = self.distance(p, side)
        if not 0 < distance < UNREACHABLE:
            return (0, 0)
        layers = self.layers[side]
        while len(layers) <= distance and layers[-1]:
            front = _spread(layers[-1], self.opened) & ~self.seen[side]
            layers.append(front)
            self.seen[side] |= front
        first = _spread(BITS[p], self.opened) & self.layers[side][distance - 1]
        n = _popcount(first)
        second = _spread(first, self.opened) & self.layers[side][distance - 2] if distance > 1 else first
        value = (n, _popcount(second))
        self.flexibility[side][p] = value
        return value

    def gates(self, p, side):
        """Walls intersecting EVERY shortest path, before wall-legality checks.

        For each outgoing shortest-path edge, a wall either cuts that edge
        or must cut all paths from its successor. Intersect those alternatives.
        Unlike counting routes or DAG width, this recognizes two parallel
        shortest-path edges that the SAME wall can block together.
        """
        cache = self.dominators[side]
        known = cache.get(p)
        if known is not None:
            return known
        distance = self.distance(p, side)
        if distance == 0 or distance == UNREACHABLE:
            cache[p] = 0
            return 0
        layers = self.layers[side]
        while len(layers) <= distance and layers[-1]:
            front = _spread(layers[-1], self.opened) & ~self.seen[side]
            layers.append(front)
            self.seen[side] |= front
        successors = _spread(BITS[p], self.opened) & self.layers[side][distance - 1]
        common = ALL_WALLS
        while successors:
            bit = successors & -successors
            successors ^= bit
            q = bit.bit_length() - 1
            lower = min(p, q)
            cutting = H_EDGE[lower] if abs(p - q) == 9 else V_EDGE[lower]
            common &= self.gates(q, side) | cutting
        cache[p] = common
        return common

    def blocked(self, own, enemy, side):
        """Can a stationary enemy prevent ALL pawn-and-jump routes?

        Used as a game-value bound only when the victim has no walls left:
        then the victim cannot change the movement topology, while the
        opponent can legally keep passing. This is not an unconditional
        draw claim when either player's walls could unlock diagonal jumps.
        """
        distance = self.distance(own, side)
        if abs(own // 9 - enemy // 9) + abs(own % 9 - enemy % 9) + abs(enemy // 9 - self.goals[side]) > distance:
            return False
        key = (own, enemy, side)
        found = self.blockades.get(key)
        if found is not None:
            return found
        enemy_bit = BITS[enemy]
        links = []
        neighbors = _spread(enemy_bit, self.opened)
        for start in _cells(neighbors):
            destinations = 0
            for dest in _moves(self, start, enemy):
                destinations |= BITS[dest]
            links.append((BITS[start], destinations))
        seen, front = (enemy_bit | BITS[own], BITS[own])
        goal = ROWS[self.goals[side]]
        while front:
            if front & goal:
                self.blockades[key] = False
                return False
            following = _spread(front, self.opened)
            for start, destinations in links:
                if front & start:
                    following |= destinations
            front = following & ~seen
            seen |= front
        self.blockades[key] = True
        return True

def _moves(board, own, enemy):
    """One source of truth for ordinary steps, straight jumps and diagonals."""
    opened = board.opened
    out = []
    for direction, delta in enumerate(DELTAS):
        if not opened[direction] & BITS[own]:
            continue
        adjacent = own + delta
        if adjacent != enemy:
            out.append(adjacent)
        elif opened[direction] & BITS[enemy]:
            out.append(enemy + delta)
        else:
            for turn in PERPENDICULAR[direction]:
                if opened[turn] & BITS[enemy]:
                    dest = enemy + DELTAS[turn]
                    if dest not in out:
                        out.append(dest)
    return out

def _finish(board, own, enemy, side):
    if abs(own // 9 - board.goals[side]) > 2:
        return False
    return any((p // 9 == board.goals[side] for p in _moves(board, own, enemy)))

def _pawn_distance(board, own, enemy, side):
    d = board.distance(own, side)
    if _spread(BITS[own], board.opened) & BITS[enemy]:
        return 1 + min((board.distance(p, side) for p in _moves(board, own, enemy)), default=UNREACHABLE)
    return d

def _evaluate(board, p0, p1, w0, w1, side):
    d0 = _pawn_distance(board, p0, p1, 0)
    d1 = _pawn_distance(board, p1, p0, 1)
    own, enemy, wm, wo, dm, do = (p0, p1, w0, w1, d0, d1) if side == 0 else (p1, p0, w1, w0, d1, d0)
    own_value = (WALL_VALUE[wm] + RESERVE[wm][min(80, do)]) // 2
    enemy_value = (WALL_VALUE[wo] + RESERVE[wo][min(80, dm)]) // 2
    reserve = own_value-enemy_value if _RACING_MODE else (own_value * min(4, do) - enemy_value * min(4, dm)) // 4
    score = 100 * (do - dm) + reserve + (40 if _RACING_MODE or _CONTROL_MODE else 60)
    if wo == 0 and dm <= do:
        score += 120
    elif wm == 0 and dm > do:
        score -= 120
    if wo:
        a, b = board.routes(own, side)
        score += 4 * min(a, 3) - (6 if a == 1 and b == 1 else 0)
    if wm:
        a, b = board.routes(enemy, 1 - side)
        score -= 4 * min(a, 3) - (6 if a == 1 and b == 1 else 0)
    if score > 0 and wm == 0 and board.blocked(own, enemy, side):
        score = 0
    elif score < 0 and wo == 0 and board.blocked(enemy, own, 1 - side):
        score = 0
    return score
_BOARDS = {}
_TT = {}
_CONTEXT = None
_LAST_TURN = -1
_VISITS = {}
_RECENT = []
_PREVIOUS_OPPONENT = None
_OPPONENT_WAITED = False
_ENDGAME = None
_LAST_STATS = {}
_SOLVED_LEAVES={}
_CONTROL_MODE=False
_RACING_MODE=False
_POLICY=None
_POLICY_TURN=-1
_POLICY_GOAL=None

def _child_board(board, index):
    mask = board.mask | 1 << index
    found = _BOARDS.get(mask)
    if found is not None:
        return found
    cut = CUTS[index]
    opened = tuple((board.opened[i] & ~cut[i] for i in range(4)))
    child = _Board(mask, opened, board.available & ~CONFLICTS[index], board.goals)
    if len(_BOARDS) >= 18000:
        for key in list(_BOARDS)[:1500]:
            del _BOARDS[key]
    _BOARDS[mask] = child
    return child

class _Search:

    def __init__(self, deadline, remaining):
        self.deadline, self.remaining = (deadline, remaining)
        self.nodes, self.depth = (0, 0)
        self.history = [0] * 210
        self.killers = [[None, None] for _ in range(96)]
        self.generations = {}
        self.risk_cache = {}
        self.pressure_cache = {}
        self.protected = set()
        self.plan_cache = {}
        self.forbidden = set()
        self.tables_built=0

    def plan_score(self, board, p0, p1, w0, w1, side):
        """Counterfactual two-ply rollout for preparation ORDERING only.

        All enemy pawn moves/pass, plus selected legal preemptive/dangerous
        walls, face our best pawn move or closing wall. The regular PVS still
        evaluates the real alternation and all retained defensive candidates.
        This selective rollout never proves a win or changes minimax scores.
        """
        key = (board.mask, p0, p1, w0, w1, side)
        if key in self.plan_cache:
            return self.plan_cache[key]
        own, enemy = (p0, p1) if side == 0 else (p1, p0)
        wm, wo = (w0, w1) if side == 0 else (w1, w0)
        replies = []
        for dest in _moves(board, enemy, own) + [enemy]:
            if dest // 9 == board.goals[1 - side]:
                self.plan_cache[key] = -MATE
                return -MATE
            a, b = (own, dest) if side == 0 else (dest, own)
            replies.append((board, a, b, w0, w1))
        if wo:
            _, closers = self.pressure(board, enemy, own, 1 - side)
            _, dangerous = self.pressure(board, own, enemy, side)
            counters = 0
            for index in _cells(closers):
                counters |= CONFLICTS[index] & ~(1 << index)
            candidates = _cells(board.available & (counters | dangerous))
            candidates.sort(key=lambda i: bool(dangerous & 1 << i), reverse=True)
            for index in candidates[:5]:
                after = _child_board(board, index)
                if after.distance(p0, 0) == UNREACHABLE or after.distance(p1, 1) == UNREACHABLE:
                    continue
                x, y = (w0, w1 - 1) if side == 0 else (w0 - 1, w1)
                replies.append((after, p0, p1, x, y))
        worst = MATE
        for after, a, b, x, y in replies:
            self.poll()
            me, other = (a, b) if side == 0 else (b, a)
            best = -MATE
            for dest in _moves(after, me, other) + [me]:
                if dest // 9 == board.goals[side]:
                    best = MATE
                    break
                if _finish(after, other, dest, 1 - side):
                    value = -MATE
                else:
                    na, nb = (dest, b) if side == 0 else (a, dest)
                    value = -_evaluate(after, na, nb, x, y, 1 - side)
                best = max(best, value)
            stock = x if side == 0 else y
            if stock and best < MATE_BOUND:
                _, closers = self.pressure(after, other, me, 1 - side)
                for index in _cells(closers)[:3]:
                    closed = _child_board(after, index)
                    nx, ny = (x - 1, y) if side == 0 else (x, y - 1)
                    value = -MATE if _finish(closed, other, me, 1 - side) else -_evaluate(closed, a, b, nx, ny, 1 - side)
                    best = max(best, value)
            worst = min(worst, best)
        self.plan_cache[key] = worst
        return worst

    def pressure(self, board, victim, attacker, side):
        """Largest legal one-wall NET delay; a heuristic, not a forced win.

        The attacker spends a turn and a wall in the real search. This
        routine only ranks prospective threats and never returns mate scores.
        Both routes must survive. Walls unable to remove every shortest path
        cannot increase distance and need no BFS here.
        """
        key = (board.mask, victim, attacker, side)
        found = self.pressure_cache.get(key)
        if found is not None:
            return found
        old_v = board.distance(victim, side)
        old_a = board.distance(attacker, 1 - side)
        pending = board.gates(victim, side) & board.available
        worst, threats = (0, 0)
        checked = 0
        while pending:
            bit = pending & -pending
            pending ^= bit
            checked += 1
            if checked & 7 == 0:
                self.poll()
            after = _child_board(board, bit.bit_length() - 1)
            dv = after.distance(victim, side)
            if dv == UNREACHABLE:
                continue
            da = after.distance(attacker, 1 - side)
            if da == UNREACHABLE:
                continue
            net = dv - old_v - (da - old_a)
            if net > worst:
                worst, threats = (net, bit)
            elif net == worst and net > 0:
                threats |= bit
        found = (worst, threats)
        self.pressure_cache[key] = found
        return found

    def poll(self):
        if perf_counter() >= self.deadline:
            raise _Deadline

    def vulnerability(self, board, own, enemy, side):
        """Largest legal one-wall detour; used only as a small root preference.

        Both players' routes are checked.  This is a risk estimate, not a
        claim that the opponent gets a free wall or two consecutive turns.
        """
        key = (board.mask, own, enemy, side)
        cached = self.risk_cache.get(key)
        if cached is not None:
            return cached
        distance = board.distance(own, side)
        candidates = board.path_walls(own, side) & board.available
        other_paths = board.path_walls(enemy, 1 - side)
        worst = 0
        checked = 0
        while candidates:
            bit = candidates & -candidates
            candidates ^= bit
            checked += 1
            if checked & 7 == 0:
                self.poll()
            after = _child_board(board, bit.bit_length() - 1)
            changed = after.distance(own, side)
            if changed == UNREACHABLE:
                continue
            if bit & other_paths and after.distance(enemy, 1 - side) == UNREACHABLE:
                continue
            worst = max(worst, changed - distance)
        self.risk_cache[key] = worst
        return worst

    def children(self,board,p0,p1,w0,w1,side,ply,preferred=None,include_walls=True,include_pawns=True):
        self.poll()
        key=(board.mask,p0,p1,w0,w1,side,ply,preferred,include_walls,include_pawns)
        found=self.generations.get(key)
        if found is None:
            found=self._build_children(board,p0,p1,w0,w1,side,ply,preferred,include_walls,include_pawns)
            if len(self.generations)<12000:self.generations[key]=tuple(found)
        return list(found)

    def _build_children(self, board, p0, p1, w0, w1, side, ply, preferred=None, include_walls=True, include_pawns=True):
        self.poll()
        own, enemy = (p0, p1) if side == 0 else (p1, p0)
        wm, wo = (w0, w1) if side == 0 else (w1, w0)
        destinations = _moves(board, own, enemy)
        for dest in destinations:
            if dest // 9 == board.goals[side]:
                return [(MATE - ply - 1, dest, board, dest if side == 0 else p0, dest if side == 1 else p1, w0, w1)]
        threat = _finish(board, enemy, own, 1 - side)
        result = []
        for dest in destinations + [own] if include_pawns else []:
            a, b = (dest, p1) if side == 0 else (p0, dest)
            code = dest if dest != own else PASS
            value = -_evaluate(board, a, b, w0, w1, 1 - side)
            if _finish(board, enemy, dest, 1 - side):
                value = -MATE + ply + 2
            result.append((value, code, board, a, b, w0, w1))
        if include_walls and wm and (ply + 1 < self.remaining):
            d0, d1 = (board.distance(p0, 0), board.distance(p1, 1))
            dag0, dag1 = (board.path_walls(p0, 0), board.path_walls(p1, 1))
            own_dag, enemy_dag = (dag0, dag1) if side == 0 else (dag1, dag0)
            counter = 0
            bits = own_dag & board.available
            while bits:
                bit = bits & -bits
                bits ^= bit
                index = bit.bit_length() - 1
                counter |= CONFLICTS[index] & ~bit if ply == 0 else 1 << (index ^ 64)
            root = ply == 0
            connected = 0
            placed = board.mask
            while placed:
                bit = placed & -placed
                placed ^= bit
                connected |= CONNECTED[bit.bit_length() - 1]
            available = board.available if root or threat else board.available & (enemy_dag | counter | NEAR[own] | NEAR[enemy] | connected & (own_dag | enemy_dag))
            nw0, nw1 = (w0 - 1, w1) if side == 0 else (w0, w1 - 1)
            walls = []
            checked = 0
            while available:
                bit = available & -available
                available ^= bit
                index = bit.bit_length() - 1
                checked += 1
                if checked & 7 == 0:
                    self.poll()
                after = _child_board(board, index)
                if dag0 & bit:
                    nd0 = after.distance(p0, 0)
                    if nd0 == UNREACHABLE:
                        continue
                else:
                    nd0 = d0
                    after.distances[0][p0] = d0
                    after.dags[0][p0] = dag0
                    after.flexibility[0][p0] = board.routes(p0, 0)
                if dag1 & bit:
                    nd1 = after.distance(p1, 1)
                    if nd1 == UNREACHABLE:
                        continue
                else:
                    nd1 = d1
                    after.distances[1][p1] = d1
                    after.dags[1][p1] = dag1
                    after.flexibility[1][p1] = board.routes(p1, 1)
                delta_own, delta_enemy = (nd0 - d0, nd1 - d1) if side == 0 else (nd1 - d1, nd0 - d0)
                value = -_evaluate(after, p0, p1, nw0, nw1, 1 - side)
                if _finish(after, enemy, own, 1 - side):
                    value = -MATE + ply + 2
                _, wr, wc = COORDS[index]
                proximity = abs(2 * wr + 1 - 2 * (enemy // 9)) + abs(2 * wc + 1 - 2 * (enemy % 9))
                chain = bool(CONNECTED[index] & board.mask)
                rank = value * 32 + 8 * chain - proximity + min(15, self.history[81 + index] >> 5)
                child = (value, 81 + index, after, p0, p1, nw0, nw1)
                walls.append((rank, child, bool(bit & counter), bool(delta_enemy == 0 and (bit & enemy_dag or chain)), delta_own))
            walls.sort(key=lambda item: item[0], reverse=True)
            plans = [item for item in walls if item[3] and item[4] == 0]
            plans.sort(key=lambda item: (bool(CONNECTED[item[1][1] - 81] & board.mask), bool(NEAR[enemy] & 1 << item[1][1] - 81), -abs(COORDS[item[1][1] - 81][1] - enemy // 9) - abs(COORDS[item[1][1] - 81][2] - enemy % 9), item[0]), reverse=True)
            probes = PLAN_PROBES[min(ply, 2)]
            assessed = []
            verified_plans = []
            if probes and wm >= 2 and board.mask and (perf_counter() + 0.06 < self.deadline):
                for item in plans[:probes]:
                    if perf_counter() + 0.04 >= self.deadline:
                        break
                    c = item[1]
                    offense, _ = self.pressure(c[2], enemy, own, 1 - side)
                    defense = self.pressure(c[2], own, enemy, side)[0] if wo else 0
                    robust = offense * 3 - defense
                    if _CONTROL_MODE and ply == 0 and offense >= 4 and len(assessed)<4 and perf_counter()+0.12<self.deadline:
                        response_value = self.plan_score(c[2], p0, p1, nw0, nw1, side)
                        optimistic = item[1][0] + 100 * max(0, offense - 1)
                        corrected = robust - min(12, max(0, optimistic - response_value) / 100)
                        if response_value > -MATE_BOUND and response_value >= item[1][0] - 150:
                            verified_plans.append((corrected, response_value, item))
                    assessed.append((robust, item[0], item))
                assessed.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
                plans = [entry[2] for entry in assessed] + plans[len(assessed):]
            danger = 0
            if root and wo:
                for item in sorted(walls, key=lambda item: item[4], reverse=True)[:4]:
                    if item[4] >= 2:
                        index = item[1][1] - 81
                        danger |= CONFLICTS[index] & ~(1 << index)
            if threat:
                result.extend((item[1] for item in walls if item[1][0] > -MATE_BOUND))
            else:
                limit = 14 if root else 7 if ply < 3 else 4
                selected = walls[:limit]
                included = {item[1][1] for item in selected}
                for item in plans[:PLAN_SLOTS[min(ply, 2)]]:
                    code = item[1][1]
                    self.protected.add((board.mask, side, code))
                    if code not in included:
                        selected.append(item)
                        included.add(code)
                if root:
                    for _, _, item in sorted(verified_plans, key=lambda entry: (entry[0], entry[1]), reverse=True):
                        code = item[1][1]
                        if code not in included:
                            selected.append(item)
                            included.add(code)
                            self.protected.add((board.mask, side, code))
                            break
                if danger:
                    defensive = [item for item in walls if 1 << item[1][1] - 81 & danger]
                    for item in defensive[:3]:
                        if item[1][1] not in included:
                            selected.append(item)
                            included.add(item[1][1])
                for flag, quota in ((2, 3 if root else 1),):
                    added = 0
                    for item in walls:
                        code = item[1][1]
                        if item[flag] and code not in included:
                            self.protected.add((board.mask, side, code))
                            selected.append(item)
                            included.add(code)
                            added += 1
                            if added >= quota:
                                break
                if preferred is not None and preferred not in included:
                    for item in walls:
                        if item[1][1] == preferred:
                            selected.append(item)
                            break
                result.extend((item[1] for item in selected))
        safe = [child for child in result if child[0] > -MATE_BOUND]
        if safe:
            result = safe
        killers = self.killers[ply]
        result.sort(key=lambda c: (c[1] == preferred, c[0], c[1] in killers, self.history[c[1]], c[1] != PASS), reverse=True)
        return result

    def leaf(self, board, p0, p1, w0, w1, side, ply, alpha, beta, extension):
        own, enemy = (p0, p1) if side == 0 else (p1, p0)
        if _finish(board, own, enemy, side):
            return MATE - ply - 1
        if ply + 1 >= self.remaining:
            return 0
        if extension and _finish(board, enemy, own, 1 - side):
            best = -MATE + ply + 2
            for _, _, after, a, b, x, y in self.children(board, p0, p1, w0, w1, side, ply):
                score = -self.search(after, a, b, x, y, 1 - side, 0, -beta, -alpha, ply + 1, extension - 1)
                best = max(best, score)
                alpha = max(alpha, score)
                if alpha >= beta:
                    break
            return best
        return _evaluate(board, p0, p1, w0, w1, side)

    def solved_leaf(self,board,p0,p1,side,ply):
        key=(board.mask,board.goals)
        table=_SOLVED_LEAVES.get(key)
        if table is None and self.tables_built<3 and perf_counter()+0.08<self.deadline:
            try:
                table=_Race(board,min(self.deadline-0.035,perf_counter()+0.065))
            except _Deadline:return None
            self.tables_built+=1
            if len(_SOLVED_LEAVES)>=16:_SOLVED_LEAVES.pop(next(iter(_SOLVED_LEAVES)))
            _SOLVED_LEAVES[key]=table
        if table is None:return None
        value,length=table.probe(p0,p1,side)
        if not value or length>self.remaining-ply:return 0
        return value*(MATE-ply-length)

    def search(self, board, p0, p1, w0, w1, side, depth, alpha, beta, ply, extension=2):
        self.nodes += 1
        if self.nodes & 15 == 0:
            self.poll()
        if p0 // 9 == board.goals[0]:
            return MATE - ply if side == 0 else -MATE + ply
        if p1 // 9 == board.goals[1]:
            return MATE - ply if side == 1 else -MATE + ply
        if ply >= self.remaining:
            return 0
        if w0==0 and w1==0:
            exact=self.solved_leaf(board,p0,p1,side,ply)
            if exact is not None:return exact
        if depth <= 0:
            return self.leaf(board, p0, p1, w0, w1, side, ply, alpha, beta, extension)
        left = self.remaining - ply
        key = (board.mask, p0, p1, w0, w1, side, min(left, 96), min(ply, 3))
        cached = _TT.get(key)
        preferred = cached[3] if cached else None
        if cached and cached[0] >= depth:
            value, flag = (cached[1], cached[2])
            value = value - ply if value > MATE_BOUND else value + ply if value < -MATE_BOUND else value
            if flag == 0 or (flag == 1 and value >= beta) or (flag == 2 and value <= alpha):
                return value
        original_alpha, original_beta = (alpha, beta)
        best, best_action = (-2 * MATE, PASS)
        own, enemy = (p0, p1) if side == 0 else (p1, p0)
        urgent = _finish(board, enemy, own, 1 - side)

        def staged():
            if urgent:
                yield from self.children(board, p0, p1, w0, w1, side, ply, preferred)
                return
            yielded = set()
            if preferred is not None and 81 <= preferred < PASS:
                index = preferred - 81
                stock = w0 if side == 0 else w1
                if stock and board.available & 1 << index:
                    after = _child_board(board, index)
                    if after.distance(p0, 0) < UNREACHABLE and after.distance(p1, 1) < UNREACHABLE:
                        x, y = (w0 - 1, w1) if side == 0 else (w0, w1 - 1)
                        estimate = -_evaluate(after, p0, p1, x, y, 1 - side)
                        if _finish(after, enemy, own, 1 - side):
                            estimate = -MATE + ply + 2
                        yielded.add(preferred)
                        yield (estimate, preferred, after, p0, p1, x, y)
            stock = w0 if side == 0 else w1
            if stock and not _CONTROL_MODE:
                for code in self.killers[ply]:
                    if code is None or not 81 <= code < PASS or code in yielded:
                        continue
                    index = code - 81
                    if not board.available & 1 << index:
                        continue
                    after = _child_board(board, index)
                    if after.distance(p0, 0) == UNREACHABLE or after.distance(p1, 1) == UNREACHABLE:
                        continue
                    if _finish(after, enemy, own, 1 - side):
                        continue
                    x, y = (w0 - 1, w1) if side == 0 else (w0, w1 - 1)
                    yielded.add(code)
                    yield (-_evaluate(after, p0, p1, x, y, 1 - side), code, after, p0, p1, x, y)
            for child in self.children(board, p0, p1, w0, w1, side, ply, preferred, include_walls=False):
                yielded.add(child[1])
                yield child
            stock = w0 if side == 0 else w1
            if stock:
                for child in self.children(board, p0, p1, w0, w1, side, ply, preferred, include_pawns=False):
                    if child[1] not in yielded:
                        yield child
        for number, child in enumerate(staged()):
            estimate, action, after, a, b, x, y = child
            if estimate > MATE_BOUND:
                return estimate
            next_own, next_enemy = (a, b) if side == 0 else (b, a)
            quiet_leaf = not _CONTROL_MODE and (x or y) and depth == 1 and (estimate < -MATE_BOUND or not _finish(after, next_own, next_enemy, side))
            if quiet_leaf:
                if ply + 1 >= self.remaining:
                    value = 0
                elif estimate < -MATE_BOUND:
                    value = estimate
                elif ply + 2 >= self.remaining:
                    value = 0
                else:
                    value = estimate
            elif number == 0:
                value = -self.search(after, a, b, x, y, 1 - side, depth - 1, -beta, -alpha, ply + 1)
            else:
                quiet = 81 <= action < PASS and (not urgent) and (after.distance(own, side) > 2) and (after.distance(enemy, 1 - side) > 2) and (after.distance(enemy, 1 - side) - board.distance(enemy, 1 - side) < 2)
                reduce = depth >= 4 and number >= 5 and quiet and (action != preferred) and ((board.mask, side, action) not in self.protected)
                value = -self.search(after, a, b, x, y, 1 - side, depth - 1 - int(reduce), -alpha - 1, -alpha, ply + 1)
                if reduce and value > alpha:
                    value = -self.search(after, a, b, x, y, 1 - side, depth - 1, -alpha - 1, -alpha, ply + 1)
                if alpha < value < beta:
                    value = -self.search(after, a, b, x, y, 1 - side, depth - 1, -beta, -alpha, ply + 1)
            if value > best:
                best, best_action = (value, action)
            alpha = max(alpha, value)
            if alpha >= beta:
                self.history[action] += depth * depth
                killers = self.killers[ply]
                if action != killers[0]:
                    killers[1], killers[0] = (killers[0], action)
                break
        flag = 2 if best <= original_alpha else 1 if best >= original_beta else 0
        stored = best + ply if best > MATE_BOUND else best - ply if best < -MATE_BOUND else best
        if not cached or depth >= cached[0]:
            _TT[key] = (depth, stored, flag, best_action)
        return best

    def choose(self, board, p0, p1, w0, w1):
        root = [child for child in self.children(board, p0, p1, w0, w1, 0, 0) if child[1] not in self.forbidden]
        if not root:
            root = self.children(board, p0, p1, w0, w1, 0, 0, include_walls=False)
        best = root[0][1]
        last_score = root[0][0]
        if last_score > MATE_BOUND:
            return (best, last_score)
        biases = {}
        contact = abs(p0 // 9 - p1 // 9) + abs(p0 % 9 - p1 % 9) <= 2
        for _, code, after, a, b, x, y in root:
            visits = _VISITS.get((after.mask, a, b, x, y, 1), 0)
            risk = self.vulnerability(after, a, b, 0) if y else 0
            stalled = sum((mask == after.mask and pawn == a for mask, pawn in _RECENT))
            stall_penalty = min(90, 18 * max(0, stalled - 2)) if not contact and w0 == 0 and (w1 > 0) else 0
            waiting_penalty = 40 if code == PASS and board.mask == 0 and _OPPONENT_WAITED else 0
            risk_weight = 30 if _CONTROL_MODE and w0<=3 and w1<=3 and not contact else 18
            biases[code] = -min(24, 6 * visits) - risk_weight * min(8, max(0, risk - 2)) - stall_penalty - waiting_penalty - (16 if code == PASS and (not contact) else 0) - (25 if _RACING_MODE and 81<=code<PASS else 20 if not _CONTROL_MODE and 81<=code<PASS and w1-w0>=2 else 0)
        for depth in range(1, MAX_DEPTH + 1):
            started = perf_counter()
            window = 70 if depth >= 3 and abs(last_score) < MATE_BOUND else 2 * MATE
            low = max(-2 * MATE, last_score - window)
            high = min(2 * MATE, last_score + window)
            try:
                while True:
                    scores = []
                    alpha = low
                    for number, (_, code, after, a, b, x, y) in enumerate(root):
                        self.poll()
                        bias = biases[code]
                        raw_alpha = alpha - bias if abs(alpha) < MATE_BOUND else alpha
                        raw_beta = high - bias if abs(high) < MATE_BOUND else high
                        if number == 0:
                            raw = -self.search(after, a, b, x, y, 1, depth - 1, -raw_beta, -raw_alpha, 1)
                        else:
                            raw = -self.search(after, a, b, x, y, 1, depth - 1, -raw_alpha - 1, -raw_alpha, 1)
                            adjusted = raw + bias if abs(raw) < MATE_BOUND else raw
                            if alpha < adjusted < high:
                                raw = -self.search(after, a, b, x, y, 1, depth - 1, -raw_beta, -raw_alpha, 1)
                        value = raw + bias if abs(raw) < MATE_BOUND else raw
                        scores.append((value, code, after, a, b, x, y))
                        alpha = max(alpha, value)
                        if alpha >= high:
                            break
                    scores.sort(key=lambda c: (c[0], c[1] != PASS), reverse=True)
                    value = scores[0][0]
                    if value <= low and low > -2 * MATE:
                        window *= 3
                        low = max(-2 * MATE, last_score - window)
                    elif value >= high and high < 2 * MATE:
                        best_code = scores[0][1]
                        root.sort(key=lambda c: c[1] == best_code, reverse=True)
                        window *= 3
                        high = min(2 * MATE, last_score + window)
                    else:
                        break
            except _Deadline:
                break
            previous_score = last_score
            best, last_score = (scores[0][1], scores[0][0])
            self.depth = depth
            root = scores
            stable_mate = depth >= 2 and abs(previous_score) > MATE_BOUND and (previous_score * last_score > 0)
            if abs(last_score) > MATE_BOUND and (_CONTROL_MODE or not (w0 or w1) or stable_mate):
                break
            duration = perf_counter() - started
            if perf_counter() + duration * 0.35 >= self.deadline:
                break
        return (best, last_score)

class _Race:
    """Exact W/D/L and distance-to-finish for a fixed topology, including PASS."""

    def __init__(self, board, deadline):
        self.mask, self.goals = (board.mask, board.goals)
        graph = tuple((tuple(_cells(_spread(BITS[p], board.opened))) for p in range(81)))
        contacts = {}
        for p in range(81):
            for q in graph[p]:
                contacts[p * 81 + q] = tuple(_moves(board, p, q))
        self.graph, self.contacts = (graph, contacts)
        size = 2 * 81 * 81
        result, distance = ([0] * size, [0] * size)
        degree, longest = ([0] * size, [0] * size)
        previous = [[] for _ in range(size)]
        queue = deque()
        span = 81 * 81
        for a in range(81):
            if a & 3 == 0 and perf_counter() >= deadline:
                raise _Deadline
            for b in range(81):
                if a == b:
                    continue
                winner = 0 if a // 9 == board.goals[0] else 1 if b // 9 == board.goals[1] else None
                pair = a * 81 + b
                for side in (0, 1):
                    state = side * span + pair
                    if winner is not None:
                        result[state] = 1 if winner == side else -1
                        queue.append(state)
                        continue
                    own, enemy = (a, b) if side == 0 else (b, a)
                    moves = contacts.get(own * 81 + enemy, graph[own]) + (own,)
                    degree[state] = len(moves)
                    for dest in moves:
                        child = span + dest * 81 + b if side == 0 else a * 81 + dest
                        previous[child].append(state)
        processed = 0
        while queue:
            state = queue.popleft()
            processed += 1
            if processed & 511 == 0 and perf_counter() >= deadline:
                raise _Deadline
            for parent in previous[state]:
                if result[parent]:
                    continue
                if result[state] < 0:
                    result[parent] = 1
                    distance[parent] = distance[state] + 1
                    queue.append(parent)
                else:
                    degree[parent] -= 1
                    longest[parent] = max(longest[parent], distance[state])
                    if degree[parent] == 0:
                        result[parent] = -1
                        distance[parent] = longest[parent] + 1
                        queue.append(parent)
        self.result, self.distance = (result, distance)

    def probe(self, a, b, side):
        state = side * 6561 + a * 81 + b
        return (self.result[state], self.distance[state])

    def choose(self, board, own, enemy, left):

        def opportunity(a, b, turns):
            """Draw-preserving opportunity under a simple opponent policy.

            WDL always outranks this secondary heuristic. Opponent responses
            are weighted by progress, never assumed forced. Visits discourage
            repeating a safe waiting cycle; proven wins still minimize DTM.
            """
            best = 0.0
            for dest in self.contacts.get(a * 81 + b, self.graph[a]) + (a,):
                out, length = self.probe(dest, b, 1)
                if out > 0:
                    continue
                if out < 0 and length + 1 <= turns:
                    return 1000.0 / (1 + 0.02 * (length + 1))
                if turns < 2:
                    continue
                replies = self.contacts.get(b * 81 + dest, self.graph[b]) + (b,)
                before = board.distance(b, 1)
                total = weight_sum = 0.0
                for reply in replies:
                    value, length = self.probe(dest, reply, 0)
                    weight = 8 if board.distance(reply, 1) < before else 1
                    if reply == b:
                        weight = 2
                    reward = 0.0
                    if value > 0 and length <= turns - 2:
                        reward = 1000.0 / (1 + 0.02 * length)
                    total += weight * reward
                    weight_sum += weight
                best = max(best, total / weight_sum if weight_sum else 0)
            return best
        best, best_key = (PASS, None)
        for dest in self.contacts.get(own * 81 + enemy, self.graph[own]) + (own,):
            outcome, distance = self.probe(dest, enemy, 1)
            outcome, distance = (-outcome, distance + 1)
            if distance > left:
                outcome = 0
            pace = -distance if outcome > 0 else distance if outcome < 0 else 0
            pressure = 0
            if outcome == 0 and left >= 2:
                replies = self.contacts.get(enemy * 81 + dest, self.graph[enemy]) + (enemy,)
                for reply in replies:
                    value, length = self.probe(dest, reply, 0)
                    if value > 0 and length <= left - 2:
                        pressure += 1
                pressure /= len(replies)
                value_sum = weight_sum = 0.0
                for reply in replies:
                    value, length = self.probe(dest, reply, 0)
                    weight = 8 if board.distance(reply, 1) < board.distance(enemy, 1) else 1
                    if reply == enemy:
                        weight = 2
                    reward = 1000.0 / (1 + 0.02 * length) if value > 0 and length <= left - 2 else 0.75 * opportunity(dest, reply, left - 2) if value == 0 else 0
                    value_sum += weight * reward
                    weight_sum += weight
                pressure = value_sum / weight_sum if weight_sum else 0
                visits = max(_VISITS.get((board.mask, dest, enemy, 0, 0, 0), 0), sum((mask == board.mask and pawn == dest for mask, pawn in _RECENT)))
                pressure -= min(120, 12 * visits)
            key = (outcome, pace, pressure, -board.distance(dest, 0), dest != own)
            if best_key is None or key > best_key:
                best_key, best = (key, PASS if dest == own else dest)
        return best

def _frozen_distance(board, own, enemy, side):
    """Exact pawn distance if the other pawn stays fixed; ordering only."""
    goal = ROWS[board.goals[side]]
    links = []
    for start in _cells(_spread(BITS[enemy], board.opened)):
        destinations = 0
        for dest in _moves(board, start, enemy):
            destinations |= BITS[dest]
        links.append((BITS[start], destinations))
    seen, front, distance = (BITS[own] | BITS[enemy], BITS[own], 0)
    while front:
        if front & goal:
            return distance
        following = _spread(front, board.opened)
        for start, destinations in links:
            if front & start:
                following |= destinations
        front = following & ~seen
        seen |= front
        distance += 1
    return UNREACHABLE

def _short_finish(board, own, enemy, enemy_stock, left, deadline):
    """Prove a win in THREE plies against EVERY legal opponent response.

    Immediate wins are handled by the caller. A successful certificate is
    therefore the fastest possible finish from this turn. No assumption
    about an opponent policy, path, wall subset or cooperation is used.
    """
    if left < 3 or abs(own // 9 - board.goals[0]) > 4:
        return None
    checked = 0
    for dest in _moves(board, own, enemy):
        if not _finish(board, dest, enemy, 0):
            continue
        if _finish(board, enemy, dest, 1):
            continue
        safe = True
        for reply in _moves(board, enemy, dest):
            checked += 1
            if reply // 9 == board.goals[1] or not _finish(board, dest, reply, 0):
                safe = False
                break
        if not safe:
            continue
        for index in _cells(board.available) if enemy_stock else []:
            checked += 1
            if checked & 7 == 0 and perf_counter() >= deadline:
                return None
            after = _child_board(board, index)
            if after.distance(dest, 0) == UNREACHABLE or after.distance(enemy, 1) == UNREACHABLE:
                continue
            if not _finish(after, dest, enemy, 0):
                safe = False
                break
        if safe and perf_counter() < deadline:
            return (dest, checked)
    return None

def _accelerate_race(board, own, enemy, stock, race, left, deadline):
    """Prove a faster win by placing ONE wall then using a fixed-board solver.

    The opponent has zero walls. Each accepted wall preserves both routes
    and solves the state with the OPPONENT to move, so their immediate finish,
    evasions, jumps and pass are all included. Partial tables are discarded.
    """
    if not stock or perf_counter() >= deadline:
        return None
    result, length = race.probe(own, enemy, 0)
    limit = length if result > 0 and length <= left else left + 1
    candidates = []
    original = board.distance(own, 0)
    for index in _cells(board.available):
        after = _child_board(board, index)
        dm, do = (after.distance(own, 0), after.distance(enemy, 1))
        if dm == UNREACHABLE or do == UNREACHABLE or _finish(after, enemy, own, 1):
            continue
        route = _frozen_distance(after, own, enemy, 0)
        if route == UNREACHABLE:
            continue
        rank = 100 * (do - dm) - 30 * max(0, dm - original) + 20 * len(_moves(after, own, enemy))
        candidates.append((rank, index, after, route))
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    portfolio = candidates[:3]
    included = {entry[1] for entry in portfolio}
    for entry in sorted(candidates, key=lambda entry: (entry[3], -entry[0])):
        if entry[1] not in included:
            portfolio.append(entry)
            included.add(entry[1])
            if len(portfolio) >= 5:
                break
    best = None
    for _, index, after, _ in portfolio:
        if perf_counter() + 0.025 >= deadline:
            break
        try:
            solved = _Race(after, min(deadline, perf_counter() + 0.065))
        except _Deadline:
            continue
        outcome, distance = solved.probe(own, enemy, 1)
        distance += 1
        if outcome < 0 and distance <= left and (distance < limit):
            limit = distance
            best = (81 + index, solved, distance)
    return best

class _OptionalWallRace:
    """Exact pawn/pass game with ONE optional wall from a bounded portfolio.

    Enemy has no walls. A win is valid in the real game even if we have
    additional walls. Losses are only about the restricted portfolio.
    DTM includes every pawn move, PASS and the eventual wall action.
    """
    def __init__(self, board, base, exits, deadline):
        from heapq import heappush, heappop
        n,span=13122,6561
        result=[0]*n; distance=[0]*n; policy=[PASS]*n
        degree=[0]*n;longest=[0]*n;previous=[[] for _ in range(n)]
        queue=[]
        # A wall may be legal after walking even if illegal at today's pawns.
        reachable=[]
        for index,child,table in exits:
            reachable.append((index,table,
                [child.distance(p,0)<UNREACHABLE for p in range(81)],
                [child.distance(p,1)<UNREACHABLE for p in range(81)]))
        for a in range(81):
            if a&3==0 and perf_counter()>=deadline:raise _Deadline
            for b in range(81):
                if a==b:continue
                pair=a*81+b
                winner=0 if a//9==board.goals[0] else 1 if b//9==board.goals[1] else None
                for side in (0,1):
                    state=side*span+pair
                    if winner is not None:
                        result[state]=1 if winner==side else -1
                        heappush(queue,(0,state));continue
                    own,enemy=(a,b) if side==0 else (b,a)
                    moves=base.contacts.get(own*81+enemy,base.graph[own])+(own,)
                    degree[state]=len(moves)
                    for dest in moves:
                        child=span+dest*81+b if side==0 else a*81+dest
                        previous[child].append(state)
                state=pair
                if winner is not None:continue
                for index,table,ra,rb in reachable:
                    if not (ra[a] and rb[b]):continue
                    value,length=table.probe(a,b,1);length+=1
                    if value<0:
                        if result[state]!=1 or length<distance[state]:
                            result[state]=1;distance[state]=length;policy[state]=81+index
                    elif value==0:
                        # A drawing wall exit prevents a forced loss.
                        degree[state]+=1
                    else:longest[state]=max(longest[state],length-1)
                if result[state]==1:heappush(queue,(distance[state],state))
        processed=0
        while queue:
            length,state=heappop(queue)
            if length!=distance[state]:continue
            processed+=1
            if processed&255==0 and perf_counter()>=deadline:raise _Deadline
            for parent in previous[state]:
                candidate=length+1
                if result[state]<0:
                    if result[parent]==-1:continue
                    if result[parent]==0 or candidate<distance[parent]:
                        result[parent]=1;distance[parent]=candidate
                        a,b=divmod(parent%span,81)
                        ca,cb=divmod(state%span,81)
                        dest=ca if parent<span else cb
                        old=a if parent<span else b
                        policy[parent]=PASS if dest==old else dest
                        heappush(queue,(candidate,parent))
                elif not result[parent]:
                    degree[parent]-=1;longest[parent]=max(longest[parent],length)
                    if degree[parent]==0:
                        result[parent]=-1;distance[parent]=longest[parent]+1
                        heappush(queue,(distance[parent],parent))
        self.mask=board.mask;self.result=result;self.distance=distance;self.policy=policy
        self.walls=tuple(index for index,_,_ in exits)

    def probe(self,a,b,side):
        state=side*6561+a*81+b
        return self.result[state],self.distance[state]

    def choose(self,a,b):return self.policy[a*81+b]

_ORACLE_MASK=None
_ORACLE_TABLES={}
_ORACLE_SOLUTION=None

def _optional_wall_finish(board,own,enemy,stock,base,left,deadline):
    """Build a reusable winning strategy: walk first, wall later if needed."""
    global _ORACLE_MASK,_ORACLE_TABLES,_ORACLE_SOLUTION
    if _ORACLE_MASK!=(board.mask,board.goals):
        _ORACLE_MASK=(board.mask,board.goals);_ORACLE_TABLES={};_ORACLE_SOLUTION=None
    if _ORACLE_SOLUTION is not None:
        value,length=_ORACLE_SOLUTION.probe(own,enemy,0)
        if value>0 and length<=left:return _ORACLE_SOLUTION.choose(own,enemy),length
    candidates=[]
    dm,do=board.distance(own,0),board.distance(enemy,1)
    for index in _cells(board.available):
        child=_child_board(board,index)
        ndm,ndo=child.distance(own,0),child.distance(enemy,1)
        legal=ndm<UNREACHABLE and ndo<UNREACHABLE
        route=_frozen_distance(child,own,enemy,0) if legal else UNREACHABLE
        rank=100*((ndo-do)-(ndm-dm)) if legal else -100000
        candidates.append((rank,index,child,route))
    candidates.sort(reverse=True,key=lambda x:x[0])
    portfolio=candidates[:4]
    included={x[1] for x in portfolio}
    for item in sorted(candidates,key=lambda x:(x[3],-x[0])):
        if item[1] not in included:
            portfolio.append(item);included.add(item[1])
        if len(portfolio)>=7:break
    added=0
    for _,index,child,_ in portfolio:
        if index in _ORACLE_TABLES:continue
        # Reserve time to solve the lifted pawn graph, rather than consuming
        # the entire budget building isolated wall tables.
        if perf_counter()+0.14>=deadline or len(_ORACLE_TABLES)>=12:break
        try:table=_Race(child,min(deadline-0.10,perf_counter()+0.065))
        except _Deadline:break
        _ORACLE_TABLES[index]=(index,child,table);added+=1
        if added>=4:break
    if _ORACLE_TABLES and (added or _ORACLE_SOLUTION is None):
        try:
            solved=_OptionalWallRace(board,base,list(_ORACLE_TABLES.values()),deadline)
            _ORACLE_SOLUTION=solved
        except _Deadline:return None
    if _ORACLE_SOLUTION is not None:
        value,length=_ORACLE_SOLUTION.probe(own,enemy,0)
        if value>0 and length<=left:return _ORACLE_SOLUTION.choose(own,enemy),length
    return None


def _action(code):
    if code < 81:
        return {'type': 'move', 'to': [code // 9, code % 9]}
    if code == PASS:
        return {'type': 'pass'}
    orientation, r, c = COORDS[code - 81]
    return {'type': 'wall', 'orientation': orientation, 'row': r, 'col': c}

def _legal(action, obs):
    if action.get('type') == 'move':
        return action.get('to') in obs['legal_moves']
    if action.get('type') == 'wall':
        return {k: action[k] for k in ('orientation', 'row', 'col')} in obs['legal_walls']
    return action.get('type') == 'pass'

def _read(obs):
    global _CONTEXT, _LAST_TURN, _ENDGAME, _PREVIOUS_OPPONENT, _OPPONENT_WAITED
    goals = (obs['you']['goal_row'], obs['opponent']['goal_row'])
    turn = obs['turn']
    if goals != _CONTEXT or turn <= _LAST_TURN:
        _BOARDS.clear()
        _TT.clear()
        _VISITS.clear()
        _RECENT.clear()
        _PREVIOUS_OPPONENT = None
        _ENDGAME = None
    _CONTEXT, _LAST_TURN = (goals, turn)
    mask = 0
    available = ALL_WALLS
    opened = list(EMPTY_OPEN)
    for offset, name in ((0, 'horizontal'), (64, 'vertical')):
        for r, c in obs['walls'][name]:
            index = offset + r * 8 + c
            mask |= 1 << index
            available &= ~CONFLICTS[index]
            for i in range(4):
                opened[i] &= ~CUTS[index][i]
    board = _BOARDS.get(mask)
    if board is None:
        board = _Board(mask, tuple(opened), available, goals)
        _BOARDS[mask] = board
    if len(_TT) > 100000:
        for key in list(_TT)[:20000]:
            del _TT[key]
    a, b = (obs['you']['position'], obs['opponent']['position'])
    p0, p1 = (a[0] * 9 + a[1], b[0] * 9 + b[1])
    w0, w1 = (obs['you']['walls_remaining'], obs['opponent']['walls_remaining'])
    _OPPONENT_WAITED = _PREVIOUS_OPPONENT == (turn - 2, p1, w1)
    _PREVIOUS_OPPONENT = (turn, p1, w1)
    state = (mask, p0, p1, w0, w1, 0)
    _VISITS[state] = _VISITS.get(state, 0) + 1
    _RECENT.append((mask, p0))
    del _RECENT[:-10]
    return (board, p0, p1, w0, w1)
_FORTRESS_CACHE = {}

def permanent_fortress(board, victim, defender, victim_side, cache=None):
    """Sound non-loss certificate for the stationary defender.

    A FALSE result means unproved, not that the defender necessarily loses.
    Terminal or invalid states return False: this function certifies an
    ongoing strategy and is not intended to override terminal game results.

    The overapproximation adds EVERY currently physically possible straight
    jump and diagonal around defender, including diagonals that become legal
    only after a future wall blocks a straight jump. Future walls only remove
    ordinary edges and jump endpoints. Thus every future legal victim move is
    already in this reachability graph. No goal in that graph implies no goal
    after any legal future topology change.
    """
    if victim_side not in (0, 1) or not 0 <= victim < 81 or (not 0 <= defender < 81) or (victim == defender):
        return False
    if defender // 9 != board.goals[victim_side]:
        return False
    if victim // 9 == board.goals[victim_side] or defender // 9 == board.goals[1 - victim_side]:
        return False
    memo = _FORTRESS_CACHE if cache is None else cache
    key = (tuple(board.opened), tuple(board.goals), victim, defender, victim_side)
    if key in memo:
        return memo[key]
    defender_bit = BITS[defender]
    neighbors = _spread(defender_bit, board.opened)
    seen = defender_bit | BITS[victim]
    front = BITS[victim]
    goal = ROWS[board.goals[victim_side]]
    while front:
        if front & goal:
            value = False
            break
        following = _spread(front, board.opened)
        if front & neighbors:
            following |= neighbors
        front = following & ~seen
        seen |= front
    else:
        value = True
    if len(memo) >= 8192:
        memo.clear()
    memo[key] = value
    return value

class _LastWallRefuter:

    def __init__(self, deadline, max_counterwalls=6, table_cache=None):
        self.deadline = deadline
        self.max_counterwalls = max_counterwalls
        self.tables = {} if table_cache is None else table_cache
        self.promoted = []
        self.tables_built = 0
        self.attempts = 0

    def _solve(self, board):
        key = (board.mask, tuple(board.goals))
        if key in self.tables:
            return self.tables[key]
        if perf_counter() + 0.008 >= self.deadline:
            raise _Deadline
        table = _Race(board, min(self.deadline, perf_counter() + 0.06))
        self.tables_built += 1
        if len(self.tables) >= 32:
            self.tables.pop(next(iter(self.tables)))
        self.tables[key] = table
        return table

    def refute(self, board, own, enemy, own_stock, enemy_stock, code, left):
        """Return a structured exact losing witness, or UNKNOWN.

        Input is BEFORE our action, our pawn is normalized player 0, and `code`
        is an Astro action code. The proof assumes we actually spend our last
        wall. After that, an enemy fixed-topology winning strategy remains a
        valid strategy because the enemy may voluntarily ignore spare walls.
        """
        unknown = dict(status='unknown', tables_built=self.tables_built)
        if own_stock != 1 or not 81 <= code < PASS or left < 2:
            return dict(status='not_applicable')
        index = code - 81
        if not board.available & 1 << index:
            return dict(status='not_applicable')
        after = _child_board(board, index)
        if after.distance(own, 0) == UNREACHABLE or after.distance(enemy, 1) == UNREACHABLE:
            return dict(status='not_applicable')
        self.attempts += 1
        try:
            solved = self._solve(after)
            value, dtm = solved.probe(own, enemy, 1)
            if value > 0 and 1 + dtm <= left:
                return dict(status='refuted', our_action=code, kind='enemy_freezes_without_wall', dtm=1 + dtm, tables_built=self.tables_built)
            if not enemy_stock or left < 3:
                return dict(status='unknown', tables_built=self.tables_built)
            dm, do = (after.distance(own, 0), after.distance(enemy, 1))
            gates = after.gates(own, 0)
            candidates = []
            for n, j in enumerate(_cells(after.available)):
                if n & 7 == 0 and perf_counter() + 0.008 >= self.deadline:
                    return dict(status='unknown', tables_built=self.tables_built)
                child = _child_board(after, j)
                ndm, ndo = (child.distance(own, 0), child.distance(enemy, 1))
                if ndm == UNREACHABLE or ndo == UNREACHABLE:
                    continue
                _, r, c = COORDS[j]
                near = abs(2 * r + 1 - 2 * (own // 9)) + abs(2 * c + 1 - 2 * (own % 9))
                rank = (j in self.promoted, 100 * (ndm - dm - (ndo - do)), bool(gates & 1 << j), -near)
                candidates.append((rank, j, child))
            candidates.sort(key=lambda item: item[0], reverse=True)
            for _, j, child in candidates[:self.max_counterwalls]:
                solved = self._solve(child)
                value, dtm = solved.probe(own, enemy, 0)
                if value < 0 and 2 + dtm <= left:
                    if j in self.promoted:
                        self.promoted.remove(j)
                    self.promoted.insert(0, j)
                    self.promoted = self.promoted[:12]
                    return dict(status='refuted', our_action=code, kind='enemy_counterwall_then_freezes', enemy_wall=81 + j, enemy_wall_geometry=COORDS[j], dtm=2 + dtm, tables_built=self.tables_built)
        except _Deadline:
            return dict(status='unknown', reason='deadline', tables_built=self.tables_built)
        return dict(status='unknown', reason='no_witness_in_examined_subset', tables_built=self.tables_built)

def _decide(observation):
    """Return a legal action before the arena's one-second deadline."""
    global _ENDGAME, _LAST_STATS
    started = perf_counter()
    obs = observation
    moves = obs.get('legal_moves') or []
    goal = obs['you']['goal_row']
    for move in moves:
        if move[0] == goal:
            _LAST_STATS = {'mode': 'immediate_win', 'depth': 0}
            return {'type': 'move', 'to': list(move)}
    if obs['turn'] < 4 and (not obs['walls']['horizontal']) and (not obs['walls']['vertical']) and (obs['you']['position'][1] == 4) and (obs['opponent']['position'][1] == 4):
        current = obs['you']['position']
        step = -1 if current[0] > goal else 1
        forward = [current[0] + step, 4]
        if forward in moves:
            _LAST_STATS = {'mode': 'opening', 'depth': 0}
            return {'type': 'move', 'to': forward}
    if obs['turn'] == 5 and obs['you']['goal_row'] == 8 and (obs['you']['position'] == [2, 4]) and (obs['opponent']['position'] == [6, 4]) and (obs['walls']['horizontal'] == [[6, 3]]) and (not obs['walls']['vertical']) and (obs['you']['walls_remaining'] == 10) and (obs['opponent']['walls_remaining'] == 9):
        action = {'type': 'wall', 'orientation': 'H', 'row': 5, 'col': 4}
        if _legal(action, obs):
            _LAST_STATS = {'mode': 'opening_reply', 'depth': 0}
            return action
    distances = obs['distances']
    if obs['turn'] == 4 and (not obs['walls']['horizontal']) and (not obs['walls']['vertical']) and (obs['you']['position'] == ([6, 4] if goal == 0 else [2, 4])) and (obs['opponent']['position'] == ([2, 4] if goal == 0 else [6, 4])):
        forward=[5,4] if goal==0 else [3,4]
        if forward in moves:
            _LAST_STATS={"mode":"opening_advance","depth":0}
            return {"type":"move","to":forward}
    # Advance before guarding the rear: use the tempo to enter the center,
    # then prevent the opponent from gaining a straight jump through us.
    if (obs['turn']==6 and not obs['walls']['horizontal'] and not obs['walls']['vertical']
            and obs['you']['position']==([5,4] if goal==0 else [3,4])
            and obs['opponent']['position']==([3,4] if goal==0 else [5,4])):
        wall={'type':'wall','orientation':'H','row':5 if goal==0 else 2,'col':3}
        if _legal(wall,obs):
            _LAST_STATS={"mode":"deferred_rear_guard","depth":0}
            return wall
    fallback = {'type': 'pass'}
    if moves:
        dest = min(moves, key=lambda p: (distances[p[0]][p[1]] if distances[p[0]][p[1]] >= 0 else UNREACHABLE, abs(p[1] - 4)))
        fallback = {'type': 'move', 'to': list(dest)}
    try:
        board, p0, p1, w0, w1 = _read(obs)
        left = obs['max_turns'] - obs['turn']
        short = _short_finish(board, p0, p1, w1, left, min(started + 0.045, started + TIME_BUDGET * 0.15))
        if short is not None:
            dest, checked = short
            action = _action(dest)
            if _legal(action, obs):
                _LAST_STATS = {'mode': 'proved_short_finish', 'plies': 3, 'replies_checked': checked, 'elapsed': perf_counter() - started}
                return action
        forward = -1 if board.goals[0] < p0 // 9 else 1
        if board.mask == 0 and w0 and _OPPONENT_WAITED and (p0 % 9 == p1 % 9) and (p1 // 9 == p0 // 9 + 2 * forward):
            row = p0 // 9 if forward < 0 else p0 // 9 - 1
            before_self = board.distance(p0, 0)
            before_enemy = board.distance(p1, 1)
            for col in (p0 % 9 - 1, p0 % 9):
                if 0 <= row < 8 and 0 <= col < 8:
                    code = 81 + row * 8 + col
                    action = _action(code)
                    if _legal(action, obs):
                        after = _child_board(board, code - 81)
                        if after.distance(p0, 0) == before_self and before_enemy < after.distance(p1, 1) < UNREACHABLE:
                            _LAST_STATS = {'mode': 'opening_counter', 'depth': 0}
                            return action
        if _finish(board, p1, p0, 1):
            safe_moves = [p for p in moves if not _finish(board, p1, p[0] * 9 + p[1], 1)]
            if safe_moves:
                dest = min(safe_moves, key=lambda p: board.distance(p[0] * 9 + p[1], 0))
                fallback = {'type': 'move', 'to': list(dest)}
            elif w0:
                for wall in obs['legal_walls']:
                    index = (64 if wall['orientation'] == 'V' else 0) + wall['row'] * 8 + wall['col']
                    if not _finish(_child_board(board, index), p1, p0, 1):
                        fallback = dict(type='wall', **wall)
                        break
        if w1 == 0:
            try:
                if _ENDGAME is None or _ENDGAME.mask != board.mask:
                    _ENDGAME = _Race(board, min(started + 0.19, started + TIME_BUDGET * 0.4))
                result, length = _ENDGAME.probe(p0, p1, 0)
                # Safely improve the original fixed-board shortcut as well.
                if w0 and result == 1 and 3 < length <= 18 and length <= left:
                    incumbent = (_ENDGAME.choose(board, p0, p1, left), length)
                    certified = _widen_known_win(board, p0, p1, w0, _ENDGAME,
                        left, min(started+0.36, started+TIME_BUDGET-0.16), incumbent)
                    if certified is not None and certified[1] < length:
                        action = _action(certified[0])
                        if _legal(action, obs):
                            _LAST_STATS = {'mode': 'proved_accelerated_finish',
                                'plies': certified[1], 'fixed_board_plies': length,
                                'elapsed': perf_counter() - started}
                            return action
                if w0 and (result != 1 or length > 18):
                    certified=_optional_wall_finish(board,p0,p1,w0,_ENDGAME,left,
                        min(started+(0.36 if w0==1 else (0.57 if _RACING_MODE else 0.55)),started+TIME_BUDGET-0.16))
                    if certified is not None:
                        code,distance=certified;action=_action(code)
                        if _legal(action,obs):
                            _LAST_STATS={"mode":"proved_optional_wall_finish","plies":distance,
                                "portfolio":len(_ORACLE_TABLES),"elapsed":perf_counter()-started}
                            return action
                if w0 and (result != 1 or length > 18):
                    improved = _accelerate_race(board, p0, p1, w0, _ENDGAME, left, min(started + 0.48, started + TIME_BUDGET - 0.18))
                    if improved is not None:
                        code, solved, distance = improved
                        action = _action(code)
                        if _legal(action, obs):
                            _ENDGAME = solved
                            _LAST_STATS = {'mode': 'proved_wall_finish', 'plies': distance, 'previous_outcome': result, 'previous_plies': length, 'elapsed': perf_counter() - started}
                            return action
                if w0 == 0 or (result == 1 and length <= left):
                    action = _action(_ENDGAME.choose(board, p0, p1, left))
                    if _legal(action, obs):
                        _LAST_STATS = {'mode': 'exact_endgame', 'outcome': result, 'plies': length, 'elapsed': perf_counter() - started}
                        return action
            except _Deadline:
                pass
        last_wall_guard = w0 == 1
        search = _Search(started + (0.56 if last_wall_guard else TIME_BUDGET), left)
        code, score = search.choose(board, p0, p1, w0, w1)
        safety_checks = []
        forbidden = set()
        if last_wall_guard and 81 <= code < PASS:
            refuter = _LastWallRefuter(min(started + 0.71, started + TIME_BUDGET - 0.06))
            for _ in range(2):
                if not 81 <= code < PASS or perf_counter() + 0.009 >= refuter.deadline:
                    break
                witness = refuter.refute(board, p0, p1, w0, w1, code, left)
                safety_checks.append(witness)
                if witness['status'] != 'refuted':
                    break
                forbidden.add(code)
                replacement = _Search(started + TIME_BUDGET, left)
                replacement.forbidden = set(forbidden) | {81 + i for i in _cells(board.available)}
                try:
                    code, score = replacement.choose(board, p0, p1, w0, w1)
                    search = replacement
                except _Deadline:
                    candidates = [p for p in _moves(board, p0, p1) + [p0] if not _finish(board, p1, p, 1)]
                    dest = min(candidates or [p0], key=lambda p: board.distance(p, 0))
                    code, score = (PASS if dest == p0 else dest, 0)
                    search = replacement
                    break
        if score < 0 and p0 // 9 == board.goals[1] and permanent_fortress(board, p1, p0, 1):
            _LAST_STATS = {'mode': 'proved_draw_fallback', 'attack_score': score, 'depth': search.depth, 'nodes': search.nodes}
            return {'type': 'pass'}
        action = _action(code)
        if _legal(action, obs):
            fallback = action
        _LAST_STATS = {'mode': 'last_wall_refuted' if forbidden else 'search', 'depth': search.depth, 'nodes': search.nodes, 'score': score, 'elapsed': perf_counter() - started, 'last_wall_checks': safety_checks}
        if _legal(fallback, obs):
            _, a, b, x, y = (code, p0, p1, w0, w1)
            mask = board.mask
            if code < 81:
                a = code
            elif code < PASS:
                mask |= 1 << code - 81
                x -= 1
            state = (mask, a, b, x, y, 1)
            _VISITS[state] = _VISITS.get(state, 0) + 1
    except Exception as error:
        _LAST_STATS = {'mode': 'fallback', 'error': type(error).__name__, 'elapsed': perf_counter() - started}
    return fallback

def choose_action(observation):
    """One search core; the rear-wall opening enables setup verification."""
    global _CONTROL_MODE,_RACING_MODE,TIME_BUDGET,_POLICY,_POLICY_TURN,_POLICY_GOAL
    obs=observation;turn=obs['turn'];goal=obs['you']['goal_row']
    _RACING_MODE=turn%2==0
    TIME_BUDGET=0.82 if _RACING_MODE else 0.78
    if turn<=_POLICY_TURN or goal!=_POLICY_GOAL:_POLICY=None
    _POLICY_TURN=turn;_POLICY_GOAL=goal
    if _POLICY is None and turn>=5:
        _POLICY=(goal==8 and turn==5 and obs['you']['position']==[2,4]
            and obs['opponent']['position']==[6,4]
            and obs['walls']['horizontal']==[[6,3]] and not obs['walls']['vertical']
            and obs['opponent']['walls_remaining']==9)
    _CONTROL_MODE=bool(_POLICY)
    return _decide(obs)

# Candidate 01: certified, anytime acceleration of v3's winning endgames.
# Enemy has zero walls. Heuristic evaluation, opening and PVS are unchanged.
_LEGACY_OPTIONAL_FINISH = _optional_wall_finish
_FAST_KEY = None
_FAST_TABLES = {}
_FAST_SOLUTION = None
_FAST_SOLVED_KEYS = frozenset()
_FAST_STATS = {}
_FAST_LIMIT = 64


def _fast_prepare(board):
    global _FAST_KEY, _FAST_TABLES, _FAST_SOLUTION, _FAST_SOLVED_KEYS
    key = (board.mask, board.goals)
    if _FAST_KEY != key:
        _FAST_KEY = key
        _FAST_TABLES = {}
        _FAST_SOLUTION = None
        _FAST_SOLVED_KEYS = frozenset()


def _fast_action_legal(board, own, enemy, stock, code):
    if code == PASS:
        return True
    if 0 <= code < 81:
        return code in _moves(board, own, enemy)
    if stock and 81 <= code < PASS:
        index = code - 81
        if board.available & (1 << index):
            after = _child_board(board, index)
            return after.distance(own, 0) < UNREACHABLE and after.distance(enemy, 1) < UNREACHABLE
    return False


def _fast_proof(board, own, enemy, stock, left):
    if not stock or _FAST_SOLUTION is None or _FAST_KEY != (board.mask, board.goals):
        return None
    value, length = _FAST_SOLUTION.probe(own, enemy, 0)
    if value > 0 and length <= left:
        code = _FAST_SOLUTION.choose(own, enemy)
        if _fast_action_legal(board, own, enemy, stock, code):
            return code, length
    return None


def _fast_base_proof(board, own, enemy, base, left):
    value, length = base.probe(own, enemy, 0)
    if value > 0 and length <= left:
        return base.choose(board, own, enemy, left), length
    return None


def _widen_known_win(board, own, enemy, stock, base, left, deadline, incumbent, allow_discovery=False):
    """Keep a proven win and publish only completed, strictly faster proofs.

    The lifted graph includes every opponent pawn/PASS reply. Our extra walls
    may be ignored. Partial construction is never used as a game value.
    """
    global _FAST_SOLUTION, _FAST_SOLVED_KEYS
    _fast_prepare(board)
    _FAST_STATS.clear()
    cached = _fast_proof(board, own, enemy, stock, left)
    best = incumbent
    if cached is not None and (best is None or cached[1] < best[1]):
        best = cached
    if (best is None and not allow_discovery) or not stock:
        return best
    _FAST_STATS.update(mode='certified', incumbent_plies=incumbent[1] if incumbent else None,
                       selected_plies=best[1] if best else None, tables=len(_FAST_TABLES))
    if perf_counter()+(0.055 if allow_discovery else 0.10) >= deadline:
        return best
    try:
        if _ORACLE_MASK == (board.mask, board.goals):
            _FAST_TABLES.update(_ORACLE_TABLES)
        dm, do = board.distance(own, 0), board.distance(enemy, 1)
        candidates = []
        for n,index in enumerate(_cells(board.available)):
            if index in _FAST_TABLES:
                continue
            if n & 3 == 0 and perf_counter()+(0.055 if allow_discovery else 0.10) >= deadline:
                break
            child = _child_board(board,index)
            a,b = child.distance(own,0),child.distance(enemy,1)
            rank = (b-do)-(a-dm) if max(a,b)<UNREACHABLE else -100000
            candidates.append((rank,index,child))
        candidates.sort(key=lambda item:(item[0],item[1]), reverse=True)
        added = 0
        for _,index,child in candidates:
            if len(_FAST_TABLES)>=_FAST_LIMIT or perf_counter()+(0.08 if allow_discovery else 0.18)>=deadline:
                break
            table = _Race(child,min(deadline-(0.045 if allow_discovery else 0.13),perf_counter()+(0.035 if allow_discovery else 0.06)))
            _FAST_TABLES[index] = (index,child,table)
            added += 1
            if added>=(1 if allow_discovery else 3):
                break
        keys = frozenset(_FAST_TABLES)
        if keys and keys!=_FAST_SOLVED_KEYS and perf_counter()+(0.035 if allow_discovery else 0.07)<deadline:
            solved = _OptionalWallRace(board,base,list(_FAST_TABLES.values()),deadline)
            _FAST_SOLUTION = solved
            _FAST_SOLVED_KEYS = keys
        improved = _fast_proof(board,own,enemy,stock,left)
        if improved is not None and (best is None or improved[1]<best[1]):
            best = improved
        _FAST_STATS.update(tables=len(_FAST_TABLES),solved_tables=len(_FAST_SOLVED_KEYS),
                           selected_plies=best[1] if best else None,mode='shorter_certificate' if incumbent and best[1]<incumbent[1] else 'certified')
    except _Deadline:
        _FAST_STATS['mode'] = 'retain_certificate_at_deadline'
    except Exception as error:
        _FAST_STATS.update(mode='retain_certificate_on_error',error=type(error).__name__)
    return best


def _optional_wall_finish(board,own,enemy,stock,base,left,deadline):
    _fast_prepare(board)
    backup = _fast_base_proof(board,own,enemy,base,left)
    cached = _fast_proof(board,own,enemy,stock,left)
    if cached is not None and (backup is None or cached[1]<backup[1]):
        backup = cached
    try:
        original = _LEGACY_OPTIONAL_FINISH(board,own,enemy,stock,base,left,deadline)
    except Exception:
        if backup is not None:
            return backup
        raise
    best = original
    if backup is not None and (best is None or backup[1]<best[1]):
        best = backup
    return _widen_known_win(board,own,enemy,stock,base,left,deadline,best)


_CANDIDATE_ENTRY = choose_action


def choose_action(observation):
    """Choose v3's action first; spend spare time only on a winning proof."""
    global _LAST_STATS
    started = perf_counter()
    incumbent_action = _CANDIDATE_ENTRY(observation)
    if (observation['opponent']['walls_remaining'] or
            not observation['you']['walls_remaining'] or
            _LAST_STATS.get('mode') not in ('search','last_wall_refuted','proved_draw_fallback') or
            perf_counter()+0.07 >= started+0.88):
        return incumbent_action
    try:
        goals = (observation['you']['goal_row'],observation['opponent']['goal_row'])
        mask = 0
        for offset,name in ((0,'horizontal'),(64,'vertical')):
            for r,c in observation['walls'][name]:
                mask |= 1 << (offset+8*r+c)
        board = _BOARDS.get(mask)
        if board is None or board.goals != goals:
            return incumbent_action
        own = 9*observation['you']['position'][0]+observation['you']['position'][1]
        enemy = 9*observation['opponent']['position'][0]+observation['opponent']['position'][1]
        stock = observation['you']['walls_remaining']
        left = observation['max_turns']-observation['turn']
        deadline = started+0.88
        base = _ENDGAME
        if base is None or base.mask != mask or base.goals != goals:
            if perf_counter()+0.09 >= deadline:
                return incumbent_action
            base = _Race(board,min(deadline-0.06,perf_counter()+0.035))
        # No original search is shortened: the fallback action was already
        # computed. Failed or unfinished discovery leaves that action intact.
        certified = _widen_known_win(board,own,enemy,stock,base,left,deadline,None,True)
        if certified is not None:
            action = _action(certified[0])
            if _legal(action,observation):
                _LAST_STATS = {'mode':'proved_postsearch_finish','plies':certified[1],
                    'baseline_action':incumbent_action,'elapsed':perf_counter()-started}
                return action
    except Exception:
        pass
    return incumbent_action

_ENGINE_ENTRY=choose_action
def choose_action(observation):
    enabled=gc.isenabled();gc.disable()
    try:return _ENGINE_ENTRY(observation)
    finally:
        if enabled:gc.enable()
