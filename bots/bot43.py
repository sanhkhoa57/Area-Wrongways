"""Bot try countering opponent's playstyle, either greedy or wall, by guessing it using the last 5 moves"""

from collections import deque

OBSERVE_TURNS = 5
WALL_SHORTLIST = 6 # Max walls search
_memory = None # Moves memory

def _cell(position): # Position into 1 int
    return position[0] * 9 + position[1]


def _edges(wall): # (H/V,x,y) for wall pos
    orientation, r, c = wall
    a = r * 9 + c
    if orientation == 'H':
        return ((a, a + 9), (a + 1, a + 10))
    return ((a, a + 1), (a + 9, a + 10))


def _graph(walls):
    graph = [set() for _ in range(81)]
    for r in range(9):
        for c in range(9):
            a = r * 9 + c
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 9 and 0 <= cc < 9:
                    graph[a].add(rr * 9 + cc)
    for wall in walls:
        for a, b in _edges(wall):
            graph[a].discard(b)
            graph[b].discard(a)
    return graph


def _with_wall(graph, wall):
    # Only four neighbor sets change; share all other sets read-only.
    new = list(graph)
    for a, b in _edges(wall):
        new[a] = graph[a] - {b}
        new[b] = graph[b] - {a}
    return new


def _distances(graph, goal):
    distances = [999] * 81
    queue = deque(range(goal * 9, goal * 9 + 9))
    for cell in queue:
        distances[cell] = 0
    while queue:
        a = queue.popleft()
        for b in graph[a]:
            if distances[b] == 999:
                distances[b] = distances[a] + 1
                queue.append(b)
    return distances


def _moves(graph, pawn, other):
    """Exact immediate movement, including straight and blocked-back jumps."""
    result = set()
    for b in graph[pawn]:
        if b != other:
            result.add(b)
            continue
        r, c = divmod(pawn, 9)
        rr, cc = divmod(other, 9)
        dr, dc = rr - r, cc - c
        br, bc = rr + dr, cc + dc
        behind = br * 9 + bc
        if 0 <= br < 9 and 0 <= bc < 9 and behind in graph[other]:
            result.add(behind)
        else:
            for side in graph[other]:
                sr, sc = divmod(side, 9)
                if (sr - rr) * dr + (sc - cc) * dc == 0:
                    result.add(side)
    return sorted(result)


def _compatible(wall, placed):
    orientation, r, c = wall
    for ori, rr, cc in placed:
        if (r, c) == (rr, cc):  # same anchor: overlap or crossing
            return False
        if ori == orientation:
            if ori == 'H' and r == rr and abs(c - cc) == 1:
                return False
            if ori == 'V' and c == cc and abs(r - rr) == 1:
                return False
    return True


_ALL_WALLS = [(o, r, c) for o in ('H', 'V')
              for r in range(8) for c in range(8)]


def _snapshot(o):
    walls = frozenset((ori, r, c)
                      for name, ori in (('horizontal', 'H'), ('vertical', 'V'))
                      for r, c in o['walls'][name])
    return (_cell(o['you']['position']), _cell(o['opponent']['position']), walls)


def _observe(o, snapshot):
    global _memory
    turn = o['turn']
    identity = (o['player_id'], o['you']['goal_row'])
    if (_memory is None or _memory['identity'] != identity
            or turn <= _memory['last_turn']):
        _memory = dict(identity=identity, last_turn=-1, previous=None,
                       seen=0, walls=0, shortest=0, mode='opening')
        # Player 1 can also observe player 0's very first action.
        if turn == 1 and o['player_id'] == 1:
            _memory['previous'] = (4, 76, frozenset())
            _memory['last_turn'] = -1
    m = _memory
    previous = m['previous']
    if (m['mode'] == 'opening' and previous is not None
            and turn == m['last_turn'] + 2):
        own, enemy, walls = previous  # state immediately AFTER our last action
        now_own, now_enemy, now_walls = snapshot
        m['seen'] += 1
        if now_walls != walls:
            m['walls'] += 1
        elif enemy != now_enemy and own == now_own:
            graph = _graph(walls)
            distances = _distances(graph, o['opponent']['goal_row'])
            moves = _moves(graph, enemy, own)
            if now_enemy in moves and distances[now_enemy] == min(
                    distances[p] for p in moves):
                m['shortest'] += 1
        if m['seen'] >= OBSERVE_TURNS:
            if m['walls'] >= 2:
                m['mode'] = 'wall-focused'
            elif m['walls'] == 0 and m['shortest'] == OBSERVE_TURNS:
                m['mode'] = 'greedy'
            else:
                m['mode'] = 'unknown'
    m['last_turn'] = turn
    return m['mode']


def _remember(snapshot, action):
    own, enemy, walls = snapshot
    if action['type'] == 'move':
        own = _cell(action['to'])
    elif action['type'] == 'wall':
        walls = walls | {(action['orientation'], action['row'], action['col'])}
    _memory['previous'] = (own, enemy, walls)
    return action


def _move_action(p):
    return {'type': 'move', 'to': list(divmod(p, 9))}


def _wall_action(w):
    return dict(type='wall', orientation=w[0], row=w[1], col=w[2])


def _choose(o, snapshot, mode):
    own, enemy, placed = snapshot
    goal, enemy_goal = o['you']['goal_row'], o['opponent']['goal_row']
    graph = _graph(placed)
    dm, de = _distances(graph, goal), _distances(graph, enemy_goal)
    moves = [_cell(p) for p in o['legal_moves']]
    for p in moves:
        if p // 9 == goal:
            return _move_action(p)
    fallback = (_move_action(min(moves, key=lambda p: (dm[p], p)))
                if moves else {'type': 'pass'})
    enemy_can_win = any(p // 9 == enemy_goal
                        for p in _moves(graph, enemy, own))
    fallback_pos = (_cell(fallback['to']) if fallback['type'] == 'move' else own)
    fallback_loses = any(p // 9 == enemy_goal
                         for p in _moves(graph, enemy, fallback_pos))
    if mode == 'opening' and not enemy_can_win and not fallback_loses:
        return fallback

    # Each candidate is already AFTER our action. Comparing move and wall
    # states accounts for the wall's lost movement turn without subtracting
    # the same tempo twice. Small reserve bonus breaks marginal wall ties.
    reserve = 0.12
    candidates = []
    for p in moves:
        score = de[enemy] - dm[p]
        candidates.append((score, _move_action(p), graph, p, placed, dm, de))
    wall_candidates = []
    for w in o['legal_walls']:
        wall = (w['orientation'], w['row'], w['col'])
        g = _with_wall(graph, wall)
        a, b = _distances(g, goal), _distances(g, enemy_goal)
        score = b[enemy] - a[own] - reserve
        wall_candidates.append((score, _wall_action(wall), g, own, placed | {wall}, a, b))
    # Keep every immediate defence, even if its distance score is poor.
    wall_candidates.sort(key=lambda item: item[0], reverse=True)
    selected = wall_candidates[:WALL_SHORTLIST]
    if enemy_can_win:
        selected = [x for x in wall_candidates
                    if not any(p // 9 == enemy_goal
                               for p in _moves(x[2], enemy, x[3]))]
    candidates.extend(selected)
    if not candidates:
        return fallback

    best_action, best_score = fallback, -float('inf')
    # Complete cheap evaluation for all candidates before expensive replies.
    ranked = []
    for static, action, g, pos, ws, a, b in candidates:
        replies = _moves(g, enemy, pos)
        if any(p // 9 == enemy_goal for p in replies):
            predicted = -10000.0
        else:
            # If shortest replies tie, take the least favorable for us.
            shortest = min((b[p] for p in replies), default=b[enemy])
            tied = [p for p in replies if b[p] == shortest] or [enemy]
            considered = tied if mode == 'greedy' else replies + [enemy]
            values = []
            for reply in considered:
                next_moves = _moves(g, pos, reply)
                our_steps = min((1 + a[p] for p in next_moves), default=a[pos])
                if any(p // 9 == goal for p in next_moves):
                    values.append(1000.0)
                else:
                    values.append(b[reply] - our_steps)
            predicted = min(values) - (reserve if action['type'] == 'wall' else 0)
        ranked.append((predicted, static, action, g, pos, ws, a, b))
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_action = ranked[0][2]
    if mode == 'greedy' or mode == 'opening':
        return best_action

    for predicted, static, action, g, pos, ws, a, b in ranked:
        worst = predicted
        if o['opponent']['walls_remaining'] and worst > -10000:
            for wall in _ALL_WALLS:
                if not _compatible(wall, ws):
                    continue
                gg = _with_wall(g, wall)
                aa, bb = _distances(gg, goal), _distances(gg, enemy_goal)
                if aa[pos] == 999 or bb[enemy] == 999:
                    continue  # both must retain a path, ignoring pawns
                next_moves = _moves(gg, pos, enemy)
                our_steps = min((1 + aa[p] for p in next_moves), default=aa[pos])
                value = (1000.0 if any(p // 9 == goal for p in next_moves)
                         else bb[enemy] - our_steps)
                value -= reserve if action['type'] == 'wall' else 0
                worst = min(worst, value)
        # Unknown mixes cautious defence and race progress. Wall-focused
        # uses the full worst-reply score. Both retain tactical loss checks.
        weight = 1.0 if mode == 'wall-focused' else 0.65
        score = (worst if worst <= -10000 else
                 weight * worst + (1 - weight) * predicted)
        score += 0.001 * static
        if score > best_score:
            best_score, best_action = score, action
    return best_action



def _search(o, snapshot, mode, fallback):
    """Iterative, beam-limited alpha-beta with no internal time limit.

    Depth 3 means our action, their reply, and our response. Depth 4 adds
    their next reply, but is deliberately not used: a consistent depth 3
    was more reliable in testing than machine-dependent depth 3/4 switching.
    Every simulated wall checks both players' reachability.
    Beam pruning makes this approximate minimax, not exhaustive game solving.
    """
    own, enemy, walls = snapshot
    goal, enemy_goal = o['you']['goal_row'], o['opponent']['goal_row']
    root = (own, enemy, walls, o['you']['walls_remaining'],
            o['opponent']['walls_remaining'])
    board_cache = {}
    action_cache = {}
    transpositions = {}
    reserve_value = 0.45 if mode == 'wall-focused' else 0.25

    def board(ws, parent=None, wall=None):
        if ws not in board_cache:
            graph = _with_wall(parent, wall) if parent is not None else _graph(ws)
            board_cache[ws] = (graph, _distances(graph, goal),
                               _distances(graph, enemy_goal))
        return board_cache[ws]

    def evaluate(state, side):
        p, q, ws, mine, theirs = state
        if p // 9 == goal:
            return 10000.0
        if q // 9 == enemy_goal:
            return -10000.0
        graph, dm, de = board(ws)
        pm, qm = _moves(graph, p, q), _moves(graph, q, p)
        if side == 0 and any(x // 9 == goal for x in pm):
            return 9999.0
        if side == 1 and any(x // 9 == enemy_goal for x in qm):
            return -9999.0
        dp = min((1 + dm[x] for x in pm), default=dm[p] + 1)
        dq = min((1 + de[x] for x in qm), default=de[q] + 1)
        return dq - dp + reserve_value * (mine - theirs) + (0.2 if side == 0 else -0.2)

    def actions(state, side, at_root=False):
        key = (state, side, at_root)
        if key in action_cache:
            return action_cache[key]
        p, q, ws, mine, theirs = state
        graph, dm, de = board(ws)
        pawn, other = (p, q) if side == 0 else (q, p)
        moves = ([_cell(x) for x in o['legal_moves']] if at_root
                 else _moves(graph, pawn, other))
        goal_row = goal if side == 0 else enemy_goal
        candidates = []
        for x in moves:
            child = ((x, q, ws, mine, theirs) if side == 0
                     else (p, x, ws, mine, theirs))
            entry = (evaluate(child, 1 - side), _move_action(x), child)
            if x // 9 == goal_row:
                action_cache[key] = [entry]
                return [entry]
            candidates.append(entry)
        if mode == 'greedy' and side == 1:
            shortest = min((de[x] for x in moves), default=999)
            candidates = [entry for entry in candidates
                          if de[entry[2][1]] == shortest]
        elif (mine if side == 0 else theirs) > 0:
            options = ([(w['orientation'], w['row'], w['col'])
                        for w in o['legal_walls']] if at_root else _ALL_WALLS)
            for wall in options:
                if not at_root and not _compatible(wall, ws):
                    continue
                new_ws = ws | {wall}
                gg, aa, bb = board(new_ws, graph, wall)
                if aa[p] == 999 or bb[q] == 999:
                    continue
                child = (p, q, new_ws, mine - (side == 0), theirs - (side == 1))
                candidates.append((evaluate(child, 1 - side), _wall_action(wall), child))
        # A pass can affect jump timing; allow it in simulations but favor
        # real progress on equal scores. Arena also permits pass at the root.
        if not (mode == 'greedy' and side == 1) or not candidates:
            candidates.append((evaluate(state, 1 - side), {'type': 'pass'}, state))
        candidates.sort(key=lambda item: item[0], reverse=(side == 0))
        # Always retain moves; keep a bounded number of the strongest walls.
        wall_limit = 10 if at_root else 4
        chosen = []
        count = 0
        for entry in candidates:
            if entry[1]['type'] == 'wall':
                if count >= wall_limit:
                    continue
                count += 1
            chosen.append(entry)
        action_cache[key] = chosen
        return chosen

    def minimax(state, side, depth, alpha, beta):
        p, q, ws, mine, theirs = state
        if p // 9 == goal or q // 9 == enemy_goal or depth == 0:
            return evaluate(state, side)
        key = (state, side, depth)
        old_alpha, old_beta = alpha, beta
        cached = transpositions.get(key)
        if cached:
            value, flag = cached
            if flag == 'exact':
                return value
            if flag == 'lower':
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if alpha >= beta:
                return value
        value = -float('inf') if side == 0 else float('inf')
        for _, action, child in actions(state, side):
            result = minimax(child, 1 - side, depth - 1, alpha, beta)
            if side == 0:
                value = max(value, result)
                alpha = max(alpha, value)
            else:
                value = min(value, result)
                beta = min(beta, value)
            if alpha >= beta:
                break
        flag = 'upper' if value <= old_alpha else 'lower' if value >= old_beta else 'exact'
        transpositions[key] = (value, flag)
        return value

    answer = fallback
    root_actions = actions(root, 0, True)
    for depth in (2, 3):
        iteration_action, iteration_score = answer, -float('inf')
        # Previous best first speeds up alpha-beta without changing ties
        # between moves at the initial iteration.
        ordered = sorted(root_actions, key=lambda entry: entry[1] != answer)
        for _, action, child in ordered:
            value = minimax(child, 1, depth - 1, iteration_score, float('inf'))
            if value > iteration_score:
                iteration_score, iteration_action = value, action
        answer = iteration_action
        if iteration_score >= 9999:
            break
    return answer


def choose_action(observation):
    """Arena entry point; no file access, external imports, or manual input."""
    snapshot = _snapshot(observation)
    mode = _observe(observation, snapshot)
    action = _choose(observation, snapshot, mode)
    if mode != 'opening' and not (action['type'] == 'move' and
                                   action['to'][0] == observation['you']['goal_row']):
        action = _search(observation, snapshot, mode, action)
    return _remember(snapshot, action)
