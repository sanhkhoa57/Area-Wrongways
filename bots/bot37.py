"""Wrongway: cached bitboard PVS, defensive walls and exact pawn endgames.

Single-file Python 3.9+ agent using only the standard library. Search has a
soft 0.80 s budget against Arena's 1 s action timeout.
"""

import gc
import random
import time
from collections import deque

# Disable GC during search to avoid mid-search pauses
gc.disable()
_GC_COUNTER = [0]

# =============================================================================
# 1. CẤU HÌNH THỜI GIAN & SIÊU THAM SỐ
# =============================================================================

MOVE_TIME_LIMIT = 0.80   # tăng từ 0.65 → 0.80s (arena timeout=1.0s, buffer 0.20s)

CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)              # 0: lên, 1: phải, 2: xuống, 3: trái
WALL0 = 81                         # mã hành động >= WALL0 là đặt tường (81..208)
RED, BLUE = 0, 1                   # người chơi 0 (đích hàng 0) và 1 (đích hàng 8)
START = (76, 4)                    # [8, 4] và [0, 4]
GOAL = (
    sum(1 << c for c in range(0, 9)),      # bitboard hàng 0
    sum(1 << c for c in range(72, 81)),    # bitboard hàng 8
)
GOAL_ROW = (0, 8)
WIN = 100000

TIME_BUDGET = MOVE_TIME_LIMIT
ROOT_WALLS = 18                    # số tường ứng viên tối đa ở gốc
INNER_WALLS = 12                   # số tường ứng viên tối đa ở nút trong cây
REPEAT_PENALTY = 8                # điểm phạt cho mỗi lần lặp lại thế cờ ở gốc
W_DIST = 10                        # trọng số khoảng cách
TEMPO = 5
TT_SIZE = 250000

ENDGAME_ON = True
ALLOW_PASS = True
PASS = 209
TABLEBASE_BUDGET = 0.25            # thời gian tối đa giải bảng tàn cuộc

# =============================================================================
# CẤU HÌNH PVS / ASPIRATION / NMP / LMR / KILLER / HISTORY
# =============================================================================

ASP_DELTA = 35                     # cửa sổ aspiration ban đầu ±35
NMP_R = 2                          # Null-Move Pruning reduction
NMP_MARGIN = 18                    # bonus margin an toàn trên beta để thử NMP
LMR_WALL_MIN = 3                   # LMR áp dụng khi wall index >= LMR_WALL_MIN
LMR_MIN_DEPTH = 3                  # LMR chỉ hoạt động khi depth >= LMR_MIN_DEPTH
RACE_SCORE = 5000                  # decisive race; still below a proven mate

# Marginal wall valuation (mỗi tường tiếp theo có giá trị giảm dần)
_WALL_MARGINAL = [24, 18, 14, 12, 12, 12, 12, 12, 12, 12]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]

# Pre-allocated killer/history arrays (tránh allocation trong search)
_HISTORY = [0] * 128               # history heuristic cho từng mã tường 0..127
_KILLERS = [[None, None] for _ in range(128)]  # killers[ply][0..1]

# Khởi tạo bảng băm Zobrist cố định
_rng = random.Random(20240607)
ZP = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
ZW = [_rng.getrandbits(64) for _ in range(128)]
ZLEFT = [_rng.getrandbits(64) for _ in range(128)]
ZT = _rng.getrandbits(64)


# =============================================================================
# 2. TIỀN XỬ LÝ HÌNH HỌC BÀN CỜ & TƯỜNG
# =============================================================================

def wall_info(w):
    """Đổi mã tường 0..127 -> (hướng 'h'/'v', row, col)."""
    return ("h" if w < 64 else "v", (w & 63) >> 3, w & 7)


def wall_id(orient, r, c):
    """Đổi (hướng 'h'/'v', row, col) -> mã tường 0..127."""
    return (0 if orient == "h" else 64) + r * 8 + c


WALL_EDGES, WALL_PTS, CONFLICT = [], [], []
for _w in range(128):
    _o, _r, _c = wall_info(_w)
    if _o == "h":
        _e = [
            (_r * 9 + _c, 2),
            (_r * 9 + _c + 1, 2),
            ((_r + 1) * 9 + _c, 0),
            ((_r + 1) * 9 + _c + 1, 0),
        ]
        _p = [(_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2]
        _cf = [wall_id("h", _r, _c), wall_id("v", _r, _c)]
        if _c > 0:
            _cf.append(wall_id("h", _r, _c - 1))
        if _c < 7:
            _cf.append(wall_id("h", _r, _c + 1))
    else:
        _e = [
            (_r * 9 + _c, 1),
            ((_r + 1) * 9 + _c, 1),
            (_r * 9 + _c + 1, 3),
            ((_r + 1) * 9 + _c + 1, 3),
        ]
        _p = [_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1]
        _cf = [wall_id("v", _r, _c), wall_id("h", _r, _c)]
        if _r > 0:
            _cf.append(wall_id("v", _r - 1, _c))
        if _r < 7:
            _cf.append(wall_id("v", _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_PTS.append(tuple(_p))
    CONFLICT.append(tuple(_cf))

BLOCKERS = [[() for _ in range(4)] for _ in range(CELLS)]
for _cell in range(CELLS):
    for _d in range(4):
        _lst = []
        for _w in range(128):
            if (_cell, _d) in WALL_EDGES[_w]:
                _lst.append(_w)
        BLOCKERS[_cell][_d] = tuple(_lst)


def _initial_can():
    can = [0, 0, 0, 0]
    for cell in range(CELLS):
        r, c = divmod(cell, 9)
        if r > 0:
            can[0] |= 1 << cell
        if c < 8:
            can[1] |= 1 << cell
        if r < 8:
            can[2] |= 1 << cell
        if c > 0:
            can[3] |= 1 << cell
    return can


# Topology-indexed BFS field cache (tránh tính lại BF khi topology không đổi)
_BF_CACHE = {}


def _get_fields(can):
    """Trả về (BF0, BF1) cached theo topology `can`. O(1) cho cùng tường."""
    key = (can[0], can[1], can[2], can[3])
    entry = _BF_CACHE.get(key)
    if entry is None:
        if len(_BF_CACHE) >= 24000:
            # Evict old topologies in batches without discarding every hot entry.
            for old in list(_BF_CACHE)[:6000]:
                del _BF_CACHE[old]
        entry = (BF(GOAL[0], can), BF(GOAL[1], can))
        _BF_CACHE[key] = entry
    return entry


# =============================================================================
# =============================================================================

class BF:
    """BFS ngược từ hàng đích bằng bitboard, mở rộng lười (lazy) theo từng lớp."""

    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3", "paths", "occupied")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can
        self.paths = {}
        self.occupied = {}

    def blocked_distance(self, cell, enemy):
        """Exact distance when the other pawn waits, including directed jumps."""
        layers = self.occupied.get(enemy)
        if layers is None:
            can = (self.c0, self.c1, self.c2, self.c3)
            jumps = []
            for d, delta in enumerate(DIRS):
                source = enemy - delta
                if not 0 <= source < CELLS or not ((can[d] >> source) & 1):
                    continue
                if (can[d] >> enemy) & 1:
                    landing = 1 << (enemy + delta)
                else:
                    landing = 0
                    for side in ((d + 1) & 3, (d + 3) & 3):
                        if (can[side] >> enemy) & 1:
                            landing |= 1 << (enemy + DIRS[side])
                jumps.append((1 << source, landing))
            front = self.layers[0] & ~(1 << enemy)
            seen = front | (1 << enemy)
            layers = [front]
            while front:
                nxt = (((front << 9) & self.c0) | ((front >> 1) & self.c1)
                       | ((front >> 9) & self.c2) | ((front << 1) & self.c3)) & ~seen
                for source, landing in jumps:
                    if front & landing:
                        nxt |= source & ~seen
                if not nxt:
                    break
                layers.append(nxt)
                seen |= nxt
                front = nxt
            self.occupied[enemy] = layers
        bit = 1 << cell
        for distance, layer in enumerate(layers):
            if layer & bit:
                return distance
        return INF

    def dist(self, cell):
        bit = 1 << cell
        k = 0
        for layer in self.layers:
            if layer & bit:
                return k
            k += 1
        layers = self.layers
        seen = self.seen
        f = layers[-1]
        c0, c1, c2, c3 = self.c0, self.c1, self.c2, self.c3
        while True:
            f = (
                ((f << 9) & c0)
                | ((f >> 1) & c1)
                | ((f >> 9) & c2)
                | ((f << 1) & c3)
            ) & ~seen
            if not f:
                return INF
            seen |= f
            self.seen = seen
            layers.append(f)
            if f & bit:
                return k
            k += 1


class Board:
    """Biểu diễn bàn cờ hỗ trợ make/unmake nhanh và kiểm tra điểm giao cắt lưới 10x10."""

    def __init__(self):
        self.pos = [START[0], START[1]]
        self.left = [10, 10]
        self.can = _initial_can()
        self.forbid = [0] * 128
        self.pts = [0] * 100
        for R in range(10):
            for C in range(10):
                if R in (0, 9) or C in (0, 9):
                    self.pts[R * 10 + C] = 1
        self.turn = RED
        self.whash = 0

    def copy(self):
        b = Board.__new__(Board)
        b.pos = self.pos[:]
        b.left = self.left[:]
        b.can = self.can[:]
        b.forbid = self.forbid[:]
        b.pts = self.pts[:]
        b.turn = self.turn
        b.whash = self.whash
        return b

    def fields(self):
        return _get_fields(self.can)

    def dist(self, start, goal):
        cur = 1 << start
        if cur & goal:
            return 0
        seen = cur
        c0, c1, c2, c3 = self.can
        d = 0
        while cur:
            d += 1
            nxt = (
                ((cur & c0) >> 9)
                | ((cur & c1) << 1)
                | ((cur & c2) << 9)
                | ((cur & c3) >> 1)
            ) & ~seen
            if nxt & goal:
                return d
            seen |= nxt
            cur = nxt
        return INF

    def pawn_moves(self, player=None):
        t = self.turn if player is None else player
        me, op = self.pos[t], self.pos[1 - t]
        can = self.can
        out = []
        for d in range(4):
            if not (can[d] >> me) & 1:
                continue
            tgt = me + DIRS[d]
            if tgt != op:
                out.append(tgt)
            elif (can[d] >> tgt) & 1:
                out.append(tgt + DIRS[d])
            else:
                for s in ((d + 1) & 3, (d + 3) & 3):
                    if (can[s] >> tgt) & 1:
                        out.append(tgt + DIRS[s])
        return out

    def _edges(self, w, on):
        can = self.can
        if on:
            for cell, d in WALL_EDGES[w]:
                can[d] |= 1 << cell
        else:
            for cell, d in WALL_EDGES[w]:
                can[d] &= ~(1 << cell)

    def put_wall(self, w):
        self._edges(w, False)
        for x in CONFLICT[w]:
            self.forbid[x] += 1
        for p in WALL_PTS[w]:
            self.pts[p] += 1
        self.whash ^= ZW[w]

    def remove_wall(self, w):
        self._edges(w, True)
        for x in CONFLICT[w]:
            self.forbid[x] -= 1
        for p in WALL_PTS[w]:
            self.pts[p] -= 1
        self.whash ^= ZW[w]

    def wall_ok(self, w, player=None):
        t = self.turn if player is None else player
        if self.left[t] <= 0 or self.forbid[w]:
            return False
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        if (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0) >= 2:
            self._edges(w, False)
            ok = (
                self.dist(self.pos[0], GOAL[0]) < INF
                and self.dist(self.pos[1], GOAL[1]) < INF
            )
            self._edges(w, True)
            return ok
        return True

    def zkey(self):
        return (
            self.whash
            ^ ZP[0][self.pos[0]]
            ^ ZP[1][self.pos[1]]
            ^ ZLEFT[self.left[0] * 11 + self.left[1]]
            ^ (ZT if self.turn else 0)
        )


# =============================================================================
# 4. BẢNG TÀN CUỘC GIẢI NGƯỢC 2 QUÂN CỜ (RETROGRADE ENDGAME TABLEBASE)
# =============================================================================

class _EGTimeout(Exception):
    pass


def _moves_with(can, me, op):
    out = []
    for d in range(4):
        if not (can[d] >> me) & 1:
            continue
        tgt = me + DIRS[d]
        if tgt != op:
            out.append(tgt)
        elif (can[d] >> tgt) & 1:
            out.append(tgt + DIRS[d])
        else:
            for s in ((d + 1) & 3, (d + 3) & 3):
                if (can[s] >> tgt) & 1:
                    out.append(tgt + DIRS[s])
    return out


class _PawnEndgame:
    """Giải ngược chính xác toàn bộ 2 x 81 x 81 trạng thái hai quân khi đối thủ còn 0 tường."""

    __slots__ = ("key", "graph", "contact", "maps", "result", "distance")

    def __init__(self, key, can, deadline):
        self.key = key
        graph = []
        for i in range(CELLS):
            graph.append(tuple(i + DIRS[d] for d in range(4) if (can[d] >> i) & 1))
        contact = {}
        for i in range(CELLS):
            for j in graph[i]:
                contact[i * CELLS + j] = tuple(_moves_with(can, i, j))
        self.graph, self.contact = graph, contact
        maps = []
        for g in (0, 1):
            bf = BF(GOAL[g], can)
            maps.append([bf.dist(i) for i in range(CELLS)])
        self.maps = maps
        r0, r1 = maps
        span = CELLS * CELLS
        result = [0] * (2 * span)
        distance = [0] * (2 * span)
        remaining = [0] * (2 * span)
        longest = [0] * (2 * span)
        previous = [[] for _ in range(2 * span)]
        pending = deque()
        push = pending.append
        cget = contact.get
        extra = 1 if ALLOW_PASS else 0
        for p0 in range(CELLS):
            if (p0 & 7) == 0 and time.perf_counter() > deadline:
                raise _EGTimeout
            if r0[p0] >= INF:
                continue
            won0 = p0 // 9 == GOAL_ROW[0]
            base0 = p0 * CELLS
            for p1 in range(CELLS):
                if p1 == p0 or r1[p1] >= INF:
                    continue
                pair = base0 + p1
                if won0 or p1 // 9 == GOAL_ROW[1]:
                    w0 = 1 if won0 else -1
                    result[pair] = w0
                    result[span + pair] = -w0
                    push(pair)
                    push(span + pair)
                    continue
                mv = cget(pair)
                if mv is None:
                    mv = graph[p0]
                remaining[pair] = len(mv) + extra
                for d in mv:
                    previous[span + d * CELLS + p1].append(pair)
                if extra:
                    previous[span + pair].append(pair)
                st = span + pair
                mv = cget(p1 * CELLS + p0)
                if mv is None:
                    mv = graph[p1]
                remaining[st] = len(mv) + extra
                for d in mv:
                    previous[base0 + d].append(st)
                if extra:
                    previous[pair].append(st)
        popleft = pending.popleft
        processed = 0
        while pending:
            state = popleft()
            processed += 1
            if (processed & 1023) == 0 and time.perf_counter() > deadline:
                raise _EGTimeout
            rs = result[state]
            ds = distance[state]
            for parent in previous[state]:
                if result[parent]:
                    continue
                if rs == -1:
                    result[parent] = 1
                    distance[parent] = ds + 1
                    push(parent)
                else:
                    remaining[parent] -= 1
                    if ds > longest[parent]:
                        longest[parent] = ds
                    if remaining[parent] == 0:
                        result[parent] = -1
                        distance[parent] = longest[parent] + 1
                        push(parent)
        self.result, self.distance = result, distance

    def moves(self, own, enemy):
        mv = self.contact.get(own * CELLS + enemy)
        return self.graph[own] if mv is None else mv

    def probe(self, p0, p1, turn):
        s = turn * CELLS * CELLS + p0 * CELLS + p1
        return self.result[s], self.distance[s]

    def action(self, p0, p1, turn, turns_left, legal, visits):
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = None, None
        opts = list(self.moves(own, enemy))
        if ALLOW_PASS:
            opts.append(own)
        for dest in opts:
            if dest != own and dest not in legal:
                continue
            np0, np1 = (dest, p1) if turn == 0 else (p0, dest)
            outcome, dist = self.probe(np0, np1, 1 - turn)
            outcome, dist = -outcome, dist + 1
            if dist > turns_left:
                outcome = 0
            pace = -dist if outcome == 1 else dist if outcome == -1 else 0
            progress = -self.maps[turn][dest]
            pressure = 0.0
            if outcome == 0 and turns_left >= 2:
                replies = list(self.moves(enemy, dest))
                if ALLOW_PASS:
                    replies.append(enemy)
                chances = 0
                for reply in replies:
                    ap0, ap1 = (dest, reply) if turn == 0 else (reply, dest)
                    rr, rd = self.probe(ap0, ap1, turn)
                    chances += rr == 1 and rd <= turns_left - 2
                pressure = chances / max(1, len(replies))
            key = (
                outcome,
                pace,
                progress,
                pressure,
                -visits.get((dest, enemy), 0),
                dest != own,
            )
            if best_key is None or key > best_key:
                best, best_key = dest, key
        return best


_EG = None
_EG_VISITS = {}
_EG_LAST_TURN = -1


def _endgame_action(observation, board, legal_cells, started, max_turns):
    global _EG, _EG_VISITS, _EG_LAST_TURN
    pid = observation["player_id"]
    turn = observation["turn"]
    goal_row = GOAL_ROW[pid]
    for r, c in observation["legal_moves"]:
        if r == goal_row:
            return {"type": "move", "to": [int(r), int(c)]}
    turns_left = max_turns - turn
    if turns_left <= 0:
        return None
    key = tuple(board.can)
    try:
        if _EG is None or _EG.key != key:
            _EG = None
            _EG = _PawnEndgame(key, board.can, started + TABLEBASE_BUDGET)
    except _EGTimeout:
        return None
    p0, p1 = board.pos[0], board.pos[1]
    outcome, dist = _EG.probe(p0, p1, pid)
    if board.left[pid] != 0 and not (outcome == 1 and dist <= turns_left):
        return None
    if turn <= _EG_LAST_TURN:
        _EG_VISITS = {}
    _EG_LAST_TURN = turn
    own, enemy = board.pos[pid], board.pos[1 - pid]
    _EG_VISITS[(own, enemy)] = min(32, _EG_VISITS.get((own, enemy), 0) + 1)
    if len(_EG_VISITS) > 256:
        _EG_VISITS = {(own, enemy): _EG_VISITS[(own, enemy)]}
    dest = _EG.action(p0, p1, pid, turns_left, legal_cells, _EG_VISITS)
    if dest is None:
        return None
    if dest == own:
        return {"type": "pass"}
    return {"type": "move", "to": [int(dest // 9), int(dest % 9)]}


# =============================================================================
# 5. ĐỘNG CƠ TÌM KIẾM NEGAMAX + ALPHA-BETA + MULTI-PATH CHOKE WALLS
# =============================================================================

class _Timeout(Exception):
    pass


class Bot:
    """Động cơ tìm kiếm Negamax + Alpha-Beta + Lazy BF + Multi-Path Choke Wall Generator."""

    def __init__(self, max_depth=30, time_limit=TIME_BUDGET):
        self.max_depth = max_depth
        self.time_limit = time_limit
        self.tt = {}
        self.nodes = 0
        self.depth_reached = 0
        self.deadline = 0.0
        self.root_player = 0
        self.turns_left = 500
        self.wall_bonus = [0, 0]
        self.eval_context = None
        self.danger_cache = {}

    def _danger(self, b, F, victim):
        """Largest legal one-wall detours, cached by geometry and pawn positions."""
        key = (b.whash, b.pos[0], b.pos[1], victim)
        cached = self.danger_cache.get(key)
        if cached is not None:
            return cached
        other = 1 - victim
        own, enemy = b.pos[victim], b.pos[other]
        dm, do = F[victim].dist(own), F[other].dist(enemy)
        threats = []
        for index, wall in enumerate(self._wall_cands(b, F[victim], own, None)):
            if index % 8 == 0 and time.perf_counter() >= self.deadline:
                raise _Timeout
            b.put_wall(wall)
            try:
                fields = b.fields()
                ndm, ndo = fields[victim].dist(own), fields[other].dist(enemy)
                if ndm < INF and ndo < INF:
                    threats.append((ndm - dm - max(0, ndo - do), wall))
            finally:
                b.remove_wall(wall)
        threats.sort(reverse=True)
        result = tuple(threats[:3])
        if len(self.danger_cache) >= 16000:
            self.danger_cache.clear()
        self.danger_cache[key] = result
        return result

    def _contact_walls(self, b, cell):
        """Walls around a pawn can change jumps even off its shortest path."""
        return {w for direction in range(4) for w in BLOCKERS[cell][direction]
                if not b.forbid[w]}

    def _wall_cands(self, b, f_op, op_pos, limit, check=None):
        """Sinh danh sách mã tường (0..127) cắt mọi đường ngắn nhất của đối thủ,
        ưu tiên tường ngay trước mặt và các tường khóa nút cổ chai (chạm >= 2 điểm lưới)."""
        cached = f_op.paths.get(op_pos)
        if cached is not None:
            out = [w for w in cached if not b.forbid[w]
                   and (check is None or b.wall_ok(w, check))]
            return out if limit is None else out[:limit]
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF:
            return []
        layers = f_op.layers
        can = b.can
        forbid = b.forbid
        pts = b.pts
        seen = set()
        front_walls = []
        choke_walls = []
        conn_walls = []
        other_walls = []

        cur_mask = 1 << op_pos
        for step_idx in range(d):
            prev = layers[d - 1 - step_idx]
            nxt_mask = 0
            m = cur_mask
            while m:
                low = m & -m
                m ^= low
                u = low.bit_length() - 1
                for dir_idx in range(4):
                    if (can[dir_idx] >> u) & 1:
                        v = u + DIRS[dir_idx]
                        if (prev >> v) & 1:
                            nxt_mask |= 1 << v
                            for w in BLOCKERS[u][dir_idx]:
                                if not forbid[w] and w not in seen:
                                    seen.add(w)
                                    p0, p1, p2 = WALL_PTS[w]
                                    t_pts = (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0)
                                    if step_idx == 0:
                                        front_walls.append(w)
                                    elif t_pts >= 2:
                                        choke_walls.append(w)
                                    elif step_idx <= 2 or t_pts == 1:
                                        conn_walls.append(w)
                                    else:
                                        other_walls.append(w)
            cur_mask = nxt_mask
            if not cur_mask:
                break

        out = front_walls + choke_walls + conn_walls + other_walls
        f_op.paths[op_pos] = tuple(out)
        if check is not None:
            out = [w for w in out if b.wall_ok(w, check)]
        return out if limit is None else out[:limit]

    def _root_walls(self, b, F, root_ok):
        """Rank all attacking walls by real detour; include trap prevention."""
        t, o = b.turn, 1 - b.turn
        me, op = b.pos[t], b.pos[o]
        dm, do = F[t].dist(me), F[o].dist(op)
        attack = self._wall_cands(b, F[o], op, None)
        threats = self._danger(b, F, t) if b.left[o] else ()
        guards = set()
        for damage, w in threats[:3]:
            if damage >= 2:
                guards.update(CONFLICT[w])
        best_damage = [0, 0]
        best_damage[o] = threats[0][0] if threats else 0
        # A stationary pawn may seal a passage although wall-only BFS sees a
        # route. Blocking the square behind it can enable a diagonal jump.
        trapped = self._blocked(b, F, t, dm)
        escape = self._contact_walls(b, op) if trapped else set()
        # Keep a few connected preparation walls near the opponent as well.
        setup = set()
        if b.left[t] >= 2 and sum(b.left) <= 17:
            for w in range(128):
                if b.forbid[w] or w in attack:
                    continue
                orient, r, c = wall_info(w)
                if abs(r - op // 9) + abs(c - op % 9) > 3:
                    continue
                if any(b.pts[p] > 0 for p in WALL_PTS[w]):
                    setup.add(w)
        extras = escape | setup
        ranked = []
        useful_escape = set()
        preparation = []
        for w in dict.fromkeys(attack + sorted(guards | extras)):
            if b.forbid[w] or (root_ok is not None and not root_ok(WALL0 + w)):
                continue
            b.put_wall(w)
            try:
                fields = b.fields()
                ndm, ndo = fields[t].dist(me), fields[o].dist(op)
                if ndm >= INF or ndo >= INF:
                    continue
                damage = ndo - do - max(0, ndm - dm)
                best_damage[t] = max(best_damage[t], damage)
                guard = max((d for d, tw in threats[:3] if tw in CONFLICT[w]), default=0)
                score = 12 * (ndo - do) - 14 * (ndm - dm) + min(5, guard) * 3
                if w in escape and fields[t].blocked_distance(me, op) < INF:
                    useful_escape.add(w)
                if w in setup and ndm == dm and ndo == do:
                    followup = self._danger(b, fields, o)
                    potential = followup[0][0] if followup else 0
                    if potential >= 5:
                        preparation.append((potential, w))
                if w in attack or w in guards:
                    ranked.append((score, w))
            finally:
                b.remove_wall(w)
        # The next wall matters more on boards with a potentially long detour.
        self.wall_bonus = [min(24, max(0, damage - 2) * 4) for damage in best_damage]
        ranked.sort(reverse=True)
        selected = [w for _, w in ranked[:ROOT_WALLS]]
        selected.extend(w for _, w in ranked if w in guards and w not in selected)
        preparation.sort(reverse=True)
        for w in sorted(useful_escape) + [w for potential, w in preparation if potential >= max(5, best_damage[t] + 2)][:1]:
            if w not in selected:
                selected.append(w)
        return [WALL0 + w for w in selected]

    def _blocked(self, b, F, player, distance):
        own, enemy = b.pos[player], b.pos[1 - player]
        # A blocker must lie on a shortest route before it can seal every route.
        necessary = (abs(own // 9 - enemy // 9) + abs(own % 9 - enemy % 9)
                     + abs(enemy // 9 - GOAL_ROW[player]))
        return necessary <= distance and F[player].blocked_distance(own, enemy) >= INF

    def _eval(self, b, F):
        """Lượng giá trạng thái từ góc nhìn của người tới lượt `b.turn`."""
        t = b.turn
        o = 1 - t
        d_me = F[t].dist(b.pos[t])
        d_op = F[o].dist(b.pos[o])
        wm, wo = b.left[t], b.left[o]
        # At contact, plain wall-only distances miss both jumps and forced detours.
        if abs(b.pos[t] // 9 - b.pos[o] // 9) + abs(b.pos[t] % 9 - b.pos[o] % 9) == 1:
            d_me = min((1 + F[t].dist(m) for m in b.pawn_moves(t)), default=INF)
            # Only the player to move can take the current jump; the reply
            # sees the new pawn position, not a simultaneous opposite jump.
        # Race bonus khi đối thủ hết tường và mình đang dẫn
        s = W_DIST * (d_op - d_me) + WALL_VALUE[wm] - WALL_VALUE[wo] + TEMPO
        s += (self.wall_bonus[t] if wm else 0) - (self.wall_bonus[o] if wo else 0)
        if sum(b.left) <= 16:
            draw = -12 if t == self.root_player else 12
            if s > draw and self._blocked(b, F, t, F[t].dist(b.pos[t])):
                return draw
            if s < draw and self._blocked(b, F, o, F[o].dist(b.pos[o])):
                return draw
        if wo == 0 and d_me <= d_op:
            s += RACE_SCORE
        elif wm == 0 and d_op < d_me:
            s -= RACE_SCORE
        return s

    def _search(self, b, depth, alpha, beta, F, ply):
        self.nodes += 1
        if (self.nodes & 63) == 0 and time.perf_counter() > self.deadline:
            raise _Timeout

        t = b.turn
        o = 1 - t
        if b.pos[o] // 9 == GOAL_ROW[o]:
            return -(WIN - ply)
        if ply >= self.turns_left:
            return 0
        if F[t].dist(b.pos[t]) <= 2:
            if any(m // 9 == GOAL_ROW[t] for m in b.pawn_moves(t)):
                return WIN - ply - 1
        if depth <= 0:
            return self._eval(b, F)

        key = b.zkey()
        ent = self.tt.get(key)
        tt_move = None
        if ent is not None:
            e_depth, e_flag, e_val, tt_move = ent
            if e_depth >= depth:
                if e_val > WIN - 200:
                    e_val -= ply
                elif e_val < -WIN + 200:
                    e_val += ply
                if e_flag == 0:
                    return e_val
                if e_flag == 1 and e_val > alpha:
                    alpha = e_val
                elif e_flag == 2 and e_val < beta:
                    beta = e_val
                if alpha >= beta:
                    return e_val

        a0 = alpha
        best = -WIN * 2
        best_mv = None
        me = b.pos[t]
        f_me = F[t]

        # === Null-Move Pruning (NMP) ===
        # Chỉ khi: không ở gần đích, không tiếp xúc quân, thế đủ tốt và còn đủ sâu
        d_me = f_me.dist(me)
        d_op = F[o].dist(b.pos[o])
        ia_r, ia_c = divmod(me, 9)
        ib_r, ib_c = divmod(b.pos[o], 9)
        adj = abs(ia_r - ib_r) + abs(ia_c - ib_c) <= 2
        if (NMP_R and depth >= NMP_R + 2 and not adj and d_me > 2 and d_op > 2):
            # Static eval từ góc nhìn của ta
            static_eval = self._eval(b, F)
            margin = NMP_MARGIN if b.left[o] > 0 else 0
            if static_eval >= beta + margin and -WIN + 200 < beta < WIN - 200:
                # Thử PASS: đổi lượt không đi
                b.turn = o
                try:
                    val = -self._search(b, depth - 1 - NMP_R, -beta, -beta + 1, F, ply + 1)
                finally:
                    b.turn = t
                if val >= beta:
                    return beta if val > WIN - 200 else val

        # === Pawn moves ===
        pawns = b.pawn_moves(t)
        pawns.sort(key=f_me.dist)
        if tt_move is not None and tt_move < WALL0 and tt_move in pawns:
            pawns.remove(tt_move)
            pawns.insert(0, tt_move)

        first = True
        for mv in pawns:
            b.pos[t] = mv
            b.turn = o
            try:
                if first:
                    val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
                    first = False
                else:
                    val = -self._search(b, depth - 1, -alpha - 1, -alpha, F, ply + 1)
                    if alpha < val < beta:
                        val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
            finally:
                b.turn = t
                b.pos[t] = me
            if val > best:
                best = val
                best_mv = mv
                if val > alpha:
                    alpha = val
                    if alpha >= beta:
                        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                        return best

        # === Wall moves với Killer/History ordering + LMR ===
        if b.left[t] > 0:
            cands = self._wall_cands(b, F[o], b.pos[o], INNER_WALLS)

            # Killer heuristic: ưu tiên các tường đã gây cutoff ở ply này
            killers = _KILLERS[ply] if ply < 128 else [None, None]
            prioritized = []
            for km in killers:
                if km is not None and not b.forbid[km] and km in cands:
                    cands.remove(km)
                    prioritized = [km] + prioritized

            if tt_move is not None and WALL0 <= tt_move < PASS:
                tw = tt_move - WALL0
                if not b.forbid[tw]:
                    if tw in prioritized:
                        prioritized.remove(tw)
                    elif tw in cands:
                        cands.remove(tw)
                    prioritized.insert(0, tw)

            # Sắp xếp cands theo history heuristic
            cands.sort(key=lambda w: -_HISTORY[w])
            ordered_walls = prioritized + cands

            pts = b.pts
            p0_pos, p1_pos = b.pos[0], b.pos[1]
            wall_idx = 0
            for w in ordered_walls:
                if b.forbid[w]:
                    continue
                wp0, wp1, wp2 = WALL_PTS[w]
                need_conn_check = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2

                b.put_wall(w)
                F2 = b.fields()
                if need_conn_check and (
                    F2[0].dist(p0_pos) >= INF or F2[1].dist(p1_pos) >= INF
                ):
                    b.remove_wall(w)
                    continue

                b.left[t] -= 1
                b.turn = o

                # LMR: giảm độ sâu cho tường muộn (không phải killer/TT move)
                use_lmr = (depth >= LMR_MIN_DEPTH and wall_idx >= LMR_WALL_MIN
                           and w not in prioritized)
                try:
                    if first:
                        val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                        first = False
                    elif use_lmr:
                        # Thử tìm kiếm với độ sâu giảm
                        val = -self._search(b, depth - 2, -alpha - 1, -alpha, F2, ply + 1)
                        if val > alpha:
                            # Re-search với full depth null-window
                            val = -self._search(b, depth - 1, -alpha - 1, -alpha, F2, ply + 1)
                            if alpha < val < beta:
                                val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    else:
                        val = -self._search(b, depth - 1, -alpha - 1, -alpha, F2, ply + 1)
                        if alpha < val < beta:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                finally:
                    b.turn = t
                    b.left[t] += 1
                    b.remove_wall(w)

                wall_idx += 1
                if val > best:
                    best = val
                    best_mv = WALL0 + w
                    if val > alpha:
                        alpha = val
                        if alpha >= beta:
                            # History heuristic update
                            _HISTORY[w] += depth * depth
                            if _HISTORY[w] > 32767:
                                for i in range(128):
                                    _HISTORY[i] >>= 1
                            # Killer update
                            if ply < 128:
                                if _KILLERS[ply][0] != w:
                                    _KILLERS[ply][1] = _KILLERS[ply][0]
                                    _KILLERS[ply][0] = w
                            self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                            return best

        # Opponents may wait at a choke point. Model that defence explicitly;
        # our own waiting is limited to positions where it holds a blockade.
        wait = (sum(b.left) <= 16 and
                ((t != self.root_player and adj) or self._blocked(b, F, o, d_op)))
        if wait:
            b.turn = o
            try:
                val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
            finally:
                b.turn = t
            if val > best:
                best, best_mv = val, PASS
        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
        return best

    def _store_tt(self, key, depth, best, a0, beta, best_mv, ply):
        if len(self.tt) > TT_SIZE:
            self.tt.clear()
        stored = best
        if stored > WIN - 200:
            stored += ply
        elif stored < -WIN + 200:
            stored -= ply
        flag = 2 if best <= a0 else (1 if best >= beta else 0)
        old = self.tt.get(key)
        if old is None or old[0] <= depth:
            self.tt[key] = (depth, flag, stored, best_mv)

    def choose(self, board, root_ok=None, repeat=None):
        """Iterative deepening; only completed iterations replace the safe move."""
        b = board.copy()
        t, o = b.turn, 1 - b.turn
        self.root_player = t
        self.nodes = self.depth_reached = 0
        self.deadline = time.perf_counter() + self.time_limit
        # Root-dependent threat values and finite remaining turn horizon.
        for i in range(128):
            _HISTORY[i] >>= 1
            _KILLERS[i][:] = [None, None]
        repeat = repeat or {}
        F = b.fields()
        me, op = b.pos[t], b.pos[o]
        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        for mv in pawns:
            if mv // 9 == GOAL_ROW[t]:
                return mv
        pawns.sort(key=lambda m: (F[t].dist(m), repeat.get(m, 0), abs(m % 9 - 4)))
        self.wall_bonus = [0, 0]
        try:
            walls = self._root_walls(b, F, root_ok) if b.left[t] else []
        except _Timeout:
            return pawns[0] if pawns else None
        context = (t, tuple(self.wall_bonus), min(self.turns_left, self.max_depth + 4))
        if context != self.eval_context:
            self.tt.clear()
            self.eval_context = context
        actions = pawns + walls
        if self._blocked(b, F, o, F[o].dist(op)):
            actions.append(PASS)
        if not actions:
            return None
        best_action = actions[0]
        prev_score = 0
        try:
            for depth in range(1, self.max_depth + 1):
                delta = ASP_DELTA
                lo, hi = (-WIN * 2, WIN * 2)
                if depth >= 3 and abs(prev_score) < WIN - 200:
                    lo, hi = prev_score - delta, prev_score + delta
                while True:
                    alpha, beta = lo, hi
                    cur_best, cur_score = None, -WIN * 2
                    scores = {}
                    for index, mv in enumerate(actions):
                        if time.perf_counter() >= self.deadline:
                            raise _Timeout
                        pen = 0
                        if mv < WALL0:
                            pen = min(3, repeat.get(mv, 0)) * REPEAT_PENALTY
                            pen -= 0.25 * (F[t].dist(me) - F[t].dist(mv))
                        child_fields = F
                        if mv < WALL0:
                            b.pos[t] = mv
                        elif mv != PASS:
                            b.put_wall(mv - WALL0)
                            b.left[t] -= 1
                            child_fields = b.fields()
                        b.turn = o
                        try:
                            if index == 0:
                                val = -self._search(b, depth - 1, -beta - pen, -alpha - pen,
                                                    child_fields, 1) - pen
                            else:
                                val = -self._search(b, depth - 1, -alpha - pen - 1, -alpha - pen,
                                                    child_fields, 1) - pen
                                if alpha < val < beta:
                                    val = -self._search(b, depth - 1, -beta - pen, -alpha - pen,
                                                        child_fields, 1) - pen
                        finally:
                            b.turn = t
                            b.pos[t] = me
                            if WALL0 <= mv < PASS:
                                b.left[t] += 1
                                b.remove_wall(mv - WALL0)
                        scores[mv] = val
                        if val > cur_score:
                            cur_best, cur_score = mv, val
                        alpha = max(alpha, val)
                        if alpha >= beta:
                            break
                    if cur_score <= lo:
                        delta *= 3
                        lo = max(-WIN * 2, cur_score - delta)
                    elif cur_score >= hi:
                        delta *= 3
                        hi = min(WIN * 2, cur_score + delta)
                    else:
                        break
                    actions.sort(key=lambda m: (m != cur_best, -scores.get(m, -WIN * 2)))
                best_action, prev_score = cur_best, cur_score
                self.depth_reached = depth
                self.last_score = cur_score
                actions.sort(key=lambda m: (m != best_action, -scores.get(m, -WIN * 2)))
                if abs(cur_score) >= WIN - 200:
                    break
        except _Timeout:
            pass
        return best_action


# =============================================================================
# 6. GIAO TIẾP ARENA, KHAI CUỘC KIỂM SOÁT XÍCH ĐẠO & BẢO VỆ AN TOÀN
# =============================================================================

_BOT = None
_VISITS = {}
_LAST_TURN = {}


def board_from_observation(obs):
    pid = obs["player_id"]
    b = Board()
    b.pos[pid] = obs["you"]["position"][0] * 9 + obs["you"]["position"][1]
    b.pos[1 - pid] = obs["opponent"]["position"][0] * 9 + obs["opponent"]["position"][1]
    b.left[pid] = obs["you"]["walls_remaining"]
    b.left[1 - pid] = obs["opponent"]["walls_remaining"]

    for r, c in obs["walls"]["horizontal"]:
        b.put_wall(wall_id("h", r, c))
    for r, c in obs["walls"]["vertical"]:
        b.put_wall(wall_id("v", r, c))

    b.turn = pid
    return b


def move_to_action(mv):
    if mv < WALL0:
        return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = wall_info(mv - WALL0)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}


def _fallback(observation):
    """Dự phòng khẩn cấp: chọn bước hợp lệ gần đích nhất (ưu tiên gần cột trung tâm)."""
    try:
        moves = observation["legal_moves"]
        if not moves:
            return {"type": "pass"}
        distances = observation["distances"]
        best = min(
            moves,
            key=lambda m: (
                distances[m[0]][m[1]] if distances[m[0]][m[1]] >= 0 else 10 ** 6
            )
            * 10
            + abs(m[1] - 4),
        )
        return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception:
        return {"type": "pass"}


def _choose(observation):
    global _BOT
    started = time.perf_counter()
    pid = observation["player_id"]
    turn = observation["turn"]

    # Manual GC trigger mỗi 25 lượt để tránh GC pause trong search
    _GC_COUNTER[0] += 1
    if _GC_COUNTER[0] >= 25:
        _GC_COUNTER[0] = 0
        gc.collect()

    if turn <= 2 or turn < _LAST_TURN.get(pid, 0):
        for k in [k for k in _VISITS if k[0] == pid]:
            del _VISITS[k]
    _LAST_TURN[pid] = turn

    legal = set()
    for r, c in observation["legal_moves"]:
        legal.add(r * 9 + c)
    for w in observation["legal_walls"]:
        legal.add(WALL0 + wall_id(w["orientation"].lower(), w["row"], w["col"]))
    if not legal:
        return {"type": "pass"}

    board = board_from_observation(observation)

    hw_list = observation["walls"]["horizontal"]
    vw_list = observation["walls"]["vertical"]

    # Answer an opening waiting fence before it grows into a one-sided barrier.
    if (pid == 0 and board.pos == [49, 31] and board.left == [10, 9]
            and not vw_list and len(hw_list) == 1):
        r, c = hw_list[0]
        if r == 2 and c in (3, 4):
            reply = WALL0 + wall_id("h", 5, c)
            if reply in legal:
                return move_to_action(reply)

    if _BOT is None:
        _BOT = Bot()

    # Tàn cuộc chính xác tuyệt đối khi đối thủ còn 0 tường
    if ENDGAME_ON and observation["opponent"]["walls_remaining"] == 0:
        act = _endgame_action(
            observation, board, legal, started, observation.get("max_turns", 500)
        )
        if act is not None and (act["type"] != "pass" or ALLOW_PASS):
            return act

    nw = len(hw_list) + len(vw_list)
    opp_cell = board.pos[1 - pid]
    repeat = {
        m: _VISITS.get((pid, m, opp_cell, nw), 0)
        for m in legal
        if m < WALL0 and (pid, m, opp_cell, nw) in _VISITS
    }

    _BOT.turns_left = max(1, observation.get("max_turns", 500) - turn)
    _BOT.time_limit = max(0.01, TIME_BUDGET - (time.perf_counter() - started))
    mv = _BOT.choose(board, root_ok=lambda m: m in legal, repeat=repeat)
    if mv == PASS:
        return {"type": "pass"}
    if mv is None or mv not in legal:
        return _fallback(observation)

    if mv < WALL0:
        vkey = (pid, mv, opp_cell, nw)
        _VISITS[vkey] = _VISITS.get(vkey, 0) + 1
    return move_to_action(mv)


def _canonical_observation(observation):
    """Rotate the second player's board so ordering is symmetric between sides."""
    obs = dict(observation)
    obs["player_id"] = obs["current_player"] = 0
    for name, goal in (("you", 0), ("opponent", 8)):
        obs[name] = dict(observation[name])
        r, c = observation[name]["position"]
        obs[name]["position"] = [8 - r, 8 - c]
        obs[name]["goal_row"] = goal
    obs["walls"] = {name: [[7 - r, 7 - c] for r, c in walls]
                    for name, walls in observation["walls"].items()}
    obs["legal_moves"] = [[8 - r, 8 - c] for r, c in observation["legal_moves"]]
    obs["legal_walls"] = [dict(orientation=w["orientation"], row=7 - w["row"], col=7 - w["col"])
                          for w in observation["legal_walls"]]
    obs["distances"] = [list(reversed(row)) for row in reversed(observation["distances"])]
    return obs


def choose_action(observation):
    """Arena entry point, using the same orientation for both players."""
    try:
        if observation["player_id"] == 0:
            return _choose(observation)
        action = _choose(_canonical_observation(observation))
        if action["type"] == "move":
            r, c = action["to"]
            return {"type": "move", "to": [8 - r, 8 - c]}
        if action["type"] == "wall":
            return dict(type="wall", orientation=action["orientation"],
                        row=7 - action["row"], col=7 - action["col"])
        return action
    except Exception:
        return _fallback(observation)
