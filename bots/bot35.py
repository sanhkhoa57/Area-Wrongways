              

import time

# ---------------------------------------------------------------------------
# Board geometry & precomputed tables
# ---------------------------------------------------------------------------
SIZE = 9
NCELL = 81
FULL = (1 << NCELL) - 1
INF = 10 ** 9
WIN = 100000

ROW_MASK = [0] * SIZE
COL_MASK = [0] * SIZE
for _r in range(SIZE):
    for _c in range(SIZE):
        ROW_MASK[_r] |= 1 << (_r * SIZE + _c)
        COL_MASK[_c] |= 1 << (_r * SIZE + _c)

BASE_UP = FULL & ~ROW_MASK[0]
BASE_DOWN = FULL & ~ROW_MASK[8]
BASE_LEFT = FULL & ~COL_MASK[0]
BASE_RIGHT = FULL & ~COL_MASK[8]
BASE_OPEN = (BASE_UP, BASE_DOWN, BASE_LEFT, BASE_RIGHT)

# direction index: 0 up, 1 down, 2 left, 3 right
DELTA = (-9, 9, -1, 1)
PERP = ((2, 3), (2, 3), (0, 1), (0, 1))

WALL_INFO = [None] * 128
WALL_ID = {}
WALL_CLEAR = [None] * 128
CONFLICT = [0] * 128
EDGE_WALLS = [[0, 0, 0, 0] for _ in range(NCELL)]
NEIGH = [[-1, -1, -1, -1] for _ in range(NCELL)]


def _bit(r, c):
    return 1 << (r * SIZE + c)


def _init():
    for r in range(8):
        for c in range(8):
            h = r * 8 + c
            v = 64 + h
            WALL_INFO[h] = ("H", r, c)
            WALL_INFO[v] = ("V", r, c)
            WALL_ID[("H", r, c)] = h
            WALL_ID[("V", r, c)] = v
            WALL_CLEAR[h] = (_bit(r + 1, c) | _bit(r + 1, c + 1),
                             _bit(r, c) | _bit(r, c + 1),
                             0, 0)
            WALL_CLEAR[v] = (0, 0,
                             _bit(r, c + 1) | _bit(r + 1, c + 1),
                             _bit(r, c) | _bit(r + 1, c))
            ch = (1 << h) | (1 << v)
            if c > 0:
                ch |= 1 << (h - 1)
            if c < 7:
                ch |= 1 << (h + 1)
            CONFLICT[h] = ch
            cv = (1 << v) | (1 << h)
            if r > 0:
                cv |= 1 << (v - 8)
            if r < 7:
                cv |= 1 << (v + 8)
            CONFLICT[v] = cv
    for r in range(SIZE):
        for c in range(SIZE):
            u = r * SIZE + c
            if r > 0:
                NEIGH[u][0] = u - 9
                m = 0
                for cc in (c, c - 1):
                    if 0 <= cc < 8:
                        m |= 1 << ((r - 1) * 8 + cc)
                EDGE_WALLS[u][0] = m
            if r < 8:
                NEIGH[u][1] = u + 9
                m = 0
                for cc in (c, c - 1):
                    if 0 <= cc < 8:
                        m |= 1 << (r * 8 + cc)
                EDGE_WALLS[u][1] = m
            if c > 0:
                NEIGH[u][2] = u - 1
                m = 0
                for rr in (r, r - 1):
                    if 0 <= rr < 8:
                        m |= 1 << (64 + rr * 8 + c - 1)
                EDGE_WALLS[u][2] = m
            if c < 8:
                NEIGH[u][3] = u + 1
                m = 0
                for rr in (r, r - 1):
                    if 0 <= rr < 8:
                        m |= 1 << (64 + rr * 8 + c)
                EDGE_WALLS[u][3] = m


_init()
NEIGH = [tuple(x) for x in NEIGH]
EDGE_WALLS = [tuple(x) for x in EDGE_WALLS]
CENTER = [max(0, 4 - abs((u % 9) - 4)) for u in range(NCELL)]


def open_after(op, wid):
    cu, cd, cl, cr = WALL_CLEAR[wid]
    return (op[0] & ~cu, op[1] & ~cd, op[2] & ~cl, op[3] & ~cr)


def open_from_mask(wmask):
    up, down, left, right = BASE_OPEN
    m = wmask
    while m:
        low = m & -m
        wid = low.bit_length() - 1
        m ^= low
        cu, cd, cl, cr = WALL_CLEAR[wid]
        up &= ~cu
        down &= ~cd
        left &= ~cl
        right &= ~cr
    return (up, down, left, right)


# ---------------------------------------------------------------------------
# Pathfinding: bit-parallel BFS
# ---------------------------------------------------------------------------
_DIST = {}


def dist_map(goal, wmask, op):
    key = (goal, wmask)
    d = _DIST.get(key)
    if d is not None:
        return d
    up, down, left, right = op
    d = [-1] * NCELL
    frontier = ROW_MASK[goal]
    reached = frontier
    k = 0
    while frontier:
        m = frontier
        while m:
            low = m & -m
            d[low.bit_length() - 1] = k
            m ^= low
        k += 1
        nxt = (((frontier & up) >> 9) | ((frontier & down) << 9)
               | ((frontier & left) >> 1) | ((frontier & right) << 1)) & ~reached
        reached |= nxt
        frontier = nxt
    _DIST[key] = d
    return d


def pawn_moves(me, opp, op):
    out = []
    for k in range(4):
        if not (op[k] >> me) & 1:
            continue
        v = NEIGH[me][k]
        if v != opp:
            out.append(v)
            continue
        if (op[k] >> v) & 1:
            out.append(NEIGH[v][k])
        else:
            for pk in PERP[k]:
                if (op[pk] >> v) & 1:
                    out.append(NEIGH[v][pk])
    return out


def is_jump_trap(my_next, opp, op, opp_dist):
    """True if moving to my_next lets opp immediately jump over us for a progress gain."""
    d_cur = opp_dist[opp]
    if d_cur <= 1:
        return False
    for k in range(4):
        if not (op[k] >> opp) & 1:
            continue
        v = NEIGH[opp][k]
        if v != my_next:
            continue
        if (op[k] >> v) & 1:
            beyond = NEIGH[v][k]
            if 0 <= opp_dist[beyond] <= d_cur - 2:
                return True
        else:
            for pk in PERP[k]:
                if (op[pk] >> v) & 1:
                    side = NEIGH[v][pk]
                    if 0 <= opp_dist[side] <= d_cur - 2:
                        return True
    return False


def dag_walls(u, dist, op):
    du = dist[u]
    if du <= 0:
        return 0
    walls = 0
    layer = 1 << u
    while du > 0:
        nd = du - 1
        nxt = 0
        m = layer
        while m:
            low = m & -m
            x = low.bit_length() - 1
            m ^= low
            nb = NEIGH[x]
            ew = EDGE_WALLS[x]
            for k in range(4):
                if (op[k] >> x) & 1:
                    y = nb[k]
                    if dist[y] == nd:
                        walls |= ew[k]
                        nxt |= 1 << y
        layer = nxt
        du = nd
    return walls


def first_flex(u, dist, op):
    du = dist[u]
    if du <= 0:
        return 2
    n = 0
    nb = NEIGH[u]
    for k in range(4):
        if (op[k] >> u) & 1 and dist[nb[k]] == du - 1:
            n += 1
    return n


# ---------------------------------------------------------------------------
# The Two Books of Sapient (Refined)
# ---------------------------------------------------------------------------
def _cum(marg):
    out = [0]
    for x in marg:
        out.append(out[-1] + x)
    return tuple(out)


# Wall reserves: preserves high value for remaining walls
BOOK_FIRST = {
    "name": "A-first",
    "time": 0.70,
    "step": 105,
    "tempo": 55,
    "wall_val": _cum((100, 90, 80, 72, 65, 60, 56, 52, 48, 45)),
    "flex": 8,
    "lock": 600,
    "root_min_gain": 1,
    "root_min_gain_far": 2,
    "far_dist": 4,
    "opening_free_moves": 3,
}

BOOK_SECOND = {
    "name": "B-second",
    "time": 0.70,
    "step": 100,
    "tempo": 40,
    "wall_val": _cum((110, 95, 85, 76, 68, 62, 58, 54, 50, 46)),
    "flex": 8,
    "lock": 600,
    "root_min_gain": 2,
    "root_min_gain_far": 2,
    "far_dist": 4,
    "opening_free_moves": 2,
}

_BOOK = BOOK_FIRST


# ---------------------------------------------------------------------------
# Strategic Evaluation
# ---------------------------------------------------------------------------
def evaluate(pos, goals, walls, wmask, op, stm):
    me = stm
    ot = 1 - stm
    dm = dist_map(goals[me], wmask, op)
    do = dist_map(goals[ot], wmask, op)
    a = dm[pos[me]]
    b = do[pos[ot]]
    if a < 0:
        a = 60
    if b < 0:
        b = 60

    bk = _BOOK
    wv = bk["wall_val"]
    eff_b = (9.0 + (b - 9.0) * 0.25) if b > 9 else float(b)
    s = int(bk["step"] * (eff_b - a)) + bk["tempo"] + wv[walls[me]] - wv[walls[ot]]

    # Anti-Wall-Spam: penalize falling 3+ walls behind in midgame
    if walls[ot] - walls[me] >= 3 and b > 3:
        s -= (walls[ot] - walls[me] - 2) * 20
    if walls[me] - walls[ot] >= 3 and a > 3:
        s += (walls[me] - walls[ot] - 2) * 20

    # Race-lock bonus: if opponent has no walls and we win the race
    if walls[ot] == 0 and a <= b:
        s += bk["lock"] + 25 * walls[me]
    elif walls[me] == 0 and b < a:
        s -= bk["lock"] + 25 * walls[ot]

    # Route flexibility
    f = bk["flex"]
    if f:
        s += f * (min(first_flex(pos[me], dm, op), 3) - min(first_flex(pos[ot], do, op), 3))
    return s


# ---------------------------------------------------------------------------
# Wall candidate generator
# ---------------------------------------------------------------------------
def wall_candidates(pos, goals, walls, wmask, op, stm, min_gain, limit, deadline=None):
    me = stm
    ot = 1 - stm
    if walls[me] <= 0:
        return []
    dm = dist_map(goals[me], wmask, op)
    do = dist_map(goals[ot], wmask, op)
    a = dm[pos[me]]
    b = do[pos[ot]]
    if a <= 0 or b <= 0:
        return []

    cut_op = dag_walls(pos[ot], do, op)
    cut_me = dag_walls(pos[me], dm, op)
    out = []
    m = cut_op
    orow = pos[ot] // 9
    ocol = pos[ot] % 9
    n = 0
    while m:
        low = m & -m
        wid = low.bit_length() - 1
        m ^= low
        if CONFLICT[wid] & wmask:
            continue
        n += 1
        if deadline is not None and (n & 7) == 0 and time.perf_counter() > deadline:
            raise TimeoutError
        nw = wmask | low
        nop = open_after(op, wid)
        nb = dist_map(goals[ot], nw, nop)[pos[ot]]
        if nb < 0:
            continue
        if low & cut_me:
            na = dist_map(goals[me], nw, nop)[pos[me]]
            if na < 0:
                continue
        else:
            na = a
        delay = nb - b
        gain = delay - (na - a)
        if gain < min_gain:
            continue
        _, wr, wc = WALL_INFO[wid]
        prox = abs(wr - orow) + abs(wc - ocol)
        out.append((gain * 100 + delay * 15 - prox, wid, nw, nop, gain))
    out.sort(reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# Search Engine
# ---------------------------------------------------------------------------
_TT = {}
EXACT, LOWER, UPPER = 0, 1, 2


class _Timeout(Exception):
    pass


class Searcher:
    def __init__(self, goals, deadline):
        self.goals = goals
        self.deadline = deadline
        self.nodes = 0

    def search(self, pos, walls, wmask, op, stm, depth, alpha, beta, ply):
        self.nodes += 1
        if (self.nodes & 31) == 0 and time.perf_counter() > self.deadline:
            raise _Timeout
        goals = self.goals
        me = stm
        ot = 1 - stm
        if pos[ot] // 9 == goals[ot]:
            return -WIN + ply
        if depth <= 0:
            return evaluate(pos, goals, walls, wmask, op, stm)

        key = (pos[0], pos[1], walls[0], walls[1], stm, wmask)
        ent = _TT.get(key)
        tt_move = None
        if ent is not None:
            e_depth, e_flag, e_val, tt_move = ent
            if e_depth >= depth:
                if e_flag == EXACT:
                    return e_val
                if e_flag == LOWER and e_val >= beta:
                    return e_val
                if e_flag == UPPER and e_val <= alpha:
                    return e_val

        dm = dist_map(goals[me], wmask, op)
        moves = pawn_moves(pos[me], pos[ot], op)
        g = goals[me]
        for v in moves:
            if v // 9 == g:
                return WIN - ply - 1

        kids = []
        for v in moves:
            dv = dm[v]
            if dv < 0:
                dv = 99
            kids.append((-dv * 10 + CENTER[v], 0, v, None, None))
        if walls[me] > 0 and depth >= 1:
            lim = 6 if depth >= 3 else (5 if depth == 2 else 4)
            for pr, wid, nw, nop, gain in wall_candidates(pos, goals, walls, wmask, op, stm, 1, lim):
                kids.append((pr - 170, 1, wid, nw, nop))
        kids.sort(reverse=True)
        if tt_move is not None:
            for i, kd in enumerate(kids):
                if (kd[1], kd[2]) == tt_move:
                    if i:
                        kids.insert(0, kids.pop(i))
                    break

        best = -INF
        best_move = None
        a0 = alpha
        first = True
        for _, kind, x, nw, nop in kids:
            if kind == 0:
                npos = (x, pos[1]) if me == 0 else (pos[0], x)
                nwalls, nwm, nop2 = walls, wmask, op
            else:
                npos = pos
                nwalls = (walls[0] - 1, walls[1]) if me == 0 else (walls[0], walls[1] - 1)
                nwm, nop2 = nw, nop
            if first:
                val = -self.search(npos, nwalls, nwm, nop2, ot, depth - 1, -beta, -alpha, ply + 1)
                first = False
            else:
                val = -self.search(npos, nwalls, nwm, nop2, ot, depth - 1, -alpha - 1, -alpha, ply + 1)
                if alpha < val < beta:
                    val = -self.search(npos, nwalls, nwm, nop2, ot, depth - 1, -beta, -val, ply + 1)
            if val > best:
                best = val
                best_move = (kind, x)
                if val > alpha:
                    alpha = val
                    if alpha >= beta:
                        break
        if best <= a0:
            flag = UPPER
        elif best >= beta:
            flag = LOWER
        else:
            flag = EXACT
        _TT[key] = (depth, flag, best, best_move)
        return best


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------
_HIST = []
_HIST_ME = []
_FIRST = None


def _reset():
    global _FIRST
    del _HIST[:]
    del _HIST_ME[:]
    _TT.clear()
    _FIRST = None


def _fallback(obs):
    moves = obs.get("legal_moves") or []
    if not moves:
        return {"type": "pass"}
    dist = obs.get("distances")
    best = moves[0]
    bd = INF
    for r, c in moves:
        try:
            d = dist[r][c]
        except Exception:
            d = -1
        if 0 <= d < bd:
            best, bd = [r, c], d
    return {"type": "move", "to": [int(best[0]), int(best[1])]}


def _act_move(u):
    return {"type": "move", "to": [u // 9, u % 9]}


def _act_wall(wid):
    o, r, c = WALL_INFO[wid]
    return {"type": "wall", "orientation": o, "row": r, "col": c}


# ---------------------------------------------------------------------------
# Strategic decision
# ---------------------------------------------------------------------------
def _decide(obs, book):
    global _BOOK
    t0 = time.perf_counter()
    deadline = t0 + book["time"]
    _BOOK = book
    me = obs["player_id"]
    ot = 1 - me
    you, opp = obs["you"], obs["opponent"]
    pos = [0, 0]
    pos[me] = you["position"][0] * 9 + you["position"][1]
    pos[ot] = opp["position"][0] * 9 + opp["position"][1]
    pos = tuple(pos)
    goals = [0, 0]
    goals[me] = you["goal_row"]
    goals[ot] = opp["goal_row"]
    goals = tuple(goals)
    walls = [0, 0]
    walls[me] = you["walls_remaining"]
    walls[ot] = opp["walls_remaining"]
    walls = tuple(walls)
    wmask = 0
    for r, c in obs["walls"]["horizontal"]:
        wmask |= 1 << (r * 8 + c)
    for r, c in obs["walls"]["vertical"]:
        wmask |= 1 << (64 + r * 8 + c)
    op = open_from_mask(wmask)

    legal_moves = {r * 9 + c for r, c in obs["legal_moves"]}
    legal_walls = set()
    for w in obs["legal_walls"]:
        wid = WALL_ID.get((w["orientation"], w["row"], w["col"]))
        if wid is not None:
            legal_walls.add(wid)

    # 1. Immediate Win
    for u in legal_moves:
        if u // 9 == goals[me]:
            return _act_move(u)

    if len(_DIST) > 50000:
        _DIST.clear()
    if len(_TT) > 150000:
        _TT.clear()

    dm = dist_map(goals[me], wmask, op)
    do = dist_map(goals[ot], wmask, op)
    a = dm[pos[me]]
    b = do[pos[ot]]

    # 2. Extended Hard Crisis Defense (Opponent <= 2 steps from victory and we are not winning immediately)
    if b <= 2 and a > b and walls[me] > 0 and legal_walls:
        best_crisis_wid = None
        best_crisis_gain = 0
        best_crisis_nb = b
        for wid in legal_walls:
            nw = wmask | (1 << wid)
            nop = open_after(op, wid)
            nb = dist_map(goals[ot], nw, nop)[pos[ot]]
            na = dist_map(goals[me], nw, nop)[pos[me]]
            if nb > b and na >= 0:
                gain = (nb - b) - (na - a)
                if nb > best_crisis_nb or (nb == best_crisis_nb and gain > best_crisis_gain):
                    best_crisis_nb = nb
                    best_crisis_gain = gain
                    best_crisis_wid = wid
        if best_crisis_wid is not None and best_crisis_nb > b:
            return _act_wall(best_crisis_wid)

    _HIST.append((pos[me], pos[ot], wmask))
    _HIST_ME.append(pos[me])

    def rep_count(u):
        exact = sum(1 for h in _HIST if h[0] == u and h[1] == pos[ot] and h[2] == wmask)
        recent = sum(1 for p in _HIST_ME[-8:] if p == u)
        return exact * 2 + recent

    # Race move
    def race_move():
        best, bs = None, -INF
        for u in legal_moves:
            du = dm[u]
            if du < 0:
                continue
            trap_pen = 160 if is_jump_trap(u, pos[ot], op, do) else 0
            s = -du * 100 + first_flex(u, dm, op) * 3 + CENTER[u] - 40 * rep_count(u) - trap_pen
            if s > bs:
                best, bs = u, s
        return best

    rm = race_move()
    fallback = _act_move(rm) if rm is not None else _fallback(obs)

    # 3. Sprint mode: pure race to victory without burning walls
    pd = abs(pos[me] // 9 - pos[ot] // 9) + abs(pos[me] % 9 - pos[ot] % 9)
    if rm is not None and pd > 2:
        if (walls[ot] == 0 and a <= b) or (a <= 3 and b >= a + 3):
            return fallback

    # 4. Opening fast-start rules (Book A & Book B)
    my_turn_index = obs["turn"] // 2
    slack = 0 if book is BOOK_FIRST else 1
    if wmask == 0 and my_turn_index < book["opening_free_moves"] and a <= b + slack and rm is not None:
        return fallback

    # 5. Build Root Candidates with Jump Trap Penalty
    root = []
    for u in legal_moves:
        du = dm[u]
        if du < 0:
            continue
        trap_pen = 160 if is_jump_trap(u, pos[ot], op, do) else 0
        root.append((-du * 10 + CENTER[u] - trap_pen, 0, u, None, None))
    if walls[me] > 0 and legal_walls:
        mg = book["root_min_gain"]
        if b <= a + 1 and walls[me] >= 4:
            mg = 1
        elif b > book["far_dist"]:
            mg = max(mg, book["root_min_gain_far"])
        if b >= 10 and b >= a + 4 and walls[me] <= 6:
            mg = max(mg, 3)
        for pr, wid, nw, nop, gain in wall_candidates(pos, goals, walls, wmask, op, me, mg, 12, deadline=deadline - 0.05):
            if wid in legal_walls:
                root.append((pr - 170, 1, wid, nw, nop))
    root.sort(reverse=True)
    if not root:
        return fallback

    # 6. Iterative Deepening Negamax
    s = Searcher(goals, deadline)
    best_act = (root[0][1], root[0][2])
    try:
        for depth in range(1, 16):
            if time.perf_counter() > deadline - 0.03:
                break
            scored = []
            alpha = -INF
            for item in root:
                _, kind, x, nw, nop = item
                if kind == 0:
                    npos = (x, pos[1]) if me == 0 else (pos[0], x)
                    nwalls, nwm, nop2 = walls, wmask, op
                else:
                    npos = pos
                    nwalls = (walls[0] - 1, walls[1]) if me == 0 else (walls[0], walls[1] - 1)
                    nwm, nop2 = nw, nop
                if not scored:
                    val = -s.search(npos, nwalls, nwm, nop2, ot, depth - 1, -INF, INF, 1)
                else:
                    val = -s.search(npos, nwalls, nwm, nop2, ot, depth - 1, -alpha - 1, -alpha, 1)
                    if val > alpha:
                        val = -s.search(npos, nwalls, nwm, nop2, ot, depth - 1, -INF, -alpha, 1)
                adj = 0
                if kind == 0:
                    rc = rep_count(x)
                    if rc:
                        adj -= 35 * rc * rc
                scored.append((val + adj, item))
                if val + adj > alpha:
                    alpha = val + adj
            scored.sort(key=lambda t: -t[0])
            root = [it for _, it in scored]
            best_act = (root[0][1], root[0][2])
            if scored[0][0] >= WIN - 100 or scored[0][0] <= -WIN + 100:
                break
    except (_Timeout, TimeoutError):
        pass

    kind, x = best_act
    if kind == 0:
        return _act_move(x)
    return _act_wall(x)


def choose_action(observation):
    global _FIRST
    try:
        turn = observation.get("turn", 0)
        if turn <= 1:
            _reset()
        if _FIRST is None:
            _FIRST = (observation.get("player_id", 0) == 0)
        book = BOOK_FIRST if _FIRST else BOOK_SECOND
        act = _decide(observation, book)
        return act
    except Exception:
        return _fallback(observation)
