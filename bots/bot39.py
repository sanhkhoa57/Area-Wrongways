"""Self-contained Wrongway Arena agent for 9x9 Quoridor.

The arena imports this file and calls ``choose_action(observation)`` once per
turn.  Everything the agent needs lives here: a bitboard board state, exact
path distances, Quoridor move generation, wall legality, a time-bounded
negamax/PVS search, and a defensive observation adapter.

Only the Python standard library is used.  No file or network I/O happens at
import time or during a call.

Board convention
----------------
Cells are ``i = r * 9 + c``.  Player 0 starts at ``(8, 4)`` and wins on row 0;
player 1 starts at ``(0, 4)`` and wins on row 8.  Inside the search the agent
always plays "player 0" (the side to move in the observation) and the
opponent is "player 1"; the observation is authoritative about goals.

Wall encoding
-------------
``BD`` has bit ``i`` set when the edge ``i <-> i+9`` is blocked.
``BR`` has bit ``i`` set when the edge ``i <-> i+1`` is blocked.
``H(wr, wc)`` sets ``BD`` bits ``wr*9+wc`` and ``wr*9+wc+1``.
``V(wr, wc)`` sets ``BR`` bits ``wr*9+wc`` and ``(wr+1)*9+wc``.
"""

import gc
import time

# The caches below hold tens of thousands of tuples.  None of them can form a
# reference cycle, so the cyclic collector has nothing to find and only costs
# the occasional multi-millisecond pause - which a 1 s arena limit cannot
# afford.  bot2.py disables it for the same reason.
gc.disable()

# ---------------------------------------------------------------------------
# Board constants
# ---------------------------------------------------------------------------

N = 9
CELLS = N * N
FULL = (1 << CELLS) - 1

ROW_MASK = tuple(
    sum(1 << (r * N + c) for c in range(N)) for r in range(N)
)
NOT_LEFT_COL = FULL
for _r in range(N):
    NOT_LEFT_COL &= ~(1 << (_r * N))
NOT_RIGHT_COL = FULL
for _r in range(N):
    NOT_RIGHT_COL &= ~(1 << (_r * N + N - 1))

# Wall slot index: wr * 8 + wc, wr/wc in [0, 7].
H_BD = tuple(
    (1 << ((i >> 3) * N + (i & 7)))
    | (1 << ((i >> 3) * N + (i & 7) + 1))
    for i in range(64)
)
V_BR = tuple(
    (1 << ((i >> 3) * N + (i & 7)))
    | (1 << (((i >> 3) + 1) * N + (i & 7)))
    for i in range(64)
)

# Search / evaluation constants.  The weights start from
# wrongway.app/js/bot.js, style "duel9/Renner", and are exposed for tuning.
WIN = 100000
MATE_BOUND = WIN // 2
INF = 999
MAX_SCORE = 10 ** 9

W_BASE = 42.8
W_DIST = 100.0
W_WALL = 130.0
W_USABLE = 30.0
W_RACE = 646.2
SURE = 1000.0
W_OPP_NOWALL = -21.9

MAX_DEPTH = 24
TIME_BUDGET = 0.92          # internal budget; the platform allows 1.0 s
PARSE_BUDGET = 0.08         # wall inference must not eat the search budget
ASPIRATION = 60
LMR_MOVES = 3               # reduce pawn moves after the first few
LMR_WALLS = 2               # reduce wall moves after the two best-ordered ones

WALL_BASE = 100             # actions >= 100 are walls
TT_LIMIT = 80000
NODE_MASK = 31              # deadline check cadence inside the search
NODE_BUDGET = 80000
DEFENSE_TIME = 0.045
DEFENSE_LIMIT = 5
DYNAMIC_WALLS = False
ROOT_REPEAT = 80.0
ROOT_BACK = 35.0
_ROOT_VISITS = {}
_SEARCH_TT = {}
_GOALS = None
_WALL_VALUES = [W_WALL, W_WALL]

# ---------------------------------------------------------------------------
# Deterministic Zobrist hashing (splitmix64)
# ---------------------------------------------------------------------------

_MASK64 = (1 << 64) - 1


def _splitmix64(x):
    x = (x + 0x9E3779B97F4A7C15) & _MASK64
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (z ^ (z >> 31)) & _MASK64


def _zobrist_tables():
    state = 0x243F6A8885A308D3
    pawn = [[0] * CELLS for _ in range(2)]
    for side in range(2):
        for i in range(CELLS):
            state += 1
            pawn[side][i] = _splitmix64(state)
    hw = [0] * 64
    for i in range(64):
        state += 1
        hw[i] = _splitmix64(state)
    vw = [0] * 64
    for i in range(64):
        state += 1
        vw[i] = _splitmix64(state)
    state += 1
    side_key = _splitmix64(state)
    return pawn, hw, vw, side_key


Z_PAWN, Z_HW, Z_VW, Z_SIDE = _zobrist_tables()

_DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _wall_id(ori, r, c):
    return WALL_BASE + (ori << 6) + (r << 3) + c


def _wall_parts(wid):
    code = wid - WALL_BASE
    return code >> 6, code & 63


def _build_walk_tables():
    """Per-cell walk steps: (neighbour, board selector, edge bit, wall ids).

    Rebuilding these tuples inside the path walk accounted for most of the
    search's wall-generation cost, so they are precomputed once.  Two orders are
    kept because the walk prefers steps toward the goal row.
    """
    up = []
    down = []
    for u in range(CELLS):
        r, c = divmod(u, N)
        steps = {}
        for dr, dc in _DIRS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < N and 0 <= nc < N):
                continue
            v = nr * N + nc
            if nr == r + 1:
                board, bit = 0, 1 << (r * N + c)
            elif nr == r - 1:
                board, bit = 0, 1 << ((r - 1) * N + c)
            elif nc == c + 1:
                board, bit = 1, 1 << (r * N + c)
            else:
                board, bit = 1, 1 << (r * N + c - 1)
            if dr == -1:
                raw = [(0, r - 1, c)]
                if c > 0:
                    raw.append((0, r - 1, c - 1))
            elif dr == 1:
                raw = [(0, r, c)]
                if c > 0:
                    raw.append((0, r, c - 1))
            elif dc == -1:
                raw = [(1, r, c - 1)]
                if r > 0:
                    raw.append((1, r - 1, c - 1))
            else:
                raw = [(1, r, c)]
                if r > 0:
                    raw.append((1, r - 1, c))
            steps[(dr, dc)] = (
                v, board, bit,
                tuple(_wall_id(o, wr, wc) for o, wr, wc in raw
                      if 0 <= wr <= 7 and 0 <= wc <= 7),
            )
        up.append(tuple(steps[k] for k in ((-1, 0), (1, 0), (0, -1), (0, 1))
                        if k in steps))
        down.append(tuple(steps[k] for k in ((1, 0), (-1, 0), (0, -1), (0, 1))
                          if k in steps))
    return tuple(up), tuple(down)


_WALK_UP, _WALK_DOWN = _build_walk_tables()


# ---------------------------------------------------------------------------
# Board state
# ---------------------------------------------------------------------------


class _State:
    """Mutable bitboard position used by the search."""

    __slots__ = ("pos", "goal", "wl", "bd", "br", "hw", "vw", "side", "hash")

    def __init__(self, pos0, pos1, goal0, goal1, wl0=10, wl1=10, side=0):
        self.pos = [pos0, pos1]
        self.goal = [goal0, goal1]
        self.wl = [wl0, wl1]
        self.bd = 0
        self.br = 0
        self.hw = 0
        self.vw = 0
        self.side = side
        h = 0
        if side:
            h ^= Z_SIDE
        h ^= Z_PAWN[0][pos0] ^ Z_PAWN[1][pos1]
        self.hash = h

    def add_wall(self, ori, idx):
        """Add a wall without touching wall counts or the side to move."""
        if ori == 0:
            self.bd |= H_BD[idx]
            self.hw |= 1 << idx
            self.hash ^= Z_HW[idx]
        else:
            self.br |= V_BR[idx]
            self.vw |= 1 << idx
            self.hash ^= Z_VW[idx]


def state_from_walls(walls, pos0=(8, 4), pos1=(0, 4), goal0=0, goal1=8,
                     walls0=10, walls1=10, side=0):
    """Build a :class:`_State` from ``(orientation, row, col)`` wall triples.

    ``orientation`` may be ``"H"``/``"V"`` or ``0``/``1``; walls may also be
    given as ``"H-r-c"`` strings.  This is the bridge used by the test suite.
    """
    st = _State(pos0[0] * N + pos0[1], pos1[0] * N + pos1[1],
                goal0, goal1, walls0, walls1, side)
    for w in walls or ():
        ori, r, c = _wall_tuple(w)
        st.add_wall(ori, r * 8 + c)
    return st


def _wall_tuple(w):
    if isinstance(w, str):
        parts = w.replace("|", "-").split("-")
        return (0 if parts[0].upper().startswith("H") else 1,
                int(parts[1]), int(parts[2]))
    ori = w[0]
    if isinstance(ori, str):
        ori = 0 if ori.upper().startswith("H") else 1
    return int(ori), int(w[1]), int(w[2])


def distance_field(st, goal_row):
    """Public wrapper: exact distance from every cell to ``goal_row``."""
    return _dist_field(goal_row, st.bd, st.br)


def legal_moves(st):
    """Public wrapper: legal pawn destinations for the side to move."""
    return _legal_moves(st.pos[st.side], st.pos[st.side ^ 1], st.bd, st.br)


# ---------------------------------------------------------------------------
# Distances (bit-frontier BFS)
# ---------------------------------------------------------------------------


def _dist_field(goal_row, bd, br):
    """Exact distance of every cell to ``goal_row``; ``INF`` when unreachable."""
    field = [INF] * CELLS
    base = goal_row * N
    for c in range(N):
        field[base + c] = 0
    seen = front = ROW_MASK[goal_row]
    d = 0
    # The four complement masks are loop invariants; hoisting them out of the
    # frontier loop is a measurable slice of the whole search.
    nbd = ~bd
    nbr = ~br
    nbd9 = ~(bd << 9)
    nbr1 = ~(br << 1)
    full = FULL
    left = NOT_LEFT_COL
    right = NOT_RIGHT_COL
    while True:
        nxt = (
            ((front >> 9) & nbd)
            | ((front << 9) & nbd9)
            | (((front & left) >> 1) & nbr)
            | (((front & right) << 1) & nbr1)
        )
        nxt &= ~seen & full
        if not nxt:
            return field
        d += 1
        bits = nxt
        while bits:
            low = bits & -bits
            field[low.bit_length() - 1] = d
            bits ^= low
        seen |= nxt
        front = nxt


def _dist_to(goal_row, cell, bd, br):
    """Distance from ``cell`` to ``goal_row`` with early exit at the target."""
    if cell // N == goal_row:
        return 0
    seen = front = ROW_MASK[goal_row]
    target = 1 << cell
    d = 0
    nbd = ~bd
    nbr = ~br
    nbd9 = ~(bd << 9)
    nbr1 = ~(br << 1)
    full = FULL
    left = NOT_LEFT_COL
    right = NOT_RIGHT_COL
    while True:
        nxt = (
            ((front >> 9) & nbd)
            | ((front << 9) & nbd9)
            | (((front & left) >> 1) & nbr)
            | (((front & right) << 1) & nbr1)
        )
        nxt &= ~seen & full
        if not nxt:
            return INF
        d += 1
        if nxt & target:
            return d
        seen |= nxt
        front = nxt

# A distance field is a pure function of (goal row, blocked-down, blocked-right),
# so memoising it is exact.  The search hits the same wall sets constantly: every
# sibling that only moves a pawn shares its field with the parent, and successive
# turns keep almost all of the previous wall set.
_FIELD_CACHE = {}
_DIST_CACHE = {}
_FIELD_LIMIT = 60000

def _field(goal_row, bd, br):
    """Memoised distance field; the returned list must not be mutated."""
    key = (goal_row, bd, br)
    got = _FIELD_CACHE.get(key)
    if got is None:
        got = _dist_field(goal_row, bd, br)
        if len(_FIELD_CACHE) >= _FIELD_LIMIT:
            _FIELD_CACHE.clear()
        _FIELD_CACHE[key] = got
    return got

def _dist_at(goal_row, cell, bd, br):
    """Distance to ``goal_row`` from ``cell`` using the memoised field."""
    field = _FIELD_CACHE.get((goal_row, bd, br))
    if field is not None:
        return field[cell]
    key = (goal_row, bd, br, cell)
    value = _DIST_CACHE.get(key)
    if value is None:
        value = _dist_to(goal_row, cell, bd, br)
        if len(_DIST_CACHE) > 120000:
            _DIST_CACHE.clear()
        _DIST_CACHE[key] = value
    return value


# ---------------------------------------------------------------------------
# Move generation
# ---------------------------------------------------------------------------


def _edge_blocked(r, c, nr, nc, bd, br):
    if nr == r + 1:
        return (bd >> (r * N + c)) & 1
    if nr == r - 1:
        return (bd >> ((r - 1) * N + c)) & 1
    if nc == c + 1:
        return (br >> (r * N + c)) & 1
    return (br >> (r * N + c - 1)) & 1


def _build_move_table():
    """Per-cell pawn steps: (target, board, bit) plus the jump variants.

    Kept in the same order as ``_DIRS`` so move ordering (and therefore the
    search) is bit-identical to the divmod version; this only removes the
    per-node arithmetic and function calls.
    """
    table = []
    for u in range(CELLS):
        r, c = divmod(u, N)
        steps = []
        for dr, dc in _DIRS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < N and 0 <= nc < N):
                continue
            v = nr * N + nc
            if nr == r + 1:
                board, bit = 0, 1 << (r * N + c)
            elif nr == r - 1:
                board, bit = 0, 1 << ((r - 1) * N + c)
            elif nc == c + 1:
                board, bit = 1, 1 << (r * N + c)
            else:
                board, bit = 1, 1 << (r * N + c - 1)

            def edge(r0, c0, r1, c1):
                if r1 == r0 + 1:
                    return 0, 1 << (r0 * N + c0)
                if r1 == r0 - 1:
                    return 0, 1 << ((r0 - 1) * N + c0)
                if c1 == c0 + 1:
                    return 1, 1 << (r0 * N + c0)
                return 1, 1 << (r0 * N + c0 - 1)

            jr, jc = nr + dr, nc + dc
            if 0 <= jr < N and 0 <= jc < N:
                b2, t2 = edge(nr, nc, jr, jc)
                v2 = jr * N + jc
            else:
                v2, b2, t2 = -1, 0, 0
            sides = []
            for sr, sc in ((nr + dc, nc + dr), (nr - dc, nc - dr)):
                if 0 <= sr < N and 0 <= sc < N:
                    b3, t3 = edge(nr, nc, sr, sc)
                    sides.append((sr * N + sc, b3, t3))
                else:
                    sides.append((-1, 0, 0))
            steps.append((v, board, bit, v2, b2, t2, sides[0], sides[1]))
        table.append(tuple(steps))
    return tuple(table)


_MOVE_TABLE = _build_move_table()


def _legal_moves(me, op, bd, br):
    """Legal pawn moves as target cell indices, matching game-logic.js."""
    out = []
    for step in _MOVE_TABLE[me]:
        v, board, bit = step[0], step[1], step[2]
        if (br if board else bd) & bit:
            continue
        if v != op:
            out.append(v)
            continue
        v2, b2, t2 = step[3], step[4], step[5]
        if v2 >= 0 and not ((br if b2 else bd) & t2):
            out.append(v2)
            continue
        s1 = step[6]
        if s1[0] >= 0 and not ((br if s1[1] else bd) & s1[2]):
            out.append(s1[0])
        s2 = step[7]
        if s2[0] >= 0 and not ((br if s2[1] else bd) & s2[2]):
            out.append(s2[0])
    return out


# ---------------------------------------------------------------------------
# Walls
# ---------------------------------------------------------------------------


def _wall_geom_ok(ori, idx, bd, br, hw, vw):
    """Overlap rule from game-logic.js, expressed on bitboards."""
    bit = 1 << idx
    if ori == 0:
        return not (hw & bit) and not (bd & H_BD[idx]) and not (vw & bit)
    return not (vw & bit) and not (br & V_BR[idx]) and not (hw & bit)


def _wall_preserves_paths(state, ori, idx):
    """True when placing the wall leaves both pawns a path to their goal."""
    if ori == 0:
        state.bd |= H_BD[idx]
    else:
        state.br |= V_BR[idx]
    ok = (
        _dist_at(state.goal[0], state.pos[0], state.bd, state.br) < INF
        and _dist_at(state.goal[1], state.pos[1], state.bd, state.br) < INF
    )
    if ori == 0:
        state.bd ^= H_BD[idx]
    else:
        state.br ^= V_BR[idx]
    return ok


def _wall_legal(state, ori, idx):
    return (
        state.wl[state.side] > 0
        and _wall_geom_ok(ori, idx, state.bd, state.br, state.hw, state.vw)
        and _wall_preserves_paths(state, ori, idx)
    )


# ---------------------------------------------------------------------------
# Action application / restoration
# ---------------------------------------------------------------------------


class _Timeout(Exception):
    pass


class _Ctx:
    __slots__ = ("deadline", "nodes", "tt", "killers", "history", "clock",
                 "allowed", "max_nodes", "penalties", "path")

    def __init__(self, deadline, allowed=None, max_nodes=0):
        self.deadline = deadline
        self.nodes = 0
        self.tt = _SEARCH_TT
        self.killers = [[-1, -1] for _ in range(64)]
        self.history = [0] * 128
        self.clock = time.perf_counter
        self.allowed = allowed
        self.max_nodes = max_nodes
        self.penalties = {}
        self.path = set()


def _reset_memory(turn):
    """Keep the distance-field cache coherent across games."""
    global _GAME_TURN
    if turn <= _GAME_TURN:
        _FIELD_CACHE.clear()
        _DIST_CACHE.clear()
        _ROOT_VISITS.clear()
        _SEARCH_TT.clear()
    _GAME_TURN = turn


_GAME_TURN = -1


def _do_action(state, act):
    side = state.side
    if act < WALL_BASE:
        old = state.pos[side]
        state.pos[side] = act
        state.hash ^= Z_PAWN[side][old] ^ Z_PAWN[side][act]
        undo = (0, side, old)
    else:
        ori, idx = _wall_parts(act)
        if ori == 0:
            state.bd |= H_BD[idx]
            state.hw |= 1 << idx
            state.hash ^= Z_HW[idx]
        else:
            state.br |= V_BR[idx]
            state.vw |= 1 << idx
            state.hash ^= Z_VW[idx]
        state.wl[side] -= 1
        undo = (1, side, (ori, idx))
    state.side ^= 1
    state.hash ^= Z_SIDE
    return undo


def _undo_action(state, undo):
    kind, side, payload = undo
    state.side = side
    state.hash ^= Z_SIDE
    if kind == 0:
        cur = state.pos[side]
        state.pos[side] = payload
        state.hash ^= Z_PAWN[side][cur] ^ Z_PAWN[side][payload]
    else:
        ori, idx = payload
        if ori == 0:
            state.bd ^= H_BD[idx]
            state.hw ^= 1 << idx
            state.hash ^= Z_HW[idx]
        else:
            state.br ^= V_BR[idx]
            state.vw ^= 1 << idx
            state.hash ^= Z_VW[idx]
        state.wl[side] += 1


# ---------------------------------------------------------------------------
# Wall candidates
# ---------------------------------------------------------------------------


def _path_walls(state, tl, who=None):
    """Walls that block edges on ``who``'s current shortest path.

    ``who`` defaults to the side that is not to move, i.e. the pawn whose route
    the current player wants to obstruct.
    """
    if who is None:
        who = state.side ^ 1
    field = _field(state.goal[who], state.bd, state.br)
    bd = state.bd
    br = state.br
    table = _WALK_DOWN if state.goal[who] == 8 else _WALK_UP
    u = state.pos[who]
    out = []
    seen = set()
    add = seen.add
    steps = 0
    while steps < tl:
        d = field[u]
        if d <= 0:
            break
        nxt = -1
        for v, board, bit, wids in table[u]:
            if (br if board else bd) & bit:
                continue
            if field[v] != d - 1:
                continue
            nxt = v
            for wid in wids:
                if wid not in seen:
                    add(wid)
                    out.append(wid)
            break
        if nxt < 0:
            break
        u = nxt
        steps += 1
    return out


def _score_walls(state, cand, allowed, max_take, deadline, my_path=None, min_gain=1):
    """Filter candidates by legality and net gain; return at most ``max_take``.

    ``my_path`` is the set of walls blocking the mover's own current shortest
    path.  Blocking a wall outside that set cannot lengthen that path, so the
    mover's distance is reused instead of recomputing a whole BFS: this halves
    the cost of wall generation, which dominates search time.
    """
    side = state.side
    opp = side ^ 1
    me = state.pos[side]
    op = state.pos[opp]
    goal_me = state.goal[side]
    goal_op = state.goal[opp]
    bd = state.bd
    br = state.br
    hw = state.hw
    vw = state.vw
    h_bd = H_BD
    v_br = V_BR
    dist = _dist_at
    d_me = dist(goal_me, me, bd, br)
    d_op = dist(goal_op, op, bd, br)
    if my_path is None:
        my_path = _path_walls(state, 8, side)
    scored = []
    clock = time.perf_counter
    base = WALL_BASE
    for n, wid in enumerate(cand):
        if (n & 15) == 15 and clock() > deadline:
            break
        if allowed is not None and wid not in allowed:
            continue
        code = wid - base
        ori = code >> 6
        idx = code & 63
        # Inlined _wall_geom_ok: this loop runs once per candidate per node.
        bit = 1 << idx
        if ori == 0:
            if hw & bit or bd & h_bd[idx] or vw & bit:
                continue
        elif vw & bit or br & v_br[idx] or hw & bit:
            continue
        if ori == 0:
            state.bd = bd | h_bd[idx]
        else:
            state.br = br | v_br[idx]
        state_bd = state.bd
        state_br = state.br
        n_op = dist(goal_op, op, state_bd, state_br)
        if n_op >= INF:
            n_me = INF
        elif wid in my_path:
            n_me = dist(goal_me, me, state_bd, state_br)
        else:
            n_me = d_me
        state.bd = bd
        state.br = br
        if n_op >= INF or n_me >= INF:
            continue
        gain = (n_op - d_op) - (n_me - d_me)
        if gain >= min_gain:
            scored.append((gain, wid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [wid for _, wid in scored[:max_take]]


def _wall_threats(state, who, deadline):
    """Legal single-wall detours, largest first, across a complete goal path."""
    bd, br = state.bd, state.br
    goal, pawn = state.goal[who], state.pos[who]
    other = who ^ 1
    distance = _dist_at(goal, pawn, bd, br)
    threats = []
    for wid in _path_walls(state, CELLS, who):
        if time.perf_counter() >= deadline:
            break
        ori, idx = _wall_parts(wid)
        if not _wall_geom_ok(ori, idx, bd, br, state.hw, state.vw):
            continue
        nbd = bd | H_BD[idx] if ori == 0 else bd
        nbr = br | V_BR[idx] if ori else br
        new_distance = _dist_at(goal, pawn, nbd, nbr)
        if not distance < new_distance < INF:
            continue
        if _dist_at(state.goal[other], state.pos[other], nbd, nbr) >= INF:
            continue
        threats.append((new_distance - distance, wid))
    threats.sort(key=lambda pair: (-pair[0], pair[1]))
    return threats


def _defensive_walls(state, allowed, deadline):
    side = state.side
    if state.wl[side] == 0 or state.wl[side ^ 1] == 0:
        return []
    threats = _wall_threats(state, side, deadline)
    if not threats or threats[0][0] < 2:
        return []
    candidates = set()
    for _, wid in threats[:8]:
        ori, idx = _wall_parts(wid)
        row, col = divmod(idx, 8)
        candidates.add(_wall_id(1 - ori, row, col))
        for delta in (-1, 1):
            r, c = (row, col + delta) if ori == 0 else (row + delta, col)
            if 0 <= r < 8 and 0 <= c < 8:
                candidates.add(_wall_id(ori, r, c))
    distance = _dist_at(state.goal[side], state.pos[side], state.bd, state.br)
    ranked = []
    for wid in sorted(candidates):
        if time.perf_counter() >= deadline:
            break
        if allowed is not None and wid not in allowed:
            continue
        ori, idx = _wall_parts(wid)
        if not _wall_legal(state, ori, idx):
            continue
        undo = _do_action(state, wid)
        try:
            if _dist_at(state.goal[side], state.pos[side], state.bd, state.br) != distance:
                continue
            after = _wall_threats(state, side, deadline)
            if time.perf_counter() >= deadline:
                break
            danger = after[0][0] if after else 0
            improvement = threats[0][0] - danger
            if improvement >= 1:
                ranked.append((improvement, wid))
        finally:
            _undo_action(state, undo)
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    return [wid for _, wid in ranked[:DEFENSE_LIMIT]]


def _wall_candidates(state, depth, allowed, deadline):
    """Ordered wall moves for a search node (empty when no wall is useful)."""
    if state.wl[state.side] <= 0:
        return []
    if depth >= 90:
        take, tl = 24, CELLS
    elif depth >= 3:
        take, tl = 10, CELLS
    elif depth == 2:
        take, tl = 8, CELLS
    else:
        take, tl = 5, 8
    cand = _path_walls(state, tl)
    my_path = set(_path_walls(state, CELLS, state.side))
    result = _score_walls(state, cand, allowed, take, deadline, my_path, 1)
    if not result and allowed:
        # Model and observation disagree about which walls are legal: look at
        # a bounded sample of the observation's own legal walls.
        sample = []
        for wid in sorted(allowed):
            ori, idx = _wall_parts(wid)
            if _wall_geom_ok(ori, idx, state.bd, state.br, state.hw, state.vw):
                sample.append(wid)
            if len(sample) >= 24:
                break
        result = _score_walls(state, sample, allowed, take, deadline, my_path)
    return result


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _evaluate(state, d_me, d_op):
    if d_me >= INF:
        return -WIN + 4000
    if d_op >= INF:
        return WIN - 4000
    if d_me == 0:
        return WIN - 1000
    if d_op == 0:
        return -WIN + 1000
    side = state.side
    w_me = state.wl[side]
    w_op = state.wl[side ^ 1]
    if w_op == 0 and d_me < d_op:
        return SURE + (d_op - d_me) + 0.1 * w_me
    if w_me == 0 and d_op < d_me - 1:
        return -SURE - (d_me - 1 - d_op)
    score = (
        W_BASE
        + W_DIST * (d_op - d_me)
        + _WALL_VALUES[side] * w_me - _WALL_VALUES[side ^ 1] * w_op
        + W_USABLE * (min(w_me, d_op) - min(w_op, d_me))
        + W_OPP_NOWALL * ((1 if w_op == 0 else 0) - (1 if w_me == 0 else 0))
    )
    if w_me == 0 and w_op == 0:
        score += W_RACE * (1 if d_me <= d_op else -1)
    return score


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _negamax(state, depth, alpha, beta, ply, ctx):
    ctx.nodes += 1
    if (ctx.nodes & NODE_MASK) == 0 and (
            (ctx.max_nodes and ctx.nodes >= ctx.max_nodes)
            or ctx.clock() >= ctx.deadline):
        raise _Timeout
    side = state.side
    opp = side ^ 1
    me, op = state.pos[side], state.pos[opp]
    f_me = _field(state.goal[side], state.bd, state.br)
    f_op = _field(state.goal[opp], state.bd, state.br)
    dm, do = f_me[me], f_op[op]
    if do == 0:
        return -WIN + ply
    if dm == 0:
        return WIN - ply
    # A jump covers two cells, so a pawn two rows out can still land on its
    # goal row; below that no move reaches the row and the test is skipped.
    if dm <= 2 and any(m // N == state.goal[side]
                       for m in _legal_moves(me, op, state.bd, state.br)):
        return WIN - ply - 1
    if depth <= 0:
        return _evaluate(state, dm, do)
    moves = _legal_moves(me, op, state.bd, state.br)
    key = (state.hash, state.wl[0], state.wl[1])
    if key in ctx.path:
        return _evaluate(state, dm, do)
    entry = ctx.tt.get(key)
    tt_act = -1
    if entry is not None:
        ed, score, flag, tt_act = entry
        if ed >= depth:
            if score > MATE_BOUND:
                score -= ply
            elif score < -MATE_BOUND:
                score += ply
            if flag == 0 or (flag == 1 and score >= beta) or (flag == 2 and score <= alpha):
                return score
    walls = _wall_candidates(state, depth, None, ctx.deadline)
    children = [(4 * (dm - f_me[m]), m) for m in moves]
    # _wall_candidates already returns walls best-gain-first; reusing that order
    # avoids two extra distance probes per wall at every search node.
    weight = len(walls) + 1
    for position, wall in enumerate(walls):
        children.append((weight - position, wall))
    killers = ctx.killers[ply] if ply < len(ctx.killers) else (-1, -1)
    children.sort(key=lambda pair: (pair[1] == tt_act, pair[1] in killers, pair[0]), reverse=True)
    initial_alpha = alpha
    best, best_act = -MAX_SCORE, -1
    ctx.path.add(key)
    for index, (_, act) in enumerate(children):
        undo = _do_action(state, act)
        try:
            if index == 0:
                score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
            else:
                reduced = depth >= 4 and index >= 6
                score = -_negamax(state, depth - 1 - reduced, -alpha - .001, -alpha, ply + 1, ctx)
                if score > alpha and (reduced or score < beta):
                    score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
        finally:
            _undo_action(state, undo)
        if score > best:
            best, best_act = score, act
        if score > alpha:
            alpha = score
        if alpha >= beta:
            if ply < len(ctx.killers) and act != killers[0]:
                killers[1], killers[0] = killers[0], act
            break
    ctx.path.discard(key)
    if best_act < 0:
        return _evaluate(state, dm, do)
    flag = 2 if best <= initial_alpha else 1 if best >= beta else 0
    stored = best + ply if best > MATE_BOUND else best - ply if best < -MATE_BOUND else best
    if entry is None or depth >= entry[0]:
        ctx.tt[key] = (depth, stored, flag, best_act)
        if len(ctx.tt) > TT_LIMIT:
            ctx.tt.clear()
    return best


def _root_pass(state, ctx, ordered, depth, alpha, beta, scores):
    best = -MAX_SCORE
    best_act = -1
    first = True
    for act in ordered:
        if ctx.clock() > ctx.deadline:
            raise _Timeout
        if act < WALL_BASE and act // N == state.goal[state.side]:
            score = WIN - 1
        else:
            penalty = ctx.penalties.get(act, 0.0)
            lower, upper = alpha + penalty, beta + penalty
            undo = _do_action(state, act)
            try:
                if first:
                    score = -_negamax(state, depth - 1, -upper, -lower, 1, ctx)
                    first = False
                else:
                    score = -_negamax(state, depth - 1, -lower - 0.001, -lower, 1, ctx)
                    if lower < score < upper:
                        score = -_negamax(state, depth - 1, -upper, -lower, 1, ctx)
                score -= penalty
            finally:
                _undo_action(state, undo)
        scores[act] = score
        if score > best:
            best = score
            best_act = act
            if score > alpha:
                alpha = score
        if alpha >= beta:
            break
    return best, best_act


def _search(state, root_moves, allowed_walls, deadline, max_depth, max_nodes=0):
    """Iterative deepening.  Returns ``(action, score, depth, nodes)``."""
    global _GOALS
    goals = tuple(state.goal)
    if _GOALS != goals:
        _SEARCH_TT.clear()
        _GOALS = goals
    for _k in [k for k, v in _SEARCH_TT.items() if abs(v[1]) > MATE_BOUND]:
        del _SEARCH_TT[_k]
    ctx = _Ctx(deadline, None, max_nodes)
    ctx.allowed = allowed_walls if allowed_walls is not None else None
    f_me = _field(state.goal[state.side], state.bd, state.br)
    moves = list(root_moves)
    moves.sort(key=lambda m: (f_me[m], m))
    side = state.side
    for who in (0, 1):
        if DYNAMIC_WALLS:
            threats = _wall_threats(state, who ^ 1, deadline)
            damage = threats[0][0] if threats else 0
            _WALL_VALUES[who] = min(240.0, W_WALL + 10.0 * damage)
        else:
            _WALL_VALUES[who] = W_WALL
    for move in moves:
        visits = _ROOT_VISITS.get((move, state.pos[side ^ 1], state.hw, state.vw), 0)
        ctx.penalties[move] = ROOT_REPEAT * visits + ROOT_BACK * max(0, f_me[move] - f_me[state.pos[side]])
    walls = _wall_candidates(state, 99, ctx.allowed, deadline)
    if DEFENSE_TIME > 0:
        extra = _defensive_walls(state, ctx.allowed, min(deadline, time.perf_counter() + DEFENSE_TIME))
        walls += [wall for wall in extra if wall not in walls]
    actions = moves + walls
    if not actions:
        return None, 0.0, 0, 0

    scores = {}
    rank = {a: i for i, a in enumerate(actions)}
    last_act = actions[0]
    last_score = 0.0
    completed = 0
    prev = 0.0

    for depth in range(1, max_depth + 1):
        alpha = -MAX_SCORE
        beta = MAX_SCORE
        if depth >= 3 and abs(prev) <= MATE_BOUND:
            alpha = prev - ASPIRATION
            beta = prev + ASPIRATION
        while True:
            if depth == 1:
                ordered = actions
            else:
                ordered = sorted(actions, key=lambda a: (-scores.get(a, -MAX_SCORE), rank[a]))
            try:
                best, best_act = _root_pass(state, ctx, ordered, depth, alpha, beta, scores)
            except _Timeout:
                return last_act, last_score, completed, ctx.nodes
            if best_act == -1:
                return last_act, last_score, completed, ctx.nodes
            if best <= alpha and alpha > -MAX_SCORE:
                alpha = -MAX_SCORE
                continue
            if best >= beta and beta < MAX_SCORE:
                beta = MAX_SCORE
                continue
            break
        if best < -MATE_BOUND and completed >= 1 and last_score >= -MATE_BOUND:
            # Selective search (pruned walls, LMR) cannot prove a loss: keep the
            # deepest non-losing iteration instead of resigning.
            break
        last_act = best_act
        last_score = best
        completed = depth
        actions.sort(key=lambda a: (-scores.get(a, -MAX_SCORE), rank[a]))
        for i, a in enumerate(actions):
            rank[a] = i
        if abs(best) > MATE_BOUND:
            break
        prev = best
    return last_act, last_score, completed, ctx.nodes


# ---------------------------------------------------------------------------
# Observation parsing
# ---------------------------------------------------------------------------


def _as_int(v):
    try:
        if isinstance(v, bool):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_rc(v):
    """Normalise ``[r, c]`` / ``{"r","c"}`` / ``{"to": ...}`` to ``(r, c)``."""
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        r, c = _as_int(v[0]), _as_int(v[1])
        if r is None or c is None:
            return None
        return r, c
    if isinstance(v, dict):
        for k in ("to", "position", "pos", "cell", "square"):
            if k in v:
                got = _as_rc(v[k])
                if got is not None:
                    return got
        r = None
        c = None
        for k in ("r", "row"):
            if k in v:
                r = _as_int(v[k])
                break
        for k in ("c", "col"):
            if k in v:
                c = _as_int(v[k])
                break
        if r is not None and c is not None:
            return r, c
    return None


def _norm_move(v):
    rc = _as_rc(v)
    if rc is None:
        return None
    r, c = rc
    if 0 <= r < N and 0 <= c < N:
        return r * N + c
    return None


def _norm_wall(v):
    """Normalise a wall to ``(orientation, row, col)`` or ``None``."""
    if isinstance(v, str):
        try:
            return _wall_tuple(v)
        except (ValueError, IndexError):
            return None
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        ori = v[0]
        if isinstance(ori, str):
            ori = 0 if ori.upper().startswith("H") else 1
        r, c = _as_int(v[1]), _as_int(v[2])
    elif isinstance(v, dict):
        for k in ("key", "wall", "id"):
            if k in v and isinstance(v[k], str):
                return _norm_wall(v[k])
        ori = v.get("orientation", v.get("ori", v.get("type", v.get("direction"))))
        if ori is None:
            if "H" in v:
                ori = 0
            elif "V" in v:
                ori = 1
        if isinstance(ori, str):
            ori = 0 if ori.upper().startswith("H") else 1
        r = _as_int(v.get("row", v.get("r")))
        c = _as_int(v.get("col", v.get("c")))
    else:
        return None
    ori = _as_int(ori)
    if ori not in (0, 1) or r is None or c is None:
        return None
    if not (0 <= r <= 7 and 0 <= c <= 7):
        return None
    return ori, r, c


def _player_position(p):
    if not isinstance(p, dict):
        return None
    for k in ("position", "pos", "cell", "square"):
        if k in p:
            rc = _as_rc(p[k])
            if rc is not None:
                return rc
    rc = _as_rc(p)
    if rc is not None and 0 <= rc[0] < N and 0 <= rc[1] < N:
        return rc
    return None


def _player_goal(p, pos, index):
    if isinstance(p, dict):
        for k in ("goal_row", "goal", "goalrow"):
            if k in p:
                g = _as_int(p[k])
                if g is not None and 0 <= g < N:
                    return g
    if index == 0:
        return 0
    if index == 1:
        return 8
    if pos is not None:
        if pos[0] >= 5:
            return 0
        if pos[0] <= 3:
            return 8
    return 0


def _player_walls(p):
    if isinstance(p, dict):
        for k in ("walls_left", "walls_remaining", "walls", "barricades", "wall_count"):
            if k in p:
                v = _as_int(p[k])
                if v is not None and v >= 0:
                    return v
    return None


def _extract_walls(obs):
    """Return explicit ``(ori, r, c)`` walls, or ``None`` when absent."""
    source = None
    for container in (obs, obs.get("state") if isinstance(obs.get("state"), dict) else None):
        if not isinstance(container, dict):
            continue
        for k in ("walls", "barricades", "wall_list", "placed_walls"):
            if k in container:
                source = container[k]
                break
        if source is not None:
            break
    if source is None:
        return None
    out = []
    if isinstance(source, dict):
        for key in ("horizontal", "h", "H", "hw"):
            seq = source.get(key)
            if isinstance(seq, (list, tuple)):
                for item in seq:
                    w = _norm_wall(item if not isinstance(item, (list, tuple))
                                   or len(item) >= 3 else (0, item[0], item[1]))
                    if w is not None:
                        out.append(w)
        for key in ("vertical", "v", "V", "vw"):
            seq = source.get(key)
            if isinstance(seq, (list, tuple)):
                for item in seq:
                    w = _norm_wall(item if not isinstance(item, (list, tuple))
                                   or len(item) >= 3 else (1, item[0], item[1]))
                    if w is not None:
                        out.append(w)
        return out
    if isinstance(source, (list, tuple, set)):
        for item in source:
            w = _norm_wall(item)
            if w is not None:
                out.append(w)
        return out
    return None


def _norm_distances(v):
    if isinstance(v, dict):
        grid = [[INF] * N for _ in range(N)]
        found = False
        for key, val in v.items():
            if isinstance(key, str):
                parts = key.replace(",", " ").split()
                if len(parts) == 2:
                    r, c = _as_int(parts[0]), _as_int(parts[1])
                else:
                    continue
            elif isinstance(key, (list, tuple)) and len(key) >= 2:
                r, c = _as_int(key[0]), _as_int(key[1])
            else:
                continue
            d = _as_int(val)
            if r is not None and c is not None and d is not None and 0 <= r < N and 0 <= c < N:
                grid[r][c] = d if d >= 0 else INF
                found = True
        return grid if found else None
    if not isinstance(v, (list, tuple)) or len(v) != N:
        return None
    grid = []
    for row in v:
        if not isinstance(row, (list, tuple)) or len(row) != N:
            return None
        out = []
        for d in row:
            val = _as_int(d)
            out.append(INF if val is None or val < 0 else val)
        grid.append(out)
    return grid


def _first_required_edge(field, observed):
    """Find an edge the inferred model must block, or ``'fail'``."""
    for r in range(N):
        for c in range(N):
            u = r * N + c
            if c + 1 < N:
                v = u + 1
                du, dv = field[u], field[v]
                ou, ov = observed[u], observed[v]
                if du < INF and dv < INF and abs(du - dv) == 1:
                    if ou >= INF or ov >= INF or abs(ou - ov) != 1:
                        return u, v
                elif ou < INF and ov < INF and abs(ou - ov) == 1:
                    if du >= INF or dv >= INF or abs(du - dv) != 1:
                        return "fail"
            if r + 1 < N:
                v = u + N
                du, dv = field[u], field[v]
                ou, ov = observed[u], observed[v]
                if du < INF and dv < INF and abs(du - dv) == 1:
                    if ou >= INF or ov >= INF or abs(ou - ov) != 1:
                        return u, v
                elif ou < INF and ov < INF and abs(ou - ov) == 1:
                    if du >= INF or dv >= INF or abs(du - dv) != 1:
                        return "fail"
    return None


def _walls_for_edge(u, v):
    ru, cu = divmod(u, N)
    rv, cv = divmod(v, N)
    out = []
    if cv == cu:  # vertical edge
        r = min(ru, rv)
        out.append(_wall_id(0, r, cu))
        if cu > 0:
            out.append(_wall_id(0, r, cu - 1))
    else:         # horizontal edge
        c = min(cu, cv)
        out.append(_wall_id(1, ru, c))
        if ru > 0:
            out.append(_wall_id(1, ru - 1, c))
    return out


def _infer_walls(goal_me, goal_op, pos_me, pos_op, wl_me, wl_op,
                 legal_ids, distances, deadline):
    """Reconstruct a wall set whose distance field matches ``distances``.

    Returns a :class:`_State` or ``None``.  The reconstruction is deliberately
    conservative: only walls required to explain the observed distance field
    are added, so a partial-but-consistent model is used rather than a
    fabricated one.  Walls that do not affect our own shortest path stay
    unknown, which makes the opponent look at least as mobile as it really is.
    """
    if distances is None:
        return None
    limit = 20
    if wl_me is not None and wl_op is not None:
        limit = max(0, min(20, 20 - wl_me - wl_op))

    budget = [0]
    clock = time.perf_counter
    found = {}

    def solve(bd, br, hw, vw, used, count):
        budget[0] += 1
        if budget[0] > 4000 or ((budget[0] & 31) == 0 and clock() > deadline):
            return False
        field = _dist_field(goal_me, bd, br)
        if field == distances:
            found["walls"] = used
            return True
        if count >= limit:
            return False
        edge = _first_required_edge(field, distances)
        if edge is None or edge == "fail":
            return False
        for wid in _walls_for_edge(edge[0], edge[1]):
            if wid in used:
                continue
            ori, idx = _wall_parts(wid)
            if legal_ids is not None and wid in legal_ids:
                # Walls already on the board are absent from the legal list.
                continue
            if not _wall_geom_ok(ori, idx, bd, br, hw, vw):
                continue
            if ori == 0:
                nbd, nbr = bd | H_BD[idx], br
                nhw, nvw = hw | (1 << idx), vw
            else:
                nbd, nbr = bd, br | V_BR[idx]
                nhw, nvw = hw, vw | (1 << idx)
            if (_dist_to(goal_me, pos_me, nbd, nbr) >= INF
                    or _dist_to(goal_op, pos_op, nbd, nbr) >= INF):
                continue
            if solve(nbd, nbr, nhw, nvw, used + (wid,), count + 1):
                return True
        return False

    if not solve(0, 0, 0, 0, (), 0):
        return None
    st = _State(pos_me, pos_op, goal_me, goal_op,
                max(0, wl_me if wl_me is not None else 10),
                max(0, wl_op if wl_op is not None else 10), 0)
    for wid in found["walls"]:
        ori, idx = _wall_parts(wid)
        st.add_wall(ori, idx)
    if _dist_field(goal_me, st.bd, st.br) != distances:
        return None
    return st


def _parse_observation(obs, deadline):
    """Normalise an arena observation into engine state plus legal actions."""
    if not isinstance(obs, dict):
        return None

    size = None
    for k in ("board_size", "size", "rows", "board_rows"):
        if k in obs:
            size = _as_int(obs[k])
            if size is not None:
                break
    cols = size
    for k in ("board_cols", "cols", "columns"):
        if k in obs:
            cols = _as_int(obs[k])
            break
    if size is None:
        size = 9
    if cols is None:
        cols = size
    supported = size == N and cols == N

    you = obs.get("you")
    opp = obs.get("opponent", obs.get("opp"))
    if not isinstance(you, dict):
        you = obs.get("self", obs.get("player"))
    if not isinstance(opp, dict):
        opp = obs.get("other")

    players = obs.get("players")
    current = _as_int(obs.get("current_player", obs.get("player_index")))
    if isinstance(players, (list, tuple)) and len(players) == 2 and isinstance(players[0], dict):
        if current is None:
            current = 0
        you = players[current % 2]
        opp = players[(current + 1) % 2]

    pos_me = _player_position(you) if isinstance(you, dict) else None
    pos_op = _player_position(opp) if isinstance(opp, dict) else None
    if pos_me is None:
        for k in ("position", "my_position", "pawn"):
            if k in obs:
                pos_me = _as_rc(obs[k])
                if pos_me is not None:
                    break
    if pos_op is None:
        for k in ("opponent_position", "opp_position"):
            if k in obs:
                pos_op = _as_rc(obs[k])
                if pos_op is not None:
                    break
    if pos_me is None or pos_op is None:
        return None

    goal_me = _player_goal(you, pos_me, None)
    if isinstance(opp, dict) and any(k in opp for k in ("goal_row", "goal", "goalrow")):
        goal_op = _player_goal(opp, pos_op, None)
    else:
        goal_op = 8 if goal_me == 0 else 0
    if goal_me == goal_op:
        goal_op = 8 if goal_me == 0 else 0

    wl_me = _player_walls(you) if isinstance(you, dict) else None
    wl_op = _player_walls(opp) if isinstance(opp, dict) else None
    observed_counts = obs.get("walls_remaining")
    if isinstance(observed_counts, (list, tuple)) and len(observed_counts) == 2 and current is not None:
        a = _as_int(observed_counts[current % 2])
        b = _as_int(observed_counts[(current + 1) % 2])
        if a is not None:
            wl_me = a
        if b is not None:
            wl_op = b

    legal_moves = None
    raw_moves = obs.get("legal_moves")
    if isinstance(raw_moves, (list, tuple)):
        legal_moves = []
        for mv in raw_moves:
            cell = _norm_move(mv)
            if cell is not None and cell not in legal_moves:
                legal_moves.append(cell)

    legal_walls = None
    legal_ids = None
    raw_walls = obs.get("legal_walls")
    if isinstance(raw_walls, (list, tuple)):
        legal_walls = []
        legal_ids = set()
        for w in raw_walls:
            nw = _norm_wall(w)
            if nw is not None:
                wid = _wall_id(nw[0], nw[1], nw[2])
                if wid not in legal_ids:
                    legal_ids.add(wid)
                    legal_walls.append(nw)

    distances = _norm_distances(obs.get("distances"))

    if not supported:
        return {
            "state": None,
            "legal_moves": legal_moves,
            "legal_walls": legal_walls,
            "allowed": legal_ids,
            "distances": distances,
            "goal_me": goal_me,
            "search_ok": False,
            "root_moves": legal_moves,
        }

    pos_me_i = pos_me[0] * N + pos_me[1]
    pos_op_i = pos_op[0] * N + pos_op[1]
    if pos_me_i == pos_op_i:
        return None

    walls = _extract_walls(obs)
    search_ok = False
    if walls is not None:
        if wl_me is None or wl_op is None:
            total = len(walls)
            wl_me = 10 - total // 2 if wl_me is None else wl_me
            wl_op = 10 - (total - total // 2) if wl_op is None else wl_op
        state = state_from_walls(
            walls, pos_me, pos_op, goal_me, goal_op,
            max(0, wl_me), max(0, wl_op), 0,
        )
        search_ok = True
    else:
        state = None
        if legal_ids is not None:
            inferred = _infer_walls(
                goal_me, goal_op, pos_me_i, pos_op_i,
                wl_me, wl_op, legal_ids, distances, deadline,
            )
            if inferred is not None:
                state = inferred
                search_ok = True

    root_moves = legal_moves
    if root_moves is None and state is not None:
        root_moves = _legal_moves(pos_me_i, pos_op_i, state.bd, state.br)

    return {
        "state": state,
        "legal_moves": legal_moves,
        "legal_walls": legal_walls,
        "allowed": legal_ids,
        "distances": distances,
        "goal_me": goal_me,
        "search_ok": search_ok,
        "root_moves": root_moves,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _move_action(cell):
    return {"type": "move", "to": [cell // N, cell % N]}


def _wall_action(wid):
    ori, idx = _wall_parts(wid)
    return {
        "type": "wall",
        "orientation": "H" if ori == 0 else "V",
        "row": idx >> 3,
        "col": idx & 7,
    }


def _safe_action(obs, parsed=None):
    """Best purely observation-driven legal action; never raises."""
    moves = None
    distances = None
    allowed_walls = None
    if isinstance(obs, dict):
        raw = obs.get("legal_moves")
        if isinstance(raw, (list, tuple)):
            moves = []
            for mv in raw:
                cell = _norm_move(mv)
                if cell is not None and cell not in moves:
                    moves.append(cell)
        distances = _norm_distances(obs.get("distances"))
    if parsed is not None:
        if moves is None:
            moves = parsed.get("legal_moves")
        if distances is None:
            distances = parsed.get("distances")
        allowed_walls = parsed.get("allowed")
        if moves is None and parsed.get("state") is not None:
            st = parsed["state"]
            moves = _legal_moves(st.pos[0], st.pos[1], st.bd, st.br)
    if not moves:
        return {"type": "pass"}
    if distances is not None:
        best = min(moves, key=lambda m: (distances[m // N][m % N], m))
    else:
        best = moves[0]
    return _move_action(best)


def _immediate_win(parsed):
    """Return a winning move straight from the observation, if one exists."""
    moves = parsed.get("root_moves") or []
    distances = parsed.get("distances")
    goal = parsed.get("goal_me")
    for cell in moves:
        if goal is not None and cell // N == goal:
            return cell
        if distances is not None and distances[cell // N][cell % N] == 0:
            return cell
    return None


def _validate(action, parsed):
    """Ensure a search action is inside the observation's legal sets."""
    if action is None:
        return None
    if action < WALL_BASE:
        legal = parsed.get("legal_moves")
        if legal is not None and action not in legal:
            return None
        return action
    allowed = parsed.get("allowed")
    if allowed is not None and action not in allowed:
        return None
    return action


def _choose(observation, budget, max_depth, want_info, max_nodes=NODE_BUDGET):
    """Parse, search, validate and format one action."""
    t0 = time.perf_counter()
    deadline = t0 + max(0.0, budget)
    parse_deadline = min(deadline, t0 + PARSE_BUDGET)
    info = {"source": "pass", "score": 0.0, "depth": 0, "nodes": 0}

    parsed = None
    try:
        parsed = _parse_observation(observation, parse_deadline)
    except Exception:
        parsed = None

    if parsed is None:
        action = _safe_action(observation)
        info["source"] = "fallback"
        return (action, info) if want_info else action

    win_cell = _immediate_win(parsed)
    if win_cell is not None:
        info.update(source="win", score=float(WIN), depth=1)
        action = _move_action(win_cell)
        return (action, info) if want_info else action

    action = None
    if parsed.get("search_ok") and parsed.get("state") is not None and budget > 0:
        try:
            _reset_memory(int(observation.get("turn", 0)))
        except (TypeError, ValueError):
            _reset_memory(0)
        state = parsed["state"]
        root_moves = parsed.get("root_moves") or []
        try:
            act, score, depth, nodes = _search(
                state, [m for m in root_moves], parsed.get("allowed"),
                max(t0, deadline - 0.01), max_depth, max_nodes,
            )
        except Exception:
            act, score, depth, nodes = None, 0.0, 0, 0
        act = _validate(act, parsed)
        if act is not None:
            action = _wall_action(act) if act >= WALL_BASE else _move_action(act)
            if act < WALL_BASE:
                key = (act, state.pos[1], state.hw, state.vw)
                _ROOT_VISITS[key] = _ROOT_VISITS.get(key, 0) + 1
            info.update(source="search", score=float(score), depth=depth, nodes=nodes)

    if action is None:
        action = _safe_action(observation, parsed)
        info["source"] = "fallback"
    return (action, info) if want_info else action


def choose_action(observation):
    """Return the agent's action for one arena observation.

    The function is deterministic, never raises, and always returns an action
    that appears in the observation's ``legal_moves`` / ``legal_walls`` when
    those lists are provided.
    """
    try:
        return _choose(observation, TIME_BUDGET, MAX_DEPTH, False)
    except Exception:
        try:
            return _safe_action(observation)
        except Exception:
            return {"type": "pass"}


def choose_action_with_info(observation, budget=None, max_depth=None,
                            max_nodes=None):
    """Development helper: like :func:`choose_action` but returns diagnostics.

    ``budget`` (seconds), ``max_depth`` and ``max_nodes`` default to the module
    constants and exist so tests and the local tournament can run the same code
    quickly.
    """
    try:
        return _choose(
            observation,
            TIME_BUDGET if budget is None else budget,
            MAX_DEPTH if max_depth is None else max_depth,
            True,
            NODE_BUDGET if max_nodes is None else max_nodes,
        )
    except Exception:
        return _safe_action(observation), {
            "source": "fallback", "score": 0.0, "depth": 0, "nodes": 0,
        }


if __name__ == "__main__":  # pragma: no cover - manual benchmark helper
    import random

    st = _State(8 * N + 4, 0 * N + 4, 0, 8)
    rng = random.Random(1)
    start = time.perf_counter()
    count = 0
    while time.perf_counter() - start < 1.0:
        _dist_field(0, st.bd, st.br)
        count += 1
    print(f"dist_field: {count / (time.perf_counter() - start):.0f}/s")
    obs = {
        "legal_moves": [[7, 4], [8, 3], [8, 5]],
        "legal_walls": [
            {"orientation": o, "row": r, "col": c}
            for o in ("H", "V") for r in range(8) for c in range(8)
        ],
        "distances": [[abs(r - 0) + abs(c - 4) for c in range(9)] for r in range(9)],
        "you": {"position": [8, 4], "goal_row": 0, "walls_left": 10},
        "opponent": {"position": [0, 4], "goal_row": 8, "walls_left": 10},
        "turn": 0,
        "board_size": 9,
        "walls": {"horizontal": [], "vertical": []},
    }
    t0 = time.perf_counter()
    act, info = choose_action_with_info(obs)
    print(f"action={act} info={info} elapsed={time.perf_counter() - t0:.3f}s")
