from collections import deque

SIZE = 9
DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))

def wall_edges(orientation, row, col):
    """Đánh dấu các cạnh bị tường chặn"""
    if orientation == "H":
        pairs = [
            ((row, col), (row + 1, col)),
            ((row, col + 1), (row + 1, col + 1)),
        ]
    else:
        pairs = [
            ((row, col), (row, col + 1)),
            ((row + 1, col), (row + 1, col + 1))
        ]
    edges = set()
    for a, b in pairs:
        edges.add((a,b))
        edges.add((b,a))
    return edges

def blocked_from_walls(walls):
    blocked = set()
    for row, col in walls["horizontal"]:
        blocked |= wall_edges("H", row, col)
    for row, col in walls["vertical"]:
        blocked |= wall_edges("V", row, col)
    return blocked

def bfs_dist(start, goal_row, blocked):
    """BFS"""
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

def near(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1

def best_move(observation, avoid_adjacent):
    moves = observation["legal_moves"]
    distances = observation["distances"]
    opp_pos = observation["opponent"]["position"]

    if avoid_adjacent:
        safe = [m for m in moves if not near(m, opp_pos)]
        moves = safe or moves

    if not moves:
        return None
    
    def key(move):
        d = distances[move[0]][move[1]]
        return (d if d >= 0 else 999, abs(move[1] - 4))
    return list(min(moves, key=key))

def choose_action(observation):
    """Đi tới ô hợp lệ có khoảng cách tới đích nhỏ nhất."""
    you = observation["you"]
    opp = observation["opponent"]
    goal = you["goal_row"]

    # 1. Nếu có thể thắng ngay, ưu tiên về đích.
    for move in observation["legal_moves"]:
        if move[0] == goal:
            return {"type": "move", "to" : list(move)}

    move = best_move(observation, False)
    blocked = blocked_from_walls(observation["walls"])
    d_me = bfs_dist(you["position"], goal, blocked)
    d_opp = bfs_dist(opp["position"], opp["goal_row"], blocked)
    margin_now = d_opp - d_me

    stepping_in = move is not None and near(move, opp["position"])
    # 2. Đang thắng hoạc hòa cuộc đua (mình đi trước): cứ chạy

    if margin_now >= 0 and not stepping_in and move is not None:
        return {"type": "move", "to": move}

    # 3. Đang thua: tìm đường tốt nhất
    best_wall = None
    best_margin = -999 if stepping_in else margin_now
    if you["walls_remaining"] > 0:
        for w in observation["legal_walls"]:
            cand = blocked | wall_edges(w["orientation"], w["row"], w["col"])
            new_me = bfs_dist(you["position"], goal, cand)
            new_opp = bfs_dist(opp["position"], opp["goal_row"], cand)
            # Sau khi đặt, đối thủ đi trước nên mình thắng khi new_me < new_opp
            margin_after = new_opp - new_me - 1
            if stepping_in and new_me > d_me:
                continue 

            if margin_after > best_margin:
                best_margin = margin_after
                best_wall = w

    if best_wall is not None:
        return {"type": "wall", "orientation": best_wall["orientation"],
                "row": best_wall["row"], "col": best_wall["col"]}
    move  = best_move(observation, True)
    if move is not None:
        return {"type": "move", "to": move}
    return {"type": "pass"}