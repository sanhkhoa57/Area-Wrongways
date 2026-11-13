import bisect
import random
import time
BOARD = 9
WIN_SCORE = 10000
SURE_SCORE = 1000
TIME_BUDGET = 0.5
MAX_DEPTH = 40
WALL_VALUE = 1.75
INNER_WALL_LIMIT = 14
JUMP_RANGE = 6
WALL_SLACK = 3
RETREAT_PENALTY = 1.0
NULL_MOVE_R = 2
LMR_AFTER = 3
LMR_TWO_AFTER = 7
NULL_WINDOW = 1e-3
EXACT, LOWER, UPPER = 0, 1, 2
_MEMORY = {"goals": None}
_LIMITS = {"tt": 300000, "cache": 20000, "walls": 50000}
_rng = random.Random(2026)
_ZOBRIST = {o: [_rng.getrandbits(64) for _ in range(BOARD * BOARD)] for o in "HV"}


class _Timeout(Exception):
    pass


def _wall_edges(orientation, key):
    if orientation == "H":
        return ((key, key + 9), (key + 1, key + 10))
    return ((key, key + 1), (key + 9, key + 10))


def _cut(adj, orientation, key):
    for a, b in _wall_edges(orientation, key):
        adj[a].remove(b)
        adj[b].remove(a)


def _join(adj, orientation, key):
    for a, b in _wall_edges(orientation, key):
        bisect.insort(adj[a], b)
        bisect.insort(adj[b], a)


def _build_adj(h_walls, v_walls):
    adj = []
    for i in range(BOARD * BOARD):
        row, col = divmod(i, BOARD)
        cells = []
        if row > 0:
            cells.append(i - 9)
        if col > 0:
            cells.append(i - 1)
        if col < BOARD - 1:
            cells.append(i + 1)
        if row < BOARD - 1:
            cells.append(i + 9)
        adj.append(cells)
    for key in h_walls:
        _cut(adj, "H", key)
    for key in v_walls:
        _cut(adj, "V", key)
    return adj


def _dist_map(goal_row, adj):
    dist = [-1] * (BOARD * BOARD)
    queue = list(range(goal_row * BOARD, goal_row * BOARD + BOARD))
    for i in queue:
        dist[i] = 0
    for i in queue:
        step = dist[i] + 1
        for j in adj[i]:
            if dist[j] < 0:
                dist[j] = step
                queue.append(j)
    return dist


def _pawn_moves(me, opp, adj):
    result = []
    for j in adj[me]:
        if j != opp:
            result.append(j)
            continue
        beyond = j + (j - me)
        around = adj[j]
        if beyond in around:
            result.append(beyond)
        else:
            for k in around:
                if k != me and k not in result:
                    result.append(k)
    return result


def _wall_fits(orientation, key, h_walls, v_walls):
    if orientation == "H":
        return (key not in h_walls and key - 1 not in h_walls
                and key + 1 not in h_walls and key not in v_walls)
    return (key not in v_walls and key - 9 not in v_walls
            and key + 9 not in v_walls and key not in h_walls)


def _walls_cutting(a, b):
    low = min(a, b)
    row, col = divmod(low, BOARD)
    walls = []
    if abs(a - b) == BOARD:
        if col <= 7:
            walls.append(("H", low))
        if col >= 1:
            walls.append(("H", low - 1))
    else:
        if row <= 7:
            walls.append(("V", low))
        if row >= 1:
            walls.append(("V", low - 9))
    return walls
_CUTS = [()] * (81 * 81)
for _a in range(81):
    for _b in (_a - 9, _a - 1, _a + 1, _a + 9):
        if 0 <= _b < 81 and (abs(_a - _b) == 9 or _a // 9 == _b // 9):
            _CUTS[_a * 81 + _b] = tuple(_walls_cutting(_a, _b))


def _candidate_walls(start, dist, adj, h_walls, v_walls):
    result = []
    seen = set()
    i = start
    while dist[i] > 0:
        following = None
        for j in adj[i]:
            if dist[j] != dist[i] - 1:
                continue
            for wall in _CUTS[i * 81 + j]:
                if wall not in seen:
                    seen.add(wall)
                    if _wall_fits(wall[0], wall[1], h_walls, v_walls):
                        result.append(wall)
            if following is None:
                following = j
        i = following
    return result


def _evaluate(d_mover, d_other, walls_mover, walls_other, passed):
    if passed:
        if walls_other == 0 and d_mover <= d_other:
            return SURE_SCORE + (d_other - d_mover)
        if walls_mover == 0 and d_other < d_mover:
            return -SURE_SCORE + (d_other - d_mover)
    walls_mover = min(walls_mover, d_other + WALL_SLACK)
    walls_other = min(walls_other, d_mover + WALL_SLACK)
    return (d_other - d_mover) + 0.5 + WALL_VALUE * (walls_mover - walls_other)


def _pawns_close(pos):
    r0, c0 = divmod(pos[0], BOARD)
    r1, c1 = divmod(pos[1], BOARD)
    return abs(r0 - r1) + abs(c0 - c1) <= JUMP_RANGE


def _maps(ctx):
    key = ctx["hash"]
    maps = ctx["cache"].get(key)
    if maps is None:
        goals, adj = ctx["goals"], ctx["adj"]
        maps = (_dist_map(goals[0], adj), _dist_map(goals[1], adj))
        ctx["cache"][key] = maps
    return maps


def _map_unchanged(dist, adj, orientation, key):
    for a, b in _wall_edges(orientation, key):
        if dist[a] == dist[b]:
            continue
        far = a if dist[a] > dist[b] else b
        closer = dist[far] - 1
        for k in adj[far]:
            if dist[k] == closer:
                break
        else:
            return False
    return True


def _maps_after_wall(parent_maps, orientation, key, ctx):
    adj, goals = ctx["adj"], ctx["goals"]
    maps = []
    for player in (0, 1):
        dist = parent_maps[player]
        if not _map_unchanged(dist, adj, orientation, key):
            dist = _dist_map(goals[player], adj)
        maps.append(dist)
    return tuple(maps)


def _solve_race(adj, goals):
    n = BOARD * BOARD
    pred, left = {}, {}
    result = {}
    queue = []
    for a in range(n):
        if a // BOARD == goals[0]:
            continue
        for b in range(n):
            if b == a or b // BOARD == goals[1]:
                continue
            for mover in (0, 1):
                me, other = (a, b) if mover == 0 else (b, a)
                state = (a, b, mover)
                cells = _pawn_moves(me, other, adj)
                if any(c // BOARD == goals[mover] for c in cells):
                    result[state] = (1, 1)
                    queue.append(state)
                    continue
                nexts = [((c, b) if mover == 0 else (a, c)) + (1 - mover,)
                         for c in cells]
                nexts.append((a, b, 1 - mover))
                left[state] = len(nexts)
                for t in nexts:
                    pred.setdefault(t, []).append(state)
    for state in queue:
        res, plies = result[state]
        for p in pred.get(state, ()):
            if p in result:
                continue
            if res == -1:
                result[p] = (1, plies + 1)
                queue.append(p)
            else:
                left[p] -= 1
                if left[p] == 0:
                    result[p] = (-1, plies + 1)
                    queue.append(p)
    return result


def _race_action(observation, pos, goals, race, no_walls, dist_me):
    result = race.get((pos[0], pos[1], 0), (0, 0))
    if result[0] != 1 and not no_walls:
        return None
    options = [row * BOARD + col for row, col in observation["legal_moves"]] + [None]
    def rank(cell):
        state = (pos[0] if cell is None else cell, pos[1], 1)
        res, plies = race.get(state, (0, 0))
        progress = -dist_me[pos[0] if cell is None else cell]
        if res == -1:
            return (2, -plies, progress)
        if res == 0:
            return (1, 0, progress)
        return (0, plies, progress)
    best = max(options, key=rank)
    if best is None:
        return {"type": "pass"}
    return {"type": "move", "to": [best // BOARD, best % BOARD]}


def _move_children(pos, mover, dist_mover, ctx):
    moves = _pawn_moves(pos[mover], pos[1 - mover], ctx["adj"])
    moves.sort(key=lambda cell: dist_mover[cell] if dist_mover[cell] >= 0 else 99)
    return [("m", cell) for cell in moves]


def _wall_children(pos, h_walls, v_walls, mover, dist_other, ctx):
    key = (ctx["hash"], pos[1 - mover], mover)
    walls = ctx["walls"].get(key)
    if walls is None:
        walls = _candidate_walls(pos[1 - mover], dist_other, ctx["adj"], h_walls,
                                 v_walls)[:INNER_WALL_LIMIT]
        ctx["walls"][key] = walls
    history = ctx["history"]
    return [("w", wall) for wall in sorted(walls, key=lambda w: -history.get(w, 0))]


def _play(child, pos, walls_left, h_walls, v_walls, mover, depth, alpha, beta,
          ply, ctx):
    kind, value = child
    if kind == "n":
        score = _search(pos, walls_left, h_walls, v_walls, 1 - mover,
                        depth - 1, -beta, -alpha, ply + 1, ctx, False)
    elif kind == "m":
        new_pos = (value, pos[1]) if mover == 0 else (pos[0], value)
        score = _search(new_pos, walls_left, h_walls, v_walls, 1 - mover,
                        depth - 1, -beta, -alpha, ply + 1, ctx)
    else:
        orientation, key = value
        walls = h_walls if orientation == "H" else v_walls
        code = _ZOBRIST[orientation][key]
        walls.add(key)
        _cut(ctx["adj"], orientation, key)
        walls_left[mover] -= 1
        parent_maps = ctx["cache"].get(ctx["hash"])
        ctx["hash"] ^= code
        if parent_maps is not None and ctx["hash"] not in ctx["cache"]:
            ctx["cache"][ctx["hash"]] = _maps_after_wall(parent_maps, orientation,
                                                         key, ctx)
        try:
            score = _search(pos, walls_left, h_walls, v_walls, 1 - mover,
                            depth - 1, -beta, -alpha, ply + 1, ctx)
        finally:
            walls.discard(key)
            _join(ctx["adj"], orientation, key)
            walls_left[mover] += 1
            ctx["hash"] ^= code
    return None if score is None else -score


def _search(pos, walls_left, h_walls, v_walls, mover, depth, alpha, beta, ply, ctx,
            null_ok=True):
    ctx["nodes"] += 1
    if ctx["nodes"] % 32 == 0 and time.perf_counter() > ctx["deadline"]:
        raise _Timeout
    other = 1 - mover
    maps = _maps(ctx)
    dist_mover, dist_other = maps[mover], maps[other]
    d_mover, d_other = dist_mover[pos[mover]], dist_other[pos[other]]
    if d_mover < 0 or d_other < 0:
        return None
    goals = ctx["goals"]
    row_me, row_opp = pos[0] // BOARD, pos[1] // BOARD
    passed = row_me < row_opp if goals[0] < goals[1] else row_me > row_opp
    static = _evaluate(d_mover, d_other, walls_left[mover], walls_left[other], passed)
    race = ctx["race"]
    if race is not None and ctx["hash"] == ctx["race_hash"]:
        res = race.get((pos[0], pos[1], mover))
        if res is not None:
            result, plies = res
            loser = other if result == 1 else mover
            if walls_left[loser] == 0:
                return result * (WIN_SCORE - (ply + plies - 1))
    if depth == 0:
        return static
    if abs(static) >= SURE_SCORE - 100 and not _pawns_close(pos):
        return static
    tt_key = (ctx["hash"], pos, walls_left[0], walls_left[1], mover)
    entry = ctx["tt"].get(tt_key)
    tt_child = None
    if entry is not None:
        e_depth, e_score, e_flag, tt_child = entry
        if e_depth >= depth:
            if e_score >= WIN_SCORE - 100:
                e_score -= ply
            elif e_score <= -WIN_SCORE + 100:
                e_score += ply
            if (e_flag == EXACT or (e_flag == LOWER and e_score >= beta)
                    or (e_flag == UPPER and e_score <= alpha)):
                return e_score
    if (null_ok and depth > NULL_MOVE_R and static >= beta
            and not _pawns_close(pos)):
        score = _play(("n", None), pos, walls_left, h_walls, v_walls, mover,
                      depth - NULL_MOVE_R, beta - NULL_WINDOW, beta, ply, ctx)
        if score is not None and score >= beta:
            return score
    children = _move_children(pos, mover, dist_mover, ctx)
    walls_to_add = walls_left[mover] > 0
    if walls_to_add and (not children or (tt_child is not None and tt_child[0] == "w")):
        children += _wall_children(pos, h_walls, v_walls, mover, dist_other, ctx)
        walls_to_add = False
    if tt_child is not None and tt_child in children:
        children.remove(tt_child)
        children.insert(0, tt_child)
    goal_row = goals[mover]
    alpha_start = alpha
    best, best_child = None, None
    for index, child in enumerate(children):
        if walls_to_add and index == len(children) - 1:
            children += _wall_children(pos, h_walls, v_walls, mover, dist_other, ctx)
            walls_to_add = False
        if child[0] == "m" and child[1] // BOARD == goal_row:
            return WIN_SCORE - ply
        if best is None:
            score = _play(child, pos, walls_left, h_walls, v_walls, mover, depth,
                          alpha, beta, ply, ctx)
        else:
            if index >= LMR_AFTER and depth >= 3 and child[0] == "w":
                cut = 2 if index >= LMR_TWO_AFTER and depth >= 5 else 1
                score = _play(child, pos, walls_left, h_walls, v_walls, mover,
                              depth - cut, alpha, alpha + NULL_WINDOW, ply, ctx)
                if score is None or score <= alpha:
                    continue
            score = _play(child, pos, walls_left, h_walls, v_walls, mover, depth,
                          alpha, alpha + NULL_WINDOW, ply, ctx)
            if score is not None and alpha < score < beta:
                score = _play(child, pos, walls_left, h_walls, v_walls, mover,
                              depth, score, beta, ply, ctx)
        if score is None:
            continue
        if best is None or score > best:
            best, best_child = score, child
        if score > alpha:
            alpha = score
        if alpha >= beta:
            if child[0] == "w":
                ctx["history"][child[1]] = ctx["history"].get(child[1], 0) + depth * depth
            break
    if best is None:
        return static
    if best <= alpha_start:
        flag = UPPER
    elif best >= beta:
        flag = LOWER
    else:
        flag = EXACT
    stored = best
    if stored >= WIN_SCORE - 100:
        stored += ply
    elif stored <= -WIN_SCORE + 100:
        stored -= ply
    ctx["tt"][tt_key] = (depth, stored, flag, best_child)
    return best


def _to_action(child):
    kind, value = child
    if kind == "m":
        return {"type": "move", "to": [value // BOARD, value % BOARD]}
    orientation, key = value
    return {"type": "wall", "orientation": orientation,
            "row": key // BOARD, "col": key % BOARD}


def _root_children(observation, pos, walls_left, h_walls, v_walls, dist_me, dist_opp, adj,
                   goal_me):
    moves = [row * BOARD + col for row, col in observation["legal_moves"]]
    moves.sort(key=lambda cell: dist_me[cell] if dist_me[cell] >= 0 else 99)
    children = [("m", cell) for cell in moves]
    if walls_left[0] > 0:
        legal_walls = {(wall["orientation"], wall["row"] * BOARD + wall["col"])
                       for wall in observation["legal_walls"]}
        for wall in _candidate_walls(pos[1], dist_opp, adj, h_walls, v_walls):
            if wall in legal_walls:
                children.append(("w", wall))
        if walls_left[1] > 0:
            threats = []
            for wall in _candidate_walls(pos[0], dist_me, adj, h_walls, v_walls):
                _cut(adj, wall[0], wall[1])
                d_after = _dist_map(goal_me, adj)[pos[0]]
                _join(adj, wall[0], wall[1])
                if d_after - dist_me[pos[0]] >= 2:
                    threats.append((d_after, wall))
            threats.sort(reverse=True)
            have = {child[1] for child in children}
            for _, (o, k) in threats[:3]:
                if o == "H":
                    blockers = [("H", k - 1), ("H", k + 1), ("V", k)]
                else:
                    blockers = [("V", k - 9), ("V", k + 9), ("H", k)]
                for wall in blockers:
                    if wall in legal_walls and wall not in have:
                        have.add(wall)
                        children.append(("w", wall))
    return children


def _best_action(children, pos, walls_left, h_walls, v_walls, dist_me, ctx):
    best_child = children[0]
    for depth in range(1, MAX_DEPTH + 1):
        scores = {}
        alpha, beta = -float("inf"), float("inf")
        iteration_best, iteration_score = None, None
        try:
            for child in children:
                retreat = child[0] == "m" and dist_me[child[1]] > dist_me[pos[0]]
                need = alpha + (RETREAT_PENALTY if retreat else 0)
                if iteration_best is not None:
                    score = _play(child, pos, walls_left, h_walls, v_walls, 0, depth,
                                  need, need + NULL_WINDOW, 1, ctx)
                    if score is not None and score > need:
                        score = _play(child, pos, walls_left, h_walls, v_walls, 0,
                                      depth, need, beta, 1, ctx)
                else:
                    score = _play(child, pos, walls_left, h_walls, v_walls, 0, depth,
                                  alpha, beta, 1, ctx)
                if score is None:
                    continue
                if retreat:
                    score -= RETREAT_PENALTY
                scores[child] = score
                if iteration_score is None or score > iteration_score:
                    iteration_best, iteration_score = child, score
                    alpha = max(alpha, score)
        except _Timeout:
            if iteration_best is not None:
                best_child = iteration_best
            break
        if iteration_best is None:
            break
        best_child = iteration_best
        children.sort(key=lambda child: scores.get(child, -float("inf")), reverse=True)
        if abs(iteration_score) >= WIN_SCORE - 100 and depth >= WIN_SCORE - abs(iteration_score):
            break
    return best_child


def _decide(observation):
    deadline = time.perf_counter() + TIME_BUDGET
    you, opponent = observation["you"], observation["opponent"]
    goals = (you["goal_row"], opponent["goal_row"])
    pos = (you["position"][0] * BOARD + you["position"][1],
           opponent["position"][0] * BOARD + opponent["position"][1])
    walls_left = [you["walls_remaining"], opponent["walls_remaining"]]
    h_walls = {row * BOARD + col for row, col in observation["walls"]["horizontal"]}
    v_walls = {row * BOARD + col for row, col in observation["walls"]["vertical"]}
    for row, col in observation["legal_moves"]:
        if row == goals[0]:
            return {"type": "move", "to": [row, col]}
    if _MEMORY["goals"] != goals:
        _MEMORY.update(goals=goals, tt={}, cache={}, walls={}, history={}, race={})
    for name, limit in _LIMITS.items():
        if len(_MEMORY[name]) > limit:
            _MEMORY[name] = {}
    adj = _build_adj(h_walls, v_walls)
    dist_me = _dist_map(goals[0], adj)
    dist_opp = _dist_map(goals[1], adj)
    wall_hash = 0
    for key in h_walls:
        wall_hash ^= _ZOBRIST["H"][key]
    for key in v_walls:
        wall_hash ^= _ZOBRIST["V"][key]
    race = None
    if min(walls_left) == 0:
        race = _MEMORY["race"].get(wall_hash)
        if race is None:
            race = _solve_race(adj, goals)
            _MEMORY["race"][wall_hash] = race
        if walls_left[1] == 0:
            action = _race_action(observation, pos, goals, race,
                                  walls_left[0] == 0, dist_me)
            if action is not None:
                return action
    children = _root_children(observation, pos, walls_left, h_walls, v_walls,
                              dist_me, dist_opp, adj, goals[0])
    if not children:
        return {"type": "pass"}
    history = _MEMORY["history"]
    for wall in history:
        history[wall] //= 2
    ctx = {"nodes": 0, "deadline": deadline, "goals": goals, "adj": adj,
           "hash": wall_hash, "cache": _MEMORY["cache"], "walls": _MEMORY["walls"],
           "tt": _MEMORY["tt"], "history": history, "race": race,
           "race_hash": wall_hash}
    best = _best_action(children, pos, walls_left, h_walls, v_walls, dist_me, ctx)
    return _to_action(best)


def _greedy(observation):
    moves = observation.get("legal_moves") or []
    if not moves:
        return {"type": "pass"}
    distances = observation["distances"]
    best = min(moves, key=lambda m: distances[m[0]][m[1]]
               if distances[m[0]][m[1]] >= 0 else 99)
    return {"type": "move", "to": [int(best[0]), int(best[1])]}


def choose_action(observation):
    try:
        return _decide(observation)
    except Exception:
        return _greedy(observation)
