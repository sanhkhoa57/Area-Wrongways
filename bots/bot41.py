"""Fuubao 4.0 - Siêu Tác Tử Bitboard 81-bit & Chiến Thuật Toàn Diện cho WrongWay.

Hợp nhất kiến trúc Bitboard nguyên thủy tốc độ cao với 4 nâng cấp vượt trội:
1. 81-bit Anchor Bitboard + Staged Move Generation (TT -> Killer -> Pawn -> Wall -> Pass)
   + Định lý touch_me/touch_op (bỏ qua BFS khi tường không chạm _sp_edges).
2. Khắc phục lỗi sắp xếp tường theo `code` khi gain == 0: định giá ưu tiên tường theo
   số cạnh ngắn nhất bị cắt (1 hoặc 2 cạnh), khoảng cách tới đối thủ và thế kẹp chữ L.
3. Nhận diện Tương tác Nhảy Quân (Jump/Diagonal Tempo) trực tiếp trong _gen_pawns và _evalb,
   triệt tiêu hoàn toàn điểm mù nhảy quân tại tầng cắt lá depth == 1.
4. Phòng thủ Phá Bẫy (Active Counter-Walls) ở cả nút gốc và các nút sâu trong cây,
   kết hợp nhận diện thế "Chặn Cửa" (_blocked) và nước đi chờ PASS.
"""

import gc
from time import perf_counter

# Tắt GC tự động trong lúc tìm kiếm để tránh khựng ~20ms ngẫu nhiên
gc.disable()

# ---------------------------------------------------------------------------
# Hằng số Bitboard 81-bit
# ---------------------------------------------------------------------------
SIZE = 9
ALL = (1 << 81) - 1
ROW = [((1 << 9) - 1) << (9 * r) for r in range(9)]
COL = [sum(1 << (9 * r + c) for r in range(9)) for c in range(9)]
ANCHOR = sum(1 << (9 * r + c) for r in range(8) for c in range(8))
U0 = ALL & ~ROW[0]
D0 = ALL & ~ROW[8]
L0 = ALL & ~COL[0]
R0 = ALL & ~COL[8]
INF = 1000

_RC_R = [i // 9 for i in range(81)]
_RC_C = [i % 9 for i in range(81)]
_CENTER_BIAS = [4 - abs((i % 9) - 4) for i in range(81)]

# Mặt nạ vùng lân cận bán kính 2 quanh mỗi ô để chấm điểm độ gần của tường trong O(1)
_NEAR_MASK = [0] * 81
for _i in range(81):
    _r0, _c0 = _i // 9, _i % 9
    _m = 0
    for _r in range(max(0, _r0 - 2), min(8, _r0 + 2)):
        for _c in range(max(0, _c0 - 2), min(8, _c0 + 2)):
            _m |= 1 << (_r * 9 + _c)
    _NEAR_MASK[_i] = _m

# Mã hóa hành động: 0..80 = đi quân, 100+a = tường H, 200+a = tường V, 999 = PASS
H_BASE = 100
V_BASE = 200
PASS = 999

# ---------------------------------------------------------------------------
# Tham số lượng giá (đã hiệu chỉnh đối kháng)
# ---------------------------------------------------------------------------
STEP = 100                  # 1 bước chênh lệch đường đi = 100 điểm
TEMPO = 50                  # lợi thế quyền đi trong lượt = 0.5 bước
JUMP_TEMPO = 75             # thưởng/phạt khi tạo thế nhảy qua đầu trực tiếp
RACE_BONUS = 600            # thưởng khi đối phương hết tường và mình dẫn nhịp
CONTEMPT = 250              # điểm phạt thế Hòa (ưu tiên tìm đường thắng)

# Giá trị cận biên của từng bức tường (3 tường cuối quý giá gấp đôi tường đầu)
_WALL_MARGINAL = [240, 185, 145, 122, 120, 120, 120, 120, 118, 118]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]

TIME_LIMIT = 0.85           # tận dụng tối đa giới hạn 1.0s của Arena
TOP_WALLS = 10              # số ứng viên tường tinh hoa tại nút trong
ITER_FACTOR = 0.95
WIN = 1000000
MATE_ZONE = WIN - 2000

_TT = {}
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0}
_HIST = set()
_ROOT_GOAL = [0]
_BLOCK = [False]
_BCACHE = {}
BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 150


class _TimeUp(Exception):
    pass


def _to_tt(score, ply):
    if score > MATE_ZONE:
        return score + ply
    if score < -MATE_ZONE:
        return score - ply
    return score


def _draw(goal):
    return -CONTEMPT if goal == _ROOT_GOAL[0] else CONTEMPT


# ---------------------------------------------------------------------------
# Các phép toán Bitboard BFS & Đồ thị Đường đi Ngắn nhất (DAG)
# ---------------------------------------------------------------------------
def _dist(start, goal, U, D, L, R):
    """Bitboard BFS xuôi siêu tốc từ 1 ô `start` tới `goal`."""
    if start & goal:
        return 0
    seen = front = start
    d = 0
    while True:
        d += 1
        front = (
            ((front & U) >> 9)
            | ((front & D) << 9)
            | ((front & L) >> 1)
            | ((front & R) << 1)
        ) & ~seen
        if not front:
            return INF
        if front & goal:
            return d
        seen |= front


def _back_layers(goal, target, U, D, L, R, extra):
    """BFS ngược từ hàng đích `goal` cho tới khi vượt qua `target` thêm `extra` lớp."""
    layers = [goal]
    seen = front = goal
    hit = 0 if (goal & target) else -1
    while hit < 0 or len(layers) <= hit + extra:
        front = (
            ((front & U) >> 9)
            | ((front & D) << 9)
            | ((front & L) >> 1)
            | ((front & R) << 1)
        ) & ~seen
        if not front:
            break
        seen |= front
        layers.append(front)
        if hit < 0 and (front & target):
            hit = len(layers) - 1
    return layers, hit


def _sp_edges(pos, layers, d, U, D, L, R):
    """Trích xuất toàn bộ cạnh nằm trên DAG đường đi ngắn nhất từ `pos` tới đích
    cùng số lượng điểm thắt cổ chai (chokes).
    """
    up = dn = lt = rt = 0
    cur = pos
    chokes = 0
    for k in range(d, 0, -1):
        nxt = layers[k - 1]
        u = cur & U & (nxt << 9)
        w = cur & D & (nxt >> 9)
        l = cur & L & (nxt << 1)
        r = cur & R & (nxt >> 1)
        up |= u
        dn |= w
        lt |= l
        rt |= r
        cur = (u >> 9) | (w << 9) | (l >> 1) | (r << 1)
        if k >= d - 3 and cur.bit_count() == 1:
            chokes += 1
    return up, dn, lt, rt, chokes


def _pawn_moves(a, b, U, D, L, R):
    """Sinh các ô đích hợp lệ cho quân `a` (có tính nhảy thẳng & nhảy chéo qua `b`)."""
    res = []
    if a & U:
        t = a >> 9
        if t != b:
            res.append(t)
        elif b & U:
            res.append(b >> 9)
        else:
            if b & L:
                res.append(b >> 1)
            if b & R:
                res.append(b << 1)
    if a & D:
        t = a << 9
        if t != b:
            res.append(t)
        elif b & D:
            res.append(b << 9)
        else:
            if b & L:
                res.append(b >> 1)
            if b & R:
                res.append(b << 1)
    if a & L:
        t = a >> 1
        if t != b:
            res.append(t)
        elif b & L:
            res.append(b >> 1)
        else:
            if b & U:
                res.append(b >> 9)
            if b & D:
                res.append(b << 9)
    if a & R:
        t = a << 1
        if t != b:
            res.append(t)
        elif b & R:
            res.append(b << 1)
        else:
            if b & U:
                res.append(b >> 9)
            if b & D:
                res.append(b << 9)
    return res


def _can_jump_Closer(me, op, gm, dm, U, D, L, R):
    """Kiểm tra O(1) xem `me` (đang tới lượt và đứng sát `op`) có nhảy qua `op`
    để rút ngắn quãng đường so với `dm` tĩnh hay không.
    """
    if (me & U) and (me >> 9) == op:
        if (op & U) and (gm == ROW[0] or _dist(op >> 9, gm, U, D, L, R) + 1 < dm):
            return True
    elif (me & D) and (me << 9) == op:
        if (op & D) and (gm == ROW[8] or _dist(op << 9, gm, U, D, L, R) + 1 < dm):
            return True
    elif (me & L) and (me >> 1) == op:
        if (op & L) and _dist(op >> 1, gm, U, D, L, R) + 1 < dm:
            return True
    elif (me & R) and (me << 1) == op:
        if (op & R) and _dist(op << 1, gm, U, D, L, R) + 1 < dm:
            return True
    return False


def _static(dm, do, wm, wo):
    """Hàm đánh giá tĩnh cơ sở theo góc nhìn bên đang tới lượt."""
    s = STEP * (do - dm) + TEMPO + WALL_VALUE[wm] - WALL_VALUE[wo]
    if wo == 0 and dm <= do:
        s += RACE_BONUS
    elif wm == 0 and dm > do:
        s -= RACE_BONUS
    return s


# ---------------------------------------------------------------------------
# Nhận diện Thế "Chặn Cửa" (Fortress / Door-Blocking Detection)
# ---------------------------------------------------------------------------
def _jumps(o, U, D, L, R):
    js = []
    if o & D:
        dest = (o >> 9) if (o & U) else (((o >> 1) if (o & L) else 0) | ((o << 1) if (o & R) else 0))
        if dest:
            js.append((o << 9, dest))
    if o & U:
        dest = (o << 9) if (o & D) else (((o >> 1) if (o & L) else 0) | ((o << 1) if (o & R) else 0))
        if dest:
            js.append((o >> 9, dest))
    if o & L:
        dest = (o << 1) if (o & R) else (((o >> 9) if (o & U) else 0) | ((o << 9) if (o & D) else 0))
        if dest:
            js.append((o >> 1, dest))
    if o & R:
        dest = (o >> 1) if (o & L) else (((o >> 9) if (o & U) else 0) | ((o << 9) if (o & D) else 0))
        if dest:
            js.append((o << 1, dest))
    return js


def _reach_mask(o, goal, U, D, L, R):
    js = _jumps(o, U, D, L, R)
    seen = goal | o
    front = goal & ~o
    while front:
        nxt = (
            ((front & U) >> 9)
            | ((front & D) << 9)
            | ((front & L) >> 1)
            | ((front & R) << 1)
        ) & ~seen
        for m, dest in js:
            if (front & dest) and not (seen & m):
                nxt |= m
        seen |= nxt
        front = nxt
    return seen & ~o


def _blocked(a, o, goal, U, D, L, R, hw, vw, d):
    key = (o, hw, vw, goal)
    m = _BCACHE.get(key)
    if m is None:
        if a & goal:
            return False
        seen = a | o
        front = a
        for _ in range(d):
            front = (
                ((front & U) >> 9)
                | ((front & D) << 9)
                | ((front & L) >> 1)
                | ((front & R) >> 1)
            ) & ~seen
            if front & goal:
                return False
            if not front:
                break
            seen |= front
        m = _reach_mask(o, goal, U, D, L, R)
        _BCACHE[key] = m
    return not (m & a)


def _may_block(a, o, goal_row, d):
    ia = a.bit_length() - 1
    io = o.bit_length() - 1
    ra = _RC_R[ia]
    ro = _RC_R[io]
    dr = ra - ro if ra > ro else ro - ra
    dc = _RC_C[ia] - _RC_C[io]
    if dc < 0:
        dc = -dc
    dg = ro - goal_row if ro > goal_row else goal_row - ro
    return dr + dc + dg <= d


def _evalb(dm, do, wm, wo, me, op, gm, go, U, D, L, R, hw, vw):
    """Đánh giá đầy đủ có xét Tương tác Nhảy Quân và Thế Chặn Cửa."""
    s = _static(dm, do, wm, wo)
    # Cộng thưởng nếu bên đang tới lượt (`me`) có thể nhảy vọt qua đầu `op`
    if _can_jump_Closer(me, op, gm, dm, U, D, L, R):
        s += JUMP_TEMPO

    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if _may_block(me, op, 0 if (gm & 1) else 8, dm) and _blocked(me, op, gm, U, D, L, R, hw, vw, dm):
                return _draw(gm)
        elif s < 0:
            if _may_block(op, me, 0 if (go & 1) else 8, do) and _blocked(op, me, go, U, D, L, R, hw, vw, do):
                return _draw(gm)
    return s


# ---------------------------------------------------------------------------
# Sinh nước đi quân & Sinh tường chiến thuật
# ---------------------------------------------------------------------------
def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    """Sinh các bước di chuyển quân (kèm điểm đánh giá chính xác sau bước đi)."""
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    for x in _pawn_moves(a, b, U, D, L, R):
        if x & ga:
            return x.bit_length() - 1, None
        nd = -1
        for k in range(lo, hi):
            if x & lay_m[k]:
                nd = k
                break
        if nd < 0:
            nd = _dist(x, ga, U, D, L, R)
        idx_x = x.bit_length() - 1
        # Sau khi `a` đi tới `x`, tới lượt `b` đi -> gọi _evalb với `b` là người tới lượt
        sc = -_evalb(db, nd, wb, wa, b, x, gb, ga, U, D, L, R, hw, vw) + _CENTER_BIAS[idx_x]
        out.append((sc, idx_x, x, U, D, L, R, hw, vw, wa, nd, db))
    return None, out


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, top, defensive):
    """Sinh các bức tường chiến thuật bằng Bitboard 81-bit.
    Sửa triệt để lỗi sắp xếp theo `code` của greenbird bằng điểm chiến thuật (Tactical Priority).
    """
    mu, md, ml, mr, my_chokes = _sp_edges(a, lay_m, dm, U, D, L, R)
    lay_o, do = _back_layers(gb, b, U, D, L, R, 0)
    ou, od, ol, orr, op_chokes = _sp_edges(b, lay_o, do, U, D, L, R)

    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)

    # 1. Tường cắt trực tiếp các cạnh thuộc DAG đường ngắn nhất của đối thủ
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v

    # 2. Bổ sung tường kẹp hông chữ L sát đối thủ nếu nối tiếp với tường đã có
    ib = b.bit_length() - 1
    b_near = _NEAR_MASK[ib]
    existing_neighbors_v = ((hw << 1) | hw | (hw >> 9) | (hw >> 8) | (vw << 18) | (vw >> 18)) & b_near
    existing_neighbors_h = ((vw << 9) | vw | (vw >> 1) | (vw << 8) | (hw << 2) | (hw >> 2)) & b_near
    cand_v |= existing_neighbors_v & avail_v
    cand_h |= existing_neighbors_h & avail_h

    # 3. Tường Phòng Thủ Phá Bẫy (khi ở gốc hoặc khi đường mình bị thắt cổ chai)
    def_h = def_v = 0
    if defensive or (wb > 0 and my_chokes >= 2):
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        def_h = ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        def_v = ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
        cand_h |= def_h
        cand_v |= def_v

    wa2 = wa - 1
    walls = []
    rb, cb = _RC_R[ib], _RC_C[ib]

    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            w_idx = w.bit_length() - 1
            if is_h:
                e1 = w | (w << 1)
                e2 = (w << 9) | (w << 10)
                D2 = D & ~e1
                U2 = U & ~e2
                L2 = L
                R2 = R
                touch_me = (md & e1) | (mu & e2)
                touch_op = (od & e1) | (ou & e2)
                hw2 = hw | w
                vw2 = vw
                code = H_BASE + w_idx
                is_def = bool(w & def_h)
            else:
                e1 = w | (w << 9)
                e2 = (w << 1) | (w << 10)
                R2 = R & ~e1
                L2 = L & ~e2
                U2 = U
                D2 = D
                touch_me = (mr & e1) | (ml & e2)
                touch_op = (orr & e1) | (ol & e2)
                hw2 = hw
                vw2 = vw | w
                code = V_BASE + w_idx
                is_def = bool(w & def_v)

            if touch_op:
                ndo = _dist(b, gb, U2, D2, L2, R2)
                if ndo >= INF:
                    continue
            else:
                ndo = do

            if touch_me:
                ndm = _dist(a, ga, U2, D2, L2, R2)
                if ndm >= INF:
                    continue
            else:
                ndm = dm

            # Điểm đánh giá tĩnh sau khi đặt tường
            base_sc = -_static(ndo, ndm, wb, wa2)

            # Điểm Tie-Break Chiến Thuật (khắc phục hoàn toàn lỗi sort theo `code` của greenbird):
            # - Thưởng nếu tường cắt đồng thời cả 2 ô của cạnh đường ngắn nhất (touch_op có 2 bit)
            # - Thưởng cho tường nằm sát vị trí đối thủ (prox_bonus)
            # - Thưởng nhẹ cho tường phòng thủ bảo vệ cổ chai (is_def)
            cut_both = 12 if (touch_op & (touch_op - 1)) else (6 if touch_op else 0)
            prox_pen = abs(_RC_R[w_idx] - rb) + abs(_RC_C[w_idx] - cb)
            def_bonus = 5 if (is_def and ndm == dm) else 0
            sort_sc = base_sc + cut_both + def_bonus - prox_pen

            walls.append((sort_sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, base_sc))

    walls.sort(key=lambda x: x[0], reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
    """Dựng nhanh 1 nút con cho tường từ TT hoặc Killer mà không cần gọi _gen_walls."""
    if wa <= 0:
        return None
    if code >= V_BASE:
        w = 1 << (code - V_BASE)
        if not (w & ANCHOR) or (w & (vw | (vw << 9) | (vw >> 9) | hw)):
            return None
        R2 = R & ~(w | (w << 9))
        L2 = L & ~((w << 1) | (w << 10))
        U2, D2, hw2, vw2 = U, D, hw, vw | w
    else:
        w = 1 << (code - H_BASE)
        if not (w & ANCHOR) or (w & (hw | (hw << 1) | (hw >> 1) | vw)):
            return None
        D2 = D & ~(w | (w << 1))
        U2 = U & ~((w << 9) | (w << 10))
        L2, R2, hw2, vw2 = L, R, hw | w, vw
    ndo = _dist(b, gb, U2, D2, L2, R2)
    if ndo >= INF:
        return None
    ndm = _dist(a, ga, U2, D2, L2, R2)
    if ndm >= INF:
        return None
    sc = -_static(ndo, ndm, wb, wa - 1)
    return (sc, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo, sc)


# ---------------------------------------------------------------------------
# Bộ Máy Tìm Kiếm Negamax + PVS + Staged Move Generation
# ---------------------------------------------------------------------------
class _Searcher:
    def __init__(self, deadline, turn, max_turns):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        self.killers = [[None, None] for _ in range(128)]
        self.path = set()

    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply):
        self.nodes += 1
        if (self.nodes & 7) == 0 and perf_counter() > self.deadline:
            raise _TimeUp()
        if self.turn + ply >= self.max_turns:
            return _draw(ga)
        if depth <= 0:
            return _evalb(da, db, wa, wb, a, b, ga, gb, U, D, L, R, hw, vw)

        key = (a, b, wa, wb, hw, vw, ga)
        if key in self.path or key in _HIST:
            return _draw(ga)

        entry = _TT.get(key)
        tt_move = None
        if entry is not None:
            e_depth, e_score, e_flag, tt_move = entry
            if e_score > MATE_ZONE:
                e_score -= ply
            elif e_score < -MATE_ZONE:
                e_score += ply
            if e_depth >= depth:
                if e_flag == 0:
                    return e_score
                if e_flag == 1:
                    if e_score >= beta:
                        return e_score
                elif e_score <= alpha:
                    return e_score

        lay_m, dm = _back_layers(ga, a, U, D, L, R, 2)
        win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
        if win is not None:
            return WIN - ply

        pass_sc = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw)

        # Cắt lá nhanh tại depth == 1 (đã tích hợp kiểm tra Nhảy Quân trong _gen_pawns)
        if depth == 1:
            best = pass_sc
            best_move = PASS
            for k in pawns:
                if k[0] > best:
                    best = k[0]
                    best_move = k[1]
            if best < beta and wa > 0:
                walls = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                   lay_m, dm, 1, False)
                if walls and walls[0][12] > best:
                    best = walls[0][12]
                    best_move = walls[0][1]
            _TT[key] = (1, _to_tt(best, ply), 1 if best >= beta else 0, best_move)
            return best

        pawns.sort(reverse=True)
        self.path.add(key)
        orig_alpha = alpha
        best = -WIN * 2
        best_move = None
        nd = depth - 1
        np1 = ply + 1
        tried = set()
        first = True
        killers = self.killers[ply]
        cutoff = False
        stage = 0

        while stage < 5 and not cutoff:
            if stage == 0:
                lst = []
                if tt_move is not None:
                    if tt_move == PASS:
                        lst = [(pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db)]
                    elif tt_move < H_BASE:
                        lst = [k for k in pawns if k[1] == tt_move]
                    else:
                        c = _wall_child(tt_move, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                        if c is not None:
                            lst = [c]
            elif stage == 1:
                lst = []
                if wa > 0:
                    for km in killers:
                        if km is not None and km not in tried:
                            c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                lst.append(c)
            elif stage == 2:
                lst = pawns
            elif stage == 3:
                lst = (
                    _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                               lay_m, dm, TOP_WALLS, False)
                    if wa > 0
                    else ()
                )
            else:
                lst = [(pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db)]
            stage += 1

            for k in lst:
                code = k[1]
                if code in tried:
                    continue
                tried.add(code)
                if first:
                    sc = -self.search(
                        b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                        k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1
                    )
                    first = False
                else:
                    sc = -self.search(
                        b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                        k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha, np1
                    )
                    if alpha < sc < beta:
                        sc = -self.search(
                            b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                            k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1
                        )
                if sc > best:
                    best = sc
                    best_move = code
                    if sc > alpha:
                        alpha = sc
                        if alpha >= beta:
                            cutoff = True
                            if code >= H_BASE and code != PASS and killers[0] != code:
                                killers[1] = killers[0]
                                killers[0] = code
                            break

        self.path.discard(key)
        flag = 2 if best <= orig_alpha else (1 if best >= beta else 0)
        _TT[key] = (depth, _to_tt(best, ply), flag, best_move)
        return best


def _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0):
    da = _dist(a, ga, U, D, L, R)
    db = _dist(b, gb, U, D, L, R)
    lay_m, dm = _back_layers(ga, a, U, D, L, R, 2)
    win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
    if win is not None:
        return win, None

    if wa > 0:
        kids.extend(_gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                               lay_m, dm, None, True))
    kids.append((
        -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw) - 5,
        PASS, a, U, D, L, R, hw, vw, wa, da, db
    ))
    kids.sort(key=lambda k: -k[0])
    best_move = kids[0][1]
    if len(kids) == 1:
        return best_move, kids[0]

    deadline = t0 + TIME_LIMIT - _STATE["margin"]
    s = _Searcher(deadline, turn, max_turns)
    depth = 1
    scores = {}

    while depth < 60:
        depth += 1
        it_start = perf_counter()
        alpha = -WIN * 2
        beta = WIN * 2
        cur_best = None
        cur_best_sc = -WIN * 2
        new_scores = {}
        try:
            first = True
            for k in kids:
                if first:
                    sc = -s.search(
                        b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                        k[7], k[8], k[11], k[10], depth - 1, -beta, -alpha, 1
                    )
                    first = False
                else:
                    sc = -s.search(
                        b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                        k[7], k[8], k[11], k[10], depth - 1, -alpha - 1, -alpha, 1
                    )
                    if sc > alpha:
                        prev_best, prev_sc = cur_best, cur_best_sc
                        cur_best, cur_best_sc = k[1], sc
                        sc = -s.search(
                            b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                            k[7], k[8], k[11], k[10], depth - 1, -beta, -alpha, 1
                        )
                        cur_best, cur_best_sc = prev_best, prev_sc
                new_scores[k[1]] = sc
                if sc > cur_best_sc:
                    cur_best_sc = sc
                    cur_best = k[1]
                    if sc > alpha:
                        alpha = sc
        except _TimeUp:
            if cur_best is not None:
                best_move = cur_best
            break

        best_move = cur_best
        scores = new_scores
        kids.sort(key=lambda k: -scores.get(k[1], -WIN * 3))
        if abs(cur_best_sc) > MATE_ZONE and WIN - abs(cur_best_sc) <= depth:
            break
        now = perf_counter()
        if now + (now - it_start) * ITER_FACTOR > deadline:
            break

    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None


# ---------------------------------------------------------------------------
# Giao tiếp với Arena
# ---------------------------------------------------------------------------
def _bit(rc):
    return 1 << (rc[0] * 9 + rc[1])


def _fallback(observation):
    moves = observation.get("legal_moves") or []
    dist = observation.get("distances")
    if not moves:
        return {"type": "pass"}
    best = moves[0]
    best_d = INF
    for m in moves:
        d = dist[m[0]][m[1]] if dist else 0
        if 0 <= d < best_d:
            best, best_d = m, d
    return {"type": "move", "to": [int(best[0]), int(best[1])]}


def _decide(observation, t0):
    you = observation["you"]
    opp = observation["opponent"]
    a = _bit(you["position"])
    b = _bit(opp["position"])
    ga = ROW[you["goal_row"]]
    gb = ROW[opp["goal_row"]]
    wa = max(0, min(10, you["walls_remaining"]))
    wb = max(0, min(10, opp["walls_remaining"]))

    U, D, L, R = U0, D0, L0, R0
    hw = vw = 0
    for r, c in observation["walls"]["horizontal"]:
        w = 1 << (r * 9 + c)
        hw |= w
        D &= ~(w | (w << 1))
        U &= ~((w << 9) | (w << 10))
    for r, c in observation["walls"]["vertical"]:
        w = 1 << (r * 9 + c)
        vw |= w
        R &= ~(w | (w << 9))
        L &= ~((w << 1) | (w << 10))

    turn = observation.get("turn", 0)
    if turn < _STATE["last_turn"]:
        _TT.clear()
        _HIST.clear()
    elif len(_TT) > 180000:
        _TT.clear()
    _STATE["last_turn"] = turn
    max_turns = observation.get("max_turns", 500)

    _BLOCK[0] = (hw.bit_count() + vw.bit_count()) >= BLOCK_MIN_WALLS
    _BCACHE.clear()
    _ROOT_GOAL[0] = ga
    _HIST.add((a, b, wa, wb, hw, vw, ga))

    code, kid = _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0)
    if kid is not None:
        _HIST.add((b, kid[2], wb, kid[9], kid[7], kid[8], gb))

    if code == PASS:
        return {"type": "pass"}
    if code < H_BASE:
        return {"type": "move", "to": [code // 9, code % 9]}
    if code < V_BASE:
        idx = code - H_BASE
        return {"type": "wall", "orientation": "H", "row": idx // 9, "col": idx % 9}
    idx = code - V_BASE
    return {"type": "wall", "orientation": "V", "row": idx // 9, "col": idx % 9}


def _is_legal(action, observation):
    kind = action.get("type")
    if kind == "pass":
        return True
    if kind == "move":
        return list(action["to"]) in [list(m) for m in observation["legal_moves"]]
    if kind == "wall":
        for w in observation["legal_walls"]:
            if (
                w["orientation"] == action["orientation"]
                and w["row"] == action["row"]
                and w["col"] == action["col"]
            ):
                return True
    return False


def choose_action(observation):
    t0 = perf_counter()
    action = None
    try:
        _STATE["moves"] += 1
        if _STATE["moves"] % 25 == 0:
            gc.collect()
        action = _decide(observation, t0)
        if not _is_legal(action, observation):
            action = None
    except Exception:
        action = None
    if action is None:
        action = _fallback(observation)
    late = perf_counter() - (t0 + TIME_LIMIT - _STATE["margin"])
    if late > 0.02:
        _STATE["margin"] = min(0.4, _STATE["margin"] + 2 * late)
    return action