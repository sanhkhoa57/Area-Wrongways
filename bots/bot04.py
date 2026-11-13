import gc
from collections import deque
from time import perf_counter

gc.disable()

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

H_BASE = 100
V_BASE = 200
PASS = 999

USE_SP_CACHE = True
USE_REP_FIX = True
USE_SHAPE_FIX = True
USE_GATE_DEF = True
USE_LAST_WALL_GUARD = True
STRICT_MODE = False
USE_DEAD = True
DEAD_NEAR = 4
DEAD_LIMIT = 20000
FIX_GUARD_SCORE = True     
USE_JUMP_Q = True           
DIAG_PV = True              
COLD_START_RESERVE = 0.08   
USE_LEAF_SHAPE = True      
FIX_CONTEMPT_TT = True      
STEP = 10
TEMPO = 5
RACE_BONUS = 60

_WALL_MARGINAL = [24, 18, 14, 12, 12, 12, 12, 12, 12, 12]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]

MULT_BONUS = 1         
CORRIDOR_PEN = 2       
DETOUR_PEN = 1         
URG_DEF_BONUS = 12     
CONTEMPT = 25
CONTEMPT_URG = 15

ROOT_PASS_PENALTY = 12
ROOT_REPEAT_PENALTY = 3
SHUFFLE_PENALTY = 5
ROOT_RECENT_WINDOW = 6
URG_START = 0.55
URG_BONUS = 6


TIME_LIMIT = 0.80
ITER_FACTOR = 0.30
WIN = 100000
MATE_ZONE = WIN - 1000
ASP_DELTA = 200000
ASP_GROW = 2.5
LMR_FULL_WALLS = 1
LMR2_IDX = 0
TOP_WALLS_D4 = 9
TOP_WALLS_D3 = 9
TOP_WALLS_D2 = 9
NMP_R = 0
COUNTER = True
CONTACT_PASS = True
EG_PROBE = True
HIST_CAP = 8
HIST_HALF = 64
BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 15
PATH_CACHE_LIMIT = 16000
TT_LIMIT = 150000

_TT = {}
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0, "goals": None}
_HIST = set()
_ACTIVE_RECENT = []
_HISTORY = [0] * 300
_KILLERS = [[None, None] for _ in range(128)]
_COUNTER = [None] * (PASS + 1)
_TRIED = [[0] * (PASS + 1) for _ in range(128)]
_STAMP = [0]
_ROOT_GOAL = [0]
_CONTEMPT_CUR = [CONTEMPT]
_URG_CUR = [0.0]
_BLOCK = [False]
_BCACHE = {}
_BQ = {}
_PATH_CACHE = {}
_DEAD = {}

_DIAG = {}
_EGP = [None]
_DEADLINE = [0.0]

_REP = [0]
for _i in range(140):
    _REP.append(_REP[-1] | (1 << (81 * _i)))


class _TimeUp(Exception):
    pass


def _to_tt(score, ply):
    if score > MATE_ZONE:
        return score + ply
    if score < -MATE_ZONE:
        return score - ply
    return score


def _draw(goal):
    c = _CONTEMPT_CUR[0]
    return -c if goal == _ROOT_GOAL[0] else c


def _top_walls(depth):
    return TOP_WALLS_D4 if depth >= 4 else (TOP_WALLS_D3 if depth == 3 else TOP_WALLS_D2)



def _layers(goal, target, U, D, L, R, extra):
    key = (goal, U, D, L, R)
    entry = _PATH_CACHE.get(key)
    if entry is None:
        if len(_PATH_CACHE) >= PATH_CACHE_LIMIT:
            _PATH_CACHE.clear()
        entry = [[goal], goal, {}, {}]
        _PATH_CACHE[key] = entry
    layers, seen, known, _ = entry
    hit = known.get(target)
    if hit is not None:
        if hit >= 0 and len(layers) > hit + extra:
            return layers, hit
    else:
        hit = -1
        i = 0
        for front in layers:
            if front & target:
                hit = i
                break
            i += 1
        if hit >= 0 and len(layers) > hit + extra:
            known[target] = hit
            return layers, hit
    front = layers[-1]
    while front and (hit < 0 or len(layers) <= hit + extra):
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        layers.append(front)
        seen |= front
        if hit < 0 and (front & target):
            hit = len(layers) - 1
    entry[1] = seen
    known[target] = hit
    return layers, hit


def _layers_at(goal, need, U, D, L, R):
    key = (goal, U, D, L, R)
    entry = _PATH_CACHE.get(key)
    if entry is None:
        if len(_PATH_CACHE) >= PATH_CACHE_LIMIT:
            _PATH_CACHE.clear()
        entry = [[goal], goal, {}, {}]
        _PATH_CACHE[key] = entry
    layers = entry[0]
    if len(layers) <= need:
        seen = entry[1]
        front = layers[-1]
        while front and len(layers) <= need:
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            layers.append(front)
            seen |= front
        entry[1] = seen
    return layers


def _dist(start, goal, U, D, L, R):
    d = _layers(goal, start, U, D, L, R, 0)[1]
    return d if d >= 0 else INF


def _sp_edges(pos, layers, d, U, D, L, R):
    """Extract directed edge masks (up, dn, lt, rt) on shortest paths from `pos`."""
    if d <= 0:
        return 0, 0, 0, 0
    if USE_SP_CACHE:
        entry = _PATH_CACHE.get((layers[0], U, D, L, R))
        if entry is not None:
            ec = entry[3]
            res = ec.get(pos)
            if res is not None:
                return res
        else:
            ec = None
    else:
        ec = None
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
    res = (up, dn, lt, rt)
    if ec is not None:
        ec[pos] = res
    return res


def _pawn_moves(a, b, U, D, L, R):
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



def _race_adj(dm, do, wm, wo):
    if wo == 0 and dm <= do:
        return RACE_BONUS
    if wm == 0 and dm > do:
        return -RACE_BONUS
    return 0


def _static(dm, do, wm, wo):
    return STEP * (do - dm) + TEMPO + WALL_VALUE[wm] - WALL_VALUE[wo] + _race_adj(dm, do, wm, wo)


def _shape(p, d, layers, U, D, L, R):
    """Shortest-path wavefront resilience & forward-detour modifier for pawn `p`."""
    if d < 2:
        return 0
    if not USE_SHAPE_FIX:
        nxt = layers[d - 1]
        n = 0
        vertical = False
        if p & U and (p >> 9) & nxt:
            n += 1
            vertical = True
        if p & D and (p << 9) & nxt:
            n += 1
            vertical = True
        if p & L and (p >> 1) & nxt:
            n += 1
        if p & R and (p << 1) & nxt:
            n += 1
        if n >= 2:
            return MULT_BONUS
        if vertical:
            if not (p & L) and not (p & R):
                return -CORRIDOR_PEN
        else:
            if not (p & U) and not (p & D):
                return -CORRIDOR_PEN
            if not (p & U) or not (p & D):
                return -DETOUR_PEN
        return 0

    nxt = layers[d - 1]
    w1 = (((p & U) >> 9) | ((p & D) << 9) | ((p & L) >> 1) | ((p & R) << 1)) & nxt
    if w1 & (w1 - 1):
        if d < 3:
            return MULT_BONUS
        w2 = (((w1 & U) >> 9) | ((w1 & D) << 9) | ((w1 & L) >> 1) | ((w1 & R) << 1)) & layers[d - 2]
        return MULT_BONUS if (w2 & (w2 - 1)) else 0
    if w1 & ((p >> 9) | (p << 9)):
        if not (p & L) and not (p & R):
            return -CORRIDOR_PEN
    else:
        if not (p & U) and not (p & D):
            return -CORRIDOR_PEN
        if DETOUR_PEN:
            if (layers[0] & 1) and not (p & U):
                return -DETOUR_PEN
            if not (layers[0] & 1) and not (p & D):
                return -DETOUR_PEN
    return 0


def _shape_n(p, d, nxt, U, D, L, R, goal=0, nxt2=0):
    if d < 2:
        return 0
    if not USE_SHAPE_FIX:
        n = 0
        vertical = False
        if p & U and (p >> 9) & nxt:
            n += 1
            vertical = True
        if p & D and (p << 9) & nxt:
            n += 1
            vertical = True
        if p & L and (p >> 1) & nxt:
            n += 1
        if p & R and (p << 1) & nxt:
            n += 1
        if n >= 2:
            return MULT_BONUS
        if vertical:
            if not (p & L) and not (p & R):
                return -CORRIDOR_PEN
        else:
            if not (p & U) and not (p & D):
                return -CORRIDOR_PEN
            if not (p & U) or not (p & D):
                return -DETOUR_PEN
        return 0

    w1 = (((p & U) >> 9) | ((p & D) << 9) | ((p & L) >> 1) | ((p & R) << 1)) & nxt
    if w1 & (w1 - 1):
        if d < 3 or not nxt2:
            return MULT_BONUS
        w2 = (((w1 & U) >> 9) | ((w1 & D) << 9) | ((w1 & L) >> 1) | ((w1 & R) << 1)) & nxt2
        return MULT_BONUS if (w2 & (w2 - 1)) else 0
    if w1 & ((p >> 9) | (p << 9)):
        if not (p & L) and not (p & R):
            return -CORRIDOR_PEN
    else:
        if not (p & U) and not (p & D):
            return -CORRIDOR_PEN
        if DETOUR_PEN and goal:
            if (goal & 1) and not (p & U):
                return -DETOUR_PEN
            if not (goal & 1) and not (p & D):
                return -DETOUR_PEN
    return 0


def _jumps(o, U, D, L, R):
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
    key = (o, hw, vw, goal)
    m = _BCACHE.get(key)
    if m is None:
        if a & goal:
            return False
        seen = a | o
        front = a
        for _ in range(d):
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
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


def _blocked_q(a, o, goal, U, D, L, R, hw, vw, d):
    key = (a, o, hw, vw, goal)
    r = _BQ.get(key)
    if r is None:
        r = (_may_block(a, o, 0 if goal & 1 else 8, d)
             and _blocked(a, o, goal, U, D, L, R, hw, vw, d))
        _BQ[key] = r
    return r


def _dead(t, gt, U, D, L, R, hw, vw, dt):

    key = (t, hw, vw, gt)
    r = _DEAD.get(key)
    if r is not None:
        return r
    entry = _PATH_CACHE.get((gt, U, D, L, R))
    if entry is not None and len(entry[0]) > dt:
        if not (t & entry[0][dt]):
            return False    
        ou, od, ol, orr = _sp_edges(t, entry[0], dt, U, D, L, R)
    else:
        lay = [gt]
        seen = front = gt
        for _ in range(dt):
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            seen |= front
            lay.append(front)
        if not (t & lay[dt]):
            return False     
        ou = od = ol = orr = 0
        cur = t
        for k in range(dt, 0, -1):
            nxt = lay[k - 1]
            u = cur & U & (nxt << 9)
            w = cur & D & (nxt >> 9)
            l = cur & L & (nxt << 1)
            r = cur & R & (nxt >> 1)
            ou |= u
            od |= w
            ol |= l
            orr |= r
            cur = (u >> 9) | (w << 9) | (l >> 1) | (r << 1)
    if len(_DEAD) >= DEAD_LIMIT:
        _DEAD.clear()
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v

    lh = COL[0] | (hw << 2) | (vw << 1) | (vw >> 8) | (vw << 10)
    rh = COL[7] | (hw >> 2) | (vw >> 1) | (vw >> 10) | (vw << 8)
    mh = (vw >> 9) | (vw << 9)
    tv = ROW[0] | (vw << 18) | (hw << 9) | (hw << 8) | (hw << 10)
    bv = ROW[7] | (vw >> 18) | (hw >> 9) | (hw >> 10) | (hw >> 8)
    mv = (hw >> 1) | (hw << 1)
    if (cand_h & ~((lh & rh) | (lh & mh) | (rh & mh))) or \
            (cand_v & ~((tv & bv) | (tv & mv) | (bv & mv))):
        _DEAD[key] = False
        return False
    r = True
    if cand_h | cand_v:
        n = 0
        cU = cD = cL = cR = 0
        m = cand_h
        while m:
            w = m & -m
            m ^= w
            s = 81 * n
            cD |= (w | (w << 1)) << s
            cU |= ((w << 9) | (w << 10)) << s
            n += 1
        m = cand_v
        while m:
            w = m & -m
            m ^= w
            s = 81 * n
            cR |= (w | (w << 9)) << s
            cL |= ((w << 1) | (w << 10)) << s
            n += 1
        rep = _REP[n]
        Ua = (U * rep) & ~cU
        Da = (D * rep) & ~cD
        La = (L * rep) & ~cL
        Ra = (R * rep) & ~cR
        T = t * rep
        seen = front = gt * rep
        while front:
            if front & T:
                r = False
                break
            front = (((front & Ua) >> 9) | ((front & Da) << 9)
                     | ((front & La) >> 1) | ((front & Ra) << 1)) & ~seen
            seen |= front
    _DEAD[key] = r
    return r


def _eff(w, t, gt, U, D, L, R, hw, vw, dt):
    if w and USE_DEAD and 0 < dt <= DEAD_NEAR and _dead(t, gt, U, D, L, R, hw, vw, dt):
        return 0
    return w


def _evalb(dm, do, wm, wo, me, op, gm, go, U, D, L, R, hw, vw, extra):
    if USE_DEAD:
        if wm and 0 < do <= DEAD_NEAR and _dead(op, go, U, D, L, R, hw, vw, do):
            wm = 0
        if wo and 0 < dm <= DEAD_NEAR and _dead(me, gm, U, D, L, R, hw, vw, dm):
            wo = 0
    s = _static(dm, do, wm, wo) + extra
    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if _blocked_q(me, op, gm, U, D, L, R, hw, vw, dm):
                return _draw(gm)
        elif _blocked_q(op, me, go, U, D, L, R, hw, vw, do):
            return _draw(gm)
    return s



def _adjacent(p, q, U, D, L, R):
    return bool(((p & U) and (p >> 9) == q) or ((p & D) and (p << 9) == q)
                or ((p & L) and (p >> 1) == q) or ((p & R) and (p << 1) == q))


def _jgain(b, x, U, D, L, R, nxt2):
    if not nxt2 or not _adjacent(b, x, U, D, L, R):
        return 0
    return _jland(b, x, U, D, L, R, nxt2)


def _jland(b, x, U, D, L, R, nxt2):
    for y in _pawn_moves(b, x, U, D, L, R):
        if y & nxt2:
            return STEP
    return 0


def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db, sb):
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    ob2 = -1
    nbb = ((b << 1) | (b >> 1) | (b << 9) | (b >> 9)) if (USE_JUMP_Q and db >= 2) else 0
    if USE_DEAD:
        wa_e = _eff(wa, b, gb, U, D, L, R, hw, vw, db)
        if not wa_e:
            sb = 0
    else:
        wa_e = wa
    s_base = TEMPO - WALL_VALUE[wa_e] + sb - STEP * db
    blk = _BLOCK[0]
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
        wb_e = wb
        if wb and USE_DEAD and 0 < nd <= DEAD_NEAR and _dead(x, ga, U, D, L, R, hw, vw, nd):
            wb_e = 0
        sx = _shape(x, nd, lay_m, U, D, L, R) if wb_e else 0
        s = s_base + WALL_VALUE[wb_e] + STEP * nd - sx + _race_adj(db, nd, wb_e, wa_e)
        if (x & nbb) and _adjacent(b, x, U, D, L, R):
            if ob2 < 0:
                ob2 = _layers_at(gb, db, U, D, L, R)[db - 2]
            if ob2:
                s += _jland(b, x, U, D, L, R, ob2)
        if blk and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
            if s > 0:
                if _blocked_q(b, x, gb, U, D, L, R, hw, vw, db):
                    s = _draw(gb)
            elif _blocked_q(x, b, ga, U, D, L, R, hw, vw, nd):
                s = _draw(gb)
        v = -s
        out.append((v, x.bit_length() - 1, x, U, D, L, R, hw, vw, wa, nd, db, v))
    return None, out


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
               top, defensive, hist):
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v

    left_h = COL[0] | (hw << 2) | (vw << 1) | (vw >> 8) | (vw << 10)
    right_h = COL[7] | (hw >> 2) | (vw >> 1) | (vw >> 10) | (vw << 8)
    mid_h = (vw >> 9) | (vw << 9)
    top_v = ROW[0] | (vw << 18) | (hw << 9) | (hw << 8) | (hw << 10)
    bot_v = ROW[7] | (vw >> 18) | (hw >> 9) | (hw >> 10) | (hw >> 8)
    mid_v = (hw >> 1) | (hw << 1)

    urg_h = urg_v = 0
    if USE_GATE_DEF and wb > 0 and (hw | vw):
        th_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & avail_h
        th_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & avail_v
        raw_gh = th_h & left_h & right_h
        raw_gv = th_v & top_v & bot_v
        gh = gv = 0
        if raw_gh:
            m_vert = (mu >> 9) if (ga & 1) else md
            tmp = raw_gh
            while tmp:
                w = tmp & -tmp
                tmp ^= w
                r = _RC_R[w.bit_length() - 1]
                if not ((m_vert & ROW[r]) & ~(w | (w << 1))):
                    gh |= w
        if raw_gv:
            m_horz = mr | (ml >> 1)
            tmp = raw_gv
            while tmp:
                w = tmp & -tmp
                tmp ^= w
                c = _RC_C[w.bit_length() - 1]
                if not ((m_horz & COL[c]) & ~(w | (w << 9))):
                    gv |= w
        if gh | gv:
            urg_h = ((gh << 1) | (gh >> 1) | gv) & avail_h
            urg_v = ((gv << 9) | (gv >> 9) | gh) & avail_v
            cand_h |= urg_h
            cand_v |= urg_v

    if defensive:
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v

    if not (cand_h | cand_v):
        return []
    cands = []
    no = nm = 0
    oU = oD = oL = oR = 0
    mU = mD = mL = mR = 0
    mask = cand_h
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        io = im = -1
        if (od & e1) or (ou & e2):
            s = 81 * no
            oD |= e1 << s
            oU |= e2 << s
            io = no
            no += 1
        if (md & e1) or (mu & e2):
            s = 81 * nm
            mD |= e1 << s
            mU |= e2 << s
            im = nm
            nm += 1
        j = (1 if (w & left_h) else 0) + (1 if (w & right_h) else 0) + (1 if (w & mid_h) else 0)
        cands.append((1, w, e1, e2, io, im, j, bool(w & urg_h)))
    mask = cand_v
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        io = im = -1
        if (orr & e1) or (ol & e2):
            s = 81 * no
            oR |= e1 << s
            oL |= e2 << s
            io = no
            no += 1
        if (mr & e1) or (ml & e2):
            s = 81 * nm
            mR |= e1 << s
            mL |= e2 << s
            im = nm
            nm += 1
        j = (1 if (w & top_v) else 0) + (1 if (w & bot_v) else 0) + (1 if (w & mid_v) else 0)
        cands.append((0, w, e1, e2, io, im, j, bool(w & urg_v)))
    if no:
        rep = _REP[no]
        odist, onext, onext2 = _multi_bfs(gb, b, (U * rep) & ~oU, (D * rep) & ~oD,
                                          (L * rep) & ~oL, (R * rep) & ~oR, no)
    if nm:
        rep = _REP[nm]
        mdist, mnext, mnext2 = _multi_bfs(ga, a, (U * rep) & ~mU, (D * rep) & ~mD,
                                          (L * rep) & ~mL, (R * rep) & ~mR, nm)
    o_nx = lay_o[do - 1] if do >= 1 else 0
    o_nx2 = lay_o[do - 2] if do >= 2 else 0
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)  
    base = TEMPO + WALL_VALUE[wb] - WALL_VALUE[wa2]
    if USE_DEAD:
        exact = top is None
        dead_b = bool(wa2) and 0 < do <= DEAD_NEAR and _dead(b, gb, U, D, L, R, hw, vw, do)
        dead_a = bool(wb) and 0 < dm <= DEAD_NEAR and _dead(a, ga, U, D, L, R, hw, vw, dm)
        thr_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        thr_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        prot_h = (thr_h << 1) | (thr_h >> 1) | thr_v
        prot_v = (thr_v << 9) | (thr_v >> 9) | thr_h
    walls = []
    append = walls.append
    for is_h, w, e1, e2, io, im, j, is_urg in cands:
        if io >= 0:
            ndo = odist[io]
            if ndo < 0:
                continue
            onx = onext[io]
            onx2 = onext2[io]
        else:
            ndo = do
            onx = o_nx
            onx2 = o_nx2
        if im >= 0:
            ndm = mdist[im]
            if ndm < 0:
                continue
            mnx = mnext[im]
            mnx2 = mnext2[im]
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
        if is_h:
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
            code = H_BASE + w.bit_length() - 1
        else:
            U2 = U
            D2 = D
            R2 = R & ~e1
            L2 = L & ~e2
            hw2 = hw
            vw2 = vw | w
            code = V_BASE + w.bit_length() - 1
        if USE_DEAD:
            wa2_e = wa2
            if wa2 and 0 < ndo <= DEAD_NEAR:
                if io >= 0 or exact:
                    if _dead(b, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                        wa2_e = 0
                elif dead_b:
                    wa2_e = 0
            wb_e = wb
            if wb and 0 < ndm <= DEAD_NEAR:
                if im >= 0 or exact or (w & (prot_h if is_h else prot_v)):
                    if _dead(a, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                        wb_e = 0
                elif dead_a:
                    wb_e = 0
            s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                 + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
            s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb, wa2)
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        v = -s
        order = v + (4 if j >= 2 else j)
        if is_urg and ndm <= dm:
            order += URG_DEF_BONUS
        if USE_LAST_WALL_GUARD and wa == 1 and wb > 0 and ndm > ndo and not is_urg:
            order -= 20
        if hist is not None:
            h = hist[code]
            if h:
                order += (HIST_CAP * h) // (h + HIST_HALF)
        append((order, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, v))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _multi_bfs(goal, target, Ua, Da, La, Ra, n):
    dl = _DEADLINE[0]
    if dl and perf_counter() > dl:
        raise _TimeUp()
    rep = _REP[n]
    T = target * rep
    seen = front = goal * rep
    dist = [-1] * n
    nxt = [0] * n
    nxt2 = [0] * n
    left = n
    k = 0
    prev = 0
    while True:
        last2 = prev
        prev = front
        front = (((front & Ua) >> 9) | ((front & Da) << 9)
                 | ((front & La) >> 1) | ((front & Ra) << 1)) & ~seen
        if not front:
            break
        k += 1
        if (k & 7) == 0 and dl and perf_counter() > dl:
            raise _TimeUp()
        hits = front & T
        if hits:
            while hits:
                low = hits & -hits
                hits ^= low
                i = (low.bit_length() - 1) // 81
                dist[i] = k
                sh = 81 * i
                nxt[i] = (prev >> sh) & ALL
                nxt2[i] = (last2 >> sh) & ALL
                left -= 1
            if not left:
                break
        seen |= front
    return dist, nxt, nxt2


def _gen_walls_fast(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                    top, defensive):
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if defensive:
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    cands = []
    no = nm = 0
    oU = oD = oL = oR = 0
    mU = mD = mL = mR = 0
    mask = cand_h
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        io = im = -1
        if (od & e1) or (ou & e2):
            s = 81 * no
            oD |= e1 << s
            oU |= e2 << s
            io = no
            no += 1
        if (md & e1) or (mu & e2):
            s = 81 * nm
            mD |= e1 << s
            mU |= e2 << s
            im = nm
            nm += 1
        cands.append((1, w, e1, e2, io, im))
    mask = cand_v
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        io = im = -1
        if (orr & e1) or (ol & e2):
            s = 81 * no
            oR |= e1 << s
            oL |= e2 << s
            io = no
            no += 1
        if (mr & e1) or (ml & e2):
            s = 81 * nm
            mR |= e1 << s
            mL |= e2 << s
            im = nm
            nm += 1
        cands.append((0, w, e1, e2, io, im))
    if no:
        rep = _REP[no]
        odist, onext, onext2 = _multi_bfs(gb, b, (U * rep) & ~oU, (D * rep) & ~oD,
                                          (L * rep) & ~oL, (R * rep) & ~oR, no)
    if nm:
        rep = _REP[nm]
        mdist, mnext, mnext2 = _multi_bfs(ga, a, (U * rep) & ~mU, (D * rep) & ~mD,
                                          (L * rep) & ~mL, (R * rep) & ~mR, nm)
    o_nx = lay_o[do - 1] if do >= 1 else 0
    o_nx2 = lay_o[do - 2] if do >= 2 else 0
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)  
    base = TEMPO + WALL_VALUE[wb] - WALL_VALUE[wa2]
    if USE_DEAD:
        dead_b = bool(wa2) and 0 < do <= DEAD_NEAR and _dead(b, gb, U, D, L, R, hw, vw, do)
        dead_a = bool(wb) and 0 < dm <= DEAD_NEAR and _dead(a, ga, U, D, L, R, hw, vw, dm)
        thr_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        thr_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        prot_h = (thr_h << 1) | (thr_h >> 1) | thr_v
        prot_v = (thr_v << 9) | (thr_v >> 9) | thr_h
    walls = []
    append = walls.append
    for is_h, w, e1, e2, io, im in cands:
        if io >= 0:
            ndo = odist[io]
            if ndo < 0:
                continue
            onx = onext[io]
            onx2 = onext2[io]
        else:
            ndo = do
            onx = o_nx
            onx2 = o_nx2
        if im >= 0:
            ndm = mdist[im]
            if ndm < 0:
                continue
            mnx = mnext[im]
            mnx2 = mnext2[im]
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
        if is_h:
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
            code = H_BASE + w.bit_length() - 1
        else:
            U2 = U
            D2 = D
            R2 = R & ~e1
            L2 = L & ~e2
            hw2 = hw
            vw2 = vw | w
            code = V_BASE + w.bit_length() - 1
        if USE_DEAD:
            wa2_e = wa2
            if wa2 and 0 < ndo <= DEAD_NEAR:
                if io >= 0:
                    if _dead(b, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                        wa2_e = 0
                elif dead_b:
                    wa2_e = 0
            wb_e = wb
            if wb and 0 < ndm <= DEAD_NEAR:
                if im >= 0 or (w & (prot_h if is_h else prot_v)):
                    if _dead(a, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                        wb_e = 0
                elif dead_a:
                    wb_e = 0
            s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                 + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
            s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb, wa2)
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        v = -s
        append((v, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, v))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _best_wall_d1(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                  best, best_move):
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if not (cand_h | cand_v):
        return best, best_move
    cands = []
    no = nm = 0
    oU = oD = oL = oR = 0
    mU = mD = mL = mR = 0
    mask = cand_h
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        s = 81 * no
        oD |= e1 << s
        oU |= e2 << s
        io = no
        no += 1
        im = -1
        if (md & e1) or (mu & e2):
            s = 81 * nm
            mD |= e1 << s
            mU |= e2 << s
            im = nm
            nm += 1
        cands.append((1, w, e1, e2, io, im))
    mask = cand_v
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        s = 81 * no
        oR |= e1 << s
        oL |= e2 << s
        io = no
        no += 1
        im = -1
        if (mr & e1) or (ml & e2):
            s = 81 * nm
            mR |= e1 << s
            mL |= e2 << s
            im = nm
            nm += 1
        cands.append((0, w, e1, e2, io, im))
    rep = _REP[no]
    odist, onext, onext2 = _multi_bfs(gb, b, (U * rep) & ~oU, (D * rep) & ~oD,
                                      (L * rep) & ~oL, (R * rep) & ~oR, no)
    if nm:
        rep = _REP[nm]
        mdist, mnext, mnext2 = _multi_bfs(ga, a, (U * rep) & ~mU, (D * rep) & ~mD,
                                          (L * rep) & ~mL, (R * rep) & ~mR, nm)
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R) 
    wb_p = _eff(wb, a, ga, U, D, L, R, hw, vw, dm) if USE_DEAD else wb
    base = TEMPO + WALL_VALUE[wb_p] - WALL_VALUE[wa2]
    max_shape = (CORRIDOR_PEN if wa2 else 0) + (MULT_BONUS if wb else 0)
    gain_im = (WALL_VALUE[wb] + RACE_BONUS) if (USE_DEAD and wb) else 0
    blk = _BLOCK[0]
    for is_h, w, e1, e2, io, im in cands:
        ndo = odist[io]
        if ndo < 0:
            continue
        if im >= 0:
            ndm = mdist[im]
            if ndm < 0:
                continue
            mnx = mnext[im]
            mnx2 = mnext2[im]
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
        s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb_p, wa2)
        if not blk and -s + max_shape + (gain_im if im >= 0 else 0) < best:
            continue
        onx = onext[io]
        onx2 = onext2[io]
        if is_h:
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
        else:
            U2 = U
            D2 = D
            R2 = R & ~e1
            L2 = L & ~e2
            hw2 = hw
            vw2 = vw | w
        if USE_DEAD:
            wa2_e = _eff(wa2, b, gb, U2, D2, L2, R2, hw2, vw2, ndo)
            wb_e = _eff(wb, a, ga, U2, D2, L2, R2, hw2, vw2, ndm) if im >= 0 else wb_p
            if wa2_e != wa2 or wb_e != wb_p:
                s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                     + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        if blk and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
            if s > 0:
                if _blocked_q(b, a, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                    s = _draw(gb)
            elif _blocked_q(a, b, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                s = _draw(gb)
        v = -s
        if v > best or (v == best and H_BASE <= best_move < PASS):
            best = v
            best_move = (H_BASE if is_h else V_BASE) + w.bit_length() - 1
    return best, best_move


def _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
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
    return (0, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo, 0)


class _Searcher:
    __slots__ = ("deadline", "turn", "max_turns", "nodes", "killers", "hist",
                 "counter", "path")

    def __init__(self, deadline, turn, max_turns, hist):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        for row in _KILLERS:
            row[0] = None
            row[1] = None
        for i in range(len(_COUNTER)):
            _COUNTER[i] = None
        self.killers = _KILLERS
        self.hist = hist
        self.counter = _COUNTER
        self.path = set()

    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply, prev):
        self.nodes += 1
        if (self.nodes & 7) == 0 and perf_counter() > self.deadline:
            raise _TimeUp()
        if a & ga:
            return WIN - ply
        if b & gb:
            return -WIN + ply
        remaining = self.max_turns - self.turn - ply
        if remaining <= 0:
            return _draw(ga)

        rkey = (a, b, wa, wb, hw, vw, ga)
        if USE_REP_FIX and ply > 0 and (rkey in self.path or rkey in _HIST):
            return _draw(ga)

        egp = _EGP[0]
        if egp is not None and (wa == 0 or wb == 0) and hw == egp[0] and vw == egp[1]:
            mover = egp[3] if ga == egp[4] else 1 - egp[3]
            ia = a.bit_length() - 1
            ib = b.bit_length() - 1
            st = (mover * 6561 + ia * 81 + ib) if mover == 0 else (6561 + ib * 81 + ia)
            out = egp[2].result[st]
            dd = egp[2].distance[st]
            if dd <= remaining:
                if out == 1 and wb == 0:
                    return WIN - ply - dd
                if out == -1 and wa == 0:
                    return -WIN + ply + dd
            if wa == 0 and wb == 0:
                return _draw(ga)
        if depth <= 0:
            if da <= 2:
                for x in _pawn_moves(a, b, U, D, L, R):
                    if x & ga:
                        return WIN - ply - 1
            if remaining == 1:
                return _draw(ga)
            extra = 0
            if USE_LEAF_SHAPE:
                wa_e = _eff(wa, b, gb, U, D, L, R, hw, vw, db) if USE_DEAD else wa
                wb_e = _eff(wb, a, ga, U, D, L, R, hw, vw, da) if USE_DEAD else wb
                lay_a = _layers_at(ga, da, U, D, L, R)
                if wb_e:
                    extra += _shape(a, da, lay_a, U, D, L, R)
                if wa_e:
                    extra -= _shape(b, db, _layers_at(gb, db, U, D, L, R), U, D, L, R)
                if USE_JUMP_Q and da >= 2 and lay_a[da - 2] and _adjacent(a, b, U, D, L, R):
                    extra += _jland(a, b, U, D, L, R, lay_a[da - 2])
            return _evalb(da, db, wa, wb, a, b, ga, gb, U, D, L, R, hw, vw, extra)

        key = (a, b, wa, wb, hw, vw, ga, remaining if remaining < 64 else 64)
        entry = _TT.get(key)
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

        dm = da
        do = db
        lay_m = _layers_at(ga, da + 2, U, D, L, R)
        if wa:
            lay_o = _layers_at(gb, db, U, D, L, R)
            sb = _shape(b, db, lay_o, U, D, L, R)
        else:
            lay_o = None
            sb = 0
        sa = _shape(a, da, lay_m, U, D, L, R) if wb else 0
        win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                lay_m, dm, db, sb)
        if win is not None:
            return WIN - ply - 1
        jg = 0
        if USE_JUMP_Q and db >= 2 and _adjacent(b, a, U, D, L, R):
            ob2 = _layers_at(gb, db, U, D, L, R)[db - 2]
            if ob2:
                jg = _jland(b, a, U, D, L, R, ob2)
        pass_sc = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw, sb - sa + jg)
        np1 = ply + 1
        ia = a.bit_length() - 1
        ib = b.bit_length() - 1
        dr = _RC_R[ia] - _RC_R[ib]
        if dr < 0:
            dr = -dr
        dc = _RC_C[ia] - _RC_C[ib]
        if dc < 0:
            dc = -dc
        if USE_REP_FIX:
            adj_c = bool(((a & U) and (a >> 9) == b) or ((a & D) and (a << 9) == b)
                         or ((a & L) and (a >> 1) == b) or ((a & R) and (a << 1) == b))
            contact = CONTACT_PASS and (adj_c or (_BLOCK[0] and dr + dc <= 2))
        else:
            contact = CONTACT_PASS and (dr + dc <= 2)

        if (NMP_R and da <= db + 1 and wb > 0 and (dr > 1 or dc > 1) and prev != PASS
                and depth >= 5 and da > 2 and db > 2 and pass_sc >= beta
                and -MATE_ZONE < beta < MATE_ZONE):
            if USE_REP_FIX:
                self.path.add(rkey)
                try:
                    sc = -self.search(b, a, wb, wa, gb, ga, U, D, L, R, hw, vw, db, da,
                                      depth - 1 - NMP_R, -beta, 1 - beta, np1, PASS)
                finally:
                    self.path.discard(rkey)
            else:
                sc = -self.search(b, a, wb, wa, gb, ga, U, D, L, R, hw, vw, db, da,
                                  depth - 1 - NMP_R, -beta, 1 - beta, np1, PASS)
            if sc >= beta:
                return beta if sc > MATE_ZONE else sc

        if depth == 1:
            if remaining == 1:
                best = _draw(ga)
                _TT[key] = (1, best, 0, PASS)
                return best
            threat = db <= 2
            leaf = self._leaf
            best = leaf(b, a, gb, U, D, L, R, db, pass_sc, np1) if threat else pass_sc
            best_move = PASS
            for k in pawns:
                v = leaf(b, k[2], gb, U, D, L, R, db, k[12], np1) if threat else k[12]
                if v > best:
                    best = v
                    best_move = k[1]
            if best < beta and wa > 0:
                if threat:
                    walls = _gen_walls_fast(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                            lay_m, dm, lay_o, do, None, True)
                    for k in walls:
                        v = leaf(b, a, gb, k[3], k[4], k[5], k[6], k[11], k[12], np1)
                        if v > best:
                            best = v
                            best_move = k[1]
                            if best >= beta:
                                break
                else:
                    best, best_move = _best_wall_d1(a, b, wa, wb, ga, gb, U, D, L, R,
                                                    hw, vw, lay_m, dm, lay_o, do,
                                                    best, best_move)
            flag = 1 if best >= beta else (2 if best <= alpha else 0)
            _TT[key] = (1, _to_tt(best, ply), flag, best_move)
            return best

        pawns.sort(reverse=True)
        if CONTACT_PASS and contact:
            pawns.append((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db, pass_sc))
        orig_alpha = alpha
        best = -WIN * 2
        best_move = None
        nd = depth - 1
        rd = depth - 2
        lmr_ok = depth >= 3 and da > 2 and db > 2
        rd2 = depth - 3 if (LMR2_IDX and depth >= 5) else rd
        tried = _TRIED[ply]
        stamp = _STAMP[0] + 1
        _STAMP[0] = stamp
        first = True
        killers = self.killers[ply]
        hist = self.hist
        counter = self.counter
        search = self.search
        stage = 0
        if USE_REP_FIX:
            self.path.add(rkey)
        try:
            while stage < 5:
                if stage == 0:
                    lst = ()
                    if tt_move is not None:
                        if tt_move == PASS:
                            lst = ((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db, pass_sc),)
                        elif tt_move < H_BASE:
                            lst = [k for k in pawns if k[1] == tt_move]
                        else:
                            c = _wall_child(tt_move, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                lst = (c,)
                elif stage == 1:
                    if wa > 0:
                        lst = []
                        for km in killers:
                            if km is not None and tried[km] != stamp:
                                c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                                if c is not None:
                                    lst.append(c)
                        if COUNTER and prev is not None:
                            km = counter[prev]
                            if km is not None and tried[km] != stamp and km not in killers:
                                c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                                if c is not None:
                                    lst.append(c)
                    else:
                        lst = ()
                elif stage == 2:
                    lst = pawns
                elif stage == 3:
                    if wa > 0:
                        lst = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                         lay_m, dm, lay_o, do, _top_walls(depth),
                                         dm < do and wb > 0, hist)
                    else:
                        lst = ()
                else:
                    if CONTACT_PASS and contact:
                        break
                    lst = ((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db, pass_sc),)
                widx = 0
                cut = False
                for k in lst:
                    code = k[1]
                    if tried[code] == stamp:
                        continue
                    tried[code] = stamp
                    if first:
                        sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                     k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1, code)
                        first = False
                    else:
                        if stage == 3 and lmr_ok and widx >= LMR_FULL_WALLS:
                            r = rd2 if (LMR2_IDX and widx >= LMR2_IDX and k[11] <= do + 1) else rd
                            sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                         k[7], k[8], k[11], k[10], r, -alpha - 1, -alpha,
                                         np1, code)
                            if sc > alpha:
                                sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                             k[6], k[7], k[8], k[11], k[10], nd,
                                             -alpha - 1, -alpha, np1, code)
                        else:
                            sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                         k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha,
                                         np1, code)
                        if alpha < sc < beta:
                            sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                         k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1, code)
                    if stage == 3:
                        widx += 1
                    if sc > best:
                        best = sc
                        best_move = code
                        if sc > alpha:
                            alpha = sc
                            if alpha >= beta:
                                if H_BASE <= code < PASS:
                                    if killers[0] != code:
                                        killers[1] = killers[0]
                                        killers[0] = code
                                    hist[code] += depth * depth
                                    if prev is not None:
                                        counter[prev] = code
                                cut = True
                                break
                if cut:
                    break
                stage += 1
        finally:
            if USE_REP_FIX:
                self.path.discard(rkey)
        if best <= orig_alpha:
            flag = 2
        elif best >= beta:
            flag = 1
        else:
            flag = 0
        _TT[key] = (depth, _to_tt(best, ply), flag, best_move)
        return best

    def _leaf(self, b, a2, gb, U, D, L, R, db, value, ply):
        if self.turn + ply >= self.max_turns:
            return -_draw(gb)
        if db <= 2:
            for x in _pawn_moves(b, a2, U, D, L, R):
                if x & gb:
                    return -WIN + ply + 1
        return value



def _root_bias(child, a, b, da, U, D, L, R, db=99, wb=0):
    if USE_REP_FIX:
        adj_c = bool(((a & U) and (a >> 9) == b) or ((a & D) and (a << 9) == b)
                     or ((a & L) and (a >> 1) == b) or ((a & R) and (a << 1) == b))
        code = child[1]
        if code == PASS:
            if _BLOCK[0] and adj_c:
                return 0
            return -ROOT_PASS_PENALTY
        if adj_c and _BLOCK[0]:
            return 0
    else:
        ia, ib = a.bit_length() - 1, b.bit_length() - 1
        if abs(_RC_R[ia] - _RC_R[ib]) + abs(_RC_C[ia] - _RC_C[ib]) <= 2:
            return 0
        code = child[1]
        if code == PASS:
            return -ROOT_PASS_PENALTY
    bias = 0
    if code < H_BASE:
        nd = child[10]
        urg = _URG_CUR[0]
        for _, pawn, horizontal, vertical in _ACTIVE_RECENT:
            if child[2] == pawn and child[7] == horizontal and child[8] == vertical:
                if nd >= da:
                    bias -= int(SHUFFLE_PENALTY * (1.0 + urg))
                else:
                    bias -= ROOT_REPEAT_PENALTY
                break
        if urg > 0.0 and nd < da:
            bias += int(URG_BONUS * urg * (da - nd))
    elif USE_LAST_WALL_GUARD and code < PASS and child[9] == 0 and wb > 0 and da > db:
        if child[10] >= child[11]:
            bias -= 30
    return bias


def _pv_line(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, code, rem, n=10):
    """Diagnostics only: principal variation read back from the TT (move codes)."""
    line = [code]
    try:
        for _ in range(n):
            if code == PASS:
                na, nwa, U2, D2, L2, R2, hw2, vw2 = a, wa, U, D, L, R, hw, vw
            elif code < H_BASE:
                na, nwa, U2, D2, L2, R2, hw2, vw2 = 1 << code, wa, U, D, L, R, hw, vw
            else:
                c = _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                if c is None:
                    break
                na, nwa, U2, D2, L2, R2, hw2, vw2 = a, wa - 1, c[3], c[4], c[5], c[6], c[7], c[8]
            rem -= 1
            if (na & ga) or rem <= 0:
                break
            a, b, wa, wb, ga, gb = b, na, wb, nwa, gb, ga
            U, D, L, R, hw, vw = U2, D2, L2, R2, hw2, vw2
            e = _TT.get((a, b, wa, wb, hw, vw, ga, rem if rem < 64 else 64))
            if e is None or e[3] is None:
                break
            code = e[3]
            line.append(code)
    except Exception:
        pass
    return line


def _cold():
    return COLD_START_RESERVE if _STATE["moves"] <= 1 else 0.0


def _adj(raw, bias):
    return raw if raw > MATE_ZONE or raw < -MATE_ZONE else raw + bias


def _raw(bound, bias):
    return bound if bound > MATE_ZONE or bound < -MATE_ZONE else bound - bias


def _root_pass(s, kids, biases, b, wb, gb, ga, depth, alpha, beta):
    lo = alpha
    cur_best = None
    cur_sc = -WIN * 2
    scores = {}
    search = s.search
    nd = depth - 1
    try:
        first = True
        for k in kids:
            bias = biases[k[1]]
            if first:
                sc = _adj(-search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                  k[7], k[8], k[11], k[10], nd,
                                  -_raw(beta, bias), -_raw(alpha, bias), 1, k[1]), bias)
                first = False
            else:
                sc = _adj(-search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                  k[7], k[8], k[11], k[10], nd,
                                  -_raw(alpha + 1, bias), -_raw(alpha, bias), 1, k[1]), bias)
                if alpha < sc < beta:
                    prev = (cur_best, cur_sc)
                    cur_best, cur_sc = k[1], sc
                    sc = _adj(-search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                      k[6], k[7], k[8], k[11], k[10], nd,
                                      -_raw(beta, bias), -_raw(alpha, bias), 1, k[1]), bias)
                    cur_best, cur_sc = prev
            scores[k[1]] = sc
            if sc > cur_sc:
                cur_sc = sc
                cur_best = k[1]
                if sc > alpha:
                    alpha = sc
                    if sc >= beta:
                        break
    except _TimeUp:
        return (cur_best if cur_sc > lo else None), cur_sc, scores, False
    return cur_best, cur_sc, scores, True


def _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0):
    lay_m, da = _layers(ga, a, U, D, L, R, 2)
    lay_o, db = _layers(gb, b, U, D, L, R, 0)
    sa = _shape(a, da, lay_m, U, D, L, R) if wb else 0
    sb = _shape(b, db, lay_o, U, D, L, R) if wa else 0
    win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, da, db, sb)
    if win is not None:
        _DIAG.clear()
        _DIAG.update(reason="immediate_win", best=win)
        return win, None
    if wa > 0:
        kids.extend(_gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                               lay_m, da, lay_o, db, None, True, None))
    jg = 0
    if USE_JUMP_Q and db >= 2 and _adjacent(b, a, U, D, L, R):
        jg = _jgain(b, a, U, D, L, R, lay_o[db - 2])
    pv = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw, sb - sa + jg) - 1
    kids.append((pv, PASS, a, U, D, L, R, hw, vw, wa, da, db, pv))
    biases = {k[1]: _root_bias(k, a, b, da, U, D, L, R, db, wb) for k in kids}

    def order(k, scores=None):
        child = (b, k[2], wb, k[9], k[7], k[8], gb)
        v = _adj(k[0], biases[k[1]]) if scores is None else scores.get(k[1], -WIN * 3)
        return (-v, child in _HIST, k[1] == PASS)

    kids.sort(key=order)
    best_move = kids[0][1]
    prev = _adj(kids[0][0], biases[best_move])
    best_sc = prev
    _STATE["depth"] = 1
    _STATE["score"] = prev
    _DIAG.clear()
    _DIAG.update(depth=1, nodes=0, score=prev, best_score=prev, best=best_move,
                 kids=len(kids), reason="single" if len(kids) == 1 else "search")
    if len(kids) == 1:
        return best_move, kids[0]

    deadline = t0 + TIME_LIMIT - _STATE["margin"] - _cold()
    _DEADLINE[0] = deadline
    s = _Searcher(deadline, turn, max_turns, _HISTORY)
    if USE_REP_FIX:
        s.path.add((a, b, wa, wb, hw, vw, ga))
    full = WIN * 2
    depth = 1
    asp_fail = 0
    timed_out = False
    while depth < 60:
        depth += 1
        it_start = perf_counter()
        if depth >= 3 and -MATE_ZONE < prev < MATE_ZONE:
            delta = ASP_DELTA
            alpha, beta = prev - delta, prev + delta
        else:
            delta = full
            alpha, beta = -full, full
        timed_out = False
        while True:
            move, sc, scores, complete = _root_pass(s, kids, biases, b, wb, gb, ga,
                                                    depth, alpha, beta)
            if not complete:
                if move is not None:
                    if move != best_move:
                        _DIAG["switch_depth"] = depth
                    best_move = move
                    best_sc = sc
                timed_out = True
                break
            if sc <= alpha and alpha > -full:
                asp_fail += 1
                delta = int(delta * ASP_GROW)
                alpha = -full if delta > 400 else max(sc - delta, -full)
                continue
            if sc >= beta and beta < full:
                asp_fail += 1
                best_move = move
                best_sc = sc
                for i, k in enumerate(kids):
                    if k[1] == move:
                        kids.insert(0, kids.pop(i))
                        break
                delta = int(delta * ASP_GROW)
                beta = full if delta > 400 else min(sc + delta, full)
                continue
            best_move = move
            prev = sc
            best_sc = sc
            break
        if timed_out:
            break
        _STATE["depth"] = depth
        _STATE["nodes"] = s.nodes
        _STATE["score"] = prev
        kids.sort(key=lambda k: order(k, scores))
        raw_best = _raw(prev, biases[best_move])
        if raw_best > MATE_ZONE or raw_best < -MATE_ZONE:
            if WIN - abs(raw_best) <= depth:
                break
        now = perf_counter()
        if now + (now - it_start) * ITER_FACTOR > deadline:
            break
    _DEADLINE[0] = 0.0
    _STATE["nodes"] = s.nodes
    _DIAG.update(depth=_STATE["depth"], nodes=s.nodes, score=_STATE["score"],
                 best_score=best_sc, kids=len(kids), reason="search",
                 last_iteration="cut_by_time" if timed_out else "complete",
                 aspiration_fails=asp_fail)
    if DIAG_PV:
        _DIAG["pv"] = _pv_line(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, best_move,
                               max_turns - turn)
    guard_sc = best_sc if FIX_GUARD_SCORE else prev
    if (USE_LAST_WALL_GUARD and wa == 1 and wb > 0 and da > db
            and H_BASE <= best_move < PASS and guard_sc < -MATE_ZONE):
        for k in kids:
            if k[1] < H_BASE and k[10] < da:
                best_move = k[1]
                _DIAG["reason"] = "last_wall_guard"
                break
    _DIAG["best"] = best_move
    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None



_EG_SPAN = 81 * 81
_EG_CACHE = None
_EG_VISITS = {}
_EG_CONTEXT = None
_EG_LAST_TURN = -1


class _EGTimeout(Exception):
    pass


def _eg_dist_map(goal, U, D, L, R):
    dist = [999] * 81
    seen = front = goal
    k = 0
    while front:
        f = front
        while f:
            low = f & -f
            dist[low.bit_length() - 1] = k
            f ^= low
        k += 1
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        seen |= front
    return dist


def _eg_tables(U, D, L, R):
    graph = []
    for i in range(81):
        bit = 1 << i
        nb = []
        if bit & U:
            nb.append(i - 9)
        if bit & L:
            nb.append(i - 1)
        if bit & R:
            nb.append(i + 1)
        if bit & D:
            nb.append(i + 9)
        graph.append(tuple(nb))
    contact = {}
    for i in range(81):
        for j in graph[i]:
            contact[i * 81 + j] = tuple(sorted(
                x.bit_length() - 1 for x in _pawn_moves(1 << i, 1 << j, U, D, L, R)))
    return graph, contact


class _PawnEndgame:
    __slots__ = ("key", "graph", "contact", "maps", "result", "distance")

    def __init__(self, key, U, D, L, R, goals, deadline):
        self.key = key
        graph, contact = _eg_tables(U, D, L, R)
        self.graph, self.contact = graph, contact
        r0 = _eg_dist_map(ROW[goals[0]], U, D, L, R)
        r1 = _eg_dist_map(ROW[goals[1]], U, D, L, R)
        self.maps = (r0, r1)
        span = _EG_SPAN
        count = 2 * span
        result = [0] * count
        distance = [0] * count
        remaining = [0] * count
        longest = [0] * count
        previous = [[] for _ in range(count)]
        pending = deque()
        push = pending.append
        cget = contact.get
        g0, g1 = goals
        for p0 in range(81):
            if (p0 & 7) == 0 and perf_counter() >= deadline:
                raise _EGTimeout
            if r0[p0] >= 999:
                continue
            won0 = p0 // 9 == g0
            base0 = p0 * 81
            for p1 in range(81):
                if p1 == p0 or r1[p1] >= 999:
                    continue
                pair = base0 + p1
                if won0 or p1 // 9 == g1:
                    w0 = 1 if won0 else -1
                    result[pair] = w0
                    result[span + pair] = -w0
                    push(pair)
                    push(span + pair)
                    continue
                mv = cget(pair)
                if mv is None:
                    mv = graph[p0]
                remaining[pair] = len(mv) + 1
                for d in mv:
                    previous[span + d * 81 + p1].append(pair)
                previous[span + pair].append(pair)
                st = span + pair
                mv = cget(p1 * 81 + p0)
                if mv is None:
                    mv = graph[p1]
                remaining[st] = len(mv) + 1
                for d in mv:
                    previous[base0 + d].append(st)
                previous[pair].append(st)
        processed = 0
        popleft = pending.popleft
        while pending:
            state = popleft()
            processed += 1
            if (processed & 1023) == 0 and perf_counter() >= deadline:
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
        if perf_counter() >= deadline:
            raise _EGTimeout
        self.result, self.distance = result, distance

    def moves(self, own, enemy):
        mv = self.contact.get(own * 81 + enemy)
        return self.graph[own] if mv is None else mv

    def probe(self, p0, p1, turn):
        state = turn * _EG_SPAN + p0 * 81 + p1
        return self.result[state], self.distance[state]

    def action(self, p0, p1, turn, turns_left, legal, visits):
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = None, None
        for dest in self.moves(own, enemy) + (own,):
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
                replies = self.moves(enemy, dest) + (enemy,)
                chances = 0
                for reply in replies:
                    ap0, ap1 = (dest, reply) if turn == 0 else (reply, dest)
                    rr, rd = self.probe(ap0, ap1, turn)
                    chances += (rr == 1 and rd <= turns_left - 2)
                pressure = chances / len(replies)
            key = (outcome, pace, progress, pressure,
                   -visits.get((dest, enemy), 0), dest != own)
            if best_key is None or key > best_key:
                best, best_key = dest, key
        return best


def solve_action(observation, deadline, U, D, L, R, hw, vw):
    global _EG_CACHE, _EG_CONTEXT, _EG_LAST_TURN, _EG_VISITS
    if observation["opponent"]["walls_remaining"] != 0 or perf_counter() >= deadline:
        return None
    me = observation["player_id"]
    goal = observation["you"]["goal_row"]
    for move in observation["legal_moves"]:
        if move[0] == goal:
            return {"type": "move", "to": [int(move[0]), int(move[1])]}
    turns_left = observation["max_turns"] - observation["turn"]
    if turns_left <= 0:
        return None
    positions, goals = [0, 0], [0, 0]
    for player, info in ((me, observation["you"]), (1 - me, observation["opponent"])):
        positions[player] = info["position"][0] * 9 + info["position"][1]
        goals[player] = info["goal_row"]
    goals = tuple(goals)
    key = (hw, vw, goals)
    try:
        if _EG_CACHE is None or _EG_CACHE.key != key:
            _EG_CACHE = None
            _EG_CACHE = _PawnEndgame(key, U, D, L, R, goals, deadline)
    except _EGTimeout:
        return None
    p0, p1 = positions
    outcome, dist = _EG_CACHE.probe(p0, p1, me)
    if (observation["you"]["walls_remaining"] != 0
            and not (outcome == 1 and dist <= turns_left)):
        return None
    context = (key, me)
    if context != _EG_CONTEXT or observation["turn"] <= _EG_LAST_TURN:
        _EG_VISITS = {}
    _EG_CONTEXT, _EG_LAST_TURN = context, observation["turn"]
    own, enemy = positions[me], positions[1 - me]
    pair = (own, enemy)
    _EG_VISITS[pair] = min(32, _EG_VISITS.get(pair, 0) + 1)
    if len(_EG_VISITS) > 256:
        _EG_VISITS = {pair: _EG_VISITS[pair]}
    legal = {r * 9 + c for r, c in observation["legal_moves"]}
    dest = _EG_CACHE.action(p0, p1, me, turns_left, legal, _EG_VISITS)
    if dest is None:
        return None
    if dest == own:
        return {"type": "pass"}
    return {"type": "move", "to": [dest // 9, dest % 9]}



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
    global _EG_CACHE
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
    max_turns = observation.get("max_turns", 500)
    goals = (ga, gb)
    if turn <= 0 or turn <= _STATE["last_turn"] or _STATE["goals"] != goals:
        _TT.clear()
        _HIST.clear()
        _ACTIVE_RECENT.clear()
        for i in range(len(_HISTORY)):
            _HISTORY[i] = 0
        _STATE["margin"] = 0.0
    elif len(_TT) > TT_LIMIT:
        _TT.clear()
    else:
        for i in range(len(_HISTORY)):
            _HISTORY[i] >>= 2
    _STATE["last_turn"] = turn
    _STATE["goals"] = goals

    frac = turn / float(max_turns if max_turns > 0 else 500)
    urg = max(0.0, (frac - URG_START) / (1.0 - URG_START)) if frac > URG_START else 0.0
    _URG_CUR[0] = urg
    _CONTEMPT_CUR[0] = CONTEMPT + int(CONTEMPT_URG * urg)
    if FIX_CONTEMPT_TT and _CONTEMPT_CUR[0] != _STATE.get("contempt"):
        _TT.clear()
    _STATE["contempt"] = _CONTEMPT_CUR[0]

    check_blocks = (hw.bit_count() + vw.bit_count() if hasattr(int, "bit_count")
                    else (bin(hw).count("1") + bin(vw).count("1"))) >= BLOCK_MIN_WALLS
    if check_blocks != _BLOCK[0]:
        _TT.clear()
    _BLOCK[0] = check_blocks
    _BCACHE.clear()
    _BQ.clear()
    _ROOT_GOAL[0] = ga
    _HIST.add((a, b, wa, wb, hw, vw, ga))
    _ACTIVE_RECENT[:] = [e for e in _ACTIVE_RECENT
                         if e[2:] == (hw, vw) and 0 < turn - e[0] <= 2 * ROOT_RECENT_WINDOW]
    _ACTIVE_RECENT.append((turn, a, hw, vw))
    del _ACTIVE_RECENT[:-ROOT_RECENT_WINDOW]

    _EGP[0] = None
    rem_budget = max(0.02, TIME_LIMIT - _STATE["margin"] - _cold())
    if wb == 0 and (wa == 0 or _dist(a, ga, U, D, L, R) <= _dist(b, gb, U, D, L, R) + 1):
        exact_deadline = min(t0 + rem_budget * 0.88, perf_counter() + 0.24)
        action = solve_action(observation, exact_deadline, U, D, L, R, hw, vw)
        if action is not None and _is_legal(action, observation):
            _STATE["mode"] = "endgame"
            _DIAG.clear()
            _DIAG.update(reason="endgame_table", turn=turn)
            return action
    if EG_PROBE and (wa == 0 or wb == 0):
        me = observation["player_id"]
        goals_id = [0, 0]
        goals_id[me] = you["goal_row"]
        goals_id[1 - me] = opp["goal_row"]
        eg_key = (hw, vw, tuple(goals_id))
        if (_EG_CACHE is None or _EG_CACHE.key != eg_key) and wb > 0 and rem_budget >= 0.35:
            try:
                _EG_CACHE = None
                _EG_CACHE = _PawnEndgame(eg_key, U, D, L, R, tuple(goals_id),
                                         min(t0 + rem_budget * 0.65,
                                             perf_counter() + 0.15))
            except _EGTimeout:
                _EG_CACHE = None
        if _EG_CACHE is not None and _EG_CACHE.key == eg_key:
            _EGP[0] = (hw, vw, _EG_CACHE, me, ga)
    egp_ctx = None if _EGP[0] is None else (_EGP[0][0], _EGP[0][1], _EGP[0][3], _EGP[0][4])
    if FIX_CONTEMPT_TT and egp_ctx != _STATE.get("egp_ctx"):
        _TT.clear()
    _STATE["egp_ctx"] = egp_ctx
    _STATE["mode"] = "search"

    code, kid = _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn,
                             max_turns, t0)
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
    if not isinstance(action, dict):
        return False
    kind = action.get("type")
    if kind == "pass":
        return True
    if kind == "move":
        to = action.get("to")
        if not isinstance(to, (list, tuple)) or len(to) != 2:
            return False
        return list(to) in [list(m) for m in observation["legal_moves"]]
    if kind == "wall":
        for w in observation["legal_walls"]:
            if (w["orientation"] == action.get("orientation")
                    and w["row"] == action.get("row")
                    and w["col"] == action.get("col")):
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
        if STRICT_MODE or observation.get("_strict"):
            raise
        action = None
    if action is None:
        _STATE["mode"] = "fallback"
        _DIAG["reason"] = "fallback"
        action = _fallback(observation)
    elapsed = perf_counter() - t0
    _DIAG["elapsed"] = elapsed
    late = elapsed - (TIME_LIMIT - _STATE["margin"])
    if late > 0.02:
        _STATE["margin"] = min(TIME_LIMIT * 0.45, _STATE["margin"] + 2 * late)
    return action