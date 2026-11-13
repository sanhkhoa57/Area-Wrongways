"""Advanced Wrongway Arena Bot — BFS + greedy scoring + strategic wall placement.

Strategies used:
1. **Immediate win** — if any legal move reaches the goal row, take it.
2. **BFS-based wall evaluation** — simulate each legal wall, compute how much it
   hurts *both* players' shortest path; pick walls that maximally delay the
   opponent while minimally hurting self (Δ_enemy − Δ_self).
3. **Effective greedy move scoring** — actual row-distance to goal is primary.

Key design: place walls near where the opponent needs to pass through (their
approach to their own goal), not far away. Earlier wall placement for more
impact. Only standard library used.
"""

from collections import deque


# ---------------------------------------------------------------------------
# Helper: wall edge representation
# ---------------------------------------------------------------------------

def _add_wall_edges(blocked, orientation, row, col):
    """Add the two edges blocked by a wall to *blocked* (bidirectional)."""
    if orientation == "H":
        pairs = [((row, col), (row + 1, col)),
                 ((row, col + 1), (row + 1, col + 1))]
    else:  # V
        pairs = [((row, col), (row, col + 1)),
                 ((row + 1, col), (row + 1, col + 1))]
    for s, e in pairs:
        blocked.add((s, e))
        blocked.add((e, s))


def _blocked_edges(walls):
    """Return a set of all blocked edges from the *walls* dict."""
    blocked = set()
    for r, c in walls.get("horizontal", []):
        _add_wall_edges(blocked, "H", r, c)
    for r, c in walls.get("vertical", []):
        _add_wall_edges(blocked, "V", r, c)
    return blocked


# ---------------------------------------------------------------------------
# BFS shortest-path (ignoring both pieces; same as Arena's *distances*)
# ---------------------------------------------------------------------------

def _bfs_distance(start_row, start_col, goal_row, size, blocked):
    """BFS from *(start_row, start_col)* to any cell on *goal_row*."""
    queue = deque([(start_row, start_col, 0)])
    visited = {(start_row, start_col)}

    while queue:
        r, c, d = queue.popleft()
        if r == goal_row:
            return d
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < size and 0 <= nc < size:
                if (nr, nc) not in visited and ((r, c), (nr, nc)) not in blocked:
                    visited.add((nr, nc))
                    queue.append((nr, nc, d + 1))
    return -1  # unreachable


# ---------------------------------------------------------------------------
# Core bot logic
# ---------------------------------------------------------------------------

def choose_action(observation):
    moves = observation["legal_moves"]
    legal_walls = observation.get("legal_walls", [])
    you = observation["you"]
    opp = observation["opponent"]
    size = observation["board_size"]
    goal_row = you["goal_row"]
    opp_goal_row = opp["goal_row"]
    walls = observation["walls"]
    distances = observation["distances"]

    # ---- 0. Immediate win check (highest priority) ----
    for move in moves:
        if move[0] == goal_row:
            return {"type": "move", "to": list(move)}

    my_id = observation["player_id"]
    opp_id = 1 - my_id
    you_pos = tuple(you["position"])
    opp_pos = tuple(opp["position"])

    # Arena pre-computed distances (ignoring pieces)
    my_dist = distances[you_pos[0]][you_pos[1]]
    opp_dist = distances[opp_pos[0]][opp_pos[1]]

    walls_remaining = you["walls_remaining"]
    turn = observation["turn"]
    current_player = observation["current_player"]

    # **Actual** row-distance to goal (ignoring walls, just counting rows).
    my_row_dist = abs(goal_row - you_pos[0])
    opp_row_dist = abs(opp_goal_row - opp_pos[0])

    # Effective distance: whoever moves next gets a "free" step closer.
    eff_my  = my_row_dist + (1 if current_player != my_id else 0)
    eff_opp = opp_row_dist + (1 if current_player != opp_id else 0)

    # ---- 1. Wall evaluation via BFS ----
    best_wall = None
    best_gain = -float("inf")

    # Place walls when: opponent is ahead by ≥2 rows AND we have enough walls left.
    # Lowered threshold and earlier start for more aggressive wall placement.
    should_consider_walls = (
        eff_opp < eff_my - 1 and      # opponent significantly ahead in row distance
        walls_remaining >= 2 and       # need at least 2 walls left
        turn >= 3                     # skip first few turns entirely
    )

    if should_consider_walls:
        for wall in legal_walls:
            w_orientation = wall["orientation"]
            w_row, w_col = wall["row"], wall["col"]

            # Compute how close this wall is to the opponent (proximity bonus)
            opp_proximity = abs(w_row - opp_pos[0]) + abs(w_col - opp_pos[1]) * 0.5

            # Simulate placing this wall
            sim_walls = {k: list(v) for k, v in walls.items()}
            sim_key = "horizontal" if w_orientation == "H" else "vertical"
            sim_walls[sim_key].append((w_row, w_col))

            sim_blocked = _blocked_edges(sim_walls)
            my_after  = _bfs_distance(you_pos[0], you_pos[1], goal_row, size, sim_blocked)
            opp_after = _bfs_distance(opp_pos[0], opp_pos[1], opp_goal_row, size, sim_blocked)

            # Gain = how much *worse* it is for opponent vs us
            my_delta  = (my_after if my_after >= 0 else float("inf")) - (my_dist if my_dist >= 0 else 0)
            opp_delta = (opp_after if opp_after >= 0 else float("inf")) - (opp_dist if opp_dist >= 0 else 0)

            gain = opp_delta - my_delta

            # Bonus: prefer walls that don't make opponent unreachable
            if _bfs_distance(opp_pos[0], opp_pos[1], opp_goal_row, size, sim_blocked) >= 0:
                gain += 0.5

            # Extra bonus for walls that actually delay the opponent
            if opp_delta > 0:
                gain += opp_delta * 0.3

            # **Proximity bonus**: prefer walls closer to opponent (they'll be delayed more)
            gain += max(0, (16 - opp_proximity)) * 0.2

            if gain > best_gain:
                best_gain = gain
                best_wall = wall

    # ---- 2. Move scoring — actual row-distance is primary! ----
    def _score_move(move):
        r, c = move
        d_walls = distances[r][c]

        # Primary metric: how many rows remain to goal from this cell
        row_dist = abs(goal_row - r)

        # If Arena says unreachable (no path around walls), heavily penalize.
        if d_walls < 0:
            return float("-inf")

        # Combine: prefer cells closer to goal, use wall-distance as small tiebreaker
        return -(row_dist + 0.1 * d_walls)

    best_move = max(moves, key=_score_move)

    # ---- 3. Decide: wall or move? ----
    if best_wall is not None and best_gain > 1.5:
        return {
            "type": "wall",
            "orientation": best_wall["orientation"],
            "row": best_wall["row"],
            "col": best_wall["col"],
        }

    return {"type": "move", "to": list(best_move)}
