"""Bài nộp Wrongway Arena, viết theo mẫu TEMPLATE.py.

Chỉ cần hàm choose_action(observation); các hàm còn lại là hàm hỗ trợ trong cùng file.
Chỉ dùng thư viện chuẩn của Python, không cần nhập thư viện của arena.

Ý tưởng chính:
1. Có nước đi tới hàng đích thì đi ngay.
2. Dùng BFS tính đường ngắn nhất tới đích của cả hai người (có xét tường).
3. Với mỗi nước đi / mỗi tường hợp lệ, tính "lợi thế đua"
   = (đường của đối thủ) - (đường của mình), rồi trừ đi thiệt hại lớn nhất
   mà một tường đáp trả của đối thủ có thể gây ra.
4. Chỉ đặt tường khi nó lợi hơn đi tiếp rõ rệt; khi sắp thua cuộc đua thì dùng
   tường để cầm chân đối thủ.
"""

import time
from collections import deque

N = 9
_history = []  # các ô mình đã đứng gần đây, dùng để tránh đi lặp qua lại


def _build_blocked(walls):
    blocked = set()

    def add(r1, c1, r2, c2):
        a, b = r1 * N + c1, r2 * N + c2
        blocked.add((a, b))
        blocked.add((b, a))

    for r, c in walls["horizontal"]:
        add(r, c, r + 1, c)
        add(r, c + 1, r + 1, c + 1)
    for r, c in walls["vertical"]:
        add(r, c, r, c + 1)
        add(r + 1, c, r + 1, c + 1)
    return blocked


def _wall_edges(o, r, c):
    if o == "H":
        pairs = [(r, c, r + 1, c), (r, c + 1, r + 1, c + 1)]
    else:
        pairs = [(r, c, r, c + 1), (r + 1, c, r + 1, c + 1)]
    edges = []
    for r1, c1, r2, c2 in pairs:
        a, b = r1 * N + c1, r2 * N + c2
        edges.append((a, b))
        edges.append((b, a))
    return edges


def _dist_map(blocked, goal_row):
    """Khoảng cách ngắn nhất từ mọi ô tới hàng đích (bỏ qua quân cờ)."""
    dist = [-1] * (N * N)
    q = deque()
    for c in range(N):
        i = goal_row * N + c
        dist[i] = 0
        q.append(i)
    while q:
        cur = q.popleft()
        r, c = divmod(cur, N)
        d = dist[cur] + 1
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= nr < N and 0 <= nc < N:
                nxt = nr * N + nc
                if dist[nxt] == -1 and (cur, nxt) not in blocked:
                    dist[nxt] = d
                    q.append(nxt)
    return dist



def _overlaps(H, V, o, r, c):
    if o == "H":
        return (r, c) in V or (r, c) in H or (r, c - 1) in H or (r, c + 1) in H
    return (r, c) in H or (r, c) in V or (r - 1, c) in V or (r + 1, c) in V


def _path_edges(dm, blocked, start):
    """Các cạnh nằm trên đường ngắn nhất xuất phát từ ô start."""
    edges = set()
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        if dm[cur] <= 0:
            continue
        r, c = divmod(cur, N)
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= nr < N and 0 <= nc < N:
                nxt = nr * N + nc
                if dm[nxt] == dm[cur] - 1 and (cur, nxt) not in blocked:
                    edges.add((cur, nxt))
                    edges.add((nxt, cur))
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
    return edges


def _threat(blocked, H, V, dm, me_i, opp_i, my_goal, opp_goal, d_opp, opp_walls, deadline):
    """Tường tốt nhất của đối thủ làm mình chậm hơn họ bao nhiêu nhịp (tối thiểu 1)."""
    best = 1
    if opp_walls <= 0:
        return best
    pe = _path_edges(dm, blocked, me_i)
    base = dm[me_i]
    for o in "HV":
        for r in range(8):
            for c in range(8):
                if _overlaps(H, V, o, r, c):
                    continue
                we = _wall_edges(o, r, c)
                if not any(e in pe for e in we):
                    continue
                nb = blocked | set(we)
                nm = _dist_map(nb, my_goal)
                a = nm[me_i] - base
                if nm[me_i] < 0 or a <= best:
                    continue
                no = _dist_map(nb, opp_goal)
                if no[opp_i] < 0:
                    continue
                v = a - (no[opp_i] - d_opp)
                if v > best:
                    best = v
        if time.perf_counter() > deadline:
            break
    return best


def _decide(obs):
    global _history
    if obs["turn"] <= 1:
        _history = []

    me = tuple(obs["you"]["position"])
    opp = tuple(obs["opponent"]["position"])
    my_goal = obs["you"]["goal_row"]
    opp_goal = obs["opponent"]["goal_row"]
    walls_left = obs["you"]["walls_remaining"]
    moves = [tuple(m) for m in obs["legal_moves"]]

    # 1. Thắng ngay nếu được.
    for m in moves:
        if m[0] == my_goal:
            return {"type": "move", "to": [m[0], m[1]]}

    blocked = _build_blocked(obs["walls"])
    dm = _dist_map(blocked, my_goal)
    do = _dist_map(blocked, opp_goal)
    d_me = dm[me[0] * N + me[1]]
    d_opp = do[opp[0] * N + opp[1]]

    start = time.perf_counter()
    deadline = start + 0.55
    H = set(tuple(x) for x in obs["walls"]["horizontal"])
    V = set(tuple(x) for x in obs["walls"]["vertical"])
    opp_walls = obs["opponent"]["walls_remaining"]
    me_i = me[0] * N + me[1]
    opp_i = opp[0] * N + opp[1]

    # 2. Ứng viên di chuyển (giá trị = lợi thế đua sau khi đối thủ đáp trả tốt nhất).
    cands = []  # (value, is_wall, tiebreak, action, extra)
    for m in moves:
        d = dm[m[0] * N + m[1]]
        if d < 0:
            continue
        rep = _history[-8:].count(m)
        cands.append([d_opp - d, False, (-rep, -abs(m[1] - 4)), m, None])
    # 3. Ứng viên đặt tường.
    if walls_left > 0:
        for w in obs["legal_walls"]:
            o, r, c = w["orientation"], w["row"], w["col"]
            nb = blocked | set(_wall_edges(o, r, c))
            nm = _dist_map(nb, my_goal)
            no = _dist_map(nb, opp_goal)
            n_me, n_opp = nm[me_i], no[opp_i]
            if n_me < 0 or n_opp < 0:
                continue
            cands.append([n_opp - n_me, True, (n_opp - d_opp, -abs(r - opp[0]) - abs(c - opp[1])),
                          (o, r, c), (nb, nm)])
    if not cands:
        return {"type": "pass"}

    # Lọc sơ bộ: giữ ứng viên tốt nhất theo lợi thế đua 1 bước.
    cands.sort(key=lambda x: (x[0], not x[1], x[2]), reverse=True)
    top_moves = [x for x in cands if not x[1]][:5]
    top_walls = [x for x in cands if x[1]][:7]
    for x in top_moves + top_walls:
        if time.perf_counter() > deadline:
            x.append(1)
            continue
        if x[1]:
            o, r, c = x[3]
            nb, nm = x[4]
            H2, V2 = (H | {(r, c)}, V) if o == "H" else (H, V | {(r, c)})
            t = _threat(nb, H2, V2, nm, me_i, opp_i, my_goal, opp_goal, d_opp, opp_walls, deadline)
        else:
            mi = x[3][0] * N + x[3][1]
            t = _threat(blocked, H, V, dm, mi, opp_i, my_goal, opp_goal, d_opp, opp_walls, deadline)
        x.append(t)
    scored = [(x[0] - x[5], x) for x in top_moves + top_walls]

    mv = [(v, x) for v, x in scored if not x[1]]
    wl = [(v, x) for v, x in scored if x[1]]
    best_mv = max(mv, key=lambda p: (p[0], p[1][2])) if mv else None
    best_wl = max(wl, key=lambda p: (p[0], p[1][2])) if wl else None

    use_wall = False
    if best_wl is not None:
        if best_mv is None:
            use_wall = True
        else:
            v_m, v_w = best_mv[0], best_wl[0]
            if v_m < 0:
                use_wall = v_w >= v_m and v_w >= v_m + (0 if v_w < 0 else 1)
                use_wall = v_w > v_m or (v_w == v_m and best_wl[1][2][0] >= 1)
            else:
                need = 1 if walls_left > 3 else 2
                if v_m >= 3:
                    need += 1
                use_wall = v_w >= v_m + need

    if use_wall:
        o, r, c = best_wl[1][3]
        return {"type": "wall", "orientation": o, "row": r, "col": c}
    if best_mv is not None:
        m = best_mv[1][3]
        _history.append(me)
        return {"type": "move", "to": [m[0], m[1]]}
    return {"type": "pass"}


def choose_action(observation):
    try:
        return _decide(observation)
    except Exception:
        moves = observation.get("legal_moves") or []
        if moves:
            return {"type": "move", "to": list(moves[0])}
        return {"type": "pass"}
