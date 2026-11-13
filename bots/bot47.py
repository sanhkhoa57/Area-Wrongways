"""apex_v4 — WW-Apex v4 "Apex Predator" Tournament AI.

Thế hệ tiến hóa đỉnh cao tổng hợp tinh hoa từ:
  • Model_003  — Inlined Bitboard Wavefront BFS, DAG Invariance, Soft Excess Wall
  • Claude     — Dual-Extremal DAG, Quadratic Wall Valuation, Tunnel/Corridor Detection,
                  Opponent Modeling, Prophylactic Anti-Trap, RaceSolver, Opening Book
  • Model_002  — Horizon-Stall Pruning, Symmetric Tactical Tie-Breaker

Đột phá kiến trúc v4 so với v3:
  1. Hybrid Evaluation Function:
     - Quadratic Wall Valuation + Usable Wall Differential (porting từ Claude).
     - Tunnel Detection phát hiện đường hầm/cổ chai → phạt nặng khi bị nhốt.
     - Corridor Blocking phát hiện khi đối thủ chặn lối đi hẹp.
  2. Exact RaceSolver:
     - Alpha-beta minimax thuần túy cho endgame 0-tường, chính xác tuyệt đối.
  3. Enhanced Search:
     - Killer Move Heuristic (killer1/killer2 per ply) cho move ordering.
     - History Heuristic cho wall move scoring.
     - In-tree Cycle Detection ngăn vòng lặp vô hạn trong search tree.
     - Aspiration Windows (±85) từ depth ≥ 4 + Re-search fallback.
     - Progressive Anti-Oscillation: phạt lũy thừa khi lặp ô.
     - Quiescence +3 plies với Threat Extension khi đối thủ cách đích ≤ 2.
     - Deeper Iterative Deepening (MAX_DEPTH = 18).
  4. Prophylactic Anti-Trap Defense:
     - Quét tường dọc DAG tìm tường đối thủ có thể đặt gây +2 bước.
  5. Opponent Modeling (OppModel):
     - Phân loại đối thủ online (RUNNER/SPAMMER/IDLE/DEFAULT).
  6. Opening Book & Endgame:
     - 10 nước mở đầu tiêu chuẩn (P0/P1, mirror) → tiết kiệm 0.5s/nước mở.
     - Endgame solver chính xác khi cả hai hết tường.
  7. Structural Wall Expansion (Category E):
     - Full wall scan ở root: tường sát biên + tiếp giáp tường đã đặt + gần DAG.

Chỉ sử dụng thư viện chuẩn Python (Python 3.9+).
"""

import gc
from collections import deque, OrderedDict
from time import perf_counter

gc.disable()

# ═══════════════════════════════════════════════════════════════════════════════
# HẰNG SỐ & THAM SỐ
# ═══════════════════════════════════════════════════════════════════════════════
SIZE = 9
CELLS = SIZE * SIZE
ALL_WALLS = (1 << 128) - 1
THINK_SECONDS = 0.75      # soft budget (v4: 0.83 too tight to 1s; v5: 0.75)
MIN_SECONDS = 0.30        # minimum budget if system lags
MAX_DEPTH = 18
MATE = 100_000
INF = 1_000_000
UNREACHABLE = 999

# --- Trọng số hàm đánh giá (Hybrid: Model_003 expected distance + Claude enhancements) ---
STEP = 100
WALL_STEP = 1.25
WALL_CAP_EXTRA = 1
WALL_EXCESS = 0.75
TEMPO = 45
WALL_BONUS = 4
DIST_PENALTY = 0.4
ROUTE_BONUS = 1.5
CENTER_PENALTY = 0.04
TUNNEL_PEN = 8          # Phạt tunnel detection an toàn
CORRIDOR_PEN = 65       # Phạt corridor blocking (Claude)
ROW_PROGRESS = 4        # Thưởng tiến hàng (Claude)
RACE_WIN_BONUS = 20000  # Thưởng race chắc thắng (Claude)

# --- Soft Excess (giữ lại cho wall_step computation) ---
WALL_STEP = 1.25
WALL_CAP_EXTRA = 1
WALL_EXCESS = 0.75

# --- Số tường ứng viên theo độ sâu ---
WALL_LIMITS = (10, 5, 3)   # Tối ưu thông lượng & độ sâu (tương đương Model_003)
QUIESCE_PLIES = 3           # Sâu hơn v3 (2)

BOARD_CACHE_MAX = 35_000
TT_MAX = 100_000

# Bảng ánh xạ 9-bit → 8-bit tường
_ROW9_TO_W8 = tuple(((m | (m >> 1)) & 255) for m in range(512))

# Distance-to-goal lookup tables
_DIST_TO_GOAL = (
    tuple(cell // SIZE for cell in range(CELLS)),          # P0 wants row 0
    tuple(SIZE - 1 - cell // SIZE for cell in range(CELLS)),  # P1 wants row 8
)
_CENTER_BONUS = tuple(4 - abs(cell % SIZE - 4) for cell in range(CELLS))

# ═══════════════════════════════════════════════════════════════════════════════
# OPPONENT MODELING (porting từ Claude)
# ═══════════════════════════════════════════════════════════════════════════════
class _OppModel:
    __slots__ = ("style", "walls_used", "moves_used", "passes", "walls_rem", "recent")

    def __init__(self):
        self.reset()

    def reset(self):
        self.style = "DEFAULT"
        self.walls_used = 0
        self.moves_used = 0
        self.passes = 0
        self.walls_rem = 10
        self.recent = []

    def _classify(self):
        if self.walls_rem <= 0:
            if self.passes >= 2 and self.moves_used == 0:
                self.style = "IDLE"
            elif self.moves_used >= 2:
                self.style = "RUNNER"
            else:
                self.style = "DEFAULT"
            return
        if self.passes >= 2 and self.passes >= self.moves_used:
            self.style = "IDLE"
        elif (self.walls_used >= 3 and self.walls_used >= self.moves_used) or (
            len(self.recent) >= 3 and self.recent[-3:] == ["w", "w", "w"]
        ):
            self.style = "SPAMMER"
        elif self.moves_used >= 5 and self.walls_used == 0:
            self.style = "RUNNER"
        else:
            self.style = "DEFAULT"

    def update(self, prev_obs, curr_obs):
        if prev_obs is None or curr_obs is None:
            return
        pw = prev_obs.get("opponent", {}).get("walls_remaining", 10)
        cw = curr_obs.get("opponent", {}).get("walls_remaining", 10)
        pp = prev_obs.get("opponent", {}).get("position")
        cp = curr_obs.get("opponent", {}).get("position")
        self.walls_rem = cw
        if cw < pw:
            self.walls_used += 1
            self.recent.append("w")
        elif pp is not None and cp is not None and list(pp) != list(cp):
            self.moves_used += 1
            self.recent.append("m")
        else:
            self.passes += 1
            self.recent.append("p")
        if len(self.recent) > 16:
            self.recent.pop(0)
        self._classify()


_OPP_MODEL = _OppModel()
_PREV_OBS = None

# ═══════════════════════════════════════════════════════════════════════════════
# ĐỒ THỊ TĨNH: graph, wall_edges, conflicts, near, adj_walls, wall_tactical
# ═══════════════════════════════════════════════════════════════════════════════
def _wall_index(orientation, row, col):
    return (64 if orientation == "V" else 0) + row * 8 + col


def _wall_action(index):
    offset = index % 64
    return {
        "type": "wall",
        "orientation": "V" if index >= 64 else "H",
        "row": offset // 8,
        "col": offset % 8,
    }


def _make_constants():
    graph, wall_edges, conflicts, near = [], [], [], [0] * CELLS
    edge_walls = {}
    for row in range(SIZE):
        for col in range(SIZE):
            graph.append(
                tuple(
                    nr * SIZE + nc
                    for nr, nc in ((row - 1, col), (row, col - 1), (row, col + 1), (row + 1, col))
                    if 0 <= nr < SIZE and 0 <= nc < SIZE
                )
            )

    endpoints = []
    for index in range(128):
        row, col = divmod(index % 64, 8)
        first = row * SIZE + col
        if index < 64:
            edges = ((first, first + SIZE), (first + 1, first + SIZE + 1))
            bad = (1 << (index + 64)) | (1 << index)
            if col > 0:
                bad |= 1 << (index - 1)
            if col < 7:
                bad |= 1 << (index + 1)
            pts = {(row, col - 1), (row, col), (row, col + 1)}
        else:
            edges = ((first, first + 1), (first + SIZE, first + SIZE + 1))
            bad = (1 << (index - 64)) | (1 << index)
            if row > 0:
                bad |= 1 << (index - 8)
            if row < 7:
                bad |= 1 << (index + 8)
            pts = {(row - 1, col), (row, col), (row + 1, col)}

        endpoints.append(pts)
        wall_edges.append(edges)
        conflicts.append(bad)
        for a, b in edges:
            edge_walls[a, b] = edge_walls.get((a, b), 0) | (1 << index)
            edge_walls[b, a] = edge_walls.get((b, a), 0) | (1 << index)
        for cell in range(CELLS):
            r, c = divmod(cell, SIZE)
            dr = min(abs(r - row), abs(r - row - 1))
            dc = min(abs(c - col), abs(c - col - 1))
            if dr + dc <= 1:
                near[cell] |= 1 << index

    adj_walls = [0] * 128
    for i in range(128):
        mask = 0
        pi = endpoints[i]
        for j in range(128):
            if i != j and not (conflicts[i] & (1 << j)) and (pi & endpoints[j]):
                mask |= 1 << j
        adj_walls[i] = mask

    # Cross-wall mapping (H↔V at same anchor)
    cross_wall = [0] * 128
    for i in range(64):
        cross_wall[i] = i + 64
        cross_wall[i + 64] = i

    # Overlap walls (adjacent non-conflicting walls sharing endpoints)
    overlap_walls = [[] for _ in range(128)]
    for i in range(128):
        for j in range(128):
            if i != j and not (conflicts[i] & (1 << j)) and (endpoints[i] & endpoints[j]):
                overlap_walls[i].append(j)

    # Touch mask (structural adjacency for Category E wall expansion)
    touch_mask = [0] * 128
    for w in range(64):
        wr, wc = divmod(w, 8)
        if wc >= 2:
            touch_mask[w] |= 1 << (wr * 8 + (wc - 2))
        if wc <= 5:
            touch_mask[w] |= 1 << (wr * 8 + (wc + 2))
        for vr in (wr - 1, wr, wr + 1):
            for vc in (wc - 1, wc, wc + 1):
                if 0 <= vr < 8 and 0 <= vc < 8:
                    if (vr, vc) != (wr, wc):
                        touch_mask[w] |= 1 << (64 + vr * 8 + vc)
    for w in range(64, 128):
        wr, wc = divmod(w - 64, 8)
        if wr >= 2:
            touch_mask[w] |= 1 << (64 + (wr - 2) * 8 + wc)
        if wr <= 5:
            touch_mask[w] |= 1 << (64 + (wr + 2) * 8 + wc)
        for hr in (wr - 1, wr, wr + 1):
            for hc in (wc - 1, wc, wc + 1):
                if 0 <= hr < 8 and 0 <= hc < 8:
                    if (hr, hc) != (wr, wc):
                        touch_mask[w] |= 1 << (hr * 8 + hc)

    # Border mask for structural wall scan
    border_mask = 0
    for w in range(64):
        _, wc = divmod(w, 8)
        if wc == 0 or wc == 7:
            border_mask |= 1 << w
    for w in range(64):
        wr, _ = divmod(w, 8)
        if wr == 0 or wr == 7:
            border_mask |= 1 << (64 + w)

    wall_tactical = []
    for cell in range(CELLS):
        er, ec = divmod(cell, SIZE)
        row_scores = []
        for index in range(128):
            wr, wc = divmod(index % 64, 8)
            d_enemy = abs(2 * wr + 1 - 2 * er) + abs(2 * wc + 1 - 2 * ec)
            center_bias = abs(2 * wr - 7) + 0.15 * abs(2 * wc - 7)
            edge_pen = 8.0 if (index < 64 and wr in (0, 7) and abs(er - wr) >= 3) else 0.0
            row_scores.append(-0.15 * d_enemy - 0.08 * center_bias - edge_pen)
        wall_tactical.append(tuple(row_scores))

    return (
        tuple(graph),
        tuple(wall_edges),
        tuple(conflicts),
        tuple(near),
        edge_walls,
        tuple(adj_walls),
        tuple(wall_tactical),
        tuple(cross_wall),
        tuple(tuple(x) for x in overlap_walls),
        tuple(touch_mask),
        border_mask,
    )


(
    _EMPTY_GRAPH,
    _WALL_EDGES,
    _CONFLICTS,
    _NEAR,
    _EDGE_WALLS,
    _ADJ_WALLS,
    _WALL_TACTICAL,
    _CROSS_WALL,
    _OVERLAP_WALLS,
    _TOUCH_MASK,
    _BORDER_MASK,
) = _make_constants()

_H_EDGE = tuple(
    sum(1 << (r * 8 + x) for x in (c - 1, c) if 0 <= x < 8) if r < 8 else 0
    for r in range(SIZE) for c in range(SIZE)
)
_V_EDGE = tuple(
    sum(1 << (64 + x * 8 + c) for x in (r - 1, r) if 0 <= x < 8) if c < 8 else 0
    for r in range(SIZE) for c in range(SIZE)
)


def _moves(own, enemy, graph):
    result = []
    own_row, own_col = divmod(own, SIZE)
    for neighbor in graph[own]:
        if neighbor != enemy:
            result.append(neighbor)
            continue
        row, col = divmod(enemy, SIZE)
        dr, dc = row - own_row, col - own_col
        nr, nc = row + dr, col + dc
        beyond = nr * SIZE + nc
        if 0 <= nr < SIZE and 0 <= nc < SIZE and beyond in graph[enemy]:
            result.append(beyond)
        else:
            for side in graph[enemy]:
                sr, sc = divmod(side, SIZE)
                if (sr - row) * dr + (sc - col) * dc == 0:
                    result.append(side)
    return tuple(sorted(set(result)))


class _Deadline(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# BITBOARD INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════
_BITS = tuple(1 << cell for cell in range(CELLS))
_GOAL_BITS = tuple(511 << (row * SIZE) for row in range(SIZE))
_BIT_COUNT = getattr(int, "bit_count", lambda value: bin(value).count("1"))
_OPEN_INITIAL = (
    ((1 << CELLS) - 1) ^ 511,
    (1 << (CELLS - SIZE)) - 1,
    sum(1 << cell for cell in range(CELLS) if cell % SIZE),
    sum(1 << cell for cell in range(CELLS) if cell % SIZE < SIZE - 1),
)
_CLOSE = []
for _index in range(128):
    (_a, _b), (_c, _d) = _WALL_EDGES[_index]
    _CLOSE.append(
        ((_BITS[_b] | _BITS[_d], _BITS[_a] | _BITS[_c], 0, 0)
         if _index < 64
         else (0, 0, _BITS[_b] | _BITS[_d], _BITS[_a] | _BITS[_c]))
    )
_CLOSE = tuple(_CLOSE)
_BOARD_CACHE = OrderedDict()
_CACHE_MASK = 0
_CACHE_GOALS = None
_CACHE_TURN = -1
_TRANSPOSITIONS = {}


def _cells(bits):
    result = []
    while bits:
        bit = bits & -bits
        bits ^= bit
        result.append(bit.bit_length() - 1)
    return tuple(result)


# ═══════════════════════════════════════════════════════════════════════════════
# RACE SOLVER — Exact 0-wall Pawn Race (porting từ Claude)
# ═══════════════════════════════════════════════════════════════════════════════
_RACE_CACHE = {}


def _race_all_dists(opens, goal_bits):
    """Compute BFS distance from every cell to goal_bits."""
    up, down, left, right = opens
    dist = [UNREACHABLE] * CELLS
    wave = goal_bits
    vis = wave
    d = 0
    while wave:
        w = wave
        while w:
            bit = w & -w
            w ^= bit
            dist[bit.bit_length() - 1] = d
        d += 1
        wave = (
            ((wave & up) >> 9)
            | ((wave & down) << 9)
            | ((wave & left) >> 1)
            | ((wave & right) << 1)
        ) & ~vis
        vis |= wave
    return dist


def _race_solve(p0, p1, turn, opens, goal_bits_0, goal_bits_1):
    """Exact minimax solver for 0-wall pawn race."""
    cache_key = (p0, p1, turn, opens)
    cached = _RACE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    r0 = p0 // SIZE
    r1 = p1 // SIZE
    c0 = p0 % SIZE
    c1 = p1 % SIZE
    mdist = abs(r0 - r1) + abs(c0 - c1)

    up, down, left, right = opens

    # Fast BFS distances
    d0 = _bfs_dist(p0, goal_bits_0, opens)
    d1 = _bfs_dist(p1, goal_bits_1, opens)

    # Fast exact resolution when pawns far apart
    if mdist >= 3 or abs(d0 - d1) >= 2:
        if turn == 0:
            res = (25000 + (d1 - d0) * 150 - d0 * 20) if d0 <= d1 else (-25000 + (d1 - d0) * 150 + d1 * 20)
        else:
            res = (25000 + (d0 - d1) * 150 - d1 * 20) if d1 <= d0 else (-25000 + (d0 - d1) * 150 + d0 * 20)
        _RACE_CACHE[cache_key] = res
        return res

    # Full minimax for close encounters
    d0_map = _race_all_dists(opens, goal_bits_0)
    d1_map = _race_all_dists(opens, goal_bits_1)
    d_maps = (d0_map, d1_map)
    tt = {}
    path = set()
    limit_depth = min(16, d0 + d1 + 2)

    def get_pawn_moves(s, me, op):
        out = []
        if (up & _BITS[me]):
            x = me - SIZE
            if x >= 0:
                if x != op:
                    out.append(x)
                elif x >= SIZE and (up & _BITS[op]):
                    out.append(op - SIZE)
                else:
                    if op % SIZE > 0 and (right & _BITS[op - 1]):
                        out.append(op - 1)
                    if op % SIZE < SIZE - 1 and (right & _BITS[op]):
                        out.append(op + 1)
        if (down & _BITS[me]):
            x = me + SIZE
            if x < CELLS:
                if x != op:
                    out.append(x)
                elif x + SIZE <= CELLS and (down & _BITS[op]):
                    out.append(op + SIZE)
                else:
                    if op % SIZE > 0 and (right & _BITS[op - 1]):
                        out.append(op - 1)
                    if op % SIZE < SIZE - 1 and (right & _BITS[op]):
                        out.append(op + 1)
        if me % SIZE > 0 and (left & _BITS[me]):
            x = me - 1
            if x != op:
                out.append(x)
            elif op % SIZE > 0 and (left & _BITS[op]):
                out.append(op - 1)
            else:
                if op >= SIZE and (up & _BITS[op]):
                    out.append(op - SIZE)
                if op + SIZE < CELLS and (down & _BITS[op]):
                    out.append(op + SIZE)
        if me % SIZE < SIZE - 1 and (right & _BITS[me]):
            x = me + 1
            if x != op:
                out.append(x)
            elif op % SIZE < SIZE - 1 and (right & _BITS[op]):
                out.append(op + 1)
            else:
                if op >= SIZE and (up & _BITS[op]):
                    out.append(op - SIZE)
                if op + SIZE < CELLS and (down & _BITS[op]):
                    out.append(op + SIZE)
        return out

    def negamax(pos0, pos1, cur_turn, depth, alpha, beta):
        s = cur_turn
        me = pos0 if s == 0 else pos1
        op = pos1 if s == 0 else pos0
        g_row = 0 if s == 0 else SIZE - 1

        moves = get_pawn_moves(s, me, op)
        for m in moves:
            if m // SIZE == g_row:
                return 25000 - depth * 100

        dm_s = d_maps[s]
        dm_o = d_maps[1 - s]
        cur_d_me = dm_s[me]
        cur_d_op = dm_o[op]

        if depth >= limit_depth or not moves:
            if cur_d_me <= cur_d_op:
                return 25000 + (cur_d_op - cur_d_me) * 150 - (cur_d_me + depth) * 20
            return -25000 + (cur_d_op - cur_d_me) * 150 + (cur_d_op + depth) * 20

        state_key = (pos0, pos1, cur_turn)
        if state_key in path:
            return 0
        if state_key in tt:
            return tt[state_key]

        path.add(state_key)
        scored = [(dm_s[m], m) for m in moves]
        scored.sort()

        best = -30000
        for _, m in scored:
            np0 = m if s == 0 else pos0
            np1 = m if s == 1 else pos1
            val = -negamax(np0, np1, 1 - s, depth + 1, -beta, -alpha)
            if val > best:
                best = val
            alpha = max(alpha, val)
            if alpha >= beta:
                break

        path.remove(state_key)
        tt[state_key] = best
        return best

    res = negamax(p0, p1, turn, 0, -30000, 30000)
    _RACE_CACHE[cache_key] = res
    return res


def _bfs_dist(cell, goal_bits, opens):
    """Fast BFS distance from cell to goal_bits."""
    bit = _BITS[cell]
    if bit & goal_bits:
        return 0
    up, down, left, right = opens
    wave = bit
    vis = wave
    d = 0
    while wave:
        d += 1
        wave = (
            ((wave & up) >> 9)
            | ((wave & down) << 9)
            | ((wave & left) >> 1)
            | ((wave & right) << 1)
        ) & ~vis
        if wave & goal_bits:
            return d
        vis |= wave
    return UNREACHABLE


# ═══════════════════════════════════════════════════════════════════════════════
# _Board CLASS — Bitboard BFS + DAG Walls
# ═══════════════════════════════════════════════════════════════════════════════
class _Board:
    __slots__ = (
        "mask", "_graph", "_maps", "available", "opens",
        "goals", "cache", "dag_cache", "gates_cache", "layers", "seen",
    )

    def __init__(self, mask, graph, maps, available, opens=None, goals=(0, 8)):
        self.mask, self._graph, self._maps = mask, graph, maps
        self.available, self.goals = available, goals
        self.cache = ({}, {})
        self.dag_cache = ({}, {})
        self.gates_cache = ({}, {})
        g0 = _GOAL_BITS[goals[0]]
        g1 = _GOAL_BITS[goals[1]]
        self.layers = ([g0], [g1])
        self.seen = [g0, g1]
        if opens is None:
            u, d, l, r = _OPEN_INITIAL
            pending = mask
            while pending:
                bit = pending & -pending
                pending ^= bit
                cu, cd, cl, cr = _CLOSE[bit.bit_length() - 1]
                u &= ~cu
                d &= ~cd
                l &= ~cl
                r &= ~cr
            opens = (u, d, l, r)
        self.opens = opens

    def neighbors(self, cell):
        bit = _BITS[cell]
        up, down, left, right = self.opens
        return ((bit & up) >> 9) | ((bit & down) << 9) | ((bit & left) >> 1) | ((bit & right) << 1)

    @property
    def graph(self):
        if self._graph is None:
            self._graph = tuple(_cells(self.neighbors(cell)) for cell in range(CELLS))
        return self._graph

    @property
    def maps(self):
        if self._maps is None:
            maps = []
            for player in (0, 1):
                while self.layers[player][-1]:
                    self.extend(player)
                values = [UNREACHABLE] * CELLS
                for distance, bits in enumerate(self.layers[player]):
                    while bits:
                        bit = bits & -bits
                        bits ^= bit
                        values[bit.bit_length() - 1] = distance
                maps.append(values)
            self._maps = tuple(maps)
        return self._maps

    def extend(self, player):
        up, down, left, right = self.opens
        frontier = self.layers[player][-1]
        following = (
            ((frontier & up) >> 9)
            | ((frontier & down) << 9)
            | ((frontier & left) >> 1)
            | ((frontier & right) << 1)
        ) & ~self.seen[player]
        self.layers[player].append(following)
        self.seen[player] |= following

    def metric(self, cell, player):
        """Return (distance, routes) using inlined bitboard wavefront BFS."""
        known = self.cache[player].get(cell)
        if known is not None:
            return known
        target = _BITS[cell]
        layers = self.layers[player]
        seen = self.seen[player]
        if not (seen & target):
            up, down, left, right = self.opens
            frontier = layers[-1]
            append = layers.append
            while frontier:
                frontier = (
                    ((frontier & up) >> 9)
                    | ((frontier & down) << 9)
                    | ((frontier & left) >> 1)
                    | ((frontier & right) << 1)
                ) & ~seen
                append(frontier)
                seen |= frontier
                if frontier & target:
                    self.seen[player] = seen
                    distance = len(layers) - 1
                    nb = ((target & up) >> 9) | ((target & down) << 9) | ((target & left) >> 1) | ((target & right) << 1)
                    routes = _BIT_COUNT(nb & layers[distance - 1])
                    value = (distance, routes)
                    self.cache[player][cell] = value
                    return value
            self.seen[player] = seen
            value = (UNREACHABLE, 0)
            self.cache[player][cell] = value
            return value
        for distance, bits in enumerate(layers):
            if bits & target:
                break
        routes = _BIT_COUNT(self.neighbors(cell) & layers[distance - 1]) if distance else 0
        value = (distance, routes)
        self.cache[player][cell] = value
        return value

    def dag_walls(self, cell, player):
        """Return 128-bit mask of walls intersecting any shortest path."""
        known = self.dag_cache[player].get(cell)
        if known is not None:
            return known
        distance, _ = self.metric(cell, player)
        if distance == 0 or distance >= UNREACHABLE:
            self.dag_cache[player][cell] = 0
            return 0
        layers = self.layers[player]
        while len(layers) <= distance and layers[-1]:
            self.extend(player)
        up, down, left, right = self.opens
        ve = he = 0
        curr = _BITS[cell]
        for step in range(distance - 1, -1, -1):
            prev = layers[step]
            uu = ((curr & up) >> 9) & prev
            ud = ((curr & down) << 9) & prev
            ul = ((curr & left) >> 1) & prev
            ur = ((curr & right) << 1) & prev
            ve |= uu | (ud >> 9)
            he |= ul | (ur >> 1)
            curr = uu | ud | ul | ur
        he |= he >> 9
        w = (
            (_ROW9_TO_W8[ve & 511])
            | (_ROW9_TO_W8[(ve >> 9) & 511] << 8)
            | (_ROW9_TO_W8[(ve >> 18) & 511] << 16)
            | (_ROW9_TO_W8[(ve >> 27) & 511] << 24)
            | (_ROW9_TO_W8[(ve >> 36) & 511] << 32)
            | (_ROW9_TO_W8[(ve >> 45) & 511] << 40)
            | (_ROW9_TO_W8[(ve >> 54) & 511] << 48)
            | (_ROW9_TO_W8[(ve >> 63) & 511] << 56)
            | ((he & 255) << 64)
            | (((he >> 9) & 255) << 72)
            | (((he >> 18) & 255) << 80)
            | (((he >> 27) & 255) << 88)
            | (((he >> 36) & 255) << 96)
            | (((he >> 45) & 255) << 104)
            | (((he >> 54) & 255) << 112)
            | (((he >> 63) & 255) << 120)
        )
        self.dag_cache[player][cell] = w
        return w

    def gates(self, cell, player):
        """Return 128-bit mask of walls intersecting EVERY shortest path from cell to goal."""
        cache = self.gates_cache[player]
        known = cache.get(cell)
        if known is not None:
            return known
        distance, _ = self.metric(cell, player)
        if distance == 0 or distance >= UNREACHABLE:
            cache[cell] = 0
            return 0
        layers = self.layers[player]
        while len(layers) <= distance and layers[-1]:
            self.extend(player)
        successors = self.neighbors(cell) & layers[distance - 1]
        common = ALL_WALLS
        while successors:
            bit = successors & -successors
            successors ^= bit
            q = bit.bit_length() - 1
            lower = cell if cell < q else q
            cutting = _H_EDGE[lower] if abs(cell - q) == SIZE else _V_EDGE[lower]
            common &= self.gates(q, player) | cutting
        cache[cell] = common
        return common

    def tunnel_score(self, cell, player):
        """Detect if cell is in a narrow tunnel (few routes through constrained corridor)."""
        nb = self.neighbors(cell)
        degree = _BIT_COUNT(nb)
        if degree > 2:
            return 0  # Degree >= 3 is open space, skip expensive BFS metric
        if degree <= 1:
            return 4  # Dead-end-like
        d, routes = self.metric(cell, player)
        if d <= 1 or d >= UNREACHABLE:
            return 0
        if routes <= 1:
            return 2  # Narrow corridor
        return 0


def _fast_moves(own, enemy, board):
    destinations = board.neighbors(own)
    enemy_bit = _BITS[enemy]
    if not (destinations & enemy_bit):
        return _cells(destinations)
    destinations ^= enemy_bit
    beyond = enemy + enemy - own
    same_line = abs(enemy - own) == SIZE or (0 <= beyond < CELLS and beyond // SIZE == enemy // SIZE)
    enemy_neighbors = board.neighbors(enemy)
    if 0 <= beyond < CELLS and same_line and (enemy_neighbors & _BITS[beyond]):
        destinations |= _BITS[beyond]
    else:
        destinations |= enemy_neighbors & ~_BITS[own]
    return _cells(destinations)


def _pawn_metric(own, enemy, board, player):
    distance, routes = board.metric(own, player)
    if distance and (board.neighbors(own) & _BITS[enemy]):
        distance = 1 + min(
            (board.metric(cell, player)[0] for cell in _fast_moves(own, enemy, board)),
            default=UNREACHABLE,
        )
    return distance, routes


# ═══════════════════════════════════════════════════════════════════════════════
# ENDGAME SOLVER — Retrograde Analysis (from Model_003)
# ═══════════════════════════════════════════════════════════════════════════════
class _Endgame:
    __slots__ = ("key", "result", "distance", "graph", "goals", "maps")

    def __init__(self, board, goals, deadline):
        self.key = (board.mask, goals)
        self.graph, self.goals = board.graph, goals
        self.maps = board.maps
        count = 2 * CELLS * CELLS
        result = [0] * count
        distance = [0] * count
        remaining, longest = [0] * count, [0] * count
        previous = [[] for _ in range(count)]
        pending = deque()
        span = CELLS * CELLS

        for p0 in range(CELLS):
            if perf_counter() >= deadline:
                raise _Deadline
            if board.maps[0][p0] == UNREACHABLE:
                continue
            for p1 in range(CELLS):
                if p0 == p1 or board.maps[1][p1] == UNREACHABLE:
                    continue
                winner = (
                    0 if p0 // SIZE == goals[0] else 1 if p1 // SIZE == goals[1] else None
                )
                pair = p0 * CELLS + p1
                for turn in (0, 1):
                    state = turn * span + pair
                    if winner is not None:
                        result[state] = 1 if winner == turn else -1
                        pending.append(state)
                        continue
                    own, enemy = (p0, p1) if turn == 0 else (p1, p0)
                    destinations = _moves(own, enemy, board.graph) + (own,)
                    remaining[state] = len(destinations)
                    for cell in destinations:
                        next_pair = cell * CELLS + p1 if turn == 0 else p0 * CELLS + cell
                        previous[(1 - turn) * span + next_pair].append(state)

        processed = 0
        while pending:
            state = pending.popleft()
            processed += 1
            if processed % 256 == 0 and perf_counter() >= deadline:
                raise _Deadline
            for parent in previous[state]:
                if result[parent]:
                    continue
                if result[state] == -1:
                    result[parent] = 1
                    distance[parent] = distance[state] + 1
                    pending.append(parent)
                else:
                    remaining[parent] -= 1
                    longest[parent] = max(longest[parent], distance[state])
                    if remaining[parent] == 0:
                        result[parent] = -1
                        distance[parent] = longest[parent] + 1
                        pending.append(parent)
        self.result, self.distance = result, distance

    def probe(self, p0, p1, turn):
        state = turn * CELLS * CELLS + p0 * CELLS + p1
        return self.result[state], self.distance[state]

    def action(self, p0, p1, turn, remaining_turns, visits):
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = own, (-2, -INF, -INF, -INF, -INF, False)
        for destination in _moves(own, enemy, self.graph) + (own,):
            np0, np1 = (destination, p1) if turn == 0 else (p0, destination)
            outcome, distance = self.probe(np0, np1, 1 - turn)
            outcome, distance = -outcome, distance + 1
            if distance > remaining_turns:
                outcome = 0
            pace = -distance if outcome >= 0 else distance
            progress = -self.maps[turn][destination]
            pressure = 0
            if outcome == 0:
                replies = _moves(enemy, destination, self.graph) + (enemy,)
                bad = 0
                for reply in replies:
                    ap0, ap1 = (destination, reply) if turn == 0 else (reply, destination)
                    reply_result, _ = self.probe(ap0, ap1, turn)
                    bad += reply_result == 1
                pressure = bad / len(replies)
            key = (outcome, pace, pressure, -visits.get(destination, 0), progress, destination != own)
            if key > best_key:
                best, best_key = destination, key
        return best


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
class _Search:
    def __init__(self, observation, deadline):
        global _BOARD_CACHE, _CACHE_MASK, _CACHE_GOALS, _CACHE_TURN, _TRANSPOSITIONS, _RACE_CACHE
        self.deadline = deadline
        self.root_turn = observation["player_id"]
        self.positions, self.walls, goals = [0, 0], [0, 0], [0, 0]
        for player, info in ((self.root_turn, observation["you"]),
                             (1 - self.root_turn, observation["opponent"])):
            self.positions[player] = info["position"][0] * SIZE + info["position"][1]
            self.walls[player] = info["walls_remaining"]
            goals[player] = info["goal_row"]
        self.goals = tuple(goals)
        self.remaining = observation["max_turns"] - observation["turn"]
        self.opp_style = _OPP_MODEL.style
        mask, available = 0, ALL_WALLS
        for name, orientation in (("horizontal", "H"), ("vertical", "V")):
            for row, col in observation["walls"][name]:
                index = _wall_index(orientation, row, col)
                mask |= 1 << index
                available &= ~_CONFLICTS[index]
        if (
            _CACHE_GOALS != self.goals
            or observation["turn"] <= _CACHE_TURN
            or (mask & _CACHE_MASK) != _CACHE_MASK
        ):
            _BOARD_CACHE = OrderedDict()
            _TRANSPOSITIONS = {}
            _RACE_CACHE = {}
        elif mask != _CACHE_MASK:
            _BOARD_CACHE = OrderedDict(
                (key, value) for key, value in _BOARD_CACHE.items() if (key & mask) == mask
            )
            _TRANSPOSITIONS = {
                key: value for key, value in _TRANSPOSITIONS.items() if (key[0] & mask) == mask
            }
        if len(_TRANSPOSITIONS) > TT_MAX:
            _TRANSPOSITIONS = {}
        if len(_RACE_CACHE) > 50_000:
            _RACE_CACHE = {}
        _CACHE_MASK, _CACHE_GOALS, _CACHE_TURN = mask, self.goals, observation["turn"]
        self.boards = _BOARD_CACHE
        root = self.boards.get(mask)
        if root is None:
            root = _Board(mask, None, None, available, goals=self.goals)
            if len(self.boards) >= BOARD_CACHE_MAX:
                self.boards.popitem(last=False)
            self.boards[mask] = root
        else:
            self.boards.move_to_end(mask)
        self.root = root
        self.table = _TRANSPOSITIONS
        self.nodes, self.completed_depth = 0, 0
        self.repetitions = {}
        # Killer move heuristic (ported from Claude)
        self.killer1 = [None] * 64
        self.killer2 = [None] * 64
        # History heuristic (ported from Claude)
        self.history = [0] * 210
        # Root move score ordering from previous iteration (ported from Claude)
        self.root_move_scores = {}
        # In-tree cycle detection (ported from Claude)
        self.path_keys = set()
        # Root eval for draw scoring
        self.root_eval = 0
        # 2-wall pressure cache & LMR-protected plan actions (ported from lambda)
        self.pressure_cache = {}
        self.protected = set()

    def pressure(self, board, victim, attacker, player):
        """Largest legal one-wall NET delay using dominator gates (ported from lambda)."""
        key = (board.mask, victim, attacker, player)
        found = self.pressure_cache.get(key)
        if found is not None:
            return found
        old_v = board.metric(victim, player)[0]
        old_a = board.metric(attacker, 1 - player)[0]
        if old_v >= UNREACHABLE or old_a >= UNREACHABLE:
            self.pressure_cache[key] = (0, 0)
            return (0, 0)
        pending = board.gates(victim, player) & board.available
        worst, threats = 0, 0
        checked = 0
        while pending:
            bit = pending & -pending
            pending ^= bit
            checked += 1
            if checked & 7 == 0:
                self.poll()
            after = self.wall_board(board, bit.bit_length() - 1)
            dv = after.metric(victim, player)[0]
            if dv >= UNREACHABLE:
                continue
            da = after.metric(attacker, 1 - player)[0]
            if da >= UNREACHABLE:
                continue
            net = (dv - old_v) - (da - old_a)
            if net > worst:
                worst, threats = net, bit
            elif net == worst and net > 0:
                threats |= bit
        found = (worst, threats)
        self.pressure_cache[key] = found
        return found

    def poll(self):
        if perf_counter() >= self.deadline:
            raise _Deadline

    def wall_board(self, board, index):
        mask = board.mask | (1 << index)
        found = self.boards.get(mask)
        if found is not None:
            self.boards.move_to_end(mask)
            return found
        u, d, l, r = board.opens
        cu, cd, cl, cr = _CLOSE[index]
        opens = (u & ~cu, d & ~cd, l & ~cl, r & ~cr)
        result = _Board(mask, None, None, board.available & ~_CONFLICTS[index], opens, self.goals)
        if len(self.boards) >= BOARD_CACHE_MAX:
            self.boards.popitem(last=False)
        self.boards[mask] = result
        return result

    def draw_score(self, turn):
        """Context-dependent draw scoring (ported from Claude)."""
        if self.root_eval < -500:
            val = 50  # We're behind — draw is decent
        else:
            val = -150  # We're ahead or equal — draw is bad
        return val if turn == self.root_turn else -val

    def evaluate(self, board, p0, p1, w0, w1, turn, ply):
        """Hybrid evaluation: Model_003 distance engine + Claude wall/tunnel/corridor features."""
        if p0 // SIZE == self.goals[0]:
            return (MATE - ply) if turn == 0 else (-MATE + ply)
        if p1 // SIZE == self.goals[1]:
            return (MATE - ply) if turn == 1 else (-MATE + ply)
        if ply >= self.remaining:
            return self.draw_score(turn)
        own_p = p0 if turn == 0 else p1
        enemy_p = p1 if turn == 0 else p0
        own_d, own_r = _pawn_metric(own_p, enemy_p, board, turn)
        if own_d == 1:
            return MATE - ply - 1
        enemy_d, enemy_r = board.metric(enemy_p, 1 - turn)
        own_w, enemy_w = (w0, w1) if turn == 0 else (w1, w0)

        # === Race detection (Claude) ===
        mdist = abs(p0 // SIZE - p1 // SIZE) + abs(p0 % SIZE - p1 % SIZE)

        # === Exact RaceSolver for 0-wall endgames (Claude) ===
        if own_w == 0 and enemy_w == 0:
            goal_bits_0 = _GOAL_BITS[self.goals[0]]
            goal_bits_1 = _GOAL_BITS[self.goals[1]]
            return _race_solve(p0, p1, turn, board.opens, goal_bits_0, goal_bits_1)

        if mdist >= 2:
            if enemy_w == 0 and own_d <= enemy_d:
                return RACE_WIN_BONUS + 150 * (enemy_d - own_d) + 120 * min(own_w, 4)
            if own_w == 0 and enemy_w > 0 and enemy_d < own_d:
                return -RACE_WIN_BONUS + 150 * (enemy_d - own_d) - 120 * min(enemy_w, 4)

        # === Distance + Wall Valuation (Model_003 expected distance engine) ===
        cap_e = own_d + WALL_CAP_EXTRA
        eff_ew = enemy_w if enemy_w <= cap_e else cap_e + WALL_EXCESS * (enemy_w - cap_e)
        cap_o = enemy_d + WALL_CAP_EXTRA
        eff_ow = own_w if own_w <= cap_o else cap_o + WALL_EXCESS * (own_w - cap_o)
        own_expected = own_d + WALL_STEP * eff_ew
        enemy_expected = enemy_d + WALL_STEP * eff_ow

        score = (
            STEP * (enemy_expected - own_expected)
            + TEMPO
            + WALL_BONUS * (own_w - enemy_w)
            - DIST_PENALTY * own_d
            + ROUTE_BONUS * (own_r - enemy_r)
            - CENTER_PENALTY * abs(own_p % SIZE - 4)
        )

        # === Row progress tie-breaker (Claude) ===
        own_goal_dist = _DIST_TO_GOAL[turn][own_p]
        enemy_goal_dist = _DIST_TO_GOAL[1 - turn][enemy_p]
        score += ROW_PROGRESS * (enemy_goal_dist - own_goal_dist)

        # === Tunnel detection & Anti-maze ===
        if enemy_w > 0:
            tun = board.tunnel_score(own_p, turn)
            score -= TUNNEL_PEN * tun
            if tun == 4:
                score -= 45  # Tránh bước vào ngõ cụt 1 lối thoát khi đối thủ còn tường
            if enemy_w >= 2 and own_r == 1 and own_d > 2:
                score -= 15  # Tránh bị dồn vào mê cung 1 đường duy nhất
        if own_w > 0:
            tun_e = board.tunnel_score(enemy_p, 1 - turn)
            score += TUNNEL_PEN * tun_e
            if tun_e == 4:
                score += 45

        # === Corridor blocking (Claude) ===
        if mdist <= 2 and board.mask != 0:
            own_nb = board.neighbors(own_p)
            own_deg = _BIT_COUNT(own_nb)
            if own_deg == 1 and (own_nb & _BITS[enemy_p]):
                score -= CORRIDOR_PEN
            enemy_nb = board.neighbors(enemy_p)
            enemy_deg = _BIT_COUNT(enemy_nb)
            if enemy_deg == 1 and (enemy_nb & _BITS[own_p]):
                score += CORRIDOR_PEN

        # === Wall asymmetry bonus (Claude) ===
        if own_w == 0 and enemy_w > 0:
            score -= 90
        elif enemy_w == 0 and own_w > 0:
            score += 90

        # === Centrality in wall-heavy early game ===
        if own_w + enemy_w >= 12:
            score += 2 * (_CENTER_BONUS[own_p] - _CENTER_BONUS[enemy_p])

        return score

    def relevant_walls(self, board, p0, p1):
        return board.available & (
            _NEAR[p0] | _NEAR[p1] | board.dag_walls(p0, 0) | board.dag_walls(p1, 1)
        )

    def _pawn_vulnerability(self, board, cell, enemy, player, enemy_walls):
        """Check if opponent has a wall in _NEAR[cell] or cutting DAG that inflicts detour >= 3."""
        if enemy_walls <= 0:
            return 0
        d_curr = board.metric(cell, player)[0]
        if d_curr <= 1 or d_curr >= UNREACHABLE:
            return 0
        threat_mask = (_NEAR[cell] | board.dag_walls(cell, player)) & board.available
        if not threat_mask:
            return 0
        enemy_goal = self.goals[1 - player]
        max_detour = 0
        while threat_mask:
            lb = threat_mask & -threat_mask
            threat_mask ^= lb
            idx = lb.bit_length() - 1
            after = self.wall_board(board, idx)
            if after.metric(enemy, 1 - player)[0] >= UNREACHABLE:
                continue
            d_new = after.metric(cell, player)[0]
            if d_new >= UNREACHABLE:
                continue
            detour = d_new - d_curr
            if detour > max_detour:
                max_detour = detour
                if max_detour >= 6:
                    break
        return max_detour

    def _prophylactic_walls(self, board, p0, p1, turn, own_walls, enemy_walls):
        """Claude-inspired prophylactic anti-trap defense: find walls opponent could
        place to trap us, then preemptively block those with cross/overlap walls."""
        if enemy_walls <= 0:
            return {}
        own = p0 if turn == 0 else p1
        enemy = p1 if turn == 0 else p0
        player = turn
        dag_own = board.dag_walls(own, player)
        d_own = board.metric(own, player)[0]
        d_enemy = board.metric(enemy, 1 - player)[0]

        # Only check prophylaxis when we're vulnerable
        if d_own > d_enemy + 3:
            return {}

        proph = {}
        # Scan enemy's candidate walls that cut our DAG
        threat_mask = dag_own & board.available
        while threat_mask:
            bit = threat_mask & -threat_mask
            threat_mask ^= bit
            idx = bit.bit_length() - 1
            after = self.wall_board(board, idx)
            new_d = after.metric(own, player)[0]
            if new_d == UNREACHABLE or new_d - d_own < 2:
                continue
            # This wall is a threat — find blocking walls
            gain = new_d - d_own
            bval = min(240, gain * 50)
            cw = _CROSS_WALL[idx]
            if board.available & (1 << cw):
                af_cw = self.wall_board(board, cw)
                if af_cw.metric(own, player)[0] <= d_own:
                    proph[cw] = max(proph.get(cw, 0), bval)
            for ow in _OVERLAP_WALLS[idx]:
                if board.available & (1 << ow):
                    af_ow = self.wall_board(board, ow)
                    if af_ow.metric(own, player)[0] <= d_own:
                        proph[ow] = max(proph.get(ow, 0), (bval * 3) // 4)
        return proph

    def children(self, board, p0, p1, w0, w1, turn, ply, preferred=None):
        self.poll()
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        children, walls = [], []
        destinations = _fast_moves(own, enemy, board)
        for destination in destinations or (own,):
            np0, np1 = (destination, p1) if turn == 0 else (p0, destination)
            action = destination if destination != own else 209
            score = -self.evaluate(board, np0, np1, w0, w1, 1 - turn, ply + 1)
            children.append((score, action, board, np0, np1, w0, w1))
        # Deadlock-breaking PASS: only available if currently caught in oscillation loop
        if ply == 0 and destinations and any(self.repetitions.get(d, 0) >= 3 for d in destinations):
            pass_score = -self.evaluate(board, p0, p1, w0, w1, 1 - turn, ply + 1) - 25
            children.append((pass_score, 209, board, p0, p1, w0, w1))
        own_walls = w0 if turn == 0 else w1
        if own_walls and ply + 1 < self.remaining:
            m0 = board.metric(p0, 0)
            m1 = board.metric(p1, 1)
            old_d0, old_r0 = m0
            old_d1, old_r1 = m1
            old_own_d, old_enemy_d = (old_d0, old_d1) if turn == 0 else (old_d1, old_d0)
            old_own_r, old_enemy_r = (old_r0, old_r1) if turn == 0 else (old_r1, old_r0)
            dag0 = board.dag_walls(p0, 0)
            dag1 = board.dag_walls(p1, 1)
            dag_both = dag0 | dag1
            adj_pawns = bool(board.neighbors(p0) & _BITS[p1])
            nw0, nw1 = (w0 - 1, w1) if turn == 0 else (w0, w1 - 1)
            nw_own = own_walls - 1
            enemy_walls = w1 if turn == 0 else w0

            # Prophylactic defense bonus (Claude feature)
            proph_bonus = {}
            if ply == 0:
                try:
                    proph_bonus = self._prophylactic_walls(board, p0, p1, turn, own_walls, enemy_walls)
                except _Deadline:
                    pass

            # Category E structural wall expansion at root (Claude feature)
            cat_e_mask = 0
            if ply == 0:
                touching_existing = 0
                wm = board.mask
                while wm:
                    lb = wm & -wm
                    wm ^= lb
                    touching_existing |= _TOUCH_MASK[lb.bit_length() - 1]
                structural_mask = (_BORDER_MASK | touching_existing) & board.available
                dag_enemy = dag1 if turn == 0 else dag0
                near_dag_mask = dag_enemy  # opponent's DAG
                co = dag_enemy
                while co:
                    lb = co & -co
                    co ^= lb
                    idx = lb.bit_length() - 1
                    if idx < 128:
                        touching_existing |= _TOUCH_MASK[idx]
                cat_e_mask = structural_mask & touching_existing & board.available
                # Don't add too many — cap the expansion
                cat_e_count = _BIT_COUNT(cat_e_mask)
                if cat_e_count > 20:
                    cat_e_mask = cat_e_mask & (_NEAR[p0] | _NEAR[p1] | dag_both)

            own_goal_dist = _DIST_TO_GOAL[turn][own]
            enemy_goal_dist = _DIST_TO_GOAL[1 - turn][enemy]
            asym_bias = -90 if (enemy_walls == 0 and nw_own > 0) else (90 if (nw_own == 0 and enemy_walls > 0) else 0)
            cent_bias = (2 * (_CENTER_BONUS[enemy] - _CENTER_BONUS[own])) if (nw_own + enemy_walls >= 12) else 0

            const_bias = (
                TEMPO
                + WALL_BONUS * (enemy_walls - nw_own)
                - CENTER_PENALTY * abs(enemy % SIZE - 4)
                + ROW_PROGRESS * (own_goal_dist - enemy_goal_dist)
                + asym_bias
                + cent_bias
            )

            if not adj_pawns:
                if old_enemy_d == 1:
                    zero_gain_base = float(-(MATE - ply - 2))
                else:
                    c_o = old_enemy_d + WALL_CAP_EXTRA
                    ef_o = nw_own if nw_own <= c_o else c_o + WALL_EXCESS * (nw_own - c_o)
                    c_e = old_own_d + WALL_CAP_EXTRA
                    ef_e = enemy_walls if enemy_walls <= c_e else c_e + WALL_EXCESS * (enemy_walls - c_e)
                    zg_own_exp = old_enemy_d + WALL_STEP * ef_o
                    zg_enemy_exp = old_own_d + WALL_STEP * ef_e
                    zero_gain_base = -(
                        STEP * (zg_enemy_exp - zg_own_exp)
                        + const_bias
                        - DIST_PENALTY * old_enemy_d
                        + ROUTE_BONUS * (old_enemy_r - old_own_r)
                    )

            tactical_table = _WALL_TACTICAL[enemy]
            er = enemy // SIZE
            placed_mask = board.mask
            hist = self.history
            plans = []
            pending = (
                board.available
                if ply == 0
                else (board.available & (_NEAR[p0] | _NEAR[p1] | dag_both))
            )
            # Add prophylactic + Category E walls to scanning set
            for pw in proph_bonus:
                pending |= 1 << pw
            if ply == 0:
                pending |= cat_e_mask
            # Add killer moves to scanning set
            k1 = self.killer1[min(ply, 63)]
            k2 = self.killer2[min(ply, 63)]
            if k1 is not None and 81 <= k1 < 209:
                ki = k1 - 81
                if board.available & (1 << ki):
                    pending |= 1 << ki
            if k2 is not None and 81 <= k2 < 209:
                ki = k2 - 81
                if board.available & (1 << ki):
                    pending |= 1 << ki
            pending &= board.available

            checked = 0
            while pending:
                bit = pending & -pending
                pending ^= bit
                index = bit.bit_length() - 1
                wr = (index % 64) // 8
                pb = proph_bonus.get(index, 0)
                is_cat_e = bool(cat_e_mask & bit) if ply == 0 else False

                if not adj_pawns and not (dag_both & bit) and pb == 0 and not is_cat_e:
                    continue

                # Skip walls that only cut our own shortest path and do not cut the enemy's path
                dag_own_cur = dag0 if turn == 0 else dag1
                dag_enemy_cur = dag1 if turn == 0 else dag0
                if not adj_pawns and (dag_own_cur & bit) and not (dag_enemy_cur & bit) and pb == 0 and not is_cat_e:
                    continue

                checked += 1
                if checked % 8 == 0:
                    self.poll()
                after = self.wall_board(board, index)
                if dag0 & bit:
                    nm0 = after.metric(p0, 0)
                    new_d0, new_r0 = nm0
                    if new_d0 == UNREACHABLE:
                        continue
                else:
                    after.cache[0][p0] = m0
                    after.dag_cache[0][p0] = dag0
                    new_d0, new_r0 = old_d0, old_r0
                if dag1 & bit:
                    nm1 = after.metric(p1, 1)
                    new_d1, new_r1 = nm1
                    if new_d1 == UNREACHABLE:
                        continue
                else:
                    after.cache[1][p1] = m1
                    after.dag_cache[1][p1] = dag1
                    new_d1, new_r1 = old_d1, old_r1
                new_own_d, new_enemy_d = (new_d0, new_d1) if turn == 0 else (new_d1, new_d0)
                d_me_inc = new_own_d - old_own_d
                d_op_inc = new_enemy_d - old_enemy_d
                net_gain = d_op_inc - d_me_inc

                # Prune self-damaging walls:
                # 1. Prune if net_gain < 0 (damages us more than opponent)
                # 2. Prune if d_me_inc > 0 and d_op_inc <= 0 (damages us without slowing opponent)
                # 3. If d_me_inc > 1, only allow if opponent is close to goal (old_enemy_d <= 4)
                if (net_gain < 0 or (d_me_inc > 0 and d_op_inc <= 0)) and pb == 0 and not is_cat_e:
                    continue
                if d_me_inc > 1 and old_enemy_d > 4 and pb == 0:
                    continue
                # Root-only zero-gain preparation walls (vertical slice / chain) for 2-wall pressure probe
                if net_gain <= 0 and pb == 0 and not is_cat_e:
                    if (
                        ply == 0
                        and own_walls >= 3
                        and placed_mask
                        and d_op_inc == 0
                        and d_me_inc == 0
                        and not adj_pawns
                        and (bool(dag_enemy_cur & bit) or bool(placed_mask & _ADJ_WALLS[index]))
                        and bool((dag_enemy_cur | _NEAR[enemy]) & bit)
                    ):
                        is_ch = bool(placed_mask & _ADJ_WALLS[index])
                        tac_p = tactical_table[index] + (0.35 if is_ch else 0.0)
                        if index >= 64 and (index % 8 in (3, 4)):
                            tac_p += 18.0
                        plan_base = zero_gain_base - (50.0 if (own_walls >= 6 and old_enemy_d >= 5) else 0.0)
                        plans.append((plan_base + tac_p, plan_base, 81 + index, after, p0, p1, nw0, nw1, is_ch))
                    continue
                if net_gain <= 1 and index < 64 and wr in (0, 7) and abs(er - wr) >= 3 and pb == 0 and not is_cat_e:
                    continue
                # Category E: accept if d_me_inc <= 0 and d_op_inc >= 1
                if is_cat_e and d_me_inc > 0:
                    continue
                if is_cat_e and d_op_inc < 1 and pb == 0:
                    if (
                        ply == 0
                        and own_walls >= 3
                        and placed_mask
                        and d_op_inc == 0
                        and d_me_inc == 0
                        and not adj_pawns
                        and bool(placed_mask & _ADJ_WALLS[index])
                        and bool((dag_enemy_cur | _NEAR[enemy]) & bit)
                    ):
                        tac_p = tactical_table[index] + 0.35 + (18.0 if (index >= 64 and (index % 8 in (3, 4))) else 0.0)
                        plan_base = zero_gain_base - (50.0 if (own_walls >= 6 and old_enemy_d >= 5) else 0.0)
                        plans.append((plan_base + tac_p, plan_base, 81 + index, after, p0, p1, nw0, nw1, True))
                    continue

                if adj_pawns:
                    base_score = -self.evaluate(after, p0, p1, nw0, nw1, 1 - turn, ply + 1)
                elif new_enemy_d == 1:
                    base_score = float(-(MATE - ply - 2))
                else:
                    new_own_r, new_enemy_r = (new_r0, new_r1) if turn == 0 else (new_r1, new_r0)
                    c_o = new_enemy_d + WALL_CAP_EXTRA
                    ef_o = nw_own if nw_own <= c_o else c_o + WALL_EXCESS * (nw_own - c_o)
                    c_e = new_own_d + WALL_CAP_EXTRA
                    ef_e = enemy_walls if enemy_walls <= c_e else c_e + WALL_EXCESS * (enemy_walls - c_e)
                    e_own_exp = new_enemy_d + WALL_STEP * ef_o
                    e_enemy_exp = new_own_d + WALL_STEP * ef_e
                    base_score = -(
                        STEP * (e_enemy_exp - e_own_exp)
                        + const_bias
                        - DIST_PENALTY * new_enemy_d
                        + ROUTE_BONUS * (new_enemy_r - new_own_r)
                    )

                if own_walls == 1 and old_enemy_d > 1 and net_gain <= 1:
                    base_score -= 150.0
                elif own_walls == 2 and old_enemy_d > 2 and net_gain <= 0:
                    base_score -= 80.0
                elif own_walls >= 6 and old_enemy_d >= 5 and net_gain <= 1 and pb == 0:
                    base_score -= 50.0
                if d_me_inc > 0:
                    base_score -= 100.0 * d_me_inc
                    if index < 64:
                        my_r = own // SIZE
                        is_behind = (wr >= my_r) if (turn == 0) else (wr < my_r)
                        if is_behind and abs(wr - my_r) <= 2:
                            base_score -= 80.0
                tac = tactical_table[index] + (0.35 if (placed_mask & _ADJ_WALLS[index]) else 0.0)
                # Vertical corridor slicing bonus (cols 3, 4)
                if index >= 64 and (index % 8 in (3, 4)) and d_op_inc >= 1 and d_me_inc <= 0 and old_enemy_d >= 4:
                    tac += 18
                # History heuristic + killer bonus
                h_bonus = min(hist[81 + index] >> 4, 35) if (81 + index) < 210 else 0
                k_bonus = 0
                action_id = 81 + index
                if k1 == action_id:
                    k_bonus = 95
                elif k2 == action_id:
                    k_bonus = 65
                walls.append((base_score + tac + pb + h_bonus + k_bonus, base_score, action_id, after, p0, p1, nw0, nw1))
            walls.sort(key=lambda item: item[0], reverse=True)
            limit = WALL_LIMITS[min(ply, len(WALL_LIMITS) - 1)]
            # Adaptive wall limit based on opponent style (Claude feature)
            if self.opp_style == "SPAMMER" and ply == 0:
                limit = min(limit + 2, 16)
            elif self.opp_style == "RUNNER" and ply == 0:
                limit = max(limit - 2, 6)
            selected = walls[:limit]
            # Root-only 2-wall Preparation Plans probe using dominator gates (ported from lambda)
            if ply == 0 and plans and own_walls >= 3 and placed_mask and perf_counter() + 0.15 < self.deadline:
                plans.sort(
                    key=lambda item: (
                        (item[2] - 81) >= 64 and ((item[2] - 81) % 8 in (3, 4)),
                        item[8],
                        bool(_NEAR[enemy] & (1 << (item[2] - 81))),
                        item[0],
                    ),
                    reverse=True,
                )
                try:
                    base_off, _ = self.pressure(board, enemy, own, 1 - turn)
                    assessed = []
                    for item in plans[:4]:
                        if perf_counter() + 0.10 >= self.deadline:
                            break
                        offense, _ = self.pressure(item[3], enemy, own, 1 - turn)
                        if offense >= max(3, base_off + 2):
                            defense = self.pressure(item[3], own, enemy, turn)[0] if enemy_walls > 0 else 0
                            if offense > defense:
                                assessed.append((offense * 3 - defense, item[0], item))
                    if assessed:
                        assessed.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
                        included_ids = {w[2] for w in selected}
                        act_id = assessed[0][2][2]
                        self.protected.add((placed_mask, turn, act_id))
                        if act_id not in included_ids:
                            selected.append(assessed[0][2][:8])
                except _Deadline:
                    pass
            if preferred is not None and 81 <= preferred < 209:
                special = next((item for item in walls if item[2] == preferred), None)
                if special is not None and special not in selected:
                    selected.append(special)
            for _, base_score, action, after, np0, np1, nw0, nw1 in selected:
                if after is None:
                    after = self.wall_board(board, action - 81)
                    after.cache[0][p0] = m0
                    after.dag_cache[0][p0] = dag0
                    after.cache[1][p1] = m1
                    after.dag_cache[1][p1] = dag1
                children.append((base_score, action, after, np0, np1, nw0, nw1))
        children.sort(key=lambda item: (item[1] == preferred, item[0], item[1] != 209), reverse=True)
        return children

    def quiesce(self, board, p0, p1, w0, w1, turn, alpha, beta, ply, left, value=None):
        self.poll()
        if value is None:
            value = self.evaluate(board, p0, p1, w0, w1, turn, ply)
        if abs(value) > MATE / 2 or ply >= self.remaining or left == 0:
            return value
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        enemy_goal = self.goals[1 - turn]
        if abs(enemy // SIZE - enemy_goal) > 2:
            return value
        if not any(cell // SIZE == enemy_goal for cell in _fast_moves(enemy, own, board)):
            return value
        best = -MATE + ply + 2
        for child in self.children(board, p0, p1, w0, w1, turn, ply):
            if child[0] < -MATE / 2:
                continue
            _, action, after, np0, np1, nw0, nw1 = child
            score = -self.quiesce(
                after, np0, np1, nw0, nw1, 1 - turn, -beta, -alpha, ply + 1, left - 1
            )
            best = max(best, score)
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        return best

    def negamax(self, board, p0, p1, w0, w1, turn, depth, alpha, beta, ply, pre=None):
        self.nodes += 1
        if self.nodes % 32 == 0:
            self.poll()
        value = pre if pre is not None else self.evaluate(board, p0, p1, w0, w1, turn, ply)
        if abs(value) > MATE / 2 or ply >= self.remaining:
            return value

        # In-tree cycle detection (ported from Claude)
        tt_key = (
            board.mask,
            p0,
            p1,
            w0,
            w1,
            turn,
            min(ply, 2),
            min(self.remaining - ply, 32),
        )
        if ply > 0 and tt_key in self.path_keys:
            return self.draw_score(turn)

        # RaceSolver for 0-wall in deep search (ported from Claude)
        if ply > 0 and w0 == 0 and w1 == 0:
            goal_bits_0 = _GOAL_BITS[self.goals[0]]
            goal_bits_1 = _GOAL_BITS[self.goals[1]]
            return _race_solve(p0, p1, turn, board.opens, goal_bits_0, goal_bits_1)

        if depth <= 0:
            return self.quiesce(board, p0, p1, w0, w1, turn, alpha, beta, ply, QUIESCE_PLIES, value=value)

        cached = self.table.get(tt_key)
        preferred = None
        if cached:
            old_depth, old_value, flag, preferred = cached
            if old_value > MATE / 2:
                old_value -= ply
            elif old_value < -MATE / 2:
                old_value += ply
            if old_depth >= depth and ply > 0:
                if flag == 0:
                    return old_value
                if flag == 1:
                    alpha = max(alpha, old_value)
                elif flag == 2:
                    beta = min(beta, old_value)
                if alpha >= beta:
                    return old_value

        original_alpha, original_beta = alpha, beta
        best, best_action = -INF, None

        self.path_keys.add(tt_key)
        try:
            for order, child in enumerate(self.children(board, p0, p1, w0, w1, turn, ply, preferred)):
                cscore, action, after, np0, np1, nw0, nw1 = child
                pre = -cscore if (action < 81 or action == 209) else None
                if order == 0:
                    result = -self.negamax(
                        after, np0, np1, nw0, nw1, 1 - turn, depth - 1, -beta, -alpha, ply + 1, pre
                    )
                else:
                    # Late Move Reduction (LMR): giảm 1 ply cho các tường muộn ở độ sâu >= 3 (trừ tường trong protected)
                    reduce = (
                        1
                        if (
                            depth >= 3
                            and order >= 4
                            and 81 <= action < 209
                            and (board.mask, turn, action) not in self.protected
                        )
                        else 0
                    )
                    result = -self.negamax(
                        after, np0, np1, nw0, nw1, 1 - turn, depth - 1 - reduce, -alpha - 0.001, -alpha, ply + 1, pre
                    )
                    if result > alpha:
                        result = -self.negamax(
                            after, np0, np1, nw0, nw1, 1 - turn, depth - 1, -beta, -alpha, ply + 1, pre
                        )
                if result > best:
                    best, best_action = result, action
                alpha = max(alpha, result)
                if alpha >= beta:
                    # Update killer moves
                    if ply < 64 and action is not None:
                        if self.killer1[ply] != action:
                            self.killer2[ply] = self.killer1[ply]
                            self.killer1[ply] = action
                        # Update history heuristic for wall moves
                        if 81 <= action < 209:
                            self.history[action] += depth * depth
                    break
        finally:
            self.path_keys.discard(tt_key)

        flag = 2 if best <= original_alpha else 1 if best >= original_beta else 0
        stored = best + ply if best > MATE / 2 else best - ply if best < -MATE / 2 else best
        if not cached or depth >= cached[0]:
            self.table[tt_key] = (depth, stored, flag, best_action)
        return best

    def choose(self):
        p0, p1 = self.positions
        w0, w1 = self.walls
        turn = self.root_turn
        try:
            root = self.children(self.root, p0, p1, w0, w1, turn, 0)
        except _Deadline:
            self.deadline = perf_counter() + 0.05
            root = self.children(self.root, p0, p1, 0 if turn == 0 else w0, 0 if turn == 1 else w1, turn, 0)
        safe = [child for child in root if child[0] > -MATE / 2]
        if safe:
            root = safe
        best_action = root[0][1]
        best_value = -INF
        vuln_biases = {}
        ew = w1 if turn == 0 else w0
        if ew > 0:
            enemy_p = p1 if turn == 0 else p0
            for child in root:
                act = child[1]
                if act < 81:
                    try:
                        v = self._pawn_vulnerability(self.root, act, enemy_p, turn, ew)
                        if v >= 3:
                            vuln_biases[act] = -15 * min(6, v - 2)
                    except Exception:
                        pass

        for depth in range(1, MAX_DEPTH + 1):
            results, alpha = [], -INF
            new_root_scores = {}
            try:
                use_aspiration = depth >= 4 and abs(best_value) < MATE / 2

                for child in root:
                    self.poll()
                    cscore, action, after, np0, np1, nw0, nw1 = child
                    pre = -cscore if (action < 81 or action == 209) else None
                    rep = self.repetitions.get(action, 0) if action < 81 else 0
                    # Progressive anti-oscillation penalty + root vulnerability tie-breaker
                    bias = (
                        (-min(rep, 4) if rep <= 2 else -(4 + 35 * min(rep - 2, 6) * (1 + (rep - 2) // 3)))
                        + vuln_biases.get(action, 0)
                    )

                    if use_aspiration and len(results) == 0:
                        asp_alpha = max(-INF, best_value - 85)
                        asp_beta = best_value + 85
                        value = (
                            -self.negamax(
                                after, np0, np1, nw0, nw1, 1 - turn, depth - 1,
                                -(asp_beta - bias), -(asp_alpha - bias), 1, pre
                            )
                            + bias
                        )
                        if value <= asp_alpha or value >= asp_beta:
                            value = (
                                -self.negamax(
                                    after, np0, np1, nw0, nw1, 1 - turn, depth - 1,
                                    -INF, -alpha + bias, 1, pre
                                )
                                + bias
                            )
                    else:
                        value = (
                            -self.negamax(
                                after, np0, np1, nw0, nw1, 1 - turn, depth - 1, -INF, -alpha + bias, 1, pre
                            )
                            + bias
                        )
                    results.append((value, child))
                    new_root_scores[action] = value
                    alpha = max(alpha, value)
            except _Deadline:
                if len(results) >= 2:
                    first_value = results[0][0]
                    value, child = max(results, key=lambda item: item[0])
                    if value > first_value:
                        best_action = child[1]
                break
            self.completed_depth = depth
            results.sort(key=lambda item: (item[0], item[1][1] != 209), reverse=True)
            best_value_new, best_child = results[0]
            best_action = best_child[1]
            best_value = best_value_new
            self.root_eval = best_value
            self.root_move_scores.update(new_root_scores)
            root = [child for _, child in results]
            if best_value > MATE / 2 and self.walls[1 - turn] == 0:
                break
        return best_action


# ═══════════════════════════════════════════════════════════════════════════════
# OPENING BOOK (porting từ Claude — tiết kiệm 0.5s cho các nước mở đầu)
# ═══════════════════════════════════════════════════════════════════════════════
_OPENING_BOOK = {
    # P0 standard
    (0, 76, 4): [7, 4],    # P0 at [8,4], P1 at [0,4] → move to [7,4]
    (0, 67, 13): [6, 4],   # P0 at [7,4], P1 at [1,4] → move to [6,4]
    # P1 standard
    (1, 4, 76): [1, 4],    # P1 at [0,4], P0 at [8,4] → move to [1,4]
    (1, 13, 67): [2, 4],   # P1 at [1,4], P0 at [7,4] → move to [2,4]
    # Flank variations P0
    (0, 75, 3): [7, 3],    # P0 at [8,3], P1 at [0,3] → move to [7,3]
    (0, 75, 4): [7, 3],    # P0 at [8,3], P1 at [0,4] → move to [7,3]
    (0, 75, 5): [7, 3],    # P0 at [8,3], P1 at [0,5] → move to [7,3]
    (0, 77, 5): [7, 5],    # P0 at [8,5], P1 at [0,5] → move to [7,5]
    (0, 77, 4): [7, 5],    # P0 at [8,5], P1 at [0,4] → move to [7,5]
    (0, 77, 3): [7, 5],    # P0 at [8,5], P1 at [0,3] → move to [7,5]
    (0, 76, 3): [7, 4],    # P0 at [8,4], P1 at [0,3] → move to [7,4]
    (0, 76, 5): [7, 4],    # P0 at [8,4], P1 at [0,5] → move to [7,4]
    # Flank variations P1
    (1, 4, 75): [1, 4],    # P1 at [0,4] vs P0 at [8,3]
    (1, 4, 77): [1, 4],    # P1 at [0,4] vs P0 at [8,5]
    (1, 5, 76): [1, 5],    # P1 at [0,5] vs P0 at [8,4] → move to [1,5]
    (1, 3, 76): [1, 3],    # P1 at [0,3] vs P0 at [8,4] → move to [1,3]
    (1, 3, 75): [1, 3],    # P1 at [0,3], P0 at [8,3] → straight [1,3]
    (1, 3, 77): [1, 3],    # P1 at [0,3], P0 at [8,5] → straight [1,3]
    (1, 5, 77): [1, 5],    # P1 at [0,5], P0 at [8,5] → straight [1,5]
    (1, 5, 75): [1, 5],    # P1 at [0,5], P0 at [8,3] → straight [1,5]
    # Second move flank
    (0, 66, 13): [6, 3],   # P0 at [7,3], P1 at [1,4] → step up [6,3]
    (0, 68, 13): [6, 5],   # P0 at [7,5], P1 at [1,4] → step up [6,5]
    (0, 66, 12): [6, 3],   # P0 at [7,3], P1 at [1,3] → step up [6,3]
    (0, 68, 14): [6, 5],   # P0 at [7,5], P1 at [1,5] → step up [6,5]
    (1, 12, 66): [2, 3],   # P1 at [1,3], P0 at [7,3] → step down [2,3]
    (1, 14, 68): [2, 5],   # P1 at [1,5], P0 at [7,5] → step down [2,5]
    (1, 13, 66): [2, 4],   # P1 at [1,4], P0 at [7,3]
    (1, 13, 68): [2, 4],   # P1 at [1,4], P0 at [7,5]
}


def _lookup_opening(observation):
    turn = observation.get("turn", 0)
    if turn > 4:
        return None
    pid = observation["player_id"]
    you_pos = observation["you"]["position"]
    opp_pos = observation["opponent"]["position"]
    walls = observation["walls"]
    if walls["horizontal"] or walls["vertical"]:
        return None  # No book if walls placed
    # Opening guard: break symmetric jump opposition and protect baseline
    if turn == 4:
        goal = observation.get("goal_row", 0 if pid == 0 else 8)
        if you_pos == ([6, 4] if goal == 0 else [2, 4]) and opp_pos == ([2, 4] if goal == 0 else [6, 4]):
            wall = {"type": "wall", "orientation": "H", "row": 6 if goal == 0 else 1, "col": 3}
            if _legal(wall, observation):
                return wall
    own = you_pos[0] * SIZE + you_pos[1]
    enemy = opp_pos[0] * SIZE + opp_pos[1]
    key = (pid, own, enemy)
    target = _OPENING_BOOK.get(key)
    if target is not None:
        if target in observation["legal_moves"]:
            return {"type": "move", "to": list(target)}
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# GIAO THỨC ARENA
# ═══════════════════════════════════════════════════════════════════════════════
_ENDGAME_CACHE = None
_LAST_STATS = {}
_RECENT_MASK = None
_RECENT_TURN = -1
_RECENT_CELLS = []


def _action_to_dict(action):
    if action < 81:
        return {"type": "move", "to": [action // SIZE, action % SIZE]}
    if action < 209:
        return _wall_action(action - 81)
    return {"type": "pass"}


def _legal(action, observation):
    if action["type"] == "move":
        return action["to"] in observation["legal_moves"]
    if action["type"] == "wall":
        return {key: action[key] for key in ("orientation", "row", "col")} in observation["legal_walls"]
    return action["type"] == "pass"


def _greedy(observation):
    moves = observation["legal_moves"]
    if not moves:
        return {"type": "pass"}
    distances = observation["distances"]
    return {
        "type": "move",
        "to": list(
            min(
                moves,
                key=lambda move: (
                    distances[move[0]][move[1]] if distances[move[0]][move[1]] >= 0 else UNREACHABLE,
                    abs(move[1] - 4),
                    move,
                ),
            )
        ),
    }


def _choose_action_impl(observation, started, budget):
    global _ENDGAME_CACHE, _LAST_STATS, _RECENT_MASK, _RECENT_TURN, _PREV_OBS
    deadline = started + budget
    moves = observation["legal_moves"]
    goal = observation["you"]["goal_row"]

    # === Immediate win ===
    for move in moves:
        if move[0] == goal:
            _LAST_STATS = {"mode": "immediate_win", "depth": 0}
            return {"type": "move", "to": list(move)}

    # === Opponent Modeling update ===
    if _PREV_OBS is not None:
        if observation.get("turn", 0) < (_PREV_OBS.get("turn", 0) or 0):
            _OPP_MODEL.reset()
        else:
            _OPP_MODEL.update(_PREV_OBS, observation)
    _PREV_OBS = observation

    # === Opening Book ===
    book = _lookup_opening(observation)
    if book is not None:
        _LAST_STATS = {"mode": "opening_book"}
        return book

    fallback = _greedy(observation)
    try:
        search = _Search(observation, deadline)
        p0, p1 = search.positions
        if search.root.mask != _RECENT_MASK or observation["turn"] <= _RECENT_TURN:
            _RECENT_CELLS.clear()
        _RECENT_MASK, _RECENT_TURN = search.root.mask, observation["turn"]
        _RECENT_CELLS.append(search.positions[search.root_turn])
        del _RECENT_CELLS[:-20]
        for cell in _RECENT_CELLS:
            search.repetitions[cell] = search.repetitions.get(cell, 0) + 1

        # === Endgame solver (0 walls) ===
        if search.walls == [0, 0]:
            try:
                if _ENDGAME_CACHE is None or _ENDGAME_CACHE.key != (search.root.mask, search.goals):
                    _ENDGAME_CACHE = _Endgame(search.root, search.goals, deadline - 0.03)
                destination = _ENDGAME_CACHE.action(
                    p0, p1, search.root_turn, search.remaining, search.repetitions
                )
                own = search.positions[search.root_turn]
                action = _action_to_dict(destination if destination != own else 209)
                if _legal(action, observation):
                    outcome, distance = _ENDGAME_CACHE.probe(p0, p1, search.root_turn)
                    _LAST_STATS = {
                        "mode": "exact_endgame",
                        "outcome": outcome,
                        "plies_to_finish": distance,
                        "elapsed": perf_counter() - started,
                    }
                    return action
            except _Deadline:
                pass

        # === Main search ===
        try:
            action = _action_to_dict(search.choose())
            if _legal(action, observation):
                fallback = action
        except _Deadline:
            pass
        _LAST_STATS = {
            "mode": "search",
            "depth": search.completed_depth,
            "nodes": search.nodes,
            "boards": len(search.boards),
            "opp_style": search.opp_style,
            "elapsed": perf_counter() - started,
        }
    except Exception:
        _LAST_STATS = {"mode": "error_fallback"}
    return fallback


_BUDGET_PENALTY = 0.0


def choose_action(observation):
    """Adaptive soft time management: auto-shrinks budget under system CPU contention."""
    global _BUDGET_PENALTY
    started = perf_counter()
    budget = max(MIN_SECONDS, THINK_SECONDS - _BUDGET_PENALTY)
    try:
        action = _choose_action_impl(observation, started, budget)
    except Exception:
        action = _greedy(observation)
    over = perf_counter() - started - budget
    if over > 0.03:
        _BUDGET_PENALTY = min(0.30, _BUDGET_PENALTY + 2.0 * over)
    else:
        _BUDGET_PENALTY = max(0.0, _BUDGET_PENALTY - 0.01)
    return action


step = choose_action
