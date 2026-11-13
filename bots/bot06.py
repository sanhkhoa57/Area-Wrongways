"""MSSV - Wrongway Arena Tournament Engine (Bitboard PVS + Full-DAG + 2-Wall Combo).

Algorithmic superiority over standard bitboard engines:
1. Bitboard Speed + Zobrist TT + Iterative Deepening PVS.
2. Full Shortest-Path DAG Wall Generator (fixes single-ray 'break' blindness).
3. Two-Wall Combo Discovery: Allows zero-loss corridor-squeezing & chain-extension
   setup walls so PVS naturally finds lethal multi-wall traps.
4. Pawn-Aware Leaf Evaluation: Models jump threats and nonlinear wall valuation.
5. Exact tree legality at inner plies (fixes the root 'allowed' set leakage bug).
"""

import time

# ---------------------------------------------------------------------------
# Constants & Bitboards
# ---------------------------------------------------------------------------

N = 9
CELLS = N * N
FULL = (1 << CELLS) - 1

ROW_MASK = tuple(sum(1 << (r * N + c) for c in range(N)) for r in range(N))
NOT_LEFT_COL = FULL
for _r in range(N):
    NOT_LEFT_COL &= ~(1 << (_r * N))
NOT_RIGHT_COL = FULL
for _r in range(N):
    NOT_RIGHT_COL &= ~(1 << (_r * N + N - 1))

H_BD = tuple((1 << ((i >> 3) * N + (i & 7))) | (1 << ((i >> 3) * N + (i & 7) + 1)) for i in range(64))
V_BR = tuple((1 << ((i >> 3) * N + (i & 7))) | (1 << (((i >> 3) + 1) * N + (i & 7))) for i in range(64))

WIN = 100000
MATE_BOUND = WIN // 2
INF = 999
MAX_SCORE = 10 ** 9

MAX_DEPTH = 12
TIME_BUDGET = 0.85
PARSE_BUDGET = 0.08
ASPIRATION = 60
LMR_MOVES = 3
LMR_WALLS = 2

WALL_BASE = 100
TT_LIMIT = 150000
NODE_MASK = 127
_DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))

# ---------------------------------------------------------------------------
# Zobrist Hashing
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

def _wall_id(ori, r, c):
    return WALL_BASE + (ori << 6) + (r << 3) + c

def _wall_parts(wid):
    code = wid - WALL_BASE
    return code >> 6, code & 63

# ---------------------------------------------------------------------------
# State & Memoized BFS
# ---------------------------------------------------------------------------

class _State:
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
        if ori == 0:
            self.bd |= H_BD[idx]
            self.hw |= 1 << idx
            self.hash ^= Z_HW[idx]
        else:
            self.br |= V_BR[idx]
            self.vw |= 1 << idx
            self.hash ^= Z_VW[idx]

def _dist_field(goal_row, bd, br):
    field = [INF] * CELLS
    base = goal_row * N
    for c in range(N):
        field[base + c] = 0
    seen = front = ROW_MASK[goal_row]
    d = 0
    while True:
        nxt = (
            ((front >> 9) & ~bd)
            | ((front << 9) & ~(bd << 9))
            | (((front & NOT_LEFT_COL) >> 1) & ~br)
            | (((front & NOT_RIGHT_COL) << 1) & ~(br << 1))
        )
        nxt &= ~seen & FULL
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

_FIELD_CACHE = {}
_FIELD_LIMIT = 80000
_GAME_TURN = -1

def _reset_memory(turn):
    global _GAME_TURN
    if turn < _GAME_TURN:
        _FIELD_CACHE.clear()
    _GAME_TURN = turn

def _field(goal_row, bd, br):
    key = (goal_row, bd, br)
    got = _FIELD_CACHE.get(key)
    if got is None:
        got = _dist_field(goal_row, bd, br)
        if len(_FIELD_CACHE) >= _FIELD_LIMIT:
            _FIELD_CACHE.clear()
        _FIELD_CACHE[key] = got
    return got

def _dist_at(goal_row, cell, bd, br):
    return _field(goal_row, bd, br)[cell]

# ---------------------------------------------------------------------------
# Move Generation & Actions
# ---------------------------------------------------------------------------

def _edge_blocked(r, c, nr, nc, bd, br):
    if nr == r + 1: return (bd >> (r * N + c)) & 1
    if nr == r - 1: return (bd >> ((r - 1) * N + c)) & 1
    if nc == c + 1: return (br >> (r * N + c)) & 1
    return (br >> (r * N + c - 1)) & 1

def _legal_moves(me, op, bd, br):
    r, c = divmod(me, N)
    out = []
    for dr, dc in _DIRS:
        nr, nc = r + dr, c + dc
        if nr < 0 or nr >= N or nc < 0 or nc >= N: continue
        if _edge_blocked(r, c, nr, nc, bd, br): continue
        target = nr * N + nc
        if target != op:
            out.append(target)
            continue
        jr, jc = nr + dr, nc + dc
        if 0 <= jr < N and 0 <= jc < N and not _edge_blocked(nr, nc, jr, jc, bd, br):
            out.append(jr * N + jc)
            continue
        for sr, sc in ((nr + dc, nc + dr), (nr - dc, nc - dr)):
            if 0 <= sr < N and 0 <= sc < N:
                if not _edge_blocked(nr, nc, sr, sc, bd, br):
                    out.append(sr * N + sc)
    return out

def _wall_geom_ok(ori, idx, bd, br, hw, vw):
    bit = 1 << idx
    if ori == 0: return not (hw & bit) and not (bd & H_BD[idx]) and not (vw & bit)
    return not (vw & bit) and not (br & V_BR[idx]) and not (hw & bit)

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
# Advanced Wall Generation (Full DAG + Chain Traps)
# ---------------------------------------------------------------------------

def _path_walls(state, max_steps, who):
    """Full DAG Shortest-Path Traversal (Fixes phat_bot_fixed's single-ray blindness)."""
    bd, br = state.bd, state.br
    field = _field(state.goal[who], bd, br)
    start = state.pos[who]
    d0 = field[start]
    if d0 <= 0 or d0 >= INF:
        return []
    
    out = []
    seen = set()
    frontier = [start]
    visited = 1 << start
    steps = 0
    
    while frontier and steps < max_steps:
        nxt_front = []
        for u in frontier:
            d = field[u]
            if d <= 0: continue
            r, c = divmod(u, N)
            for dr, dc in _DIRS:
                nr, nc = r + dr, c + dc
                if 0 <= nr < N and 0 <= nc < N and not _edge_blocked(r, c, nr, nc, bd, br):
                    v = nr * N + nc
                    if field[v] == d - 1:
                        if dr == -1: cands = ((0, r-1, c), (0, r-1, c-1))
                        elif dr == 1: cands = ((0, r, c), (0, r, c-1))
                        elif dc == -1: cands = ((1, r, c-1), (1, r-1, c-1))
                        else: cands = ((1, r, c), (1, r-1, c))

                        for ori, wr, wc in cands:
                            if 0 <= wr <= 7 and 0 <= wc <= 7:
                                wid = WALL_BASE + (ori << 6) + (wr << 3) + wc
                                if wid not in seen:
                                    seen.add(wid)
                                    out.append(wid)

                        if not (visited & (1 << v)):
                            visited |= (1 << v)
                            nxt_front.append(v)
        frontier = nxt_front
        steps += 1
    return out

def _score_walls(state, cand, allowed, max_take, deadline, my_path):
    """Evaluates walls, enables 2-Wall Setup Traps (Fixes phat_bot_fixed's strict filtering)."""
    side = state.side
    opp = side ^ 1
    me, op = state.pos[side], state.pos[opp]
    d_me = _dist_at(state.goal[side], me, state.bd, state.br)
    d_op = _dist_at(state.goal[opp], op, state.bd, state.br)
    scored = []
    clock = time.perf_counter

    for n, wid in enumerate(cand):
        if (n & 15) == 15 and clock() > deadline: break
        if allowed is not None and wid not in allowed: continue
        ori, code = _wall_parts(wid)
        if not _wall_geom_ok(ori, code, state.bd, state.br, state.hw, state.vw): continue

        if ori == 0: state.bd |= H_BD[code]
        else: state.br |= V_BR[code]

        n_op = _dist_at(state.goal[opp], op, state.bd, state.br)
        if n_op >= INF:
            n_me = INF
        elif wid in my_path:
            n_me = _dist_at(state.goal[side], me, state.bd, state.br)
        else:
            n_me = d_me

        if ori == 0: state.bd ^= H_BD[code]
        else: state.br ^= V_BR[code]

        if n_op >= INF or n_me >= INF: continue

        gain = (n_op - d_op) - (n_me - d_me)
        if gain > 0:
            scored.append((gain * 100 - (n_me - d_me) * 10, wid))
        elif gain == 0 and n_me == d_me:
            # Setup Trap: Find zero-loss walls that connect barriers to set up combos
            bit = 1 << code
            connected = False
            if ori == 0: 
                if (code & 7) == 0 or (code & 7) == 7: connected = True
                elif state.hw & ((bit >> 1) | (bit << 1)): connected = True
                elif state.vw & (bit | (bit >> 8) | (bit << 8) | (bit >> 7) | (bit << 9)): connected = True
            else:
                if (code >> 3) == 0 or (code >> 3) == 7: connected = True
                elif state.vw & ((bit >> 8) | (bit << 8)): connected = True
                elif state.hw & (bit | (bit >> 1) | (bit << 1) | (bit >> 9) | (bit << 7)): connected = True
            
            if connected:
                scored.append((50, wid)) # 2-Wall setup! Will be searched in PVS.

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [w for _, w in scored[:max_take]]

def _wall_candidates(state, depth, allowed, deadline):
    if state.wl[state.side] <= 0:
        return []
    take = 8 if depth >= 3 else 4
    tl = 8 if depth >= 3 else 5
    cand = _path_walls(state, tl, state.side ^ 1)
    my_path = set(_path_walls(state, CELLS, state.side))
    return _score_walls(state, cand, allowed, take, deadline, my_path)

# ---------------------------------------------------------------------------
# Pawn-Aware Evaluation
# ---------------------------------------------------------------------------

def _evaluate(state, d_me, d_op):
    if d_me >= INF: return -WIN + 4000
    if d_op >= INF: return WIN - 4000
    if d_me == 0: return WIN - 1000
    if d_op == 0: return -WIN + 1000
    
    side = state.side
    opp = side ^ 1
    w_me = state.wl[side]
    w_op = state.wl[opp]

    score = 42 + 100 * (d_op - d_me) + 196 * (w_me - w_op) + 30 * (min(w_me, d_op) - min(w_op, d_me))

    # Tactical Jump Detection
    me_pos, op_pos = state.pos[side], state.pos[opp]
    mr, mc = divmod(me_pos, N)
    orr, oc = divmod(op_pos, N)
    if abs(mr - orr) + abs(mc - oc) == 1 and not _edge_blocked(mr, mc, orr, oc, state.bd, state.br):
        for mv in _legal_moves(me_pos, op_pos, state.bd, state.br):
            if _dist_at(state.goal[side], mv, state.bd, state.br) <= d_me - 2:
                score += 85
                break

    if w_me == 0 and w_op == 0:
        score += 646 if d_me <= d_op else -646
    return score

# ---------------------------------------------------------------------------
# PVS Search
# ---------------------------------------------------------------------------

class _Timeout(Exception): pass

class _Ctx:
    __slots__ = ("deadline", "nodes", "tt", "killers", "history", "clock", "allowed")
    def __init__(self, deadline, allowed=None):
        self.deadline = deadline
        self.nodes = 0
        self.tt = {}
        self.killers = [[-1, -1] for _ in range(64)]
        self.history = [0] * 128
        self.clock = time.perf_counter
        self.allowed = allowed

def _negamax(state, depth, alpha, beta, ply, ctx):
    ctx.nodes += 1
    if (ctx.nodes & NODE_MASK) == 0 and ctx.clock() > ctx.deadline:
        raise _Timeout

    side = state.side
    opp = side ^ 1
    me, op = state.pos[side], state.pos[opp]

    key = (state.hash, state.wl[0], state.wl[1])
    ent = ctx.tt.get(key)
    tt_act = -1
    if ent is not None:
        ed, esc, eflag, eact = ent
        tt_act = eact
        if ed >= depth:
            if esc > MATE_BOUND: esc -= ply
            elif esc < -MATE_BOUND: esc += ply
            if eflag == 0: return esc
            if eflag == 1 and esc >= beta: return esc
            if eflag == 2 and esc <= alpha: return esc

    f_me = _field(state.goal[side], state.bd, state.br)
    f_op = _field(state.goal[opp], state.bd, state.br)
    d_me, d_op = f_me[me], f_op[op]

    if depth <= 0: return _evaluate(state, d_me, d_op)

    moves = _legal_moves(me, op, state.bd, state.br)
    moves.sort(key=lambda m: (f_me[m], m))
    if tt_act >= 0 and tt_act < WALL_BASE and tt_act in moves:
        moves.remove(tt_act)
        moves.insert(0, tt_act)

    # VITAL FIX: Pass allowed=None to inner plies to enable opponent wall simulation!
    walls = _wall_candidates(state, depth, None, ctx.deadline)
    if tt_act >= WALL_BASE and tt_act in walls:
        walls.remove(tt_act)
        walls.insert(0, tt_act)

    killer = ctx.killers[ply] if ply < len(ctx.killers) else (-1, -1)
    if walls and killer[0] in walls:
        walls.remove(killer[0])
        walls.insert(0, killer[0])
    if len(walls) > 1 and killer[1] in walls:
        walls.remove(killer[1])
        walls.insert(1, killer[1])

    first = True
    best = -MAX_SCORE
    best_act = -1
    alpha0 = alpha
    late = 0

    for act in moves:
        if act // N == state.goal[side]:
            return WIN - ply
        undo = _do_action(state, act)
        if first:
            score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
            first = False
        elif depth >= 3 and late >= LMR_MOVES:
            score = -_negamax(state, depth - 2, -alpha - 1, -alpha, ply + 1, ctx)
            if score > alpha:
                score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
        else:
            score = -_negamax(state, depth - 1, -alpha - 1, -alpha, ply + 1, ctx)
            if alpha < score < beta:
                score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
        _undo_action(state, undo)
        late += 1
        if score > best:
            best = score
            best_act = act
            if score > alpha:
                alpha = score
                if alpha >= beta: break

    if alpha < beta and state.wl[side] > 0:
        late = 0
        for act in walls:
            undo = _do_action(state, act)
            if depth >= 4 and late >= LMR_WALLS:
                score = -_negamax(state, depth - 2, -alpha - 1, -alpha, ply + 1, ctx)
                if score > alpha:
                    score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
            else:
                score = -_negamax(state, depth - 1, -alpha - 1, -alpha, ply + 1, ctx)
                if alpha < score < beta:
                    score = -_negamax(state, depth - 1, -beta, -alpha, ply + 1, ctx)
            _undo_action(state, undo)
            late += 1
            if score > best:
                best = score
                best_act = act
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        slot = (act - WALL_BASE) & 127
                        ctx.history[slot] += depth * depth
                        if ply < len(ctx.killers):
                            ks = ctx.killers[ply]
                            if ks[0] != act:
                                ks[1] = ks[0]
                                ks[0] = act
                        break

    if best_act == -1: return _evaluate(state, d_me, d_op)

    flag = 2 if best <= alpha0 else (1 if best >= beta else 0)
    stored = best + ply if best > MATE_BOUND else (best - ply if best < -MATE_BOUND else best)
    old = ctx.tt.get(key)
    if old is None or depth >= old[0]:
        ctx.tt[key] = (depth, stored, flag, best_act)
        if len(ctx.tt) > TT_LIMIT: ctx.tt.clear()
    return best

def _root_pass(state, ctx, ordered, depth, alpha, beta, scores):
    best, best_act, first = -MAX_SCORE, -1, True
    for act in ordered:
        if ctx.clock() > ctx.deadline: raise _Timeout
        if act < WALL_BASE and act // N == state.goal[state.side]:
            score = WIN - 1
        else:
            undo = _do_action(state, act)
            try:
                if first:
                    score = -_negamax(state, depth - 1, -beta, -alpha, 1, ctx)
                    first = False
                else:
                    score = -_negamax(state, depth - 1, -alpha - 1, -alpha, 1, ctx)
                    if alpha < score < beta:
                        score = -_negamax(state, depth - 1, -beta, -alpha, 1, ctx)
            finally:
                _undo_action(state, undo)
        scores[act] = score
        if score > best:
            best, best_act = score, act
            if score > alpha: alpha = score
    return best, best_act

def _search(state, root_moves, allowed_walls, deadline, max_depth):
    ctx = _Ctx(deadline, allowed_walls)
    f_me = _field(state.goal[0], state.bd, state.br)
    moves = list(root_moves)
    moves.sort(key=lambda m: (f_me[m], m))
    
    walls = _wall_candidates(state, 99, ctx.allowed, deadline)
    actions = moves + walls
    if not actions: return None, 0.0, 0, 0

    scores, rank = {}, {a: i for i, a in enumerate(actions)}
    last_act, last_score, completed, prev = actions[0], 0.0, 0, 0.0

    for depth in range(1, max_depth + 1):
        alpha, beta = -MAX_SCORE, MAX_SCORE
        if depth >= 3 and abs(prev) <= MATE_BOUND:
            alpha, beta = prev - ASPIRATION, prev + ASPIRATION
        while True:
            ordered = actions if depth == 1 else sorted(actions, key=lambda a: (-scores.get(a, -MAX_SCORE), rank[a]))
            try:
                best, best_act = _root_pass(state, ctx, ordered, depth, alpha, beta, scores)
            except _Timeout:
                return last_act, last_score, completed, ctx.nodes
            if best_act == -1: return last_act, last_score, completed, ctx.nodes
            
            if best <= alpha and alpha > -MAX_SCORE:
                alpha = -MAX_SCORE; continue
            if best >= beta and beta < MAX_SCORE:
                beta = MAX_SCORE; continue
            break
        
        last_act, last_score, completed = best_act, best, depth
        actions.sort(key=lambda a: (-scores.get(a, -MAX_SCORE), rank[a]))
        for i, a in enumerate(actions): rank[a] = i
        if abs(best) > MATE_BOUND: break
        prev = best
    return last_act, last_score, completed, ctx.nodes

# ---------------------------------------------------------------------------
# Arena Adapters
# ---------------------------------------------------------------------------

def _as_int(v):
    try:
        if isinstance(v, bool): return None
        return int(v)
    except (TypeError, ValueError): return None

def _as_rc(v):
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        r, c = _as_int(v[0]), _as_int(v[1])
        return (r, c) if r is not None and c is not None else None
    if isinstance(v, dict):
        for k in ("to", "position", "pos", "cell"):
            if k in v:
                got = _as_rc(v[k])
                if got is not None: return got
        r, c = _as_int(v.get("row")), _as_int(v.get("col"))
        return (r, c) if r is not None and c is not None else None
    return None

def _norm_move(v):
    rc = _as_rc(v)
    return rc[0] * N + rc[1] if rc is not None and 0 <= rc[0] < N and 0 <= rc[1] < N else None

def _norm_wall(w):
    if isinstance(w, dict):
        ori = 0 if str(w.get("orientation", "H")).upper().startswith("H") else 1
        r, c = _as_int(w.get("row")), _as_int(w.get("col"))
        if r is not None and c is not None and 0 <= r <= 7 and 0 <= c <= 7:
            return ori, r, c
    return None

def _parse_observation(obs):
    if not isinstance(obs, dict): return None
    you, opp = obs.get("you", {}), obs.get("opponent", {})
    pos_me, pos_op = _as_rc(you.get("position")), _as_rc(opp.get("position"))
    if not pos_me or not pos_op: return None

    goal_me = _as_int(you.get("goal_row", 0))
    goal_op = _as_int(opp.get("goal_row", 8 if goal_me == 0 else 0))
    wl_me, wl_op = _as_int(you.get("walls_remaining", 10)), _as_int(opp.get("walls_remaining", 10))

    me_i, op_i = pos_me[0] * N + pos_me[1], pos_op[0] * N + pos_op[1]
    st = _State(me_i, op_i, goal_me, goal_op, max(0, wl_me), max(0, wl_op), 0)
    
    walls = obs.get("walls", {})
    if isinstance(walls, dict):
        for r, c in walls.get("horizontal", ()): st.add_wall(0, int(r) * 8 + int(c))
        for r, c in walls.get("vertical", ()): st.add_wall(1, int(r) * 8 + int(c))

    legal_moves = []
    for mv in obs.get("legal_moves", ()):
        cell = _norm_move(mv)
        if cell is not None and cell not in legal_moves:
            legal_moves.append(cell)

    allowed_walls = set()
    for w in obs.get("legal_walls", ()):
        nw = _norm_wall(w)
        if nw: allowed_walls.add(_wall_id(nw[0], nw[1], nw[2]))

    return {"state": st, "legal_moves": legal_moves, "allowed": allowed_walls, "goal_me": goal_me}

def _move_action(cell): return {"type": "move", "to": [cell // N, cell % N]}
def _wall_action(wid):
    ori, idx = _wall_parts(wid)
    return {"type": "wall", "orientation": "H" if ori == 0 else "V", "row": idx >> 3, "col": idx & 7}

def _fallback_action(obs):
    moves = obs.get("legal_moves", [])
    if moves:
        dist = obs.get("distances", [])
        def move_key(mv):
            r, c = int(mv[0]), int(mv[1])
            try: d = dist[r][c] if dist[r][c] >= 0 else INF
            except: d = INF
            return (d, abs(c - 4), r, c)
        best = min(moves, key=move_key)
        return {"type": "move", "to": [int(best[0]), int(best[1])]}
    walls = obs.get("legal_walls", [])
    if walls: return {"type": "wall", "orientation": walls[0]["orientation"], "row": int(walls[0]["row"]), "col": int(walls[0]["col"])}
    return {"type": "pass"}

def choose_action(observation):
    t0 = time.perf_counter()
    deadline = t0 + TIME_BUDGET
    try:
        parsed = _parse_observation(observation)
        if not parsed: return _fallback_action(observation)

        root_moves = parsed["legal_moves"]
        if not root_moves and not parsed["allowed"]: return {"type": "pass"}

        goal_me = parsed["goal_me"]
        for cell in root_moves:
            if cell // N == goal_me: return _move_action(cell)

        _reset_memory(int(observation.get("turn", 0)))
        state, allowed = parsed["state"], parsed["allowed"]

        act, _score, _depth, _nodes = _search(state, root_moves, allowed, max(t0, deadline - 0.01), MAX_DEPTH)
        
        if act is not None:
            if act < WALL_BASE and act in root_moves: return _move_action(act)
            if act >= WALL_BASE and act in allowed: return _wall_action(act)

        return _fallback_action(observation)
    except Exception:
        return _fallback_action(observation)