"""
Wrongway Arena - Apex Grandmaster Engine (v5)
MSSV: xxxxxxxx
Thuật toán:
  1. 81-bit Bitboard Parallel BFS & Topology-Indexed Reverse Cache (_PATH_CACHE)
  2. Principal Variation Search (PVS) + Fail-Safe Aspiration Windows + Selective LMR
  3. Dynamic Wall Economy & Wall Chaining Bonus (Khắc chế bế tắc & bẫy tường dài)
  4. Bitboard Retrograde Pawn Endgame Tablebase (_PawnEndgame)
  5. Jump-Aware Face-Off Tactician (Không bao giờ loop, luôn chủ động chiếm thế thượng phong)
"""

import gc
from collections import deque as _eg_deque
from time import perf_counter

gc.disable()

# ---------------------------------------------------------------------------
# Hằng số Bitboard & Bảng tra cứu
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

_BORDER_H = sum(1 << (9 * r + c) for r in range(8) for c in (0, 7))
_BORDER_V = sum(1 << (9 * r + c) for r in (0, 7) for c in range(8))

H_BASE = 100
V_BASE = 200
PASS = 999

# ---------------------------------------------------------------------------
# Lượng giá & Tham số tìm kiếm
# ---------------------------------------------------------------------------
STEP = 10
TEMPO = 5
RACE_BONUS = 60

# Bảng giá trị biên phi tuyến: Tường đầu chỉ tốn 4-5 điểm (dễ tung đòn), tường cuối cực quý (14-24 điểm)
_WALL_MARGINAL = [24, 18, 14, 10, 8, 6, 5, 5, 4, 4]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]

MULTI_PATH_BONUS = 2
BOTTLENECK_PENALTY = 1

TIME_LIMIT = 0.84
TOP_WALLS = 10
ITER_FACTOR = 0.30
ASP_DELTA = 35
NMP_R = 2
NMP_MARGIN = 18
WIN = 100000
MATE_ZONE = WIN - 1000

_TT = {}
_HISTORY = [0] * 300
_CM = [None] * 1000
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0, "goals": None}
_HIST = set()

ROOT_PASS_PENALTY = 120
ROOT_REPEAT_PENALTY = 40
ROOT_RECENT_WINDOW = 8
_ACTIVE_RECENT = []
_ROOT_GOAL = [0]
CONTEMPT = 25

_BLOCK = [False]
_BCACHE = {}
_BQ = {}
BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 15

_PATH_CACHE = {}
_PATH_HITS = 0
_PATH_MISSES = 0

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
    return -CONTEMPT if goal == _ROOT_GOAL[0] else CONTEMPT


# ---------------------------------------------------------------------------
# Topology-Indexed Reverse BFS Cache
# ---------------------------------------------------------------------------
def _cached_layers(goal, target, U, D, L, R, extra):
    global _PATH_HITS, _PATH_MISSES
    key = (goal, U, D, L, R)
    entry = _PATH_CACHE.get(key)
    if entry is None:
        _PATH_MISSES += 1
        if len(_PATH_CACHE) >= 16000:
            _PATH_CACHE.clear()
        entry = [[goal], goal, {}]
        _PATH_CACHE[key] = entry
    else:
        _PATH_HITS += 1

    layers, seen, distances = entry
    hit = distances.get(target)
    if hit is None:
        hit = -1
        i = 0
        for front in layers:
            if front & target:
                hit = i
                break
            i += 1

    if hit < 0 or len(layers) <= hit + extra:
        front = layers[-1]
        while front and (hit < 0 or len(layers) <= hit + extra):
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            layers.append(front)
            seen |= front
            if hit < 0 and (front & target):
                hit = len(layers) - 1
        entry[1] = seen

    distances[target] = hit
    return layers, hit


def _dist(start, goal, U, D, L, R):
    distance = _cached_layers(goal, start, U, D, L, R, 0)[1]
    return distance if distance >= 0 else INF


def _back_layers(goal, target, U, D, L, R, extra):
    return _cached_layers(goal, target, U, D, L, R, extra)


def _sp_edges(pos, layers, d, U, D, L, R):
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


# ---------------------------------------------------------------------------
# Lượng giá tĩnh & Pháo đài
# ---------------------------------------------------------------------------
def _static(dm, do, wm, wo):
    s = STEP * (do - dm) + TEMPO + WALL_VALUE[wm] - WALL_VALUE[wo]
    if wo == 0 and dm <= do:
        s += RACE_BONUS
    elif wm == 0 and dm > do:
        s -= RACE_BONUS
    return s


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
        dest = (o << 1) if o & R else (((o >> 9) if o & U else 0) | ((o >> 9) if o & D else 0))
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


def _evalb(dm, do, wm, wo, me, op, gm, go, U, D, L, R, hw, vw):
    s = _static(dm, do, wm, wo)
    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if _blocked_q(me, op, gm, U, D, L, R, hw, vw, dm):
                return _draw(gm)
        elif _blocked_q(op, me, go, U, D, L, R, hw, vw, do):
            return _draw(gm)
    return s


def _top_walls_for_depth(depth):
    if depth >= 4:
        return 12
    if depth == 3:
        return 10
    if depth == 2:
        return 8
    return TOP_WALLS


def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    check_flex = wb > 0
    ia, ib = a.bit_length() - 1, b.bit_length() - 1
    face_off = abs(_RC_R[ia] - _RC_R[ib]) <= 2 and abs(_RC_C[ia] - _RC_C[ib]) <= 1

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

        # Tránh tự bước vào vị trí để đối thủ nhảy qua đầu khi đối mặt
        if face_off:
            opp_moves_after = _pawn_moves(b, x, U, D, L, R)
            for om in opp_moves_after:
                d_om = _dist(om, gb, U, D, L, R)
                if db - d_om >= 2:
                    sc -= 30
                    break

        if check_flex and 0 < nd < nlay:
            s1 = (((x & U) >> 9) | ((x & D) << 9) | ((x & L) >> 1) | ((x & R) << 1)) & lay_m[nd - 1]
            if s1 & (s1 - 1):
                sc += MULTI_PATH_BONUS
            elif nd >= 2 and s1:
                s2 = (((s1 & U) >> 9) | ((s1 & D) << 9) | ((s1 & L) >> 1) | ((s1 & R) << 1)) & lay_m[nd - 2]
                if s2 and not (s2 & (s2 - 1)):
                    sc -= BOTTLENECK_PENALTY

        # Ưu tiên nhẹ các cột trung tâm để tránh bị ép góc
        sc -= abs(_RC_C[x.bit_length() - 1] - 4) * 0.5
        out.append((sc, x.bit_length() - 1, x, U, D, L, R, hw, vw, wa, nd, db))
    return None, out


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, top, defensive):
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    lay_o, do = _back_layers(gb, b, U, D, L, R, 0)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)

    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)

    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v

    # Tường phòng thủ / chặn đối đầu
    threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
    threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
    cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
    cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v

    # Tính điểm chuỗi tường (Wall Chaining)
    conn_h = _BORDER_H | (hw << 2) | (hw >> 2) | (vw << 1) | (vw >> 1) | (vw << 9) | (vw >> 9)
    conn_v = _BORDER_V | (vw << 18) | (vw >> 18) | (hw << 9) | (hw >> 9) | (hw << 1) | (hw >> 1)

    wa2 = wa - 1
    walls = []
    hist = _HISTORY
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
                chain_bonus = 3 if (w & conn_h) else 0
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
                chain_bonus = 3 if (w & conn_v) else 0

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
                if ndm > dm and (ndo - do) < (ndm - dm + 2):
                    continue
            else:
                ndm = dm

            order_sc = -_static(ndo, ndm, wb, wa2)
            h_bonus = min(6, hist[code] >> 6)
            order_sc += (4 if ndo > do else 0) + chain_bonus + h_bonus
            walls.append((order_sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo))

    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


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
    return (0, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo)


# ---------------------------------------------------------------------------
# Negamax PVS Engine
# ---------------------------------------------------------------------------
class _Searcher:
    __slots__ = ("deadline", "turn", "max_turns", "nodes", "killers", "cm")

    def __init__(self, deadline, turn, max_turns, cm):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        self.killers = [[None, None] for _ in range(128)]
        self.cm = cm

    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply, prev_move=0):
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

        if depth <= 0:
            if da <= 2 and any(x & ga for x in _pawn_moves(a, b, U, D, L, R)):
                return WIN - ply - 1
            if remaining == 1:
                return _draw(ga)
            return _static(da, db, wa, wb)

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

        lay_m, dm = _back_layers(ga, a, U, D, L, R, 2)
        win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
        if win is not None:
            return WIN - ply - 1

        pass_sc = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw)
        np1 = ply + 1

        ia, ib = a.bit_length() - 1, b.bit_length() - 1
        adj = abs(_RC_R[ia] - _RC_R[ib]) + abs(_RC_C[ia] - _RC_C[ib]) <= 2

        if (NMP_R and depth >= NMP_R + 2 and not adj and da > 2 and db > 2
                and pass_sc >= beta + (NMP_MARGIN if wb > 0 else 0)
                and -MATE_ZONE < beta < MATE_ZONE):
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
            best = (self._leaf_child(b, a, wb, wa, gb, ga, U, D, L, R,
                                     hw, vw, db, da, np1)
                    if threat else pass_sc)
            best_move = PASS
            for k in pawns:
                value = (self._leaf_child(b, k[2], wb, k[9], gb, ga,
                                         k[3], k[4], k[5], k[6], k[7], k[8],
                                         k[11], k[10], np1)
                         if threat else k[0])
                if value > best:
                    best = value
                    best_move = k[1]
            if best < beta and wa > 0:
                walls = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                   lay_m, dm, None if threat else 1, threat)
                for k in walls:
                    value = (self._leaf_child(b, a, wb, k[9], gb, ga,
                                             k[3], k[4], k[5], k[6], k[7], k[8],
                                             k[11], k[10], np1)
                             if threat else k[0])
                    if value > best:
                        best = value
                        best_move = k[1]
                    if best >= beta:
                        break
            flag = 1 if best >= beta else (2 if best <= alpha else 0)
            _TT[key] = (1, _to_tt(best, ply), flag, best_move)
            return best

        pawns.sort(reverse=True)
        if _BLOCK[0] and pass_sc >= -CONTEMPT:
            pawns.append((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db))

        orig_alpha = alpha
        best = -WIN * 2
        best_move = None
        nd = depth - 1
        tried = set()
        first = True
        killers = self.killers[ply]
        cm_move = self.cm[prev_move] if prev_move else None
        cutoff = False
        stage = 0
        can_lmr = depth >= 3 and da > 2 and db > 2

        while stage < 5 and not cutoff:
            if stage == 0:
                lst = ()
                if tt_move is not None:
                    if tt_move == PASS:
                        lst = ((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db),)
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
                        if km is not None and km not in tried:
                            c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                lst.append(c)
            elif stage == 2:
                lst = pawns
            elif stage == 3:
                if wa > 0:
                    defensive = (db <= 2) or (dm < db and wb > 0)
                    top_w = _top_walls_for_depth(depth)
                    lst = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                     lay_m, dm, top_w, defensive)
                else:
                    lst = ()
            else:
                lst = () if _BLOCK[0] else ((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db),)

            current_stage = stage
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
                    # LMR có chọn lọc: Không hạ độ sâu nếu tường gây chậm đối phương hoặc nối chuỗi
                    is_tactical_wall = (current_stage == 3 and k[11] > db)
                    if current_stage == 3 and can_lmr and wall_idx >= 4 and not is_tactical_wall:
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                          k[7], k[8], k[11], k[10], nd - 1, -alpha - 1, -alpha, np1, code)
                        if sc > alpha:
                            sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                              k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha, np1, code)
                            if alpha < sc < beta:
                                sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                                  k[6], k[7], k[8], k[11], k[10], nd, -beta,
                                                  -alpha, np1, code)
                    else:
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                          k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha, np1, code)
                        if alpha < sc < beta:
                            sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                              k[6], k[7], k[8], k[11], k[10], nd, -beta,
                                              -alpha, np1, code)

                if current_stage == 3:
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
                                if prev_move:
                                    self.cm[prev_move] = code
                            break

        if best <= orig_alpha:
            flag = 2
        elif best >= beta:
            flag = 1
        else:
            flag = 0
        _TT[key] = (depth, _to_tt(best, ply), flag, best_move)
        return best

    def _leaf_child(self, a, b, wa, wb, ga, gb, U, D, L, R,
                    hw, vw, da, db, ply):
        if a & ga:
            return -WIN + ply
        if b & gb:
            return WIN - ply
        if self.turn + ply >= self.max_turns:
            return -_draw(ga)
        if da <= 2 and any(x & ga for x in _pawn_moves(a, b, U, D, L, R)):
            return -WIN + ply + 1
        return -_static(da, db, wa, wb)


# ---------------------------------------------------------------------------
# Root Search
# ---------------------------------------------------------------------------
def _root_bias(child, a, b):
    code = child[1]
    if code == PASS:
        return 0 if _BLOCK[0] else -ROOT_PASS_PENALTY
    if code < H_BASE:
        reps = sum(
            1 for _, pawn, horizontal, vertical in _ACTIVE_RECENT
            if child[2] == pawn and child[7] == horizontal and child[8] == vertical
        )
        if reps > 0:
            return -ROOT_REPEAT_PENALTY * reps
    return 0


def _root_adjust(raw_score, bias):
    return raw_score if abs(raw_score) > MATE_ZONE else raw_score + bias


def _root_raw_bound(bound, bias):
    return bound if abs(bound) > MATE_ZONE else bound - bias


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
    kids.append((-_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw) - 1, PASS,
                 a, U, D, L, R, hw, vw, wa, da, db))

    biases = {child[1]: _root_bias(child, a, b) for child in kids}

    def root_order(k, scores=None):
        child = (b, k[2], wb, k[9], k[7], k[8], gb)
        value = _root_adjust(k[0], biases[k[1]]) if scores is None else scores.get(k[1], -WIN * 3)
        return (-value, child in _HIST, k[1] == PASS)

    kids.sort(key=root_order)
    best_move = kids[0][1]
    prev_score = _root_adjust(kids[0][0], biases[kids[0][1]])
    _STATE["depth"] = 1
    _STATE["score"] = prev_score
    if len(kids) == 1:
        return best_move, kids[0]

    deadline = t0 + TIME_LIMIT - _STATE["margin"]
    s = _Searcher(deadline, turn, max_turns, _CM)
    depth = 1
    scores = {}

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
        iteration_completed = False

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
                                      -_root_raw_bound(alpha, bias), 1, code),
                            bias
                        )
                        first = False
                    else:
                        sc = _root_adjust(
                            -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], depth - 1,
                                      -_root_raw_bound(alpha + 1, bias),
                                      -_root_raw_bound(alpha, bias), 1, code),
                            bias
                        )
                        if alpha < sc < beta:
                            prev_b, prev_s = cur_best, cur_best_sc
                            cur_best, cur_best_sc = code, sc
                            sc = _root_adjust(
                                -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                          k[6], k[7], k[8], k[11], k[10], depth - 1,
                                          -_root_raw_bound(beta, bias),
                                          -_root_raw_bound(alpha, bias), 1, code),
                                bias
                            )
                            cur_best, cur_best_sc = prev_b, prev_s

                    new_scores[code] = sc
                    if sc > cur_best_sc:
                        cur_best_sc = sc
                        cur_best = code
                        if sc > alpha:
                            alpha = sc
                            if alpha >= beta:
                                break

                if cur_best_sc <= asp_alpha and asp_alpha > -WIN * 2:
                    delta = int(delta * 2.5)
                    asp_alpha = prev_score - delta if delta < MATE_ZONE else -WIN * 2
                elif cur_best_sc >= asp_beta and asp_beta < WIN * 2:
                    kids.sort(key=lambda item: (0 if item[1] == cur_best else 1))
                    delta = int(delta * 2.5)
                    asp_beta = prev_score + delta if delta < MATE_ZONE else WIN * 2
                else:
                    iteration_completed = True
                    break
        except _TimeUp:
            _STATE["nodes"] = s.nodes
            # Nếu hết giờ giữa chừng, chỉ nhận cur_best nếu nó thực sự tốt hơn alpha
            if cur_best is not None and cur_best_sc > asp_alpha:
                best_move = cur_best
            break

        if iteration_completed and cur_best is not None:
            best_move = cur_best
            prev_score = cur_best_sc
            scores = new_scores
            _STATE["depth"] = depth
            _STATE["nodes"] = s.nodes
            _STATE["score"] = cur_best_sc
            raw_best = _root_raw_bound(cur_best_sc, biases[best_move])
            _STATE["raw_score"] = raw_best

            kids.sort(key=lambda k: root_order(k, scores))
            if abs(raw_best) > MATE_ZONE and WIN - abs(raw_best) <= depth:
                break
            now = perf_counter()
            if now + (now - it_start) * ITER_FACTOR > deadline:
                break

    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None


# ---------------------------------------------------------------------------
# Cờ tàn Retrograde Endgame Solver (Chính xác 100% khi đối thủ hết tường)
# ---------------------------------------------------------------------------
_EG_N = 9
_EG_CELLS = 81
_EG_SPAN = _EG_CELLS * _EG_CELLS
_EG_CACHE = None
_EG_VISITS = {}
_EG_CONTEXT = None
_EG_LAST_TURN = -1


class _EGTimeout(Exception):
    pass


def _eg_dist_map(goal_mask, U, D, L, R):
    dist = [999] * 81
    seen = front = goal_mask
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
        pending = _eg_deque()
        push = pending.append
        cget = contact.get
        g0, g1 = goals

        for p0 in range(81):
            if (p0 & 7) == 0 and perf_counter() >= deadline:
                raise _EGTimeout
            if r0[p0] >= 999:
                continue
            won0 = _RC_R[p0] == g0
            base0 = p0 * 81
            for p1 in range(81):
                if p1 == p0 or r1[p1] >= 999:
                    continue
                pair = base0 + p1
                if won0 or _RC_R[p1] == g1:
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


def solve_action(observation, deadline, U=None, D=None, L=None, R=None, hw=None, vw=None):
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
    if U is None:
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


# ---------------------------------------------------------------------------
# Giao diện chính choose_action
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


def _search_decide(observation, t0):
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
    goals = (ga, gb)
    if turn <= _STATE["last_turn"] or _STATE["goals"] != goals:
        _TT.clear()
        _HIST.clear()
        _ACTIVE_RECENT.clear()
        for i in range(len(_HISTORY)):
            _HISTORY[i] = 0
        for i in range(len(_CM)):
            _CM[i] = None
        _STATE["margin"] = 0.0
    elif len(_TT) > 150000:
        _TT.clear()
        for i in range(len(_HISTORY)):
            _HISTORY[i] >>= 1
    else:
        for i in range(len(_HISTORY)):
            _HISTORY[i] >>= 1

    _STATE["last_turn"] = turn
    _STATE["goals"] = goals
    max_turns = observation.get("max_turns", 500)

    check_blocks = (hw.bit_count() + vw.bit_count() if hasattr(int, "bit_count")
                    else (bin(hw).count("1") + bin(vw).count("1"))) >= BLOCK_MIN_WALLS
    if check_blocks != _BLOCK[0]:
        _TT.clear()
    _BLOCK[0] = check_blocks
    _BCACHE.clear()
    _BQ.clear()
    _ROOT_GOAL[0] = ga
    _HIST.add((a, b, wa, wb, hw, vw, ga))

    _ACTIVE_RECENT[:] = [entry for entry in _ACTIVE_RECENT
                         if entry[2:] == (hw, vw)
                         and 0 < turn - entry[0] <= 2 * ROOT_RECENT_WINDOW]
    _ACTIVE_RECENT.append((turn, a, hw, vw))
    del _ACTIVE_RECENT[:-ROOT_RECENT_WINDOW]

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


def _decide(observation, t0):
    if observation["opponent"]["walls_remaining"] == 0:
        avail = max(0.02, TIME_LIMIT - _STATE["margin"])
        exact_deadline = min(t0 + avail * 0.85, perf_counter() + 0.24)
        action = solve_action(observation, exact_deadline)
        if action is not None and _is_legal(action, observation):
            _STATE["mode"] = "exact_endgame"
            return action
    _STATE["mode"] = "search"
    return _search_decide(observation, t0)


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


def choose_action(observation: dict) -> dict:
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
        _STATE["margin"] = min(0.35 * TIME_LIMIT, _STATE["margin"] + 2 * late)
    return action