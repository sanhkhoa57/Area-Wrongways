"""Cinnamonroll Agent 
Strategy Overview:
1. Immediate Win Check: Instantly executes a winning move if goal row is reachable.
2. Minimax Search with Alpha-Beta Pruning: Evaluates alternating moves up to Depth 5,
   stopping early if time budget (TIME_BUDGET = 0.35s) is reached.
   Considers valid pawn moves (including straight/diagonal jumps) and up to K_WALLS candidate walls.
3. Tactical Trap Extensions: At root and shallow plies, detects two-wall traps and defense
   counter-walls via pair analysis.
4. Evaluation at Leaf Nodes: Score = (Opponent Steps Needed - Own Steps Needed)
   + W_RESERVE * (Own Remaining Walls - Opponent Remaining Walls) + Turn Adjustment.
   W_RESERVE = 2.0 enforces selective wall placement.

Pure standard library implementation. Safe against board resets.
Supports greedy fallback on error or timeout.
"""
import time

N = 9
CELLS = N * N
INF = 99
DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))
W_RESERVE = 2.0          # Wall reserve value weight (in step units)
TIME_BUDGET = 0.35       # Time budget per turn in seconds (limit is 1.0s)
SAFETY_MARGIN = 0.03

# Static Precomputed Tables 
ROW = [i // N for i in range(CELLS)]
NB = []
for _r in range(N):
    for _c in range(N):
        _a = _r * N + _c
        _l = []
        for _dr, _dc in DIRS:
            _nr, _nc = _r + _dr, _c + _dc
            if 0 <= _nr < N and 0 <= _nc < N:
                _b = _nr * N + _nc
                _l.append((_b, _a * CELLS + _b))
        NB.append(_l)


def _wall_keys(o, r, c):
    """Generate 1D edge key set blocked by placing wall at (o, r, c)."""
    if o == "H":
        pairs = (((r, c), (r + 1, c)), ((r, c + 1), (r + 1, c + 1)))
    else:
        pairs = (((r, c), (r, c + 1)), ((r + 1, c), (r + 1, c + 1)))
    ks = []
    for (r1, c1), (r2, c2) in pairs:
        a, b = r1 * N + c1, r2 * N + c2
        ks.append(a * CELLS + b)
        ks.append(b * CELLS + a)
    return frozenset(ks)


class _Timeout(Exception):
    """Custom exception raised when search deadline is exceeded."""
    pass


def _deadline_check(deadline=None):
    """Check if execution time has exceeded the deadline."""
    if deadline is not None and time.monotonic() >= deadline:
        raise _Timeout()


WALLKEYS = {}
BLOCKERS = {}
for _o in ("H", "V"):
    for _r in range(N - 1):
        for _c in range(N - 1):
            _ks = _wall_keys(_o, _r, _c)
            WALLKEYS[(_o, _r, _c)] = _ks
            for _k in _ks:
                BLOCKERS.setdefault(_k, []).append((_o, _r, _c))


# Graph & Pathfinding Helpers (BFS) 
def dist(start, goal_row, blocked, deadline=None):
    """Compute shortest distance from start cell index to goal_row using BFS."""
    if ROW[start] == goal_row:
        return 0
    seen = {start}
    frontier = [start]
    d = 0
    while frontier:
        _deadline_check(deadline)
        d += 1
        nxt = []
        for a in frontier:
            for b, k in NB[a]:
                if b not in seen and k not in blocked:
                    if ROW[b] == goal_row:
                        return d
                    seen.add(b)
                    nxt.append(b)
        frontier = nxt
    return INF


def _full_bfs(sources, blocked, deadline=None):
    """Multi-source BFS returning distance array from source cells to all cells."""
    d = [-1] * CELLS
    for s in sources:
        d[s] = 0
    frontier = list(sources)
    while frontier:
        _deadline_check(deadline)
        nxt = []
        for a in frontier:
            for b, k in NB[a]:
                if d[b] < 0 and k not in blocked:
                    d[b] = d[a] + 1
                    nxt.append(b)
        frontier = nxt
    return d


def wall_ok(o, r, c, H, V):
    """Check if a candidate wall overlaps or collides with existing walls."""
    if not (0 <= r < N - 1 and 0 <= c < N - 1):
        return False
    if o == "H":
        if (r, c - 1) in H or (r, c) in H or (r, c + 1) in H or (r, c) in V:
            return False
    else:
        if (r - 1, c) in V or (r, c) in V or (r + 1, c) in V or (r, c) in H:
            return False
    return True


def goal_cells(row):
    """Return list of cell indices belonging to the given goal row."""
    return [row * N + c for c in range(N)]


def pawn_moves(a, b, blocked):
    """Generate valid pawn movement destination cells from cell 'a' given opponent cell 'b'."""
    res = []
    r, c = divmod(a, N)
    for dr, dc in DIRS:
        nr, nc = r + dr, c + dc
        if not (0 <= nr < N and 0 <= nc < N):
            continue
        n = nr * N + nc
        if a * CELLS + n in blocked:
            continue
        if n != b:
            res.append(n)
            continue
        # Jump over opponent pawn
        fr, fc = nr + dr, nc + dc
        if 0 <= fr < N and 0 <= fc < N and n * CELLS + fr * N + fc not in blocked:
            res.append(fr * N + fc)
        else:
            # Diagonal jump if straight jump is blocked
            for sr_, sc_ in ((dc, dr), (-dc, -dr)):
                tr, tc = nr + sr_, nc + sc_
                if 0 <= tr < N and 0 <= tc < N and n * CELLS + tr * N + tc not in blocked:
                    res.append(tr * N + tc)
    return res


def steps_needed(a, b, dg, blocked):
    """Calculate exact steps needed to goal row considering pawn jumps at first step."""
    if dg[a] == 0:
        return 0
    if dg[a] < 0:
        return INF
    best = INF
    for d in pawn_moves(a, b, blocked):
        v = dg[d]
        if 0 <= v < best:
            best = v
    return best + 1 if best < INF else INF


# Tactical Trap Engine Constants 
DEPTH_MAX = 5
K_WALLS = 5
WIN = 1000.0
TACTICAL_MAX_PLY = 2
TACTICAL_MIN_DEPTH = 2
PAIR_MAX_STEPS = 3
PAIR_MIN_DETOUR = 3
PAIR_MAX_RESULTS = 4
PAIR_CANDIDATE_CAP = 10
TACTICAL_DEFENSE_MAX = 4
TACTICAL_OFFENSE_MAX = 2


class _Ctx:
    """Search context holding distance map caches, trap caches, and deadline."""
    pass


def _maps(ctx, bl):
    """Retrieve cached full BFS distance maps or compute them if missing."""
    r = ctx.maps.get(bl)
    if r is None:
        r = (_full_bfs(ctx.MG, bl, ctx.deadline),
             _full_bfs(ctx.OG, bl, ctx.deadline))
        ctx.maps[bl] = r
    return r


def _wall_candidates_on_path(pos, goal_row, bl, H, V, deadline,
                             max_steps=PAIR_MAX_STEPS):
    """Identify a bounded set of legal walls touching target's shortest paths."""
    dgoal = _full_bfs(goal_cells(goal_row), bl, deadline)
    original = dgoal[pos]
    if original <= 0 or original >= INF:
        return []
    from_pos = _full_bfs([pos], bl, deadline)
    candidates = set()
    for u in range(CELLS):
        _deadline_check(deadline)
        if from_pos[u] < 0 or from_pos[u] > max_steps:
            continue
        if dgoal[u] < 0 or from_pos[u] + dgoal[u] != original:
            continue
        for v, edge in NB[u]:
            if dgoal[v] != dgoal[u] - 1 or edge in bl:
                continue
            for wall in BLOCKERS.get(edge, ()):
                if wall_ok(wall[0], wall[1], wall[2], H, V):
                    candidates.add(wall)

    def rank(wall):
        orientation, row, col = wall
        distance = min(from_pos[row * N + col], from_pos[(row + 1) * N + col])
        return (distance if distance >= 0 else INF, orientation, row, col)

    return sorted(candidates, key=rank)[:PAIR_CANDIDATE_CAP]


def _find_wall_pairs(ctx, attacker_pos, target_pos, attacker_goal,
                     target_goal, bl, H, V):
    """Find two-wall plans that add at least PAIR_MIN_DETOUR steps to target's path."""
    cache_key = (bl, H, V, attacker_pos, target_pos, attacker_goal, target_goal)
    cached = ctx.pairs.get(cache_key)
    if cached is not None:
        return cached

    deadline = ctx.deadline
    original = dist(target_pos, target_goal, bl, deadline)
    if original >= INF:
        ctx.pairs[cache_key] = ()
        return ()
    first_walls = _wall_candidates_on_path(
        target_pos, target_goal, bl, H, V, deadline)
    pairs = []
    for wall1 in first_walls:
        _deadline_check(deadline)
        o1, r1, c1 = wall1
        blocked1 = bl | WALLKEYS[wall1]
        h1 = H | {(r1, c1)} if o1 == "H" else H
        v1 = V | {(r1, c1)} if o1 == "V" else V
        after1 = dist(target_pos, target_goal, blocked1, deadline)
        if after1 >= INF or dist(attacker_pos, attacker_goal, blocked1, deadline) >= INF:
            continue
        second_walls = _wall_candidates_on_path(
            target_pos, target_goal, blocked1, h1, v1, deadline)
        for wall2 in second_walls:
            _deadline_check(deadline)
            if wall2 == wall1:
                continue
            blocked2 = blocked1 | WALLKEYS[wall2]
            after2 = dist(target_pos, target_goal, blocked2, deadline)
            if after2 >= INF or dist(attacker_pos, attacker_goal, blocked2, deadline) >= INF:
                continue
            detour = after2 - original
            if detour >= PAIR_MIN_DETOUR:
                pairs.append((wall1, wall2, detour, after1 - original))

    pairs.sort(key=lambda pair: (-pair[2], -pair[3], pair[0], pair[1]))
    result = tuple(pairs[:PAIR_MAX_RESULTS])
    ctx.pairs[cache_key] = result
    return result


def _still_wall_action(ctx, st, who, wall):
    """Return a tactical wall action if it preserves own path and valid paths for both players."""
    mp, op, mw, ow, H, V, bl = st
    walls_left = mw if who == 0 else ow
    if walls_left <= 0:
        return None
    orientation, row, col = wall
    if not wall_ok(orientation, row, col, H, V):
        return None
    blocked = bl | WALLKEYS[wall]
    my_pos, other_pos = (mp, op) if who == 0 else (op, mp)
    my_goal, other_goal = (ctx.mg, ctx.og) if who == 0 else (ctx.og, ctx.mg)
    old_distance = dist(my_pos, my_goal, bl, ctx.deadline)
    if dist(my_pos, my_goal, blocked, ctx.deadline) > old_distance:
        return None
    if dist(other_pos, other_goal, blocked, ctx.deadline) >= INF:
        return None
    return ("w", wall, blocked)


def _tactical_wall_actions(ctx, st, who, ordinary_walls):
    """Generate tactical trap-counter walls first, followed by offensive trap openers."""
    mp, op, mw, ow, H, V, bl = st
    my_pos, other_pos = (mp, op) if who == 0 else (op, mp)
    my_goal, other_goal = (ctx.mg, ctx.og) if who == 0 else (ctx.og, ctx.mg)
    my_walls = mw if who == 0 else ow
    key = (bl, H, V, mp, op, mw, ow, who, tuple(sorted(ordinary_walls)))
    cached = ctx.tactical.get(key)
    if cached is not None:
        return cached

    result = []
    seen = set(ordinary_walls)

    # Defensive counter-walls have priority over offensive trap ideas
    if my_walls > 0:
        threats = _find_wall_pairs(
            ctx, other_pos, my_pos, other_goal, my_goal, bl, H, V)
        defense_score = {}
        for wall1, wall2, gain, first_gain in threats:
            _deadline_check(ctx.deadline)
            for component, value in ((wall1, gain), (wall2, max(0, gain - first_gain))):
                o, r, c = component
                conflicts = []
                if o == "H":
                    if c > 0:
                        conflicts.append(("H", r, c - 1))
                    if c < N - 2:
                        conflicts.append(("H", r, c + 1))
                    conflicts.append(("V", r, c))
                else:
                    if r > 0:
                        conflicts.append(("V", r - 1, c))
                    if r < N - 2:
                        conflicts.append(("V", r + 1, c))
                    conflicts.append(("H", r, c))
                for counter in conflicts:
                    if counter not in seen:
                        defense_score[counter] = max(defense_score.get(counter, -1), value)
        for wall, _score in sorted(
                defense_score.items(), key=lambda item: (-item[1], item[0])):
            _deadline_check(ctx.deadline)
            action = _still_wall_action(ctx, st, who, wall)
            if action is not None:
                result.append(action)
                seen.add(wall)
                if len(result) >= TACTICAL_DEFENSE_MAX:
                    break

    # Offensive trap starters require at least two walls remaining
    if my_walls >= 2:
        traps = _find_wall_pairs(
            ctx, my_pos, other_pos, my_goal, other_goal, bl, H, V)
        offense_score = {}
        for wall1, wall2, gain, _first_gain in traps:
            offense_score[wall1] = max(offense_score.get(wall1, -1), gain)
            offense_score[wall2] = max(offense_score.get(wall2, -1), gain)
        added = 0
        for wall, _score in sorted(
                offense_score.items(), key=lambda item: (-item[1], item[0])):
            _deadline_check(ctx.deadline)
            if wall in seen:
                continue
            action = _still_wall_action(ctx, st, who, wall)
            if action is not None:
                result.append(action)
                seen.add(wall)
                added += 1
                if added >= TACTICAL_OFFENSE_MAX:
                    break

    ctx.tactical[key] = result
    return result


def _static(ctx, st, who):
    """Static heuristic evaluation function for leaf nodes."""
    mp, op, mw, ow, H, V, bl = st
    dgm, dgo = _maps(ctx, bl)
    M = steps_needed(mp, op, dgm, bl)
    O = steps_needed(op, mp, dgo, bl)
    return (O - M) + W_RESERVE * (mw - ow) + (0.5 if who == 0 else -0.5)


def _gen(ctx, st, who, k_walls, ply=99, depth=0):
    """Generate filtered and ranked move and wall candidate actions."""
    mp, op, mw, ow, H, V, bl = st
    walls_left = mw if who == 0 else ow
    include_tactics = ply <= TACTICAL_MAX_PLY and depth >= TACTICAL_MIN_DEPTH
    inventory_key = (mw, ow) if include_tactics else (walls_left > 0)
    key = (bl, mp, op, who, inventory_key, k_walls, include_tactics)
    r = ctx.gen.get(key)
    if r is not None:
        return r
    _deadline_check(ctx.deadline)
    dgm, dgo = _maps(ctx, bl)
    if who == 0:
        pos, other, dgs = mp, op, dgm
        tpos, tgoal, dgt, opos, ogoal = op, ctx.og, dgo, mp, ctx.mg
    else:
        pos, other, dgs = op, mp, dgo
        tpos, tgoal, dgt, opos, ogoal = mp, ctx.mg, dgm, op, ctx.og
    acts = []
    dests = sorted(pawn_moves(pos, other, bl), key=lambda d: dgs[d] if dgs[d] >= 0 else INF)
    for d in dests:
        acts.append(("m", d))
    if walls_left > 0 and k_walls > 0:
        D = dgt[tpos]
        if D > 0:
            df = _full_bfs([tpos], bl, ctx.deadline)
            cands = set()
            for u in range(CELLS):
                _deadline_check(ctx.deadline)
                if df[u] >= 0 and dgt[u] >= 0 and df[u] + dgt[u] == D:
                    for v, kk in NB[u]:
                        if dgt[v] == dgt[u] - 1 and kk not in bl:
                            for w in BLOCKERS.get(kk, ()):
                                cands.add(w)
            tr, tc = divmod(tpos, N)
            scored = []
            for w in cands:
                _deadline_check(ctx.deadline)
                o, r_, c_ = w
                if not wall_ok(o, r_, c_, H, V):
                    continue
                nb = bl | WALLKEYS[w]
                d = dist(tpos, tgoal, nb, ctx.deadline)
                if D < d < INF:
                    scored.append((-(d - D), abs(r_ + 0.5 - tr) + abs(c_ + 0.5 - tc), w, nb))
            scored.sort(key=lambda x: (x[0], x[1], x[2]))
            n = 0
            for ng, _, w, nb in scored:
                _deadline_check(ctx.deadline)
                if dist(opos, ogoal, nb, ctx.deadline) < INF:
                    acts.append(("w", w, nb))
                    n += 1
                    if n >= k_walls:
                        break
    if include_tactics and walls_left > 0:
        ordinary_walls = {act[1] for act in acts if act[0] == "w"}
        acts.extend(_tactical_wall_actions(ctx, st, who, ordinary_walls))
    ctx.gen[key] = acts
    return acts


def _apply(st, who, act):
    """Apply an action to state tuple and return (new_state, is_win)."""
    mp, op, mw, ow, H, V, bl = st
    if act[0] == "m":
        d = act[1]
        if who == 0:
            return (d, op, mw, ow, H, V, bl), False
        return (mp, d, mw, ow, H, V, bl), False
    o, r, c = act[1]
    nb = act[2]
    H2, V2 = (H | {(r, c)}, V) if o == "H" else (H, V | {(r, c)})
    if who == 0:
        return (mp, op, mw - 1, ow, H2, V2, nb), False
    return (mp, op, mw, ow - 1, H2, V2, nb), False


def _wins(ctx, st, who, act):
    """Check if action reaches the goal row immediately."""
    if act[0] != "m":
        return False
    d = act[1]
    return ROW[d] == (ctx.mg if who == 0 else ctx.og)


def _ab(ctx, st, who, depth, alpha, beta, ply=1):
    """Alpha-Beta Minimax search implementation."""
    _deadline_check(ctx.deadline)
    if depth == 0:
        return _static(ctx, st, who)
    acts = _gen(ctx, st, who, K_WALLS, ply, depth)
    if who == 0:
        best = -1e9
        for a in acts:
            if _wins(ctx, st, who, a):
                v = WIN + depth
            else:
                ch, _ = _apply(st, who, a)
                v = _ab(ctx, ch, 1, depth - 1, alpha, beta, ply + 1)
            if v > best:
                best = v
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        return best
    best = 1e9
    for a in acts:
        if _wins(ctx, st, who, a):
            v = -WIN - depth
        else:
            ch, _ = _apply(st, who, a)
            v = _ab(ctx, ch, 0, depth - 1, alpha, beta, ply + 1)
        if v < best:
            best = v
        if best < beta:
            beta = best
        if alpha >= beta:
            break
    return best


def _fallback(observation):
    """Greedy fallback movement strategy if search times out or errors."""
    moves = observation.get("legal_moves") or []
    if not moves:
        return {"type": "pass"}
    d = observation["distances"]
    best = min(moves, key=lambda m: d[m[0]][m[1]] if d[m[0]][m[1]] >= 0 else 999)
    return {"type": "move", "to": list(best)}


def choose_action(observation):
    """Main entry point required by Wrongway Arena environment.

    Args:
        observation (dict): Environment observation dictionary.

    Returns:
        dict: Chosen action ('move', 'wall', or 'pass').
    """
    try:
        return _choose(observation)
    except Exception:
        return _fallback(observation)


def _blocked_from(walls):
    """Reconstruct edge blockage bitmask and sets from wall position dict."""
    H = frozenset(map(tuple, walls["horizontal"]))
    V = frozenset(map(tuple, walls["vertical"]))
    blocked = set()
    for r, c in H:
        blocked |= WALLKEYS[("H", r, c)]
    for r, c in V:
        blocked |= WALLKEYS[("V", r, c)]
    return H, V, frozenset(blocked)


def _choose(obs):
    """Execute iterative deepening Alpha-Beta search within time budget."""
    t0 = time.monotonic()
    moves = obs["legal_moves"]
    if not moves:
        return {"type": "pass"}
    you, opp = obs["you"], obs["opponent"]
    mg, og = you["goal_row"], opp["goal_row"]
    for m in moves:
        if m[0] == mg:
            return {"type": "move", "to": list(m)}
    mp = you["position"][0] * N + you["position"][1]
    op = opp["position"][0] * N + opp["position"][1]
    H, V, bl = _blocked_from(obs["walls"])
    ctx = _Ctx()
    ctx.mg, ctx.og = mg, og
    ctx.MG, ctx.OG = goal_cells(mg), goal_cells(og)
    ctx.maps, ctx.gen = {}, {}
    ctx.pairs, ctx.tactical = {}, {}
    ctx.deadline = t0 + TIME_BUDGET - SAFETY_MARGIN
    root = (mp, op, you["walls_remaining"], opp["walls_remaining"], H, V, bl)
    legal_walls = {(w["orientation"], w["row"], w["col"]) for w in obs["legal_walls"]}
    legal_moves = {m[0] * N + m[1] for m in moves}

    def root_acts(k, depth):
        acts = [a for a in _gen(ctx, root, 0, k, 0, depth)
                if (a[0] == "m" and a[1] in legal_moves) or (a[0] == "w" and a[1] in legal_walls)]
        return acts

    best = None
    order = None
    for depth in range(1, DEPTH_MAX + 1):
        try:
            acts = root_acts(K_WALLS, depth)
            if order is not None:
                rank = {id(a): i for i, a in enumerate(order)}
                acts = sorted(acts, key=lambda a: rank.get(id(a), 999))
            alpha, res = -1e9, []
            for a in acts:
                if _wins(ctx, root, 0, a):
                    return {"type": "move", "to": [a[1] // N, a[1] % N]}
                ch, _ = _apply(root, 0, a)
                v = _ab(ctx, ch, 1, depth - 1, alpha, 1e9, 1)
                res.append((v, a))
                if v > alpha:
                    alpha = v
                    cur = a
            best = cur
            order = [a for v, a in sorted(res, key=lambda x: -x[0])]
        except _Timeout:
            break
    if best is None:
        return _fallback(obs)
    if best[0] == "m":
        return {"type": "move", "to": [best[1] // N, best[1] % N]}
    o, r, c = best[1]
    return {"type": "wall", "orientation": o, "row": r, "col": c}
