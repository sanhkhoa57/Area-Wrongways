import gc
import time
from operator import itemgetter


TIME_BUDGET = 0.80
TIME_ADAPT = 1
_TM = {"margin": 0.0}
N = 9
INF = 999
WIN = 100000
DECIDED = 5000
WALL_VALUE = 1.25
TEMPO = 0.5
MAX_WALL_CHILDREN = 10
FRONTIER_WALLS = 6
ROOT_WALLS = 24
NO_WALL_PENALTY = 2.0
LEAD_CAP = 3
LEAD_SLOPE = 0.3
EPS = 0.001
DMG_SCALE = 0.5
DMG_MAX = 3.5
QS_DEPTH = 2
MAX_DEPTH = 40
TT_LIMIT = 200000

USE_THREAT = 0
USE_NMP = 0
NMP_R = 2
NMP_MIN_DEPTH = 3
SHUFFLE_PENALTY = 1.0
SHUFFLE_WINDOW = 6
URG_START = 0.2
URG_BONUS = 3.0
DRAW_CONTEMPT = 0.5
DRAW_CONTEMPT_URG = 3.0

FORT = 1
FORT_MIN_WALLS = 4
ROOT_PASS = 1
RACE_FIX = 1
PASS = 99
VERIFY_WIN = 1
GUARD_ROOT = 1
GUARD_THREATS = 3
GUARD_MIN = 3
GUARD_MIN_RED = 2
GUARD_K = 3
USE_PASS = 1

DMG_SPAN = DMG_MAX - WALL_VALUE

FULL = (1 << 81) - 1
BIT = [1 << i for i in range(81)]
ROWMASK = [((1 << 9) - 1) << (9 * r) for r in range(9)]
_COL0 = sum(BIT[r * 9] for r in range(9))
_COL8 = sum(BIT[r * 9 + 8] for r in range(9))
U0 = FULL & ~ROWMASK[0]
D0 = FULL & ~ROWMASK[8]
L0 = FULL & ~_COL0
R0 = FULL & ~_COL8

NEAR2 = []
for _a in range(81):
    _m = 0
    for _b in range(81):
        if abs(_a // 9 - _b // 9) + abs(_a % 9 - _b % 9) <= 2:
            _m |= 1 << _b
    NEAR2.append(_m)


def _abit(r, c):
    if 0 <= r < 8 and 0 <= c < 8:
        return 1 << (r * 8 + c)
    return 0


W_ROW, W_COL, W_ABIT = [], [], []
CLR_U, CLR_D, CLR_L, CLR_R = [], [], [], []
KEEP_U, KEEP_D, KEEP_L, KEEP_R = [], [], [], []
CONF_H, CONF_V = [], []
for _w in range(128):
    _o = 0 if _w < 64 else 1
    _r, _c = (_w % 64) // 8, _w % 8
    W_ROW.append(_r)
    W_COL.append(_c)
    W_ABIT.append(_abit(_r, _c))
    if _o == 0:
        CLR_D.append(BIT[_r * 9 + _c] | BIT[_r * 9 + _c + 1])
        CLR_U.append(BIT[(_r + 1) * 9 + _c] | BIT[(_r + 1) * 9 + _c + 1])
        CLR_L.append(0)
        CLR_R.append(0)
        CONF_H.append(_abit(_r, _c - 1) | _abit(_r, _c) | _abit(_r, _c + 1))
        CONF_V.append(_abit(_r, _c))
    else:
        CLR_R.append(BIT[_r * 9 + _c] | BIT[(_r + 1) * 9 + _c])
        CLR_L.append(BIT[_r * 9 + _c + 1] | BIT[(_r + 1) * 9 + _c + 1])
        CLR_U.append(0)
        CLR_D.append(0)
        CONF_V.append(_abit(_r - 1, _c) | _abit(_r, _c) | _abit(_r + 1, _c))
        CONF_H.append(_abit(_r, _c))
    KEEP_U.append(FULL & ~CLR_U[-1])
    KEEP_D.append(FULL & ~CLR_D[-1])
    KEEP_L.append(FULL & ~CLR_L[-1])
    KEEP_R.append(FULL & ~CLR_R[-1])
WTOUCH = [CLR_U[w] | CLR_D[w] | CLR_L[w] | CLR_R[w] for w in range(128)]


def _wbit(o, r, c):
    if 0 <= r < 8 and 0 <= c < 8:
        return 1 << (o * 64 + r * 8 + c)
    return 0


_CELL_U = [_wbit(0, i // 9 - 1, i % 9) | _wbit(0, i // 9 - 1, i % 9 - 1) for i in range(81)]
_CELL_D = [_wbit(0, i // 9, i % 9) | _wbit(0, i // 9, i % 9 - 1) for i in range(81)]
_CELL_L = [_wbit(1, i // 9, i % 9 - 1) | _wbit(1, i // 9 - 1, i % 9 - 1) for i in range(81)]
_CELL_R = [_wbit(1, i // 9, i % 9) | _wbit(1, i // 9 - 1, i % 9) for i in range(81)]


def _row_table(cell):
    tables = []
    for r in range(9):
        t = [0] * 512
        for chunk in range(1, 512):
            low = chunk & -chunk
            t[chunk] = t[chunk ^ low] | cell[r * 9 + low.bit_length() - 1]
        tables.append(t)
    return tables


TAB_U, TAB_D, TAB_L, TAB_R = (_row_table(x) for x in (_CELL_U, _CELL_D, _CELL_L, _CELL_R))
_SHIFTS = [(r, 9 * r) for r in range(9)]


def cut_mask(su, sd, sl, sr):
    m = 0
    for r, sh in _SHIFTS:
        m |= (TAB_U[r][(su >> sh) & 511] | TAB_D[r][(sd >> sh) & 511]
              | TAB_L[r][(sl >> sh) & 511] | TAB_R[r][(sr >> sh) & 511])
    return m


FORBID_H = [0] * 64
FORBID_V = [0] * 64
for _x in range(128):
    for _a in range(64):
        if CONF_H[_x] & (1 << _a):
            FORBID_H[_a] |= 1 << _x
        if CONF_V[_x] & (1 << _a):
            FORBID_V[_a] |= 1 << _x


class _Timeout(Exception):
    pass


_BANNED_CACHE = {}
_DIST_CACHE = {}
_PI_CACHE = {}
_GL_CACHE = {}
_OW_CACHE = {}
_WL_CACHE = {}
_FV_CACHE = {}
_TT = {}
_HISTORY = {}
_LAST_TURN = [-1]
_DEBUG = {}
WV = [WALL_VALUE] * 9
_S = {"deadline": 0.0, "nodes": 0, "reps": 0, "root_g": 0, "contempt": 0.0, "urg": 0.0,
      "fort_on": False}
_KILLERS = {}
_MYPOS = []

gc.disable()
_now = time.perf_counter
_K0 = itemgetter(0)


def banned_walls(hw, vw):
    key = (hw, vw)
    m = _BANNED_CACHE.get(key)
    if m is None:
        m = 0
        x = hw
        while x:
            low = x & -x
            m |= FORBID_H[low.bit_length() - 1]
            x ^= low
        x = vw
        while x:
            low = x & -x
            m |= FORBID_V[low.bit_length() - 1]
            x ^= low
        _BANNED_CACHE[key] = m
    return m


def masks_from_walls(hw, vw):
    u, d, l, r = U0, D0, L0, R0
    for w in range(128):
        placed = hw if w < 64 else vw
        if placed & W_ABIT[w]:
            u &= KEEP_U[w]
            d &= KEEP_D[w]
            l &= KEEP_L[w]
            r &= KEEP_R[w]
    return u, d, l, r


def bfs_dist(p, goal_row, U, D, L, R, hw, vw):
    key = (p, goal_row, hw, vw)
    val = _DIST_CACHE.get(key)
    if val is not None:
        return val
    goal = ROWMASK[goal_row]
    seen = front = BIT[p]
    dist = 0
    if not (front & goal):
        while True:
            dist += 1
            nb = (((front & U) >> 9) | ((front & D) << 9)
                  | ((front & L) >> 1) | ((front & R) << 1))
            front = nb & ~seen
            if not front:
                dist = INF
                break
            if front & goal:
                break
            seen |= front
    _DIST_CACHE[key] = dist
    return dist


def pinfo(p, goal_row, U, D, L, R, hw, vw):
    key = (p, goal_row, hw, vw)
    val = _PI_CACHE.get(key)
    if val is not None:
        return val
    goal = ROWMASK[goal_row]
    front = seen = BIT[p]
    layers = [front]
    seens = [seen]
    if front & goal:
        val = (0, layers, seens, 0, None, None, 0, 0, 0, 0)
    else:
        while True:
            nb = (((front & U) >> 9) | ((front & D) << 9)
                  | ((front & L) >> 1) | ((front & R) << 1))
            front = nb & ~seen
            if not front:
                val = (INF, layers, seens, 0, None, None, 0, 0, 0, 0)
                break
            seen |= front
            layers.append(front)
            seens.append(seen)
            if front & goal:
                d = len(layers) - 1
                sp = front & goal
                dl = [0] * (d + 1)
                dsuf = [0] * (d + 1)
                dl[d] = dsuf[d] = acc = sp
                su = sd = sl = sr = 0
                for k in range(d - 1, -1, -1):
                    lay = layers[k]
                    cu = lay & U & (sp << 9)
                    cd = lay & D & (sp >> 9)
                    cl = lay & L & (sp << 1)
                    cr = lay & R & (sp >> 1)
                    su |= cu
                    sd |= cd
                    sl |= cl
                    sr |= cr
                    sp = cu | cd | cl | cr
                    dl[k] = sp
                    acc |= sp
                    dsuf[k] = acc
                val = (d, layers, seens, cut_mask(su, sd, sl, sr), dl, dsuf, su, sd, sl, sr)
                break
    _PI_CACHE[key] = val
    _DIST_CACHE[key] = val[0]
    return val


def dist_with_wall(p, goal_row, info, w, U, D, L, R, hw, vw):
    dl = info[4]
    touch = WTOUCH[w]
    k = 0
    while not (dl[k] & touch):
        k += 1
    dsuf = info[5]
    last = len(dl) - 1
    su = info[6] & KEEP_U[w]
    sd = info[7] & KEEP_D[w]
    sl = info[8] & KEEP_L[w]
    sr = info[9] & KEEP_R[w]
    front = dl[k]
    while k < last and (dsuf[k] & touch):
        front = (((front & su) >> 9) | ((front & sd) << 9)
                 | ((front & sl) >> 1) | ((front & sr) << 1))
        if not front:
            break
        k += 1
    else:
        return info[0]
    if w < 64:
        hw2 = hw | W_ABIT[w]
        vw2 = vw
    else:
        hw2 = hw
        vw2 = vw | W_ABIT[w]
    key = (p, goal_row, hw2, vw2)
    val = _DIST_CACHE.get(key)
    if val is not None:
        return val
    U2 = U & KEEP_U[w]
    D2 = D & KEEP_D[w]
    L2 = L & KEEP_L[w]
    R2 = R & KEEP_R[w]
    layers = info[1]
    k = 0
    while not (layers[k] & touch):
        k += 1
    front = layers[k]
    seen = info[2][k]
    goal = ROWMASK[goal_row]
    dist = k
    while True:
        dist += 1
        nb = (((front & U2) >> 9) | ((front & D2) << 9)
              | ((front & L2) >> 1) | ((front & R2) << 1))
        front = nb & ~seen
        if not front:
            dist = INF
            break
        if front & goal:
            break
        seen |= front
    _DIST_CACHE[key] = dist
    return dist


def goal_layers(goal_row, U, D, L, R, hw, vw):
    key = (goal_row, hw, vw)
    val = _GL_CACHE.get(key)
    if val is None:
        front = seen = ROWMASK[goal_row]
        val = [front]
        while True:
            nb = (((front & U) >> 9) | ((front & D) << 9)
                  | ((front & L) >> 1) | ((front & R) << 1))
            front = nb & ~seen
            if not front:
                break
            seen |= front
            val.append(front)
        _GL_CACHE[key] = val
    return val


def near_dist(gl, m, d):
    k = d - 2 if d > 2 else 0
    b = BIT[m]
    while not (gl[k] & b):
        k += 1
    return k


ROW_OF = [i // 9 for i in range(81)]
COL_OF = [i % 9 for i in range(81)]
_REACH_CACHE = {}


def may_block(x, y, gy, dy):
    dr = ROW_OF[y] - ROW_OF[x]
    dc = COL_OF[y] - COL_OF[x]
    dg = ROW_OF[x] - gy
    return (dr if dr > 0 else -dr) + (dc if dc > 0 else -dc) + (dg if dg > 0 else -dg) <= dy


def reach_without(x, gy, U, D, L, R, hw, vw):
    key = (x, gy, hw, vw)
    val = _REACH_CACHE.get(key)
    if val is not None:
        return val
    bx = BIT[x]
    jumps = []
    for mask, delta, side1, d1, side2, d2 in (
            (U, -9, L, -1, R, 1), (D, 9, L, -1, R, 1),
            (L, -1, U, -9, D, 9), (R, 1, U, -9, D, 9)):
        m = x - delta
        if m < 0 or m > 80 or not (BIT[m] & mask):
            continue
        if bx & mask:
            dest = BIT[x + delta]
        else:
            dest = 0
            if bx & side1:
                dest |= BIT[x + d1]
            if bx & side2:
                dest |= BIT[x + d2]
        if dest:
            jumps.append((BIT[m], dest))
    goal = ROWMASK[gy]
    seen = goal | bx
    front = goal & ~bx
    while front:
        nxt = (((front & U) >> 9) | ((front & D) << 9)
               | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        for mb, dest in jumps:
            if (front & dest) and not (seen & mb):
                nxt |= mb
        seen |= nxt
        front = nxt
    val = seen & ~bx
    _REACH_CACHE[key] = val
    return val


_CUT_CACHE = {}


def sp_cut_cells(y, gy, U, D, L, R, hw, vw):
    key = (y, gy, hw, vw)
    m = _CUT_CACHE.get(key)
    if m is None:
        m = 0
        dl = pinfo(y, gy, U, D, L, R, hw, vw)[4]
        if dl:
            for k in range(1, len(dl)):
                c = dl[k]
                if not (c & (c - 1)):
                    m |= c
        _CUT_CACHE[key] = m
    return m


def fort_check(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do):
    s = evaluate(dm, do, wm, wo, WALL_VALUE, WALL_VALUE)
    if s > 0:
        if (may_block(po, pm, gm, dm) and BIT[po] & sp_cut_cells(pm, gm, U, D, L, R, hw, vw)
                and not (reach_without(po, gm, U, D, L, R, hw, vw) & BIT[pm])):
            return -_S["contempt"] if gm == _S["root_g"] else _S["contempt"]
    elif s < 0:
        if (may_block(pm, po, go, do) and BIT[pm] & sp_cut_cells(po, go, U, D, L, R, hw, vw)
                and not (reach_without(pm, go, U, D, L, R, hw, vw) & BIT[po])):
            return -_S["contempt"] if gm == _S["root_g"] else _S["contempt"]
    return None


def pawn_moves(p, q, U, D, L, R):
    res = []
    b = BIT[p]
    for mask, delta, side1, d1, side2, d2 in (
            (U, -9, L, -1, R, 1), (D, 9, L, -1, R, 1),
            (L, -1, U, -9, D, 9), (R, 1, U, -9, D, 9)):
        if not (b & mask):
            continue
        n = p + delta
        if n != q:
            res.append(n)
            continue
        nb = BIT[n]
        if nb & mask:
            res.append(n + delta)
        else:
            if nb & side1:
                res.append(n + d1)
            if nb & side2:
                res.append(n + d2)
    return res


def opp_walls(pv, gv, U, D, L, R, hw, vw):
    key = (pv, gv, hw, vw)
    res = _OW_CACHE.get(key)
    if res is not None:
        return res
    info = pinfo(pv, gv, U, D, L, R, hw, vw)
    dv = info[0]
    cand = info[3] & ~banned_walls(hw, vw)
    res = []
    while cand:
        low = cand & -cand
        cand ^= low
        w = low.bit_length() - 1
        d2 = dist_with_wall(pv, gv, info, w, U, D, L, R, hw, vw)
        if d2 == dv or d2 >= INF:
            continue
        res.append((w, d2))
    _OW_CACHE[key] = res
    return res


def wall_list(pm, po, gm, go, U, D, L, R, hw, vw, dm, do):
    key = (pm, po, gm, hw, vw)
    lst = _WL_CACHE.get(key)
    if lst is not None:
        return lst
    lst = []
    ow = opp_walls(po, go, U, D, L, R, hw, vw)
    if ow:
        info = pinfo(pm, gm, U, D, L, R, hw, vw)
        mycut = info[3]
        for w, do2 in ow:
            if (mycut >> w) & 1:
                dm2 = dist_with_wall(pm, gm, info, w, U, D, L, R, hw, vw)
                if dm2 >= INF:
                    continue
            else:
                dm2 = dm
            lst.append((do2 - dm2 - WALL_VALUE, 100 + w, dm2, do2))
    _WL_CACHE[key] = lst
    return lst


def wall_damage(pv, gv, pa, ga, U, D, L, R, hw, vw):
    ow = opp_walls(pv, gv, U, D, L, R, hw, vw)
    if not ow:
        return 0
    dv = bfs_dist(pv, gv, U, D, L, R, hw, vw)
    acut = None
    ainfo = None
    best = 0
    for w, d2 in ow:
        if d2 - dv <= best:
            continue
        if acut is None:
            ainfo = pinfo(pa, ga, U, D, L, R, hw, vw)
            acut = ainfo[3]
        if (acut >> w) & 1 and dist_with_wall(pa, ga, ainfo, w, U, D, L, R, hw, vw) >= INF:
            continue
        best = d2 - dv
    return best


def first_wall_value(pv, gv, pa, ga, U, D, L, R, hw, vw):
    key = (pv, gv, pa, hw, vw)
    v = _FV_CACHE.get(key)
    if v is None:
        v = DMG_SCALE * (1 + wall_damage(pv, gv, pa, ga, U, D, L, R, hw, vw))
        if v < WALL_VALUE:
            v = WALL_VALUE
        elif v > DMG_MAX:
            v = DMG_MAX
        _FV_CACHE[key] = v
    return v


def evaluate(dm, do, wm, wo, vm, vo):
    if wo == 0 and dm <= do:
        return DECIDED + (do - dm) * 10 - dm
    if wm == 0 and dm > do:
        return -DECIDED - (dm - do) * 10 + do
    lead = do - dm
    cap = LEAD_CAP + wo
    if lead > cap:
        lead = cap + (lead - cap) * LEAD_SLOPE
    score = lead + TEMPO + WALL_VALUE * (wm - wo)
    if wm > 0:
        score += vm - WALL_VALUE
    elif wo > 0:
        score -= NO_WALL_PENALTY
    if wo > 0:
        score -= vo - WALL_VALUE
    elif wm > 0:
        score += NO_WALL_PENALTY
    return score


def _to_tt(score, ply):
    if score > WIN - 1000:
        return score + ply
    if score < -(WIN - 1000):
        return score - ply
    return score


def _from_tt(score, ply):
    if score > WIN - 1000:
        return score - ply
    if score < -(WIN - 1000):
        return score + ply
    return score


def qsearch(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply, qd):
    S = _S
    S["nodes"] += 1
    if _now() > S["deadline"]:
        raise _Timeout()
    stand = evaluate(dm, do, wm, wo, WV[gm], WV[go])
    if qd <= 0 or stand >= beta:
        return stand
    if not (NEAR2[pm] & BIT[po]):
        return stand
    best = stand
    if stand > alpha:
        alpha = stand
    goal = ROWMASK[gm]
    gl = goal_layers(gm, U, D, L, R, hw, vw)
    for m in pawn_moves(pm, po, U, D, L, R):
        if BIT[m] & goal:
            return WIN - ply - 1
        dm2 = near_dist(gl, m, dm)
        s = -qsearch(po, m, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm2,
                     -beta, -alpha, ply + 1, qd - 1)
        if s > best:
            best = s
            if s > alpha:
                alpha = s
                if alpha >= beta:
                    break
    return best


def wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, max_w, killers):
    wl = wall_list(pm, po, gm, go, U, D, L, R, hw, vw, dm, do)
    if killers:
        ws = [((t[0] + 5.0, t[1], t[2], t[3]) if t[1] in killers else t) for t in wl]
        ws.sort(key=_K0, reverse=True)
    else:
        ws = sorted(wl, key=_K0, reverse=True)
    return ws[:max_w]


def frontier(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply,
             moves, key, tt_code, reps0):
    alpha0 = alpha
    if USE_THREAT and (USE_THREAT == 1 or gm != _S["root_g"]):
        a = 0.0 if wm == 0 else _FV_CACHE.get((po, go, pm, hw, vw))
        b = 0.0 if wo == 0 else _FV_CACHE.get((pm, gm, po, hw, vw))
        if a is not None and wm:
            a -= WALL_VALUE
        if b is not None and wo:
            b -= WALL_VALUE
    else:
        a = WV[gm] - WALL_VALUE
        b = WV[go] - WALL_VALUE
    recs = []
    best_lo = best_up = -INF * WIN
    code_lo = None
    killers = _KILLERS.get(ply, ())
    lazy = tt_code is None or tt_code < 100
    exact = a is not None and b is not None
    for phase in (0, 1):
        if phase == 0:
            gl = goal_layers(gm, U, D, L, R, hw, vw)
            kids = []
            for m in moves:
                dm2 = near_dist(gl, m, dm)
                kids.append((do - dm2, m, dm2, do))
            if not lazy and wm > 0:
                kids.extend(wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do,
                                      FRONTIER_WALLS, killers))
        else:
            if not lazy or wm == 0:
                break
            if not exact and best_up >= beta:
                a, b = _exact_ab(a, b, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
                exact = True
                best_lo, code_lo = _best_exact(recs, a, b)
                best_up = best_lo
                if best_lo >= beta:
                    break
            kids = wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, FRONTIER_WALLS, killers)
            if not exact and wm:
                a = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw) - WALL_VALUE
                exact = b is not None
                if exact:
                    best_lo, code_lo = _best_exact(recs, a, b)
                    best_up = best_lo
        kids.sort(key=_K0, reverse=True)
        if tt_code is not None:
            for i, k in enumerate(kids):
                if k[1] == tt_code:
                    if i:
                        kids.insert(0, kids.pop(i))
                    break
        A_lo = a if a is not None else 0.0
        A_hi = a if a is not None else DMG_SPAN
        B_lo = b if b is not None else 0.0
        B_hi = b if b is not None else DMG_SPAN
        for _, code, dm2, do2 in kids:
            if code < 100:
                pm2 = code
                wm2 = wm
                if NEAR2[po] & BIT[pm2]:
                    lo = max(alpha0, best_lo)
                    s = -qsearch(po, pm2, go, gm, U, D, L, R, hw, vw, wo, wm2, do2, dm2,
                                 -beta, -lo, ply + 1, QS_DEPTH)
                    recs.append((s, 0.0, 0.0, code))
                    lo_s = up_s = s
                else:
                    lo_s = up_s = None
            else:
                pm2 = pm
                wm2 = wm - 1
                if NEAR2[po] & BIT[pm2]:
                    w = code - 100
                    if w < 64:
                        hw2 = hw | W_ABIT[w]
                        vw2 = vw
                    else:
                        hw2 = hw
                        vw2 = vw | W_ABIT[w]
                    lo = max(alpha0, best_lo)
                    s = -qsearch(po, pm2, go, gm, U & KEEP_U[w], D & KEEP_D[w], L & KEEP_L[w],
                                 R & KEEP_R[w], hw2, vw2, wo, wm2, do2, dm2,
                                 -beta, -lo, ply + 1, QS_DEPTH)
                    recs.append((s, 0.0, 0.0, code))
                    lo_s = up_s = s
                else:
                    lo_s = up_s = None
            if lo_s is None or up_s is None:
                s0 = -evaluate(do2, dm2, wo, wm2, WALL_VALUE, WALL_VALUE)
                if (wm2 == 0 and do2 <= dm2) or (wo == 0 and do2 > dm2):
                    m1 = m2 = 0.0
                else:
                    m1 = 1.0 if wm2 > 0 else 0.0
                    m2 = 1.0 if wo > 0 else 0.0
                recs.append((s0, m1, m2, code))
                lo_s = s0 + A_lo * m1 - B_hi * m2
                up_s = s0 + A_hi * m1 - B_lo * m2
            if up_s > best_up:
                best_up = up_s
            if lo_s > best_lo:
                best_lo = lo_s
                code_lo = code
                if best_lo >= beta:
                    break
        if best_lo >= beta:
            break
    if USE_PASS and best_lo < beta and NEAR2[pm] & BIT[po] and gm != _S["root_g"]:
        lo = max(alpha0, best_lo)
        s = -qsearch(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm,
                     -beta, -lo, ply + 1, QS_DEPTH)
        recs.append((s, 0.0, 0.0, PASS))
        if s > best_up:
            best_up = s
        if s > best_lo:
            best_lo = s
            code_lo = PASS
    if best_lo >= beta:
        if code_lo is not None and code_lo >= 100:
            ks = _KILLERS.setdefault(ply, [])
            if code_lo not in ks:
                ks.insert(0, code_lo)
                del ks[2:]
        if _S["reps"] == reps0:
            _TT[key] = (1, 1, _to_tt(best_lo, ply), code_lo)
        return best_lo
    if not recs:
        return None
    if best_up <= alpha0:
        if _S["reps"] == reps0:
            _TT[key] = (1, 2, _to_tt(best_up, ply), code_lo)
        return best_up
    if not exact:
        a, b = _exact_ab(a, b, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
    best, code = _best_exact(recs, a, b)
    if best >= beta and code is not None and code >= 100:
        ks = _KILLERS.setdefault(ply, [])
        if code not in ks:
            ks.insert(0, code)
            del ks[2:]
    if _S["reps"] == reps0:
        flag = 0
        if best <= alpha0:
            flag = 2
        elif best >= beta:
            flag = 1
        _TT[key] = (1, flag, _to_tt(best, ply), code)
    return best


def _exact_ab(a, b, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    if a is None:
        a = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw) - WALL_VALUE
    if b is None:
        b = first_wall_value(pm, gm, po, go, U, D, L, R, hw, vw) - WALL_VALUE
    return a, b


def _best_exact(recs, a, b):
    best, code = -INF * WIN, None
    for s0, m1, m2, c in recs:
        s = s0 + a * m1 - b * m2
        if s > best:
            best, code = s, c
    return best, code


def negamax(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, depth, alpha, beta, ply,
            can_null=True):
    S = _S
    S["nodes"] += 1
    if _now() > S["deadline"]:
        raise _Timeout()
    if do == 0:
        return -(WIN - ply)
    if depth >= 2 and _S["fort_on"]:
        f = fort_check(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do)
        if f is not None:
            return f
    if depth <= 0:
        if NEAR2[pm] & BIT[po]:
            return qsearch(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply,
                           QS_DEPTH)
        return evaluate(dm, do, wm, wo, WV[gm], WV[go])
    goal = ROWMASK[gm]
    moves = pawn_moves(pm, po, U, D, L, R)
    for m in moves:
        if BIT[m] & goal:
            return WIN - ply - 1
    if wm == 0 and wo == 0 and abs(dm - do) >= 2:
        return evaluate(dm, do, wm, wo, WALL_VALUE, WALL_VALUE)

    key = (pm, po, gm, hw, vw, wm, wo)
    if ply > 0 and _HISTORY.get(key, 0) >= 2:
        S["reps"] += 1
        return -S["contempt"] if gm == S["root_g"] else S["contempt"]
    tt = _TT
    entry = tt.get(key)
    tt_code = None
    alpha0 = alpha
    if entry is not None:
        e_depth, e_flag, e_score, tt_code = entry
        if e_depth >= depth:
            e_score = _from_tt(e_score, ply)
            if e_flag == 0:
                return e_score
            if e_flag == 1 and e_score > alpha:
                alpha = e_score
            elif e_flag == 2 and e_score < beta:
                beta = e_score
            if alpha >= beta:
                return e_score
    reps0 = S["reps"]

    if depth == 1:
        r = frontier(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply,
                     moves, key, tt_code, reps0)
        if r is not None:
            return r
        return -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm, 0, -beta, -alpha, ply + 1)

    if (USE_NMP and can_null and depth >= NMP_MIN_DEPTH and beta < DECIDED
            and not (NEAR2[pm] & BIT[po]) and evaluate(dm, do, wm, wo, WV[gm], WV[go]) >= beta):
        score = -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm,
                         depth - 1 - NMP_R, -beta, -beta + EPS, ply + 1, False)
        if score >= beta:
            return score

    lazy = tt_code is None or tt_code < 100
    killers = _KILLERS.get(ply, ())
    best = -INF * WIN
    best_code = None
    searched = 0
    done = False
    d1 = depth - 1
    for phase in (0, 1):
        if phase == 0:
            gl = goal_layers(gm, U, D, L, R, hw, vw)
            kids = []
            for m in moves:
                dm2 = near_dist(gl, m, dm)
                kids.append((do - dm2, m, dm2, do))
            if not lazy and wm > 0:
                kids.extend(wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do,
                                      MAX_WALL_CHILDREN, killers))
        else:
            if not lazy or wm == 0:
                break
            kids = wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, MAX_WALL_CHILDREN, killers)
        kids.sort(key=_K0, reverse=True)
        if tt_code is not None:
            for i, k in enumerate(kids):
                if k[1] == tt_code:
                    if i:
                        kids.insert(0, kids.pop(i))
                    break
        for _, code, dm2, do2 in kids:
            if code < 100:
                pm2 = code
                U2, D2, L2, R2, hw2, vw2, wm2 = U, D, L, R, hw, vw, wm
            else:
                w = code - 100
                pm2 = pm
                if w < 64:
                    hw2 = hw | W_ABIT[w]
                    vw2 = vw
                else:
                    hw2 = hw
                    vw2 = vw | W_ABIT[w]
                U2 = U & KEEP_U[w]
                D2 = D & KEEP_D[w]
                L2 = L & KEEP_L[w]
                R2 = R & KEEP_R[w]
                wm2 = wm - 1
            if searched == 0:
                score = -negamax(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2,
                                 wo, wm2, do2, dm2, d1, -beta, -alpha, ply + 1)
            else:
                score = -negamax(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2,
                                 wo, wm2, do2, dm2, d1, -alpha - EPS, -alpha, ply + 1)
                if score > alpha and score < beta:
                    score = -negamax(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2,
                                     wo, wm2, do2, dm2, d1, -beta, -alpha, ply + 1)
            searched += 1
            if score > best:
                best = score
                best_code = code
            if score > alpha:
                alpha = score
            if alpha >= beta:
                if code >= 100:
                    ks = _KILLERS.setdefault(ply, [])
                    if code not in ks:
                        ks.insert(0, code)
                        del ks[2:]
                done = True
                break
        if done:
            break
    if (USE_PASS and not done and searched and NEAR2[pm] & BIT[po] and gm != _S["root_g"]):
        score = -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm,
                         d1, -alpha - EPS, -alpha, ply + 1)
        if score > alpha and score < beta:
            score = -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm,
                             d1, -beta, -alpha, ply + 1)
        if score > best:
            best = score
            best_code = PASS
        if score > alpha:
            alpha = score
    if searched == 0:
        return -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm,
                        do, dm, depth - 1, -beta, -alpha, ply + 1)
    if S["reps"] == reps0:
        flag = 0
        if best <= alpha0:
            flag = 2
        elif best >= beta:
            flag = 1
        tt[key] = (depth, flag, _to_tt(best, ply), best_code)
    return best


_GUARD_BONUS = {}


def _wv(dmg):
    v = DMG_SCALE * (1 + dmg)
    return WALL_VALUE if v < WALL_VALUE else (DMG_MAX if v > DMG_MAX else v)


def guard_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do):
    threats = opp_walls(pm, gm, U, D, L, R, hw, vw)
    if not threats:
        return []
    top = sorted(threats, key=lambda t: -t[1])[:GUARD_THREATS]
    if top[0][1] - dm < GUARD_MIN:
        return []
    before = wall_damage(pm, gm, po, go, U, D, L, R, hw, vw)
    if before < GUARD_MIN:
        return []
    cand = 0
    for w, d2 in top:
        if d2 - dm < 2:
            break
        a = w & 63
        cand |= FORBID_H[a] if w < 64 else FORBID_V[a]
    cand &= ~banned_walls(hw, vw)
    out = []
    while cand:
        low = cand & -cand
        cand ^= low
        c = low.bit_length() - 1
        if c < 64:
            hw2, vw2 = hw | W_ABIT[c], vw
        else:
            hw2, vw2 = hw, vw | W_ABIT[c]
        U2, D2, L2, R2 = U & KEEP_U[c], D & KEEP_D[c], L & KEEP_L[c], R & KEEP_R[c]
        if bfs_dist(pm, gm, U2, D2, L2, R2, hw2, vw2) != dm:
            continue
        do2 = bfs_dist(po, go, U2, D2, L2, R2, hw2, vw2)
        if do2 >= INF or do2 > do:
            continue
        after = wall_damage(pm, gm, po, go, U2, D2, L2, R2, hw2, vw2)
        if before - after >= GUARD_MIN_RED:
            out.append((before - after, c, do2))
    out.sort(reverse=True)
    kids = []
    for red, c, do2 in out[:GUARD_K]:
        code = 100 + c
        _GUARD_BONUS[code] = _wv(before) - _wv(before - red)
        kids.append((do2 - dm - WALL_VALUE, code, dm, do2))
    return kids


def root_children(pm, po, gm, go, U, D, L, R, hw, vw, wm, dm, do):
    out = []
    gl = goal_layers(gm, U, D, L, R, hw, vw)
    for m in pawn_moves(pm, po, U, D, L, R):
        dm2 = near_dist(gl, m, dm)
        out.append((do - dm2, m, dm2, do))
    if wm > 0:
        out.extend(wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, ROOT_WALLS,
                             _KILLERS.get(0, ())))
    _GUARD_BONUS.clear()
    if GUARD_ROOT and wm > 0:
        have = {k[1] for k in out}
        out.extend(k for k in guard_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do)
                   if k[1] not in have)
    if (ROOT_PASS and _S["fort_on"] and may_block(pm, po, go, do)
            and not (reach_without(pm, go, U, D, L, R, hw, vw) & BIT[po])):
        out.append((do - dm, PASS, dm, do))
    out.sort(key=_K0, reverse=True)
    return out


def child_state(kid, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    _, code, dm2, do2 = kid
    if code == PASS:
        return (po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do2, dm2)
    if code < 100:
        return (po, code, go, gm, U, D, L, R, hw, vw, wo, wm, do2, dm2)
    w = code - 100
    if w < 64:
        hw2, vw2 = hw | W_ABIT[w], vw
    else:
        hw2, vw2 = hw, vw | W_ABIT[w]
    U2, D2, L2, R2 = U & KEEP_U[w], D & KEEP_D[w], L & KEEP_L[w], R & KEEP_R[w]
    return (po, pm, go, gm, U2, D2, L2, R2, hw2, vw2, wo, wm - 1, do2, dm2)


def search_root(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    dm = bfs_dist(pm, gm, U, D, L, R, hw, vw)
    do = bfs_dist(po, go, U, D, L, R, hw, vw)
    kids = root_children(pm, po, gm, go, U, D, L, R, hw, vw, wm, dm, do)
    if not kids:
        return None, 0, 0
    entry = _TT.get((pm, po, gm, hw, vw, wm, wo))
    if entry is not None and entry[3] is not None:
        for i, k in enumerate(kids):
            if k[1] == entry[3]:
                kids.insert(0, kids.pop(i))
                break
    states = {k[1]: child_state(k, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo) for k in kids}
    recent = set(_MYPOS[-SHUFFLE_WINDOW - 1:-1]) if SHUFFLE_PENALTY else ()
    best_code, best_score, done_depth = kids[0][1], -INF * WIN, 0
    prev_code = None
    scores = {}
    depth = 1
    while depth <= MAX_DEPTH:
        if scores:
            kids.sort(key=lambda t: -scores.get(t[1], -INF * WIN))
        alpha, beta = -INF * WIN, INF * WIN
        iter_best, iter_code = -INF * WIN, None
        new_scores = {}
        first = kids[0][1]
        proven = None
        try:
            for i, k in enumerate(kids):
                code = k[1]
                args = states[code]
                if i == 0:
                    s = -negamax(*args, depth - 1, -beta, -alpha, 1)
                else:
                    s = -negamax(*args, depth - 1, -alpha - EPS, -alpha, 1)
                    if s > alpha:
                        proven = code
                        s = -negamax(*args, depth - 1, -beta, -alpha, 1)
                        proven = None
                a = args
                rep = _HISTORY.get((a[0], a[1], a[2], a[8], a[9], a[10], a[11]), 0)
                if code in _GUARD_BONUS:
                    s += _GUARD_BONUS[code]
                if rep >= 1 and s > 0:
                    s -= 0.3 * rep
                if code < 100:
                    urg = _S["urg"]
                    if code in recent and k[2] >= dm:
                        s -= SHUFFLE_PENALTY * (1.0 + urg)
                    if urg:
                        s += URG_BONUS * urg * (dm - k[2])
                new_scores[code] = s
                if s > iter_best or iter_code is None:
                    iter_best, iter_code = s, code
                if s > alpha:
                    alpha = s
        except _Timeout:
            if done_depth > 0:
                if proven is not None:
                    best_code = proven
                elif iter_code is not None and iter_code != first:
                    best_code, best_score = iter_code, iter_best
            break
        scores = new_scores
        if iter_best <= -(WIN - 1000) and done_depth > 0 and best_score > -(WIN - 1000):
            prev_code = best_code
            break
        best_code, best_score, done_depth = iter_code, iter_best, depth
        if abs(best_score) >= WIN - 1000:
            if not (VERIFY_WIN and _S["fort_on"] and best_score > 0 and WIN - best_score > 1):
                break
        if len(kids) == 1:
            break
        depth += 1
    if prev_code is not None:
        return prev_code, best_score, done_depth
    return best_code, best_score, done_depth


def safe_action(observation):
    try:
        moves = observation.get("legal_moves") or []
        dist = observation.get("distances")
        best, best_d = None, 0
        for mv in moves:
            d = dist[mv[0]][mv[1]] if dist else 0
            if d < 0:
                d = 10 ** 6
            if best is None or d < best_d:
                best, best_d = mv, d
        if best is not None:
            return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception:
        pass
    return {"type": "pass"}


def _record_history(key):
    _HISTORY[key] = _HISTORY.get(key, 0) + 1


def decide(observation, start):
    you, opp = observation["you"], observation["opponent"]
    pm = you["position"][0] * 9 + you["position"][1]
    po = opp["position"][0] * 9 + opp["position"][1]
    gm, go = int(you["goal_row"]), int(opp["goal_row"])
    wm, wo = int(you["walls_remaining"]), int(opp["walls_remaining"])
    legal_moves = [tuple(m) for m in observation.get("legal_moves") or []]
    legal_walls = set()
    for lw in observation.get("legal_walls") or []:
        legal_walls.add((lw["orientation"], int(lw["row"]), int(lw["col"])))

    for m in legal_moves:
        if m[0] == gm:
            return {"type": "move", "to": [int(m[0]), int(m[1])]}

    hw = vw = 0
    for r, c in observation["walls"]["horizontal"]:
        hw |= _abit(int(r), int(c))
    for r, c in observation["walls"]["vertical"]:
        vw |= _abit(int(r), int(c))
    U, D, L, R = masks_from_walls(hw, vw)

    turn = int(observation.get("turn", 0))
    new_game = turn < _LAST_TURN[0]
    for cache, limit in ((_DIST_CACHE, 300000), (_PI_CACHE, 60000), (_GL_CACHE, 60000),
                         (_OW_CACHE, 60000), (_WL_CACHE, 100000), (_FV_CACHE, 100000),
                         (_BANNED_CACHE, 100000), (_TT, TT_LIMIT), (_REACH_CACHE, 50000),
                         (_CUT_CACHE, 100000)):
        if new_game or len(cache) > limit:
            cache.clear()
    if new_game:
        _HISTORY.clear()
        del _MYPOS[:]
    _MYPOS.append(pm)
    _LAST_TURN[0] = turn
    gc.collect(0)
    _record_history((pm, po, gm, hw, vw, wm, wo))

    if wo == 0:
        dm = bfs_dist(pm, gm, U, D, L, R, hw, vw)
        do = bfs_dist(po, go, U, D, L, R, hw, vw)
        if dm <= do - 2 and not (RACE_FIX and may_block(po, pm, gm, dm)):
            best = min(pawn_moves(pm, po, U, D, L, R),
                       key=lambda m: bfs_dist(m, gm, U, D, L, R, hw, vw), default=None)
            if best is not None and (best // 9, best % 9) in legal_moves:
                return {"type": "move", "to": [best // 9, best % 9]}

    WV[gm] = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw)
    WV[go] = first_wall_value(pm, gm, po, go, U, D, L, R, hw, vw)
    frac = turn / float(observation.get("max_turns") or 500)
    urg = max(0.0, (frac - URG_START) / (1.0 - URG_START))
    _S["urg"] = urg
    _S["root_g"] = gm
    _S["contempt"] = DRAW_CONTEMPT + DRAW_CONTEMPT_URG * urg
    _S["deadline"] = start + TIME_BUDGET - _TM["margin"]
    _S["fort_on"] = bool(FORT) and (bin(hw).count("1") + bin(vw).count("1")) >= FORT_MIN_WALLS
    _S["nodes"] = 0
    _S["reps"] = 0
    _KILLERS.clear()
    code, score, depth = search_root(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
    _DEBUG["last"] = (depth, score, _S["nodes"])
    if code is None:
        return safe_action(observation)

    if code == PASS:
        _record_history((po, pm, go, hw, vw, wo, wm))
        return {"type": "pass"}
    if code < 100:
        mv = (code // 9, code % 9)
        if mv in legal_moves:
            _record_history((po, code, go, hw, vw, wo, wm))
            return {"type": "move", "to": [mv[0], mv[1]]}
        return safe_action(observation)
    w = code - 100
    ori = "H" if w < 64 else "V"
    if (ori, W_ROW[w], W_COL[w]) in legal_walls:
        hw2 = hw | W_ABIT[w] if w < 64 else hw
        vw2 = vw | W_ABIT[w] if w >= 64 else vw
        _record_history((po, pm, go, hw2, vw2, wo, wm - 1))
        return {"type": "wall", "orientation": ori, "row": W_ROW[w], "col": W_COL[w]}
    return safe_action(observation)


def choose_action(observation):
    start = time.perf_counter()
    try:
        action = decide(observation, start)
    except Exception:
        action = safe_action(observation)
    if TIME_ADAPT:
        late = time.perf_counter() - (start + TIME_BUDGET - _TM["margin"])
        if late > 0.02:
            _TM["margin"] = min(0.25, _TM["margin"] + 2 * late)
        else:
            _TM["margin"] *= 0.97
    return action