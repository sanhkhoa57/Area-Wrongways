"""
Wrongway Arena - Mastermind Champion Engine (Chess Engine Paradigm)
File nộp bài dự thi: xxxxxxxx.py
Kiến trúc: 5-Stage Decision Pipeline + Path-Edge Pruning + Multi-Factor Chess Evaluation
Tuân thủ: 100% chuẩn quy định Wrongway Arena & Hướng dẫn Chess-based Agent
"""

from collections import deque
import time

BOARD_SIZE = 9
_DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def get_blocked_edges(walls_h, walls_v):
    """
    Xây dựng tập hợp các cạnh bị chặn bởi các bức tường đã đặt.
    Mỗi đoạn tường chặn 2 cạnh kề nhau (lưu 2 chiều trong set).
    """
    blocked = set()
    for r, c in walls_h:
        blocked.add(((r, c), (r + 1, c)))
        blocked.add(((r + 1, c), (r, c)))
        blocked.add(((r, c + 1), (r + 1, c + 1)))
        blocked.add(((r + 1, c + 1), (r, c + 1)))

    for r, c in walls_v:
        blocked.add(((r, c), (r, c + 1)))
        blocked.add(((r, c + 1), (r, c)))
        blocked.add(((r + 1, c), (r + 1, c + 1)))
        blocked.add(((r + 1, c + 1), (r + 1, c)))

    return blocked


def add_wall_edges(blocked_set, orientation, r, c):
    """
    Tạo bản sao blocked_set và bổ sung nhanh 2 cạnh của 1 tường ứng viên.
    """
    s = blocked_set.copy()
    if orientation == "H":
        s.add(((r, c), (r + 1, c)))
        s.add(((r + 1, c), (r, c)))
        s.add(((r, c + 1), (r + 1, c + 1)))
        s.add(((r + 1, c + 1), (r, c + 1)))
    else:
        s.add(((r, c), (r, c + 1)))
        s.add(((r, c + 1), (r, c)))
        s.add(((r + 1, c), (r + 1, c + 1)))
        s.add(((r + 1, c + 1), (r + 1, c)))
    return s


def bfs_shortest_path(start_pos, goal_row, blocked_edges):
    """
    Thuật toán BFS: trả về cả khoảng cách ngắn nhất và danh sách các ô trên đường đi.
    Dùng để dựng vết đường đi ngắn nhất của đối thủ (Path-Edge Tracing).
    """
    sr, sc = start_pos
    if sr == goal_row:
        return 0, [(sr, sc)]

    queue = deque([(sr, sc)])
    visited = {(sr, sc): None}
    found_end = None

    while queue:
        r, c = queue.popleft()
        if r == goal_row:
            found_end = (r, c)
            break

        for dr, dc in _DIRS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE:
                if ((r, c), (nr, nc)) not in blocked_edges:
                    nb = (nr, nc)
                    if nb not in visited:
                        visited[nb] = (r, c)
                        queue.append(nb)

    if found_end is None:
        return float("inf"), []

    path = []
    curr = found_end
    while curr is not None:
        path.append(curr)
        curr = visited[curr]
    path.reverse()
    return len(path) - 1, path


def bfs_distance_only(start_pos, goal_row, blocked_edges):
    """
    BFS tối ưu chỉ tính khoảng cách ngắn nhất, tốc độ cực cao (< 0.05ms).
    """
    sr, sc = start_pos
    if sr == goal_row:
        return 0

    queue = deque([(sr, sc, 0)])
    visited = {(sr, sc)}

    while queue:
        r, c, dist = queue.popleft()
        if r == goal_row:
            return dist

        for dr, dc in _DIRS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE:
                if ((r, c), (nr, nc)) not in blocked_edges:
                    nb = (nr, nc)
                    if nb not in visited:
                        visited.add(nb)
                        queue.append((nr, nc, dist + 1))

    return float("inf")


def evaluate_board(my_dist, opp_dist, my_walls, opp_walls, my_col, opp_col):
    """
    Hàm đánh giá đa yếu tố (Multi-Factor Evaluation) chuẩn Chess Engine:
      H = 100 * (Dist_Opp - Dist_You) + 15 * (Walls_You - Walls_Opp)
          - 3 * |Col_You - 4| + 1.5 * |Col_Opp - 4| + LockoutBonus
    """
    if my_dist == 0:
        return 100000.0
    if opp_dist == 0:
        return -100000.0

    score = (opp_dist - my_dist) * 100.0
    score += (my_walls - opp_walls) * 15.0
    score -= abs(my_col - 4) * 3.0
    score += abs(opp_col - 4) * 1.5

    # Thưởng bẫy khóa (Lockout Bonus) nếu đối thủ bị đẩy đi đường vòng xa
    if opp_dist >= 15:
        score += 200.0

    return score


def wall_cuts_path_edges(orientation, r, c, path_edges):
    """
    Kiểm tra nhanh O(1) xem tường ứng viên có cắt qua bất kỳ cạnh nào
    trên đường đi ngắn nhất của đối thủ hay không.
    """
    if orientation == "H":
        pairs = (
            ((r, c), (r + 1, c)),
            ((r, c + 1), (r + 1, c + 1)),
        )
    else:
        pairs = (
            ((r, c), (r, c + 1)),
            ((r + 1, c), (r + 1, c + 1)),
        )
    for a, b in pairs:
        if (a, b) in path_edges or (b, a) in path_edges:
            return True
    return False


def choose_action(observation):
    """
    Hàm giao diện bắt buộc của Wrongway Arena.
    Triển khai quy trình ra quyết định 5 bước (Decision Pipeline):
      Bước 1: Instant Win Check (Cán đích ngay lập tức)
      Bước 2: Emergency Lockout System (Cứu thua khẩn cấp)
      Bước 3: BFS Path-Edge Tracing (Dựng vết đường đi ngắn nhất của đối thủ)
      Bước 4: Move Ordering & Safe Net-Gain Candidate Pruning (Lọc ứng viên an toàn)
      Bước 5: Multi-Factor Chess Selection (Đánh giá và chọn nước đi tối ưu)
    """
    start_time = time.perf_counter()

    you = observation["you"]
    opp = observation["opponent"]

    my_pos = tuple(you["position"])
    opp_pos = tuple(opp["position"])
    my_goal = you["goal_row"]
    opp_goal = opp["goal_row"]
    my_walls = you["walls_remaining"]
    opp_walls = opp["walls_remaining"]

    legal_moves = observation["legal_moves"]
    legal_walls = observation["legal_walls"]

    existing_h = observation["walls"]["horizontal"]
    existing_v = observation["walls"]["vertical"]

    base_blocked = get_blocked_edges(existing_h, existing_v)

    my_dist, _ = bfs_shortest_path(my_pos, my_goal, base_blocked)
    opp_dist, opp_path = bfs_shortest_path(opp_pos, opp_goal, base_blocked)

    # -------------------------------------------------------------------------
    # BƯỚC 1: INSTANT WIN CHECK (CÁN ĐÍCH THẮNG NGAY LẬP TỨC)
    # -------------------------------------------------------------------------
    for move in legal_moves:
        if move[0] == my_goal:
            return {"type": "move", "to": list(move)}

    # -------------------------------------------------------------------------
    # BƯỚC 2: EMERGENCY LOCKOUT (PHÒNG THỦ CỨU THUA KHẨN CẤP)
    # Nếu đối thủ chỉ còn cách đích <= 2 bước và ta còn tường, lập tức chặn
    # -------------------------------------------------------------------------
    if opp_dist <= 2 and my_walls > 0 and legal_walls:
        best_wall = None
        max_delayed_dist = opp_dist
        min_my_increase = float("inf")

        for wall in legal_walls:
            cand_blocked = add_wall_edges(
                base_blocked, wall["orientation"], wall["row"], wall["col"]
            )
            d = bfs_distance_only(opp_pos, opp_goal, cand_blocked)
            if d > opp_dist:
                my_d = bfs_distance_only(my_pos, my_goal, cand_blocked)
                my_inc = my_d - my_dist
                # Ưu tiên tường làm chậm đối thủ nhiều nhất, nếu bằng nhau chọn tường ít cản mình nhất
                if d > max_delayed_dist or (d == max_delayed_dist and my_inc < min_my_increase):
                    max_delayed_dist = d
                    min_my_increase = my_inc
                    best_wall = wall

        if best_wall and max_delayed_dist > opp_dist:
            return {
                "type": "wall",
                "orientation": str(best_wall["orientation"]),
                "row": int(best_wall["row"]),
                "col": int(best_wall["col"]),
            }

    # -------------------------------------------------------------------------
    # BƯỚC 3: ĐÁNH GIÁ CÁC NƯỚC DI CHUYỂN (MOVE CANDIDATES & CENTER CONTROL)
    # -------------------------------------------------------------------------
    best_action = None
    best_score = -float("inf")

    for move in legal_moves:
        d = bfs_distance_only(tuple(move), my_goal, base_blocked)
        score = evaluate_board(d, opp_dist, my_walls, opp_walls, move[1], opp_pos[1])
        # Thưởng tiến bước về đích
        if d < my_dist:
            score += 15.0

        if score > best_score:
            best_score = score
            best_action = {"type": "move", "to": list(move)}

    # -------------------------------------------------------------------------
    # BƯỚC 4: ĐÁNH GIÁ TƯỜNG (PATH-EDGE PRUNING + SAFE NET-GAIN)
    # Chỉ xem xét đặt tường khi đối thủ bám sát hoặc đang dẫn trước
    # -------------------------------------------------------------------------
    if my_walls > 0 and legal_walls and (opp_dist <= my_dist + 1):
        # Dựng set các cạnh của đường đi ngắn nhất đối thủ
        opp_path_edges = set()
        for i in range(len(opp_path) - 1):
            opp_path_edges.add((opp_path[i], opp_path[i + 1]))
            opp_path_edges.add((opp_path[i + 1], opp_path[i]))

        # Lọc tường cắt vết đường đi
        candidate_walls = [
            w for w in legal_walls
            if wall_cuts_path_edges(w["orientation"], w["row"], w["col"], opp_path_edges)
        ]

        if not candidate_walls:
            candidate_walls = legal_walls[:15]

        for wall in candidate_walls:
            # Time guard an toàn: không tính toán vượt quá 40ms
            if (time.perf_counter() - start_time) > 0.040:
                break

            cand_blocked = add_wall_edges(
                base_blocked, wall["orientation"], wall["row"], wall["col"]
            )

            new_opp_d = bfs_distance_only(opp_pos, opp_goal, cand_blocked)
            delta_opp = new_opp_d - opp_dist

            if delta_opp < 1:
                continue

            new_my_d = bfs_distance_only(my_pos, my_goal, cand_blocked)
            delta_my = new_my_d - my_dist

            # QUY TẮC AN TOÀN: Tuyệt đối không bao giờ đặt tường làm tăng đường đi của mình!
            if delta_my > 0:
                continue

            net_gain = delta_opp - delta_my

            # Điều kiện đặt tường:
            # - Tường tạo lợi thế >= 2 bước, HOẶC
            # - Đối thủ đang dẫn trước / sắp về đích và tường làm chậm >= 1 bước
            if net_gain >= 2 or (my_dist >= opp_dist and net_gain >= 1):
                # Vì đặt tường tốn 1 lượt (không đi được 1 bước), tương đương new_my_d + 1
                w_score = evaluate_board(
                    new_my_d + 1,
                    new_opp_d,
                    my_walls - 1,
                    opp_walls,
                    my_pos[1],
                    opp_pos[1],
                )
                w_score += net_gain * 25.0

                if w_score > best_score:
                    best_score = w_score
                    best_action = {
                        "type": "wall",
                        "orientation": str(wall["orientation"]),
                        "row": int(wall["row"]),
                        "col": int(wall["col"]),
                    }

    # -------------------------------------------------------------------------
    # BƯỚC 5: FALLBACK AN TOÀN
    # -------------------------------------------------------------------------
    if best_action is None:
        best_action = (
            {"type": "move", "to": list(legal_moves[0])}
            if legal_moves
            else {"type": "pass"}
        )

    return best_action
