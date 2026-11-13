"""
Chiến thuật: "nhìn trước vài nước" (tìm kiếm minimax / alpha-beta).

  - Khoảng cách của một quân = số bước ngắn nhất tới hàng đích (BFS).
  - Điểm của một thế cờ, nhìn từ người đang tới lượt:
        (khoảng cách của đối thủ) - (khoảng cách của mình) + 2 * (hơn số tường)
    Điểm càng cao càng có lợi cho người đang tới lượt. Mỗi tường còn lại
    được tính khá giá trị nên agent không đặt tường bừa bãi, chỉ đặt khi
    thật sự làm đối thủ chậm đi nhiều.
  - Mỗi lượt, agent thử các hành động của mình (đi, đứng yên, đặt tường),
    rồi thử các cách đáp trả của đối thủ, rồi nước tiếp theo của mình...
    (đào sâu 2 -> 3 -> 4 nước khi còn thời gian). Chọn hành động có điểm
    tốt nhất khi cả hai bên đều chơi hay nhất.
  - Luật đi (kể cả nhảy qua quân đối thủ) được cài lại trong
    `possible_moves`, nên agent thấy được các tình huống như "ai bước vào
    ô sát đối thủ trước thì bị nhảy qua".
  - Để tìm kiếm nhanh, chỉ thử những tường chặn đúng đường ngắn nhất của
    đối thủ (các tường khác hầu như không có tác dụng).
  - Chỉ dùng thư viện chuẩn, không phụ thuộc file nào khác.
"""

import time
from collections import deque

BOARD_SIZE = 9
DIRECTIONS = [(-1, 0), (1, 0), (0, -1), (0, 1)]
WIN_SCORE = 100.0
TIME_BUDGET = 0.55          # giây
WALL_VALUE = 2.0            # mỗi tường còn lại đáng giá bấy nhiêu "bước đi"
ROOT_WALL_LIMIT = 10        # số tường tối đa thử ở lượt của ta
REPLY_WALL_LIMIT = 5        # số tường tối đa thử ở lượt đáp trả

_visit_count = {}           # ô -> số lần ta đã đứng ở ô đó (tránh đi qua đi lại)


class SearchTimeout(Exception):
    """Hết thời gian suy nghĩ."""

# Tường, cạnh bị chặn, BFS

def wall_edges(orientation, row, col):
    """Hai cạnh bị chặn bởi một tường (theo bảng trong STUDENT_GUIDE)."""
    if orientation == "H":
        return [((row, col), (row + 1, col)),
                ((row, col + 1), (row + 1, col + 1))]
    return [((row, col), (row, col + 1)),
            ((row + 1, col), (row + 1, col + 1))]


def blocked_with_wall(blocked, orientation, row, col):
    """Bản sao của tập cạnh bị chặn, thêm một tường (mỗi cạnh lưu cả hai chiều)."""
    result = set(blocked)
    for a, b in wall_edges(orientation, row, col):
        result.add((a, b))
        result.add((b, a))
    return result


def inside(row, col):
    return 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE


def distance_map(goal_row, blocked):
    """BFS từ cả hàng đích: bảng 9x9 số bước ngắn nhất từ mỗi ô tới hàng đích
    (bỏ qua quân cờ, giống `distances` của Arena)."""
    dist = [[-1] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    queue = deque()
    for col in range(BOARD_SIZE):
        dist[goal_row][col] = 0
        queue.append((goal_row, col))
    while queue:
        row, col = queue.popleft()
        for dr, dc in DIRECTIONS:
            nr, nc = row + dr, col + dc
            if not inside(nr, nc) or dist[nr][nc] != -1:
                continue
            if ((row, col), (nr, nc)) in blocked:
                continue
            dist[nr][nc] = dist[row][col] + 1
            queue.append((nr, nc))
    return dist


def possible_moves(pos, other, blocked):
    """Các ô quân ở `pos` đi tới được khi quân kia đứng ở `other`:
    đi thường, nhảy qua quân kia, hoặc đi chéo khi phía sau bị chặn."""
    result = []
    for dr, dc in DIRECTIONS:
        adj = (pos[0] + dr, pos[1] + dc)
        if not inside(*adj) or (pos, adj) in blocked:
            continue
        if adj != other:
            result.append(adj)
            continue
        beyond = (adj[0] + dr, adj[1] + dc)
        if inside(*beyond) and (adj, beyond) not in blocked:
            result.append(beyond)
        else:
            for sr, sc in ((dc, dr), (-dc, -dr)):
                side = (adj[0] + sr, adj[1] + sc)
                if inside(*side) and (adj, side) not in blocked:
                    result.append(side)
    return result

# Thế cờ trong cây tìm kiếm

class State:
    """Một thế cờ: vị trí hai quân, số tường còn lại, tường đã đặt, và các
    bảng khoảng cách tới đích của cả hai người (chỉ tính lại khi có tường mới)."""

    __slots__ = ("pos", "left", "maps", "blocked", "hwalls", "vwalls")

    def __init__(self, pos, left, maps, blocked, hwalls, vwalls):
        self.pos = pos
        self.left = left
        self.maps = maps
        self.blocked = blocked
        self.hwalls = hwalls
        self.vwalls = vwalls

    def after_move(self, player, cell):
        pos = list(self.pos)
        pos[player] = cell
        return State(pos, self.left, self.maps, self.blocked, self.hwalls, self.vwalls)

    def dist(self, player):
        row, col = self.pos[player]
        return self.maps[player][row][col]


def wall_is_placeable(state, orientation, row, col):
    """Tường không được chồng lấn hoặc cắt ngang tường đã có (luật của Arena)."""
    if not (0 <= row < BOARD_SIZE - 1 and 0 <= col < BOARD_SIZE - 1):
        return False
    if orientation == "H":
        for d in (-1, 0, 1):
            if (row, col + d) in state.hwalls:
                return False
        return (row, col) not in state.vwalls
    for d in (-1, 0, 1):
        if (row + d, col) in state.vwalls:
            return False
    return (row, col) not in state.hwalls


def shortest_path_edges(state, player):
    """Các cạnh trên MỘT đường ngắn nhất của `player` tới đích."""
    dist_map = state.maps[player]
    row, col = state.pos[player]
    edges = []
    while dist_map[row][col] > 0:
        here = dist_map[row][col]
        for dr, dc in DIRECTIONS:
            nr, nc = row + dr, col + dc
            if (inside(nr, nc) and dist_map[nr][nc] == here - 1
                    and ((row, col), (nr, nc)) not in state.blocked):
                edges.append(((row, col), (nr, nc)))
                row, col = nr, nc
                break
        else:
            break
    return edges


def wall_candidates(state, player, limit):
    """Các tường hợp lệ chặn đường ngắn nhất của đối thủ, tốt nhất trước.
    Trả về danh sách (hành động, thế cờ mới)."""
    other = 1 - player
    old_other = state.dist(other)
    old_self = state.dist(player)
    seen = set()
    found = []
    for (r1, c1), (r2, c2) in shortest_path_edges(state, other):
        if r1 != r2:   # cạnh dọc -> bị chặn bởi tường nằm ngang
            top = min(r1, r2)
            anchors = [("H", top, c1), ("H", top, c1 - 1)]
        else:          # cạnh ngang -> bị chặn bởi tường đứng
            anchors = [("V", r1, min(c1, c2)), ("V", r1 - 1, min(c1, c2))]
        for orientation, row, col in anchors:
            if (orientation, row, col) in seen:
                continue
            seen.add((orientation, row, col))
            if not wall_is_placeable(state, orientation, row, col):
                continue
            blocked = blocked_with_wall(state.blocked, orientation, row, col)
            maps = [distance_map(GOALS[0], blocked), distance_map(GOALS[1], blocked)]
            new_other = maps[other][state.pos[other][0]][state.pos[other][1]]
            new_self = maps[player][state.pos[player][0]][state.pos[player][1]]
            if new_other < 0 or new_self < 0:
                continue  # chặn kín đường: không hợp lệ
            gain = (new_other - old_other) - (new_self - old_self)
            if new_other <= old_other:
                continue  # tường không làm đối thủ chậm đi
            hwalls, vwalls = state.hwalls, state.vwalls
            if orientation == "H":
                hwalls = hwalls | {(row, col)}
            else:
                vwalls = vwalls | {(row, col)}
            left = list(state.left)
            left[player] -= 1
            new_state = State(state.pos, left, maps, blocked, hwalls, vwalls)
            found.append(((gain, new_other), ("wall", orientation, row, col), new_state))
    found.sort(key=lambda item: item[0], reverse=True)
    return [(action, new_state) for _, action, new_state in found[:limit]]


GOALS = [0, 8]  # hàng đích của người chơi 0 và 1

# Tìm kiếm negamax với alpha-beta

def evaluate(state, player):
    """Điểm của thế cờ, nhìn từ `player` (người đang tới lượt)."""
    other = 1 - player
    return (state.dist(other) - state.dist(player)
            + WALL_VALUE * (state.left[player] - state.left[other]))


def generate_children(state, player, wall_limit):
    """Các hành động có thể thử ở thế cờ này: đi, đặt tường, đứng yên.
    Trả về (thắng_ngay, danh_sách (hành động, thế cờ mới))."""
    other = 1 - player
    children = []
    my_map = state.maps[player]
    for cell in possible_moves(state.pos[player], state.pos[other], state.blocked):
        if cell[0] == GOALS[player]:
            return cell, None
        children.append((my_map[cell[0]][cell[1]], ("move", cell[0], cell[1]),
                         state.after_move(player, cell)))
    children.sort(key=lambda item: item[0])      # nước gần đích hơn thử trước
    ordered = [(action, child) for _, action, child in children]
    if wall_limit > 0 and state.left[player] > 0:
        ordered.extend(wall_candidates(state, player, wall_limit))
    ordered.append((("pass",), state))
    return None, ordered


def negamax(state, player, depth, alpha, beta, ctx):
    """Điểm của thế cờ cho `player` (đang tới lượt), nhìn trước `depth` nước."""
    if time.perf_counter() > ctx["deadline"]:
        raise SearchTimeout()
    if depth == 0:
        return evaluate(state, player)
    ply = ctx["depth"] - depth           # đã đi bao nhiêu nước kể từ gốc
    wall_limit = REPLY_WALL_LIMIT if ply == 1 else 0   # chỉ thử tường ở nước đáp trả đầu tiên
    winning_cell, children = generate_children(state, player, wall_limit)
    if winning_cell is not None:
        return WIN_SCORE + depth         # có nước tới đích -> thắng
    best = -1e9
    for _action, child in children:
        score = -negamax(child, 1 - player, depth - 1, -beta, -alpha, ctx)
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break                        # cắt tỉa alpha-beta
    return best


def search_root(state, player, depth, ctx):
    """Thử từng hành động của ta; trả về (điểm, hành động) tốt nhất."""
    ctx["depth"] = depth
    winning_cell, children = generate_children(state, player, ROOT_WALL_LIMIT)
    if winning_cell is not None:
        return WIN_SCORE + depth, ("move", winning_cell[0], winning_cell[1])
    best_score, best_action, best_tiebreak = -1e9, None, None
    for action, child in children:
        score = -negamax(child, 1 - player, depth - 1, -1e9, 1e9, ctx)
        # Hòa điểm: bỏ "pass", ưu tiên ô ít đi qua, rồi ô gần cột giữa.
        if action[0] == "move":
            tiebreak = (2, -_visit_count.get((action[1], action[2]), 0), -abs(action[2] - 4))
        elif action[0] == "wall":
            tiebreak = (1, 0, 0)
        else:
            tiebreak = (0, 0, 0)
        if (score > best_score + 1e-9
                or (abs(score - best_score) <= 1e-9 and tiebreak > best_tiebreak)):
            best_score, best_action, best_tiebreak = score, action, tiebreak
    return best_score, best_action

# Hàm chính do Arena gọi

def build_state(observation):
    """Dựng `State` từ observation của Arena."""
    you_id = observation["player_id"]
    me = tuple(observation["you"]["position"])
    opp = tuple(observation["opponent"]["position"])
    hwalls = frozenset(tuple(w) for w in observation["walls"]["horizontal"])
    vwalls = frozenset(tuple(w) for w in observation["walls"]["vertical"])
    blocked = set()
    for row, col in hwalls:
        for a, b in wall_edges("H", row, col):
            blocked.add((a, b))
            blocked.add((b, a))
    for row, col in vwalls:
        for a, b in wall_edges("V", row, col):
            blocked.add((a, b))
            blocked.add((b, a))
    pos = [None, None]
    left = [0, 0]
    pos[you_id], pos[1 - you_id] = me, opp
    left[you_id] = observation["you"]["walls_remaining"]
    left[1 - you_id] = observation["opponent"]["walls_remaining"]
    maps = [distance_map(GOALS[0], blocked), distance_map(GOALS[1], blocked)]
    return State(pos, left, maps, blocked, hwalls, vwalls)


def to_action(action):
    if action[0] == "move":
        return {"type": "move", "to": [action[1], action[2]]}
    if action[0] == "wall":
        return {"type": "wall", "orientation": action[1], "row": action[2], "col": action[3]}
    return {"type": "pass"}


def is_allowed(action, observation):
    """Kiểm tra lại với danh sách hợp lệ của Arena trước khi trả về."""
    if action[0] == "move":
        return [action[1], action[2]] in observation["legal_moves"]
    if action[0] == "wall":
        return {"orientation": action[1], "row": action[2], "col": action[3]} in observation["legal_walls"]
    return True


def fallback_action(observation):
    """Phương án dự phòng đơn giản: đi ô gần đích nhất."""
    distances = observation["distances"]
    best = None
    for row, col in observation["legal_moves"]:
        d = distances[row][col]
        if d >= 0 and (best is None or d < best[0]):
            best = (d, [row, col])
    if best is not None:
        return {"type": "move", "to": best[1]}
    if observation["legal_moves"]:
        return {"type": "move", "to": list(observation["legal_moves"][0])}
    return {"type": "pass"}


def _decide(observation):
    started = time.perf_counter()
    if observation["turn"] < 2:
        _visit_count.clear()

    my_pos = tuple(observation["you"]["position"])
    _visit_count[my_pos] = _visit_count.get(my_pos, 0) + 1

    player = observation["player_id"]
    state = build_state(observation)

    best_action = None
    # Đào sâu dần: 2 nước, 3 nước, 4 nước... Giữ kết quả của độ sâu cuối cùng hoàn tất.
    for depth in (2, 3, 4, 5):
        ctx = {"deadline": started + TIME_BUDGET}
        try:
            _score, action = search_root(state, player, depth, ctx)
        except SearchTimeout:
            break
        if action is not None and is_allowed(action, observation):
            best_action = action
        if time.perf_counter() - started > TIME_BUDGET / 4:
            break  # độ sâu tiếp theo sẽ tốn gấp nhiều lần
    if best_action is None:
        return fallback_action(observation)
    return to_action(best_action)


def choose_action(observation):
    try:
        return _decide(observation)
    except Exception:
        # Phòng hờ: không để lỗi làm mất lượt.
        return fallback_action(observation)
