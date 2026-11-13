"""
Wrongway Arena Agent - MSSV: xxxxxxxx
Chiến thuật: Bitboard Lazy BFS + Bitmask Make/Unmake + Negamax Alpha-Beta + Anti-Tunnel Evaluation
Chỉ sử dụng thư viện chuẩn của Python (Python 3.9+).
"""

import random
import time

# =============================================================================
# 1. CẤU HÌNH THỜI GIAN & HẰNG SỐ CHIẾN THUẬT
# =============================================================================

# Ngân sách thời gian 0.78s/lượt (nhiều hơn 0.65s của đối thủ nhưng vẫn an toàn dưới 1.0s của Arena)
MOVE_TIME_LIMIT = 0.78

CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)              # 0: Lên, 1: Phải, 2: Xuống, 3: Trái
WALL0 = 81                         # Mã hành động >= 81 là đặt tường (81..208)
RED, BLUE = 0, 1                   # Người chơi 0 (đích hàng 0) và 1 (đích hàng 8)
START = (76, 4)                    # [8, 4] và [0, 4]
GOAL = (
    sum(1 << c for c in range(0, 9)),
    sum(1 << c for c in range(72, 81)),
)
GOAL_ROW = (0, 8)
PREF_DIRS = ((0, 1, 3, 2), (2, 1, 3, 0))
WIN = 100000

TIME_BUDGET = MOVE_TIME_LIMIT
ROOT_WALLS = 20                    # Số tường ứng viên tối đa ở gốc
INNER_WALLS = 14                   # Số tường ứng viên tối đa ở các nút trong cây
REPEAT_PENALTY = 25                # Phạt lặp lại thế cờ ở gốc
W_DIST = 10                        # Trọng số khoảng cách
W_WALLS = 22                       # Trọng số tường dự trữ (hàm lõm ^0.8)
WALL_POW = 0.8
TEMPO = 2
TT_SIZE = 300000

_WPOW = tuple(i ** WALL_POW for i in range(16))
_BIT = tuple(1 << i for i in range(CELLS))
ALL_81 = (1 << 81) - 1

# Khởi tạo bảng băm Zobrist cố định
_rng = random.Random(20240607)
ZP = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
ZW = [_rng.getrandbits(64) for _ in range(128)]
ZLEFT = [_rng.getrandbits(64) for _ in range(128)]
ZT = _rng.getrandbits(64)


def wall_info(w):
    """Mã tường 0..127 -> (hướng 'h'/'v', row, col)."""
    return ("h" if w < 64 else "v", (w & 63) >> 3, w & 7)


def wall_id(orient, r, c):
    """(hướng 'h'/'v', row, col) -> mã tường 0..127."""
    return (0 if orient == "h" else 64) + r * 8 + c


# Tiền xử lý hình học cho 128 vị trí tường: cạnh bị chặn, mặt nạ bit, điểm lưới, xung đột
WALL_EDGES, WALL_MASKS, WALL_PTS, CONFLICT = [], [], [], []
for _w in range(128):
    _o, _r, _c = wall_info(_w)
    if _o == "h":
        _u1, _u2 = _r * 9 + _c, _r * 9 + _c + 1
        _v1, _v2 = (_r + 1) * 9 + _c, (_r + 1) * 9 + _c + 1
        _e = [(_u1, 2), (_u2, 2), (_v1, 0), (_v2, 0)]
        _m1 = (1 << _u1) | (1 << _u2)
        _m2 = (1 << _v1) | (1 << _v2)
        _wm = (2, _m1, ALL_81 ^ _m1, 0, _m2, ALL_81 ^ _m2)
        _p = ((_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2)
        _cf = [wall_id("h", _r, _c), wall_id("v", _r, _c)]
        if _c > 0:
            _cf.append(wall_id("h", _r, _c - 1))
        if _c < 7:
            _cf.append(wall_id("h", _r, _c + 1))
    else:
        _u1, _u2 = _r * 9 + _c, (_r + 1) * 9 + _c
        _v1, _v2 = _r * 9 + _c + 1, (_r + 1) * 9 + _c + 1
        _e = [(_u1, 1), (_u2, 1), (_v1, 3), (_v2, 3)]
        _m1 = (1 << _u1) | (1 << _u2)
        _m2 = (1 << _v1) | (1 << _v2)
        _wm = (1, _m1, ALL_81 ^ _m1, 3, _m2, ALL_81 ^ _m2)
        _p = (_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1)
        _cf = [wall_id("v", _r, _c), wall_id("h", _r, _c)]
        if _r > 0:
            _cf.append(wall_id("v", _r - 1, _c))
        if _r < 7:
            _cf.append(wall_id("v", _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_MASKS.append(_wm)
    WALL_PTS.append(_p)
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
    """can[d] = bitboard các ô có thể bước theo hướng d khi chưa có tường."""
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
# 2. TRƯỜNG KHOẢNG CÁCH BITBOARD LƯỜI (LAZY BITBOARD BFS)
# =============================================================================

class BF:
    """BFS ngược từ hàng đích bằng bitboard, mở rộng lười theo từng lớp."""
    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can

    def dist(self, cell):
        """Số bước ngắn nhất từ `cell` tới đích; trả về INF nếu bị bịt kín."""
        bit = _BIT[cell]
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


# =============================================================================
# 3. TRẠNG THÁI BÀN CỜ (BOARD MAKE / UNMAKE)
# =============================================================================

class Board:
    """Trạng thái ván cờ hỗ trợ đặt/gỡ tường tốc độ cao bằng mặt nạ bit."""

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
        """Tạo cặp trường khoảng cách lười (BF cho RED, BF cho BLUE)."""
        c = self.can
        return (BF(GOAL[0], c), BF(GOAL[1], c))

    def dist(self, start, goal):
        """BFS bitboard trực tiếp khi kiểm tra nhanh 1 lần."""
        cur = _BIT[start]
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
        """Các ô hợp lệ mà quân của `player` có thể bước/nhảy tới."""
        t = self.turn if player is None else player
        me, op = self.pos[t], self.pos[1 - t]
        can = self.can
        out = []
        for d in PREF_DIRS[t]:
            if not ((can[d] >> me) & 1):
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

    def put_wall(self, w):
        """Đặt tường `w` lên bàn cờ bằng mặt nạ bit."""
        d1, _, inv1, d2, _, inv2 = WALL_MASKS[w]
        can = self.can
        can[d1] &= inv1
        can[d2] &= inv2
        forbid = self.forbid
        for x in CONFLICT[w]:
            forbid[x] += 1
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        pts[p0] += 1
        pts[p1] += 1
        pts[p2] += 1
        self.whash ^= ZW[w]

    def remove_wall(self, w):
        """Gỡ tường `w` khỏi bàn cờ."""
        d1, m1, _, d2, m2, _ = WALL_MASKS[w]
        can = self.can
        can[d1] |= m1
        can[d2] |= m2
        forbid = self.forbid
        for x in CONFLICT[w]:
            forbid[x] -= 1
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        pts[p0] -= 1
        pts[p1] -= 1
        pts[p2] -= 1
        self.whash ^= ZW[w]

    def wall_ok(self, w, player=None):
        """Kiểm tra tính hợp lệ đầy đủ của tường `w`."""
        t = self.turn if player is None else player
        if self.left[t] <= 0 or self.forbid[w]:
            return False
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        if (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0) >= 2:
            d1, m1, inv1, d2, m2, inv2 = WALL_MASKS[w]
            can = self.can
            can[d1] &= inv1
            can[d2] &= inv2
            ok = (
                self.dist(self.pos[0], GOAL[0]) < INF
                and self.dist(self.pos[1], GOAL[1]) < INF
            )
            can[d1] |= m1
            can[d2] |= m2
            return ok
        return True

    def zkey(self):
        """Khóa băm Zobrist đầy đủ của trạng thái hiện tại."""
        return (
            self.whash
            ^ ZP[0][self.pos[0]]
            ^ ZP[1][self.pos[1]]
            ^ ZLEFT[self.left[0] * 11 + self.left[1]]
            ^ (ZT if self.turn else 0)
        )


# =============================================================================
# 4. ĐỘNG CƠ TÌM KIẾM NEGAMAX + ALPHA-BETA + CHỐNG BẪY HÀNH LANG HẸP
# =============================================================================

class _Timeout(Exception):
    """Ngắt tìm kiếm khi hết thời gian cho phép."""


class Bot:
    """Bộ máy tìm kiếm Negamax + Alpha-Beta + Lazy BF + Transposition Table."""

    def __init__(self, max_depth=32, time_limit=TIME_BUDGET):
        self.max_depth = max_depth
        self.time_limit = time_limit
        self.tt = {}
        self.nodes = 0
        self.depth_reached = 0
        self.deadline = 0.0

    def _wall_cands(self, b, f_op, op_pos, limit, check=None):
        """
        Sinh danh sách mã tường (0..127) cắt đường ngắn nhất của đối thủ,
        duyệt hết nhánh đường đi chính từ gần đối thủ tới đích (không bỏ sót bẫy xa),
        sau đó duyệt tiếp nhánh phụ nếu vẫn còn chỗ trong `limit`.
        """
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF:
            return []
        layers = f_op.layers
        can = b.can
        forbid = b.forbid
        out = []
        seen = set()

        # Nhánh 1: Đi dọc theo đường ngắn nhất chính từ op_pos tới đích
        u = op_pos
        cur_d = d
        alt_u = -1
        alt_d = -1

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
                                if check is None or b.wall_ok(w, check):
                                    out.append(w)
                        if nxt < 0:
                            nxt = v
                        elif alt_u < 0:
                            alt_u = v
                            alt_d = cur_d - 1
            if nxt < 0 or len(out) >= limit:
                break
            u = nxt
            cur_d -= 1

        # Nhánh 2: Nếu đường ngắn nhất của đối thủ phân nhánh và chưa đủ limit, xét tiếp nhánh phụ
        if alt_u >= 0 and len(out) < limit:
            u = alt_u
            cur_d = alt_d
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
                                    if check is None or b.wall_ok(w, check):
                                        out.append(w)
                            if nxt < 0:
                                nxt = v
                if nxt < 0 or len(out) >= limit:
                    break
                u = nxt
                cur_d -= 1

        return out[:limit]

    def _eval(self, b, F, ply):
        """
        Hàm đánh giá tại nút lá từ góc nhìn của người tới lượt `b.turn`.
        Kết hợp:
        - Phát hiện thắng ngay trong 1 bước đi.
        - Đua về đích khi một bên đã hết tường.
        - Khoảng cách + hàm lõm số tường dự trữ.
        - Phát hiện hành lang hẹp 1 ô (Anti-Tunnel / Anti-Box) bằng dịch bit 2 lớp.
        """
        t = b.turn
        o = 1 - t
        me = b.pos[t]
        op = b.pos[o]
        f_me = F[t]
        f_op = F[o]
        d_me = f_me.dist(me)
        d_op = f_op.dist(op)

        # 1. Nếu tới lượt mình mà chỉ cách đích 1 bước (và đối thủ không đứng chặn ở hàng đích)
        if d_me == 1 and (op // 9) != GOAL_ROW[t]:
            return WIN - (ply + 1)

        # 2. Khi đối thủ hết tường và mình kịp về đích trước/bằng -> thắng chắc cuộc đua
        if b.left[o] == 0 and d_me <= d_op:
            return WIN // 2 - d_me
        # Khi mình hết tường và đối thủ gần đích hơn hẳn -> thua chắc cuộc đua
        if b.left[t] == 0 and d_op < d_me:
            return -(WIN // 2 - d_op)

        wv = _WPOW[b.left[t]] - _WPOW[b.left[o]]
        score = W_DIST * (d_op - d_me) + W_WALLS * wv + TEMPO

        # 3. Kiểm tra độ rộng hành lang thoát hiểm 2 bước tiếp theo bằng phép toán bit O(1):
        #    Tránh chui vào hành lang hẹp 1 ô khi đối thủ còn tường (chống bẫy hộp 4 tường)
        c0, c1, c2, c3 = b.can
        if b.left[o] >= 2 and d_me >= 2:
            cur_m = _BIT[me]
            s1_m = (
                ((cur_m & c0) >> 9)
                | ((cur_m & c1) << 1)
                | ((cur_m & c2) << 9)
                | ((cur_m & c3) >> 1)
            ) & f_me.layers[d_me - 1]
            if (s1_m & (s1_m - 1)) == 0:
                # Bước 1 chỉ có đúng 1 ô duy nhất
                s2_m = (
                    ((s1_m & c0) >> 9)
                    | ((s1_m & c1) << 1)
                    | ((s1_m & c2) << 9)
                    | ((s1_m & c3) >> 1)
                ) & f_me.layers[d_me - 2]
                if (s2_m & (s2_m - 1)) == 0:
                    score -= 3.5
                else:
                    score -= 1.2

        if b.left[t] >= 2 and d_op >= 2:
            cur_o = _BIT[op]
            s1_o = (
                ((cur_o & c0) >> 9)
                | ((cur_o & c1) << 1)
                | ((cur_o & c2) << 9)
                | ((cur_o & c3) >> 1)
            ) & f_op.layers[d_op - 1]
            if (s1_o & (s1_o - 1)) == 0:
                s2_o = (
                    ((s1_o & c0) >> 9)
                    | ((s1_o & c1) << 1)
                    | ((s1_o & c2) << 9)
                    | ((s1_o & c3) >> 1)
                ) & f_op.layers[d_op - 2]
                if (s2_o & (s2_o - 1)) == 0:
                    score += 3.5
                else:
                    score += 1.2

        return score

    def _search(self, b, depth, alpha, beta, F, ply):
        self.nodes += 1
        if (self.nodes & 31) == 0 and time.perf_counter() > self.deadline:
            raise _Timeout

        t = b.turn
        o = 1 - t
        if b.pos[o] // 9 == GOAL_ROW[o]:
            return -(WIN - ply)
        if depth <= 0:
            return self._eval(b, F, ply)

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

        # 1. Xét các nước đi quân trước (tái sử dụng F tốn 0 BFS)
        pawns = b.pawn_moves(t)
        pawns.sort(key=f_me.dist)
        if tt_move is not None and tt_move < WALL0 and tt_move in pawns:
            pawns.remove(tt_move)
            pawns.insert(0, tt_move)

        for mv in pawns:
            b.pos[t] = mv
            b.turn = o
            val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
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

        # 2. Khi nước đi quân chưa cắt tỉa beta mới sinh và thử các nước đặt tường
        if b.left[t] > 0:
            cands = self._wall_cands(b, F[o], b.pos[o], INNER_WALLS)
            if tt_move is not None and tt_move >= WALL0:
                tw = tt_move - WALL0
                if not b.forbid[tw]:
                    if tw in cands:
                        cands.remove(tw)
                    cands.insert(0, tw)

            pts = b.pts
            p0_pos, p1_pos = b.pos[0], b.pos[1]
            for w in cands:
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
                val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                b.turn = t
                b.left[t] += 1
                b.remove_wall(w)

                if val > best:
                    best = val
                    best_mv = WALL0 + w
                    if val > alpha:
                        alpha = val
                        if alpha >= beta:
                            self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                            return best

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
        """Chọn nước đi tốt nhất ở gốc bằng Iterative Deepening."""
        b = board.copy()
        t = b.turn
        o = 1 - t
        repeat = repeat or {}
        self.nodes = 0
        self.deadline = time.perf_counter() + self.time_limit

        # 1. Nếu có bước về đích ngay lập tức -> đi luôn
        for mv in b.pawn_moves(t):
            if mv // 9 == GOAL_ROW[t] and (root_ok is None or root_ok(mv)):
                return mv

        F = b.fields()
        me, op = b.pos[t], b.pos[o]
        d_me = F[t].dist(me)

        # 2. Xây dựng danh sách nước đi ở gốc: đi quân trước, đặt tường sau
        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        pawns.sort(key=lambda m: (F[t].dist(m), repeat.get(m, 0)))

        if b.left[t] == 0:
            fwd = [m for m in pawns if F[t].dist(m) < d_me]
            if fwd:
                pawns = fwd

        walls = []
        if b.left[t] > 0:
            for w in self._wall_cands(b, F[o], op, ROOT_WALLS, check=t):
                mv = WALL0 + w
                if root_ok is None or root_ok(mv):
                    walls.append(mv)

        actions = pawns + walls
        if not actions:
            return None
        if len(actions) == 1:
            return actions[0]

        best_action = actions[0]
        self.depth_reached = 0

        try:
            for depth in range(1, self.max_depth + 1):
                alpha, beta = -WIN * 2, WIN * 2
                cur_best, cur_score = None, -WIN * 2
                scores = {}

                for mv in actions:
                    if mv < WALL0:
                        pen = REPEAT_PENALTY * repeat.get(mv, 0)
                        b.pos[t] = mv
                        b.turn = o
                        val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1) - pen
                        b.turn = t
                        b.pos[t] = me
                    else:
                        w = mv - WALL0
                        b.put_wall(w)
                        F2 = b.fields()
                        if F2[0].dist(b.pos[0]) >= INF or F2[1].dist(b.pos[1]) >= INF:
                            b.remove_wall(w)
                            continue
                        b.left[t] -= 1
                        b.turn = o
                        try:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, 1)
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
                    self.depth_reached = depth
                    actions.sort(key=lambda a: -scores.get(a, -WIN * 2))

                if abs(cur_score) >= WIN - 200:
                    if depth >= WIN - abs(cur_score):
                        break
        except _Timeout:
            pass

        return best_action


# =============================================================================
# 5. GIAO TIẾP VỚI ARENA
# =============================================================================

_BOT = None
_VISITS = {}
_LAST_TURN = {}


def board_from_observation(obs):
    """Dựng lại đối tượng Board từ observation của Arena."""
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
    """Mã số nguyên nước đi -> dict hành động theo giao thức của Arena."""
    if mv < WALL0:
        return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = wall_info(mv - WALL0)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}


def _fallback(observation):
    """Dự phòng an toàn: bước tới ô hợp lệ gần đích nhất (ưu tiên gần cột giữa)."""
    try:
        moves = observation["legal_moves"]
        if not moves:
            return {"type": "pass"}
        distances = observation["distances"]
        best = min(
            moves,
            key=lambda m: (
                distances[m[0]][m[1]] if distances[m[0]][m[1]] >= 0 else 10**6
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
    if _BOT is None:
        _BOT = Bot()

    nw = len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"])
    opp_cell = board.pos[1 - pid]
    repeat = {
        m: _VISITS.get((pid, m, opp_cell, nw), 0)
        for m in legal
        if m < WALL0 and (pid, m, opp_cell, nw) in _VISITS
    }

    _BOT.time_limit = max(0.05, TIME_BUDGET - (time.perf_counter() - started))
    mv = _BOT.choose(board, root_ok=lambda m: m in legal, repeat=repeat)
    if mv is None or mv not in legal:
        return _fallback(observation)

    if mv < WALL0:
        vkey = (pid, mv, opp_cell, nw)
        _VISITS[vkey] = _VISITS.get(vkey, 0) + 1
    return move_to_action(mv)


def choose_action(observation):
    """Hàm giao tiếp chính được Arena gọi mỗi lượt."""
    try:
        return _choose(observation)
    except Exception:
        return _fallback(observation)