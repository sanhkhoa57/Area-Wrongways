import gc
import sys
import time
from operator import itemgetter


TIME_BUDGET = 0.58
N = 9
D81 = 81
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

SHUFFLE_PENALTY = 1.0
SHUFFLE_WINDOW = 6
URG_START = 0.2
URG_BONUS = 3.0
DRAW_CONTEMPT = 0.5
DRAW_CONTEMPT_URG = 3.0

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

_D_TENSOR = [4 - abs((i % 9) - 4) for i in range(81)]

NEAR2 = []
for _a in range(81):
    _m = 0
    _ra, _ca = _a // 9, _a % 9
    for _b in range(81):
        if abs(_ra - _b // 9) + abs(_ca - _b % 9) <= 2:
            _m |= 1 << _b
    NEAR2.append(_m)

def _abit(r, c):
    return (1 << (r * 8 + c)) if (0 <= r < 8 and 0 <= c < 8) else 0

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
    return (1 << (o * 64 + r * 8 + c)) if (0 <= r < 8 and 0 <= c < 8) else 0

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
_TRACK = {}
_LAST_TURN = [-1]
_DEBUG = {}
WV = [WALL_VALUE] * 9
_S = {"deadline": 0.0, "nodes": 0, "reps": 0, "root_g": 0, "contempt": 0.0, "urg": 0.0}
_KILLERS = {}
_MYPOS = []
_PATH_SET = set()

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
 
    if not ((info[3] >> w) & 1):
        return info[0]

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

    hw2 = (hw | W_ABIT[w]) if w < 64 else hw
    vw2 = vw if w < 64 else (vw | W_ABIT[w])
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
    setup_cand = []
    while cand:
        low = cand & -cand
        cand ^= low
        w = low.bit_length() - 1
        d2 = dist_with_wall(pv, gv, info, w, U, D, L, R, hw, vw)
        if d2 >= INF:
            continue
        if d2 > dv:
            res.append((w, d2))
        elif d2 == dv:
            setup_cand.append((w, d2))
    if len(res) < 2 and setup_cand:
        for item in setup_cand[:2]:
            res.append(item)
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
            clean_bonus = 0.3 if dm2 == dm else 0.0
            lst.append((do2 - dm2 - WALL_VALUE + clean_bonus, 100 + w, dm2, do2))
    _WL_CACHE[key] = lst
    return lst

def wall_damage(pv, gv, pa, ga, U, D, L, R, hw, vw):
  
    ow = opp_walls(pv, gv, U, D, L, R, hw, vw)
    if not ow:
        return 0
    dv = bfs_dist(pv, gv, U, D, L, R, hw, vw)
    acut = None
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


def _corner_trap(p, g, opp_walls_left):
  
    if opp_walls_left <= 0:
        return 0.0
    r, c = p // 9, p % 9
    if (g == 8 and r == 7 and (c == 0 or c == 8)) or \
       (g == 0 and r == 1 and (c == 0 or c == 8)):
        return 1.2
    return 0.0

def evaluate(pm, po, gm, go, wm, wo, dm, do, vm, vo):
  
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

    if wm <= 3 and wo > wm:
        score -= 0.3 * (wo - wm)
    elif wo <= 3 and wm > wo:
        score += 0.3 * (wm - wo)

    score -= _corner_trap(pm, gm, wo)
    score += _corner_trap(po, go, wm)
    score += (_D_TENSOR[pm] - _D_TENSOR[po]) * 0.04

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

    stand = evaluate(pm, po, gm, go, wm, wo, dm, do, WV[gm], WV[go])
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
    a = WV[gm] - WALL_VALUE
    b = WV[go] - WALL_VALUE
    best = -INF * WIN
    best_code = None
    killers = _KILLERS.get(ply, ())
    lazy = (tt_code is None or tt_code < 100)

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
            kids = wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, FRONTIER_WALLS, killers)

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
                wm2 = wm
                if NEAR2[po] & BIT[pm2]:
                    s = -qsearch(po, pm2, go, gm, U, D, L, R, hw, vw, wo, wm2, do2, dm2,
                                 -beta, -max(alpha, best), ply + 1, QS_DEPTH)
                else:
                    s0 = -evaluate(po, pm2, go, gm, wo, wm2, do2, dm2, WALL_VALUE, WALL_VALUE)
                    if (wm2 == 0 and do2 <= dm2) or (wo == 0 and do2 > dm2):
                        s = s0
                    else:
                        s = s0 + a * (1.0 if wm2 > 0 else 0.0) - b * (1.0 if wo > 0 else 0.0)
            else:
                pm2 = pm
                wm2 = wm - 1
                w = code - 100
                hw2 = (hw | W_ABIT[w]) if w < 64 else hw
                vw2 = vw if w < 64 else (vw | W_ABIT[w])
                if NEAR2[po] & BIT[pm2]:
                    s = -qsearch(po, pm2, go, gm, U & KEEP_U[w], D & KEEP_D[w], L & KEEP_L[w],
                                 R & KEEP_R[w], hw2, vw2, wo, wm2, do2, dm2,
                                 -beta, -max(alpha, best), ply + 1, QS_DEPTH)
                else:
                    s0 = -evaluate(po, pm2, go, gm, wo, wm2, do2, dm2, WALL_VALUE, WALL_VALUE)
                    if (wm2 == 0 and do2 <= dm2) or (wo == 0 and do2 > dm2):
                        s = s0
                    else:
                        s = s0 + a * (1.0 if wm2 > 0 else 0.0) - b * (1.0 if wo > 0 else 0.0)

            if s > best:
                best = s
                best_code = code
                if s > alpha:
                    alpha = s
                    if alpha >= beta:
                        break
        if best >= beta:
            break

    if best == -INF * WIN:
        return None

    if best >= beta and best_code is not None and best_code >= 100:
        ks = _KILLERS.setdefault(ply, [])
        if best_code not in ks:
            ks.insert(0, best_code)
            del ks[2:]

    if _S["reps"] == reps0:
        flag = 0
        if best <= alpha0:
            flag = 2
        elif best >= beta:
            flag = 1
        _TT[key] = (1, flag, _to_tt(best, ply), best_code)
    return best

def negamax(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, depth, alpha, beta, ply):

    S = _S
    S["nodes"] += 1
    if _now() > S["deadline"]:
        raise _Timeout()

    if do == 0:
        return -(WIN - ply)

    if depth <= 0:
        if NEAR2[pm] & BIT[po]:
            return qsearch(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply, QS_DEPTH)
        return evaluate(pm, po, gm, go, wm, wo, dm, do, WV[gm], WV[go])

    goal = ROWMASK[gm]
    moves = pawn_moves(pm, po, U, D, L, R)
    for m in moves:
        if BIT[m] & goal:
            return WIN - ply - 1

    if wm == 0 and wo == 0 and abs(dm - do) >= 2:
        return evaluate(pm, po, gm, go, wm, wo, dm, do, WALL_VALUE, WALL_VALUE)

    key = (pm, po, gm, hw, vw, wm, wo)
    if ply > 0 and (key in _PATH_SET or _TRACK.get(key, 0) >= 2):
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

    lazy = (tt_code is None or tt_code < 100)
    killers = _KILLERS.get(ply, ())
    best = -INF * WIN
    best_code = None
    searched = 0
    done = False
    d1 = depth - 1

    _PATH_SET.add(key)
    try:
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
                    hw2 = (hw | W_ABIT[w]) if w < 64 else hw
                    vw2 = vw if w < 64 else (vw | W_ABIT[w])
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
    finally:
        _PATH_SET.remove(key)


def root_children(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do):
 
    out = []
    gl = goal_layers(gm, U, D, L, R, hw, vw)
    for m in pawn_moves(pm, po, U, D, L, R):
        dm2 = near_dist(gl, m, dm)
        out.append((do - dm2, m, dm2, do))

    if wm > 0:
        base_walls = wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, ROOT_WALLS,
                               _KILLERS.get(0, ()))
        out.extend(base_walls)

        if wo > 0 and WV[go] >= 3.0:
            existing_codes = {t[1] for t in base_walls}
            ainfo = pinfo(pm, gm, U, D, L, R, hw, vw)
            banned = banned_walls(hw, vw)
            def_added = 0
            for w_opp, d0_after in opp_walls(pm, gm, U, D, L, R, hw, vw):
                if d0_after - dm >= 4 and d0_after < INF:
                    cmask = (FORBID_H[w_opp] if w_opp < 64 else FORBID_V[w_opp - 64]) & ~banned
                    while cmask and def_added < 2:
                        low = cmask & -cmask
                        cmask ^= low
                        w_def = low.bit_length() - 1
                        code_def = 100 + w_def
                        if code_def not in existing_codes:
                            if dist_with_wall(pm, gm, ainfo, w_def, U, D, L, R, hw, vw) == dm:
                                do_after = bfs_dist(po, go, U & KEEP_U[w_def], D & KEEP_D[w_def],
                                                    L & KEEP_L[w_def], R & KEEP_R[w_def],
                                                    (hw | W_ABIT[w_def]) if w_def < 64 else hw,
                                                    vw if w_def < 64 else (vw | W_ABIT[w_def]))
                                if do_after < INF:
                                    out.append((do_after - dm - WALL_VALUE + 0.3, code_def, dm, do_after))
                                    existing_codes.add(code_def)
                                    def_added += 1

    out.sort(key=_K0, reverse=True)
    return out

def child_state(kid, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    _, code, dm2, do2 = kid
    if code < 100:
        return (po, code, go, gm, U, D, L, R, hw, vw, wo, wm, do2, dm2)
    w = code - 100
    hw2 = (hw | W_ABIT[w]) if w < 64 else hw
    vw2 = vw if w < 64 else (vw | W_ABIT[w])
    U2, D2, L2, R2 = U & KEEP_U[w], D & KEEP_D[w], L & KEEP_L[w], R & KEEP_R[w]
    return (po, pm, go, gm, U2, D2, L2, R2, hw2, vw2, wo, wm - 1, do2, dm2)

def search_root(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
  
    dm = bfs_dist(pm, gm, U, D, L, R, hw, vw)
    do = bfs_dist(po, go, U, D, L, R, hw, vw)
    kids = root_children(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do)
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
                rep = _TRACK.get((a[0], a[1], a[2], a[8], a[9], a[10], a[11]), 0)
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
            break
        if len(kids) == 1:
            break
        depth += 1

    if prev_code is not None:
        return prev_code, best_score, done_depth
    return best_code, best_score, done_depth


def _safe_escape(observation):
   
    try:
        moves = observation.get("legal_moves") or []
        dist = observation.get("distances")
        best, best_d = None, None
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

def _record_track(key):
    _TRACK[key] = _TRACK.get(key, 0) + 1

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
    new_game = (turn < _LAST_TURN[0])
    for cache, limit in ((_DIST_CACHE, 300000), (_PI_CACHE, 60000), (_GL_CACHE, 60000),
                         (_OW_CACHE, 60000), (_WL_CACHE, 100000), (_FV_CACHE, 100000),
                         (_BANNED_CACHE, 100000), (_TT, TT_LIMIT)):
        if new_game or len(cache) > limit:
            cache.clear()
    if new_game:
        _TRACK.clear()
        del _MYPOS[:]

    _MYPOS.append(pm)
    _LAST_TURN[0] = turn
    _PATH_SET.clear()
    gc.collect(0)

    _record_track((pm, po, gm, hw, vw, wm, wo))

    if wo == 0:
        dm = bfs_dist(pm, gm, U, D, L, R, hw, vw)
        do = bfs_dist(po, go, U, D, L, R, hw, vw)
        if dm <= do - 2:
            best_m = min(pawn_moves(pm, po, U, D, L, R),
                         key=lambda m: bfs_dist(m, gm, U, D, L, R, hw, vw), default=None)
            if best_m is not None and (best_m // 9, best_m % 9) in legal_moves:
                _dest = (best_m // 9, best_m % 9)
                _record_track((po, best_m, go, hw, vw, wo, wm))
                return {"type": "move", "to": [_dest[0], _dest[1]]}

    WV[gm] = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw)
    WV[go] = first_wall_value(pm, gm, po, go, U, D, L, R, hw, vw)

    frac = turn / float(observation.get("max_turns") or 500)
    urg = max(0.0, (frac - URG_START) / (1.0 - URG_START))
    _S["urg"] = urg
    _S["root_g"] = gm
    _S["contempt"] = DRAW_CONTEMPT + DRAW_CONTEMPT_URG * urg
    _S["deadline"] = start + TIME_BUDGET
    _S["nodes"] = 0
    _S["reps"] = 0
    _KILLERS.clear()

    code, score, depth = search_root(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
    _DEBUG["last"] = (depth, score, _S["nodes"])

    if code is None:
        return _safe_escape(observation)

    if code < 100:
        mv = (code // 9, code % 9)
        if mv in legal_moves:
            _record_track((po, code, go, hw, vw, wo, wm))
            return {"type": "move", "to": [mv[0], mv[1]]}
        return _safe_escape(observation)

    w = code - 100
    ori = "H" if w < 64 else "V"
    if (ori, W_ROW[w], W_COL[w]) in legal_walls:
        hw2 = (hw | W_ABIT[w]) if w < 64 else hw
        vw2 = vw if w < 64 else (vw | W_ABIT[w])
        _record_track((po, pm, go, hw2, vw2, wo, wm - 1))
        return {"type": "wall", "orientation": ori, "row": W_ROW[w], "col": W_COL[w]}

    return _safe_escape(observation)

def choose_action(observation):
  
    start = time.perf_counter()
    try:
        return decide(observation, start)
    except Exception:
        return _safe_escape(observation)
