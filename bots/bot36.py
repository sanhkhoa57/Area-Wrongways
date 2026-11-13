"""Agent Wrongway: đi đường ngắn nhất, đặt tường khi làm đối thủ chậm hơn rõ rệt."""

from collections import deque

SIZE = 9
DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def wall_edges(orientation, row, col):
    if orientation == "H":
        pairs = [((row, col), (row + 1, col)), ((row, col + 1), (row + 1, col + 1))]
    else:
        pairs = [((row, col), (row, col + 1)), ((row + 1, col), (row + 1, col + 1))]
    edges = set()
    for a, b in pairs:
        edges.add((a, b))
        edges.add((b, a))
    return edges


def blocked_edges(walls):
    blocked = set()
    for r, c in walls["horizontal"]:
        blocked |= wall_edges("H", r, c)
    for r, c in walls["vertical"]:
        blocked |= wall_edges("V", r, c)
    return blocked


def path_length(start, goal_row, blocked):
    """BFS: số bước ngắn nhất từ start tới hàng goal_row (bỏ qua quân cờ)."""
    start = tuple(start)
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        cell, d = queue.popleft()
        if cell[0] == goal_row:
            return d
        for dr, dc in DIRS:
            nxt = (cell[0] + dr, cell[1] + dc)
            if not (0 <= nxt[0] < SIZE and 0 <= nxt[1] < SIZE):
                continue
            if nxt in seen or (cell, nxt) in blocked:
                continue
            seen.add(nxt)
            queue.append((nxt, d + 1))
    return 999


def best_move(observation):
    distances = observation["distances"]
    me = observation["you"]["position"]
    opp = observation["opponent"]["position"]
    best, best_key = None, None
    for move in observation["legal_moves"]:
        r, c = move
        d = distances[r][c]
        if d < 0:
            d = 999
        # Ưu tiên: gần đích, tiếp theo là ô gần cột giữa, rồi tránh đứng sát đối thủ.
        key = (d, abs(c - 4), abs(r - opp[0]) + abs(c - opp[1]) == 1)
        if best_key is None or key < best_key:
            best, best_key = move, key
    return best


def choose_action(observation):
    moves = observation["legal_moves"]
    you = observation["you"]
    opp = observation["opponent"]

    # 1. Về đích được ngay thì đi luôn.
    for move in moves:
        if move[0] == you["goal_row"]:
            return {"type": "move", "to": list(move)}

    blocked = blocked_edges(observation["walls"])
    my_dist = path_length(you["position"], you["goal_row"], blocked)
    opp_dist = path_length(opp["position"], opp["goal_row"], blocked)

    # 2. Đặt tường nếu đối thủ sắp thắng hoặc tường tạo lợi thế lớn.
    if you["walls_remaining"] > 0 and observation["legal_walls"] and opp_dist <= my_dist + 1:
        # Đối thủ đi sau mình một lượt nếu mình đang tới lượt: mình thắng sau my_dist lượt.
        behind = opp_dist < my_dist
        threshold = 2 if my_dist <= opp_dist else 1
        best_wall, best_gain = None, 0
        for wall in observation["legal_walls"]:
            cand = blocked | wall_edges(wall["orientation"], wall["row"], wall["col"])
            opp_after = path_length(opp["position"], opp["goal_row"], cand)
            if opp_after - opp_dist < threshold:
                continue
            my_after = path_length(you["position"], you["goal_row"], cand)
            gain = (opp_after - opp_dist) - (my_after - my_dist)
            if gain > best_gain:
                best_gain, best_wall = gain, wall
        if best_wall is not None and best_gain >= threshold:
            return {"type": "wall", "orientation": best_wall["orientation"],
                    "row": best_wall["row"], "col": best_wall["col"]}

    # 3. Ngược lại đi theo đường ngắn nhất.
    move = best_move(observation)
    if move is not None:
        return {"type": "move", "to": list(move)}
    return {"type": "pass"}
