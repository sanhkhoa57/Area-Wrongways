import time
from collections import deque

INF = 10 ** 6
WIN = 1000
DIRECTIONS = [(-1, 0), (1, 0), (0, -1), (0, 1)]

# Giá trị của MỖI TƯỜNG CÒN TRONG TAY. Đây là tham số quan trọng nhất: các tường đặt
# muộn cộng hưởng với hàng rào đã có và có thể kéo dài đường đối thủ 3-6 bước mỗi
# tường, nên bên còn nhiều tường hơn thường thắng. Đo trên 30 ván (đấu với chiến
# thuật C, thế mở huấn luyện): 0,3 -> 15 thắng; 1,0 -> 20; 2,0 -> 23; 3,5 -> 15.
WALL_VALUE = 2.0
# Giá "lõm" của tường (đo trên 60 ván với chiến thuật D: 36 thắng, 24 thua).
# Nếu dư tường cũng tính đủ giá thì kém hơn; nếu tường dư quá rẻ (CAP 3, SLOPE 0.3)
# thì quay lại đốt tường sớm và thua D (14-16).
WALL_CAP = 4           # từ tường thứ 5 trở lên (tường dư) ...
WALL_SLOPE = 0.5       # ... chỉ tính SLOPE lần giá


def wall_units(w):
    return w if w <= WALL_CAP else WALL_CAP + WALL_SLOPE * (w - WALL_CAP)

TEMPO = 0.5
TIME_BUDGET = 0.40     # giây; giới hạn của Arena là 1 giây
MAX_DEPTH = 5
MOVES_PER_NODE = 3     # số nước đi xét ở mỗi nút sâu
POOL_GAIN, POOL_CUT = 6, 3      # tường xét ở các nút sâu: có lợi ngay / chuẩn bị
ROOT_GAIN, ROOT_CUT = 8, 4      # tường xét ở lượt đầu
PATH_K = 3             # ở mỗi nút sâu: xét tường chặn K cạnh đầu của đường ngắn nhất hiện tại
STATIC_POOL = 2        # cộng thêm vài tường tốt nhất tính từ đầu lượt

REPEAT_PENALTY = 0.8   # trừ điểm hành động đã chọn ở đúng thế cờ này trước đó (chống lặp vô tận)

_CHOSEN = {}           # thế cờ -> các hành động đã chọn ở thế cờ đó (bộ nhớ trong một ván)
LAST_DEPTH = 0         # chỉ để thống kê khi thử nghiệm


class _Timeout(Exception):
    pass


def wall_pairs(orientation, row, col):
    if orientation == "H":
        return [((row, col), (row + 1, col)),
                ((row, col + 1), (row + 1, col + 1))]
    return [((row, col), (row, col + 1)),
            ((row + 1, col), (row + 1, col + 1))]


def wall_edges(orientation, row, col):
    edges = set()
    for a, b in wall_pairs(orientation, row, col):
        edges.add((a, b))
        edges.add((b, a))
    return edges


def blocked_from_walls(walls):
    blocked = set()
    for row, col in walls["horizontal"]:
        blocked |= wall_edges("H", row, col)
    for row, col in walls["vertical"]:
        blocked |= wall_edges("V", row, col)
    return blocked


def conflicts(a, b):
    o1, r1, c1 = a
    o2, r2, c2 = b
    if o1 == o2:
        if o1 == "H":
            return r1 == r2 and abs(c1 - c2) <= 1
        return c1 == c2 and abs(r1 - r2) <= 1
    return r1 == r2 and c1 == c2


def bfs_map(sources, size, blocked):
    """BFS nhiều nguồn: dist[r*size+c] = số bước ngắn nhất tới một nguồn (-1 nếu không tới được)."""
    dist = [-1] * (size * size)
    queue = deque()
    for cell in sources:
        dist[cell[0] * size + cell[1]] = 0
        queue.append(cell)
    while queue:
        cell = queue.popleft()
        nd = dist[cell[0] * size + cell[1]] + 1
        for dr, dc in DIRECTIONS:
            nr, nc = cell[0] + dr, cell[1] + dc
            if 0 <= nr < size and 0 <= nc < size and dist[nr * size + nc] < 0 \
                    and (cell, (nr, nc)) not in blocked:
                dist[nr * size + nc] = nd
                queue.append((nr, nc))
    return dist


def goal_map(goal_row, size, blocked):
    return bfs_map([(goal_row, c) for c in range(size)], size, blocked)


def pawn_moves(pos, opp, size, blocked):
    """Đúng luật Arena: đi 1 ô, nhảy qua đối thủ, hoặc đi chéo khi phía sau bị chặn."""
    out = []
    for dr, dc in DIRECTIONS:
        adj = (pos[0] + dr, pos[1] + dc)
        if not (0 <= adj[0] < size and 0 <= adj[1] < size) or (pos, adj) in blocked:
            continue
        if adj != opp:
            out.append(adj)
            continue
        beyond = (adj[0] + dr, adj[1] + dc)
        if 0 <= beyond[0] < size and 0 <= beyond[1] < size and (adj, beyond) not in blocked:
            out.append(beyond)
        else:
            for sdr, sdc in ((dc, dr), (-dc, -dr)):
                beside = (adj[0] + sdr, adj[1] + sdc)
                if 0 <= beside[0] < size and 0 <= beside[1] < size \
                        and (adj, beside) not in blocked:
                    out.append(beside)
    return out


def shortest_path_edges(pos, goal_dist, size, blocked):
    """Tập cạnh (u, v) nằm trên ít nhất một đường ngắn nhất từ pos tới đích."""
    d = goal_dist[pos[0] * size + pos[1]]
    forward = bfs_map([pos], size, blocked)
    edges = set()
    for r in range(size):
        for c in range(size):
            i = r * size + c
            if forward[i] < 0 or forward[i] + goal_dist[i] != d or goal_dist[i] == 0:
                continue
            for dr, dc in DIRECTIONS:
                nr, nc = r + dr, c + dc
                if 0 <= nr < size and 0 <= nc < size and ((r, c), (nr, nc)) not in blocked \
                        and goal_dist[nr * size + nc] == goal_dist[i] - 1:
                    edges.add(((r, c), (nr, nc)))
    return edges


def choose_action(observation):
    global LAST_DEPTH
    deadline = time.perf_counter() + TIME_BUDGET

    you = observation["you"]
    opponent = observation["opponent"]
    size = observation["board_size"]
    moves = observation["legal_moves"]

    # 1) Về đích được ngay thì về luôn.
    for move in moves:
        if move[0] == you["goal_row"]:
            return {"type": "move", "to": list(move)}

    my_pos, my_goal = tuple(you["position"]), you["goal_row"]
    en_pos, en_goal = tuple(opponent["position"]), opponent["goal_row"]
    my_walls, en_walls = you["walls_remaining"], opponent["walls_remaining"]
    blocked = blocked_from_walls(observation["walls"])
    center = size // 2

    placed0 = [("H", r, c) for r, c in observation["walls"]["horizontal"]]
    placed0 += [("V", r, c) for r, c in observation["walls"]["vertical"]]

    state_key = (my_pos, en_pos, len(placed0), my_walls, en_walls)
    history = _CHOSEN.setdefault(state_key, [])

    map_me0 = goal_map(my_goal, size, blocked)
    map_en0 = goal_map(en_goal, size, blocked)
    m0 = map_me0[my_pos[0] * size + my_pos[1]]
    e0 = map_en0[en_pos[0] * size + en_pos[1]]

    # 2) Chọn các tường đáng xét: "có lợi ngay" và "chuẩn bị" (cắt đường ngắn nhất).
    en_path = shortest_path_edges(en_pos, map_en0, size, blocked)
    my_path = shortest_path_edges(my_pos, map_me0, size, blocked)
    legal_for_me = {(w["orientation"], w["row"], w["col"])
                    for w in observation["legal_walls"]}
    my_gain, my_cut, en_gain, en_cut = [], [], [], []
    for orientation in ("H", "V"):
        for row in range(size - 1):
            for col in range(size - 1):
                wall = (orientation, row, col)
                if any(conflicts(wall, other) for other in placed0):
                    continue
                cand = blocked | wall_edges(*wall)
                m_map = goal_map(my_goal, size, cand)
                e_map = goal_map(en_goal, size, cand)
                m = m_map[my_pos[0] * size + my_pos[1]]
                e = e_map[en_pos[0] * size + en_pos[1]]
                if m < 0 or e < 0:
                    continue  # chặn kín đường -> bất hợp lệ
                pairs = wall_pairs(*wall)
                if wall in legal_for_me:
                    if e > e0:
                        my_gain.append(((e - e0) - (m - m0), wall))
                    else:
                        cuts = sum(1 for a, b in pairs if (a, b) in en_path or (b, a) in en_path)
                        if cuts and m == m0:
                            near = abs(row - en_pos[0]) + abs(col - en_pos[1])
                            my_cut.append((cuts * 10 - near, wall))
                if m > m0:
                    en_gain.append(((m - m0) - (e - e0), wall))
                else:
                    cuts = sum(1 for a, b in pairs if (a, b) in my_path or (b, a) in my_path)
                    if cuts and e == e0:
                        near = abs(row - my_pos[0]) + abs(col - my_pos[1])
                        en_cut.append((cuts * 10 - near, wall))
    for lst in (my_gain, my_cut, en_gain, en_cut):
        lst.sort(key=lambda item: -item[0])

    my_pool = [w for _, w in my_gain[:POOL_GAIN]] + [w for _, w in my_cut[:POOL_CUT]]
    en_pool = [w for _, w in en_gain[:POOL_GAIN]] + [w for _, w in en_cut[:POOL_CUT]]
    root_walls = [w for _, w in my_gain[:ROOT_GAIN]] + [w for _, w in my_cut[:ROOT_CUT]]

    my_static = my_pool[:STATIC_POOL]
    en_static = en_pool[:STATIC_POOL]

    def dyn_walls(pos, gmap, static):
        """Tường chặn K cạnh đầu trên một đường ngắn nhất của quân ở `pos` (tính tại nút này)."""
        found, cur = [], pos
        for _ in range(PATH_K):
            d = gmap[cur[0] * size + cur[1]]
            if d <= 0:
                break
            nxt = None
            for dr, dc in DIRECTIONS:
                n = (cur[0] + dr, cur[1] + dc)
                if 0 <= n[0] < size and 0 <= n[1] < size and (cur, n) not in blocked \
                        and gmap[n[0] * size + n[1]] == d - 1:
                    nxt = n
                    break
            if nxt is None:
                break
            if cur[0] != nxt[0]:
                r = min(cur[0], nxt[0])
                found += [("H", r, cur[1]), ("H", r, cur[1] - 1)]
            else:
                c = min(cur[1], nxt[1])
                found += [("V", cur[0], c), ("V", cur[0] - 1, c)]
            cur = nxt
        found = [w for w in found if 0 <= w[1] <= size - 2 and 0 <= w[2] <= size - 2]
        return list(dict.fromkeys(found + static))

    # 3) Tìm kiếm alpha-beta. mover: 0 = mình, 1 = đối thủ.
    def search(depth, ply, mover, pm, pe, wm, we, placed, mapm, mape, alpha, beta):
        if time.perf_counter() > deadline:
            raise _Timeout
        if mapm is None:
            mapm = goal_map(my_goal, size, blocked)
            mape = goal_map(en_goal, size, blocked)
        dm = mapm[pm[0] * size + pm[1]]
        de = mape[pe[0] * size + pe[1]]
        if dm < 0 or de < 0:
            return None  # tường bất hợp lệ

        def leaf():
            lead = (de - dm) + (TEMPO if mover == 0 else -TEMPO)
            return lead + WALL_VALUE * (wall_units(wm) - wall_units(we))

        if depth == 0:
            if mover == 0 and dm == 1:
                return WIN - ply
            if mover == 1 and de == 1:
                return -(WIN - ply)
            return leaf()

        if mover == 0:
            options = pawn_moves(pm, pe, size, blocked)
            if any(mv[0] == my_goal for mv in options):
                return WIN - ply
            options.sort(key=lambda mv: (mapm[mv[0] * size + mv[1]], abs(mv[1] - center)))
            keep = [mv for mv in options if mapm[mv[0] * size + mv[1]] <= dm][:MOVES_PER_NODE]
            keep = keep or options[:1]
            best = -10 * WIN
            for mv in keep:
                value = search(depth - 1, ply + 1, 1, mv, pe, wm, we, placed,
                               mapm, mape, alpha, beta)
                if value is not None:
                    best = max(best, value)
                    alpha = max(alpha, best)
                    if alpha >= beta:
                        return best
            if wm > 0:
                for wall in dyn_walls(pe, mape, my_static):
                    if any(conflicts(wall, other) for other in placed):
                        continue
                    edges = wall_edges(*wall)
                    blocked.update(edges)
                    placed.append(wall)
                    try:
                        value = search(depth - 1, ply + 1, 1, pm, pe, wm - 1, we,
                                       placed, None, None, alpha, beta)
                    finally:
                        blocked.difference_update(edges)
                        placed.pop()
                    if value is None:
                        continue
                    best = max(best, value)
                    alpha = max(alpha, best)
                    if alpha >= beta:
                        break
            return best if best > -10 * WIN else leaf()

        options = pawn_moves(pe, pm, size, blocked)
        if any(mv[0] == en_goal for mv in options):
            return -(WIN - ply)
        options.sort(key=lambda mv: (mape[mv[0] * size + mv[1]], abs(mv[1] - center)))
        keep = [mv for mv in options if mape[mv[0] * size + mv[1]] <= de][:MOVES_PER_NODE]
        keep = keep or options[:1]
        best = 10 * WIN
        for mv in keep:
            value = search(depth - 1, ply + 1, 0, pm, mv, wm, we, placed,
                           mapm, mape, alpha, beta)
            if value is not None:
                best = min(best, value)
                beta = min(beta, best)
                if alpha >= beta:
                    return best
        if we > 0:
            for wall in dyn_walls(pm, mapm, en_static):
                if any(conflicts(wall, other) for other in placed):
                    continue
                edges = wall_edges(*wall)
                blocked.update(edges)
                placed.append(wall)
                try:
                    value = search(depth - 1, ply + 1, 0, pm, pe, wm, we - 1,
                                   placed, None, None, alpha, beta)
                finally:
                    blocked.difference_update(edges)
                    placed.pop()
                if value is None:
                    continue
                best = min(best, value)
                beta = min(beta, best)
                if alpha >= beta:
                    break
        return best if best < 10 * WIN else leaf()

    # 4) Ứng viên ở lượt đầu: nước đi hợp lệ tốt nhất (do Arena xác nhận), rồi các tường.
    ranked_moves = sorted(
        [mv for mv in moves if map_me0[mv[0] * size + mv[1]] >= 0],
        key=lambda mv: (map_me0[mv[0] * size + mv[1]], abs(mv[1] - center)))
    good = [mv for mv in ranked_moves if map_me0[mv[0] * size + mv[1]] <= m0]
    options = [("move", tuple(mv)) for mv in (good or ranked_moves)[:MOVES_PER_NODE]]
    if my_walls > 0:
        options += [("wall", w) for w in root_walls]
    if not options:
        return {"type": "move", "to": list(moves[0])} if moves else {"type": "pass"}

    chosen = options[0]
    reached = 1
    for depth in range(2, MAX_DEPTH + 1):
        best_value, best_option = -10 * WIN, None
        try:
            for option in options:
                kind, data = option
                penalty = REPEAT_PENALTY * history.count(option)
                if kind == "move":
                    value = search(depth - 1, 1, 1, data, en_pos, my_walls, en_walls,
                                   list(placed0), map_me0, map_en0,
                                   best_value + penalty, 10 * WIN)
                else:
                    edges = wall_edges(*data)
                    blocked.update(edges)
                    try:
                        value = search(depth - 1, 1, 1, my_pos, en_pos, my_walls - 1,
                                       en_walls, placed0 + [data], None, None,
                                       best_value + penalty, 10 * WIN)
                    finally:
                        blocked.difference_update(edges)
                if value is not None:
                    value -= penalty
                if value is not None and value > best_value:
                    best_value, best_option = value, option
        except _Timeout:
            break
        if best_option is not None:
            chosen = best_option
            reached = depth
            options.remove(best_option)
            options.insert(0, best_option)
    LAST_DEPTH = reached
    history.append(chosen)

    kind, data = chosen
    if kind == "move":
        return {"type": "move", "to": list(data)}
    return {"type": "wall", "orientation": data[0], "row": data[1], "col": data[2]}
