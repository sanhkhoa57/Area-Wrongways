"""Agent Wrongway - standalone Python implementation."""

import gc
from array import array
from time import perf_counter

# Tắt bộ dọn rác tự động để giảm độ trễ trong lượt suy nghĩ.
gc.disable()

# ---------------------------------------------------------------------------
# Hằng số bitboard
# ---------------------------------------------------------------------------
SIZE = 9
ALL = (1 << 81) - 1
ROW = [((1 << 9) - 1) << (9 * r) for r in range(9)]
COL = [sum(1 << (9 * r + c) for r in range(9)) for c in range(9)]
ANCHOR = sum(1 << (9 * r + c) for r in range(8) for c in range(8))  # neo tường hợp lệ
U0 = ALL & ~ROW[0]   # ô có thể đi lên (chưa có tường)
D0 = ALL & ~ROW[8]   # ô có thể đi xuống
L0 = ALL & ~COL[0]   # ô có thể sang trái
R0 = ALL & ~COL[8]   # ô có thể sang phải
INF = 1000
_RC_R = [i // 9 for i in range(81)]   # hàng của ô theo chỉ số bit
_RC_C = [i % 9 for i in range(81)]    # cột của ô theo chỉ số bit

# Mã hóa nước đi bằng số nguyên: 0..80 = đi tới ô, 100+a = tường H tại neo a,
# 200+a = tường V tại neo a, PASS = đứng yên.
H_BASE = 100
V_BASE = 200
PASS = 999

# ---------------------------------------------------------------------------
# Tham số đánh giá (đơn vị: 10 = một bước đi)
# ---------------------------------------------------------------------------
STEP = 10           # giá trị 1 bước chênh lệch quãng đường
TEMPO = 5           # lợi thế của bên đang tới lượt
RACE_BONUS = 60     # cuộc đua đã "chắc chắn" (một bên hết tường)
# Giá trị tổng của k bức tường còn lại.
# bước, riêng 3 tường cuối cùng quý hơn (2.4 / 1.8 / 1.4 bước) vì cuối ván, bên
# còn tường có thể chặn quyết định.  WALL_VALUE[k] = tổng giá trị của k tường.
_WALL_MARGINAL = [24, 18, 14, 12, 12, 12, 12, 12, 12, 12]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]
TIME_LIMIT = 0.82    # ngân sách cân bằng, chừa biên so với 1 giây Arena
TOP_WALLS = 9       # số tường tối đa xét tại một nút trong cây
FRONTIER_KILLERS = True  # ở nút sát lá, thử tường killer/TT trước
TB = True           # bảng tàn cuộc chính xác khi cả hai đã hết tường
TB_MAX = 12         # số bảng mới tối đa được tính trong một lượt
TB_TIME = 0.45      # chỉ tính bảng mới trong 45% đầu thời gian của lượt
TB_BOUND_PLY = 2    # dùng cận (một bên hết tường) chỉ khi bảng có sẵn hoặc ở <= 2 tầng đầu
TB1 = True          # giải trọn tiền tàn cuộc: một bên hết tường, bên kia còn đúng 1 tường
TB1_MAXK = 2        # còn <= 2 tường thì vẫn dùng bảng "1 tường" làm cận
TB1_TIME = 0.5      # tối đa 50% thời gian lượt để dựng bảng (phần còn lại để lượt sau)
ITER_FACTOR = 1.0   # bắt đầu độ sâu mới nếu còn ít nhất bằng thời gian lượt lặp vừa rồi
WIN = 100000

# Bộ nhớ dùng chung giữa các lượt trong một ván
_TT = {}
_TT_OLD = {}         # thế hệ trước của bảng chuyển vị (giữ lại khi bảng chính đầy)
TT2 = True
TT_CAP = 75000
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0}
_HIST = set()        # các thế cờ đã xuất hiện trong ván (để nhận ra lặp lại)
_ROOT_GOAL = [0]     # hàng đích của "mình" ở lượt hiện tại (để tính điểm hòa)
_ROOT_RECENT = []
_LAST_MY_POS = None
_LAST_BOARD_SIG = None
_STAGNANT = 0
_LAST_OBS_TURN = -1
CONTEMPT = 25        # hòa chỉ được 1 điểm (thắng 3): coi hòa là hơi xấu cho mình
_BLOCK = [False]     # bật kiểm tra "chặn cửa" khi trên bàn đã đủ nhiều tường
_BCACHE = {}
_LCACHE = {}         # nhớ các lớp BFS ngược theo (hàng đích, tường)
_WCACHE = {}         # nhớ danh sách tường ứng viên theo (quân đối thủ, tường)
_DCACHE = {}         # nhớ kết quả BFS: (ô, hàng đích, tường H, tường V) -> khoảng cách
_GCACHE = {}         # nhớ "đồ thị đường ngắn nhất" của một quân theo (ô, hàng đích, tường)
_TBC = {}            # bảng tàn cuộc 0-0 theo (tường H, tường V)
_TB1C = {}           # bảng tiền tàn cuộc "1 tường vs 0" theo (tường H, tường V, bên còn tường là N?)
BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 15    # chỉ kiểm tra khi một bên dẫn rõ rệt


MATE_ZONE = WIN - 1000


class _TimeUp(Exception):
    pass


def _to_tt(score, ply):
    """Điểm thắng/thua phụ thuộc độ sâu: lưu dạng tương đối để tái sử dụng đúng."""
    if score > MATE_ZONE:
        return score + ply
    if score < -MATE_ZONE:
        return score - ply
    return score


def _draw(goal):
    """Điểm của thế HÒA theo góc nhìn bên có hàng đích `goal` đang tới lượt.

    Thể thức 3-1-0: hòa kém hơn một ván cân bằng, nên với "mình" hòa = -CONTEMPT,
    với đối thủ hòa = +CONTEMPT (giữ tính đối xứng của negamax).
    """
    return -CONTEMPT if goal == _ROOT_GOAL[0] else CONTEMPT


# ---------------------------------------------------------------------------
# Các hàm cơ bản trên bitboard
# ---------------------------------------------------------------------------
def _dist(start, goal, U, D, L, R):
    """BFS bitboard: số bước ít nhất từ ô `start` (1 bit) tới hàng `goal`."""
    if start & goal:
        return 0
    seen = front = start
    d = 0
    while True:
        d += 1
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        if not front:
            return INF
        if front & goal:
            return d
        seen |= front


def _layers(goal, target, U, D, L, R, hw, vw):
    """BFS ngược từ hàng đích, có bộ nhớ đệm: các lớp BFS ngược từ hàng đích chỉ phụ
    thuộc vào TƯỜNG (không phụ thuộc vị trí quân), nên tính một lần cho mỗi bộ tường.

    Trả về (layers đầy đủ, chỉ số lớp chứa target).
    """
    key = (goal, hw, vw)
    lay = _LCACHE.get(key)
    if lay is None:
        lay = [goal]
        seen = front = goal
        while True:
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            if not front:
                break
            seen |= front
            lay.append(front)
        _LCACHE[key] = lay
    k = 0
    for f in lay:
        if f & target:
            return lay, k
        k += 1
    return lay, -1


def _sp_edges(pos, layers, d, U, D, L, R):
    """Các cạnh nằm trên ÍT NHẤT MỘT đường đi ngắn nhất từ pos tới đích.

    Trả về 4 mặt nạ: ô xuất phát của cạnh đi lên / xuống / trái / phải.
    """
    up = dn = lt = rt = 0
    cur = pos
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
    return up, dn, lt, rt


def _dag(pos, goal, U, D, L, R, hw, vw):
    """Đồ thị các đường ngắn nhất (DAG) của quân ở ô `pos`, có bộ nhớ đệm.

    Trả về (d, layers, F, up, dn, lt, rt): d = quãng đường; F[k] = các ô cách đích
    k bước nằm trên ít nhất một đường ngắn nhất xuất phát từ pos; up/dn/lt/rt =
    ô xuất phát của các cạnh thuộc DAG (như _sp_edges).
    """
    key = (pos, goal, hw, vw)
    ent = _GCACHE.get(key)
    if ent is not None:
        return ent
    lay, d = _layers(goal, pos, U, D, L, R, hw, vw)
    up = dn = lt = rt = 0
    if d < 0:
        ent = (INF, lay, None, 0, 0, 0, 0)
        _GCACHE[key] = ent
        return ent
    F = [0] * (d + 1)
    F[d] = cur = pos
    for k in range(d, 0, -1):
        nxt = lay[k - 1]
        u = cur & U & (nxt << 9)
        w = cur & D & (nxt >> 9)
        l = cur & L & (nxt << 1)
        r = cur & R & (nxt >> 1)
        up |= u
        dn |= w
        lt |= l
        rt |= r
        cur = (u >> 9) | (w << 9) | (l >> 1) | (r << 1)
        F[k - 1] = cur
    ent = (d, lay, F, up, dn, lt, rt)
    _GCACHE[key] = ent
    return ent


def _newdist(pos, goal, d, lay, F, src, U2, D2, L2, R2, hw2, vw2):
    """Quãng đường mới của quân sau khi chặn các cạnh DAG xuất phát từ ô `src`.

    Chính xác như BFS lại từ đầu, nhưng nhanh hơn: chỉ đi lại các lớp DAG từ chỗ
    bị chặn trở xuống; nếu DAG vẫn thông (một đường ngắn nhất còn nguyên) thì
    quãng đường không đổi; chỉ khi mọi đường ngắn nhất bị cắt mới phải BFS lại.
    """
    kmax = kmin = 0
    rest = src
    for k in range(d, 0, -1):
        f = F[k] & rest
        if f:
            if not kmax:
                kmax = k
            kmin = k
            rest ^= f
            if not rest:
                break
    cur = F[kmax]
    for j in range(kmax, 0, -1):
        cur = (((cur & U2) >> 9) | ((cur & D2) << 9)
               | ((cur & L2) >> 1) | ((cur & R2) << 1)) & lay[j - 1]
        if not cur:
            break
        if j <= kmin and cur == F[j - 1]:
            return d
    else:
        return d
    ck = (pos, goal, hw2, vw2)
    nd = _DCACHE.get(ck)
    if nd is None:
        nd = _DCACHE[ck] = _dist(pos, goal, U2, D2, L2, R2)
    return nd


def _pawn_moves(a, b, U, D, L, R):
    """Các ô quân `a` đi được (đúng luật arena: nhảy thẳng, chéo khi bị chặn)."""
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


def _static(dm, do, wm, wo):
    """Điểm thế cờ theo góc nhìn bên ĐANG TỚI LƯỢT.

    dm/do: quãng đường của mình/đối thủ; wm/wo: số tường còn lại.
    """
    s = STEP * (do - dm) + TEMPO + WALL_VALUE[wm] - WALL_VALUE[wo]
    if wo == 0 and dm <= do:
        s += RACE_BONUS        # đối thủ không thể làm mình chậm lại nữa
    elif wm == 0 and dm > do:
        s -= RACE_BONUS        # mình không thể làm đối thủ chậm lại nữa
    return s


def _jumps(o, U, D, L, R):
    """Các bước "nhảy qua" quân o: list (ô đứng cạnh o, các ô đáp xuống)."""
    js = []
    if o & D:                          # đứng dưới o, nhảy lên
        dest = (o >> 9) if o & U else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o << 9, dest))
    if o & U:                          # đứng trên o, nhảy xuống
        dest = (o << 9) if o & D else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o >> 9, dest))
    if o & L:                          # đứng bên trái o, nhảy sang phải
        dest = (o << 1) if o & R else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o >> 1, dest))
    if o & R:                          # đứng bên phải o, nhảy sang trái
        dest = (o >> 1) if o & L else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o << 1, dest))
    return js


def _reach_mask(o, goal, U, D, L, R):
    """Tập ô có thể tới hàng `goal` khi quân o đứng yên mãi (được nhảy qua o).

    BFS ngược từ đích, không đi vào ô o; cạnh "nhảy" m -> dest được xét ngược:
    nếu dest tới được đích thì m cũng tới được.
    """
    js = _jumps(o, U, D, L, R)
    seen = goal | o
    front = goal & ~o
    while front:
        nxt = (((front & U) >> 9) | ((front & D) << 9)
               | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        for m, dest in js:
            if (front & dest) and not (seen & m):
                nxt |= m
        seen |= nxt
        front = nxt
    return seen & ~o


def _blocked(a, o, goal, U, D, L, R, hw, vw, d):
    """True nếu quân a KHÔNG thể tới đích khi quân o đứng yên mãi.

    Luật cho phép đứng yên, nên nếu o đứng đúng "cửa ải" duy nhất thì bên kia
    chỉ có thể hòa -> nhận ra thế "chặn cửa". Kết quả được nhớ theo (o, tường).
    """
    key = (o, hw, vw, goal)
    m = _BCACHE.get(key)
    if m is None:
        m = _reach_mask(o, goal, U, D, L, R)
        _BCACHE[key] = m
    return not (m & a)


def _may_block(a, o, goal_row, d):
    """Điều kiện cần để o chặn được a: o nằm trên một đường ngắn nhất của a.

    Kiểm tra nhanh bằng khoảng cách Manhattan (cận dưới của quãng đường thật).
    """
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
    """Đánh giá tĩnh có xét thế "chặn cửa" (bên đang thắng bị chặn -> hòa)."""
    s = _static(dm, do, wm, wo)
    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if (_may_block(me, op, 0 if gm & 1 else 8, dm)
                    and _blocked(me, op, gm, U, D, L, R, hw, vw, dm)):
                return _draw(gm)
        elif s < 0:
            if (_may_block(op, me, 0 if go & 1 else 8, do)
                    and _blocked(op, me, go, U, D, L, R, hw, vw, do)):
                return _draw(gm)
    return s


def _wall_list(hw, vw):
    """Đổi mặt nạ neo tường sang danh sách bit (dùng khi cần)."""
    out = []
    for mask, base in ((hw, H_BASE), (vw, V_BASE)):
        while mask:
            low = mask & -mask
            out.append(base + low.bit_length() - 1)
            mask ^= low
    return out


# ---------------------------------------------------------------------------
# Sinh nước đi cho một nút
#   Mỗi "con" là tuple:
#   (điểm sắp xếp, mã nước, a', U', D', L', R', hw', vw', wa', d_a', d_b')
#   Điểm sắp xếp = đánh giá tĩnh sau nước đi, theo góc nhìn bên vừa đi.
# ---------------------------------------------------------------------------
def _root_bias(code, child, root_distance, turn, max_turns):
    """Tie-break repeated squares / pass; increase progress preference near turn cap."""
    frac = min(1.0, max(0.0, turn / float(max_turns or 500)))
    urgency = max(0.0, (frac - 0.20) / 0.80)
    if code == PASS:
        return -(1.0 + 0.45 * min(_STAGNANT, 6) + 2.0 * urgency)
    if code < H_BASE:
        child_distance = child[10]
        bias = 0.0
        if code in _ROOT_RECENT and child_distance >= root_distance:
            bias -= 1.0 * (1.0 + urgency)
        progress = root_distance - child_distance
        if progress > 0:
            bias += 2.0 * urgency * progress
        return bias
    return 0.0


def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    """Các nước di chuyển quân. Trả về (mã nước thắng ngay hoặc None, list con)."""
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
        score = -_evalb(db, nd, wb, wa, b, x, gb, ga, U, D, L, R, hw, vw)
        out.append((score, x.bit_length() - 1,
                    x, U, D, L, R, hw, vw, wa, nd, db))
    return None, out


def _pawn_nd(x, ga, U, D, L, R, lay_m, dm):
    """Quãng đường mới của quân sau khi đi tới ô x (tra lớp BFS ngược, như _gen_pawns)."""
    nlay = len(lay_m)
    hi = dm + 3 if dm + 3 <= nlay else nlay
    for k in range(dm - 2 if dm >= 2 else 0, hi):
        if x & lay_m[k]:
            return k
    return _dist(x, ga, U, D, L, R)


def _pawn_children(pm, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    """Như _gen_pawns nhưng dùng danh sách nước đi quân đã có (không ô nào về đích)."""
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    for x in pm:
        nd = -1
        for k in range(lo, hi):
            if x & lay_m[k]:
                nd = k
                break
        if nd < 0:
            nd = _dist(x, ga, U, D, L, R)
        score = -_evalb(db, nd, wb, wa, b, x, gb, ga, U, D, L, R, hw, vw)
        out.append((score, x.bit_length() - 1,
                    x, U, D, L, R, hw, vw, wa, nd, db))
    return out


def _opp_walls(b, gb, U, D, L, R, hw, vw):
    """Danh sách tường ứng viên chống quân b (đối thủ), có bộ nhớ đệm.

    Chỉ phụ thuộc (vị trí b, tường trên bàn) - KHÔNG phụ thuộc quân của mình - nên
    dùng lại được cho rất nhiều nút. Sắp xếp giảm dần theo quãng đường mới của b.
    Trả về (quãng đường hiện tại của b, mảng gọn các số ndo*1024 + mã tường) - lưu
    gọn để bộ nhớ đệm không phình to (mặt nạ tường tính lại khi cần, rất rẻ).
    """
    key = (b, gb, hw, vw)
    ent = _WCACHE.get(key)
    if ent is not None:
        return ent
    do, lay_o, F, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    lst = []
    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            if is_h:
                e1 = w | (w << 1)
                e2 = (w << 9) | (w << 10)
                D2 = D & ~e1
                U2 = U & ~e2
                L2 = L
                R2 = R
                hw2 = hw | w
                vw2 = vw
                code = H_BASE + w.bit_length() - 1
                src_ = (od & e1) | (ou & e2)
            else:
                e1 = w | (w << 9)
                e2 = (w << 1) | (w << 10)
                R2 = R & ~e1
                L2 = L & ~e2
                U2 = U
                D2 = D
                hw2 = hw
                vw2 = vw | w
                code = V_BASE + w.bit_length() - 1
                src_ = (orr & e1) | (ol & e2)
            ndo = _newdist(b, gb, do, lay_o, F, src_, U2, D2, L2, R2, hw2, vw2)
            if ndo >= INF:
                continue
            lst.append((ndo, code))
    lst.sort(key=lambda t: -t[0])
    ent = (do, array("i", [t[0] * 1024 + t[1] for t in lst]))
    _WCACHE[key] = ent
    return ent


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, top, defensive,
               stop_at=None, floor=-WIN * 4):
    """Các bức tường đáng xét (nhanh): dùng danh sách đã sắp xếp của _opp_walls.

    Mẹo cắt chính xác: tường không thể làm mình đi NGẮN hơn, nên điểm của mọi tường
    còn lại <= điểm "nếu mình không bị ảnh hưởng" của tường đang xét (danh sách xếp
    theo quãng đường mới của đối thủ giảm dần) -> dừng sớm mà không sai kết quả.
    """
    if defensive:
        return _gen_walls_full(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm,
                               top, defensive, stop_at)
    do, lst = _opp_walls(b, gb, U, D, L, R, hw, vw)
    if not lst:
        return []
    dm, lay_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    wa2 = wa - 1
    out = []
    best = floor          # (nút sát lá) chỉ cần tường tốt hơn nước tốt nhất đã có
    best_item = None
    thr = -WIN * 4
    for v in lst:
        ndo = v >> 10
        bound = -_static(ndo, dm, wb, wa2)
        if stop_at is not None:
            if bound <= best:
                break
        elif top is not None and len(out) >= top and bound <= thr:
            break
        code = v & 1023
        if code < V_BASE:
            w = 1 << (code - H_BASE)
            e1 = w | (w << 1)
            e2 = (w << 9) | (w << 10)
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
            src_ = (md & e1) | (mu & e2)
        else:
            w = 1 << (code - V_BASE)
            e1 = w | (w << 9)
            e2 = (w << 1) | (w << 10)
            R2 = R & ~e1
            L2 = L & ~e2
            U2 = U
            D2 = D
            hw2 = hw
            vw2 = vw | w
            src_ = (mr & e1) | (ml & e2)
        if src_:
            ndm = _newdist(a, ga, dm, lay_m, Fm, src_, U2, D2, L2, R2, hw2, vw2)
            if ndm >= INF:
                continue
            sc = -_static(ndo, ndm, wb, wa2)
        else:
            ndm = dm
            sc = bound
        item = (sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo)
        if stop_at is not None:
            if sc > best:
                best = sc
                best_item = item
                if sc >= stop_at:
                    break
        else:
            out.append(item)
            if top is not None and len(out) >= top:
                out.sort(reverse=True)
                del out[top:]
                thr = out[-1][0]
    if stop_at is not None:
        return [best_item] if best_item is not None else []
    out.sort(reverse=True)
    if top is not None and len(out) > top:
        del out[top:]
    return out


def _gen_walls_full(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, top, defensive,
                    stop_at=None):
    """Các bức tường đáng xét, đã sắp xếp giảm dần theo lợi ích, tối đa `top`.

    Ứng viên = tường chặn ít nhất một cạnh trên một đường ngắn nhất của đối thủ.
    `defensive`: thêm tường chiếm chỗ những tường đối thủ có thể dùng để chặn mình.
    """
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    lay_o, do = _layers(gb, b, U, D, L, R, hw, vw)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if defensive:
        # Tường "phòng thủ": chiếm trước chỗ (chồng lấn / cắt ngang) của những tường
        # mà đối thủ có thể dùng để chặn đường ngắn nhất của mình.
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    wa2 = wa - 1
    walls = []
    dcache = _DCACHE
    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            if is_h:
                e1 = w | (w << 1)
                e2 = (w << 9) | (w << 10)
                D2 = D & ~e1
                U2 = U & ~e2
                L2 = L
                R2 = R
                touch_me = (md & e1) or (mu & e2)
                touch_op = (od & e1) or (ou & e2)
                hw2 = hw | w
                vw2 = vw
                code = H_BASE + w.bit_length() - 1
            else:
                e1 = w | (w << 9)
                e2 = (w << 1) | (w << 10)
                R2 = R & ~e1
                L2 = L & ~e2
                U2 = U
                D2 = D
                touch_me = (mr & e1) or (ml & e2)
                touch_op = (orr & e1) or (ol & e2)
                hw2 = hw
                vw2 = vw | w
                code = V_BASE + w.bit_length() - 1
            if touch_op:
                ck = (b, gb, hw2, vw2)
                ndo = dcache.get(ck)
                if ndo is None:
                    ndo = _dist(b, gb, U2, D2, L2, R2)
                    dcache[ck] = ndo
                if ndo >= INF:
                    continue
            else:
                ndo = do
            if touch_me:
                ck = (a, ga, hw2, vw2)
                ndm = dcache.get(ck)
                if ndm is None:
                    ndm = _dist(a, ga, U2, D2, L2, R2)
                    dcache[ck] = ndm
                if ndm >= INF:
                    continue
            else:
                ndm = dm
            sc = -_static(ndo, ndm, wb, wa2)
            if stop_at is not None and sc >= stop_at:
                # đủ để cắt tỉa ở nút sát lá: khỏi xét các tường còn lại
                return [(sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo)]
            walls.append((sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
    """Dựng con cho một mã tường cụ thể (từ bảng chuyển vị / killer); None nếu sai luật."""
    if wa <= 0:
        return None
    do, lay_o, Fo, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    dm, lay_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    if code >= V_BASE:
        w = 1 << (code - V_BASE)
        if not (w & ANCHOR) or (w & (vw | (vw << 9) | (vw >> 9) | hw)):
            return None
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        R2 = R & ~e1
        L2 = L & ~e2
        U2, D2, hw2, vw2 = U, D, hw, vw | w
        so = (orr & e1) | (ol & e2)
        sm = (mr & e1) | (ml & e2)
    else:
        w = 1 << (code - H_BASE)
        if not (w & ANCHOR) or (w & (hw | (hw << 1) | (hw >> 1) | vw)):
            return None
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        D2 = D & ~e1
        U2 = U & ~e2
        L2, R2, hw2, vw2 = L, R, hw | w, vw
        so = (od & e1) | (ou & e2)
        sm = (md & e1) | (mu & e2)
    ndo = _newdist(b, gb, do, lay_o, Fo, so, U2, D2, L2, R2, hw2, vw2) if so else do
    if ndo >= INF:
        return None
    ndm = _newdist(a, ga, dm, lay_m, Fm, sm, U2, D2, L2, R2, hw2, vw2) if sm else dm
    if ndm >= INF:
        return None
    return (0, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo)


# ---------------------------------------------------------------------------
# Bảng tàn cuộc (endgame tablebase) khi CẢ HAI đã hết tường
#   Lúc đó bàn cờ cố định, chỉ còn 81 x 81 x 2 = 13122 thế cờ (hai quân + bên đi),
#   nên giải TRỌN được bằng phân tích ngược (retrograde analysis) như tablebase
#   cờ vua: xuất phát từ các thế về đích ngay, lan ngược ra mọi thế khác.
#   Kết quả chính xác tuyệt đối: thắng/thua sau đúng bao nhiêu nước, hay hòa
#   (cả hai đứng chờ "giữ thế đối mặt" - không ai dám lại gần để bị nhảy qua).
#   Giá trị theo góc nhìn bên đi: +k = thắng, nước về đích là nước thứ k (tính cả
#   nước đối thủ); -k = thua, đối thủ về đích ở nước thứ k; 0 = hòa.
#   N = quân có đích hàng 0, S = quân có đích hàng 8; chỉ số = ô_N * 81 + ô_S.
# ---------------------------------------------------------------------------
def _tb_graph(U, D, L, R):
    """Đồ thị nước đi của quân trên một bàn cố định: (láng giềng, nước khi kề quân
    kia (có nhảy), nhảy ngược)."""
    nb = []
    for c in range(81):
        m = 1 << c
        lst = []
        if m & U:
            lst.append(c - 9)
        if m & D:
            lst.append(c + 9)
        if m & L:
            lst.append(c - 1)
        if m & R:
            lst.append(c + 1)
        nb.append(lst)

    def jumps(x, y):
        """Ô đáp khi quân ở x nhảy qua quân ở y (kề nhau), đúng luật arena."""
        my = 1 << y
        dd = y - x
        if dd == -9:
            if my & U:
                return (y - 9,)
            return tuple(c for c, o in ((y - 1, my & L), (y + 1, my & R)) if o)
        if dd == 9:
            if my & D:
                return (y + 9,)
            return tuple(c for c, o in ((y - 1, my & L), (y + 1, my & R)) if o)
        if dd == -1:
            if my & L:
                return (y - 1,)
            return tuple(c for c, o in ((y - 9, my & U), (y + 9, my & D)) if o)
        if my & R:
            return (y + 1,)
        return tuple(c for c, o in ((y - 9, my & U), (y + 9, my & D)) if o)

    adjmv = {}      # nước đi của x khi y đứng kề (gồm cả nhảy)
    rj = {}         # nhảy ngược: (y, ô đáp) -> các ô xuất phát
    for x in range(81):
        for y in nb[x]:
            j = jumps(x, y)
            adjmv[x * 81 + y] = [c for c in nb[x] if c != y] + list(j)
            for z in j:
                rj.setdefault(y * 81 + z, []).append(x)
    return nb, adjmv, rj


def _tb_solve(U, D, L, R):
    nb, adjmv, rj = _tb_graph(U, D, L, R)
    NN = 6561
    valN = [0] * NN
    valS = [0] * NN
    cntN = [0] * NN
    cntS = [0] * NN
    queue = []
    qa = queue.append
    # khởi tạo: thế về đích ngay (thắng sau 1 nước) và số nước đi của mỗi thế
    for n in range(9, 81):
        base = nb[n]
        bwin = n < 18 and (n - 9) in base
        bcnt = len(base) + 1
        nadj = set(base)
        off = n * 81
        for s in range(0, 72):
            if s == n:
                continue
            i = off + s
            if s in nadj:
                mv = adjmv[n * 81 + s]
                if any(c < 9 for c in mv):
                    valN[i] = 1
                    qa(i)
                else:
                    cntN[i] = len(mv) + 1
            elif bwin:
                valN[i] = 1
                qa(i)
            else:
                cntN[i] = bcnt
    for s in range(0, 72):
        base = nb[s]
        bwin = s >= 63 and (s + 9) in base
        bcnt = len(base) + 1
        sadj = set(base)
        for n in range(9, 81):
            if n == s:
                continue
            i = n * 81 + s
            if n in sadj:
                mv = adjmv[s * 81 + n]
                if any(c >= 72 for c in mv):
                    valS[i] = 1
                    qa(NN + i)
                else:
                    cntS[i] = len(mv) + 1
            elif bwin:
                valS[i] = 1
                qa(NN + i)
            else:
                cntS[i] = bcnt
    # lan ngược theo thứ tự quãng đường tăng dần (BFS): thế nào có MỘT nước dẫn tới
    # thế thua của đối thủ là thắng; thế nào MỌI nước (kể cả đứng yên) đều dẫn tới
    # thế thắng của đối thủ là thua; còn lại là hòa.
    head = 0
    while head < len(queue):
        q = queue[head]
        head += 1
        if q < NN:
            i = q
            v = valN[i]
            n, s = divmod(i, 81)
            off = n * 81
            extra = rj.get(n * 81 + s)
            lst = nb[s] + [s] if extra is None else nb[s] + [s] + extra
            for sp in lst:
                if sp == n or sp >= 72:
                    continue
                j = off + sp
                if valS[j]:
                    continue
                if v < 0:
                    valS[j] = 1 - v
                    qa(NN + j)
                else:
                    c = cntS[j] - 1
                    cntS[j] = c
                    if c == 0:
                        valS[j] = -1 - v
                        qa(NN + j)
        else:
            i = q - NN
            v = valS[i]
            n, s = divmod(i, 81)
            extra = rj.get(s * 81 + n)
            lst = nb[n] + [n] if extra is None else nb[n] + [n] + extra
            for np_ in lst:
                if np_ == s or np_ < 9:
                    continue
                j = np_ * 81 + s
                if valN[j]:
                    continue
                if v < 0:
                    valN[j] = 1 - v
                    qa(j)
                else:
                    c = cntN[j] - 1
                    cntN[j] = c
                    if c == 0:
                        valN[j] = -1 - v
                        qa(j)
    return array("h", valN), array("h", valS)


def _reach(goal, U, D, L, R):
    """Các ô có đường tới hàng đích (bỏ qua quân - đúng luật kiểm tra tường của arena)."""
    seen = front = goal
    while front:
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        seen |= front
    return seen


def _walls_of(U, D, L, R, hw, vw):
    """Mọi chỗ đặt tường không chồng/cắt tường cũ: (hw', vw', U', D', L', R')."""
    out = []
    m = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    while m:
        w = m & -m
        m ^= w
        out.append((hw | w, vw, U & ~((w << 9) | (w << 10)), D & ~(w | (w << 1)), L, R))
    m = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    while m:
        w = m & -m
        m ^= w
        out.append((hw, vw | w, U, D, L & ~((w << 1) | (w << 10)), R & ~(w | (w << 9))))
    return out


def _tb1_build(U, D, L, R, hw, vw, hN, until):
    """Dựng bảng "1 tường vs 0 tường" (bên H còn 1 tường, bên kia hết) cho bàn hiện tại.

    Trước hết cần bảng 0-0 của MỌI bàn sau khi H đặt nốt bức tường cuối (dựng dần,
    có thể qua nhiều lượt, dừng khi hết thời gian `until`). Sau đó phân tích ngược
    một lần nữa: ngoài đi quân / đứng yên, bên H còn "lối ra" là đặt tường w ->
    giá trị lấy từ bảng 0-0 của bàn có thêm w. Trả về True nếu đã dựng xong.
    """
    ws = _walls_of(U, D, L, R, hw, vw)
    for hw2, vw2, U2, D2, L2, R2 in ws:
        if (hw2, vw2) not in _TBC:
            if perf_counter() > until:
                return False
            _TBC[(hw2, vw2)] = _tb_solve(U2, D2, L2, R2)
    if perf_counter() > until:
        return False
    nb, adjmv, rj = _tb_graph(U, D, L, R)
    NN = 6561
    val = [[0] * NN, [0] * NN]      # [N đi, S đi]
    cnt = [[0] * NN, [0] * NN]
    buckets = {}

    def push(d, ev):
        bk = buckets.get(d)
        if bk is None:
            buckets[d] = [ev]
        else:
            bk.append(ev)

    for n in range(9, 81):
        base = nb[n]
        bwin = n < 18 and (n - 9) in base
        bcnt = len(base) + 1
        nadj = set(base)
        off = n * 81
        for s in range(0, 72):
            if s == n:
                continue
            i = off + s
            if s in nadj:
                mv = adjmv[n * 81 + s]
                if any(c < 9 for c in mv):
                    push(1, (0, i, 1))
                else:
                    cnt[0][i] = len(mv) + 1
            elif bwin:
                push(1, (0, i, 1))
            else:
                cnt[0][i] = bcnt
    for s in range(0, 72):
        base = nb[s]
        bwin = s >= 63 and (s + 9) in base
        bcnt = len(base) + 1
        sadj = set(base)
        for n in range(9, 81):
            if n == s:
                continue
            i = n * 81 + s
            if n in sadj:
                mv = adjmv[s * 81 + n]
                if any(c >= 72 for c in mv):
                    push(1, (1, i, 1))
                else:
                    cnt[1][i] = len(mv) + 1
            elif bwin:
                push(1, (1, i, 1))
            else:
                cnt[1][i] = bcnt
    # lối ra: H đặt bức tường cuối -> thế 0-0 trên bàn mới, bên kia đi
    hs = 0 if hN else 1
    cH = cnt[hs]
    decs = {}
    for hw2, vw2, U2, D2, L2, R2 in ws:
        rN = _reach(ROW[0], U2, D2, L2, R2)
        rS = _reach(ROW[8], U2, D2, L2, R2)
        t = _TBC[(hw2, vw2)][1 - hs]
        for n in range(9, 81):
            if not (rN >> n) & 1:
                continue
            off = n * 81
            for s in range(0, 72):
                if s == n or not (rS >> s) & 1:
                    continue
                i = off + s
                if cH[i] == 0:
                    continue          # H về đích ngay được rồi
                cH[i] += 1            # thêm một lựa chọn: H chỉ thua nếu MỌI lựa chọn đều thua
                v = t[i]
                if v < 0:
                    push(1 - v, (hs, i, 1 - v))
                elif v > 0:
                    dl = decs.get(v)
                    if dl is None:
                        decs[v] = [i]
                    else:
                        dl.append(i)
    valN, valS = val
    cntN, cntS = cnt
    j = 1
    while buckets or decs:
        evs = buckets.pop(j, None)
        if evs:
            k = 0
            while k < len(evs):
                side, i, v = evs[k]
                k += 1
                arr = val[side]
                if arr[i]:
                    continue
                arr[i] = v
                n, s = divmod(i, 81)
                if side == 0:
                    off = n * 81
                    extra = rj.get(n * 81 + s)
                    lst = nb[s] + [s] if extra is None else nb[s] + [s] + extra
                    for sp in lst:
                        if sp == n or sp >= 72:
                            continue
                        jj = off + sp
                        if valS[jj]:
                            continue
                        if v < 0:
                            push(1 - v, (1, jj, 1 - v))
                        else:
                            c = cntS[jj] - 1
                            cntS[jj] = c
                            if c == 0:
                                push(v + 1, (1, jj, -1 - v))
                else:
                    extra = rj.get(s * 81 + n)
                    lst = nb[n] + [n] if extra is None else nb[n] + [n] + extra
                    for np_ in lst:
                        if np_ == s or np_ < 9:
                            continue
                        jj = np_ * 81 + s
                        if valN[jj]:
                            continue
                        if v < 0:
                            push(1 - v, (0, jj, 1 - v))
                        else:
                            c = cntN[jj] - 1
                            cntN[jj] = c
                            if c == 0:
                                push(v + 1, (0, jj, -1 - v))
        dl = decs.pop(j, None)
        if dl:
            arr = val[hs]
            for i in dl:
                if arr[i]:
                    continue
                c = cH[i] - 1
                cH[i] = c
                if c == 0:
                    push(j + 1, (hs, i, -(j + 1)))
        j += 1
    _TB1C[(hw, vw, hN)] = (array("h", valN), array("h", valS))
    return True


def _tb_score(v, ply, ga, da, db, turn, max_turns):
    """Đổi giá trị bảng tàn cuộc sang điểm tìm kiếm (theo góc nhìn bên đi)."""
    if v > 0:
        if turn + ply + v - 1 < max_turns:
            return WIN - (ply + v - 1)
    elif v < 0:
        if turn + ply - v - 1 < max_turns:
            return -(WIN - (ply - v - 1))
    # hòa: thêm chút ưu tiên cho bên đang dẫn trong cuộc đua (để còn cơ hội nếu
    # đối thủ đi sai), nhưng không bao giờ vượt quá +-4 quanh điểm hòa
    t = db - da
    if t > 4:
        t = 4
    elif t < -4:
        t = -4
    return _draw(ga) + t


# ---------------------------------------------------------------------------
# Tìm kiếm Negamax + Alpha-Beta (PVS, bảng chuyển vị, killer move)
# ---------------------------------------------------------------------------
class _Searcher:
    def __init__(self, deadline, turn, max_turns):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        self.killers = [[None, None] for _ in range(128)]
        self.path = set()
        self.tb_left = TB_MAX if TB else 0
        self.tb_until = deadline - TIME_LIMIT * (1.0 - TB_TIME)

    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply):
        self.nodes += 1
        if (self.nodes & 7) == 0 and perf_counter() > self.deadline:
            raise _TimeUp()
        if self.turn + ply >= self.max_turns:
            return _draw(ga)               # hết giới hạn lượt -> hòa
        if (wa == 0 or wb == 0) and TB:
            # Bảng tàn cuộc: hết tường cả hai -> giá trị CHÍNH XÁC. Chỉ một bên hết
            # tường -> bên còn tường ít nhất cũng đạt được giá trị của bảng (cứ việc
            # không đặt tường nữa) -> dùng làm cận để cắt tỉa.
            both = wa == 0 and wb == 0
            exact = both
            t = None
            if not both and _TB1C:
                # bảng "1 tường vs 0": chính xác nếu bên còn tường còn đúng 1 tường,
                # còn nhiều hơn thì là cận (bên đó ít nhất làm được như khi chỉ dùng 1)
                t = _TB1C.get((hw, vw, (ga if wa else gb) & 1))
                if t is not None:
                    exact = wa + wb == 1
            if t is None:
                t = _TBC.get((hw, vw))
                if (t is None and self.tb_left > 0 and (both or ply <= TB_BOUND_PLY)
                        and perf_counter() < self.tb_until):
                    t = _tb_solve(U, D, L, R)
                    _TBC[(hw, vw)] = t
                    self.tb_left -= 1
            if t is not None:
                ia = a.bit_length() - 1
                ib = b.bit_length() - 1
                v = t[0][ia * 81 + ib] if ga & 1 else t[1][ib * 81 + ia]
                sc = _tb_score(v, ply, ga, da, db, self.turn, self.max_turns)
                if exact:
                    return sc
                if wb == 0:                # mình còn tường: sc là cận dưới
                    if sc >= beta or sc > MATE_ZONE:
                        return sc
                elif sc <= alpha or sc < -MATE_ZONE:   # mình hết tường: cận trên
                    return sc
        if depth <= 0:
            return _static(da, db, wa, wb)

        key = (a, b, wa, wb, hw, vw, ga)
        if key in self.path or key in _HIST:
            return _draw(ga)               # lặp lại thế cũ -> coi như hòa
        entry = _TT.get(key)
        if entry is None and TT2:
            entry = _TT_OLD.get(key)
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

        # các lớp BFS ngược tới đích của mình (theo bộ tường); quãng đường của mình
        # đã được tính chính xác ở nút cha nên khỏi tra lại
        lay_m = _LCACHE.get((ga, hw, vw))
        if lay_m is None:
            lay_m = _layers(ga, a, U, D, L, R, hw, vw)[0]
        dm = da
        if depth == 1 and not _BLOCK[0]:
            # Nút sát lá, chưa cần kiểm tra "chặn cửa": điểm của nước đi quân chỉ phụ
            # thuộc quãng đường mới -> chỉ cần tìm nước làm quãng đường ngắn nhất
            # (cùng kết quả với cách tính đầy đủ bên dưới, nhanh hơn).
            nlay = len(lay_m)
            lo = dm - 2 if dm >= 2 else 0
            hi = dm + 3 if dm + 3 <= nlay else nlay
            bnd = INF + 1
            bx = 0
            for x in _pawn_moves(a, b, U, D, L, R):
                if x & ga:
                    return WIN - ply       # về đích ngay
                nd = -1
                for k in range(lo, hi):
                    if x & lay_m[k]:
                        nd = k
                        break
                if nd < 0:
                    nd = _dist(x, ga, U, D, L, R)
                if nd < bnd:
                    bnd = nd
                    bx = x
            best = -_static(db, da, wb, wa)
            best_move = PASS
            if bx:
                sc = -_static(db, bnd, wb, wa)
                if sc > best:
                    best = sc
                    best_move = bx.bit_length() - 1
            pawns = None
        elif depth == 1:
            win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
            if win is not None:
                return WIN - ply               # về đích ngay
        else:
            # Nút trong: chỉ sinh danh sách nước đi quân; các "con" (kèm đánh giá để
            # sắp xếp) chỉ dựng khi thật sự tới lượt xét - phần lớn nút bị cắt tỉa
            # ngay ở nước trong bảng chuyển vị nên khỏi tốn công.
            pm = _pawn_moves(a, b, U, D, L, R)
            for x in pm:
                if x & ga:
                    return WIN - ply           # về đích ngay
            pawns = None
        if depth == 1:
            # Nút sát lá: giá trị = nước tốt nhất theo đánh giá tĩnh.
            if pawns is not None:
                best = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw)
                best_move = PASS
                for k in pawns:
                    if k[0] > best:
                        best = k[0]
                        best_move = k[1]
            if best < beta and wa > 0:
                # thử trước tường đã nhớ (bảng chuyển vị / killer): hay cắt tỉa được ngay
                if FRONTIER_KILLERS:
                    for km in (tt_move, self.killers[ply][0], self.killers[ply][1]):
                        if km is not None and km >= H_BASE and km != PASS:
                            c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                sc = -_static(c[11], c[10], wb, c[9])
                                if sc > best:
                                    best = sc
                                    best_move = km
                                    if best >= beta:
                                        break
                if best < beta:
                    walls = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                       lay_m, dm, 1, False, beta, best)
                    if walls and walls[0][0] > best:
                        best = walls[0][0]
                        best_move = walls[0][1]
            if best >= beta and best_move is not None and best_move >= H_BASE and best_move != PASS:
                kl = self.killers[ply]
                if kl[0] != best_move:
                    kl[1] = kl[0]
                    kl[0] = best_move
            _TT[key] = (1, _to_tt(best, ply), 1 if best >= beta else 0, best_move)
            return best

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
            # Thứ tự xét: nước trong bảng chuyển vị -> killer -> di chuyển -> tường -> đứng yên
            if stage == 0:
                lst = []
                if tt_move is not None:
                    if tt_move == PASS:
                        lst = [(0, PASS, a, U, D, L, R, hw, vw, wa, da, db)]
                    elif tt_move < H_BASE:
                        x = 1 << tt_move
                        if x in pm:
                            lst = [(0, tt_move, x, U, D, L, R, hw, vw, wa,
                                    _pawn_nd(x, ga, U, D, L, R, lay_m, dm), db)]
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
                lst = _pawn_children(pm, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
                lst.sort(reverse=True)
            elif stage == 3:
                lst = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm,
                                 TOP_WALLS, False) if wa > 0 else ()
            else:
                lst = [(0, PASS, a, U, D, L, R, hw, vw, wa, da, db)]
            stage += 1
            for k in lst:
                code = k[1]
                if code in tried:
                    continue
                tried.add(code)
                if first:
                    sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1)
                    first = False
                else:
                    sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha, np1)
                    if alpha < sc < beta:
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                          k[6], k[7], k[8], k[11], k[10], nd, -beta,
                                          -alpha, np1)
                if sc > best:
                    best = sc
                    best_move = code
                    if sc > alpha:
                        alpha = sc
                        if alpha >= beta:
                            cutoff = True
                            if code >= H_BASE and code != PASS:
                                if killers[0] != code:
                                    killers[1] = killers[0]
                                    killers[0] = code
                            break
        self.path.discard(key)
        if best <= orig_alpha:
            flag = 2
        elif best >= beta:
            flag = 1
        else:
            flag = 0
        _TT[key] = (depth, _to_tt(best, ply), flag, best_move)
        return best


def _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0):
    """Đào sâu lặp ở gốc; trả về mã nước đi tốt nhất."""
    da = _dist(a, ga, U, D, L, R)
    db = _dist(b, gb, U, D, L, R)
    lay_m, dm = _layers(ga, a, U, D, L, R, hw, vw)
    win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
    if win is not None:
        return win, None
    if wa > 0:
        kids.extend(_gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                               lay_m, dm, None, True))
    kids.append((-_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw) - 1, PASS,
                 a, U, D, L, R, hw, vw, wa, da, db))
    kids.sort(key=lambda k: -k[0])
    best_move = kids[0][1]
    _STATE["depth"] = 1
    _STATE["score"] = kids[0][0]
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
                    sc = -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                   k[7], k[8], k[11], k[10], depth - 1, -beta, -alpha, 1)
                    first = False
                else:
                    sc = -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                   k[7], k[8], k[11], k[10], depth - 1, -alpha - 1,
                                   -alpha, 1)
                    if sc > alpha:
                        # Đã chắc chắn tốt hơn nước tốt nhất hiện tại: ghi nhận
                        # ngay, phòng khi hết giờ trong lúc tính lại cho chính xác.
                        prev_best, prev_sc = cur_best, cur_best_sc
                        cur_best, cur_best_sc = k[1], sc
                        sc = -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                       k[6], k[7], k[8], k[11], k[10], depth - 1,
                                       -beta, -alpha, 1)
                        cur_best, cur_best_sc = prev_best, prev_sc
                sc += _root_bias(k[1], k, dm, turn, max_turns)
                new_scores[k[1]] = sc
                if sc > cur_best_sc:
                    cur_best_sc = sc
                    cur_best = k[1]
                    if sc > alpha:
                        alpha = sc
        except _TimeUp:
            # Lượt lặp dở dang: nước đầu tiên (tốt nhất cũ) đã xét xong thì dùng
            # kết quả tốt nhất tìm được trong lượt lặp này.
            if cur_best is not None:
                best_move = cur_best
            break
        best_move = cur_best
        scores = new_scores
        _STATE["depth"] = depth
        _STATE["nodes"] = s.nodes
        _STATE["score"] = cur_best_sc
        # Lượt lặp sau: nước tốt nhất lên đầu, còn lại theo điểm.
        kids.sort(key=lambda k: -scores.get(k[1], -WIN * 3))
        if abs(cur_best_sc) > MATE_ZONE and WIN - abs(cur_best_sc) <= depth:
            break  # thắng/thua chắc chắn đã được chứng minh trong tầm tìm kiếm
        now = perf_counter()
        if now + (now - it_start) * ITER_FACTOR > deadline:
            break  # không đủ thời gian cho độ sâu tiếp theo
    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None


# ---------------------------------------------------------------------------
# Giao tiếp với arena
# ---------------------------------------------------------------------------
def _bit(rc):
    return 1 << (rc[0] * 9 + rc[1])


def _fallback(observation):
    """Phương án dự phòng: đi theo bản đồ khoảng cách của arena."""
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
    global _TT, _TT_OLD
    you = observation["you"]
    opp = observation["opponent"]
    a = _bit(you["position"])
    b = _bit(opp["position"])
    ga = ROW[you["goal_row"]]
    gb = ROW[opp["goal_row"]]
    wa = you["walls_remaining"]
    wb = opp["walls_remaining"]
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
    wa = max(0, min(10, wa))
    wb = max(0, min(10, wb))

    turn = observation.get("turn", 0)
    if turn < _STATE["last_turn"]:
        _TT.clear()          # ván mới
        _TT_OLD.clear()
        _HIST.clear()
    elif len(_TT) > TT_CAP:
        # bảng đầy: chuyển sang "thế hệ cũ" thay vì xóa sạch, vẫn tra cứu được
        _TT_OLD = _TT if TT2 else {}
        _TT = {}
    _STATE["last_turn"] = turn
    max_turns = observation.get("max_turns", 500)

    _BLOCK[0] = (bin(hw).count("1") + bin(vw).count("1")) >= BLOCK_MIN_WALLS
    if len(_BCACHE) > 20000:
        _BCACHE.clear()      # kết quả "chặn cửa" chỉ phụ thuộc (quân chặn, tường): giữ qua các lượt
    # giới hạn kích thước các bộ nhớ đệm (giữ tiến trình quanh ~100 MB)
    if len(_DCACHE) > 60000:
        _DCACHE.clear()
    if len(_LCACHE) > 10000:
        _LCACHE.clear()
    if len(_WCACHE) > 20000:
        _WCACHE.clear()
    if len(_GCACHE) > 10000:
        _GCACHE.clear()
    if _TBC:
        # tường không bao giờ bị gỡ: bảng nào thiếu một bức tường đang có trên bàn
        # thì không còn dùng được nữa
        for k in [k for k in _TBC if (k[0] & hw) != hw or (k[1] & vw) != vw]:
            del _TBC[k]
    if _TB1C:
        for k in [k for k in _TB1C if (k[0] & hw) != hw or (k[1] & vw) != vw]:
            del _TB1C[k]
    _ROOT_GOAL[0] = ga
    if TB and TB1 and (wa == 0) != (wb == 0) and wa + wb <= TB1_MAXK:
        hN = (ga if wa else gb) & 1
        if (hw, vw, hN) not in _TB1C:
            _tb1_build(U, D, L, R, hw, vw, hN, t0 + TIME_LIMIT * TB1_TIME)
    _HIST.add((a, b, wa, wb, hw, vw, ga))
    code, kid = _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn,
                             max_turns, t0)
    if kid is not None:
        # ghi nhớ thế cờ sau nước đi của mình (đối thủ tới lượt)
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
            if (w["orientation"] == action["orientation"] and w["row"] == action["row"]
                    and w["col"] == action["col"]):
                return True
    return False


def _base_choose_action(observation):
    t0 = perf_counter()
    action = None
    try:
        _STATE["moves"] += 1
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


def choose_action(observation):
    """Arena entry point with light root anti-shuffle and anti-pass memory."""
    global _LAST_MY_POS, _LAST_BOARD_SIG, _STAGNANT, _LAST_OBS_TURN
    try:
        turn = int(observation.get("turn", 0))
        me = observation["you"]
        op = observation["opponent"]
        pos_code = int(me["position"][0]) * 9 + int(me["position"][1])
        if turn == 0 or (_LAST_OBS_TURN >= 0 and turn < _LAST_OBS_TURN):
            _ROOT_RECENT.clear()
            _LAST_MY_POS = None
            _LAST_BOARD_SIG = None
            _STAGNANT = 0
        if _LAST_MY_POS is not None:
            _ROOT_RECENT.append(_LAST_MY_POS)
            if len(_ROOT_RECENT) > 6:
                del _ROOT_RECENT[0]
        walls = observation["walls"]
        signature = (tuple(me["position"]), tuple(op["position"]),
                     tuple(tuple(x) for x in walls["horizontal"]),
                     tuple(tuple(x) for x in walls["vertical"]),
                     int(me["walls_remaining"]), int(op["walls_remaining"]))
        if signature == _LAST_BOARD_SIG:
            _STAGNANT = min(8, _STAGNANT + 1)
        else:
            _STAGNANT = 0
        _LAST_BOARD_SIG = signature
        _LAST_MY_POS = pos_code
        _LAST_OBS_TURN = turn
    except Exception:
        pass
    return _base_choose_action(observation)
