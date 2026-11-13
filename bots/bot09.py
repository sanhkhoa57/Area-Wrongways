"""Wrongway Arena Student Agent.

A high-performance independent bot combining Bitboard-accelerated Negamax 
with Alpha-Beta pruning, a 2-Pawn Retrograde Endgame Tablebase, and Dynamic 
Evaluation. It efficiently scales search depth while minimizing timeout risks 
within the strict 1.0s server limit.
"""

from collections import deque
import random
import time

# =============================================================================
# 1. CORE CONFIGURATION
# =============================================================================

TIME_LIMIT = 0.65            # Giới hạn 0.65s để giảm rủi ro quá giờ
ROOT_WALL_LIMIT = 18         # Số lượng tường ứng viên tối đa ở gốc
INNER_WALL_LIMIT = 12        # Số lượng tường ứng viên tối đa ở các nút trong cây
TT_MAX_SIZE = 250_000        # Kích thước tối đa của Transposition Table

WEIGHT_DIST = 10             # Trọng số cơ sở cho khoảng cách
WEIGHT_WALLS = 22            # Trọng số cơ sở cho lợi thế tường
WALL_EXPONENT = 0.8          # Đường cong hiệu quả của tường (giảm dần)
TEMPO_BONUS = 2              # Lợi thế người đi trước (initiative)
REPEAT_PENALTY = 25          # Phạt điểm khi lặp lại thế cờ cũ

ENDGAME_ENABLED = True       # Bật bảng tàn cuộc (khi địch hết tường)
TABLEBASE_BUDGET = 0.25      # Thời gian tối đa để giải bảng tàn cuộc

CELLS = 81
INF = 99
WIN_SCORE = 10_000
WALL_OFFSET = 81             # Nước đi >= 81 là đặt tường

DIRS = (-9, 1, 9, -1)        # 0: Lên, 1: Phải, 2: Xuống, 3: Trái
GOAL_MASKS = (
    sum(1 << c for c in range(0, 9)),    # Đích của P0 (Hàng 0)
    sum(1 << c for c in range(72, 81)),  # Đích của P1 (Hàng 8)
)
GOAL_ROWS = (0, 8)

_WALL_POW = tuple(i ** WALL_EXPONENT for i in range(16))

# Khởi tạo Zobrist Hashing (Cố định seed để đảm bảo hash đồng nhất)
_rng = random.Random(20261030)
_Z_POS = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
_Z_WALL = [_rng.getrandbits(64) for _ in range(128)]
_Z_LEFT = [_rng.getrandbits(64) for _ in range(128)]
_Z_TURN = _rng.getrandbits(64)


# =============================================================================
# 2. GEOMETRY PRECOMPUTATION
# =============================================================================

def _decode_wall(w):
    """Chuyển mã tường (0..127) về (hướng, dòng, cột)."""
    return ('h' if w < 64 else 'v', (w & 63) >> 3, w & 7)

def encode_wall(orient, r, c):
    """Chuyển (hướng, dòng, cột) thành mã tường (0..127)."""
    return (0 if orient == 'h' else 64) + r * 8 + c

WALL_EDGES, WALL_PTS, CONFLICTS = [], [], []
for _w in range(128):
    _o, _r, _c = _decode_wall(_w)
    if _o == 'h':
        _e = [(_r * 9 + _c, 2), (_r * 9 + _c + 1, 2),
              ((_r + 1) * 9 + _c, 0), ((_r + 1) * 9 + _c + 1, 0)]
        _p = [(_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2]
        _cf = [encode_wall('h', _r, _c), encode_wall('v', _r, _c)]
        if _c > 0:
            _cf.append(encode_wall('h', _r, _c - 1))
        if _c < 7:
            _cf.append(encode_wall('h', _r, _c + 1))
    else:
        _e = [(_r * 9 + _c, 1), ((_r + 1) * 9 + _c, 1),
              (_r * 9 + _c + 1, 3), ((_r + 1) * 9 + _c + 1, 3)]
        _p = [_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1]
        _cf = [encode_wall('v', _r, _c), encode_wall('h', _r, _c)]
        if _r > 0:
            _cf.append(encode_wall('v', _r - 1, _c))
        if _r < 7:
            _cf.append(encode_wall('v', _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_PTS.append(tuple(_p))
    CONFLICTS.append(tuple(_cf))

BLOCKERS = [[() for _ in range(4)] for _ in range(CELLS)]
for _cell in range(CELLS):
    for _d in range(4):
        _lst = []
        for _w in range(128):
            if (_cell, _d) in WALL_EDGES[_w]:
                _lst.append(_w)
        BLOCKERS[_cell][_d] = tuple(_lst)

def _default_connectivity():
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


# =============================================================================
# 3. BITBOARD BFS & STATE REPRESENTATION
# =============================================================================

class BitboardBFS:
    """Tính khoảng cách BFS lười bằng bitwise shift cực nhanh."""
    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can

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
            f = (((f << 9) & c0) | ((f >> 1) & c1) |
                 ((f >> 9) & c2) | ((f << 1) & c3)) & ~seen
            if not f:
                return INF
            seen |= f
            self.seen = seen
            layers.append(f)
            if f & bit:
                return k
            k += 1


class BoardState:
    """Trạng thái bàn cờ tối ưu hóa cho Undo/Redo."""
    def __init__(self):
        self.pos = [76, 4]
        self.left = [10, 10]
        self.can = _default_connectivity()
        self.forbid = [0] * 128
        self.pts = [0] * 100
        for r in range(10):
            for c in range(10):
                if r in (0, 9) or c in (0, 9):
                    self.pts[r * 10 + c] = 1
        self.turn = 0
        self.whash = 0

    def copy(self):
        b = BoardState.__new__(BoardState)
        b.pos = self.pos[:]
        b.left = self.left[:]
        b.can = self.can[:]
        b.forbid = self.forbid[:]
        b.pts = self.pts[:]
        b.turn = self.turn
        b.whash = self.whash
        return b

    def create_bfs(self):
        return (BitboardBFS(GOAL_MASKS[0], self.can), BitboardBFS(GOAL_MASKS[1], self.can))

    def fast_dist(self, start, goal):
        cur = 1 << start
        if cur & goal:
            return 0
        seen = cur
        c0, c1, c2, c3 = self.can
        d = 0
        while cur:
            d += 1
            nxt = (((cur & c0) >> 9) | ((cur & c1) << 1) |
                   ((cur & c2) << 9) | ((cur & c3) >> 1)) & ~seen
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

    def _toggle_edges(self, w, open_edges):
        can = self.can
        if open_edges:
            for cell, d in WALL_EDGES[w]:
                can[d] |= 1 << cell
        else:
            for cell, d in WALL_EDGES[w]:
                can[d] &= ~(1 << cell)

    def put_wall(self, w):
        self._toggle_edges(w, False)
        for x in CONFLICTS[w]:
            self.forbid[x] += 1
        for p in WALL_PTS[w]:
            self.pts[p] += 1
        self.whash ^= _Z_WALL[w]

    def remove_wall(self, w):
        self._toggle_edges(w, True)
        for x in CONFLICTS[w]:
            self.forbid[x] -= 1
        for p in WALL_PTS[w]:
            self.pts[p] -= 1
        self.whash ^= _Z_WALL[w]

    def is_wall_legal(self, w, player=None):
        t = self.turn if player is None else player
        if self.left[t] <= 0 or self.forbid[w]:
            return False
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        if (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0) >= 2:
            self._toggle_edges(w, False)
            ok = (self.fast_dist(self.pos[0], GOAL_MASKS[0]) < INF and
                  self.fast_dist(self.pos[1], GOAL_MASKS[1]) < INF)
            self._toggle_edges(w, True)
            return ok
        return True

    def zobrist_key(self):
        return (self.whash ^ _Z_POS[0][self.pos[0]] ^ _Z_POS[1][self.pos[1]] ^
                _Z_LEFT[self.left[0] * 11 + self.left[1]] ^
                (_Z_TURN if self.turn else 0))


# =============================================================================
# 4. RETROGRADE ENDGAME TABLEBASE
# =============================================================================

class SearchTimeout(Exception):
    """Lỗi nội bộ ngắt tìm kiếm khi hết ngân sách thời gian."""
    pass

def _calc_reachable(can, me, op):
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

class RetrogradeTablebase:
    """Giải ngược tàn cuộc hoàn hảo khi đối thủ hết tường."""
    __slots__ = ("key", "graph", "contact", "maps", "result", "distance")

    def __init__(self, key, can, deadline):
        self.key = key
        graph = [tuple(i + DIRS[d] for d in range(4) if (can[d] >> i) & 1) for i in range(CELLS)]
        contact = {}
        for i in range(CELLS):
            for j in graph[i]:
                contact[i * CELLS + j] = tuple(_calc_reachable(can, i, j))
        self.graph, self.contact = graph, contact

        maps = []
        for g in (0, 1):
            bf = BitboardBFS(GOAL_MASKS[g], can)
            maps.append([bf.dist(i) for i in range(CELLS)])
        self.maps = maps

        span = CELLS * CELLS
        result = [0] * (2 * span)
        distance = [0] * (2 * span)
        remaining = [0] * (2 * span)
        longest = [0] * (2 * span)
        previous = [[] for _ in range(2 * span)]
        pending = deque()
        push = pending.append
        cget = contact.get

        for p0 in range(CELLS):
            if (p0 & 7) == 0 and time.perf_counter() > deadline:
                raise SearchTimeout
            if maps[0][p0] >= INF:
                continue
            won0 = p0 // 9 == GOAL_ROWS[0]
            base0 = p0 * CELLS
            for p1 in range(CELLS):
                if p1 == p0 or maps[1][p1] >= INF:
                    continue
                pair = base0 + p1
                if won0 or p1 // 9 == GOAL_ROWS[1]:
                    w0 = 1 if won0 else -1
                    result[pair] = w0
                    result[span + pair] = -w0
                    push(pair)
                    push(span + pair)
                    continue

                mv = cget(pair)
                if mv is None:
                    mv = tuple(x for x in graph[p0] if x != p1)
                remaining[pair] = len(mv)
                for d in mv:
                    previous[span + d * CELLS + p1].append(pair)

                st = span + pair
                mv = cget(p1 * CELLS + p0)
                if mv is None:
                    mv = tuple(x for x in graph[p1] if x != p0)
                remaining[st] = len(mv)
                for d in mv:
                    previous[base0 + d].append(st)

        popleft = pending.popleft
        processed = 0
        while pending:
            state = popleft()
            processed += 1
            if (processed & 1023) == 0 and time.perf_counter() > deadline:
                raise SearchTimeout
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

    def probe(self, p0, p1, turn):
        s = turn * CELLS * CELLS + p0 * CELLS + p1
        return self.result[s], self.distance[s]

    def select_action(self, p0, p1, turn, turns_left, legal, visits):
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = None, None
        mv = self.contact.get(own * CELLS + enemy)
        
        opts = list(self.graph[own] if mv is None else mv)
        opts = [x for x in opts if x != enemy]
        
        # Chỉ cho phép pass khi không còn nước đi nào khác
        if not opts:
            opts = [own]

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

            key = (outcome, pace, progress, -visits.get((dest, enemy), 0))
            if best_key is None or key > best_key:
                best, best_key = dest, key
        return best


# =============================================================================
# 5. NEGAMAX SEARCH ENGINE
# =============================================================================

class SearchBot:
    """Động cơ Negamax với Alpha-Beta Pruning và Transposition Table."""
    def __init__(self, time_limit=TIME_LIMIT):
        self.time_limit = time_limit
        self.tt = {}
        self.nodes = 0
        self.deadline = 0.0

    def _get_wall_candidates(self, board, f_op, op_pos, limit, check_player=None):
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF:
            return []
        layers = f_op.layers
        can, forbid = board.can, board.forbid
        out, seen = [], set()

        u, cur_d = op_pos, d
        while cur_d > 0:
            prev = layers[cur_d - 1]
            nxt = -1
            for dir_idx in range(4):
                if (can[dir_idx] >> u) & 1:
                    v = u + DIRS[dir_idx]
                    if (prev >> v) & 1:
                        for w in BLOCKERS[u][dir_idx]:
                            if not forbid[w] and w not in seen:
                                seen.add(w)
                                if check_player is None or board.is_wall_legal(w, check_player):
                                    out.append(w)
                        if nxt < 0:
                            nxt = v
            if nxt < 0 or len(out) >= limit:
                break
            u = nxt
            cur_d -= 1
        return out[:limit]

    def _evaluate(self, board, bfs_pair):
        """Hàm đánh giá linh hoạt (Dynamic Evaluation)."""
        t = board.turn
        o = 1 - t
        d_me = bfs_pair[t].dist(board.pos[t])
        d_op = bfs_pair[o].dist(board.pos[o])

        if board.left[o] == 0 and d_me <= d_op:
            return WIN_SCORE // 2 - d_me
        if board.left[t] == 0 and d_op < d_me:
            return -(WIN_SCORE // 2 - d_op)

        # Tính toán trọng số tường phụ thuộc vào tiến độ ván cờ
        game_phase = min(1.0, (d_me + d_op) / 16.0)
        dynamic_weight_walls = WEIGHT_WALLS * (0.6 + 0.6 * game_phase)

        wall_diff = _WALL_POW[board.left[t]] - _WALL_POW[board.left[o]]
        
        # Nếu đang bị dẫn trước, thưởng thêm cho việc giữ tường phòng thủ
        if d_me > d_op and board.left[t] > board.left[o]:
            dynamic_weight_walls += 2.0

        return WEIGHT_DIST * (d_op - d_me) + dynamic_weight_walls * wall_diff + TEMPO_BONUS

    def _negamax(self, board, depth, alpha, beta, bfs_pair, ply):
        self.nodes += 1
        if (self.nodes & 63) == 0 and time.perf_counter() > self.deadline:
            raise SearchTimeout

        t = board.turn
        o = 1 - t
        if board.pos[o] // 9 == GOAL_ROWS[o]:
            return -(WIN_SCORE - ply)
        if depth <= 0:
            return self._evaluate(board, bfs_pair)

        key = board.zobrist_key()
        entry = self.tt.get(key)
        tt_move = None
        if entry is not None:
            e_depth, e_flag, e_val, tt_move = entry
            if e_depth >= depth:
                if e_val > WIN_SCORE - 200:
                    e_val -= ply
                elif e_val < -WIN_SCORE + 200:
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
        best = -WIN_SCORE * 2
        best_mv = None
        me = board.pos[t]

        pawns = board.pawn_moves(t)
        pawns.sort(key=bfs_pair[t].dist)
        if tt_move is not None and tt_move < WALL_OFFSET and tt_move in pawns:
            pawns.remove(tt_move)
            pawns.insert(0, tt_move)

        for mv in pawns:
            board.pos[t] = mv
            board.turn = o
            val = -self._negamax(board, depth - 1, -beta, -alpha, bfs_pair, ply + 1)
            board.turn = t
            board.pos[t] = me
            if val > best:
                best, best_mv = val, mv
                if val > alpha:
                    alpha = val
                    if alpha >= beta:
                        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                        return best

        if board.left[t] > 0:
            cands = self._get_wall_candidates(board, bfs_pair[o], board.pos[o], INNER_WALL_LIMIT)
            if tt_move is not None and tt_move >= WALL_OFFSET:
                tw = tt_move - WALL_OFFSET
                if not board.forbid[tw]:
                    if tw in cands:
                        cands.remove(tw)
                    cands.insert(0, tw)

            pts = board.pts
            p0, p1 = board.pos[0], board.pos[1]
            for w in cands:
                wp0, wp1, wp2 = WALL_PTS[w]
                check_conn = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2

                board.put_wall(w)
                bfs_next = board.create_bfs()
                if check_conn and (bfs_next[0].dist(p0) >= INF or bfs_next[1].dist(p1) >= INF):
                    board.remove_wall(w)
                    continue

                board.left[t] -= 1
                board.turn = o
                val = -self._negamax(board, depth - 1, -beta, -alpha, bfs_next, ply + 1)
                board.turn = t
                board.left[t] += 1
                board.remove_wall(w)

                if val > best:
                    best, best_mv = val, WALL_OFFSET + w
                    if val > alpha:
                        alpha = val
                        if alpha >= beta:
                            self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                            return best

        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
        return best

    def _store_tt(self, key, depth, best, a0, beta, best_mv, ply):
        if len(self.tt) > TT_MAX_SIZE:
            self.tt.clear()
        stored = best
        if stored > WIN_SCORE - 200:
            stored += ply
        elif stored < -WIN_SCORE + 200:
            stored -= ply
        flag = 2 if best <= a0 else (1 if best >= beta else 0)
        old = self.tt.get(key)
        if old is None or old[0] <= depth:
            self.tt[key] = (depth, flag, stored, best_mv)

    def search(self, board, root_ok=None, repeat=None):
        b = board.copy()
        t = b.turn
        o = 1 - t
        repeat = repeat or {}
        self.nodes = 0
        self.deadline = time.perf_counter() + self.time_limit

        for mv in b.pawn_moves(t):
            if mv // 9 == GOAL_ROWS[t] and (root_ok is None or root_ok(mv)):
                return mv

        bfs_pair = b.create_bfs()
        me, op = b.pos[t], b.pos[o]

        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        pawns.sort(key=lambda m: (bfs_pair[t].dist(m), repeat.get(m, 0)))

        if b.left[t] == 0:
            fwd = [m for m in pawns if bfs_pair[t].dist(m) < bfs_pair[t].dist(me)]
            if fwd:
                pawns = fwd

        walls = []
        if b.left[t] > 0:
            for w in self._get_wall_candidates(b, bfs_pair[o], op, ROOT_WALL_LIMIT, check_player=t):
                mv = WALL_OFFSET + w
                if root_ok is None or root_ok(mv):
                    walls.append(mv)

        actions = pawns + walls
        if not actions:
            return None
        if len(actions) == 1:
            return actions[0]

        best_action = actions[0]
        try:
            for depth in range(1, 30):
                alpha, beta = -WIN_SCORE * 2, WIN_SCORE * 2
                cur_best, cur_score = None, -WIN_SCORE * 2
                scores = {}

                for mv in actions:
                    if mv < WALL_OFFSET:
                        pen = REPEAT_PENALTY * repeat.get(mv, 0)
                        b.pos[t] = mv
                        b.turn = o
                        val = -self._negamax(b, depth - 1, -beta, -(alpha + pen), bfs_pair, 1) - pen
                        b.turn = t
                        b.pos[t] = me
                    else:
                        w = mv - WALL_OFFSET
                        b.put_wall(w)
                        bfs_next = b.create_bfs()
                        if bfs_next[0].dist(b.pos[0]) >= INF or bfs_next[1].dist(b.pos[1]) >= INF:
                            b.remove_wall(w)
                            continue
                        b.left[t] -= 1
                        b.turn = o
                        try:
                            val = -self._negamax(b, depth - 1, -beta, -alpha, bfs_next, 1)
                        finally:
                            b.turn = t
                            b.left[t] += 1
                            b.remove_wall(w)

                    scores[mv] = val
                    if val > cur_score:
                        cur_score, cur_best = val, mv
                        if val > alpha:
                            alpha = val

                if cur_best is not None:
                    best_action = cur_best
                    actions.sort(key=lambda a: -scores.get(a, -WIN_SCORE * 2))

                if abs(cur_score) >= WIN_SCORE - 200:
                    break
        except SearchTimeout:
            pass

        return best_action


# =============================================================================
# 6. ARENA INTERFACE & EXECUTION
# =============================================================================

_SEARCH_BOT = None
_ENDGAME_TB = None
_POSITION_VISITS = {}
_LAST_TURN_RECORD = {}


def parse_arena_board(obs):
    pid = obs["player_id"]
    b = BoardState()
    b.pos[pid] = obs["you"]["position"][0] * 9 + obs["you"]["position"][1]
    b.pos[1 - pid] = obs["opponent"]["position"][0] * 9 + obs["opponent"]["position"][1]
    b.left[pid] = obs["you"]["walls_remaining"]
    b.left[1 - pid] = obs["opponent"]["walls_remaining"]

    for r, c in obs["walls"]["horizontal"]:
        b.put_wall(encode_wall('h', r, c))
    for r, c in obs["walls"]["vertical"]:
        b.put_wall(encode_wall('v', r, c))

    b.turn = pid
    return b


def format_action(mv):
    if mv < WALL_OFFSET:
        return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = _decode_wall(mv - WALL_OFFSET)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}


def fallback_action(obs):
    try:
        moves = obs["legal_moves"]
        if not moves:
            return {"type": "pass"}
        distances = obs["distances"]
        best = min(
            moves,
            key=lambda m: (distances[m[0]][m[1]] if distances[m[0]][m[1]] >= 0 else 10 ** 6) * 10 + abs(m[1] - 4)
        )
        return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception:
        return {"type": "pass"}


def _handle_endgame(obs, board, legal_cells, started):
    global _ENDGAME_TB
    pid = obs["player_id"]
    turns_left = obs.get("max_turns", 500) - obs["turn"]
    if turns_left <= 0:
        return None

    key = tuple(board.can)
    try:
        if _ENDGAME_TB is None or _ENDGAME_TB.key != key:
            _ENDGAME_TB = None
            _ENDGAME_TB = RetrogradeTablebase(key, board.can, started + TABLEBASE_BUDGET)
    except SearchTimeout:
        return None

    p0, p1 = board.pos[0], board.pos[1]
    outcome, dist = _ENDGAME_TB.probe(p0, p1, pid)
    if board.left[pid] != 0 and not (outcome == 1 and dist <= turns_left):
        return None

    dest = _ENDGAME_TB.select_action(p0, p1, pid, turns_left, legal_cells, {})
    if dest is None:
        return None
        
    own = board.pos[pid]
    if dest == own:
        return {"type": "pass"}
    return {"type": "move", "to": [int(dest // 9), int(dest % 9)]}


def choose_action(observation):
    """Hàm Main Entrypoint được Arena gọi mỗi lượt."""
    global _SEARCH_BOT, _POSITION_VISITS, _LAST_TURN_RECORD
    started = time.perf_counter()

    try:
        pid = observation["player_id"]
        turn = observation["turn"]

        # Reset bộ đếm lặp lại khi sang ván mới
        if turn <= 2 or turn < _LAST_TURN_RECORD.get(pid, 0):
            for k in [k for k in _POSITION_VISITS if k[0] == pid]:
                del _POSITION_VISITS[k]
        _LAST_TURN_RECORD[pid] = turn

        legal = set()
        for r, c in observation["legal_moves"]:
            legal.add(r * 9 + c)
        for w in observation["legal_walls"]:
            legal.add(WALL_OFFSET + encode_wall(w["orientation"].lower(), w["row"], w["col"]))

        if not legal:
            return {"type": "pass"}

        board = parse_arena_board(observation)
        if _SEARCH_BOT is None:
            _SEARCH_BOT = SearchBot()

        # Gọi bảng tàn cuộc nếu đối thủ hết tường
        if ENDGAME_ENABLED and observation["opponent"]["walls_remaining"] == 0:
            endgame_act = _handle_endgame(observation, board, legal, started)
            if endgame_act is not None:
                return endgame_act

        num_walls = len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"])
        opp_cell = board.pos[1 - pid]
        repeat = {
            m: _POSITION_VISITS.get((pid, m, opp_cell, num_walls), 0)
            for m in legal if m < WALL_OFFSET and (pid, m, opp_cell, num_walls) in _POSITION_VISITS
        }

        # Tính toán nước đi bằng thuật toán tìm kiếm
        _SEARCH_BOT.time_limit = max(0.05, TIME_LIMIT - (time.perf_counter() - started))
        mv = _SEARCH_BOT.search(board, root_ok=lambda m: m in legal, repeat=repeat)

        if mv is None or mv not in legal:
            return fallback_action(observation)

        if mv < WALL_OFFSET:
            key = (pid, mv, opp_cell, num_walls)
            _POSITION_VISITS[key] = _POSITION_VISITS.get(key, 0) + 1

        return format_action(mv)

    except Exception:
        # Cơ chế dự phòng để giảm rủi ro lỗi crash/quá thời gian
        return fallback_action(observation)