"""
Bài nộp Đồ án - Stage 1
MSSV: xxxxxxxx
Chiến thuật: Ưu tiên dứt điểm + Greedy bám trung lộ + Đặt tường tối ưu hóa lợi thế bước đi.
"""
from collections import deque


def get_shortest_path_length(start_pos, goal_row, blocked_edges, extra_blocked=()):
    if start_pos[0] == goal_row:
        return 0

    queue = deque([(start_pos[0], start_pos[1], 0)])
    visited = {start_pos}

    while queue:
        r, c, d = queue.popleft()

        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < 9 and 0 <= nc < 9 and (nr, nc) not in visited:
                edge = ((r, c), (nr, nc))
                if edge not in blocked_edges and edge not in extra_blocked:
                    if nr == goal_row:
                        return d + 1
                    visited.add((nr, nc))
                    queue.append((nr, nc, d + 1))
    return -1


def build_blocked_edges(walls):
    blocked = set()
    for r, c in walls.get("horizontal", []):
        blocked.add(((r, c), (r + 1, c)))
        blocked.add(((r + 1, c), (r, c)))
        blocked.add(((r, c + 1), (r + 1, c + 1)))
        blocked.add(((r + 1, c + 1), (r, c + 1)))

    for r, c in walls.get("vertical", []):
        blocked.add(((r, c), (r, c + 1)))
        blocked.add(((r, c + 1), (r, c)))
        blocked.add(((r + 1, c), (r + 1, c + 1)))
        blocked.add(((r + 1, c + 1), (r + 1, c)))
    return blocked


def get_wall_edges(r, c, ori):
    if ori == "H":
        return (
            ((r, c), (r + 1, c)), ((r + 1, c), (r, c)),
            ((r, c + 1), (r + 1, c + 1)), ((r + 1, c + 1), (r, c + 1))
        )
    return (
        ((r, c), (r, c + 1)), ((r, c + 1), (r, c)),
        ((r + 1, c), (r + 1, c + 1)), ((r + 1, c + 1), (r + 1, c))
    )


def choose_action(observation):
    legal_moves = observation.get("legal_moves", [])
    distances = observation.get("distances", [])
    legal_walls = observation.get("legal_walls", [])

    my_info = observation["you"]
    opp_info = observation["opponent"]

    my_pos = tuple(my_info["position"])
    opp_pos = tuple(opp_info["position"])
    my_goal = my_info["goal_row"]
    opp_goal = opp_info["goal_row"]

    # 0.Thắng luôn nếu có thể trong lượt này
    for move in legal_moves:
        if move[0] == my_goal:
            return {"type": "move", "to": list(move)}

    my_dist = distances[my_pos[0]][my_pos[1]]
    current_blocked = build_blocked_edges(observation["walls"])
    opp_dist = get_shortest_path_length(opp_pos, opp_goal, current_blocked)

    # 1. Chiến thuật đặt tường
    if my_info["walls_remaining"] > 0 and legal_walls and opp_dist != -1:
        should_consider_wall = (opp_dist < my_dist) or (opp_dist <= 3)

        if should_consider_wall:
            best_wall = None
            best_net_gain = -999
            best_wall_proximity = 999

            for w in legal_walls:
                r, c, ori = w["row"], w["col"], w["orientation"]
                extra_edges = get_wall_edges(r, c, ori)

                new_opp_dist = get_shortest_path_length(opp_pos, opp_goal, current_blocked, extra_edges)
                if new_opp_dist <= opp_dist:
                    continue

                new_my_dist = get_shortest_path_length(my_pos, my_goal, current_blocked, extra_edges)
                if new_my_dist == -1:
                    continue

                delay = new_opp_dist - opp_dist
                penalty = max(0, new_my_dist - my_dist)
                net_gain = delay - penalty

                wall_proximity = abs(r - opp_pos[0]) + abs(c - opp_pos[1])

                if net_gain > best_net_gain or (net_gain == best_net_gain and wall_proximity < best_wall_proximity):
                    best_net_gain = net_gain
                    best_wall_proximity = wall_proximity
                    best_wall = w

            required_gain = 1 if opp_dist <= 2 else 2

            if best_wall is not None and best_net_gain >= required_gain:
                return {
                    "type": "wall",
                    "orientation": best_wall["orientation"],
                    "row": int(best_wall["row"]),
                    "col": int(best_wall["col"])
                }

    # 2. Chiến thuật di chuyển (Đường ngắn nhất + Ưu tiên nhảy + Tránh làm bàn đạp có lợi cho đối thủ + Bám trung lộ)
    if not legal_moves:
        return {"type": "pass"}

    best_move = legal_moves[0]
    best_score = (float("inf"), 1, 1, float("inf"))

    for move in legal_moves:
        r, c = move
        dist = distances[r][c]
        if dist < 0:
            continue

        step_manhattan = abs(r - my_pos[0]) + abs(c - my_pos[1])
        is_jump = (step_manhattan == 2)
        not_a_jump = 0 if is_jump else 1

        dist_to_opp = abs(r - opp_pos[0]) + abs(c - opp_pos[1])
        bad_stepping_stone = 0
        if dist_to_opp == 1 and ((r, c), opp_pos) not in current_blocked:

            opp_forward_step = 1 if opp_goal > opp_pos[0] else -1
            if (r - opp_pos[0]) == opp_forward_step:
                bad_stepping_stone = 1

        center_dist = abs(c - 4)

        score = (dist, not_a_jump, bad_stepping_stone, center_dist)

        if score < best_score:
            best_score = score
            best_move = move

    return {
        "type": "move",
        "to": [int(best_move[0]), int(best_move[1])]
    }