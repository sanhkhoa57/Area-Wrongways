"""Mẫu bài nộp: chỉ cần hoàn thiện hàm choose_action(observation).

Sao chép file này thành MSSV.py rồi thay đổi chiến thuật của bạn.
Không cần cài đặt luật chơi hoặc nhập thư viện của arena.
"""

from collections import deque

# thêm các cạnh bị chặn vào tập hợp để fast lookup
def add_wall_edges(blocked, orientation, row, col):
    if orientation == "H":
        pairs = [
            ((row, col), (row + 1, col)),
            ((row, col + 1), (row + 1, col + 1)),
        ]
    else:
        pairs = [
            ((row, col), (row, col + 1)),
            ((row + 1, col), (row + 1, col + 1)),
        ]
    for start, end in pairs:
        blocked.add((start, end))
        blocked.add((end, start))

# tìm các cạnh bị chặn
def get_blocked_edges(walls):
    blocked = set()
    for row, col in walls["horizontal"]:
        add_wall_edges(blocked, "H", row, col)
    for row, col in walls["vertical"]:
        add_wall_edges(blocked, "V", row, col)
    return blocked

# bfs để tìm khoảng cách ngắn nhất đến đích
def distance_to_goal(position, goal_row, size, blocked):
    start = tuple(position)
    queue = deque([(start, 0)])
    visited = {start}

    while queue:
        current, distance = queue.popleft()
        row, col = current
        
        if row == goal_row:
            return distance

        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            neighbor = (row + dr, col + dc)
            nr, nc = neighbor
            
            if not (0 <= nr < size and 0 <= nc < size):
                continue
                
            if neighbor in visited or (current, neighbor) in blocked:
                continue
                
            visited.add(neighbor)
            queue.append((neighbor, distance + 1))

    return float('inf')

def choose_action(observation):
    """Đi tới ô hợp lệ có khoảng cách tới đích nhỏ nhất."""
    legal_moves = observation["legal_moves"]
    you = observation["you"]
    opponent = observation["opponent"]
    size = observation["board_size"]
    
    for move in legal_moves:
        if move[0] == you["goal_row"]:
            return {"type": "move", "to": list(move)}
            
    blocked = get_blocked_edges(observation["walls"])
    
    current_own_dist = distance_to_goal(you["position"], you["goal_row"], size, blocked)
    current_enemy_dist = distance_to_goal(opponent["position"], opponent["goal_row"], size, blocked)
    
    best_action = None
    best_score = -float('inf')
    
    center_col = size // 2

    # Mỗi move là một list [hàng, cột]. Khoảng cách -1 là không tới được.
    for move in legal_moves:
        own_dist = distance_to_goal(move, you["goal_row"], size, blocked)
        score = current_enemy_dist - own_dist
        
        centrality = -abs(move[1] - center_col) * 0.1
        score += centrality
        
        if score > best_score:
            best_score = score
            best_action = {"type": "move", "to": list(move)}
            
    if you["walls_remaining"] > 0:
        if current_own_dist > current_enemy_dist:
            wall_penalty = 0.1
        elif current_own_dist == current_enemy_dist:
            wall_penalty = 0.5
        else:
            wall_penalty = 1.0
            
        for wall in observation["legal_walls"]:
            candidate_blocked = blocked.copy()
            add_wall_edges(candidate_blocked, wall["orientation"], wall["row"], wall["col"])
            
            own_after = distance_to_goal(you["position"], you["goal_row"], size, candidate_blocked)
            enemy_after = distance_to_goal(opponent["position"], opponent["goal_row"], size, candidate_blocked)
            
            if own_after == float('inf') or enemy_after == float('inf'):
                continue
                
            score = enemy_after - own_after - wall_penalty
            
            if score > best_score:
                best_score = score
                best_action = {
                    "type": "wall", 
                    "orientation": wall["orientation"], 
                    "row": wall["row"], 
                    "col": wall["col"]
                }
                
    if best_action is not None:
        return best_action
        
    return {"type": "pass"}