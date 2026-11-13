

import gc
from array import array
from collections import deque
from time import perf_counter

# Tắt GC tự động (một lần quét toàn bộ cache có thể mất hàng trăm ms giữa lúc tìm
# kiếm); chỉ gọi gc.collect(0) nhẹ ở đầu mỗi lượt.
gc.disable()

# ---------------------------------------------------------------------------
# 1. HẰNG SỐ BITBOARD
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

# NEAR2[i]: các ô cách ô i không quá 2 bước Manhattan (vùng có thể nhảy qua đầu nhau)
NEAR2 = []
NEAR1 = []
for _i in range(81):
    _m2 = 0
    _m1 = 0
    for _j in range(81):
        _md = abs(_i // 9 - _j // 9) + abs(_i % 9 - _j % 9)
        if _md <= 2:
            _m2 |= 1 << _j
        if _md == 1:
            _m1 |= 1 << _j
    NEAR2.append(_m2)
    NEAR1.append(_m1)

# Neo tường sát biên (thưởng "nối tường" khi sắp xếp)
_BORDER_H = sum(1 << (9 * r + c) for r in range(8) for c in (0, 7))
_BORDER_V = sum(1 << (9 * r + c) for r in (0, 7) for c in range(8))

# Mã nước đi: 0..80 = đi tới ô; 100+a = tường H tại neo a; 200+a = tường V; 999 = PASS
H_BASE = 100
V_BASE = 200
PASS = 999
NCODES = 1000

# ---------------------------------------------------------------------------
# 2. THAM SỐ ĐÁNH GIÁ & TÌM KIẾM (10 đơn vị = 1 bước)
# ---------------------------------------------------------------------------
STEP = 10
TEMPO = 5
RACE_BONUS = 60
_WALL_MARGINAL = [24, 18, 14, 12, 12, 12, 12, 12, 12, 12]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]
WALL_ECON = 5               # kinh tế tường có điều kiện (xem _decide): tường của mình +0.5 bước mỗi bức
                            # khi mình không bị dẫn; tường đối thủ luôn giữ thang chuẩn
WALL_DISPARITY = 0          # phạt/thưởng thêm mỗi tường chênh lệch vượt quá 2 (giữ tường dự trữ)
LEAD_CAP = 3                # dẫn quá (LEAD_CAP + tường bên bị dẫn) bước thì phần dư bị nén
LEAD_SLOPE_PCT = 40         # phần dư chỉ còn 40% giá trị
# Độc quyền tường: bên hết tường không còn cách đáp trả, nên mỗi tường của bên kia
# đáng giá hơn bình thường (đọc log: dẫn +12, 0 tường vs 6 tường vẫn thua).
MONO_BASE = 12
MONO_PER = 6
_MONO = [0] + [MONO_BASE + MONO_PER * k for k in range(1, 11)]
THREAT_SCALE = 4           # giá trị thêm của bức tường đầu tiên theo wall_damage
THREAT_MIN_DMG = 2
THREAT_CAP = 16
MULTI_PATH_BONUS = 2
BOTTLENECK_PENALTY = 1

TIME_LIMIT = 0.80
ITER_FRACTION = 0.35        # bắt đầu độ sâu mới nếu còn >= 35% thời gian dự phóng
ITER_PARTIAL = 0.30         # dù dự phóng thiếu, vẫn đào tiếp nếu còn >= 30% thời gian lượt lặp vừa rồi
VOLATILE_SWING = 15         # điểm dao động > 15: cố đào tiếp để tránh hiệu ứng chân trời
ASP_DELTA = 30
ASP_GROW = 2.2
NMP_R = 2
NMP_MARGIN = 18
QS_DEPTH = 1                # số ply quiescence (chỉ đi quân / nhảy) khi hai quân kề nhau
QS_RADIUS = 1               # 1: chỉ khi kề nhau (nhảy được ngay); 2: Manhattan <= 2
QS_WALLS = 0                # 1: cả tường ở nút sát lá cũng qua quiescence (đắt)
FRONTIER_WALLS = 6          # số tường xét bằng quiescence ở nút sát lá khi hai quân sát nhau
COUNTER_THREATS = 2         # số tường đe dọa mạnh nhất của đối thủ được "chiếm chỗ" trước
COUNTER_MIN_DMG = 2
COUNTER_TRIGGER = 3
COUNTER_MAX_PLY = 2         # tường chéo chỉ sinh ở các nút gần gốc (đo được: sinh ở mọi nút làm cây phình to)
LMR_MIN_INDEX = 3
WIN = 100000
MATE_ZONE = WIN - 1000
MAX_PLY = 128

ROOT_PASS_PENALTY = 4
ROOT_REPEAT_PENALTY = 4
ROOT_RECENT_WINDOW = 6
ROOT_THREAT_CAP = 6
URG_START = 0.2             # sau 20% số lượt tối đa bắt đầu "sốt ruột"
URG_BONUS = 20              # thưởng / bước tiến về đích ở gốc khi sắp hết lượt
CONTEMPT_BASE = 25          # hòa chỉ được 1 điểm (thắng 3): hòa bị coi là xấu cho mình
CONTEMPT_URG = 30

BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 15

TB_MAX = 12                 # số bảng 0-0 mới tối đa được dựng trong cây tìm kiếm mỗi lượt
TB_TIME = 0.45              # chỉ dựng bảng trong 45% đầu thời gian lượt
TB_BOUND_PLY = 2
TB1_MAXK = 2
TB1_TIME = 0.5
TT_CAP = 120000
_QSN = NEAR1 if QS_RADIUS == 1 else NEAR2
FEAT_HIST_DRAW = 1          # thế cờ đã gặp trong ván / trên đường tìm kiếm = hòa
FEAT_COUNTERS = 1           # tường phòng thủ chéo ở nút trong

# ---------------------------------------------------------------------------
# Bộ nhớ xuyên lượt
# ---------------------------------------------------------------------------
_TT = {}
_TT_OLD = {}
_HISTORY = [0] * NCODES
_CM = [0] * NCODES
_HIST = set()               # thế cờ đã xuất hiện trong ván
_RECENT = []                # (lượt, ô, hw, vw) các ô mình vừa đứng
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0, "goals": None,
          "depth": 0, "score": 0, "nodes": 0, "mode": ""}
_ROOT_GOAL = [0]
_CONT = [CONTEMPT_BASE]
_BLOCK = [False]
_WVS = [list(WALL_VALUE), list(WALL_VALUE)]   # _WVS[g][k]: giá trị k tường của bên có đích g

_LCACHE = {}    # (đích, hw, vw) -> (lớp BFS ngược, hợp các lớp)
_GCACHE = {}    # (ô, đích, hw, vw) -> DAG
_DCACHE = {}    # (ô, đích, hw, vw) -> quãng đường (sau BFS sửa chữa)
_WCACHE = {}    # (ô nạn nhân, đích, hw, vw) -> danh sách tường làm nạn nhân đi xa hơn
_BCACHE = {}    # chặn cửa: (o, hw, vw, đích) -> tập ô tới được đích
_BQ = {}        # chặn cửa: (a, o, hw, vw, đích) -> bool
_TBC = {}       # bảng tàn cuộc 0-0 theo (hw, vw)
_TB1C = {}      # bảng 1-0 theo (hw, vw, bên còn tường là N?)
_EG_VISITS = {}


class _TimeUp(Exception):
    pass


def _to_tt(score, ply):
    if score > MATE_ZONE:
        return score + ply
    if score < -MATE_ZONE:
        return score - ply
    return score


def _draw(goal):
    """Điểm hòa theo góc nhìn bên có đích `goal` đang tới lượt (đối xứng negamax)."""
    return -_CONT[0] if goal == _ROOT_GOAL[0] else _CONT[0]


def _popcount(x):
    return bin(x).count("1")


# ---------------------------------------------------------------------------
# 3. LÕI ĐỒ THỊ: BFS NGƯỢC, DAG, BFS SỬA CHỮA
# ---------------------------------------------------------------------------
def _dist(start, goal, U, D, L, R):
    """BFS thuần từ ô `start` tới hàng `goal` (dùng khi không có cache)."""
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


def _layers(goal, U, D, L, R, hw, vw):
    """Các lớp BFS ngược từ hàng đích (lay[k] = ô cách đích k bước) + hợp tiền tố."""
    key = (goal, hw, vw)
    ent = _LCACHE.get(key)
    if ent is None:
        lay = [goal]
        sns = [goal]
        seen = front = goal
        while True:
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            if not front:
                break
            seen |= front
            lay.append(front)
            sns.append(seen)
        ent = (lay, sns)
        _LCACHE[key] = ent
    return ent


def _lindex(lay, x):
    k = 0
    for f in lay:
        if f & x:
            return k
        k += 1
    return INF


def _cut_mask(su, sd, sl, sr):
    """Neo tường H, V chặn ít nhất một cạnh trong (su, sd, sl, sr) - O(1) bằng dịch bit.

    Tường H tại neo w chặn cạnh xuống của ô w, w+1 và cạnh lên của ô w+9, w+10.
    Tường V tại neo w chặn cạnh phải của ô w, w+9 và cạnh trái của ô w+1, w+10.
    """
    ch = ((su >> 9) | (su >> 10) | sd | (sd >> 1)) & ANCHOR
    cv = (sr | (sr >> 9) | (sl >> 1) | (sl >> 10)) & ANCHOR
    return ch, cv


def _dag(pos, goal, U, D, L, R, hw, vw):
    """DAG đường ngắn nhất của quân ở `pos`: (d, lay, sns, F, up, dn, lt, rt)."""
    key = (pos, goal, hw, vw)
    ent = _GCACHE.get(key)
    if ent is not None:
        return ent
    lay, sns = _layers(goal, U, D, L, R, hw, vw)
    d = _lindex(lay, pos)
    if d >= INF:
        ent = (INF, lay, sns, None, 0, 0, 0, 0)
        _GCACHE[key] = ent
        return ent
    up = dn = lt = rt = 0
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
    ent = (d, lay, sns, F, up, dn, lt, rt)
    _GCACHE[key] = ent
    return ent


def _newdist(pos, goal, d, lay, sns, F, src, wt, U2, D2, L2, R2, hw2, vw2):
    """Quãng đường mới sau khi đặt tường cắt các cạnh DAG xuất phát từ `src`.

    Bước 1 (không BFS): lan truyền trong DAG qua các lớp bị cắt; còn một đường ngắn
    nhất nguyên vẹn -> trả về d ngay.
    Bước 2 (chỉ khi mọi đường bị cắt): BFS sửa chữa tiếp từ lớp đầu tiên chạm tường
    (`wt` = 4 ô kề tường); các lớp gần đích hơn không đổi.
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
    if nd is not None:
        return nd
    k = 0
    n = len(lay)
    while k < n and not (lay[k] & wt):
        k += 1
    if k >= n or k >= d:
        nd = _dist(pos, goal, U2, D2, L2, R2)
    else:
        front = lay[k]
        seen = sns[k]
        nd = k
        while True:
            front = (((front & U2) >> 9) | ((front & D2) << 9)
                     | ((front & L2) >> 1) | ((front & R2) << 1)) & ~seen
            if not front:
                nd = INF
                break
            nd += 1
            if front & pos:
                break
            seen |= front
    _DCACHE[ck] = nd
    return nd


def _wall_masks(code, U, D, L, R, hw, vw):
    """Áp tường `code`: (U2, D2, L2, R2, hw2, vw2, w, e1, e2) hoặc None nếu chồng/cắt."""
    if code >= V_BASE:
        w = 1 << (code - V_BASE)
        if not (w & ANCHOR) or (w & (vw | (vw << 9) | (vw >> 9) | hw)):
            return None
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        return U, D, L & ~e2, R & ~e1, hw, vw | w, w, e1, e2
    w = 1 << (code - H_BASE)
    if not (w & ANCHOR) or (w & (hw | (hw << 1) | (hw >> 1) | vw)):
        return None
    e1 = w | (w << 1)
    e2 = (w << 9) | (w << 10)
    return U & ~e2, D & ~e1, L, R, hw | w, vw, w, e1, e2


def _pawn_moves(a, b, U, D, L, R):
    """Các ô quân `a` đi được khi đối thủ ở `b` (đúng luật nhảy / đi chéo của Arena)."""
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


def _can_win_now(a, b, ga, U, D, L, R):
    for x in _pawn_moves(a, b, U, D, L, R):
        if x & ga:
            return True
    return False


# ---------------------------------------------------------------------------
# 4. HÀM ĐÁNH GIÁ
# ---------------------------------------------------------------------------
def _lead_value(lead, trail_walls):
    cap = LEAD_CAP + trail_walls
    if lead > cap:
        return STEP * cap + (STEP * (lead - cap) * LEAD_SLOPE_PCT) // 100
    return STEP * lead


_DISP = [WALL_DISPARITY * (d + 2) if d < -2 else (WALL_DISPARITY * (d - 2) if d > 2 else 0)
         for d in range(-10, 11)]
_LEADV = [[_lead_value(lead, w) for lead in range(0, 128)] for w in range(11)]


def _static(dm, do, wm, wo, g):
    """Đánh giá tĩnh theo góc nhìn bên đang đi (đích có bit chẵn lẻ g)."""
    lead = do - dm
    if lead >= 0:
        s = _LEADV[wo][lead if lead < 128 else 127]
    else:
        s = -_LEADV[wm][-lead if lead > -128 else 127]
    s += TEMPO + _WVS[g][wm] - _WVS[g ^ 1][wo] + _DISP[wm - wo + 10]
    if wo == 0:
        s += _MONO[wm]
        if dm <= do:
            s += RACE_BONUS
    elif wm == 0:
        s -= _MONO[wo]
        if dm > do:
            s -= RACE_BONUS
    return s


def _jumps(o, U, D, L, R):
    """Các bước nhảy qua quân o: list (ô đứng cạnh o, mặt nạ ô đáp)."""
    js = []
    if o & D:
        dest = (o >> 9) if o & U else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o << 9, dest))
    if o & U:
        dest = (o << 9) if o & D else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o >> 9, dest))
    if o & L:
        dest = (o << 1) if o & R else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o >> 1, dest))
    if o & R:
        dest = (o >> 1) if o & L else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o << 1, dest))
    return js


def _reach_mask(o, goal, U, D, L, R):
    """Flood-fill ngược có tính nhảy: các ô tới được `goal` khi quân o đứng yên mãi."""
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


def _blocked_q(a, o, goal, U, D, L, R, hw, vw, d):
    """True nếu quân a không thể về đích khi quân o đứng yên (PASS) mãi. Cache kép."""
    key = (a, o, hw, vw, goal)
    r = _BQ.get(key)
    if r is None:
        r = False
        if not (a & goal) and _may_block(a, o, 0 if goal & 1 else 8, d):
            k2 = (o, hw, vw, goal)
            m = _BCACHE.get(k2)
            if m is None:
                m = _reach_mask(o, goal, U, D, L, R)
                _BCACHE[k2] = m
            r = not (m & a)
        _BQ[key] = r
    return r


def _evalb(dm, do, wm, wo, me, op, gm, go, U, D, L, R, hw, vw):
    """Đánh giá tĩnh + nhận diện thế chặn cửa (bên dẫn bị chặn vĩnh viễn -> hòa)."""
    s = _static(dm, do, wm, wo, gm & 1)
    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if _blocked_q(me, op, gm, U, D, L, R, hw, vw, dm):
                return _draw(gm)
        elif _blocked_q(op, me, go, U, D, L, R, hw, vw, do):
            return _draw(gm)
    return s


# ---------------------------------------------------------------------------
# 5. SINH NƯỚC
#   Con = (điểm sắp xếp, mã, a', U', D', L', R', hw', vw', wa', d_a', d_b')
# ---------------------------------------------------------------------------
def _pawn_nd(x, ga, U, D, L, R, lay_m, dm):
    nlay = len(lay_m)
    hi = dm + 3 if dm + 3 <= nlay else nlay
    for k in range(dm - 2 if dm >= 2 else 0, hi):
        if x & lay_m[k]:
            return k
    return _dist(x, ga, U, D, L, R)


def _flex(x, nd, U, D, L, R, lay_m):
    """Thưởng đa đường / phạt cổ chai cho ô x (cách đích nd)."""
    if nd <= 0 or nd >= len(lay_m):
        return 0
    s1 = (((x & U) >> 9) | ((x & D) << 9) | ((x & L) >> 1) | ((x & R) << 1)) & lay_m[nd - 1]
    if s1 & (s1 - 1):
        return MULTI_PATH_BONUS
    if nd >= 2 and s1:
        s2 = (((s1 & U) >> 9) | ((s1 & D) << 9) | ((s1 & L) >> 1) | ((s1 & R) << 1)) & lay_m[nd - 2]
        if s2 and not (s2 & (s2 - 1)):
            return -BOTTLENECK_PENALTY
    return 0


def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    """Nước đi quân (sắp theo đánh giá có đa đường). Trả về (mã thắng ngay | None, list)."""
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    flex = wb > 0
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
        sc = -_evalb(db, nd, wb, wa, b, x, gb, ga, U, D, L, R, hw, vw)
        if flex:
            sc += _flex(x, nd, U, D, L, R, lay_m)
        out.append((sc, x.bit_length() - 1, x, U, D, L, R, hw, vw, wa, nd, db))
    return None, out


def _opp_walls(b, gb, U, D, L, R, hw, vw):
    """Tường chạm DAG đường ngắn nhất của quân b (không chặn hết đường của b), cache theo
    (b, tường). Gồm cả tường chưa làm b đi xa hơn (tường "chuẩn bị").

    Trả về (quãng đường hiện tại của b, array các số ndo*1024 + mã) giảm dần theo ndo.
    """
    key = (b, gb, hw, vw)
    ent = _WCACHE.get(key)
    if ent is not None:
        return ent
    do, lay_o, sns_o, F, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    if do >= INF:
        ent = (do, array("i"))
        _WCACHE[key] = ent
        return ent
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    ch, cv = _cut_mask(ou, od, ol, orr)
    lst = []
    for is_h, mask in ((1, ch & avail_h), (0, cv & avail_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            wt = w | (w << 1) | (w << 9) | (w << 10)
            if is_h:
                e1 = w | (w << 1)
                e2 = (w << 9) | (w << 10)
                ndo = _newdist(b, gb, do, lay_o, sns_o, F, (od & e1) | (ou & e2), wt,
                               U & ~e2, D & ~e1, L, R, hw | w, vw)
                code = H_BASE + w.bit_length() - 1
            else:
                e1 = w | (w << 9)
                e2 = (w << 1) | (w << 10)
                ndo = _newdist(b, gb, do, lay_o, sns_o, F, (orr & e1) | (ol & e2), wt,
                               U, D, L & ~e2, R & ~e1, hw, vw | w)
                code = V_BASE + w.bit_length() - 1
            if ndo >= INF:
                continue
            lst.append(ndo * 1024 + code)
    lst.sort(reverse=True)
    ent = (do, array("i", lst))
    _WCACHE[key] = ent
    return ent


def _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
    """Con cho một mã tường cụ thể (TT / killer / countermove / phòng thủ); None nếu sai luật."""
    if wa <= 0 or code < H_BASE or code >= PASS:
        return None
    m = _wall_masks(code, U, D, L, R, hw, vw)
    if m is None:
        return None
    U2, D2, L2, R2, hw2, vw2, w, e1, e2 = m
    wt = w | (w << 1) | (w << 9) | (w << 10)
    do, lay_o, sns_o, Fo, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    dm, lay_m, sns_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    if code >= V_BASE:
        so = (orr & e1) | (ol & e2)
        sm = (mr & e1) | (ml & e2)
    else:
        so = (od & e1) | (ou & e2)
        sm = (md & e1) | (mu & e2)
    ndo = _newdist(b, gb, do, lay_o, sns_o, Fo, so, wt, U2, D2, L2, R2, hw2, vw2) if so else do
    if ndo >= INF:
        return None
    ndm = _newdist(a, ga, dm, lay_m, sns_m, Fm, sm, wt, U2, D2, L2, R2, hw2, vw2) if sm else dm
    if ndm >= INF:
        return None
    return (0, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo)


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, top, stop_at=None, floor=-WIN * 4,
               ply=0):
    """Tường tấn công, dựa trên danh sách đã sắp xếp của _opp_walls.

    - `stop_at` (nút sát lá): trả về [tường tốt nhất] với điểm tĩnh CHÍNH XÁC, dừng sớm
      khi các tường còn lại chắc chắn không tốt hơn (tường không làm mình đi nhanh hơn).
    - nút trong: tối đa `top` tường, khóa sắp xếp = điểm tĩnh + tie-break (< 1 bước):
      đối thủ chậm thêm, nối tường, history.
    """
    do, lst = _opp_walls(b, gb, U, D, L, R, hw, vw)
    if not lst:
        return []
    dm, lay_m, sns_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    wa2 = wa - 1
    gb1 = gb & 1
    out = []
    best = floor
    best_item = None
    thr = -WIN * 4
    leaf = stop_at is not None
    if not leaf:
        conn_h = _BORDER_H | (hw << 2) | (hw >> 2) | (vw << 1) | (vw >> 1) | (vw << 9) | (vw >> 9) \
            | (vw << 8) | (vw >> 10)
        conn_v = _BORDER_V | (vw << 18) | (vw >> 18) | (hw << 9) | (hw >> 9) | (hw << 1) | (hw >> 1) \
            | (hw << 10) | (hw >> 8)
        hist = _HISTORY
    for v in lst:
        ndo = v >> 10
        if leaf:
            if ndo <= 1:
                break
            bound = -_static(ndo, dm, wb, wa2, gb1)
            if bound <= best:
                break
        else:
            bound = -_static(ndo, dm, wb, wa2, gb1)
            if top is not None and len(out) >= top and bound + 9 <= thr:
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
            ndm = _newdist(a, ga, dm, lay_m, sns_m, Fm, src_, w | (w << 1) | (w << 9) | (w << 10),
                           U2, D2, L2, R2, hw2, vw2)
            if ndm >= INF:
                continue
            sc = -_static(ndo, ndm, wb, wa2, gb1)
        else:
            ndm = dm
            sc = bound
        if leaf:
            if sc > best:
                best = sc
                best_item = (sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo)
                if sc >= stop_at:
                    break
            continue
        tb = 2 if ndo - do >= 2 else (1 if ndo > do else 0)
        if (w & conn_h) if code < V_BASE else (w & conn_v):
            tb += 1
        hb = hist[code] >> 6
        tb += hb if hb < 6 else 6
        out.append((sc + tb, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo))
        if top is not None and len(out) >= top + 4:
            out.sort(reverse=True)
            del out[top:]
            thr = out[-1][0]
    if leaf:
        return [best_item] if best_item is not None else []
    out.sort(reverse=True)
    if top is not None and len(out) > top:
        del out[top:]
    return out


def _counter_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, dm, db, force):
    """Tường phòng thủ chéo: chiếm trước chỗ (chồng/cắt) của các tường đe dọa mạnh nhất
    mà đối thủ có thể dùng để chặn mình. Trả về (list con, đe dọa lớn nhất lên mình)."""
    dmy, lst = _opp_walls(a, ga, U, D, L, R, hw, vw)
    if not lst:
        return [], 0
    top_dmg = (lst[0] >> 10) - dmy
    if top_dmg < COUNTER_MIN_DMG or (not force and dm > db and top_dmg < COUNTER_TRIGGER):
        return [], top_dmg
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand = {}
    n = 0
    for v in lst:
        dmg = (v >> 10) - dmy
        if dmg < COUNTER_MIN_DMG:
            break
        n += 1
        if n > COUNTER_THREATS:
            break
        code = v & 1023
        if code < V_BASE:
            w = 1 << (code - H_BASE)
            hs = ((w >> 1) | (w << 1)) & avail_h
            vs = w & avail_v
        else:
            w = 1 << (code - V_BASE)
            vs = ((w >> 9) | (w << 9)) & avail_v
            hs = w & avail_h
        while hs:
            x = hs & -hs
            hs ^= x
            c = H_BASE + x.bit_length() - 1
            if cand.get(c, -1) < dmg:
                cand[c] = dmg
        while vs:
            x = vs & -vs
            vs ^= x
            c = V_BASE + x.bit_length() - 1
            if cand.get(c, -1) < dmg:
                cand[c] = dmg
    out = []
    gb1 = gb & 1
    for code, dmg in cand.items():
        c = _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
        if c is None:
            continue
        sc = -_static(c[11], c[10], wb, c[9], gb1)
        out.append((sc + dmg,) + c[1:])
    out.sort(reverse=True)
    return out, top_dmg


def _gen_walls_root(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
    """Mọi tường đáng xét ở gốc: tấn công + phòng thủ (chồng/cắt mỏ neo đe dọa) + chéo."""
    dm, lay_m, sns_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    do, lay_o, sns_o, Fo, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    oh, ov = _cut_mask(ou, od, ol, orr)
    th, tv = _cut_mask(mu, md, ml, mr)
    cand_h = (oh | (th << 1) | (th >> 1) | tv) & avail_h
    cand_v = (ov | (tv << 9) | (tv >> 9) | th) & avail_v
    codes = set()
    while cand_h:
        x = cand_h & -cand_h
        cand_h ^= x
        codes.add(H_BASE + x.bit_length() - 1)
    while cand_v:
        x = cand_v & -cand_v
        cand_v ^= x
        codes.add(V_BASE + x.bit_length() - 1)
    counters, _ = _counter_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, dm, do, True)
    for c in counters:
        codes.add(c[1])
    out = []
    gb1 = gb & 1
    for code in codes:
        c = _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
        if c is None:
            continue
        sc = -_static(c[11], c[10], wb, c[9], gb1)
        out.append((sc,) + c[1:])
    out.sort(reverse=True)
    return out


def _top_walls(depth):
    if depth >= 4:
        return 11
    if depth == 3:
        return 9
    return 7


# ---------------------------------------------------------------------------
# 6. BẢNG TÀN CUỘC (0-0 chính xác, 1-0 chính xác, k-0 làm cận)
#   Giá trị theo bên đi: +k = thắng ở nước thứ k (tính cả nước đối thủ), -k = thua,
#   0 = hòa. N = quân có đích hàng 0, S = đích hàng 8; chỉ số = ô_N * 81 + ô_S.
# ---------------------------------------------------------------------------
def _tb_graph(U, D, L, R):
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

    adjmv = {}
    rj = {}
    for x in range(81):
        for y in nb[x]:
            j = jumps(x, y)
            adjmv[x * 81 + y] = [c for c in nb[x] if c != y] + list(j)
            for z in j:
                rj.setdefault(y * 81 + z, []).append(x)
    return nb, adjmv, rj


def _tb_solve(U, D, L, R):
    """Phân tích ngược toàn bộ 2 x 81 x 81 thế khi cả hai hết tường."""
    nb, adjmv, rj = _tb_graph(U, D, L, R)
    NN = 6561
    valN = [0] * NN
    valS = [0] * NN
    cntN = [0] * NN
    cntS = [0] * NN
    queue = []
    qa = queue.append
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
    seen = front = goal
    while front:
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        seen |= front
    return seen


def _walls_of(U, D, L, R, hw, vw):
    """Mọi chỗ đặt tường không chồng/cắt: (hw', vw', U', D', L', R')."""
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
    """Bảng "1 tường vs 0 tường" (bên hN còn đúng 1 tường). Dựng dần qua nhiều lượt:
    trước hết là bảng 0-0 của mọi bàn sau khi đặt bức tường cuối, rồi phân tích ngược
    thêm một lần với lựa chọn "đặt tường". Trả về True nếu đã xong."""
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
    val = [[0] * NN, [0] * NN]
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
                    continue
                cH[i] += 1
                v = t[i]
                if v < 0:
                    push(1 - v, (hs, i, 1 - v))
                elif v > 0:
                    dl = decs.get(v)
                    if dl is None:
                        decs[v] = [i]
                    else:
                        dl.append(i)
        if perf_counter() > until + 0.15:
            return False
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
    """Giá trị bảng tàn cuộc -> điểm tìm kiếm (theo bên đi)."""
    if v > 0:
        if turn + ply + v - 1 < max_turns:
            return WIN - (ply + v - 1)
    elif v < 0:
        if turn + ply - v - 1 < max_turns:
            return -(WIN - (ply - v - 1))
    t = db - da
    if t > 4:
        t = 4
    elif t < -4:
        t = -4
    return _draw(ga) + t


def _tb_value(t, ia, ib, ga):
    return t[0][ia * 81 + ib] if ga & 1 else t[1][ib * 81 + ia]


def _tb_pick(t, a, b, ga, U, D, L, R, turns_left, legal):
    """Chọn nước theo bảng 0-0: thắng nhanh nhất / hòa gây áp lực nhất / thua lâu nhất.

    Trả về (ô đích hoặc ô hiện tại nếu PASS, kết quả +1/0/-1) hoặc None."""
    ia = a.bit_length() - 1
    ib = b.bit_length() - 1
    meN = ga & 1
    gb = ROW[8] if meN else ROW[0]
    best = None
    best_key = None
    opts = [x.bit_length() - 1 for x in _pawn_moves(a, b, U, D, L, R)] + [ia]
    for dest in opts:
        if dest != ia and dest not in legal:
            continue
        x = 1 << dest
        if x & ga:
            return dest, 1
        # đối thủ đi tiếp
        if meN:
            v = t[1][dest * 81 + ib]
        else:
            v = t[0][ib * 81 + dest]
        if v < 0 and 1 - v <= turns_left:
            outcome, pace = 1, v          # thắng: |v| nhỏ hơn tốt hơn -> pace = v (âm)
        elif v > 0 and v + 1 <= turns_left:
            outcome, pace = -1, v         # thua: kéo dài càng lâu càng tốt
        else:
            outcome, pace = 0, 0
        pressure = 0.0
        if outcome == 0 and turns_left >= 3:
            replies = [y.bit_length() - 1 for y in _pawn_moves(b, x, U, D, L, R)] + [ib]
            chances = 0
            for rep in replies:
                y = 1 << rep
                if y & gb:
                    continue
                vv = t[0][dest * 81 + rep] if meN else t[1][rep * 81 + dest]
                if 0 < vv <= turns_left - 2:
                    chances += 1
            pressure = chances / float(len(replies))
        progress = -_dist(x, ga, U, D, L, R)
        visits = _EG_VISITS.get((dest, ib), 0)
        key = (outcome, pace, pressure, progress, -visits, dest != ia)
        if best_key is None or key > best_key:
            best, best_key = dest, key
    if best is None:
        return None
    return best, best_key[0]


# ---------------------------------------------------------------------------
# 7. TÌM KIẾM: PVS + NMP + LMR + QUIESCENCE
# ---------------------------------------------------------------------------
class _Searcher:
    __slots__ = ("deadline", "turn", "max_turns", "nodes", "killers", "path",
                 "tb_left", "tb_until")

    def __init__(self, deadline, turn, max_turns, tb_until):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        self.killers = [[None, None] for _ in range(MAX_PLY)]
        self.path = set()
        self.tb_left = TB_MAX
        self.tb_until = tb_until

    # ---- bảng tàn cuộc trong cây ----
    def tb_probe(self, a, b, wa, wb, ga, U, D, L, R, hw, vw, da, db, alpha, beta, ply):
        both = wa == 0 and wb == 0
        exact = both
        t = None
        if not both and _TB1C:
            t = _TB1C.get((hw, vw, (ga & 1) if wa else ((ga & 1) ^ 1)))
            if t is not None:
                exact = wa + wb == 1
        if t is None:
            t = _TBC.get((hw, vw))
            if (t is None and self.tb_left > 0 and (both or ply <= TB_BOUND_PLY)
                    and perf_counter() < self.tb_until):
                t = _tb_solve(U, D, L, R)
                _TBC[(hw, vw)] = t
                self.tb_left -= 1
        if t is None:
            return None
        v = _tb_value(t, a.bit_length() - 1, b.bit_length() - 1, ga)
        sc = _tb_score(v, ply, ga, da, db, self.turn, self.max_turns)
        if exact:
            return sc
        if wb == 0:
            if sc >= beta or sc > MATE_ZONE:
                return sc
        elif sc <= alpha or sc < -MATE_ZONE:
            return sc
        return None

    # ---- quiescence khi hai quân sát nhau ----
    def qs(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db, alpha, beta, ply, qd):
        self.nodes += 1
        if (self.nodes & 7) == 0 and perf_counter() > self.deadline:
            raise _TimeUp()
        if self.turn + ply >= self.max_turns:
            return _draw(ga)
        ia = a.bit_length() - 1
        contact = _QSN[ia] & b
        if da <= 2:
            if da == 1 and not (NEAR1[ia] & b):
                return WIN - ply - 1
            if contact and _can_win_now(a, b, ga, U, D, L, R):
                return WIN - ply - 1
        stand = _evalb(da, db, wa, wb, a, b, ga, gb, U, D, L, R, hw, vw)
        if qd <= 0 or stand >= beta or not contact:
            return stand
        best = stand
        if stand > alpha:
            alpha = stand
        lay = _layers(ga, U, D, L, R, hw, vw)[0]
        for x in _pawn_moves(a, b, U, D, L, R):
            nd = _pawn_nd(x, ga, U, D, L, R, lay, da)
            s = -self.qs(b, x, wb, wa, gb, ga, U, D, L, R, hw, vw, db, nd,
                         -beta, -alpha, ply + 1, qd - 1)
            if s > best:
                best = s
                if s > alpha:
                    alpha = s
                    if alpha >= beta:
                        break
        return best

    def child_value(self, b, x, wb, wx, gb, gx, U, D, L, R, hw, vw, db, dx, alpha, beta, ply):
        """Giá trị (theo bên vừa đi, quân ở x) của thế con ở ply `ply`, b tới lượt."""
        if self.turn + ply >= self.max_turns:
            return -_draw(gb)
        ib = b.bit_length() - 1
        if _QSN[ib] & x:
            return -self.qs(b, x, wb, wx, gb, gx, U, D, L, R, hw, vw, db, dx,
                            -beta, -alpha, ply, QS_DEPTH)
        if db == 1:
            return -(WIN - ply - 1)
        return -_evalb(db, dx, wb, wx, b, x, gb, gx, U, D, L, R, hw, vw)

    # ---- nút sát lá ----
    def frontier(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db, alpha, beta,
                 ply, key, tt_move, prev, lay_m):
        np1 = ply + 1
        alpha0 = alpha
        flex = wb > 0
        nlay = len(lay_m)
        lo = da - 2 if da >= 2 else 0
        hi = da + 3 if da + 3 <= nlay else nlay
        moves = _pawn_moves(a, b, U, D, L, R)
        for x in moves:
            if x & ga:
                return WIN - ply - 1
        best = self.child_value(b, a, wb, wa, gb, ga, U, D, L, R, hw, vw, db, da,
                                alpha, beta, np1)
        best_move = PASS
        if best > alpha:
            alpha = best
        if best < beta:
            for x in moves:
                nd = -1
                for k in range(lo, hi):
                    if x & lay_m[k]:
                        nd = k
                        break
                if nd < 0:
                    nd = _dist(x, ga, U, D, L, R)
                v = self.child_value(b, x, wb, wa, gb, ga, U, D, L, R, hw, vw, db, nd,
                                     alpha, beta, np1)
                if flex and -MATE_ZONE < v < MATE_ZONE:
                    v += _flex(x, nd, U, D, L, R, lay_m)
                if v > best:
                    best = v
                    best_move = x.bit_length() - 1
                    if v > alpha:
                        alpha = v
                        if v >= beta:
                            break
        if best < beta and wa > 0:
            wa2 = wa - 1
            kl = self.killers[ply]
            cm = _CM[prev] if prev else 0
            for km in (tt_move, kl[0], kl[1], cm):
                if km is None or km < H_BASE or km >= PASS or km == best_move:
                    continue
                c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                if c is None:
                    continue
                v = self.child_value(b, a, wb, wa2, gb, ga, c[3], c[4], c[5], c[6], c[7], c[8],
                                     c[11], c[10], alpha, beta, np1)
                if v > best:
                    best = v
                    best_move = km
                    if v > alpha:
                        alpha = v
                        if v >= beta:
                            break
            if best < beta:
                if QS_WALLS and QS_DEPTH > 0 and _QSN[b.bit_length() - 1] & a:
                    walls = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, FRONTIER_WALLS)
                    for c in walls:
                        v = self.child_value(b, a, wb, wa2, gb, ga, c[3], c[4], c[5], c[6],
                                             c[7], c[8], c[11], c[10], alpha, beta, np1)
                        if v > best:
                            best = v
                            best_move = c[1]
                            if v > alpha:
                                alpha = v
                                if v >= beta:
                                    break
                elif self.turn + np1 < self.max_turns:
                    walls = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, 1, beta, best)
                    if walls and walls[0][0] > best:
                        best = walls[0][0]
                        best_move = walls[0][1]
        if best >= beta and best_move is not None and H_BASE <= best_move < PASS:
            kl = self.killers[ply]
            if kl[0] != best_move:
                kl[1] = kl[0]
                kl[0] = best_move
            if prev:
                _CM[prev] = best_move
        flag = 1 if best >= beta else (2 if best <= alpha0 else 0)
        _TT[key] = (1, _to_tt(best, ply), flag, best_move)
        return best

    # ---- nút trong ----
    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply, prev):
        self.nodes += 1
        if (self.nodes & 7) == 0 and perf_counter() > self.deadline:
            raise _TimeUp()
        if db == 0:
            return -(WIN - ply)
        if self.turn + ply >= self.max_turns:
            return _draw(ga)
        if (wa == 0 or wb == 0):
            r = self.tb_probe(a, b, wa, wb, ga, U, D, L, R, hw, vw, da, db, alpha, beta, ply)
            if r is not None:
                return r
        if depth <= 0:
            return self.qs(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db, alpha, beta,
                           ply, QS_DEPTH)

        key = (a, b, wa, wb, hw, vw, ga)
        if FEAT_HIST_DRAW and (key in self.path or key in _HIST):
            return _draw(ga)
        entry = _TT.get(key)
        if entry is None:
            entry = _TT_OLD.get(key)
        tt_move = None
        if entry is not None:
            e_depth, e_score, e_flag, tt_move = entry
            if e_depth >= depth:
                if e_score > MATE_ZONE:
                    e_score -= ply
                elif e_score < -MATE_ZONE:
                    e_score += ply
                if e_flag == 0:
                    return e_score
                if e_flag == 1:
                    if e_score >= beta:
                        return e_score
                elif e_score <= alpha:
                    return e_score

        lay_m = _layers(ga, U, D, L, R, hw, vw)[0]
        if depth == 1:
            return self.frontier(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db, alpha, beta,
                                 ply, key, tt_move, prev, lay_m)

        win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, da, db)
        if win is not None:
            return WIN - ply - 1
        np1 = ply + 1
        ia = a.bit_length() - 1
        adj = bool(NEAR2[ia] & b)
        pass_sc = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw)

        # PASS null-move: chỉ khi hai quân không sát nhau và cả hai còn xa đích
        if (depth >= NMP_R + 2 and not adj and da > 2 and db > 2
                and pass_sc >= beta + (NMP_MARGIN if wb > 0 else 0)
                and -MATE_ZONE < beta < MATE_ZONE):
            sc = -self.search(b, a, wb, wa, gb, ga, U, D, L, R, hw, vw, db, da,
                              depth - 1 - NMP_R, -beta, 1 - beta, np1, PASS)
            if sc >= beta:
                return beta if sc > MATE_ZONE else sc

        pawns.sort(reverse=True)
        pass_child = (pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db)
        contact = adj or (_BLOCK[0] and pass_sc >= -_CONT[0])
        if contact:
            pawns.append(pass_child)

        self.path.add(key)
        orig_alpha = alpha
        best = -WIN * 2
        best_move = None
        nd = depth - 1
        tried = set()
        first = True
        killers = self.killers[ply]
        cm_move = _CM[prev] if prev else 0
        cutoff = False
        stage = 0
        can_lmr = depth >= 3 and da > 2 and db > 2
        while stage < 5 and not cutoff:
            if stage == 0:
                lst = ()
                if tt_move is not None:
                    if tt_move == PASS:
                        lst = (pass_child,)
                    elif tt_move < H_BASE:
                        lst = [k for k in pawns if k[1] == tt_move]
                    else:
                        c = _wall_child(tt_move, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                        if c is not None:
                            lst = (c,)
            elif stage == 1:
                lst = []
                if wa > 0:
                    for km in (killers[0], killers[1], cm_move):
                        if km and km not in tried and H_BASE <= km < PASS:
                            c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                lst.append(c)
            elif stage == 2:
                lst = pawns
            elif stage == 3:
                if wa > 0:
                    lst = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, _top_walls(depth))
                    if wb > 0 and FEAT_COUNTERS and ply <= COUNTER_MAX_PLY:
                        cl, _thr = _counter_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                                  da, db, False)
                        if cl:
                            lst = lst + cl
                else:
                    lst = ()
            else:
                lst = () if contact else (pass_child,)
            cur_stage = stage
            stage += 1
            wall_idx = 0
            for k in lst:
                code = k[1]
                if code in tried:
                    continue
                tried.add(code)
                if first:
                    sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1, code)
                    first = False
                else:
                    if (cur_stage == 3 and can_lmr and wall_idx >= LMR_MIN_INDEX
                            and k[11] - db < 2):
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                          k[7], k[8], k[11], k[10], nd - 1, -alpha - 1, -alpha,
                                          np1, code)
                        if sc > alpha:
                            sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                              k[6], k[7], k[8], k[11], k[10], nd, -alpha - 1,
                                              -alpha, np1, code)
                    else:
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                          k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha,
                                          np1, code)
                    if alpha < sc < beta:
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                          k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1, code)
                if cur_stage == 3:
                    wall_idx += 1
                if sc > best:
                    best = sc
                    best_move = code
                    if sc > alpha:
                        alpha = sc
                        if alpha >= beta:
                            cutoff = True
                            if H_BASE <= code < PASS:
                                _HISTORY[code] += depth * depth
                                if killers[0] != code:
                                    killers[1] = killers[0]
                                    killers[0] = code
                                if prev:
                                    _CM[prev] = code
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


# ---------------------------------------------------------------------------
# 8. GỐC: ĐÀO SÂU DẦN + ASPIRATION + THIÊN LỆCH Ở GỐC
# ---------------------------------------------------------------------------
def _root_adjust(raw, bias):
    return raw if abs(raw) > MATE_ZONE else raw + bias


def _root_raw_bound(bound, bias):
    return bound if abs(bound) > MATE_ZONE else bound - bias


def _threat_on(x, gx, U, D, L, R, hw, vw, dx):
    """Số bước nhiều nhất một bức tường đối thủ bắt quân ở x đi thêm."""
    d0, lst = _opp_walls(x, gx, U, D, L, R, hw, vw)
    if not lst:
        return 0
    return (lst[0] >> 10) - d0


def _wall_damage(pv, gv, pa, ga, U, D, L, R, hw, vw):
    """wall_damage (mitsuha): thiệt hại lớn nhất MỘT tường hợp lệ của quân pa gây cho pv."""
    dv, lst = _opp_walls(pv, gv, U, D, L, R, hw, vw)
    if not lst:
        return 0
    da, lay_a, sns_a, Fa, au, ad, al, ar = _dag(pa, ga, U, D, L, R, hw, vw)
    n = 0
    for v in lst:
        n += 1
        if n > 6:
            break
        code = v & 1023
        m = _wall_masks(code, U, D, L, R, hw, vw)
        if m is None:
            continue
        U2, D2, L2, R2, hw2, vw2, w, e1, e2 = m
        src = ((ar & e1) | (al & e2)) if code >= V_BASE else ((ad & e1) | (au & e2))
        if src and _newdist(pa, ga, da, lay_a, sns_a, Fa, src, w | (w << 1) | (w << 9) | (w << 10),
                            U2, D2, L2, R2, hw2, vw2) >= INF:
            continue
        return (v >> 10) - dv
    return 0


def _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0, deadline,
                 urg):
    da = _dist(a, ga, U, D, L, R)
    db = _dist(b, gb, U, D, L, R)
    lay_m = _layers(ga, U, D, L, R, hw, vw)[0]
    win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, da, db)
    if win is not None:
        return win, None
    if wa > 0:
        kids.extend(_gen_walls_root(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw))
    kids.append((-_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw) - 1, PASS,
                 a, U, D, L, R, hw, vw, wa, da, db))

    # Thiên lệch chỉ ở gốc (không làm bẩn TT): chống đi qua đi lại, chống PASS vô ích,
    # sốt ruột khi gần hết lượt, ưu tiên ô ít bị tường đe dọa.
    ia = a.bit_length() - 1
    contact = bool(NEAR2[ia] & b)
    recent = {(e[1], e[2], e[3]) for e in _RECENT}
    threat = {}
    if wb > 0 and not contact and ROOT_THREAT_CAP > 0:
        for k in kids:
            if k[1] < H_BASE:
                threat[k[1]] = _threat_on(k[2], ga, U, D, L, R, hw, vw, k[10])
    tmin = min(threat.values()) if threat else 0
    biases = {}
    for k in kids:
        code = k[1]
        bias = 0
        if code == PASS:
            if not contact:
                bias -= int(ROOT_PASS_PENALTY * (1 + urg))
        elif code < H_BASE:
            if not contact and (k[2], hw, vw) in recent and k[10] >= da:
                bias -= int(ROOT_REPEAT_PENALTY * (1 + 2 * urg))
            if urg > 0:
                bias += int(URG_BONUS * urg * (da - k[10]))
            if threat:
                t = threat.get(code, tmin) - tmin
                bias -= t if t < ROOT_THREAT_CAP else ROOT_THREAT_CAP
        biases[code] = bias

    entry = _TT.get((a, b, wa, wb, hw, vw, ga)) or _TT_OLD.get((a, b, wa, wb, hw, vw, ga))
    tt_code = entry[3] if entry is not None else None

    def root_order(k, scores=None):
        if scores is None:
            value = _root_adjust(k[0], biases[k[1]])
            if k[1] == tt_code:
                value += 100000
        else:
            value = scores.get(k[1], -WIN * 3)
        child = (b, k[2], wb, k[9], k[7], k[8], gb)
        return (-value, child in _HIST, k[1] == PASS)

    kids.sort(key=root_order)
    best_move = kids[0][1]
    prev_score = _root_adjust(kids[0][0], biases[kids[0][1]])
    _STATE["depth"] = 1
    _STATE["score"] = prev_score
    if len(kids) == 1:
        return best_move, kids[0]

    tb_until = t0 + TIME_LIMIT * TB_TIME
    s = _Searcher(deadline, turn, max_turns, tb_until)
    depth = 1
    prev_it = 0.0
    while depth < 60:
        depth += 1
        it_start = perf_counter()
        if depth >= 3 and abs(prev_score) < MATE_ZONE:
            delta = ASP_DELTA
            asp_alpha = prev_score - delta
            asp_beta = prev_score + delta
        else:
            delta = WIN * 2
            asp_alpha = -WIN * 2
            asp_beta = WIN * 2
        cur_best = None
        cur_best_sc = -WIN * 2
        new_scores = {}
        finished = False
        try:
            while True:
                alpha = asp_alpha
                beta = asp_beta
                cur_best = None
                cur_best_sc = -WIN * 2
                new_scores = {}
                first = True
                for k in kids:
                    code = k[1]
                    bias = biases[code]
                    if first:
                        sc = _root_adjust(
                            -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], depth - 1,
                                      -_root_raw_bound(beta, bias),
                                      -_root_raw_bound(alpha, bias), 1, code), bias)
                        first = False
                    else:
                        sc = _root_adjust(
                            -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], depth - 1,
                                      -_root_raw_bound(alpha + 1, bias),
                                      -_root_raw_bound(alpha, bias), 1, code), bias)
                        if alpha < sc < beta:
                            # đã chứng minh tốt hơn: ghi nhận ngay phòng khi hết giờ
                            prev_best, prev_sc = cur_best, cur_best_sc
                            cur_best, cur_best_sc = code, sc
                            sc = _root_adjust(
                                -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                          k[7], k[8], k[11], k[10], depth - 1,
                                          -_root_raw_bound(beta, bias),
                                          -_root_raw_bound(alpha, bias), 1, code), bias)
                            cur_best, cur_best_sc = prev_best, prev_sc
                    new_scores[code] = sc
                    if sc > cur_best_sc:
                        cur_best_sc = sc
                        cur_best = code
                        if sc > alpha:
                            alpha = sc
                            if alpha >= beta:
                                break
                if cur_best_sc <= asp_alpha and asp_alpha > -WIN * 2:
                    delta = int(delta * ASP_GROW)
                    asp_alpha = prev_score - delta if delta < MATE_ZONE else -WIN * 2
                    if cur_best_sc < -MATE_ZONE:
                        asp_alpha = -WIN * 2
                elif cur_best_sc >= asp_beta and asp_beta < WIN * 2:
                    best_move = cur_best
                    kids.sort(key=lambda item: (0 if item[1] == cur_best else 1))
                    delta = int(delta * ASP_GROW)
                    asp_beta = prev_score + delta if delta < MATE_ZONE else WIN * 2
                    if cur_best_sc > MATE_ZONE:
                        asp_beta = WIN * 2
                else:
                    finished = True
                    break
        except _TimeUp:
            if cur_best is not None:
                best_move = cur_best
            _STATE["nodes"] = s.nodes
            break
        if not finished:
            break
        best_move = cur_best
        swing = abs(cur_best_sc - prev_score)
        prev_score = cur_best_sc
        _STATE["depth"] = depth
        _STATE["nodes"] = s.nodes
        _STATE["score"] = cur_best_sc
        raw_best = _root_raw_bound(cur_best_sc, biases[best_move])
        kids.sort(key=lambda k: root_order(k, new_scores))
        if abs(raw_best) > MATE_ZONE and WIN - abs(raw_best) <= depth:
            break
        now = perf_counter()
        it_time = now - it_start
        if prev_it > 0.0005:
            ebf = it_time / prev_it
            if ebf < 1.5:
                ebf = 1.5
            elif ebf > 8.0:
                ebf = 8.0
        else:
            ebf = 4.0
        prev_it = it_time
        predicted = it_time * ebf
        left = deadline - now
        if left <= 0.004:
            break
        # Không ngắt nếu còn >= 35% thời gian dự phóng của ply sau, hoặc điểm vừa dao
        # động mạnh (chống hiệu ứng chân trời). Lượt lặp dở dang vẫn có ích: nước đầu
        # (tốt nhất cũ) xét xong trước, nước khác chỉ thay khi đã chứng minh tốt hơn.
        if left >= ITER_FRACTION * predicted or swing > VOLATILE_SWING:
            continue
        if left < ITER_PARTIAL * it_time:
            break
    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None


# ---------------------------------------------------------------------------
# 9. GIAO TIẾP ARENA & AN TOÀN
# ---------------------------------------------------------------------------
def _bit(rc):
    return 1 << (int(rc[0]) * 9 + int(rc[1]))


def _fallback(observation):
    """Nước an toàn tuyệt đối: ô hợp lệ gần đích nhất theo ma trận distances; không có thì PASS."""
    try:
        moves = observation.get("legal_moves") or []
        dist = observation.get("distances")
        best = None
        best_d = None
        for m in moves:
            d = 0
            if dist:
                d = dist[m[0]][m[1]]
                if d is None or d < 0:
                    d = 10 ** 6
            if best is None or d < best_d:
                best, best_d = m, d
        if best is not None:
            return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception:
        pass
    return {"type": "pass"}


def _is_legal(action, observation):
    if not isinstance(action, dict):
        return False
    kind = action.get("type")
    if kind == "pass":
        return True
    if kind == "move":
        to = action.get("to")
        if not isinstance(to, list) or len(to) != 2:
            return False
        if type(to[0]) is not int or type(to[1]) is not int:
            return False
        for m in observation.get("legal_moves") or []:
            if m[0] == to[0] and m[1] == to[1]:
                return True
        return False
    if kind == "wall":
        o = action.get("orientation")
        r = action.get("row")
        c = action.get("col")
        if type(r) is not int or type(c) is not int:
            return False
        for w in observation.get("legal_walls") or []:
            if w["orientation"] == o and w["row"] == r and w["col"] == c:
                return True
    return False


def _code_to_action(code):
    if code == PASS:
        return {"type": "pass"}
    if code < H_BASE:
        return {"type": "move", "to": [code // 9, code % 9]}
    if code < V_BASE:
        idx = code - H_BASE
        return {"type": "wall", "orientation": "H", "row": idx // 9, "col": idx % 9}
    idx = code - V_BASE
    return {"type": "wall", "orientation": "V", "row": idx // 9, "col": idx % 9}


def _housekeeping(turn, goals, hw, vw):
    global _TT, _TT_OLD, _EG_VISITS
    if turn <= _STATE["last_turn"] or _STATE["goals"] != goals:
        # ván mới
        _TT.clear()
        _TT_OLD.clear()
        _HIST.clear()
        del _RECENT[:]
        for i in range(NCODES):
            _HISTORY[i] = 0
            _CM[i] = 0
        _STATE["margin"] = 0.0
        _EG_VISITS = {}
        _BCACHE.clear()
        _BQ.clear()
    else:
        if len(_TT) > TT_CAP:
            _TT_OLD = _TT
            _TT = {}
        for i in range(NCODES):
            _HISTORY[i] >>= 1
    _STATE["last_turn"] = turn
    _STATE["goals"] = goals
    for cache, limit in ((_DCACHE, 80000), (_LCACHE, 12000), (_WCACHE, 25000),
                         (_GCACHE, 25000), (_BCACHE, 20000), (_BQ, 60000)):
        if len(cache) > limit:
            cache.clear()
    if _TBC:
        for k in [k for k in _TBC if (k[0] & hw) != hw or (k[1] & vw) != vw]:
            del _TBC[k]
    if _TB1C:
        for k in [k for k in _TB1C if (k[0] & hw) != hw or (k[1] & vw) != vw]:
            del _TB1C[k]


def _decide(observation, t0):
    global _EG_VISITS
    you = observation["you"]
    opp = observation["opponent"]
    a = _bit(you["position"])
    b = _bit(opp["position"])
    ga = ROW[int(you["goal_row"])]
    gb = ROW[int(opp["goal_row"])]
    wa = max(0, min(10, int(you["walls_remaining"])))
    wb = max(0, min(10, int(opp["walls_remaining"])))
    legal = set()
    for m in observation.get("legal_moves") or []:
        legal.add(int(m[0]) * 9 + int(m[1]))
    gr = int(you["goal_row"])
    for m in observation.get("legal_moves") or []:
        if int(m[0]) == gr:
            return {"type": "move", "to": [int(m[0]), int(m[1])]}

    U, D, L, R = U0, D0, L0, R0
    hw = vw = 0
    for r, c in observation["walls"]["horizontal"]:
        w = 1 << (int(r) * 9 + int(c))
        hw |= w
        D &= ~(w | (w << 1))
        U &= ~((w << 9) | (w << 10))
    for r, c in observation["walls"]["vertical"]:
        w = 1 << (int(r) * 9 + int(c))
        vw |= w
        R &= ~(w | (w << 9))
        L &= ~((w << 1) | (w << 10))

    turn = int(observation.get("turn", 0))
    max_turns = int(observation.get("max_turns", 500) or 500)
    _housekeeping(turn, (ga, gb), hw, vw)
    _ROOT_GOAL[0] = ga
    frac = turn / float(max_turns)
    urg = (frac - URG_START) / (1.0 - URG_START) if frac > URG_START else 0.0
    if urg > 1.0:
        urg = 1.0
    _CONT[0] = CONTEMPT_BASE + int(CONTEMPT_URG * urg)
    block = (_popcount(hw) + _popcount(vw)) >= BLOCK_MIN_WALLS
    if block != _BLOCK[0]:
        _TT.clear()
        _TT_OLD.clear()
    _BLOCK[0] = block
    _HIST.add((a, b, wa, wb, hw, vw, ga))
    _RECENT[:] = [e for e in _RECENT if e[2] == hw and e[3] == vw
                  and 0 < turn - e[0] <= 2 * ROOT_RECENT_WINDOW]
    _RECENT.append((turn, a, hw, vw))
    del _RECENT[:-ROOT_RECENT_WINDOW]

    deadline = t0 + TIME_LIMIT - _STATE["margin"]
    turns_left = max_turns - turn

    # --- Bảng tàn cuộc ở gốc ---
    if wb == 0 and turns_left > 0:
        t = _TBC.get((hw, vw))
        if t is None and perf_counter() - t0 < 0.3:
            t = _tb_solve(U, D, L, R)
            _TBC[(hw, vw)] = t
        if t is not None:
            ia = a.bit_length() - 1
            ib = b.bit_length() - 1
            pair = (ia, ib)
            _EG_VISITS[pair] = min(32, _EG_VISITS.get(pair, 0) + 1)
            if len(_EG_VISITS) > 512:
                _EG_VISITS = {pair: _EG_VISITS[pair]}
            pick = _tb_pick(t, a, b, ga, U, D, L, R, turns_left, legal)
            if pick is not None:
                dest, outcome = pick
                if wa == 0 or outcome == 1:
                    _STATE["mode"] = "tablebase"
                    if dest == ia:
                        return {"type": "pass"}
                    return {"type": "move", "to": [dest // 9, dest % 9]}
    if (wa == 0) != (wb == 0) and wa + wb <= TB1_MAXK:
        hN = (ga & 1) if wa else ((ga & 1) ^ 1)
        if (hw, vw, hN) not in _TB1C:
            _tb1_build(U, D, L, R, hw, vw, hN, t0 + TIME_LIMIT * TB1_TIME)

    # --- Giá trị "tường đầu tiên" (đe dọa) của hai bên, tính một lần ở gốc ---
    g = ga & 1
    if wa > 0:
        dmg_me = _wall_damage(b, gb, a, ga, U, D, L, R, hw, vw)
    else:
        dmg_me = 0
    if wb > 0:
        dmg_op = _wall_damage(a, ga, b, gb, U, D, L, R, hw, vw)
    else:
        dmg_op = 0
    # Kinh tế tường có điều kiện: chỉ "tiếc tường" khi mình không bị dẫn trong cuộc đua; bị dẫn
    # (thường ngay sau khi đối thủ đặt tường) thì dùng giá chuẩn để đáp trả kịp thời.
    econ_on = wa > 0 and _dist(a, ga, U, D, L, R) <= _dist(b, gb, U, D, L, R)
    for side, dmg in ((g, dmg_me), (g ^ 1, dmg_op)):
        bonus = THREAT_SCALE * (dmg - THREAT_MIN_DMG + 1) if dmg >= THREAT_MIN_DMG else 0
        if bonus > THREAT_CAP:
            bonus = THREAT_CAP
        row = _WVS[side]
        econ = WALL_ECON if (side == g and econ_on) else 0
        for k in range(11):
            row[k] = WALL_VALUE[k] + (bonus if k > 0 else 0) + econ * k

    _STATE["mode"] = "search"
    code, kid = _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns,
                             t0, deadline, urg)
    if kid is not None:
        _HIST.add((b, kid[2], wb, kid[9], kid[7], kid[8], gb))
    return _code_to_action(code)


# Sổ khai cuộc: thế cờ -> nước đi. Khóa không phụ thuộc mình là P0 hay P1; chỉ dùng khi
# nước trong sổ hợp lệ. Dùng cho các thế mà tìm kiếm 1 giây dao động giữa hai nước gần bằng
# điểm nhưng kết quả khác hẳn.
OPENING_BOOK = {
    # Đi sau: đối thủ vừa đặt H43 chắn trước mặt (hai quân chéo nhau ở hàng 4-5) -> M43
    # (M45 thua 3/3 ván đo được, M43 thắng 2/2).
    '8|44|55|10|9|H43': ('M', 4, 3),
    # ...tiếp theo, đối thủ đi M45 sát bên -> H35 (thắng 5/5; H34 thua 2/2).
    '8|43|45|10|9|H43': ('H', 3, 5),
    # Đi trước: sau 3 nước đi thẳng mỗi bên -> M55 (vào thế '0|55|44' bên dưới, rồi M45:
    # thắng 4/4 đối thủ đặt tường chặn sớm).
    '0|54|34|10|10|': ('M', 5, 5),
    # Đi trước: hai quân chéo nhau ở hàng 4-5, chưa ai đặt tường -> M45 (thắng 19/27 ván trên
    # mọi đối thủ; H43 chỉ thắng ~85/350).
    '0|55|44|10|10|': ('M', 4, 5),
    # ...tiếp theo, sau H54 của mình và H25 của đối thủ -> H52 (thắng 4/4; V43 thua 2/2).
    '0|45|54|9|9|H25,H54': ('H', 5, 2),
    # Đi sau: đối thủ đứng ở hàng 5 và đặt H54 -> H34 (thắng 68/105; H33 32/102, M53 16/63).
    '8|34|54|10|9|H54': ('H', 3, 4),
    # Đi sau: đối thủ đứng ở hàng 5 và đặt H53 -> H34 (thắng 9/9; H33 7/39).
    '8|34|54|10|9|H53': ('H', 3, 4),
    # Đi sau: đối thủ đặt H53 rồi đứng chéo ở 44 -> M45 (thắng 19/26; H33 10/28).
    '8|35|44|10|9|H53': ('M', 4, 5),
}


def _book_action(obs):
    try:
        you, opp = obs["you"], obs["opponent"]
        ws = obs.get("walls") or {}
        walls = sorted(["H%d%d" % (r, c) for r, c in ws.get("horizontal", [])]
                       + ["V%d%d" % (r, c) for r, c in ws.get("vertical", [])])
        key = "%d|%d%d|%d%d|%d|%d|%s" % (
            you["goal_row"], you["position"][0], you["position"][1],
            opp["position"][0], opp["position"][1],
            you["walls_remaining"], opp["walls_remaining"], ",".join(walls))
        act = OPENING_BOOK.get(key)
        if act is None:
            return None
        if act[0] == "M":
            for m in obs.get("legal_moves") or []:
                if int(m[0]) == act[1] and int(m[1]) == act[2]:
                    return {"type": "move", "to": [act[1], act[2]]}
        else:
            for w in obs.get("legal_walls") or []:
                if w["orientation"] == act[0] and w["row"] == act[1] and w["col"] == act[2]:
                    return {"type": "wall", "orientation": act[0], "row": act[1], "col": act[2]}
    except Exception:
        pass
    return None


def choose_action(observation: dict) -> dict:
    t0 = perf_counter()
    action = None
    try:
        _STATE["moves"] += 1
        gc.collect(0)
        action = _book_action(observation)
        if action is None:
            action = _decide(observation, t0)
        if not _is_legal(action, observation):
            action = None
    except Exception:
        action = None
    if action is None:
        action = _fallback(observation)
    # Lề trễ thích nghi: trễ thì tăng lề ngay, đúng giờ thì giảm dần (mượt).
    try:
        late = perf_counter() - (t0 + TIME_LIMIT - _STATE["margin"])
        if late > 0.005:
            m = _STATE["margin"] + 1.5 * late
            _STATE["margin"] = m if m < 0.3 else 0.3
        else:
            _STATE["margin"] *= 0.85
    except Exception:
        pass
    return action
