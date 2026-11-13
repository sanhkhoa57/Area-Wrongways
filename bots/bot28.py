"""Mẫu bài nộp: chỉ cần hoàn thiện hàm choose_action(observation).

Sao chép file này thành MSSV.py rồi thay đổi chiến thuật của bạn.
Không cần cài đặt luật chơi hoặc nhập thư viện của arena.
"""


import time
from collections import deque

class TimeoutException(Exception):
    """Ngoại lệ để ngắt đệ quy ngay lập tức khi cạn 0.95s."""
    pass

# Hai Hàm giúp Agent đặt tường
def add_wall_edges(blocked, orientation, row, col):
    """Cập nhật bản đồ các cạnh bị bít bởi tường."""
    if orientation == "H":
        pairs = [((row, col), (row + 1, col)), ((row, col + 1), (row + 1, col + 1))]
    else:
        pairs = [((row, col), (row, col + 1)), ((row + 1, col), (row + 1, col + 1))]
    for start, end in pairs:
        blocked.add((start, end))
        blocked.add((end, start))

def get_blocked_edges(walls):
    blocked = set()
    for r, c in walls["horizontal"]: add_wall_edges(blocked, "H", r, c)
    for r, c in walls["vertical"]: add_wall_edges(blocked, "V", r, c)
    return blocked

# Hàm lưu tường trong giả lập
def get_placed_walls(walls):
    placed = set()
    for r, c in walls["horizontal"]: placed.add((r, c, "H"))
    for r, c in walls["vertical"]: placed.add((r, c, "V"))
    return placed

# Hàm tính khoảng cách mỗi ô đến đích
def bfs_distance(position, goal_row, size, blocked):
    """Tính khoảng cách ngắn nhất né tường"""
    start = tuple(position)
    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        current, distance = queue.popleft()
        if current[0] == goal_row: return distance
        row, col = current
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = row + dr, col + dc
            if 0 <= nr < size and 0 <= nc < size:
                neighbor = (nr, nc)
                if neighbor not in visited and (current, neighbor) not in blocked:
                    visited.add(neighbor)
                    queue.append((neighbor, distance + 1))
    return float('inf')

def get_simulated_moves(pos, opp_pos, blocked, size):
    """Sinh các nước đi hợp lệ"""
    r, c = pos
    moves = []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = r + dr, c + dc
        if 0 <= nr < size and 0 <= nc < size and ((r, c), (nr, nc)) not in blocked:
            if (nr, nc) == opp_pos:
                jr, jc = nr + dr, nc + dc
                if 0 <= jr < size and 0 <= jc < size and ((nr, nc), (jr, jc)) not in blocked:
                    moves.append((jr, jc))
                else:
                    for sdr, sdc in [(dc, dr), (-dc, -dr)]:
                        sr, sc = nr + sdr, nc + sdc
                        if 0 <= sr < size and 0 <= sc < size and ((nr, nc), (sr, sc)) not in blocked:
                            moves.append((sr, sc))
            else:
                moves.append((nr, nc))
    return moves

# Lấy hành động tốt nhất để Minimax
def get_dynamic_top_actions(is_my_turn, my_pos, opp_pos, blocked, placed, my_walls, opp_walls, my_goal, opp_goal, size):
    """
    Sinh tối đa 6 hành động (1 Move Greedy + Top 5 Walls) ĐỘNG cho từng trạng thái.
    Hàm này được gọi liên tục ở mỗi nút của Minimax.
    """
    actions = []
    
    # 1. Tìm 1 Nước di chuyển tốt nhất (Greedy Move)
    curr_pos = my_pos if is_my_turn else opp_pos
    curr_opp = opp_pos if is_my_turn else my_pos
    curr_goal = my_goal if is_my_turn else opp_goal
    
    moves = get_simulated_moves(curr_pos, curr_opp, blocked, size)
    best_move = None
    min_d = float('inf')
    for m in moves:
        d = bfs_distance(m, curr_goal, size, blocked)
        if d < min_d:
            min_d = d
            best_move = m
    if best_move:
        actions.append({"type": "move", "to": best_move})
        
    # 2. Tìm TOP 5 bức tường có Gain cao nhất
    walls_left = my_walls if is_my_turn else opp_walls
    if walls_left > 0:
        base_my_dist = bfs_distance(my_pos, my_goal, size, blocked)
        base_opp_dist = bfs_distance(opp_pos, opp_goal, size, blocked)
        
        wall_candidates = []
        for r in range(size - 1):
            for c in range(size - 1):
                for ori in ["H", "V"]:
                    # Lọc đè lấp vật lý
                    if ori == "H" and ((r, c, "H") in placed or (r, c, "V") in placed or (r, c - 1, "H") in placed or (r, c + 1, "H") in placed): continue
                    if ori == "V" and ((r, c, "V") in placed or (r, c, "H") in placed or (r - 1, c, "V") in placed or (r + 1, c, "V") in placed): continue
                    
                    cand_blocked = blocked.copy()
                    add_wall_edges(cand_blocked, ori, r, c)
                    
                    # Tính khoảng cách mới
                    m_d = bfs_distance(my_pos, my_goal, size, cand_blocked)
                    o_d = bfs_distance(opp_pos, opp_goal, size, cand_blocked)
                    
                    if m_d != float('inf') and o_d != float('inf'): # Lọc bít đường
                        if is_my_turn:
                            # Gain của Mình = (Địch thiệt hại) - (Mình thiệt hại)
                            gain = (o_d - base_opp_dist) - (m_d - base_my_dist)
                        else:
                            # Gain của Địch = (Mình thiệt hại) - (Địch thiệt hại)
                            gain = (m_d - base_my_dist) - (o_d - base_opp_dist)
                            
                        if gain > 0: # Chỉ lưu tường sinh lời
                            wall_candidates.append((gain, {"type": "wall", "orientation": ori, "row": r, "col": c}))
                            
        # Sắp xếp tường theo Gain giảm dần và lấy tối đa 5 bức
        wall_candidates.sort(key=lambda x: x[0], reverse=True)
        for w in wall_candidates[:5]:
            actions.append(w[1])
            
    return actions

# Hàm Minimax (Giả lập tương lai để trả về nước đi tốt nhất dựa trên Evaluation Bar)
def minimax(depth, is_my_turn, sim_my_pos, sim_opp_pos, sim_blocked, sim_placed, my_walls, opp_walls, my_goal, opp_goal, size, alpha, beta, start_time):
    """Thuật toán Minimax Alpha-Beta."""
    # BẮN LỖI TIMEOUT NẾU CHẠM 0.95s
    if time.time() - start_time > 0.95:
        raise TimeoutException()

    # Tính Evaluation Bar tại độ sâu thấp nhất
    if depth == 0 or sim_my_pos[0] == my_goal or sim_opp_pos[0] == opp_goal:
        m_d = bfs_distance(sim_my_pos, my_goal, size, sim_blocked)
        o_d = bfs_distance(sim_opp_pos, opp_goal, size, sim_blocked)

        # Công thức tính Evaluation Bar
        return (o_d - m_d) + (0.75 * my_walls) - (0.75 * opp_walls)

    # Lấy Top 6 hành động cho trạng thái hiện tại
    actions = get_dynamic_top_actions(is_my_turn, sim_my_pos, sim_opp_pos, sim_blocked, sim_placed, my_walls, opp_walls, my_goal, opp_goal, size)
    
    # Dự phòng nếu bị kẹt
    if not actions:
        m_d = bfs_distance(sim_my_pos, my_goal, size, sim_blocked)
        o_d = bfs_distance(sim_opp_pos, opp_goal, size, sim_blocked)

        return (o_d - m_d) + (0.75 * my_walls) - (0.75 * opp_walls)

    if is_my_turn: # Lượt của mình (Evaluation Bar = Max)
        max_eval = float('-inf')
        for act in actions:
            if act["type"] == "move":
                ev = minimax(depth - 1, False, act["to"], sim_opp_pos, sim_blocked, sim_placed, my_walls, opp_walls, my_goal, opp_goal, size, alpha, beta, start_time)
            else:
                c_block = sim_blocked.copy()
                add_wall_edges(c_block, act["orientation"], act["row"], act["col"])
                c_place = sim_placed.copy()
                c_place.add((act["row"], act["col"], act["orientation"]))
                ev = minimax(depth - 1, False, sim_my_pos, sim_opp_pos, c_block, c_place, my_walls - 1, opp_walls, my_goal, opp_goal, size, alpha, beta, start_time)
                
            max_eval = max(max_eval, ev)
            alpha = max(alpha, ev)
            if beta <= alpha: break 
        return max_eval
        
    else: # Lượt đối thủ (Evaluation Bar = Min)
        min_eval = float('inf')
        for act in actions:
            if act["type"] == "move":
                ev = minimax(depth - 1, True, sim_my_pos, act["to"], sim_blocked, sim_placed, my_walls, opp_walls, my_goal, opp_goal, size, alpha, beta, start_time)
            else:
                c_block = sim_blocked.copy()
                add_wall_edges(c_block, act["orientation"], act["row"], act["col"])
                c_place = sim_placed.copy()
                c_place.add((act["row"], act["col"], act["orientation"]))
                ev = minimax(depth - 1, True, sim_my_pos, sim_opp_pos, c_block, c_place, my_walls, opp_walls - 1, my_goal, opp_goal, size, alpha, beta, start_time)
                
            min_eval = min(min_eval, ev)
            beta = min(beta, ev)
            if beta <= alpha: break 
        return min_eval

def choose_action(observation):
    start_time = time.time() # BẤM GIỜ
    
    size = observation["board_size"]
    my_pos = tuple(observation["you"]["position"])
    opp_pos = tuple(observation["opponent"]["position"])
    my_goal = observation["you"]["goal_row"]
    opp_goal = observation["opponent"]["goal_row"]
    my_walls = observation["you"]["walls_remaining"]
    opp_walls = observation["opponent"]["walls_remaining"]
    
    base_blocked = get_blocked_edges(observation["walls"])
    base_placed = get_placed_walls(observation["walls"])
    legal_moves = observation["legal_moves"]

    if not legal_moves:
        return {"type": "pass"}

    # Thắng luôn nếu có thể về đích
    for move in legal_moves:
        if move[0] == my_goal:
            return {"type": "move", "to": move}

    # BƯỚC 1: Lấy Top 6 hành động cho trạng thái thực tế hiện tại
    root_actions = get_dynamic_top_actions(True, my_pos, opp_pos, base_blocked, base_placed, my_walls, opp_walls, my_goal, opp_goal, size)
    
    if not root_actions:
        return {"type": "move", "to": legal_moves[0]}

    # Đặt sẵn nước tốt nhất (Mặc định là Greedy Move, luôn đứng đầu mảng root_actions)
    best_action = root_actions[0]
    best_eval = float('-inf')
    
    alpha = float('-inf')
    beta = float('inf')

    # CHIỀU SÂU: 4 lớp mô phỏng + 1 lớp gốc = Depth 5
    SEARCH_DEPTH = 5
    SEARCH_DEPTH = SEARCH_DEPTH - 1

    # BƯỚC 2: Chạy Minimax trong vỏ bọc Try...Except
    try:
        for action in root_actions:
            if action["type"] == "move":
                ev = minimax(SEARCH_DEPTH, False, tuple(action["to"]), opp_pos, base_blocked, base_placed, my_walls, opp_walls, my_goal, opp_goal, size, alpha, beta, start_time)
            else:
                sim_blocked = base_blocked.copy()
                add_wall_edges(sim_blocked, action["orientation"], action["row"], action["col"])
                sim_placed = base_placed.copy()
                sim_placed.add((action["row"], action["col"], action["orientation"]))
                ev = minimax(SEARCH_DEPTH, False, my_pos, opp_pos, sim_blocked, sim_placed, my_walls - 1, opp_walls, my_goal, opp_goal, size, alpha, beta, start_time)
                
            if ev > best_eval:
                best_eval = ev
                best_action = action
                
            alpha = max(alpha, best_eval)
            
    except TimeoutException:
        # Nhảy dù an toàn nếu hết 0.95s, trả về nước tốt nhất đã kịp tính (hoặc Greedy nếu chưa tính xong vòng 1)
        pass

    return best_action