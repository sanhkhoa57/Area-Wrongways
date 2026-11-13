from collections import deque


def wall_edges(orientation, row, col):
    if orientation == "H":
        pairs = [((row, col), (row + 1, col)),
                 ((row, col + 1), (row + 1, col + 1))]
    else:
        pairs = [((row, col), (row, col + 1)),
                 ((row + 1, col), (row + 1, col + 1))]
    edges = set()
    for a, b in pairs:
        edges.add((a, b))
        edges.add((b, a))
    return edges


def blocked_edges(walls):
    blocked = set()
    for row, col in walls["horizontal"]:
        blocked |= wall_edges("H", row, col)
    for row, col in walls["vertical"]:
        blocked |= wall_edges("V", row, col)
    return blocked


def path_length(start, goal_row, size, blocked):
    start = tuple(start)
    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        (row, col), dist = queue.popleft()
        if row == goal_row:
            return dist
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxt = (row + dr, col + dc)
            if not (0 <= nxt[0] < size and 0 <= nxt[1] < size):
                continue
            if nxt in visited or ((row, col), nxt) in blocked:
                continue
            visited.add(nxt)
            queue.append((nxt, dist + 1))
    return size * size  # không tới được (không xảy ra với tường hợp lệ)


def best_move(observation):
    moves = observation["legal_moves"]
    distances = observation["distances"]
    if not moves:
        return {"type": "pass"}

    def score(move):
        d = distances[move[0]][move[1]]
        if d < 0:
            d = 999
        return (d, abs(move[1] - 4))

    return {"type": "move", "to": list(min(moves, key=score))}


def best_wall(observation):
    you = observation["you"]
    opponent = observation["opponent"]
    size = observation["board_size"]
    blocked = blocked_edges(observation["walls"])

    own_now = path_length(you["position"], you["goal_row"], size, blocked)
    enemy_now = path_length(opponent["position"], opponent["goal_row"], size, blocked)

    # chay nhanh hon thi chay
    if enemy_now > own_now:
        return None

    existing = [tuple(w) for w in observation["walls"]["horizontal"]]
    existing += [tuple(w) for w in observation["walls"]["vertical"]]
    opp_row, opp_col = opponent["position"]

    chosen = None
    chosen_score = 0
    for wall in observation["legal_walls"]:
        after = blocked | wall_edges(wall["orientation"], wall["row"], wall["col"])
        own_after = path_length(you["position"], you["goal_row"], size, after)
        enemy_after = path_length(opponent["position"], opponent["goal_row"], size, after)
        gain = (enemy_after - enemy_now) - (own_after - own_now)
        if gain < 1:
            continue
        score = gain * 10
        # tuong gan tuong
        for r, c in existing:
            if abs(r - wall["row"]) + abs(c - wall["col"]) <= 2:
                score += 3
                break
        # tuong gan dich
        near = abs(wall["row"] - opp_row) + abs(wall["col"] - opp_col)
        score += max(0, 4 - near)
        if score > chosen_score:
            chosen_score = score
            chosen = wall
    return chosen


def decide(observation):
    goal_row = observation["you"]["goal_row"]

    # 1. Về đích ngay nếu được.
    for move in observation["legal_moves"]:
        if move[0] == goal_row:
            return {"type": "move", "to": list(move)}

    # 2. Cân nhắc đặt tường.
    if observation["you"]["walls_remaining"] > 0:
        wall = best_wall(observation)
        if wall is not None:
            return {"type": "wall", "orientation": wall["orientation"],
                    "row": wall["row"], "col": wall["col"]}

    # 3. Đi tiếp về phía đích.
    return best_move(observation)


def choose_action(observation):
    try:
        return decide(observation)
    except Exception:
        moves = observation.get("legal_moves", [])
        if moves:
            return {"type": "move", "to": list(moves[0])}
        return {"type": "pass"}