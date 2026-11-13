import time

N = 9
CELLS = 81
INF = 10 ** 6
WIN = 10 ** 5
WIN_STATIC = 5 * 10 ** 4

# tham số 
TIME_LIMIT = 0.55     
MAX_DEPTH = 14
TEMPO = 0.5            
WV = 2.0               
RACE_SCALE = 40.0      
MIN_GAIN_ROOT = 1      
MIN_GAIN_INNER = 1     
ROOT_WALLS = 14        
INNER_WALLS = 5        
REPEAT_PEN = 1.5       

_clock = time.monotonic
_deadline = 0.0
_visited = {}          
_nodes = 0
_tt = {}               
_EXACT, _LOWER, _UPPER = 0, 1, 2
TT_MAX = 100000        
_EPS = 0.01            
_MATE = WIN - 300      
_last = 0              


class _Timeout(Exception):
    pass


# bảng tính sẵn
FULL = (1 << CELLS) - 1
_ROW0 = 0x1FF
_COL0 = sum(1 << (9 * r) for r in range(9))
UP0 = FULL ^ _ROW0              
DN0 = FULL ^ (_ROW0 << 72)      
LF0 = FULL ^ _COL0              
RT0 = FULL ^ (_COL0 << 8)       


KU = [FULL] * 128
KD = [FULL] * 128
KL = [FULL] * 128
KR = [FULL] * 128
WEDGE = [0] * 128              
CONFLICT = [0] * 128          
for _o in (0, 1):
    for _r in range(8):
        for _c in range(8):
            _w = _o * 64 + _r * 8 + _c
            _a = _r * 9 + _c
            if _o == 0:
                KD[_w] = FULL ^ ((1 << _a) | (1 << (_a + 1)))
                KU[_w] = FULL ^ ((1 << (_a + 9)) | (1 << (_a + 10)))
                WEDGE[_w] = (1 << _a) | (1 << (_a + 1))
            else:
                KR[_w] = FULL ^ ((1 << _a) | (1 << (_a + 9)))
                KL[_w] = FULL ^ ((1 << (_a + 1)) | (1 << (_a + 10)))
                WEDGE[_w] = (1 << (81 + _a)) | (1 << (81 + _a + 9))
            _cm = 1 << _w
            if _o == 0:
                if _c >= 1:
                    _cm |= 1 << (_w - 1)
                if _c <= 6:
                    _cm |= 1 << (_w + 1)
                _cm |= 1 << (64 + _r * 8 + _c)
            else:
                if _r >= 1:
                    _cm |= 1 << (_w - 8)
                if _r <= 6:
                    _cm |= 1 << (_w + 8)
                _cm |= 1 << (_r * 8 + _c)
            CONFLICT[_w] = _cm

_tmp = [[] for _ in range(162)]
for _w in range(128):
    for _s in range(162):
        if (WEDGE[_w] >> _s) & 1:
            _tmp[_s].append(_w)
SLOT_WALLS = [tuple(t) for t in _tmp]
del _tmp


# BFS và đường đi
def bfs_dist(start, goal, up, dn, lf, rt):
    
    front = seen = 1 << start
    d = 0
    while not (front & goal):
        nxt = (((front & up) >> 9) | ((front & dn) << 9)
               | ((front & lf) >> 1) | ((front & rt) << 1)) & ~seen
        if not nxt:
            return INF
        seen |= nxt
        front = nxt
        d += 1
    return d


def layers_to(goal, tb, up, dn, lf, rt, extra):
    
    layers = [goal]
    front = seen = goal
    dist = 0 if goal & tb else -1
    more = extra
    while True:
        if dist >= 0:
            if more == 0:
                break
            more -= 1
        nxt = (((front << 9) & up) | ((front >> 9) & dn)
               | ((front << 1) & lf) | ((front >> 1) & rt)) & ~seen
        if not nxt:
            break
        seen |= nxt
        front = nxt
        layers.append(nxt)
        if dist < 0 and (nxt & tb):
            dist = len(layers) - 1
    return layers, dist


def cell_dist(layers, x, lo):
    for k in range(lo, len(layers)):
        if (layers[k] >> x) & 1:
            return k
    return len(layers) + 1


def path_mask(x, layers, dm, up, dn, lf, rt):
   
    m = 0
    for k in range(dm - 1, -1, -1):
        L = layers[k]
        b = 1 << x
        if (b & up) and ((L >> (x - 9)) & 1):
            x -= 9
            m |= 1 << x
        elif (b & dn) and ((L >> (x + 9)) & 1):
            m |= 1 << x
            x += 9
        elif (b & lf) and ((L >> (x - 1)) & 1):
            x -= 1
            m |= 1 << (81 + x)
        else:
            m |= 1 << (81 + x)
            x += 1
    return m


def pawn_moves(me, op, up, dn, lf, rt):
    
    res = []
    b = 1 << me
    if b & up:
        t = me - 9
        if t != op:
            res.append(t)
        else:
            tb = 1 << t
            if tb & up:
                res.append(t - 9)
            else:
                if tb & lf:
                    res.append(t - 1)
                if tb & rt:
                    res.append(t + 1)
    if b & dn:
        t = me + 9
        if t != op:
            res.append(t)
        else:
            tb = 1 << t
            if tb & dn:
                res.append(t + 9)
            else:
                if tb & lf:
                    res.append(t - 1)
                if tb & rt:
                    res.append(t + 1)
    if b & lf:
        t = me - 1
        if t != op:
            res.append(t)
        else:
            tb = 1 << t
            if tb & lf:
                res.append(t - 1)
            else:
                if tb & up:
                    res.append(t - 9)
                if tb & dn:
                    res.append(t + 9)
    if b & rt:
        t = me + 1
        if t != op:
            res.append(t)
        else:
            tb = 1 << t
            if tb & rt:
                res.append(t + 1)
            else:
                if tb & up:
                    res.append(t - 9)
                if tb & dn:
                    res.append(t + 9)
    return res


def cut_candidates(op, Lo, Do, up, dn, lf, rt):
    
    C = 1 << op
    found = set()
    for i in range(Do):
        nxt = (((C & up) >> 9) | ((C & dn) << 9)
               | ((C & lf) >> 1) | ((C & rt) << 1)) & Lo[Do - i - 1]
        eu = C & up & (nxt << 9)
        ed = C & dn & (nxt >> 9)
        el = C & lf & (nxt << 1)
        er = C & rt & (nxt >> 1)
        u = eu | ed | el | er
        u1 = u & (u - 1)
        if not (u1 & (u1 - 1)):         
            slots = []
            for m, mode in ((eu, 0), (ed, 1), (el, 2), (er, 3)):
                while m:
                    low = m & -m
                    x = low.bit_length() - 1
                    m ^= low
                    if mode == 0:
                        slots.append(x - 9)
                    elif mode == 1:
                        slots.append(x)
                    elif mode == 2:
                        slots.append(80 + x)
                    else:
                        slots.append(81 + x)
            if len(slots) == 1:
                found.update(SLOT_WALLS[slots[0]])
            elif len(slots) == 2:
                other = SLOT_WALLS[slots[1]]
                for w in SLOT_WALLS[slots[0]]:
                    if w in other:
                        found.add(w)
        C = nxt
    return found


# đánh giá
def ev_after(do_, dm_, wm_, wo_):
    
    if do_ <= 1:
        return -WIN_STATIC
    if wm_ == 0 and wo_ == 0:
        return (do_ - dm_ - TEMPO) * RACE_SCALE
    return (do_ - dm_) - TEMPO + WV * (wm_ - wo_)


def wall_children(me, op, wm, wo, gm, go, up, dn, lf, rt, placed, Lm, dm,
                  do_, min_gain, full=0):
  
    Lo, Do = layers_to(go, 1 << op, up, dn, lf, rt, 0)
    pm = path_mask(me, Lm, dm, up, dn, lf, rt)
    out = []
    cands = cut_candidates(op, Lo, Do, up, dn, lf, rt)
    if full:
        pmo = path_mask(op, Lo, Do, up, dn, lf, rt)
        while pmo:
            low = pmo & -pmo
            pmo ^= low
            cands.update(SLOT_WALLS[low.bit_length() - 1])
    for w in cands:
        if placed & CONFLICT[w]:
            continue
        u2 = up & KU[w]
        d2 = dn & KD[w]
        l2 = lf & KL[w]
        r2 = rt & KR[w]
        e_o = bfs_dist(op, go, u2, d2, l2, r2)
        if e_o >= INF:
            continue
        if pm & WEDGE[w]:
            e_m = bfs_dist(me, gm, u2, d2, l2, r2)
            if e_m >= INF:
                continue
        else:
            e_m = dm
        if (e_o - do_) - (e_m - dm) < min_gain:
            continue
        out.append((ev_after(e_o, e_m, wm - 1, wo), w, u2, d2, l2, r2))
    return out


# tìm kiếm 
def negamax(me, op, wm, wo, gm, go, up, dn, lf, rt, placed, depth, alpha, beta, ply):
    
    global _nodes
    _nodes += 1
    if _clock() > _deadline:
        raise _Timeout
    moves = pawn_moves(me, op, up, dn, lf, rt)
    for t in moves:
        if (gm >> t) & 1:
            return WIN - ply
    key = (me, op, wm, wo, gm, placed)
    ent = _tt.get(key)
    tt_move = None
    alpha0 = alpha
    if ent is not None:
        e_depth, e_flag, e_val, tt_move = ent
        if e_depth >= depth:
            if e_flag == _EXACT:
                return e_val
            if e_flag == _LOWER:
                if e_val > alpha:
                    alpha = e_val
            elif e_val < beta:
                beta = e_val
            if alpha >= beta:
                return e_val
    Lm, dm = layers_to(gm, 1 << me, up, dn, lf, rt, 2)
    if dm < 0:
        return -WIN_STATIC
    do_ = bfs_dist(op, go, up, dn, lf, rt)
    lo = dm - 2 if dm > 2 else 0

    if depth <= 1:
        best = -INF
        for t in moves:
            v = ev_after(do_, cell_dist(Lm, t, lo), wm, wo)
            if v > best:
                best = v
        if best < beta and wm > 0:
            for v, _w, _u, _d, _l, _r in wall_children(
                    me, op, wm, wo, gm, go, up, dn, lf, rt, placed, Lm, dm,
                    do_, MIN_GAIN_INNER):
                if v > best:
                    best = v
                    if best >= beta:
                        break
        if best == -INF:                      
            best = ev_after(do_, dm, wm, wo)
        _tt[key] = (-1 if abs(best) >= _MATE else depth,
                    _LOWER if best >= beta else _EXACT, best, tt_move)
        return best

    best = -INF
    best_mv = None
    tried = -1
    if tt_move is not None and tt_move[0] == 0 and tt_move[1] in moves:
        tried = tt_move[1]
        best = -negamax(op, tried, wo, wm, go, gm, up, dn, lf, rt, placed,
                        depth - 1, -beta, -alpha, ply + 1)
        best_mv = tt_move
        if best > alpha:
            alpha = best
    if alpha < beta:
        kids = [(ev_after(do_, cell_dist(Lm, t, lo), wm, wo), 0, t, 0, 0, 0, 0)
                for t in moves if t != tried]
        if wm > 0:
            walls = wall_children(me, op, wm, wo, gm, go, up, dn, lf, rt, placed, Lm, dm,
                                  do_, MIN_GAIN_INNER)
            walls.sort(reverse=True)
            for v, w, u2, d2, l2, r2 in walls[:INNER_WALLS]:
                kids.append((v, 1, w, u2, d2, l2, r2))
        if not kids and best_mv is None:
            kids.append((0.0, 2, 0, 0, 0, 0, 0))
        kids.sort(reverse=True)
        if tt_move is not None and tt_move[0] == 1:
            for i, k in enumerate(kids):
                if k[1] == 1 and k[2] == tt_move[1]:
                    kids.insert(0, kids.pop(i))
                    break
        for _v, kind, x, u2, d2, l2, r2 in kids:
            if kind == 0:
                args = (op, x, wo, wm, go, gm, up, dn, lf, rt, placed)
            elif kind == 1:
                args = (op, me, wo, wm - 1, go, gm, u2, d2, l2, r2, placed | (1 << x))
            else:
                args = (op, me, wo, wm, go, gm, up, dn, lf, rt, placed)
            if best_mv is None:
                s = -negamax(*args, depth - 1, -beta, -alpha, ply + 1)
            else:                               # PVS: thử cửa sổ rỗng trước
                s = -negamax(*args, depth - 1, -alpha - _EPS, -alpha, ply + 1)
                if alpha < s < beta:
                    s = -negamax(*args, depth - 1, -beta, -alpha, ply + 1)
            if s > best:
                best = s
                best_mv = (kind, x)
                if s > alpha:
                    alpha = s
                    if alpha >= beta:
                        break
    if best <= alpha0:
        flag = _UPPER
        best_mv = tt_move
    elif best >= beta:
        flag = _LOWER
    else:
        flag = _EXACT
    _tt[key] = (-1 if abs(best) >= _MATE else depth, flag, best, best_mv)
    return best


def _root_options(me, op, wm, wo, gm, go, up, dn, lf, rt, placed, legal_cells,
                  legal_walls):
    
    Lm, dm = layers_to(gm, 1 << me, up, dn, lf, rt, 2)
    do_ = bfs_dist(op, go, up, dn, lf, rt)
    lo = dm - 2 if dm > 2 else 0
    opts = []
    for t in legal_cells:
        pen = REPEAT_PEN * _visited.get((t, placed), 0)
        opts.append([ev_after(do_, cell_dist(Lm, t, lo), wm, wo) - pen, 0, t, None])
    if wm > 0 and legal_walls:
        walls = wall_children(me, op, wm, wo, gm, go, up, dn, lf, rt, placed, Lm, dm,
                              do_, MIN_GAIN_ROOT, 1)
        walls = [x for x in walls if x[1] in legal_walls]
        walls.sort(reverse=True)
        for v, w, u2, d2, l2, r2 in walls[:ROOT_WALLS]:
            opts.append([v, 1, w, (u2, d2, l2, r2)])
    opts.sort(key=lambda o: -o[0])
    return opts


def _root_iteration(opts, depth, ctx, partial):
    me, op, wm, wo, gm, go, up, dn, lf, rt, placed = ctx
    best_score = -INF
    best = None
    alpha = -INF
    for opt in opts:
        kind, x = opt[1], opt[2]
        if kind == 0:
            pen = REPEAT_PEN * _visited.get((x, placed), 0)
            v = negamax(op, x, wo, wm, go, gm, up, dn, lf, rt, placed,
                        depth - 1, -INF, -(alpha + pen), 1)
            score = -v - pen
        else:
            u2, d2, l2, r2 = opt[3]
            v = negamax(op, me, wo, wm - 1, go, gm, u2, d2, l2, r2,
                        placed | (1 << x), depth - 1, -INF, -alpha, 1)
            score = -v
        if score > best_score:
            best_score = score
            best = opt
            partial[0] = (score, opt)
            if score > alpha:
                alpha = score
    return best_score, best


def _to_action(opt):
    kind, x = opt[1], opt[2]
    if kind == 0:
        return {"type": "move", "to": [x // 9, x % 9]}
    o, rest = divmod(x, 64)
    return {"type": "wall", "orientation": "H" if o == 0 else "V",
            "row": rest // 8, "col": rest % 8}


def _fallback(observation):
    moves = observation.get("legal_moves") or []
    dist = observation.get("distances")
    if moves:
        def key(m):
            try:
                d = dist[m[0]][m[1]]
                return d if d >= 0 else 10 ** 6
            except Exception:
                return 0
        return {"type": "move", "to": list(min(moves, key=key))}
    return {"type": "pass"}


def choose_action(observation):
    global _deadline, _nodes, _last
    started = _clock()
    _deadline = started + TIME_LIMIT
    _nodes = 0
    if len(_tt) > TT_MAX:
        _tt.clear()
    try:
        you = observation["you"]
        opponent = observation["opponent"]
        me = you["position"][0] * 9 + you["position"][1]
        op = opponent["position"][0] * 9 + opponent["position"][1]
        gm = _ROW0 << (9 * you["goal_row"])
        go = _ROW0 << (9 * opponent["goal_row"])
        wm = you["walls_remaining"]
        wo = opponent["walls_remaining"]
        legal_cells = [m[0] * 9 + m[1] for m in observation["legal_moves"]]
        for t in legal_cells:
            if (gm >> t) & 1:
                return {"type": "move", "to": [t // 9, t % 9]}
        up, dn, lf, rt = UP0, DN0, LF0, RT0
        placed = 0
        for r, c in observation["walls"]["horizontal"]:
            w = r * 8 + c
            placed |= 1 << w
            up &= KU[w]
            dn &= KD[w]
        for r, c in observation["walls"]["vertical"]:
            w = 64 + r * 8 + c
            placed |= 1 << w
            lf &= KL[w]
            rt &= KR[w]
        legal_walls = set()
        for w in observation["legal_walls"]:
            legal_walls.add((0 if w["orientation"] == "H" else 64) + w["row"] * 8 + w["col"])
        if not legal_cells and not legal_walls:
            return {"type": "pass"}

        ctx = (me, op, wm, wo, gm, go, up, dn, lf, rt, placed)
        opts = _root_options(*ctx, legal_cells, legal_walls)
        if not opts:
            return _fallback(observation)
        best = opts[0]
        depth = 2
        while depth <= MAX_DEPTH:
            partial = [None]
            try:
                score, choice = _root_iteration(opts, depth, ctx, partial)
            except _Timeout:
               
                if partial[0] is not None and partial[0][1] is not opts[0]:
                    best = partial[0][1]
                break
            best = choice
            _last = depth
            if score >= WIN - 100 or score <= -WIN + 100:
                break
            opts.remove(choice)
            opts.insert(0, choice)
            depth += 1
        if best[1] == 0:
            key = (best[2], placed)
            _visited[key] = _visited.get(key, 0) + 1
        return _to_action(best)
    except Exception:
        return _fallback(observation)
