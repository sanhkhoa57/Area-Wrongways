import time
from collections import deque

# ================= ===================================================
# HẰNG SỐ CẤU HÌNH CỦA BOT
# =====================================================================
SIZE = 9             # Kích thước bàn cờ 9x9
TIME_BUDGET = 0.50   # Thời gian tối đa cho phép suy nghĩ (0.5 giây)
TOP_WALLS = 8        # Chỉ giữ lại tối đa 8 tường tiềm năng nhất để phân tích sâu
WALL_COST = 0.35     # Chi phí tài nguyên khi dùng 1 bức tường (tương đương 0.35 bước đi)
BIG_NUM = 99         # Giá trị đại diện cho việc không có đường đi (bị chặn hoàn toàn)

# 4 hướng di chuyển: Lên, Xuống, Trái, Phải
DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1))


# =====================================================================
# 1. CÁC HÀM XỬ LÝ HÌNH HỌC VÀ TƯỜNG
# =====================================================================

def get_wall_edges(orientation, row, col):
    """Lấy danh sách các đoạn cạnh bị chặn bởi tường tại vị trí (row, col)."""
    if orientation == "H":  # Tường Nằm Ngang
        return (((row, col), (row + 1, col)),
                ((row, col + 1), (row + 1, col + 1)))
    # Tường Dọc ("V")
    return (((row, col), (row, col + 1)),
            ((row + 1, col), (row + 1, col + 1)))


def add_wall_to_set(blocked_set, orientation, row, col):
    """Thêm các cạnh bị tường cản vào tập hợp bị chặn (cả 2 chiều di chuyển)."""
    for edge_a, edge_b in get_wall_edges(orientation, row, col):
        blocked_set.add((edge_a, edge_b))
        blocked_set.add((edge_b, edge_a))


def get_all_blocked_edges(walls_state):
    """Tạo tập hợp chứa tất cả các cạnh bị cản bởi toàn bộ tường hiện có trên bàn cờ."""
    blocked = set()
    for r, c in walls_state["horizontal"]:
        add_wall_to_set(blocked, "H", r, c)
    for r, c in walls_state["vertical"]:
        add_wall_to_set(blocked, "V", r, c)
    return blocked


def check_wall_conflict(w1, w2):
    """Kiểm tra xem 2 bức tường w1 và w2 có bị đè lên nhau hoặc cắt nhau không."""
    o1, r1, c1 = w1
    o2, r2, c2 = w2
    if o1 == o2:
        if o1 == "H":
            return r1 == r2 and abs(c1 - c2) <= 1
        return c1 == c2 and abs(r1 - r2) <= 1
    return r1 == r2 and c1 == c2


# =====================================================================
# 2. THUẬT TOÁN BỎ TÚI: BFS TÌM ĐƯỜNG VÀ KHOẢNG CÁCH
# =====================================================================

def get_valid_neighbors(cell, blocked_edges):
    """Tìm các ô láng giềng hợp lệ (nằm trong bàn cờ và không bị tường chặn)."""
    row, col = cell
    for dr, dc in DIRECTIONS:
        nr, nc = row + dr, col + dc
        if 0 <= nr < SIZE and 0 <= nc < SIZE:
            if (cell, (nr, nc)) not in blocked_edges:
                yield (nr, nc)


def get_shortest_path_len(start_pos, goal_row, blocked_edges):
    """BFS: Tính độ dài đường đi ngắn nhất từ start_pos tới hàng goal_row."""
    start_pos = tuple(start_pos)
    if start_pos[0] == goal_row:
        return 0

    queue = deque([(start_pos, 0)])
    visited = {start_pos}

    while queue:
        curr_cell, dist = queue.popleft()
        for nxt in get_valid_neighbors(curr_cell, blocked_edges):
            if nxt in visited:
                continue
            if nxt[0] == goal_row:
                return dist + 1
            visited.add(nxt)
            queue.append((nxt, dist + 1))

    return BIG_NUM  # Không tìm thấy đường đi (bị nhốt hoàn toàn)


def get_opponent_path_edges(opp_pos, opp_goal_row, blocked_edges):
    """Tìm tập hợp các CẠNH nằm trên đường đi ngắn nhất của đối thủ.
    
    Giúp Bot biết chính xác cần đặt tường chặn vào đâu để có hiệu quả tối đa.
    """
    opp_pos = tuple(opp_pos)
    
    # 1. BFS nhiều nguồn từ hàng đích ngược lại để tạo bản đồ khoảng cách tới đích
    dist_to_goal = {}
    q = deque([(opp_goal_row, c) for c in range(SIZE)])
    for r, c in q:
        dist_to_goal[(r, c)] = 0

    while q:
        curr = q.popleft()
        for nxt in get_valid_neighbors(curr, blocked_edges):
            if nxt not in dist_to_goal:
                dist_to_goal[nxt] = dist_to_goal[curr] + 1
                q.append(nxt)

    total_dist = dist_to_goal.get(opp_pos)
    if total_dist is None:
        return set()

    # 2. BFS từ vị trí đối thủ để lọc ra các cạnh thuộc đường đi ngắn nhất
    dist_from_start = {opp_pos: 0}
    q = deque([opp_pos])
    important_edges = set()

    while q:
        curr = q.popleft()
        d = dist_from_start[curr]
        for nxt in get_valid_neighbors(curr, blocked_edges):
            if nxt not in dist_from_start:
                dist_from_start[nxt] = d + 1
                q.append(nxt)
            
            # Cạnh thuộc đường ngắn nhất khi:
            # (Khoảng cách từ điểm xuất phát) + 1 + (Khoảng cách tới đích) == Tổng độ dài
            if dist_from_start[nxt] == d + 1 and d + 1 + dist_to_goal.get(nxt, BIG_NUM) == total_dist:
                edge = (curr, nxt) if curr < nxt else (nxt, curr)
                important_edges.add(edge)

    return important_edges


def is_wall_blocking_edges(wall, edges_set):
    """Kiểm tra xem bức tường này có chặn cạnh nào trong edges_set hay không."""
    o, r, c = wall
    for a, b in get_wall_edges(o, r, c):
        if (a, b) in edges_set or (b, a) in edges_set:
            return True
    return False


# =====================================================================
# 3. LỚP ĐÁP TRẢ CỦA ĐỐI THỦ (MINIMAX LEVEL 2)
# =====================================================================

def evaluate_best_opponent_reply(blocked_edges, my_pos, my_goal, opp_pos, opp_goal, opp_walls_left, valid_walls, deadline):
    """Giả định đối thủ phản công bằng nước đi HOẶC bức tường tốt nhất của họ.
    
    Trả về ĐIỂM SỐ TỆ NHẤT cho Bot sau khi đối thủ phản công.
    """
    my_dist = get_shortest_path_len(my_pos, my_goal, blocked_edges)
    opp_dist = get_shortest_path_len(opp_pos, opp_goal, blocked_edges)

    # Lựa chọn 1 của đối thủ: Di chuyển 1 bước về đích
    # Điểm lợi thế = (Số bước còn lại của đối thủ - 1) - (Số bước còn lại của mình)
    worst_score_for_me = (opp_dist - 1) - my_dist if opp_dist > 1 else -100

    # Lựa chọn 2 của đối thủ: Đặt 1 bức tường chặn mình (nếu còn tường)
    if opp_walls_left > 0 and my_dist < BIG_NUM:
        my_path_edges = get_opponent_path_edges(my_pos, my_goal, blocked_edges)

        for wall in valid_walls:
            if time.perf_counter() > deadline:
                break  # Dừng lại nếu hết thời gian cho phép

            # Chỉ xét tường va chạm trực tiếp với đường đi của mình
            if not is_wall_blocking_edges(wall, my_path_edges):
                continue

            # Giả lập đặt tường của đối thủ
            temp_blocked = set(blocked_edges)
            add_wall_to_set(temp_blocked, *wall)

            new_my_dist = get_shortest_path_len(my_pos, my_goal, temp_blocked)
            new_opp_dist = get_shortest_path_len(opp_pos, opp_goal, temp_blocked)

            # Tường nhốt hoàn toàn 1 trong 2 người là vi phạm luật, bỏ qua
            if new_my_dist >= BIG_NUM or new_opp_dist >= BIG_NUM:
                continue

            # Tính điểm: Đối thủ tốn 1 tường nên cộng thêm WALL_COST cho mình
            score = new_opp_dist - new_my_dist + WALL_COST

            # Đối thủ sẽ chọn nước đi khiến điểm của mình THẤP NHẤT
            if score < worst_score_for_me:
                worst_score_for_me = score

    return worst_score_for_me


# =====================================================================
# 4. HÀM CHÍNH RA QUYẾT ĐỊNH CHO BOT
# =====================================================================

def choose_action(observation):
    start_time = time.perf_counter()
    deadline = start_time + TIME_BUDGET

    # 1. Trích xuất thông tin từ observation
    me = observation["you"]
    opp = observation["opponent"]

    my_goal, opp_goal = me["goal_row"], opp["goal_row"]
    my_pos, opp_pos = tuple(me["position"]), tuple(opp["position"])
    my_walls, opp_walls = me["walls_remaining"], opp["walls_remaining"]
    legal_moves = [tuple(m) for m in observation["legal_moves"]]

    # -----------------------------------------------------------------
    # QUYẾT ĐỊNH NHANH: Đã sát hàng đích thì đi thẳng về đích để thắng!
    # -----------------------------------------------------------------
    for move in legal_moves:
        if move[0] == my_goal:
            return {"type": "move", "to": list(move)}

    # 2. Chuẩn bị dữ liệu cờ
    blocked_edges = get_all_blocked_edges(observation["walls"])
    my_dist = get_shortest_path_len(my_pos, my_goal, blocked_edges)
    opp_dist = get_shortest_path_len(opp_pos, opp_goal, blocked_edges)

    # Tạo danh sách toàn bộ các tường hợp lệ trên bàn cờ
    existing_walls = [("H", r, c) for r, c in observation["walls"]["horizontal"]] + \
                     [("V", r, c) for r, c in observation["walls"]["vertical"]]
    
    all_possible_walls = []
    for o in ("H", "V"):
        for r in range(SIZE - 1):
            for c in range(SIZE - 1):
                wall = (o, r, c)
                if not any(check_wall_conflict(wall, w) for w in existing_walls):
                    all_possible_walls.append(wall)

    candidate_actions = []  # Lưu (điểm_số, độ_ưu_tiên_loại_hành_động, điểm_phụ, hành_động)

    # -----------------------------------------------------------------
    # ĐÁNH GIÁ 1: Xem xét các nước DI CHUYỂN của bản thân
    # -----------------------------------------------------------------
    distances_map = observation["distances"]
    for move in legal_moves:
        if distances_map[move[0]][move[1]] < 0:
            continue

        # Giả lập nước di chuyển này và tìm phản công tốt nhất của đối thủ
        score = evaluate_best_opponent_reply(
            blocked_edges, move, my_goal, 
            opp_pos, opp_goal, opp_walls, 
            all_possible_walls, deadline
        )
        
        # Thưởng nhỏ cho việc di chuyển gần cột trung tâm (cột 4)
        center_bonus = -abs(move[1] - 4)
        candidate_actions.append((score, 0, center_bonus, {"type": "move", "to": list(move)}))

    # -----------------------------------------------------------------
    # ĐÁNH GIÁ 2: Xem xét các nước ĐẶT TƯỜNG để chặn đối thủ
    # -----------------------------------------------------------------
    if my_walls > 0:
        legal_walls = {(w["orientation"], w["row"], w["col"]) for w in observation["legal_walls"]}
        opp_path_edges = get_opponent_path_edges(opp_pos, opp_goal, blocked_edges)

        # Lọc nhanh: Chỉ giữ các tường ĐÈ TRỰC TIẾP lên đường đi ngắn nhất của đối thủ
        quick_wall_candidates = []
        for wall in sorted(legal_walls):
            if not is_wall_blocking_edges(wall, opp_path_edges):
                continue

            temp_blocked = set(blocked_edges)
            add_wall_to_set(temp_blocked, *wall)

            new_opp_d = get_shortest_path_len(opp_pos, opp_goal, temp_blocked)
            new_my_d = get_shortest_path_len(my_pos, my_goal, temp_blocked)

            # Hiệu số tiến triển = (đối thủ bị làm chậm) - (mình bị làm chậm)
            gain = (new_opp_d - opp_dist) - (new_my_d - my_dist)
            quick_wall_candidates.append((gain, new_opp_d, wall, temp_blocked))

        # Sắp xếp các tường theo hiệu quả giảm tốc đối thủ
        quick_wall_candidates.sort(key=lambda x: (-x[0], -x[1]))

        # Lấy TOP bức tường tốt nhất để đánh giá sâu bằng Minimax
        for gain, _, wall, temp_blocked in quick_wall_candidates[:TOP_WALLS]:
            if time.perf_counter() > deadline or gain <= 0:
                break

            # Loại bỏ tường này khỏi danh sách ứng viên tường còn lại của đối thủ
            rest_walls = [w for w in all_possible_walls if not check_wall_conflict(wall, w)]
            
            score = evaluate_best_opponent_reply(
                temp_blocked, my_pos, my_goal, 
                opp_pos, opp_goal, opp_walls, 
                rest_walls, deadline
            )
            score -= WALL_COST  # Trừ điểm vì đã tiêu tốn 1 tài nguyên tường

            candidate_actions.append((
                score, 1, 0, 
                {"type": "wall", "orientation": wall[0], "row": wall[1], "col": wall[2]}
            ))

    # -----------------------------------------------------------------
    # LỰA CHỌN HÀNH ĐỘNG TỐT NHẤT
    # -----------------------------------------------------------------
    if not candidate_actions:
        return {"type": "move", "to": list(legal_moves[0])} if legal_moves else {"type": "pass"}

    # Sắp xếp ưu tiên: Điểm cao nhất > Di chuyển hay Đặt tường > Vị trí tối ưu
    candidate_actions.sort(key=lambda x: (-x[0], x[1], x[2]))
    
    return candidate_actions[0][3]