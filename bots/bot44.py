"""Mẫu bài nộp: chỉ cần hoàn thiện hàm choose_action(observation).

Sao chép file này thành MSSV.py rồi thay đổi chiến thuật của bạn.
Không cần cài đặt luật chơi hoặc nhập thư viện của arena.
"""


def choose_action(observation):
    legal_moves = observation.get("legal_moves", [])
    distances = observation.get("distances", [])
    you = observation.get("you", {})
    goal_row = you.get("goal_row")

    # Kiểm tra nếu không có nước đi hợp lệ
    if not legal_moves:
        return {"type": "pass"}

    # 1. Ưu tiên hàng đầu: Đi ngay vào hàng đích nếu ô kề bên là đích
    for move in legal_moves:
        if goal_row is not None and move[0] == goal_row:
            return {"type": "move", "to": list(move)}

    # 2. Chọn nước đi có khoảng cách ngắn nhất và ưu tiên giữ vị trí trung tâm
    best_move = legal_moves[0]
    best_score = float("inf")

    for move in legal_moves:
        row, col = move
        
        # Tra cứu khoảng cách tới đích của ô này
        dist = distances[row][col] if 0 <= row < 9 and 0 <= col < 9 else -1
        
        # Bỏ qua các ô bị tường chặn không thể tới đích (-1)
        if dist < 0:
            continue

        # Điểm đánh giá = Khoảng cách tới đích + Phạt nhẹ nếu ở xa cột trung tâm (cột 4)
        center_penalty = abs(col - 4) * 0.1
        score = dist + center_penalty

        if score < best_score:
            best_score = score
            best_move = move

    return {"type": "move", "to": list(best_move)}
