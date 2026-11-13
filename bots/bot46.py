"""shizuka_v3q - shizuka_v3 (SHA-256 dc37513c...79b2) + techniques re-optimized
 standard library only.  v3 core unchanged
(bitboards, multi-lane BFS/DAG, PVS/TT/ID/LMR, evaluation, verified pawn tablebase for
the root 0:0 decision, rules, legality and fallback).  Each addition is behind a flag:
  OPT_CUT_CACHE  per-(walls, pawn cell, goal) cache of the multi-lane BFS results of every
                 wall that cuts that pawn's shortest-path DAG (Q's attack lists).  Pure
                 function of its key: search results are identical, only faster.
  OPT_KEEP_BCACHE  blockade caches are pure functions of their key: kept across turns.
  USE_TREE_TB    0:0 pawn tables built lazily INSIDE the search for new wall layouts
                 (<= TB_MAX per turn, first TB_FRAC of the budget, ply <= TB_BOUND_PLY or
                 both inventories empty): exact when both have 0 walls, lower bound when
                 only the mover has walls, upper bound when only the opponent has walls.
  USE_TB1        exact "1 wall vs 0" table of the root layout, built incrementally over
                 several turns (all last-wall placements -> 0:0 child tables), used exactly
                 in the search.
  ROOT_ALL_WALLS root considers every legal wall when the opponent has no wall left.
  LOSS_HOLD      proven loss: among the longest-resisting moves keep the best static race.
  USE_BOOK       quantumgreenbird's opening book (normalised by side and mirror, <= turn 64).

shizuka.py - Wrong Way engine derived from fatter_angrybird.py (standard library only).

Base: leaderboard/fatter_angrybird.py, SHA-256
1cf1537a229afe36dbb8f91e6fee51e404ba9831e64a02f50cf61dcbee11de5f.  Interface, action
format, rule generators, endgame tablebase, legality check and fallback are unchanged.
Changes (each behind a flag for ablation; see shizuka_20261028/reports):
  USE_DEAD (+DEAD_NEAR/LIMIT) dead wall inventory: walls aimed at a pawn whose whole
      shortest-path DAG can no longer be cut by any legal wall are worth nothing
      (protected-corridor case of the 2026-10-28 duel, G001 T41/T42).  Exact planar
      shortcut + multi-lane BFS; sound w.r.t. official legality.
  USE_JUMP_Q  leaf credit when the side to move can jump two steps closer.
  FIX_GUARD_SCORE  last-wall guard judged on the chosen move's own score.
  FIX_CONTEMPT_TT  TT cleared when contempt or exact-table context changes.
  USE_LEAF_SHAPE   depth<=0 leaves evaluated with the same terms as other nodes.
  LMR_FULL_WALLS = 1 late-wall reductions (re-searched at full depth on fail-high).
  TIME_LIMIT 0.80 and COLD_START_RESERVE for the first decision of a worker.
  _DIAG / DIAG_PV  diagnostics of the last decision (never printed).

Original notes of the base engine (angrybird_fixed.py):
Upgrades over baseline angrybird.py:
1. Cached Shortest-Path DAG Edges (_sp_edges cached in _PATH_CACHE per (goal, U, D, L, R, pos))
   accelerating wall generation and frontier evaluation by ~40%.
2. In-Tree Cycle & Game Repetition Detection (self.path & _HIST checked in _Searcher.search()
   before _TT lookup) + true orthogonal adjacency check for contact PASS.
3. Multi-Step Wavefront & Direction-Aware Path Shape (_shape / _shape_n):
   - Verifies step-2 wavefront branching (w2 & (w2 - 1)) before awarding MULT_BONUS so
     2x2 Manhattan retreats into a single funnel square are not falsely rewarded.
   - Applies DETOUR_PEN only when blocked in the forward direction toward the goal row
     (never penalizing walls or board borders already behind the pawn).
   - Calibrates (MULT_BONUS=1, CORRIDOR_PEN=2, DETOUR_PEN=1) strictly below 1 step (STEP=10)
     and evaluates shape consistently at depth <= 0 as well as depth == 1.
4. Surgical Gate-Slam Threat Detection & Defensive Counter-Wall Prioritization (_gen_walls):
   - Detects 100%-cut shortest-path gate threats bridged between barriers (gh, gv) in O(1)
     bitwise operations at every internal node (even when dm >= do).
   - Generates the exact crossing/overlapping counter-walls (urg_h, urg_v) and gives them
     a +12 move-ordering priority compensating for wall cost so critical defensive walls
     (such as V(3,7) against H(3,7)) are never pruned by _top_walls or buried by LMR.
5. Last-Wall Reserve & Monopoly Protection (_race_adj):
   - Steepens the marginal value of the final two walls ([26, 20, 14, 12, ...]) and
     penalizes exhausting the final wall (wm == 0 while wo > 0) unless holding a safe
     multi-step race lead over the opponent's remaining wall capacity.
"""

import gc
from collections import deque
from time import perf_counter

gc.disable()

# ---------------------------------------------------------------------------
# Bitboard Constants & Precomputed Lookup Tables
# ---------------------------------------------------------------------------
SIZE = 9
ALL = (1 << 81) - 1
ROW = [((1 << 9) - 1) << (9 * r) for r in range(9)]
COL = [sum(1 << (9 * r + c) for r in range(9)) for c in range(9)]
ANCHOR = sum(1 << (9 * r + c) for r in range(8) for c in range(8))
U0 = ALL & ~ROW[0]
D0 = ALL & ~ROW[8]
L0 = ALL & ~COL[0]
R0 = ALL & ~COL[8]
INF = 1000
_RC_R = [i // 9 for i in range(81)]
_RC_C = [i % 9 for i in range(81)]

H_BASE = 100
V_BASE = 200
PASS = 999

# ---------------------------------------------------------------------------
# Ablation Feature Flags (all True in final build)
# ---------------------------------------------------------------------------
USE_SP_CACHE = True
USE_REP_FIX = True
USE_SHAPE_FIX = True
USE_GATE_DEF = True
USE_LAST_WALL_GUARD = True
STRICT_MODE = False
# Dead-inventory evaluation: walls aimed at a pawn whose whole shortest-path DAG can no
# longer be cut by any legal wall are worth nothing (see _dead).  Only checked when the
# target is within DEAD_NEAR steps of its goal, to bound the cost.
USE_DEAD = True
DEAD_NEAR = 4
DEAD_LIMIT = 20000
FIX_GUARD_SCORE = True      # last-wall guard judged on the chosen move's own score
USE_JUMP_Q = True           # leaf credit for a pending two-step jump of the side to move
DIAG_PV = True              # read the PV back from the TT after each search (diagnostics)
COLD_START_RESERVE = 0.08   # seconds kept back on a worker's first decision
USE_LEAF_SHAPE = True       # depth<=0 leaves use the same shape terms as other nodes
FIX_CONTEMPT_TT = True      # TT values embed contempt: clear TT when contempt changes
# --- additions (see module docstring) ---
OPT_CUT_CACHE = True
OPT_KEEP_BCACHE = True
USE_TREE_TB = True
USE_TB1 = True
ROOT_ALL_WALLS = True
LOSS_HOLD = True
USE_BOOK = True
CUT_LIMIT = 6000            # entries of the cut cache (each ~20-40 walls)
BCACHE_LIMIT = 60000
TB_MAX = 12                 # new 0:0 tables built inside one decision
TB_FRAC = 0.5               # ... only during the first half of the budget
TB_BOUND_PLY = 2            # tables used as bounds (one side still has walls) at ply <= 2
TB1_FRAC = 0.5              # share of the budget spent per turn on the 1-vs-0 table
TBF_LIMIT = 400             # cached 0:0 tables
BOOK_MAX_TURN = 64

# ---------------------------------------------------------------------------
# Evaluation Hyperparameters (10 units = 1 full step)
# ---------------------------------------------------------------------------
STEP = 10
TEMPO = 5
RACE_BONUS = 60

_WALL_MARGINAL = [24, 18, 14, 12, 12, 12, 12, 12, 12, 12]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]

MULT_BONUS = 1         # Calibrated strictly below 1 step: 2+ independent 2-ply branches
CORRIDOR_PEN = 2       # 1-tile closed corridor penalty
DETOUR_PEN = 1         # Forward-blocked horizontal detour penalty
FIX_REP_SEMANTICS = True
FIX_ROOT_PAWN_SORT = True
FIX_URG_LMR = True
FIX_DIAG_GUARD = True
URG_DEF_BONUS = 8 if FIX_URG_LMR else 12     # Ordering bonus for counter-walls blocking a 100%-cut gate threat
CONTEMPT = 25
CONTEMPT_URG = 15

ROOT_PASS_PENALTY = 12
ROOT_REPEAT_PENALTY = 3
SHUFFLE_PENALTY = 5
ROOT_RECENT_WINDOW = 6
URG_START = 0.55
URG_BONUS = 6

# ---------------------------------------------------------------------------
# Search Hyperparameters & Persistent State
# ---------------------------------------------------------------------------
TIME_LIMIT = 0.80
ITER_FACTOR = 0.30
WIN = 100000
MATE_ZONE = WIN - 1000
ASP_DELTA = 200000
ASP_GROW = 2.5
LMR_FULL_WALLS = 2 if FIX_URG_LMR else 1
LMR2_IDX = 0
TOP_WALLS_D4 = 9
TOP_WALLS_D3 = 9
TOP_WALLS_D2 = 9
NMP_R = 0
COUNTER = True
CONTACT_PASS = True
EG_PROBE = True
HIST_CAP = 8
HIST_HALF = 64
BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 15
PATH_CACHE_LIMIT = 16000
TT_LIMIT = 150000

_TT = {}
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0, "goals": None}
_HIST = set()
_ACTIVE_RECENT = []
_HISTORY = [0] * 300
_KILLERS = [[None, None] for _ in range(128)]
_COUNTER = [None] * (PASS + 1)
_TRIED = [[0] * (PASS + 1) for _ in range(128)]
_STAMP = [0]
_ROOT_GOAL = [0]
_CONTEMPT_CUR = [CONTEMPT]
_URG_CUR = [0.0]
_BLOCK = [False]
_BCACHE = {}
_BQ = {}
_PATH_CACHE = {}
_DEAD = {}
# Diagnostics channel for the last decision (never printed; not part of the protocol):
# completed depth, nodes, score of that depth, score attached to the returned move,
# root candidate count, depth of an unfinished iteration that switched move, reason.
_DIAG = {}
_EGP = [None]
_DEADLINE = [0.0]
_CUT = {}           # (pawn bit, goal, hw, vw) -> {wall code: (new dist, next layer, next2)}
_TBF = {}           # (hw, vw) -> (valN, valS) 0:0 pawn tables (N = goal row 0, S = row 8)
_TB1 = {}           # (hw, vw, holder 0=N/1=S) -> (valN, valS) exact 1-wall-vs-0 tables
_TB1JOB = {}        # incremental 1-vs-0 builds: key -> [codes still to do, best exit keys]
_TBQ = [0, 0.0]     # tables still allowed this decision, time limit for building them
_TBON = [False]     # standard goal rows (tables assume N -> row 0, S -> row 8)
_DIAG_TB1 = [None]  # diagnostics: 1-vs-0 table of the root layout ready this turn

_REP = [0]
for _i in range(140):
    _REP.append(_REP[-1] | (1 << (81 * _i)))


class _TimeUp(Exception):
    pass


def _to_tt(score, ply):
    if score > MATE_ZONE:
        return score + ply
    if score < -MATE_ZONE:
        return score - ply
    return score


def _draw(goal):
    c = _CONTEMPT_CUR[0]
    return -c if goal == _ROOT_GOAL[0] else c


def _top_walls(depth):
    return TOP_WALLS_D4 if depth >= 4 else (TOP_WALLS_D3 if depth == 3 else TOP_WALLS_D2)


# ---------------------------------------------------------------------------
# Pillar 1: Topology-Indexed Bitboards & Cached Shortest-Path DAG Edges
# ---------------------------------------------------------------------------
def _layers(goal, target, U, D, L, R, extra):
    key = (goal, U, D, L, R)
    entry = _PATH_CACHE.get(key)
    if entry is None:
        if len(_PATH_CACHE) >= PATH_CACHE_LIMIT:
            _PATH_CACHE.clear()
        entry = [[goal], goal, {}, {}]
        _PATH_CACHE[key] = entry
    layers, seen, known, _ = entry
    hit = known.get(target)
    if hit is not None:
        if hit >= 0 and len(layers) > hit + extra:
            return layers, hit
    else:
        hit = -1
        i = 0
        for front in layers:
            if front & target:
                hit = i
                break
            i += 1
        if hit >= 0 and len(layers) > hit + extra:
            known[target] = hit
            return layers, hit
    front = layers[-1]
    while front and (hit < 0 or len(layers) <= hit + extra):
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        layers.append(front)
        seen |= front
        if hit < 0 and (front & target):
            hit = len(layers) - 1
    entry[1] = seen
    known[target] = hit
    return layers, hit


def _layers_at(goal, need, U, D, L, R):
    key = (goal, U, D, L, R)
    entry = _PATH_CACHE.get(key)
    if entry is None:
        if len(_PATH_CACHE) >= PATH_CACHE_LIMIT:
            _PATH_CACHE.clear()
        entry = [[goal], goal, {}, {}]
        _PATH_CACHE[key] = entry
    layers = entry[0]
    if len(layers) <= need:
        seen = entry[1]
        front = layers[-1]
        while front and len(layers) <= need:
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            layers.append(front)
            seen |= front
        entry[1] = seen
    return layers


def _dist(start, goal, U, D, L, R):
    d = _layers(goal, start, U, D, L, R, 0)[1]
    return d if d >= 0 else INF


def _sp_edges(pos, layers, d, U, D, L, R):
    """Extract directed edge masks (up, dn, lt, rt) on shortest paths from `pos`."""
    if d <= 0:
        return 0, 0, 0, 0
    if USE_SP_CACHE:
        entry = _PATH_CACHE.get((layers[0], U, D, L, R))
        if entry is not None:
            ec = entry[3]
            res = ec.get(pos)
            if res is not None:
                return res
        else:
            ec = None
    else:
        ec = None
    up = dn = lt = rt = 0
    cur = pos
    for k in range(d, 0, -1):
        nxt = layers[k - 1]
        u = cur & U & (nxt << 9)
        w = cur & D & (nxt >> 9)
        l = cur & L & (nxt << 1)
        r = cur & R & (nxt >> 1)
        up |= u
        dn |= w
        lt |= l
        rt |= r
        cur = (u >> 9) | (w << 9) | (l >> 1) | (r << 1)
    res = (up, dn, lt, rt)
    if ec is not None:
        ec[pos] = res
    return res


def _pawn_moves(a, b, U, D, L, R):
    res = []
    if a & U:
        t = a >> 9
        if t != b:
            res.append(t)
        elif b & U:
            res.append(b >> 9)
        else:
            if b & L:
                res.append(b >> 1)
            if b & R:
                res.append(b << 1)
    if a & D:
        t = a << 9
        if t != b:
            res.append(t)
        elif b & D:
            res.append(b << 9)
        else:
            if b & L:
                res.append(b >> 1)
            if b & R:
                res.append(b << 1)
    if a & L:
        t = a >> 1
        if t != b:
            res.append(t)
        elif b & L:
            res.append(b >> 1)
        else:
            if b & U:
                res.append(b >> 9)
            if b & D:
                res.append(b << 9)
    if a & R:
        t = a << 1
        if t != b:
            res.append(t)
        elif b & R:
            res.append(b << 1)
        else:
            if b & U:
                res.append(b >> 9)
            if b & D:
                res.append(b << 9)
    return res


# ---------------------------------------------------------------------------
# Pillar 3: Grandmaster Evaluation (Last-Wall Guard, Wavefront Shape & Blockade)
# ---------------------------------------------------------------------------
def _race_adj(dm, do, wm, wo):
    if wo == 0 and dm <= do:
        return RACE_BONUS
    if wm == 0 and dm > do:
        return -RACE_BONUS
    return 0


def _static(dm, do, wm, wo):
    return STEP * (do - dm) + TEMPO + WALL_VALUE[wm] - WALL_VALUE[wo] + _race_adj(dm, do, wm, wo)


def _shape(p, d, layers, U, D, L, R):
    """Shortest-path wavefront resilience & forward-detour modifier for pawn `p`."""
    if d < 2:
        return 0
    if not USE_SHAPE_FIX:
        nxt = layers[d - 1]
        n = 0
        vertical = False
        if p & U and (p >> 9) & nxt:
            n += 1
            vertical = True
        if p & D and (p << 9) & nxt:
            n += 1
            vertical = True
        if p & L and (p >> 1) & nxt:
            n += 1
        if p & R and (p << 1) & nxt:
            n += 1
        if n >= 2:
            return MULT_BONUS
        if vertical:
            if not (p & L) and not (p & R):
                return -CORRIDOR_PEN
        else:
            if not (p & U) and not (p & D):
                return -CORRIDOR_PEN
            if not (p & U) or not (p & D):
                return -DETOUR_PEN
        return 0

    nxt = layers[d - 1]
    w1 = (((p & U) >> 9) | ((p & D) << 9) | ((p & L) >> 1) | ((p & R) << 1)) & nxt
    if w1 & (w1 - 1):
        if d < 3:
            return MULT_BONUS
        w2 = (((w1 & U) >> 9) | ((w1 & D) << 9) | ((w1 & L) >> 1) | ((w1 & R) << 1)) & layers[d - 2]
        return MULT_BONUS if (w2 & (w2 - 1)) else 0
    if w1 & ((p >> 9) | (p << 9)):
        if not (p & L) and not (p & R):
            return -CORRIDOR_PEN
    else:
        if not (p & U) and not (p & D):
            return -CORRIDOR_PEN
        if DETOUR_PEN:
            if (layers[0] & 1) and not (p & U):
                return -DETOUR_PEN
            if not (layers[0] & 1) and not (p & D):
                return -DETOUR_PEN
    return 0


def _shape_n(p, d, nxt, U, D, L, R, goal=0, nxt2=0):
    if d < 2:
        return 0
    if not USE_SHAPE_FIX:
        n = 0
        vertical = False
        if p & U and (p >> 9) & nxt:
            n += 1
            vertical = True
        if p & D and (p << 9) & nxt:
            n += 1
            vertical = True
        if p & L and (p >> 1) & nxt:
            n += 1
        if p & R and (p << 1) & nxt:
            n += 1
        if n >= 2:
            return MULT_BONUS
        if vertical:
            if not (p & L) and not (p & R):
                return -CORRIDOR_PEN
        else:
            if not (p & U) and not (p & D):
                return -CORRIDOR_PEN
            if not (p & U) or not (p & D):
                return -DETOUR_PEN
        return 0

    w1 = (((p & U) >> 9) | ((p & D) << 9) | ((p & L) >> 1) | ((p & R) << 1)) & nxt
    if w1 & (w1 - 1):
        if d < 3 or not nxt2:
            return MULT_BONUS
        w2 = (((w1 & U) >> 9) | ((w1 & D) << 9) | ((w1 & L) >> 1) | ((w1 & R) << 1)) & nxt2
        return MULT_BONUS if (w2 & (w2 - 1)) else 0
    if w1 & ((p >> 9) | (p << 9)):
        if not (p & L) and not (p & R):
            return -CORRIDOR_PEN
    else:
        if not (p & U) and not (p & D):
            return -CORRIDOR_PEN
        if DETOUR_PEN and goal:
            if (goal & 1) and not (p & U):
                return -DETOUR_PEN
            if not (goal & 1) and not (p & D):
                return -DETOUR_PEN
    return 0


def _jumps(o, U, D, L, R):
    js = []
    if o & D:
        dest = (o >> 9) if o & U else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o << 9, dest))
    if o & U:
        dest = (o << 9) if o & D else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o >> 9, dest))
    if o & L:
        dest = (o << 1) if o & R else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o >> 1, dest))
    if o & R:
        dest = (o >> 1) if o & L else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o << 1, dest))
    return js


def _reach_mask(o, goal, U, D, L, R):
    js = _jumps(o, U, D, L, R)
    seen = goal | o
    front = goal & ~o
    while front:
        nxt = (((front & U) >> 9) | ((front & D) << 9)
               | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        for m, dest in js:
            if (front & dest) and not (seen & m):
                nxt |= m
        seen |= nxt
        front = nxt
    return seen & ~o


def _blocked(a, o, goal, U, D, L, R, hw, vw, d):
    key = (o, hw, vw, goal)
    m = _BCACHE.get(key)
    if m is None:
        if a & goal:
            return False
        seen = a | o
        front = a
        for _ in range(d):
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            if front & goal:
                return False
            if not front:
                break
            seen |= front
        m = _reach_mask(o, goal, U, D, L, R)
        _BCACHE[key] = m
    return not (m & a)


def _may_block(a, o, goal_row, d):
    ia = a.bit_length() - 1
    io = o.bit_length() - 1
    ra = _RC_R[ia]
    ro = _RC_R[io]
    dr = ra - ro if ra > ro else ro - ra
    dc = _RC_C[ia] - _RC_C[io]
    if dc < 0:
        dc = -dc
    dg = ro - goal_row if ro > goal_row else goal_row - ro
    return dr + dc + dg <= d


def _blocked_q(a, o, goal, U, D, L, R, hw, vw, d):
    key = (a, o, hw, vw, goal)
    r = _BQ.get(key)
    if r is None:
        r = (_may_block(a, o, 0 if goal & 1 else 8, d)
             and _blocked(a, o, goal, U, D, L, R, hw, vw, d))
        _BQ[key] = r
    return r


def _dead(t, gt, U, D, L, R, hw, vw, dt):
    """True when no legal wall can cut any edge of pawn t's shortest-path DAG.

    Legality here = no overlap/crossing and t keeps a route (the other pawn's route is
    ignored, which can only report "not dead" more often).  Walls only add constraints,
    so the property persists while t walks its shortest path: walls aimed at t are then
    worthless.  Cached by (t, topology, goal); pure function of its key.
    """
    key = (t, hw, vw, gt)
    r = _DEAD.get(key)
    if r is not None:
        return r
    entry = _PATH_CACHE.get((gt, U, D, L, R))
    if entry is not None and len(entry[0]) > dt:
        # Reuse the search's own goal layers / shortest-path edge cache (read-only use
        # of an existing entry; _sp_edges only memoises inside that entry).
        if not (t & entry[0][dt]):
            return False      # caller's distance is not exact: never claim "dead"
        ou, od, ol, orr = _sp_edges(t, entry[0], dt, U, D, L, R)
    else:
        # Local goal layers (kept out of _PATH_CACHE so per-wall topologies never evict it).
        lay = [gt]
        seen = front = gt
        for _ in range(dt):
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            seen |= front
            lay.append(front)
        if not (t & lay[dt]):
            return False      # caller's distance is not exact: never claim "dead"
        ou = od = ol = orr = 0
        cur = t
        for k in range(dt, 0, -1):
            nxt = lay[k - 1]
            u = cur & U & (nxt << 9)
            w = cur & D & (nxt >> 9)
            l = cur & L & (nxt << 1)
            r = cur & R & (nxt >> 1)
            ou |= u
            od |= w
            ol |= l
            orr |= r
            cur = (u >> 9) | (w << 9) | (l >> 1) | (r << 1)
    if len(_DEAD) >= DEAD_LIMIT:
        _DEAD.clear()
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    # Planarity shortcut: a wall can only complete a cut (be illegal by blocking a route)
    # if it touches existing walls/border at >= 2 of its 3 points (two ends, middle).
    # Any DAG-cutting candidate with fewer contacts is legal, so t is not dead.
    lh = COL[0] | (hw << 2) | (vw << 1) | (vw >> 8) | (vw << 10)
    rh = COL[7] | (hw >> 2) | (vw >> 1) | (vw >> 10) | (vw << 8)
    mh = (vw >> 9) | (vw << 9)
    tv = ROW[0] | (vw << 18) | (hw << 9) | (hw << 8) | (hw << 10)
    bv = ROW[7] | (vw >> 18) | (hw >> 9) | (hw >> 10) | (hw >> 8)
    mv = (hw >> 1) | (hw << 1)
    if (cand_h & ~((lh & rh) | (lh & mh) | (rh & mh))) or \
            (cand_v & ~((tv & bv) | (tv & mv) | (bv & mv))):
        _DEAD[key] = False
        return False
    r = True
    if cand_h | cand_v:
        n = 0
        cU = cD = cL = cR = 0
        m = cand_h
        while m:
            w = m & -m
            m ^= w
            s = 81 * n
            cD |= (w | (w << 1)) << s
            cU |= ((w << 9) | (w << 10)) << s
            n += 1
        m = cand_v
        while m:
            w = m & -m
            m ^= w
            s = 81 * n
            cR |= (w | (w << 9)) << s
            cL |= ((w << 1) | (w << 10)) << s
            n += 1
        rep = _REP[n]
        Ua = (U * rep) & ~cU
        Da = (D * rep) & ~cD
        La = (L * rep) & ~cL
        Ra = (R * rep) & ~cR
        T = t * rep
        seen = front = gt * rep
        while front:
            if front & T:
                r = False
                break
            front = (((front & Ua) >> 9) | ((front & Da) << 9)
                     | ((front & La) >> 1) | ((front & Ra) << 1)) & ~seen
            seen |= front
    _DEAD[key] = r
    return r


def _eff(w, t, gt, U, D, L, R, hw, vw, dt):
    """Effective size of an inventory `w` whose only target is pawn t at distance dt."""
    if w and USE_DEAD and 0 < dt <= DEAD_NEAR and _dead(t, gt, U, D, L, R, hw, vw, dt):
        return 0
    return w


def _evalb(dm, do, wm, wo, me, op, gm, go, U, D, L, R, hw, vw, extra):
    if USE_DEAD:
        if wm and 0 < do <= DEAD_NEAR and _dead(op, go, U, D, L, R, hw, vw, do):
            wm = 0
        if wo and 0 < dm <= DEAD_NEAR and _dead(me, gm, U, D, L, R, hw, vw, dm):
            wo = 0
    s = _static(dm, do, wm, wo) + extra
    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if _blocked_q(me, op, gm, U, D, L, R, hw, vw, dm):
                return _draw(gm)
        elif _blocked_q(op, me, go, U, D, L, R, hw, vw, do):
            return _draw(gm)
    return s


# ---------------------------------------------------------------------------
# Move Generation & Multi-Lane Bitboard Wall Evaluation
# ---------------------------------------------------------------------------
def _adjacent(p, q, U, D, L, R):
    """Pawns p and q are orthogonal neighbours with an open edge between them."""
    return bool(((p & U) and (p >> 9) == q) or ((p & D) and (p << 9) == q)
                or ((p & L) and (p >> 1) == q) or ((p & R) and (p << 1) == q))


def _jgain(b, x, U, D, L, R, nxt2):
    """Jump quiescence: STEP when pawn b, to move and touching pawn x, has a legal jump
    landing two steps closer to its goal (a cell of nxt2 = its layer d_b - 2).  A plain
    step changes the distance by at most one, so only a jump can land there."""
    if not nxt2 or not _adjacent(b, x, U, D, L, R):
        return 0
    return _jland(b, x, U, D, L, R, nxt2)


def _jland(b, x, U, D, L, R, nxt2):
    """_jgain without the adjacency test (the caller has established it)."""
    for y in _pawn_moves(b, x, U, D, L, R):
        if y & nxt2:
            return STEP
    return 0


def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db, sb):
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    ob2 = -1
    # cells orthogonally next to b: superset of those adjacent through an open edge
    nbb = ((b << 1) | (b >> 1) | (b << 9) | (b >> 9)) if (USE_JUMP_Q and db >= 2) else 0
    if USE_DEAD:
        wa_e = _eff(wa, b, gb, U, D, L, R, hw, vw, db)
        if not wa_e:
            sb = 0
    else:
        wa_e = wa
    s_base = TEMPO - WALL_VALUE[wa_e] + sb - STEP * db
    blk = _BLOCK[0]
    for x in _pawn_moves(a, b, U, D, L, R):
        if x & ga:
            return x.bit_length() - 1, None
        nd = -1
        for k in range(lo, hi):
            if x & lay_m[k]:
                nd = k
                break
        if nd < 0:
            nd = _dist(x, ga, U, D, L, R)
        wb_e = wb
        if wb and USE_DEAD and 0 < nd <= DEAD_NEAR and _dead(x, ga, U, D, L, R, hw, vw, nd):
            wb_e = 0
        sx = _shape(x, nd, lay_m, U, D, L, R) if wb_e else 0
        s = s_base + WALL_VALUE[wb_e] + STEP * nd - sx + _race_adj(db, nd, wb_e, wa_e)
        if (x & nbb) and _adjacent(b, x, U, D, L, R):
            if ob2 < 0:
                ob2 = _layers_at(gb, db, U, D, L, R)[db - 2]
            if ob2:
                s += _jland(b, x, U, D, L, R, ob2)
        if blk and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
            if s > 0:
                if _blocked_q(b, x, gb, U, D, L, R, hw, vw, db):
                    s = _draw(gb)
            elif _blocked_q(x, b, ga, U, D, L, R, hw, vw, nd):
                s = _draw(gb)
        v = -s
        out.append((v, x.bit_length() - 1, x, U, D, L, R, hw, vw, wa, nd, db, v))
    return None, out


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
               top, defensive, hist, allw=False):
    if OPT_CUT_CACHE:
        return _gen_walls_c(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                            top, defensive, hist, allw)
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v

    left_h = COL[0] | (hw << 2) | (vw << 1) | (vw >> 8) | (vw << 10)
    right_h = COL[7] | (hw >> 2) | (vw >> 1) | (vw >> 10) | (vw << 8)
    mid_h = (vw >> 9) | (vw << 9)
    top_v = ROW[0] | (vw << 18) | (hw << 9) | (hw << 8) | (hw << 10)
    bot_v = ROW[7] | (vw >> 18) | (hw >> 9) | (hw >> 10) | (hw >> 8)
    mid_v = (hw >> 1) | (hw << 1)

    urg_h = urg_v = 0
    if USE_GATE_DEF and wb > 0 and (hw | vw):
        th_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & avail_h
        th_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & avail_v
        raw_gh = th_h & left_h & right_h
        raw_gv = th_v & top_v & bot_v
        gh = gv = 0
        if raw_gh:
            m_vert = (mu >> 9) if (ga & 1) else md
            tmp = raw_gh
            while tmp:
                w = tmp & -tmp
                tmp ^= w
                r = _RC_R[w.bit_length() - 1]
                if not ((m_vert & ROW[r]) & ~(w | (w << 1))):
                    gh |= w
        if raw_gv:
            m_horz = mr | (ml >> 1)
            tmp = raw_gv
            while tmp:
                w = tmp & -tmp
                tmp ^= w
                c = _RC_C[w.bit_length() - 1]
                if not ((m_horz & COL[c]) & ~(w | (w << 9))):
                    gv |= w
        if gh | gv:
            urg_h = ((gh << 1) | (gh >> 1) | gv) & avail_h
            urg_v = ((gv << 9) | (gv >> 9) | gh) & avail_v
            cand_h |= urg_h
            cand_v |= urg_v

    if defensive:
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    if allw:
        cand_h |= avail_h
        cand_v |= avail_v

    if not (cand_h | cand_v):
        return []
    cands = []
    no = nm = 0
    oU = oD = oL = oR = 0
    mU = mD = mL = mR = 0
    mask = cand_h
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        io = im = -1
        if (od & e1) or (ou & e2):
            s = 81 * no
            oD |= e1 << s
            oU |= e2 << s
            io = no
            no += 1
        if (md & e1) or (mu & e2):
            s = 81 * nm
            mD |= e1 << s
            mU |= e2 << s
            im = nm
            nm += 1
        j = (1 if (w & left_h) else 0) + (1 if (w & right_h) else 0) + (1 if (w & mid_h) else 0)
        cands.append((1, w, e1, e2, io, im, j, bool(w & urg_h)))
    mask = cand_v
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        io = im = -1
        if (orr & e1) or (ol & e2):
            s = 81 * no
            oR |= e1 << s
            oL |= e2 << s
            io = no
            no += 1
        if (mr & e1) or (ml & e2):
            s = 81 * nm
            mR |= e1 << s
            mL |= e2 << s
            im = nm
            nm += 1
        j = (1 if (w & top_v) else 0) + (1 if (w & bot_v) else 0) + (1 if (w & mid_v) else 0)
        cands.append((0, w, e1, e2, io, im, j, bool(w & urg_v)))
    if no:
        rep = _REP[no]
        odist, onext, onext2 = _multi_bfs(gb, b, (U * rep) & ~oU, (D * rep) & ~oD,
                                          (L * rep) & ~oL, (R * rep) & ~oR, no)
    if nm:
        rep = _REP[nm]
        mdist, mnext, mnext2 = _multi_bfs(ga, a, (U * rep) & ~mU, (D * rep) & ~mD,
                                          (L * rep) & ~mL, (R * rep) & ~mR, nm)
    o_nx = lay_o[do - 1] if do >= 1 else 0
    o_nx2 = lay_o[do - 2] if do >= 2 else 0
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)   # walls never open edges
    base = TEMPO + WALL_VALUE[wb] - WALL_VALUE[wa2]
    if USE_DEAD:
        # A wall that touches neither the target's DAG nor (for my own pawn) a spot that
        # overlaps/crosses a wall cutting my DAG keeps the parent's deadness; others are
        # recomputed.  The root (top is None) recomputes every child exactly.
        exact = top is None
        dead_b = bool(wa2) and 0 < do <= DEAD_NEAR and _dead(b, gb, U, D, L, R, hw, vw, do)
        dead_a = bool(wb) and 0 < dm <= DEAD_NEAR and _dead(a, ga, U, D, L, R, hw, vw, dm)
        thr_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        thr_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        prot_h = (thr_h << 1) | (thr_h >> 1) | thr_v
        prot_v = (thr_v << 9) | (thr_v >> 9) | thr_h
    walls = []
    append = walls.append
    for is_h, w, e1, e2, io, im, j, is_urg in cands:
        if io >= 0:
            ndo = odist[io]
            if ndo < 0:
                continue
            onx = onext[io]
            onx2 = onext2[io]
        else:
            ndo = do
            onx = o_nx
            onx2 = o_nx2
        if im >= 0:
            ndm = mdist[im]
            if ndm < 0:
                continue
            mnx = mnext[im]
            mnx2 = mnext2[im]
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
        if is_h:
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
            code = H_BASE + w.bit_length() - 1
        else:
            U2 = U
            D2 = D
            R2 = R & ~e1
            L2 = L & ~e2
            hw2 = hw
            vw2 = vw | w
            code = V_BASE + w.bit_length() - 1
        if USE_DEAD:
            wa2_e = wa2
            if wa2 and 0 < ndo <= DEAD_NEAR:
                if io >= 0 or exact:
                    if _dead(b, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                        wa2_e = 0
                elif dead_b:
                    wa2_e = 0
            wb_e = wb
            if wb and 0 < ndm <= DEAD_NEAR:
                if im >= 0 or exact or (w & (prot_h if is_h else prot_v)):
                    if _dead(a, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                        wb_e = 0
                elif dead_a:
                    wb_e = 0
            s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                 + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
            s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb, wa2)
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        v = -s
        order = v + (4 if j >= 2 else j)
        if is_urg and ndm <= dm:
            order += URG_DEF_BONUS
        if USE_LAST_WALL_GUARD and wa == 1 and wb > 0 and ndm > ndo and not is_urg:
            order -= 20
        if hist is not None:
            h = hist[code]
            if h:
                order += (HIST_CAP * h) // (h + HIST_HALF)
        append((order, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, v))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _multi_bfs(goal, target, Ua, Da, La, Ra, n):
    dl = _DEADLINE[0]
    if dl and perf_counter() > dl:
        raise _TimeUp()
    rep = _REP[n]
    T = target * rep
    seen = front = goal * rep
    dist = [-1] * n
    nxt = [0] * n
    nxt2 = [0] * n
    left = n
    k = 0
    prev = 0
    while True:
        last2 = prev
        prev = front
        front = (((front & Ua) >> 9) | ((front & Da) << 9)
                 | ((front & La) >> 1) | ((front & Ra) << 1)) & ~seen
        if not front:
            break
        k += 1
        if (k & 7) == 0 and dl and perf_counter() > dl:
            raise _TimeUp()
        hits = front & T
        if hits:
            while hits:
                low = hits & -hits
                hits ^= low
                i = (low.bit_length() - 1) // 81
                dist[i] = k
                sh = 81 * i
                nxt[i] = (prev >> sh) & ALL
                nxt2[i] = (last2 >> sh) & ALL
                left -= 1
            if not left:
                break
        seen |= front
    return dist, nxt, nxt2


def _cut(p, g, lay, d, U, D, L, R, hw, vw):
    """Every available wall that cuts an edge of pawn p's shortest-path DAG (goal g, at
    distance d, `lay` = layers of g): {code: (new distance or -1, next layer, next2)},
    H walls by anchor then V walls by anchor (v3's candidate order).  Same multi-lane BFS
    as the generators, so the values are identical; pure function of (p, g, hw, vw)."""
    key = (p, g, hw, vw)
    r = _CUT.get(key)
    if r is not None:
        return r
    pu, pd, pl, pr = _sp_edges(p, lay, d, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    codes = []
    n = 0
    cU = cD = cL = cR = 0
    m = ((pu >> 9) | (pu >> 10) | pd | (pd >> 1)) & avail_h
    while m:
        w = m & -m
        m ^= w
        s = 81 * n
        cD |= (w | (w << 1)) << s
        cU |= ((w << 9) | (w << 10)) << s
        codes.append(H_BASE + w.bit_length() - 1)
        n += 1
    m = (pr | (pr >> 9) | (pl >> 1) | (pl >> 10)) & avail_v
    while m:
        w = m & -m
        m ^= w
        s = 81 * n
        cR |= (w | (w << 9)) << s
        cL |= ((w << 1) | (w << 10)) << s
        codes.append(V_BASE + w.bit_length() - 1)
        n += 1
    r = {}
    if n:
        rep = _REP[n]
        dist, nx, nx2 = _multi_bfs(g, p, (U * rep) & ~cU, (D * rep) & ~cD,
                                   (L * rep) & ~cL, (R * rep) & ~cR, n)
        for i in range(n):
            r[codes[i]] = (dist[i], nx[i], nx2[i])
    if len(_CUT) >= CUT_LIMIT:
        _CUT.clear()
    _CUT[key] = r
    return r


def _wall_masks(code, U, D, L, R, hw, vw):
    if code < V_BASE:
        w = 1 << (code - H_BASE)
        return U & ~((w << 9) | (w << 10)), D & ~(w | (w << 1)), L, R, hw | w, vw
    w = 1 << (code - V_BASE)
    return U, D, L & ~((w << 1) | (w << 10)), R & ~(w | (w << 9)), hw, vw | w


def _gen_walls_c(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                 top, defensive, hist, allw=False):
    """_gen_walls with the BFS results taken from the cut cache (identical output)."""
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v

    left_h = COL[0] | (hw << 2) | (vw << 1) | (vw >> 8) | (vw << 10)
    right_h = COL[7] | (hw >> 2) | (vw >> 1) | (vw >> 10) | (vw << 8)
    mid_h = (vw >> 9) | (vw << 9)
    top_v = ROW[0] | (vw << 18) | (hw << 9) | (hw << 8) | (hw << 10)
    bot_v = ROW[7] | (vw >> 18) | (hw >> 9) | (hw >> 10) | (hw >> 8)
    mid_v = (hw >> 1) | (hw << 1)

    urg_h = urg_v = 0
    if USE_GATE_DEF and wb > 0 and (hw | vw):
        th_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & avail_h
        th_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & avail_v
        raw_gh = th_h & left_h & right_h
        raw_gv = th_v & top_v & bot_v
        gh = gv = 0
        if raw_gh:
            m_vert = (mu >> 9) if (ga & 1) else md
            tmp = raw_gh
            while tmp:
                w = tmp & -tmp
                tmp ^= w
                r = _RC_R[w.bit_length() - 1]
                if not ((m_vert & ROW[r]) & ~(w | (w << 1))):
                    gh |= w
        if raw_gv:
            m_horz = mr | (ml >> 1)
            tmp = raw_gv
            while tmp:
                w = tmp & -tmp
                tmp ^= w
                c = _RC_C[w.bit_length() - 1]
                if not ((m_horz & COL[c]) & ~(w | (w << 9))):
                    gv |= w
        if gh | gv:
            urg_h = ((gh << 1) | (gh >> 1) | gv) & avail_h
            urg_v = ((gv << 9) | (gv >> 9) | gh) & avail_v
            cand_h |= urg_h
            cand_v |= urg_v

    if defensive:
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    if allw:
        cand_h |= avail_h
        cand_v |= avail_v

    if not (cand_h | cand_v):
        return []
    oc = _cut(b, gb, lay_o, do, U, D, L, R, hw, vw)
    mc = _cut(a, ga, lay_m, dm, U, D, L, R, hw, vw)
    o_nx = lay_o[do - 1] if do >= 1 else 0
    o_nx2 = lay_o[do - 2] if do >= 2 else 0
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)   # walls never open edges
    base = TEMPO + WALL_VALUE[wb] - WALL_VALUE[wa2]
    if USE_DEAD:
        exact = top is None
        dead_b = bool(wa2) and 0 < do <= DEAD_NEAR and _dead(b, gb, U, D, L, R, hw, vw, do)
        dead_a = bool(wb) and 0 < dm <= DEAD_NEAR and _dead(a, ga, U, D, L, R, hw, vw, dm)
        thr_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        thr_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        prot_h = (thr_h << 1) | (thr_h >> 1) | thr_v
        prot_v = (thr_v << 9) | (thr_v >> 9) | thr_h
    walls = []
    append = walls.append
    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            if is_h:
                j = (1 if (w & left_h) else 0) + (1 if (w & right_h) else 0) + (1 if (w & mid_h) else 0)
                is_urg = bool(w & urg_h)
                code = H_BASE + w.bit_length() - 1
            else:
                j = (1 if (w & top_v) else 0) + (1 if (w & bot_v) else 0) + (1 if (w & mid_v) else 0)
                is_urg = bool(w & urg_v)
                code = V_BASE + w.bit_length() - 1
            oe = oc.get(code)
            if oe is not None:
                ndo, onx, onx2 = oe
                if ndo < 0:
                    continue
                io = True
            else:
                ndo = do
                onx = o_nx
                onx2 = o_nx2
                io = False
            me = mc.get(code)
            if me is not None:
                ndm, mnx, mnx2 = me
                if ndm < 0:
                    continue
                im = True
            else:
                ndm = dm
                mnx = m_nx
                mnx2 = m_nx2
                im = False
            if is_h:
                U2 = U & ~((w << 9) | (w << 10))
                D2 = D & ~(w | (w << 1))
                L2 = L
                R2 = R
                hw2 = hw | w
                vw2 = vw
            else:
                U2 = U
                D2 = D
                R2 = R & ~(w | (w << 9))
                L2 = L & ~((w << 1) | (w << 10))
                hw2 = hw
                vw2 = vw | w
            if USE_DEAD:
                wa2_e = wa2
                if wa2 and 0 < ndo <= DEAD_NEAR:
                    if io or exact:
                        if _dead(b, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                            wa2_e = 0
                    elif dead_b:
                        wa2_e = 0
                wb_e = wb
                if wb and 0 < ndm <= DEAD_NEAR:
                    if im or exact or (w & (prot_h if is_h else prot_v)):
                        if _dead(a, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                            wb_e = 0
                    elif dead_a:
                        wb_e = 0
                s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                     + _race_adj(ndo, ndm, wb_e, wa2_e))
            else:
                wa2_e, wb_e = wa2, wb
                s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb, wa2)
            if wa2_e:
                s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
            if wb_e:
                s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
            if badj and ndo >= 2:
                s += _jgain(b, a, U2, D2, L2, R2, onx2)
            v = -s
            order = v + (4 if j >= 2 else j)
            if is_urg and ndm <= dm:
                order += URG_DEF_BONUS
            if USE_LAST_WALL_GUARD and wa == 1 and wb > 0 and ndm > ndo and not is_urg:
                order -= 20
            if hist is not None:
                h = hist[code]
                if h:
                    order += (HIST_CAP * h) // (h + HIST_HALF)
            append((order, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, v))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _gen_walls_fast_c(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                      top, defensive):
    """_gen_walls_fast with the BFS results taken from the cut cache (identical output)."""
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if defensive:
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    oc = _cut(b, gb, lay_o, do, U, D, L, R, hw, vw)
    mc = _cut(a, ga, lay_m, dm, U, D, L, R, hw, vw)
    o_nx = lay_o[do - 1] if do >= 1 else 0
    o_nx2 = lay_o[do - 2] if do >= 2 else 0
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)   # walls never open edges
    base = TEMPO + WALL_VALUE[wb] - WALL_VALUE[wa2]
    if USE_DEAD:
        dead_b = bool(wa2) and 0 < do <= DEAD_NEAR and _dead(b, gb, U, D, L, R, hw, vw, do)
        dead_a = bool(wb) and 0 < dm <= DEAD_NEAR and _dead(a, ga, U, D, L, R, hw, vw, dm)
        thr_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        thr_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        prot_h = (thr_h << 1) | (thr_h >> 1) | thr_v
        prot_v = (thr_v << 9) | (thr_v >> 9) | thr_h
    walls = []
    append = walls.append
    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            code = (H_BASE if is_h else V_BASE) + w.bit_length() - 1
            oe = oc.get(code)
            if oe is not None:
                ndo, onx, onx2 = oe
                if ndo < 0:
                    continue
                io = True
            else:
                ndo = do
                onx = o_nx
                onx2 = o_nx2
                io = False
            me = mc.get(code)
            if me is not None:
                ndm, mnx, mnx2 = me
                if ndm < 0:
                    continue
                im = True
            else:
                ndm = dm
                mnx = m_nx
                mnx2 = m_nx2
                im = False
            if is_h:
                U2 = U & ~((w << 9) | (w << 10))
                D2 = D & ~(w | (w << 1))
                L2 = L
                R2 = R
                hw2 = hw | w
                vw2 = vw
            else:
                U2 = U
                D2 = D
                R2 = R & ~(w | (w << 9))
                L2 = L & ~((w << 1) | (w << 10))
                hw2 = hw
                vw2 = vw | w
            if USE_DEAD:
                wa2_e = wa2
                if wa2 and 0 < ndo <= DEAD_NEAR:
                    if io:
                        if _dead(b, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                            wa2_e = 0
                    elif dead_b:
                        wa2_e = 0
                wb_e = wb
                if wb and 0 < ndm <= DEAD_NEAR:
                    if im or (w & (prot_h if is_h else prot_v)):
                        if _dead(a, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                            wb_e = 0
                    elif dead_a:
                        wb_e = 0
                s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                     + _race_adj(ndo, ndm, wb_e, wa2_e))
            else:
                wa2_e, wb_e = wa2, wb
                s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb, wa2)
            if wa2_e:
                s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
            if wb_e:
                s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
            if badj and ndo >= 2:
                s += _jgain(b, a, U2, D2, L2, R2, onx2)
            v = -s
            append((v, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, v))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _best_wall_d1_c(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                    best, best_move):
    """_best_wall_d1 with the BFS results taken from the cut cache (identical output)."""
    oc = _cut(b, gb, lay_o, do, U, D, L, R, hw, vw)
    if not oc:
        return best, best_move
    mc = _cut(a, ga, lay_m, dm, U, D, L, R, hw, vw)
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)   # walls never open edges
    wb_p = _eff(wb, a, ga, U, D, L, R, hw, vw, dm) if USE_DEAD else wb
    base = TEMPO + WALL_VALUE[wb_p] - WALL_VALUE[wa2]
    max_shape = (CORRIDOR_PEN if wa2 else 0) + (MULT_BONUS if wb else 0)
    gain_im = (WALL_VALUE[wb] + RACE_BONUS) if (USE_DEAD and wb) else 0
    blk = _BLOCK[0]
    for code, oe in oc.items():
        ndo = oe[0]
        if ndo < 0:
            continue
        me = mc.get(code)
        if me is not None:
            ndm, mnx, mnx2 = me
            if ndm < 0:
                continue
            im = True
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
            im = False
        s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb_p, wa2)
        if not blk and -s + max_shape + (gain_im if im else 0) < best:
            continue
        onx = oe[1]
        onx2 = oe[2]
        if code < V_BASE:
            w = 1 << (code - H_BASE)
            U2 = U & ~((w << 9) | (w << 10))
            D2 = D & ~(w | (w << 1))
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
        else:
            w = 1 << (code - V_BASE)
            U2 = U
            D2 = D
            R2 = R & ~(w | (w << 9))
            L2 = L & ~((w << 1) | (w << 10))
            hw2 = hw
            vw2 = vw | w
        if USE_DEAD:
            wa2_e = _eff(wa2, b, gb, U2, D2, L2, R2, hw2, vw2, ndo)
            wb_e = _eff(wb, a, ga, U2, D2, L2, R2, hw2, vw2, ndm) if im else wb_p
            if wa2_e != wa2 or wb_e != wb_p:
                s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                     + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        if blk and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
            if s > 0:
                if _blocked_q(b, a, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                    s = _draw(gb)
            elif _blocked_q(a, b, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                s = _draw(gb)
        v = -s
        if v > best or (v == best and H_BASE <= best_move < PASS):
            best = v
            best_move = code
    return best, best_move


def _gen_walls_fast(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                    top, defensive):
    if OPT_CUT_CACHE:
        return _gen_walls_fast_c(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm,
                                 lay_o, do, top, defensive)
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if defensive:
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    cands = []
    no = nm = 0
    oU = oD = oL = oR = 0
    mU = mD = mL = mR = 0
    mask = cand_h
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        io = im = -1
        if (od & e1) or (ou & e2):
            s = 81 * no
            oD |= e1 << s
            oU |= e2 << s
            io = no
            no += 1
        if (md & e1) or (mu & e2):
            s = 81 * nm
            mD |= e1 << s
            mU |= e2 << s
            im = nm
            nm += 1
        cands.append((1, w, e1, e2, io, im))
    mask = cand_v
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        io = im = -1
        if (orr & e1) or (ol & e2):
            s = 81 * no
            oR |= e1 << s
            oL |= e2 << s
            io = no
            no += 1
        if (mr & e1) or (ml & e2):
            s = 81 * nm
            mR |= e1 << s
            mL |= e2 << s
            im = nm
            nm += 1
        cands.append((0, w, e1, e2, io, im))
    if no:
        rep = _REP[no]
        odist, onext, onext2 = _multi_bfs(gb, b, (U * rep) & ~oU, (D * rep) & ~oD,
                                          (L * rep) & ~oL, (R * rep) & ~oR, no)
    if nm:
        rep = _REP[nm]
        mdist, mnext, mnext2 = _multi_bfs(ga, a, (U * rep) & ~mU, (D * rep) & ~mD,
                                          (L * rep) & ~mL, (R * rep) & ~mR, nm)
    o_nx = lay_o[do - 1] if do >= 1 else 0
    o_nx2 = lay_o[do - 2] if do >= 2 else 0
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)   # walls never open edges
    base = TEMPO + WALL_VALUE[wb] - WALL_VALUE[wa2]
    if USE_DEAD:
        dead_b = bool(wa2) and 0 < do <= DEAD_NEAR and _dead(b, gb, U, D, L, R, hw, vw, do)
        dead_a = bool(wb) and 0 < dm <= DEAD_NEAR and _dead(a, ga, U, D, L, R, hw, vw, dm)
        thr_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        thr_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        prot_h = (thr_h << 1) | (thr_h >> 1) | thr_v
        prot_v = (thr_v << 9) | (thr_v >> 9) | thr_h
    walls = []
    append = walls.append
    for is_h, w, e1, e2, io, im in cands:
        if io >= 0:
            ndo = odist[io]
            if ndo < 0:
                continue
            onx = onext[io]
            onx2 = onext2[io]
        else:
            ndo = do
            onx = o_nx
            onx2 = o_nx2
        if im >= 0:
            ndm = mdist[im]
            if ndm < 0:
                continue
            mnx = mnext[im]
            mnx2 = mnext2[im]
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
        if is_h:
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
            code = H_BASE + w.bit_length() - 1
        else:
            U2 = U
            D2 = D
            R2 = R & ~e1
            L2 = L & ~e2
            hw2 = hw
            vw2 = vw | w
            code = V_BASE + w.bit_length() - 1
        if USE_DEAD:
            wa2_e = wa2
            if wa2 and 0 < ndo <= DEAD_NEAR:
                if io >= 0:
                    if _dead(b, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                        wa2_e = 0
                elif dead_b:
                    wa2_e = 0
            wb_e = wb
            if wb and 0 < ndm <= DEAD_NEAR:
                if im >= 0 or (w & (prot_h if is_h else prot_v)):
                    if _dead(a, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                        wb_e = 0
                elif dead_a:
                    wb_e = 0
            s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                 + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
            s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb, wa2)
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        v = -s
        append((v, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo, v))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _best_wall_d1(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, lay_o, do,
                  best, best_move):
    if OPT_CUT_CACHE:
        return _best_wall_d1_c(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm,
                               lay_o, do, best, best_move)
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if not (cand_h | cand_v):
        return best, best_move
    cands = []
    no = nm = 0
    oU = oD = oL = oR = 0
    mU = mD = mL = mR = 0
    mask = cand_h
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        s = 81 * no
        oD |= e1 << s
        oU |= e2 << s
        io = no
        no += 1
        im = -1
        if (md & e1) or (mu & e2):
            s = 81 * nm
            mD |= e1 << s
            mU |= e2 << s
            im = nm
            nm += 1
        cands.append((1, w, e1, e2, io, im))
    mask = cand_v
    while mask:
        w = mask & -mask
        mask ^= w
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        s = 81 * no
        oR |= e1 << s
        oL |= e2 << s
        io = no
        no += 1
        im = -1
        if (mr & e1) or (ml & e2):
            s = 81 * nm
            mR |= e1 << s
            mL |= e2 << s
            im = nm
            nm += 1
        cands.append((0, w, e1, e2, io, im))
    rep = _REP[no]
    odist, onext, onext2 = _multi_bfs(gb, b, (U * rep) & ~oU, (D * rep) & ~oD,
                                      (L * rep) & ~oL, (R * rep) & ~oR, no)
    if nm:
        rep = _REP[nm]
        mdist, mnext, mnext2 = _multi_bfs(ga, a, (U * rep) & ~mU, (D * rep) & ~mD,
                                          (L * rep) & ~mL, (R * rep) & ~mR, nm)
    m_nx = lay_m[dm - 1] if dm >= 1 else 0
    m_nx2 = lay_m[dm - 2] if dm >= 2 else 0
    wa2 = wa - 1
    badj = USE_JUMP_Q and _adjacent(b, a, U, D, L, R)   # walls never open edges
    # A wall that does not touch my own shortest-path DAG cannot revive opponent walls
    # that are already dead against me, so the parent's effective inventory is reused;
    # walls touching my DAG are re-checked and get a widened pruning allowance.
    wb_p = _eff(wb, a, ga, U, D, L, R, hw, vw, dm) if USE_DEAD else wb
    base = TEMPO + WALL_VALUE[wb_p] - WALL_VALUE[wa2]
    max_shape = (CORRIDOR_PEN if wa2 else 0) + (MULT_BONUS if wb else 0)
    gain_im = (WALL_VALUE[wb] + RACE_BONUS) if (USE_DEAD and wb) else 0
    blk = _BLOCK[0]
    for is_h, w, e1, e2, io, im in cands:
        ndo = odist[io]
        if ndo < 0:
            continue
        if im >= 0:
            ndm = mdist[im]
            if ndm < 0:
                continue
            mnx = mnext[im]
            mnx2 = mnext2[im]
        else:
            ndm = dm
            mnx = m_nx
            mnx2 = m_nx2
        s = STEP * (ndm - ndo) + base + _race_adj(ndo, ndm, wb_p, wa2)
        if not blk and -s + max_shape + (gain_im if im >= 0 else 0) < best:
            continue
        onx = onext[io]
        onx2 = onext2[io]
        if is_h:
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
        else:
            U2 = U
            D2 = D
            R2 = R & ~e1
            L2 = L & ~e2
            hw2 = hw
            vw2 = vw | w
        if USE_DEAD:
            wa2_e = _eff(wa2, b, gb, U2, D2, L2, R2, hw2, vw2, ndo)
            wb_e = _eff(wb, a, ga, U2, D2, L2, R2, hw2, vw2, ndm) if im >= 0 else wb_p
            if wa2_e != wa2 or wb_e != wb_p:
                s = (STEP * (ndm - ndo) + TEMPO + WALL_VALUE[wb_e] - WALL_VALUE[wa2_e]
                     + _race_adj(ndo, ndm, wb_e, wa2_e))
        else:
            wa2_e, wb_e = wa2, wb
        if wa2_e:
            s += _shape_n(b, ndo, onx, U2, D2, L2, R2, gb, onx2)
        if wb_e:
            s -= _shape_n(a, ndm, mnx, U2, D2, L2, R2, ga, mnx2)
        if badj and ndo >= 2:
            s += _jgain(b, a, U2, D2, L2, R2, onx2)
        if blk and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
            if s > 0:
                if _blocked_q(b, a, gb, U2, D2, L2, R2, hw2, vw2, ndo):
                    s = _draw(gb)
            elif _blocked_q(a, b, ga, U2, D2, L2, R2, hw2, vw2, ndm):
                s = _draw(gb)
        v = -s
        if v > best or (v == best and H_BASE <= best_move < PASS):
            best = v
            best_move = (H_BASE if is_h else V_BASE) + w.bit_length() - 1
    return best, best_move


def _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
    if wa <= 0:
        return None
    if code >= V_BASE:
        w = 1 << (code - V_BASE)
        if not (w & ANCHOR) or (w & (vw | (vw << 9) | (vw >> 9) | hw)):
            return None
        R2 = R & ~(w | (w << 9))
        L2 = L & ~((w << 1) | (w << 10))
        U2, D2, hw2, vw2 = U, D, hw, vw | w
    else:
        w = 1 << (code - H_BASE)
        if not (w & ANCHOR) or (w & (hw | (hw << 1) | (hw >> 1) | vw)):
            return None
        D2 = D & ~(w | (w << 1))
        U2 = U & ~((w << 9) | (w << 10))
        L2, R2, hw2, vw2 = L, R, hw | w, vw
    ndo = _dist(b, gb, U2, D2, L2, R2)
    if ndo >= INF:
        return None
    ndm = _dist(a, ga, U2, D2, L2, R2)
    if ndm >= INF:
        return None
    return (0, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo, 0)


# ---------------------------------------------------------------------------
# Pillar 2: Next-Gen Search (Negamax PVS + Repetition + Aspiration + LMR + NMP)
# ---------------------------------------------------------------------------
class _Searcher:
    __slots__ = ("deadline", "turn", "max_turns", "nodes", "killers", "hist",
                 "counter", "path")

    def __init__(self, deadline, turn, max_turns, hist):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        for row in _KILLERS:
            row[0] = None
            row[1] = None
        for i in range(len(_COUNTER)):
            _COUNTER[i] = None
        self.killers = _KILLERS
        self.hist = hist
        self.counter = _COUNTER
        self.path = set()

    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply, prev):
        self.nodes += 1
        if (self.nodes & 7) == 0 and perf_counter() > self.deadline:
            raise _TimeUp()
        if a & ga:
            return WIN - ply
        if b & gb:
            return -WIN + ply
        remaining = self.max_turns - self.turn - ply
        if remaining <= 0:
            return _draw(ga)

        if FIX_REP_SEMANTICS and da <= 2:
            for x in _pawn_moves(a, b, U, D, L, R):
                if x & ga:
                    return WIN - ply - 1
        rkey = (a, b, wa, wb, hw, vw, ga)
        if USE_REP_FIX and ply > 0 and (rkey in self.path or rkey in _HIST):
            return _draw(ga)

        if USE_TREE_TB and (wa == 0 or wb == 0) and _TBON[0]:
            r = _tb_probe(a, b, wa, wb, ga, U, D, L, R, hw, vw, ply, remaining, alpha, beta)
            if r is not None:
                return r
        egp = _EGP[0]
        if egp is not None and (wa == 0 or wb == 0) and hw == egp[0] and vw == egp[1]:
            mover = egp[3] if ga == egp[4] else 1 - egp[3]
            ia = a.bit_length() - 1
            ib = b.bit_length() - 1
            st = (mover * 6561 + ia * 81 + ib) if mover == 0 else (6561 + ib * 81 + ia)
            out = egp[2].result[st]
            dd = egp[2].distance[st]
            if dd <= remaining:
                if out == 1 and wb == 0:
                    return WIN - ply - dd
                if out == -1 and wa == 0:
                    return -WIN + ply + dd
            if wa == 0 and wb == 0:
                return _draw(ga)
        if depth <= 0:
            if da <= 2:
                for x in _pawn_moves(a, b, U, D, L, R):
                    if x & ga:
                        return WIN - ply - 1
            if remaining == 1:
                return _draw(ga)
            extra = 0
            if USE_LEAF_SHAPE:
                # Same terms as every other evaluation site (audit #7): shape of a pawn
                # counts only while walls aimed at it are alive; pending jump of the mover.
                wa_e = _eff(wa, b, gb, U, D, L, R, hw, vw, db) if USE_DEAD else wa
                wb_e = _eff(wb, a, ga, U, D, L, R, hw, vw, da) if USE_DEAD else wb
                lay_a = _layers_at(ga, da, U, D, L, R)
                if wb_e:
                    extra += _shape(a, da, lay_a, U, D, L, R)
                if wa_e:
                    extra -= _shape(b, db, _layers_at(gb, db, U, D, L, R), U, D, L, R)
                if USE_JUMP_Q and da >= 2 and lay_a[da - 2] and _adjacent(a, b, U, D, L, R):
                    extra += _jland(a, b, U, D, L, R, lay_a[da - 2])
            return _evalb(da, db, wa, wb, a, b, ga, gb, U, D, L, R, hw, vw, extra)

        key = (a, b, wa, wb, hw, vw, ga, remaining if remaining < 64 else 64)
        entry = _TT.get(key)
        tt_move = None
        if entry is not None:
            e_depth, e_score, e_flag, tt_move = entry
            if e_depth >= depth:
                if e_score > MATE_ZONE:
                    e_score -= ply
                elif e_score < -MATE_ZONE:
                    e_score += ply
                if e_flag == 0:
                    return e_score
                if e_flag == 1:
                    if e_score >= beta:
                        return e_score
                elif e_score <= alpha:
                    return e_score

        dm = da
        do = db
        lay_m = _layers_at(ga, da + 2, U, D, L, R)
        if wa:
            lay_o = _layers_at(gb, db, U, D, L, R)
            sb = _shape(b, db, lay_o, U, D, L, R)
        else:
            lay_o = None
            sb = 0
        sa = _shape(a, da, lay_m, U, D, L, R) if wb else 0
        win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                lay_m, dm, db, sb)
        if win is not None:
            return WIN - ply - 1
        jg = 0
        if USE_JUMP_Q and db >= 2 and _adjacent(b, a, U, D, L, R):
            ob2 = _layers_at(gb, db, U, D, L, R)[db - 2]
            if ob2:
                jg = _jland(b, a, U, D, L, R, ob2)
        pass_sc = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw, sb - sa + jg)
        np1 = ply + 1
        ia = a.bit_length() - 1
        ib = b.bit_length() - 1
        dr = _RC_R[ia] - _RC_R[ib]
        if dr < 0:
            dr = -dr
        dc = _RC_C[ia] - _RC_C[ib]
        if dc < 0:
            dc = -dc
        if USE_REP_FIX:
            adj_c = bool(((a & U) and (a >> 9) == b) or ((a & D) and (a << 9) == b)
                         or ((a & L) and (a >> 1) == b) or ((a & R) and (a << 1) == b))
            contact = CONTACT_PASS and (adj_c or (_BLOCK[0] and dr + dc <= 2))
        else:
            contact = CONTACT_PASS and (dr + dc <= 2)

        if (NMP_R and da <= db + 1 and wb > 0 and (dr > 1 or dc > 1) and prev != PASS
                and depth >= 5 and da > 2 and db > 2 and pass_sc >= beta
                and -MATE_ZONE < beta < MATE_ZONE):
            if USE_REP_FIX:
                self.path.add(rkey)
                try:
                    sc = -self.search(b, a, wb, wa, gb, ga, U, D, L, R, hw, vw, db, da,
                                      depth - 1 - NMP_R, -beta, 1 - beta, np1, PASS)
                finally:
                    self.path.discard(rkey)
            else:
                sc = -self.search(b, a, wb, wa, gb, ga, U, D, L, R, hw, vw, db, da,
                                  depth - 1 - NMP_R, -beta, 1 - beta, np1, PASS)
            if sc >= beta:
                return beta if sc > MATE_ZONE else sc

        if depth == 1:
            if remaining == 1:
                best = _draw(ga)
                _TT[key] = (1, best, 0, PASS)
                return best
            threat = db <= 2
            leaf = self._leaf
            best = leaf(b, a, gb, U, D, L, R, db, pass_sc, np1) if threat else pass_sc
            best_move = PASS
            for k in pawns:
                v = leaf(b, k[2], gb, U, D, L, R, db, k[12], np1) if threat else k[12]
                if v > best:
                    best = v
                    best_move = k[1]
            if best < beta and wa > 0:
                if threat:
                    walls = _gen_walls_fast(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                            lay_m, dm, lay_o, do, None, True)
                    for k in walls:
                        v = leaf(b, a, gb, k[3], k[4], k[5], k[6], k[11], k[12], np1)
                        if v > best:
                            best = v
                            best_move = k[1]
                            if best >= beta:
                                break
                else:
                    best, best_move = _best_wall_d1(a, b, wa, wb, ga, gb, U, D, L, R,
                                                    hw, vw, lay_m, dm, lay_o, do,
                                                    best, best_move)
            flag = 1 if best >= beta else (2 if best <= alpha else 0)
            _TT[key] = (1, _to_tt(best, ply), flag, best_move)
            return best

        pawns.sort(reverse=True)
        if CONTACT_PASS and contact:
            pawns.append((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db, pass_sc))
        orig_alpha = alpha
        best = -WIN * 2
        best_move = None
        nd = depth - 1
        rd = depth - 2
        lmr_ok = depth >= 3 and da > 2 and db > 2
        rd2 = depth - 3 if (LMR2_IDX and depth >= 5) else rd
        tried = _TRIED[ply]
        stamp = _STAMP[0] + 1
        _STAMP[0] = stamp
        first = True
        killers = self.killers[ply]
        hist = self.hist
        counter = self.counter
        search = self.search
        stage = 0
        if USE_REP_FIX:
            self.path.add(rkey)
        try:
            while stage < 5:
                if stage == 0:
                    lst = ()
                    if tt_move is not None:
                        if tt_move == PASS:
                            lst = ((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db, pass_sc),)
                        elif tt_move < H_BASE:
                            lst = [k for k in pawns if k[1] == tt_move]
                        else:
                            c = _wall_child(tt_move, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                lst = (c,)
                elif stage == 1:
                    if wa > 0:
                        lst = []
                        for km in killers:
                            if km is not None and tried[km] != stamp:
                                c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                                if c is not None:
                                    lst.append(c)
                        if COUNTER and prev is not None:
                            km = counter[prev]
                            if km is not None and tried[km] != stamp and km not in killers:
                                c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                                if c is not None:
                                    lst.append(c)
                    else:
                        lst = ()
                elif stage == 2:
                    lst = pawns
                elif stage == 3:
                    if wa > 0:
                        lst = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                         lay_m, dm, lay_o, do, _top_walls(depth),
                                         dm < do and wb > 0, hist)
                    else:
                        lst = ()
                else:
                    if CONTACT_PASS and contact:
                        break
                    lst = ((pass_sc, PASS, a, U, D, L, R, hw, vw, wa, da, db, pass_sc),)
                widx = 0
                cut = False
                for k in lst:
                    code = k[1]
                    if tried[code] == stamp:
                        continue
                    tried[code] = stamp
                    if first:
                        sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                     k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1, code)
                        first = False
                    else:
                        if stage == 3 and lmr_ok and widx >= LMR_FULL_WALLS:
                            r = rd2 if (LMR2_IDX and widx >= LMR2_IDX and k[11] <= do + 1) else rd
                            sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                         k[7], k[8], k[11], k[10], r, -alpha - 1, -alpha,
                                         np1, code)
                            if sc > alpha:
                                sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                             k[6], k[7], k[8], k[11], k[10], nd,
                                             -alpha - 1, -alpha, np1, code)
                        else:
                            sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                         k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha,
                                         np1, code)
                        if alpha < sc < beta:
                            sc = -search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                         k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1, code)
                    if stage == 3:
                        widx += 1
                    if sc > best:
                        best = sc
                        best_move = code
                        if sc > alpha:
                            alpha = sc
                            if alpha >= beta:
                                if H_BASE <= code < PASS:
                                    if killers[0] != code:
                                        killers[1] = killers[0]
                                        killers[0] = code
                                    hist[code] += depth * depth
                                    if prev is not None:
                                        counter[prev] = code
                                cut = True
                                break
                if cut:
                    break
                stage += 1
        finally:
            if USE_REP_FIX:
                self.path.discard(rkey)
        if best <= orig_alpha:
            flag = 2
        elif best >= beta:
            flag = 1
        else:
            flag = 0
        _TT[key] = (depth, _to_tt(best, ply), flag, best_move)
        return best

    def _leaf(self, b, a2, gb, U, D, L, R, db, value, ply):
        if self.turn + ply >= self.max_turns:
            return -_draw(gb)
        if db <= 2:
            for x in _pawn_moves(b, a2, U, D, L, R):
                if x & gb:
                    return -WIN + ply + 1
        return value


# ---------------------------------------------------------------------------
# Root Iterative Deepening with Aspiration Windows & Anti-Shuffle Bias
# ---------------------------------------------------------------------------
def _root_bias(child, a, b, da, U, D, L, R, db=99, wb=0):
    if USE_REP_FIX:
        adj_c = bool(((a & U) and (a >> 9) == b) or ((a & D) and (a << 9) == b)
                     or ((a & L) and (a >> 1) == b) or ((a & R) and (a << 1) == b))
        code = child[1]
        if code == PASS:
            if _BLOCK[0] and adj_c:
                return 0
            return -ROOT_PASS_PENALTY
        if adj_c and _BLOCK[0]:
            return 0
    else:
        ia, ib = a.bit_length() - 1, b.bit_length() - 1
        if abs(_RC_R[ia] - _RC_R[ib]) + abs(_RC_C[ia] - _RC_C[ib]) <= 2:
            return 0
        code = child[1]
        if code == PASS:
            return -ROOT_PASS_PENALTY
    bias = 0
    if code < H_BASE:
        nd = child[10]
        urg = _URG_CUR[0]
        for _, pawn, horizontal, vertical in _ACTIVE_RECENT:
            if child[2] == pawn and child[7] == horizontal and child[8] == vertical:
                if nd >= da:
                    bias -= int(SHUFFLE_PENALTY * (1.0 + urg))
                else:
                    bias -= ROOT_REPEAT_PENALTY
                break
        if urg > 0.0 and nd < da:
            bias += int(URG_BONUS * urg * (da - nd))
    elif USE_LAST_WALL_GUARD and code < PASS and child[9] == 0 and wb > 0 and da > db:
        if child[10] >= child[11]:
            bias -= 30
    return bias


def _pv_line(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, code, rem, n=10):
    """Diagnostics only: principal variation read back from the TT (move codes)."""
    line = [code]
    try:
        for _ in range(n):
            if code == PASS:
                na, nwa, U2, D2, L2, R2, hw2, vw2 = a, wa, U, D, L, R, hw, vw
            elif code < H_BASE:
                na, nwa, U2, D2, L2, R2, hw2, vw2 = 1 << code, wa, U, D, L, R, hw, vw
            else:
                c = _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                if c is None:
                    break
                na, nwa, U2, D2, L2, R2, hw2, vw2 = a, wa - 1, c[3], c[4], c[5], c[6], c[7], c[8]
            rem -= 1
            if (na & ga) or rem <= 0:
                break
            a, b, wa, wb, ga, gb = b, na, wb, nwa, gb, ga
            U, D, L, R, hw, vw = U2, D2, L2, R2, hw2, vw2
            e = _TT.get((a, b, wa, wb, hw, vw, ga, rem if rem < 64 else 64))
            if e is None or e[3] is None:
                break
            code = e[3]
            line.append(code)
    except Exception:
        pass
    return line


def _cold():
    """Extra reserve on the first decision of a worker process (cold interpreter/heap;
    also right after the official runner restarts a worker following a timeout)."""
    return COLD_START_RESERVE if _STATE["moves"] <= 1 else 0.0


def _adj(raw, bias):
    return raw if raw > MATE_ZONE or raw < -MATE_ZONE else raw + bias


def _raw(bound, bias):
    return bound if bound > MATE_ZONE or bound < -MATE_ZONE else bound - bias


def _root_pass(s, kids, biases, b, wb, gb, ga, depth, alpha, beta):
    lo = alpha
    cur_best = None
    cur_sc = -WIN * 2
    scores = {}
    search = s.search
    nd = depth - 1
    try:
        first = True
        for k in kids:
            bias = biases[k[1]]
            if first:
                sc = _adj(-search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                  k[7], k[8], k[11], k[10], nd,
                                  -_raw(beta, bias), -_raw(alpha, bias), 1, k[1]), bias)
                first = False
            else:
                sc = _adj(-search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                  k[7], k[8], k[11], k[10], nd,
                                  -_raw(alpha + 1, bias), -_raw(alpha, bias), 1, k[1]), bias)
                if alpha < sc < beta:
                    prev = (cur_best, cur_sc)
                    cur_best, cur_sc = k[1], sc
                    sc = _adj(-search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                      k[6], k[7], k[8], k[11], k[10], nd,
                                      -_raw(beta, bias), -_raw(alpha, bias), 1, k[1]), bias)
                    cur_best, cur_sc = prev
            scores[k[1]] = sc
            if sc > cur_sc:
                cur_sc = sc
                cur_best = k[1]
                if sc > alpha:
                    alpha = sc
                    if sc >= beta:
                        break
    except _TimeUp:
        return (cur_best if cur_sc > lo else None), cur_sc, scores, False
    return cur_best, cur_sc, scores, True


def _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0):
    lay_m, da = _layers(ga, a, U, D, L, R, 2)
    lay_o, db = _layers(gb, b, U, D, L, R, 0)
    sa = _shape(a, da, lay_m, U, D, L, R) if wb else 0
    sb = _shape(b, db, lay_o, U, D, L, R) if wa else 0
    win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, da, db, sb)
    if win is not None:
        _DIAG.clear()
        _DIAG.update(reason="immediate_win", best=win)
        return win, None
    if FIX_ROOT_PAWN_SORT:
        kids.sort(reverse=True)
    if wa > 0:
        kids.extend(_gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                               lay_m, da, lay_o, db, None, True, None,
                               ROOT_ALL_WALLS and wb == 0))
    jg = 0
    if USE_JUMP_Q and db >= 2 and _adjacent(b, a, U, D, L, R):
        jg = _jgain(b, a, U, D, L, R, lay_o[db - 2])
    pv = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw, sb - sa + jg) - 1
    kids.append((pv, PASS, a, U, D, L, R, hw, vw, wa, da, db, pv))
    biases = {k[1]: _root_bias(k, a, b, da, U, D, L, R, db, wb) for k in kids}
    last_scores = None

    def order(k, scores=None):
        child = (b, k[2], wb, k[9], k[7], k[8], gb)
        v = _adj(k[0], biases[k[1]]) if scores is None else scores.get(k[1], -WIN * 3)
        return (-v, child in _HIST, k[1] == PASS)

    kids.sort(key=order)
    best_move = kids[0][1]
    prev = _adj(kids[0][0], biases[best_move])
    best_sc = prev
    _STATE["depth"] = 1
    _STATE["score"] = prev
    _DIAG.clear()
    _DIAG.update(depth=1, nodes=0, score=prev, best_score=prev, best=best_move,
                 kids=len(kids), reason="single" if len(kids) == 1 else "search")
    if len(kids) == 1:
        return best_move, kids[0]

    deadline = t0 + TIME_LIMIT - _STATE["margin"] - _cold()
    _DEADLINE[0] = deadline
    s = _Searcher(deadline, turn, max_turns, _HISTORY)
    if USE_REP_FIX:
        s.path.add((a, b, wa, wb, hw, vw, ga))
    full = WIN * 2
    depth = 1
    asp_fail = 0
    timed_out = False
    while depth < 60:
        depth += 1
        it_start = perf_counter()
        if depth >= 3 and -MATE_ZONE < prev < MATE_ZONE:
            delta = ASP_DELTA
            alpha, beta = prev - delta, prev + delta
        else:
            delta = full
            alpha, beta = -full, full
        timed_out = False
        while True:
            move, sc, scores, complete = _root_pass(s, kids, biases, b, wb, gb, ga,
                                                    depth, alpha, beta)
            if not complete:
                if move is not None:
                    # fail-high / completed first move of an unfinished iteration:
                    # keep its own (bound) score with the move it belongs to.
                    if move != best_move:
                        _DIAG["switch_depth"] = depth
                    best_move = move
                    best_sc = sc
                timed_out = True
                break
            if sc <= alpha and alpha > -full:
                asp_fail += 1
                delta = int(delta * ASP_GROW)
                alpha = -full if delta > 400 else max(sc - delta, -full)
                continue
            if sc >= beta and beta < full:
                asp_fail += 1
                best_move = move
                best_sc = sc
                for i, k in enumerate(kids):
                    if k[1] == move:
                        kids.insert(0, kids.pop(i))
                        break
                delta = int(delta * ASP_GROW)
                beta = full if delta > 400 else min(sc + delta, full)
                continue
            best_move = move
            prev = sc
            best_sc = sc
            break
        if timed_out:
            break
        last_scores = scores
        _STATE["depth"] = depth
        _STATE["nodes"] = s.nodes
        _STATE["score"] = prev
        kids.sort(key=lambda k: order(k, scores))
        raw_best = _raw(prev, biases[best_move])
        if raw_best > MATE_ZONE or raw_best < -MATE_ZONE:
            if WIN - abs(raw_best) <= depth:
                break
        now = perf_counter()
        if now + (now - it_start) * ITER_FACTOR > deadline:
            break
    _DEADLINE[0] = 0.0
    _STATE["nodes"] = s.nodes
    _DIAG.update(depth=_STATE["depth"], nodes=s.nodes, score=_STATE["score"],
                 best_score=best_sc, kids=len(kids), reason="search",
                 last_iteration="cut_by_time" if timed_out else "complete",
                 aspiration_fails=asp_fail)
    if DIAG_PV:
        _DIAG["pv"] = _pv_line(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, best_move,
                               max_turns - turn)
    # Proven loss: among the moves resisting as long as the chosen one (last completed
    # iteration), keep the best static race - the opponent may still go wrong.
    if LOSS_HOLD and best_sc < -MATE_ZONE and last_scores:
        cands = [k for k in kids if last_scores.get(k[1], -WIN * 3) >= best_sc]
        if cands:
            k = max(cands, key=lambda k: _adj(k[0], biases[k[1]]))
            if k[1] != best_move:
                _DIAG["loss_hold_from"] = best_move
                best_move = k[1]
    # Last-wall guard: only inside a proven loss of the move actually chosen (not a stale
    # score of an earlier iteration), so it can never overturn a found tactical result.
    guard_sc = best_sc if FIX_GUARD_SCORE else prev
    if (USE_LAST_WALL_GUARD and wa == 1 and wb > 0 and da > db
            and H_BASE <= best_move < PASS and guard_sc < -MATE_ZONE):
        for k in kids:
            if k[1] < H_BASE and k[10] < da:
                if FIX_DIAG_GUARD:
                    _DIAG["guard_from"] = best_move
                    _DIAG["best_score"] = _adj(k[0], biases.get(k[1], 0))
                    if DIAG_PV:
                        _DIAG["pv"] = _pv_line(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, k[1], max_turns - turn)
                best_move = k[1]
                _DIAG["reason"] = "last_wall_guard"
                break
    _DIAG["best"] = best_move
    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None


# ---------------------------------------------------------------------------
# Pillar 4: Bitboard-Accelerated Retrograde Endgame Tablebase (_PawnEndgame)
# ---------------------------------------------------------------------------
_EG_SPAN = 81 * 81
_EG_CACHE = None
_EG_VISITS = {}
_EG_CONTEXT = None
_EG_LAST_TURN = -1


class _EGTimeout(Exception):
    pass


def _eg_dist_map(goal, U, D, L, R):
    dist = [999] * 81
    seen = front = goal
    k = 0
    while front:
        f = front
        while f:
            low = f & -f
            dist[low.bit_length() - 1] = k
            f ^= low
        k += 1
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        seen |= front
    return dist


def _eg_tables(U, D, L, R):
    graph = []
    for i in range(81):
        bit = 1 << i
        nb = []
        if bit & U:
            nb.append(i - 9)
        if bit & L:
            nb.append(i - 1)
        if bit & R:
            nb.append(i + 1)
        if bit & D:
            nb.append(i + 9)
        graph.append(tuple(nb))
    contact = {}
    for i in range(81):
        for j in graph[i]:
            contact[i * 81 + j] = tuple(sorted(
                x.bit_length() - 1 for x in _pawn_moves(1 << i, 1 << j, U, D, L, R)))
    return graph, contact


class _PawnEndgame:
    __slots__ = ("key", "graph", "contact", "maps", "result", "distance")

    def __init__(self, key, U, D, L, R, goals, deadline):
        self.key = key
        graph, contact = _eg_tables(U, D, L, R)
        self.graph, self.contact = graph, contact
        r0 = _eg_dist_map(ROW[goals[0]], U, D, L, R)
        r1 = _eg_dist_map(ROW[goals[1]], U, D, L, R)
        self.maps = (r0, r1)
        span = _EG_SPAN
        count = 2 * span
        result = [0] * count
        distance = [0] * count
        remaining = [0] * count
        longest = [0] * count
        previous = [[] for _ in range(count)]
        pending = deque()
        push = pending.append
        cget = contact.get
        g0, g1 = goals
        for p0 in range(81):
            if (p0 & 7) == 0 and perf_counter() >= deadline:
                raise _EGTimeout
            if r0[p0] >= 999:
                continue
            won0 = p0 // 9 == g0
            base0 = p0 * 81
            for p1 in range(81):
                if p1 == p0 or r1[p1] >= 999:
                    continue
                pair = base0 + p1
                if won0 or p1 // 9 == g1:
                    w0 = 1 if won0 else -1
                    result[pair] = w0
                    result[span + pair] = -w0
                    push(pair)
                    push(span + pair)
                    continue
                mv = cget(pair)
                if mv is None:
                    mv = graph[p0]
                remaining[pair] = len(mv) + 1
                for d in mv:
                    previous[span + d * 81 + p1].append(pair)
                previous[span + pair].append(pair)
                st = span + pair
                mv = cget(p1 * 81 + p0)
                if mv is None:
                    mv = graph[p1]
                remaining[st] = len(mv) + 1
                for d in mv:
                    previous[base0 + d].append(st)
                previous[pair].append(st)
        processed = 0
        popleft = pending.popleft
        while pending:
            state = popleft()
            processed += 1
            if (processed & 1023) == 0 and perf_counter() >= deadline:
                raise _EGTimeout
            rs = result[state]
            ds = distance[state]
            for parent in previous[state]:
                if result[parent]:
                    continue
                if rs == -1:
                    result[parent] = 1
                    distance[parent] = ds + 1
                    push(parent)
                else:
                    remaining[parent] -= 1
                    if ds > longest[parent]:
                        longest[parent] = ds
                    if remaining[parent] == 0:
                        result[parent] = -1
                        distance[parent] = longest[parent] + 1
                        push(parent)
        if perf_counter() >= deadline:
            raise _EGTimeout
        self.result, self.distance = result, distance

    def moves(self, own, enemy):
        mv = self.contact.get(own * 81 + enemy)
        return self.graph[own] if mv is None else mv

    def probe(self, p0, p1, turn):
        state = turn * _EG_SPAN + p0 * 81 + p1
        return self.result[state], self.distance[state]

    def action(self, p0, p1, turn, turns_left, legal, visits):
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = None, None
        for dest in self.moves(own, enemy) + (own,):
            if dest != own and dest not in legal:
                continue
            np0, np1 = (dest, p1) if turn == 0 else (p0, dest)
            outcome, dist = self.probe(np0, np1, 1 - turn)
            outcome, dist = -outcome, dist + 1
            if dist > turns_left:
                outcome = 0
            pace = -dist if outcome == 1 else dist if outcome == -1 else 0
            progress = -self.maps[turn][dest]
            pressure = 0.0
            if outcome == 0 and turns_left >= 2:
                replies = self.moves(enemy, dest) + (enemy,)
                chances = 0
                for reply in replies:
                    ap0, ap1 = (dest, reply) if turn == 0 else (reply, dest)
                    rr, rd = self.probe(ap0, ap1, turn)
                    chances += (rr == 1 and rd <= turns_left - 2)
                pressure = chances / len(replies)
            key = (outcome, pace, progress, pressure,
                   -visits.get((dest, enemy), 0), dest != own)
            if best_key is None or key > best_key:
                best, best_key = dest, key
        return best


def solve_action(observation, deadline, U, D, L, R, hw, vw):
    global _EG_CACHE, _EG_CONTEXT, _EG_LAST_TURN, _EG_VISITS
    if observation["opponent"]["walls_remaining"] != 0 or perf_counter() >= deadline:
        return None
    me = observation["player_id"]
    goal = observation["you"]["goal_row"]
    for move in observation["legal_moves"]:
        if move[0] == goal:
            return {"type": "move", "to": [int(move[0]), int(move[1])]}
    turns_left = observation["max_turns"] - observation["turn"]
    if turns_left <= 0:
        return None
    positions, goals = [0, 0], [0, 0]
    for player, info in ((me, observation["you"]), (1 - me, observation["opponent"])):
        positions[player] = info["position"][0] * 9 + info["position"][1]
        goals[player] = info["goal_row"]
    goals = tuple(goals)
    key = (hw, vw, goals)
    try:
        if _EG_CACHE is None or _EG_CACHE.key != key:
            _EG_CACHE = None
            _EG_CACHE = _PawnEndgame(key, U, D, L, R, goals, deadline)
    except _EGTimeout:
        return None
    p0, p1 = positions
    outcome, dist = _EG_CACHE.probe(p0, p1, me)
    if (observation["you"]["walls_remaining"] != 0
            and not (outcome == 1 and dist <= turns_left)):
        return None
    context = (key, me)
    if context != _EG_CONTEXT or observation["turn"] <= _EG_LAST_TURN:
        _EG_VISITS = {}
    _EG_CONTEXT, _EG_LAST_TURN = context, observation["turn"]
    own, enemy = positions[me], positions[1 - me]
    pair = (own, enemy)
    _EG_VISITS[pair] = min(32, _EG_VISITS.get(pair, 0) + 1)
    if len(_EG_VISITS) > 256:
        _EG_VISITS = {pair: _EG_VISITS[pair]}
    legal = {r * 9 + c for r, c in observation["legal_moves"]}
    dest = _EG_CACHE.action(p0, p1, me, turns_left, legal, _EG_VISITS)
    if dest is None:
        return None
    if dest == own:
        return {"type": "pass"}
    return {"type": "move", "to": [dest // 9, dest % 9]}


# ---------------------------------------------------------------------------
# Pillar 4b: pawn tables inside the search + exact 1-wall-vs-0 tables
#   (techniques of quantumgreenbird, re-implemented on v3's masks).
#   N = pawn whose goal is row 0, S = pawn whose goal is row 8; index n * 81 + s.
#   valN / valS = value for N / S to move: +k win (goal reached on ply k), -k loss on
#   ply k, 0 draw.  Cross-checked state by state against _PawnEndgame (tests).
# ---------------------------------------------------------------------------
def _tbf_nb(U, D, L, R):
    nb = []
    for i in range(81):
        m = 1 << i
        t = []
        if m & U:
            t.append(i - 9)
        if m & D:
            t.append(i + 9)
        if m & L:
            t.append(i - 1)
        if m & R:
            t.append(i + 1)
        nb.append(t)
    return nb


def _tbf_jump(x, y, U, D, L, R):
    """Landing cells when the pawn on x steps into the pawn on y (adjacent, edge open)."""
    my = 1 << y
    dd = y - x
    if dd == -9:
        if my & U:
            return [y - 9]
        side = ((y - 1, my & L), (y + 1, my & R))
    elif dd == 9:
        if my & D:
            return [y + 9]
        side = ((y - 1, my & L), (y + 1, my & R))
    elif dd == -1:
        if my & L:
            return [y - 1]
        side = ((y - 9, my & U), (y + 9, my & D))
    else:
        if my & R:
            return [y + 1]
        side = ((y - 9, my & U), (y + 9, my & D))
    return [c for c, ok in side if ok]


def _tbf_graph(U, D, L, R):
    nb = _tbf_nb(U, D, L, R)
    adjmv = {}
    rj = {}
    for x in range(81):
        for y in nb[x]:
            j = _tbf_jump(x, y, U, D, L, R)
            adjmv[x * 81 + y] = [c for c in nb[x] if c != y] + j
            for z in j:
                rj.setdefault(y * 81 + z, []).append(x)
    return nb, adjmv, rj


def _tbf_solve(U, D, L, R):
    """Retrograde analysis of every (N, S, side to move) of a fixed layout (no walls left)."""
    nb, adjmv, rj = _tbf_graph(U, D, L, R)
    NN = 6561
    valN = [0] * NN
    valS = [0] * NN
    cntN = [0] * NN
    cntS = [0] * NN
    q = []
    push = q.append
    for n in range(9, 81):
        base = nb[n]
        base_win = n < 18 and (n - 9) in base
        bc = len(base) + 1
        off = n * 81
        for s in range(72):
            if s == n:
                continue
            i = off + s
            mv = adjmv.get(n * 81 + s)
            if mv is None:
                if base_win:
                    valN[i] = 1
                    push(i)
                else:
                    cntN[i] = bc
            elif mv and min(mv) < 9:
                valN[i] = 1
                push(i)
            else:
                cntN[i] = len(mv) + 1
    for s in range(72):
        base = nb[s]
        base_win = s >= 63 and (s + 9) in base
        bc = len(base) + 1
        for n in range(9, 81):
            if n == s:
                continue
            i = n * 81 + s
            mv = adjmv.get(s * 81 + n)
            if mv is None:
                if base_win:
                    valS[i] = 1
                    push(NN + i)
                else:
                    cntS[i] = bc
            elif mv and max(mv) >= 72:
                valS[i] = 1
                push(NN + i)
            else:
                cntS[i] = len(mv) + 1
    head = 0
    while head < len(q):
        st = q[head]
        head += 1
        if st < NN:
            v = valN[st]
            n, s = divmod(st, 81)
            off = n * 81
            preds = nb[s] + [s]
            extra = rj.get(n * 81 + s)
            if extra:
                preds += extra
            for s0 in preds:
                if s0 == n or s0 >= 72:
                    continue
                j = off + s0
                if valS[j]:
                    continue
                if v < 0:
                    valS[j] = 1 - v
                    push(NN + j)
                else:
                    c = cntS[j] - 1
                    cntS[j] = c
                    if not c:
                        valS[j] = -1 - v
                        push(NN + j)
        else:
            i = st - NN
            v = valS[i]
            n, s = divmod(i, 81)
            preds = nb[n] + [n]
            extra = rj.get(s * 81 + n)
            if extra:
                preds += extra
            for n0 in preds:
                if n0 == s or n0 < 9:
                    continue
                j = n0 * 81 + s
                if valN[j]:
                    continue
                if v < 0:
                    valN[j] = 1 - v
                    push(j)
                else:
                    c = cntN[j] - 1
                    cntN[j] = c
                    if not c:
                        valN[j] = -1 - v
                        push(j)
    return valN, valS


def _tbf_prune():
    """Keep only tables of layouts still reachable from the current walls."""
    hw, vw = _STATE.get("root_walls", (0, 0))
    keep = {k: t for k, t in _TBF.items() if (k[0] & hw) == hw and (k[1] & vw) == vw}
    _TBF.clear()
    if len(keep) < TBF_LIMIT * 3 // 4:
        _TBF.update(keep)


def _tbf_build(U, D, L, R, hw, vw):
    t = _tbf_solve(U, D, L, R)
    if len(_TBF) >= TBF_LIMIT:
        _tbf_prune()
    _TBF[(hw, vw)] = t
    return t


def _tbf_reach(goal, U, D, L, R):
    seen = front = goal
    while front:
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        seen |= front
    return seen


def _tb1_exit_keys(t, H, U, D, L, R):
    """Side H (0 = N, 1 = S) placed its last wall giving table t: value for H of every
    (N, S) as a comparable key (win 20000 - plies, draw 0, loss plies - 20000, wall
    illegal there because a pawn would lose its route -10**6)."""
    vals = t[1] if H == 0 else t[0]
    rN = _tbf_reach(ROW[0], U, D, L, R)
    rS = _tbf_reach(ROW[8], U, D, L, R)
    bad = [-1000000] * 81
    out = []
    ext = out.extend
    for n in range(81):
        if not (rN >> n) & 1:
            ext(bad)
            continue
        keys = [(20000 + v if v < 0 else (v - 20000 if v > 0 else 0))
                for v in vals[n * 81:n * 81 + 81]]
        for s in range(81):
            if not (rS >> s) & 1:
                keys[s] = -1000000
        ext(keys)
    return out


def _tb1_solve(U, D, L, R, H, best):
    """Exact table when side H has exactly one wall and the other side none.  best[i] =
    best key of placing that wall (from the child 0:0 tables); level-by-level retrograde."""
    nb, adjmv, rj = _tbf_graph(U, D, L, R)
    NN = 6561
    vals = ([0] * NN, [0] * NN)
    cnts = ([0] * NN, [0] * NN)
    W = {}
    LO = {}
    DE = {}
    for side in (0, 1):
        cnt = cnts[side]
        for p in range(81):
            if side == 0:
                if p < 9:
                    continue
            elif p >= 72:
                continue
            base = nb[p]
            if side == 0:
                base_win = p < 18 and (p - 9) in base
            else:
                base_win = p >= 63 and (p + 9) in base
            bc = len(base) + 1
            for q in range(81):
                if q == p:
                    continue
                if side == 0:
                    if q >= 72:
                        continue
                    i = p * 81 + q
                else:
                    if q < 9:
                        continue
                    i = q * 81 + p
                mv = adjmv.get(p * 81 + q)
                if mv is None:
                    win = base_win
                    c = bc
                else:
                    win = bool(mv) and ((min(mv) < 9) if side == 0 else (max(mv) >= 72))
                    c = len(mv) + 1
                if win:
                    W.setdefault(1, []).append(side * NN + i)
                    continue
                if side == H:
                    k = best[i]
                    if k > -100000:
                        c += 1
                        if k > 10000:
                            W.setdefault(20001 - k, []).append(side * NN + i)
                        elif k < -10000:
                            DE.setdefault(k + 20000, []).append(side * NN + i)
                cnt[i] = c
    valN, valS = vals
    cntN, cntS = cnts
    lvl = 1
    while W or LO or DE:
        for kind in (0, 1, 2):
            lst = (W if kind == 0 else (LO if kind == 1 else DE)).pop(lvl, None)
            if not lst:
                continue
            for e in lst:
                if e < NN:
                    side = 0
                    i = e
                    arr = valN
                else:
                    side = 1
                    i = e - NN
                    arr = valS
                if arr[i]:
                    continue
                if kind == 2:
                    c = cnts[side][i] - 1
                    cnts[side][i] = c
                    if not c:
                        LO.setdefault(lvl + 1, []).append(e)
                    continue
                v = lvl if kind == 0 else -lvl
                arr[i] = v
                n, s = divmod(i, 81)
                if side == 0:
                    off = n * 81
                    preds = nb[s] + [s]
                    extra = rj.get(n * 81 + s)
                    if extra:
                        preds += extra
                    for s0 in preds:
                        if s0 == n or s0 >= 72:
                            continue
                        j = off + s0
                        if valS[j]:
                            continue
                        if v < 0:
                            W.setdefault(lvl + 1, []).append(NN + j)
                        else:
                            c = cntS[j] - 1
                            cntS[j] = c
                            if not c:
                                LO.setdefault(lvl + 1, []).append(NN + j)
                else:
                    preds = nb[n] + [n]
                    extra = rj.get(s * 81 + n)
                    if extra:
                        preds += extra
                    for n0 in preds:
                        if n0 == s or n0 < 9:
                            continue
                        j = n0 * 81 + s
                        if valN[j]:
                            continue
                        if v < 0:
                            W.setdefault(lvl + 1, []).append(j)
                        else:
                            c = cntN[j] - 1
                            cntN[j] = c
                            if not c:
                                LO.setdefault(lvl + 1, []).append(j)
        lvl += 1
    return valN, valS


def _tb1_work(U, D, L, R, hw, vw, H, until):
    """Build (over several turns if needed) the 1-vs-0 table of this layout for holder H."""
    key = (hw, vw, H)
    t = _TB1.get(key)
    if t is not None:
        return t
    job = _TB1JOB.get(key)
    if job is None:
        todo = []
        m = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
        while m:
            w = m & -m
            m ^= w
            todo.append(H_BASE + w.bit_length() - 1)
        m = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
        while m:
            w = m & -m
            m ^= w
            todo.append(V_BASE + w.bit_length() - 1)
        job = [todo, [-1000000] * 6561]
        _TB1JOB[key] = job
    todo = job[0]
    while todo:
        if perf_counter() > until:
            return None
        U2, D2, L2, R2, hw2, vw2 = _wall_masks(todo[-1], U, D, L, R, hw, vw)
        ct = _TBF.get((hw2, vw2))
        if ct is None:
            ct = _tbf_build(U2, D2, L2, R2, hw2, vw2)
        job[1] = list(map(max, job[1], _tb1_exit_keys(ct, H, U2, D2, L2, R2)))
        todo.pop()
    if perf_counter() > until:
        return None
    t = _tb1_solve(U, D, L, R, H, job[1])
    _TB1[key] = t
    del _TB1JOB[key]
    return t


def _tb_probe(a, b, wa, wb, ga, U, D, L, R, hw, vw, ply, remaining, alpha, beta):
    """Search-node probe when one side has no wall: exact (0:0, or 1:0 with its table),
    otherwise a bound (mover alone has walls: lower bound; opponent alone: upper bound).
    Returns a score or None.  Builds missing 0:0 tables within the per-turn allowance."""
    t = None
    exact = False
    if USE_TB1 and wa + wb == 1 and _TB1:
        hold = (0 if ga == ROW[0] else 1) if wa else (1 if ga == ROW[0] else 0)
        t = _TB1.get((hw, vw, hold))
        exact = t is not None
    if t is None:
        t = _TBF.get((hw, vw))
        if t is None:
            if (_TBQ[0] <= 0 or not (ply <= TB_BOUND_PLY or not (wa or wb))
                    or perf_counter() >= _TBQ[1]):
                return None
            _TBQ[0] -= 1
            t = _tbf_build(U, D, L, R, hw, vw)
        exact = not (wa or wb)
    ia = a.bit_length() - 1
    ib = b.bit_length() - 1
    v = t[0][ia * 81 + ib] if ga == ROW[0] else t[1][ib * 81 + ia]
    if 0 < v <= remaining:
        sc = WIN - ply - v
    elif v < 0 and -v <= remaining:
        sc = -WIN + ply - v
    else:
        sc = _draw(ga)
    if exact:
        return sc
    if wa:
        if sc >= beta or sc > MATE_ZONE:
            return sc
    elif sc <= alpha or sc < -MATE_ZONE:
        return sc
    return None


# ---------------------------------------------------------------------------
# Pillar 4c: opening book (data and normalisation of quantumgreenbird)
# ---------------------------------------------------------------------------
def _tr_cell(r, c, vf, hm):
    if vf:
        r = 8 - r
    if hm:
        c = 8 - c
    return r, c


def _tr_wall(r, c, vf, hm):
    if vf:
        r = 7 - r
    if hm:
        c = 7 - c
    return r, c


def _book_key(me, op, wme, wop, hwalls, vwalls, my_goal):
    """Key of the position seen by the side to move, always heading for row 0, smaller of
    the two left/right mirror images.  Returns (key, vertical flip, mirrored)."""
    vf = my_goal == 8
    best = None
    for hm in (False, True):
        a = _tr_cell(me[0], me[1], vf, hm)
        b = _tr_cell(op[0], op[1], vf, hm)
        hs = sorted(_tr_wall(r, c, vf, hm) for r, c in hwalls)
        vs = sorted(_tr_wall(r, c, vf, hm) for r, c in vwalls)
        k = "%d%d%d%d%d,%d" % (a[0], a[1], b[0], b[1], wme, wop)
        k += "H" + "".join("%d%d" % w for w in hs) + "V" + "".join("%d%d" % w for w in vs)
        if best is None or k < best[0]:
            best = (k, hm)
    return best[0], vf, best[1]


def _book_action(s, vf, hm):
    if s == "p":
        return {"type": "pass"}
    r, c = int(s[1]), int(s[2])
    if s[0] == "m":
        r, c = _tr_cell(r, c, vf, hm)
        return {"type": "move", "to": [r, c]}
    r, c = _tr_wall(r, c, vf, hm)
    return {"type": "wall", "orientation": s[0], "row": r, "col": c}


def _book_move(obs):
    if obs.get("turn", 0) > BOOK_MAX_TURN or not OPENING_BOOK:
        return None
    you = obs["you"]
    opp = obs["opponent"]
    if {you["goal_row"], opp["goal_row"]} != {0, 8}:
        return None
    key, vf, hm = _book_key(you["position"], opp["position"], you["walls_remaining"],
                            opp["walls_remaining"], obs["walls"]["horizontal"],
                            obs["walls"]["vertical"], you["goal_row"])
    s = OPENING_BOOK.get(key)
    if s is None:
        return None
    act = _book_action(s, vf, hm)
    return act if _is_legal(act, obs) else None


# ---------------------------------------------------------------------------
# Pillar 5: Arena Interface, Memory Management & Safety Wrapper
# ---------------------------------------------------------------------------
def _bit(rc):
    return 1 << (rc[0] * 9 + rc[1])


def _fallback(observation):
    moves = observation.get("legal_moves") or []
    dist = observation.get("distances")
    if not moves:
        return {"type": "pass"}
    best = moves[0]
    best_d = INF
    for m in moves:
        d = dist[m[0]][m[1]] if dist else 0
        if 0 <= d < best_d:
            best, best_d = m, d
    return {"type": "move", "to": [int(best[0]), int(best[1])]}


def _decide(observation, t0):
    global _EG_CACHE
    you = observation["you"]
    opp = observation["opponent"]
    a = _bit(you["position"])
    b = _bit(opp["position"])
    ga = ROW[you["goal_row"]]
    gb = ROW[opp["goal_row"]]
    wa = max(0, min(10, you["walls_remaining"]))
    wb = max(0, min(10, opp["walls_remaining"]))
    U, D, L, R = U0, D0, L0, R0
    hw = vw = 0
    for r, c in observation["walls"]["horizontal"]:
        w = 1 << (r * 9 + c)
        hw |= w
        D &= ~(w | (w << 1))
        U &= ~((w << 9) | (w << 10))
    for r, c in observation["walls"]["vertical"]:
        w = 1 << (r * 9 + c)
        vw |= w
        R &= ~(w | (w << 9))
        L &= ~((w << 1) | (w << 10))

    turn = observation.get("turn", 0)
    max_turns = observation.get("max_turns", 500)
    goals = (ga, gb)
    if turn <= 0 or turn <= _STATE["last_turn"] or _STATE["goals"] != goals:
        _TT.clear()
        _HIST.clear()
        _ACTIVE_RECENT.clear()
        for i in range(len(_HISTORY)):
            _HISTORY[i] = 0
        _STATE["margin"] = 0.0
        _TBF.clear()
        _TB1.clear()
        _TB1JOB.clear()
    elif len(_TT) > TT_LIMIT:
        _TT.clear()
    else:
        for i in range(len(_HISTORY)):
            _HISTORY[i] >>= 2
    _STATE["last_turn"] = turn
    _STATE["goals"] = goals

    frac = turn / float(max_turns if max_turns > 0 else 500)
    urg = max(0.0, (frac - URG_START) / (1.0 - URG_START)) if frac > URG_START else 0.0
    _URG_CUR[0] = urg
    _CONTEMPT_CUR[0] = CONTEMPT + int(CONTEMPT_URG * urg)
    if FIX_CONTEMPT_TT and _CONTEMPT_CUR[0] != _STATE.get("contempt"):
        _TT.clear()
    _STATE["contempt"] = _CONTEMPT_CUR[0]

    check_blocks = (hw.bit_count() + vw.bit_count() if hasattr(int, "bit_count")
                    else (bin(hw).count("1") + bin(vw).count("1"))) >= BLOCK_MIN_WALLS
    if check_blocks != _BLOCK[0]:
        _TT.clear()
    _BLOCK[0] = check_blocks
    if not OPT_KEEP_BCACHE or len(_BCACHE) > BCACHE_LIMIT:
        _BCACHE.clear()
    if not OPT_KEEP_BCACHE or len(_BQ) > BCACHE_LIMIT:
        _BQ.clear()
    _ROOT_GOAL[0] = ga
    _HIST.add((a, b, wa, wb, hw, vw, ga))
    _ACTIVE_RECENT[:] = [e for e in _ACTIVE_RECENT
                         if e[2:] == (hw, vw) and 0 < turn - e[0] <= 2 * ROOT_RECENT_WINDOW]
    _ACTIVE_RECENT.append((turn, a, hw, vw))
    del _ACTIVE_RECENT[:-ROOT_RECENT_WINDOW]

    _EGP[0] = None
    rem_budget = max(0.02, TIME_LIMIT - _STATE["margin"] - _cold())
    if wb == 0 and (wa == 0 or _dist(a, ga, U, D, L, R) <= _dist(b, gb, U, D, L, R) + 1):
        exact_deadline = min(t0 + rem_budget * 0.88, perf_counter() + 0.24)
        action = solve_action(observation, exact_deadline, U, D, L, R, hw, vw)
        if action is not None and _is_legal(action, observation):
            _STATE["mode"] = "endgame"
            _DIAG.clear()
            _DIAG.update(reason="endgame_table", turn=turn)
            return action
    _STATE["root_walls"] = (hw, vw)
    _TBON[0] = USE_TREE_TB and (ga | gb) == (ROW[0] | ROW[8])
    _TBQ[0] = TB_MAX
    _TBQ[1] = t0 + rem_budget * TB_FRAC
    if _TBON[0] and USE_TB1 and (wa == 0) != (wb == 0) and wa + wb == 1:
        holder = ga if wa else gb
        _tb1_work(U, D, L, R, hw, vw, 0 if holder == ROW[0] else 1,
                  t0 + rem_budget * TB1_FRAC)
    if EG_PROBE and not _TBON[0] and (wa == 0 or wb == 0):
        me = observation["player_id"]
        goals_id = [0, 0]
        goals_id[me] = you["goal_row"]
        goals_id[1 - me] = opp["goal_row"]
        eg_key = (hw, vw, tuple(goals_id))
        if (_EG_CACHE is None or _EG_CACHE.key != eg_key) and wb > 0 and rem_budget >= 0.35:
            try:
                _EG_CACHE = None
                _EG_CACHE = _PawnEndgame(eg_key, U, D, L, R, tuple(goals_id),
                                         min(t0 + rem_budget * 0.65,
                                             perf_counter() + 0.15))
            except _EGTimeout:
                _EG_CACHE = None
        if _EG_CACHE is not None and _EG_CACHE.key == eg_key:
            _EGP[0] = (hw, vw, _EG_CACHE, me, ga)
    # TT values computed with/without exact table probes must not be mixed.
    egp_ctx = None if _EGP[0] is None else (_EGP[0][0], _EGP[0][1], _EGP[0][3], _EGP[0][4])
    if FIX_CONTEMPT_TT and egp_ctx != _STATE.get("egp_ctx"):
        _TT.clear()
    _STATE["egp_ctx"] = egp_ctx
    _STATE["mode"] = "search"

    _DIAG_TB1[0] = None
    if _TBON[0] and USE_TB1 and (wa == 0) != (wb == 0) and wa + wb == 1:
        hk = (hw, vw, 0 if (ga if wa else gb) == ROW[0] else 1)
        _DIAG_TB1[0] = hk in _TB1
    code, kid = _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn,
                             max_turns, t0)
    _DIAG["tb1_ready"] = _DIAG_TB1[0]
    _DIAG["tb_built"] = TB_MAX - _TBQ[0]
    if kid is not None:
        _HIST.add((b, kid[2], wb, kid[9], kid[7], kid[8], gb))
    if code == PASS:
        return {"type": "pass"}
    if code < H_BASE:
        return {"type": "move", "to": [code // 9, code % 9]}
    if code < V_BASE:
        idx = code - H_BASE
        return {"type": "wall", "orientation": "H", "row": idx // 9, "col": idx % 9}
    idx = code - V_BASE
    return {"type": "wall", "orientation": "V", "row": idx // 9, "col": idx % 9}


def _is_legal(action, observation):
    if not isinstance(action, dict):
        return False
    kind = action.get("type")
    if kind == "pass":
        return True
    if kind == "move":
        to = action.get("to")
        if not isinstance(to, (list, tuple)) or len(to) != 2:
            return False
        return list(to) in [list(m) for m in observation["legal_moves"]]
    if kind == "wall":
        for w in observation["legal_walls"]:
            if (w["orientation"] == action.get("orientation")
                    and w["row"] == action.get("row")
                    and w["col"] == action.get("col")):
                return True
    return False


def choose_action(observation):
    t0 = perf_counter()
    action = None
    try:
        _STATE["moves"] += 1
        if _STATE["moves"] % 25 == 0:
            gc.collect()
        if USE_BOOK:
            action = _book_move(observation)
            if action is not None:
                _STATE["mode"] = "book"
                _DIAG.clear()
                _DIAG.update(reason="book", turn=observation.get("turn", 0))
        if action is None:
            action = _decide(observation, t0)
        if not _is_legal(action, observation):
            action = None
    except Exception:
        if STRICT_MODE or observation.get("_strict"):
            raise
        action = None
    if action is None:
        _STATE["mode"] = "fallback"
        _DIAG["reason"] = "fallback"
        action = _fallback(observation)
    elapsed = perf_counter() - t0
    _DIAG["elapsed"] = elapsed
    late = elapsed - (TIME_LIMIT - _STATE["margin"])
    if late > 0.02:
        _STATE["margin"] = min(TIME_LIMIT * 0.45, _STATE["margin"] + 2 * late)
    return action


# ---------------------------------------------------------------------------
# Opening book data copied from quantumgreenbird.py (SHA-256 b5f6dfb5...ed7e):
# normalised key -> normalised move ('mRC' move, 'HRC'/'VRC' wall, 'p' pass).
# ---------------------------------------------------------------------------
OPENING_BOOK = {
    '10000,3H04121530323446535567V00111314254257': 'm20',
    '10050,2H000311212325405254V020513202433414450': 'm11',
    '10060,1H01152023253140435254V000512162433414450': 'm00',
    '10122,6H0103050711131530333537V22': 'm00',
    '10163,5H02212430505254V1113253345': 'm00',
    '10172,1H00020421333540465254V17203237414550': 'm11',
    '10181,5H21303335465254V10173237414550': 'm00',
    '10181,5H21333540465254V10173237414550': 'm00',
    '10210,2H0103050711131525333537505254V17233455': 'm00',
    '10215,2H0002041113151722414345V2130': 'm11',
    '10221,0H01032325305254V101112262735415055567677': 'm00',
    '10252,4H0012212325405254V132433414450': 'm11',
    '10273,3H0011132035525460V031434455051': 'm11',
    '10310,2H0011172123253133365254V12131627414550': 'm11',
    '10330,1H000204111315172125353743505254V23323455': 'm11',
    '10340,5H0111131517333641535557V21374245': 'm00',
    '10363,1H000204212325273550525456V30324346': 'm11',
    '10363,5H212325273234505254V103045': 'm00',
    '10372,1H000204212325274550525456V3032364347': 'm11',
    '10372,3H0003112035525460V02141634455051': 'm11',
    '10381,1H000204212325275052545677V303235434647': 'm11',
    '10400,0H112325434547525461V0110122731333550515557': 'm00',
    '10400,2H01101723253652546170V1221273035505562': 'm00',
    '10401,0H112325434547525461V01102227313335505155': 'm00',
    '10401,0H2123252731335254617072V1011121314445063': 'm00',
    '10401,1H000207111315515355577073V041721244044': 'm11',
    '10403,3H0122243133535557V112530424570': 'm00',
    '10404,3H21232527304143454751V104260': 'm00',
    '10430,4H00031113151744465153555770V202143': 'm11',
    '10450,3H000204111315323640515355V0623314650': 'm11',
    '10460,1H000406111320223547525460V03141634455051': 'm11',
    '10470,0H00020421232527505254567577V05303235434647': 'm20',
    '10470,3H0112142123252731334550525456V132644': 'm00',
    '10472,0H000204212325275052545677V053032354346': 'H75',
    '10480,1H0002042123252730505254567577V3234434547': 'm11',
    '10480,1H0002212325273041505254567577V3234434647': 'm11',
    '10482,2H00022123252734364250525467V314557': 'm11',
    '10530,3H0004071113151730407072V121432435263': 'm11',
    '10571,0H00020421232527505254567577V053032354346': 'V47',
    '10571,0H00023133404547506264667577V143442536061': 'm11',
    '10581,1H00022123252736505254567577V3032414347': 'm11',
    '10602,5H111315173133535557V10142542': 'm00',
    '10603,5H0122243133535557V11254245': 'm00',
    '10610,0H000204061113151732415153555760626466V2240': 'm11',
    '10630,5H012123252736505254636567V203445': 'm00',
    '10640,0H01031114202235374446515355576062V31334042': 'm00',
    '10641,5H0121232527365052546567V203445': 'm00',
    '10670,3H002123252732505254566567V1231344547': 'm11',
    '10670,3H002123252742505254566567V1231344547': 'm11',
    '10701,3H011022245153555770V12203134435054': 'm00',
    '10720,1H21232527303236414347505254737577V113445': 'm00',
    '10720,3H01102123252736505254636567V12313445': 'm00',
    '10720,4H2123252744465052737577V1030324365': 'm00',
    '10721,3H011022245153555770V12203134435054': 'm00',
    '10730,4H00040711131517307072V121432435263': 'm11',
    '10741,2H21232527505254566567V10303234434547': 'm00',
    '10751,4H21232527444650525667V1030324366': 'm00',
    '10761,3H2123252734435052545677V1030324547': 'm00',
    '10764,1H01030507101214162542444667V2637': 'm00',
    '10781,1H000204212325273050525456737577V324347': 'm11',
    '11000,3H04121530323446535567V00111314254257': 'm01',
    '11032,4H00052224333537V01121416324363': 'm21',
    '11041,5H022325305254V1112262735415055': 'm01',
    '11043,4H00052224333537V011416324363': 'V12',
    '11072,1H00020421333540465254V17203237414550': 'm12',
    '11102,7H000311535567V0222425657': 'm12',
    '11123,7H000311535567V22425657': 'V02',
    '11124,7H0003535567V22425657': 'H11',
    '11130,2H0002040612141622303234535557V10243352': 'm12',
    '11150,2H000311212325405254V020513202433414450': 'm12',
    '11200,2H0103050711131525333537505254V17233455': 'm10',
    '11205,2H0002041113151722414345V2130': 'm12',
    '11211,4H022224273335V051011142632374363': 'm01',
    '11260,2H012023253140435254V000512162433414450': 'm21',
    '11262,4H0012212325405254V132433414450': 'm12',
    '11300,2H01101723253652546170V1221273035505562': 'm10',
    '11303,3H0122243133535557V112530424570': 'm10',
    '11310,1H0011172123253133365254V1012131627414550': 'm12',
    '11330,0H00020406111315172125353743505254V23323455': 'm12',
    '11330,2H01031113151725353743505254V0023323455': 'm12',
    '11331,3H0113151725353743505254V0023323455': 'H11',
    '11350,3H000204111315323640515355V0623314650': 'm12',
    '11373,1H000204212325273550525456V30324346': 'm12',
    '11373,3H0011132035525460V031434455051': 'm12',
    '11382,1H000204212325274550525456V3032364347': 'm12',
    '11430,3H0004071113151730407072V121432435263': 'm12',
    '11463,2H0002172325305254V16273234414550': 'm12',
    '11472,2H00022123252734364250525467V314557': 'm12',
    '11472,3H0003112035525460V02141634455051': 'm12',
    '11481,1H000204212325275052545677V303235434647': 'm12',
    '11501,1H000207111315515355577073V041721244044': 'm12',
    '11503,5H0122243133535557V11254245': 'm10',
    '11530,4H00031113151744465153555770V202143': 'm12',
    '11560,1H000406111320223547525460V03141634455051': 'm12',
    '11560,1H0204061012141622303244535557V0724333552': 'm01',
    '11580,1H0002042123252730505254567577V3234434547': 'm12',
    '11580,1H0002212325273041505254567577V3234434647': 'm12',
    '11601,3H011022245153555770V12203134435054': 'm10',
    '11620,0H000204061113151732415153555760626466V2240': 'm12',
    '11620,3H01102123252736505254636567V12313445': 'm10',
    '11630,0H01031114202235374446515355576062V31334042': 'm10',
    '11630,4H00040711131517307072V121432435263': 'm12',
    '11664,1H01030507101214162542444667V2637': 'm10',
    '11670,2H002123252732505254566567V011231344547': 'm21',
    '11670,2H002123252742505254566567V011231344547': 'm21',
    '11671,0H00023133404547506264667577V143442536061': 'm12',
    '11680,0H000210212325273041505254567577V3234434547': 'm12',
    '11681,1H00022123252736505254567577V3032414347': 'm12',
    '11681,1H000221232527505254567577V303234414346': 'm12',
    '11702,3H0110222451535557V12203134435054': 'H70',
    '11711,3H011022245153555770V12203134435054': 'm10',
    '11740,1H011021232527304150525456737577V12324347': 'm10',
    '11771,1H000204212325273050525456737577V324347': 'm12',
    '12020,6H031315222443535557V0114324462': 'm03',
    '12070,4H01101533424650535557V162124323644': 'm13',
    '12082,1H00020421333540465254V17203237414550': 'm13',
    '12100,2H0103050711131525333537505254V17233455': 'm11',
    '12110,0H03051316222527344144475355V11203133424657': 'm02',
    '12140,2H0002040612141622303234535557V10243352': 'm13',
    '12162,4H0012212325405254V132433414450': 'm02',
    '12180,0H03050713162224535567V11232532363740525657': 'm02',
    '12200,2H01101723253652546170V1221273035505562': 'm11',
    '12202,7H000311535567V0222425657': 'm02',
    '12205,1H000204061113151722414345V2130': 'm13',
    '12210,4H00232545475254V122124273235414355': 'm02',
    '12224,2H01102325405254V02112433414450': 'm32',
    '12230,0H00020406111315172125353743505254V23323455': 'm13',
    '12250,2H000311212325405254V020513202433414450': 'm02',
    '12260,3H012023253140435254V0512162433414450': 'm11',
    '12271,3H001013212325405254V02162433414450': 'm02',
    '12300,1H0011172123253133365254V1012131627414550': 'm02',
    '12302,0H0210213443515355577376V22232430445062': 'm11',
    '12310,3H0010222427333550V051214263237414363': 'm02',
    '12320,4H00020411142325525460V162235505155': 'm13',
    '12321,5H000204112325525460V2235505155': 'V16',
    '12322,6H02042325525460V2235505155': 'H11',
    '12330,1H0103051113151725353743505254V0023323455': 'm13',
    '12330,3H0004071113151730407072V121432435263': 'm02',
    '12331,4H0113151725353743505254V23323455': 'm11',
    '12360,3H000204111315323640515355V0623314650': 'm13',
    '12380,0H01030507101214162226303244535557V24333552': 'm11',
    '12411,0H010305072022242631334553555760V36425051': 'm11',
    '12460,1H0204061012141622303244535557V0724333552': 'm11',
    '12463,3H02172325305254V16273234414550': 'm11',
    '12471,2H00022123252734364250525467V31455657': 'm13',
    '12473,1H000204212325273550525456V30324346': 'm13',
    '12473,3H0011132035525460V031434455051': 'm02',
    '12482,1H000204212325274550525456V3032364347': 'm13',
    '12500,3H030507121416313547525456V0124364157': 'm02',
    '12501,3H011022245153555770V12203134435054': 'm11',
    '12520,4H00031113151744465153555770V202143': 'm02',
    '12521,0H010305072022242631334553555760V36425051': 'm11',
    '12530,0H00232545475254607073V12132124273235414355': 'm02',
    '12530,4H00040711131517307072V121432435263': 'm02',
    '12542,5H0305224351535557V0211133262': 'm02',
    '12563,2H0002172325305254V16273234414550': 'H56',
    '12572,2H000217232530525456V16273234414550': 'm13',
    '12572,2H00022123252734364250525467V314557': 'm13',
    '12572,3H0003112035525460V02141634455051': 'm02',
    '12580,0H031315172022364345535557V1114273234356267': 'm02',
    '12581,1H000204212325275052545677V303235434647': 'm13',
    '12601,1H000207111315515355577073V041721244044': 'm13',
    '12602,3H0110222451535557V12203134435054': 'm11',
    '12603,0H01030507202224263251535557V35374144': 'H70',
    '12620,0H01031114202235374446515355576062V31334042': 'm11',
    '12622,0H010305072022242631334553555760V364250': 'H62',
    '12630,0H000204061113151732415153555760626466V2240': 'm13',
    '12630,3H01102123252736505254636567V12313445': 'm11',
    '12631,0H01030507202224263133455355576062V364250': 'm11',
    '12660,1H000406111320223547525460V03141634455051': 'm02',
    '12660,2H002123252732505254566567V011231344547': 'm02',
    '12660,2H002123252742505254566567V011231344547': 'm02',
    '12674,1H01030507101214162542444667V2637': 'm11',
    '12680,1H0002042123252730505254567577V3234434547': 'm13',
    '12680,1H0002212325273041505254567577V3234434647': 'm13',
    '12680,1H0210212325273041505254567577V3234434547': 'm11',
    '12680,2H0310121533425062646677V11143234546061': 'm02',
    '12681,0H00023133404547506264667577V143442536061': 'm13',
    '12685,1H010305071012141625424446V2637': 'H67',
    '12701,3H011022245153555770V12203134435054': 'm11',
    '12720,1H030507101222243751535570V11203335424657': 'm02',
    '12750,1H011021232527304150525456737577V12324347': 'm11',
    '12761,1H000204212325273050525456737577V324347': 'm13',
    '12781,1H00022123252736505254567577V3032414347': 'm13',
    '12781,1H000221232527505254567577V303234414346': 'm13',
    '13030,3H0406121734414550525457V112326313543': 'm04',
    '13060,5H041012232544505254V021516355365': 'm03',
    '13070,3H0103101533424650535557V162124323644': 'm14',
    '13082,0H00020421333540465254V1317203237414550': 'm23',
    '13110,2H0103050711131525333537505254V17233455': 'm12',
    '13124,8H03535567V22425657': 'm11',
    '13141,4H04252731333550525477V0214234446': 'm03',
    '13150,2H0002040612141622303234535557V10243352': 'm14',
    '13170,1H021114202225334043465254V16273436414550': 'm14',
    '13210,0H03051316222527344144475355V11203133424657': 'm12',
    '13220,0H00020406111315172125353743505254V23323455': 'm14',
    '13220,0H00020411142325525460V16222430313547505155': 'm14',
    '13220,4H00020411142325525460V162235505155': 'm23',
    '13230,1H0103051113151725353743505254V0023323455': 'm14',
    '13232,7H2242475355V031232465257': 'm03',
    '13253,5H0305224351535557V02133262': 'V11',
    '13254,6H0305224351535557V3262': 'V13',
    '13280,0H01030507101214162226303244535557V24333552': 'm12',
    '13280,0H03050713162224535567V11232532363740525657': 'm12',
    '13305,1H000204061113151722414345V2130': 'm14',
    '13352,5H0305224351535557V0211133262': 'm23',
    '13360,1H0204061012141622303244535557V0724333552': 'm12',
    '13361,3H0411252731333550525477V0214234446': 'm03',
    '13370,3H000204111315323640515355V0623314650': 'm14',
    '13400,0H0305071320222446515355577072V023335404244': 'm14',
    '13400,3H030507121416313547525456V0124364157': 'm12',
    '13411,1H0305072022242631334553555760V36425051': 'm12',
    '13461,2H00022123252734364250525467V31455657': 'm14',
    '13521,1H0305072022242631334553555760V36425051': 'm12',
    '13573,1H000204212325273550525456V30324346': 'm14',
    '13582,1H000204212325274550525456V3032364347': 'm14',
    '13582,2H000217232530525456V16273234414550': 'm14',
    '13585,1H010305071012141625424446V2637': 'm12',
    '13603,1H030507202224263251535557V35374144': 'm12',
    '13620,1H031114202235374446515355576062V31334042': 'm12',
    '13622,1H0305072022242631334553555760V364250': 'm12',
    '13640,0H000204061113151732415153555760626466V2240': 'm14',
    '13672,2H00022123252734364250525467V314557': 'm14',
    '13680,0H0002041423252741505254567577V053234434547': 'm23',
    '13681,1H000204212325275052545677V303235434647': 'm14',
    '13701,1H000207111315515355577073V041721244044': 'm14',
    '13701,1H0305071012222437515355V11203335424657': 'H70',
    '13702,1H030507202224264251535557V3135374044': 'H70',
    '13702,1H0305072022242651535557V313536404344': 'H70',
    '13702,1H0305072022242651535557V313537404244': 'H70',
    '13710,1H030507101222243751535570V11203335424657': 'm12',
    '13751,1H000204212325273050525456737577V324347': 'm14',
    '13771,1H00022123252736505254567577V3032414347': 'm14',
    '13771,1H000221232527505254567577V303234414346': 'm14',
    '13780,1H0002042123252730505254567577V3234434547': 'm14',
    '13780,1H0002212325273041505254567577V3234434647': 'm14',
    '13780,2H0310121533425062646677V11143234546061': 'm12',
    '14020,3H0406121734414550525457V112326313543': 'm13',
    '14030,5H031517222443535557V051112325462': 'm15',
    '14110,0H03051316222527344144475355V11203133424657': 'm13',
    '14120,2H0103050711131525333537505254V17233455': 'm13',
    '14210,0H00020406111315172125353743505254V23323455': 'm15',
    '14210,0H00020411142325525460V16222430313547505155': 'm15',
    '14220,1H0103051113151725353743505254V0023323455': 'm15',
    '14241,4H03202232343653555770V0513244143': 'm15',
    '14300,3H030507121416313547525456V0124364157': 'm13',
    '14331,3H0316202232343653555770V0513244143': 'm15',
    '14400,1H0305071320222446515355577072V3335404244': 'm13',
    '14405,1H000204061113151722414345V2130': 'm15',
    '14413,0H050725344246576365V1314263345536667': 'm04',
    '14421,1H0305072022242631334553555760V36425051': 'm13',
    '14421,2H1012172224333550V030514263237414363': 'm04',
    '14500,0H0305071320222446515355577072V023335404244': 'm15',
    '14502,1H03050710222437515355V11203335424657': 'H12',
    '14503,1H030507202224263251535557V35374144': 'm13',
    '14511,1H0305072022242631334553555760V36425051': 'm13',
    '14600,2H0412151734455761636570V13163335536667': 'm15',
    '14601,1H0305071012222437515355V11203335424657': 'm13',
    '14602,0H03050730323436424455616365V3546506675': 'H70',
    '14602,1H030507202224264251535557V3135374044': 'm13',
    '14602,1H0305072022242651535557V313536404344': 'm13',
    '14602,1H0305072022242651535557V313537404244': 'm13',
    '14612,1H0305072022242631334553555760V364250': 'm13',
    '14630,0H010305071012141635465052545661636567V2547': 'm13',
    '14630,2H101215222430333537517173V031426436063': 'm04',
    '14701,1H030507202224265153555770V323537404144': 'm13',
    '14711,1H000207111315515355577073V041721244044': 'm04',
    '14741,1H000204212325273050525456737577V324347': 'm15',
    '20000,3H04121530323446535567V00111314254257': 'm21',
    '20022,2H000311212325405254V05132433414450': 'V20',
    '20031,2H000311212325405254V0513202433414450': 'V02',
    '20040,2H000311212325405254V020513202433414450': 'm10',
    '20053,4H00212325405254V132433414450': 'H12',
    '20063,5H02212430505254V1113253345': 'm10',
    '20081,5H21303335465254V10173237414550': 'm10',
    '20081,5H21333540465254V10173237414550': 'm10',
    '20121,0H01032325305254V101112262735415055567677': 'm10',
    '20123,2H000311212325405254V132433414450': 'V05',
    '20132,6H0103050711131530333537V22': 'm10',
    '20144,3H10212325405254V022433414450': 'm21',
    '20152,4H0012212325405254V132433414450': 'm10',
    '20160,1H01152023253140435254V000512162433414450': 'm10',
    '20162,1H00020421333540465254V17203237414550': 'm10',
    '20173,3H0011132035525460V031434455051': 'm10',
    '20222,1H10213443515355577376V22232430445062': 'm21',
    '20301,1H000207111315515355577073V041721244044': 'm10',
    '20315,2H0002041113151722414345V2130': 'm10',
    '20320,2H0011172123253133365254V12131627414550': 'm10',
    '20330,2H0002111315172125353743505254V23323455': 'm10',
    '20330,4H00031113151744465153555770V202143': 'm10',
    '20350,5H0111131517333641535557V21374245': 'm10',
    '20353,1H000204212325273550525456V30324346': 'm10',
    '20353,5H212325273234505254V103045': 'm10',
    '20362,3H0003112035525460V02141634455051': 'm10',
    '20370,0H00020421232527505254567577V05303235434647': 'm21',
    '20371,1H000204212325275052545677V303235434647': 'm10',
    '20380,1H0002042123252730505254567577V3234434547': 'm10',
    '20380,1H0002212325273041505254567577V3234434647': 'm10',
    '20380,1H10121416232527415052545671737577V324347': 'm21',
    '20410,0H112325434547525461V0110122731333550515557': 'm10',
    '20411,0H112325434547525461V01102227313335505155': 'm10',
    '20411,0H2123252731335254617072V1011121314445063': 'm10',
    '20414,3H21232527304143454751V104260': 'm10',
    '20416,2H0002041113151722414345V21': 'V30',
    '20431,2H000407111315177072V1214213241435263': 'm10',
    '20440,3H000204111315323640515355V0623314650': 'm10',
    '20460,3H0112142123252731334550525456V132644': 'm10',
    '20470,1H000406111320223547525460V03141634455051': 'm10',
    '20470,1H10121421232527354150525456737577V324346': 'm21',
    '20472,1H000204212325274550525456V3032364347': 'm10',
    '20472,1H0002042123252750525456V303236434547': 'm21',
    '20472,1H0002042123252750525456V313236434447': 'm21',
    '20480,2H10212325273041505254567577V3234434547': 'm21',
    '20481,1H00022123252736505254567577V3032414347': 'm10',
    '20502,5H111315173133535557V10142542': 'm10',
    '20560,1H10121416232527415052545671737577V324344': 'm21',
    '20560,1H10121416232527415052545671737577V324345': 'm21',
    '20561,0H00023133404547506264667577V143442536061': 'm10',
    '20561,2H00020423252741505254567577V32344345': 'm21',
    '20571,1H000221232527505254567577V303234414346': 'm21',
    '20571,1H1012212325273041505254567577V32354346': 'm21',
    '20571,2H00313335374345527375V02213241616672': 'm21',
    '20572,1H000231333537434552626466V3241576172': 'm21',
    '20580,5H01101533424650535557V1624323644': 'm21',
    '20582,2H00022123252734364250525467V314557': 'm10',
    '20600,0H000204061113151732415153555760626466V2240': 'm10',
    '20613,2H0110152224303335375171V14264363': 'm21',
    '20630,3H0004071113151730407072V121432435263': 'm10',
    '20640,5H012123252736505254636567V203445': 'm10',
    '20641,2H21232527505254566567V10303234434547': 'm10',
    '20650,0H10121721232541505254616365V26273243455156': 'm21',
    '20651,4H21232527444650525667V1030324366': 'm10',
    '20651,5H0121232527365052546567V203445': 'm10',
    '20660,3H01102123252736505254636567V12313445': 'm21',
    '20680,3H002123252732505254566567V1231344547': 'm10',
    '20680,3H002123252742505254566567V1231344547': 'm10',
    '20681,1H000204212325273050525456737577V324347': 'm10',
    '20683,2H00022123252734364250525467V3145': 'V57',
    '20720,4H00040711131517307072V121432435263': 'm10',
    '20730,1H21232527303236414347505254737577V113445': 'm10',
    '20730,4H2123252744465052737577V1030324365': 'm10',
    '20741,1H212325273032364143475052547577V113445': 'H73',
    '20741,4H21232527444650527577V1030324365': 'H73',
    '20770,2H1021232527304150525456737577V12324347': 'm21',
    '20771,3H2123252734435052545677V1030324547': 'm10',
    '20782,3H21232527344350525456V1030324547': 'H77',
    '21011,4H022224273335V051011142632374363': 'm11',
    '21022,4H00052224333537V01121416324363': 'm22',
    '21022,4H2224273335V051011142632374363': 'H02',
    '21022,5H2325305254V1112262735415055': 'H02',
    '21031,5H022325305254V1112262735415055': 'm11',
    '21043,5H00052224333537V1416324363': 'm11',
    '21053,3H10212325405254V02162433414450': 'H13',
    '21062,3H1013212325405254V02162433414450': 'H00',
    '21071,3H001013212325405254V02162433414450': 'm22',
    '21100,3H04121530323446535567V00111314254257': 'm11',
    '21112,6H1012232544505254V16355365': 'm22',
    '21120,2H0002040612141622303234535557V10243352': 'm11',
    '21120,3H0010222427333550V051214263237414363': 'm22',
    '21121,3H01032325305254V101112262735415055': 'm31',
    '21122,4H032325305254V1112262735415055': 'H01',
    '21123,5H2325305254V11262735415055': 'V12',
    '21130,2H1015233234656773V14222641444652616472': 'm22',
    '21142,6H0103050711131530333537V22': 'm20',
    '21154,3H10212325405254V022433414450': 'V16',
    '21160,2H012023253140435254V000512162433414450': 'm20',
    '21163,3H0011132035525460V031434455051': 'm20',
    '21202,1H10213443515355577376V22232430445062': 'm22',
    '21220,0H101523323465677375V1114222641445261647277': 'm31',
    '21220,0H1015233234677375V111422264144526164667277': 'm31',
    '21220,0H112325434547525461V0110122731333550515557': 'm31',
    '21221,0H112325434547525461V01102227313335505155': 'm31',
    '21224,5H23255254V11262735415055': 'H30',
    '21231,2H1012172224333550V030514263237414363': 'm22',
    '21262,3H0003112035525460V02141634455051': 'm20',
    '21303,4H22243133535557V112530424570': 'm11',
    '21311,1H000207111315515355577073V041721244044': 'm20',
    '21330,3H00111315172125353743505254V23323455': 'm20',
    '21370,1H000406111320223547525460V03141634455051': 'm20',
    '21372,1H000204212325274550525456V3032364347': 'm22',
    '21372,1H0002042123252750525456V303236434547': 'm22',
    '21372,1H0002042123252750525456V313236434447': 'm22',
    '21380,0H00020421232527505254567577V05303235434647': 'm22',
    '21380,0H10121416232527415052545671737577V21324347': 'm31',
    '21402,3H0110222451535557V12203134435054': 'm22',
    '21411,3H011022245153555770V12203134435054': 'm22',
    '21412,2H121416232527323454V21354363657476': 'V01',
    '21470,2H00020423252741505254567577V3234434547': 'm22',
    '21472,0H021124263032414350525457V131620343555': 'H46',
    '21472,1H000231333537434552626466V3241576172': 'm22',
    '21481,0H02112426303241434650525457V131620343555': 'm22',
    '21481,0H021124263032414350525457V13162034354655': 'm22',
    '21482,1H000204212325275052545677V3032354346': 'm22',
    '21500,0H000204061113151732415153555760626466V2240': 'm20',
    '21501,3H011022245153555770V12203134435054': 'm22',
    '21503,6H22243133535557V11254245': 'm11',
    '21511,2H121416232527323454V0121354363657476': 'm11',
    '21560,0H10121416232527415052545671737577V21324344': 'm31',
    '21560,0H10121416232527415052545671737577V21324345': 'm31',
    '21570,1H10121421232527354150525456737577V324346': 'm22',
    '21571,2H00020423252741505254567577V32344345': 'V47',
    '21580,2H10212325273041505254567577V3234434547': 'm22',
    '21580,4H01101533424650535557V162124323644': 'm31',
    '21580,6H011533424650535557V1624323644': 'm20',
    '21581,1H000221232527505254567577V303234414346': 'm11',
    '21581,1H1012212325273041505254567577V32354346': 'm22',
    '21582,0H021124263032414350525477V131620343555': 'H75',
    '21582,0H0211242630324143505254V13162034354655': 'H57',
    '21603,2H0110152224303335375171V14264363': 'H60',
    '21612,2H011015222430333537516071V14264363': 'm22',
    '21613,3H01152224303335375171V14264363': 'm20',
    '21620,1H1017232541505254616365V2126273243455660': 'm31',
    '21623,2H1012152224303335375171V14264363': 'V03',
    '21624,3H011522243033353751V14264363': 'H71',
    '21624,3H101522243033353751V14264363': 'H71',
    '21650,3H01102123252736505254636567V12313445': 'm22',
    '21660,0H10121721232541505254616365V26273243455156': 'm22',
    '21660,2H002123252732505254566567V011231344547': 'm22',
    '21660,2H002123252742505254566567V011231344547': 'm22',
    '21681,0H02112426303241435052547577V131620343555': 'm22',
    '21683,0H030507222630345052545676V1725323364': 'm11',
    '21710,4H00040711131517307072V121432435263': 'm20',
    '21722,2H1012152224303335375171V0314264363': 'm22',
    '21730,3H0004071113151730407072V121432435263': 'm20',
    '21760,2H1021232527304150525456737577V12324347': 'm22',
    '22030,6H031315222443535557V0114324462': 'm12',
    '22080,4H01101533424650535557V162124323644': 'm12',
    '22110,2H0002040612141622303234535557V10243352': 'm21',
    '22110,4H00232545475254V122124273235414355': 'm12',
    '22112,7H12232544505254V16355365': 'm21',
    '22122,4H00052224333537V01121416324363': 'm02',
    '22122,6H1012232544505254V16355365': 'm23',
    '22152,0H010305071216202332343646V001421274464': 'm23',
    '22152,6H0103050711131530333537V22': 'm21',
    '22153,3H0003112035525460V021434455051': 'V16',
    '22153,3H0011132035525460V031434455051': 'm21',
    '22154,4H031135525460V021434455051': 'H20',
    '22154,4H111335525460V031434455051': 'H20',
    '22162,3H0003112035525460V02141634455051': 'm21',
    '22171,3H001013212325405254V02162433414450': 'm12',
    '22200,3H101723253652546170V1221273035505562': 'm12',
    '22210,3H0010222427333550V051214263237414363': 'm12',
    '22211,2H1012172224333550V030514263237414363': 'm23',
    '22213,7H12232544505254V354365': 'm23',
    '22213,7H12232544505254V355365': 'V16',
    '22260,4H2023253140435254V0512162433414450': 'm12',
    '22302,1H10213443515355577376V22232430445062': 'm12',
    '22320,0H112325434547525461V0110122731333550515557': 'm21',
    '22321,0H112325434547525461V01102227313335505155': 'm21',
    '22322,7H022325525460V2235505155': 'm12',
    '22330,4H111315172125353743505254V23323455': 'm21',
    '22331,5H13151725353743505254V23323455': 'm12',
    '22360,1H000406111320223547525460V03141634455051': 'm21',
    '22363,3H02172325305254V16273234414550': 'm12',
    '22370,2H00020423252741505254567577V3234434547': 'm23',
    '22381,0H021124263032414350525457V13162034354655': 'm23',
    '22382,1H000204212325274550525456V3032364347': 'm23',
    '22382,1H0002042123252750525456V303236434547': 'm23',
    '22382,1H0002042123252750525456V313236434447': 'm23',
    '22401,3H011022245153555770V12203134435054': 'm12',
    '22430,0H00232545475254607073V12132124273235414355': 'm12',
    '22450,0H020406202527335052545677V1217213541444665': 'm12',
    '22480,0H00020421232527505254567577V05303235434647': 'm23',
    '22480,0H031315172022364345535557V1114273234356267': 'm12',
    '22482,1H000231333537434552626466V3241576172': 'm23',
    '22483,1H0211242630324143505254V1316343555': 'm21',
    '22502,3H0110222451535557V12203134435054': 'm12',
    '22552,5H0305224351535557V0211133262': 'm12',
    '22560,0H10121721232541505254616365V26273243455156': 'm23',
    '22570,3H10121533425062646677V11143234546061': 'm23',
    '22580,1H10121421232527354150525456737577V324346': 'm23',
    '22581,0H02112426303241434650525457V131620343555': 'm23',
    '22582,1H000204212325275052545677V3032354346': 'm23',
    '22601,3H011022245153555770V12203134435054': 'm12',
    '22612,1H01101215222430333537516071V14264363': 'V03',
    '22621,1H01101215222430333537516071V0314264363': 'm23',
    '22640,3H01102123252736505254636567V12313445': 'm12',
    '22650,2H002123252732505254566567V011231344547': 'm12',
    '22650,2H002123252742505254566567V011231344547': 'm12',
    '22660,0H101217232541505254616365V2126273243455660': 'm23',
    '22680,2H10212325273041505254567577V3234434547': 'm12',
    '22681,1H1012212325273041505254567577V32354346': 'm23',
    '22700,4H00040711131517307072V121432435263': 'm21',
    '22720,3H0004071113151730407072V121432435263': 'm21',
    '22721,2H101215222430333537517173V0314264363': 'm23',
    '22732,2H1012152224303335375171V0314264363': 'H73',
    '22750,2H1021232527304150525456737577V12324347': 'm12',
    '22770,0H10121416232527415052545671737577V21324344': 'm23',
    '22770,0H10121416232527415052545671737577V21324345': 'm23',
    '22770,0H10121416232527415052545671737577V21324347': 'm23',
    '22781,0H02112426303241435052547577V131620343555': 'm23',
    '23030,5H041012232544505254V021516355365': 'm24',
    '23040,6H031315222443535557V0114324462': 'm22',
    '23113,7H12232544505254V354365': 'm24',
    '23131,5H041012232544505254V1516355365': 'V02',
    '23132,6H1012232544505254V16355365': 'V15',
    '23154,5H0335525460V021434455051': 'm22',
    '23154,5H1335525460V031434455051': 'm22',
    '23162,0H010305071216202332343646V001421274464': 'm24',
    '23170,2H1114202225334043465254V16273436414550': 'm13',
    '23182,0H00020421333540465254V1317203237414550': 'm33',
    '23210,2H0002040612141622303234535557V10243352': 'm22',
    '23220,1H00020411142325525460V162224303547505155': 'm13',
    '23220,3H00020411142325525460V16223547505155': 'm24',
    '23241,4H03121533353741525460V1314224550': 'V16',
    '23241,4H12153032344346535567V1316254257': 'H04',
    '23242,5H03121533353741525460V224550': 'V14',
    '23242,5H12153032344346535567V254257': 'V16',
    '23242,6H121530323446535567V254257': 'm25',
    '23242,7H2242475355V031232465257': 'm13',
    '23243,6H1215303234535567V254257': 'H46',
    '23243,7H12333537525460V224550': 'm25',
    '23244,7H02254450525456V3565': 'm25',
    '23245,7H022544505254V3565': 'H56',
    '23254,7H05224351535557V3262': 'm13',
    '23260,1H000406111320223547525460V03141634455051': 'm22',
    '23261,2H04061113223547525460V03141634455051': 'H20',
    '23311,2H1012172224333550V030514263237414363': 'm24',
    '23330,5H1315172125353743505254V23323455': 'm22',
    '23334,6H02042550525456V354665': 'm24',
    '23334,7H12333537525460V4550': 'V22',
    '23340,4H03121533353741525460V131416224550': 'm33',
    '23343,4H10121425274244505254V133565': 'm33',
    '23345,7H123537525460V4550': 'H33',
    '23356,7H1235525460V4550': 'H37',
    '23371,0H021124263032414350525457V13162034354655': 'm13',
    '23380,0H031315172022364345535557V1114273234356267': 'm22',
    '23380,2H00020423252741505254567577V3234434547': 'm24',
    '23431,5H13151725353743505254V23323455': 'm22',
    '23452,5H0305224351535557V0211133262': 'm22',
    '23482,1H000204212325274550525456V3032364347': 'm24',
    '23482,1H0002042123252750525456V303236434547': 'm24',
    '23482,1H0002042123252750525456V313236434447': 'm24',
    '23571,0H02112426303241434650525457V131620343555': 'm13',
    '23582,1H000231333537434552626466V3241576172': 'm24',
    '23620,2H1114202235374446515355576062V31334042': 'm13',
    '23670,3H10121533425062646677V11143234546061': 'm24',
    '23680,1H10121421232527354150525456737577V324346': 'm24',
    '23682,1H000204212325275052545677V3032354346': 'm24',
    '23711,2H101215222430333537517173V0314264363': 'V60',
    '23712,1H030507202224265153555770V3235374144': 'H72',
    '23720,2H101215222430333537517173V031426436063': 'm24',
    '23721,1H01101215222430333537516071V0314264363': 'm24',
    '23721,1H03050720222426515355577072V3235374144': 'm13',
    '23760,0H10121416232527415052545671737577V21324344': 'm24',
    '23760,0H10121416232527415052545671737577V21324345': 'm24',
    '23760,0H10121416232527415052545671737577V21324347': 'm24',
    '23771,0H02112426303241435052547577V131620343555': 'm13',
    '23781,1H1012212325273041505254567577V32354346': 'm24',
    '24010,2H1316222527344144475355V11203133424657': 'm25',
    '24021,2H13222527344144475355V11203133424657': 'H16',
    '24021,6H1214232544505254V0613354365': 'H04',
    '24030,6H041214232544505254V0613354365': 'm25',
    '24040,5H031517222443535557V051112325462': 'm14',
    '24112,0H000204061115242731333541V071320264363': 'm14',
    '24113,6H1214232544505254V354365': 'V13',
    '24122,6H1214232544505254V13354365': 'V06',
    '24131,4H04252731333550525477V0214234446': 'm14',
    '24132,4H0114253240535567V041333425657': 'm34',
    '24210,3H00020412141622303234535557V10243352': 'm23',
    '24211,4H000212141622303234535557V243352': 'V10',
    '24212,5H00121422303234535557V243352': 'H16',
    '24212,5H10121422303244535557V243352': 'V35',
    '24213,6H1214223032345355V243352': 'H57',
    '24220,2H00020411142325525460V1622243547505155': 'm23',
    '24221,3H010314253240535567V04111333425657': 'm25',
    '24221,5H10121422303244535557V24333552': 'm34',
    '24300,1H041012142527314244505254V13162032356065': 'm25',
    '24313,5H101214223032445355V243352': 'H57',
    '24314,6H12142230325355V243352': 'H34',
    '24321,2H1012172224333550V030514263237414363': 'm14',
    '24324,5H1012142230325355V243352': 'H44',
    '24325,6H121422325355V243352': 'H30',
    '24326,7H1214225355V3352': 'H32',
    '24327,8H14225355V52': 'V33',
    '24332,3H02040625275052545677V1217354665': 'm34',
    '24333,4H020406255052545677V17354665': 'H27',
    '24334,5H0204062550525456V354665': 'H77',
    '24334,7H042550525456V354665': 'm23',
    '24341,3H0316202232343653555770V0513244143': 'm14',
    '24342,7H2242475355V031232465257': 'm23',
    '24400,2H03050720222446515355577072V3335404244': 'm14',
    '24402,0H03050726323441475355V1014273035424657': 'm14',
    '24423,0H050725344246576365V1314263345536667': 'm14',
    '24427,9H225355V52': 'H42',
    '24435,8H22425355V325257': 'V46',
    '24436,9H22425355V52': 'V32',
    '24443,7H2242475355V0332465257': 'V12',
    '24444,8H22425355V32465257': 'H47',
    '24502,1H030507202224264251535557V3135374044': 'm14',
    '24502,1H0305072022242651535557V313536404344': 'm14',
    '24502,1H0305072022242651535557V313537404244': 'm14',
    '24600,3H12151734455761636570V13163335536667': 'm14',
    '24602,1H050730323436424455616365V3546506675': 'm14',
    '24610,2H1114202235374446515355576062V31334042': 'm23',
    '24700,1H13151720222426324651535557707274V354144': 'm23',
    '24701,0H131517202224263746515355577072V32354144': 'm23',
    '24702,1H030507202224265153555770V3235374144': 'm23',
    '24730,0H11131517202224465153555770727476V26354044': 'm23',
    '24730,0H11131517202224465153555770727476V26354244': 'm23',
    '24730,0H11131517202224465153555770727476V26354344': 'm23',
    '24730,2H101215222430333537517173V031426436063': 'm14',
    '24731,1H01101215222430333537516071V0314264363': 'H73',
    '30043,1H000222334145506264V13143442546061': 'm31',
    '30043,4H00212325405254V132433414450': 'm20',
    '30053,5H02212430505254V1113253345': 'm20',
    '30071,5H21303335465254V10173237414550': 'm20',
    '30071,5H21333540465254V10173237414550': 'm20',
    '30121,1H01032325305254V1011122627354150557677': 'm20',
    '30122,1H10213443515355577376V22232430445062': 'm20',
    '30123,3H0003212325405254V132433414450': 'm20',
    '30134,3H10212325405254V022433414450': 'm20',
    '30162,2H000221333540465254V17203237414550': 'm20',
    '30200,3H04121530323446535567V00111314254257': 'm10',
    '30223,3H11131520222433355153V26324044': 'm40',
    '30240,0H12202231355153555761636570V13243342446667': 'm31',
    '30310,0H112325434547525461V0110122731333550515557': 'm20',
    '30311,0H112325434547525461V01102227313335505155': 'm20',
    '30330,2H0011172123253133365254V12131627414550': 'm20',
    '30340,2H101214232527354150525456737577V324346': 'm31',
    '30340,4H00031113151744465153555770V202143': 'm20',
    '30343,1H000204212325273550525456V30324346': 'm20',
    '30343,5H212325273234505254V103045': 'm20',
    '30355,1H00020406152427333541V13264363': 'm31',
    '30360,5H0111131517333641535557V21374245': 'm20',
    '30370,1H0002042123252730505254567577V3234434547': 'm20',
    '30370,1H0002212325273041505254567577V3234434647': 'm20',
    '30370,1H10121416232527415052545671737577V324347': 'm20',
    '30370,1H10121421232527354150525456737577V324346': 'm20',
    '30372,1H000204212325275052545677V3032354346': 'm20',
    '30380,2H10212325273041505254567577V3234434547': 'm20',
    '30381,1H00022123252736505254567577V3032414347': 'm20',
    '30402,5H111315173133535557V10142542': 'm20',
    '30414,4H212325273041434547V104260': 'm20',
    '30416,3H00021113151722414345V21': 'm20',
    '30421,0H020406202527335052545677V12173541444665': 'm31',
    '30421,0H2123252731335254617072V1011121314445063': 'm20',
    '30421,0H2123252731335254617072V1011121342445063': 'm20',
    '30430,3H000204111315323640515355V0623314650': 'm20',
    '30450,3H0112142123252731334550525456V132644': 'm20',
    '30460,1H10121416232527415052545671737577V324345': 'm20',
    '30461,2H00020423252741505254567577V32344345': 'm20',
    '30471,1H000221232527505254567577V303234414346': 'm20',
    '30471,1H1012212325273041505254567577V32354346': 'm20',
    '30473,0H20222426313335374345526567V41546172': 'm31',
    '30531,2H000407111315177072V1214213241435263': 'm20',
    '30550,1H10121416232527415052545671737577V324344': 'm20',
    '30551,0H00023133404547506264667577V143442536061': 'm20',
    '30551,4H21232527444650525667V1030324366': 'm20',
    '30561,2H00313335374345527375V02213241616672': 'm20',
    '30562,1H000231333537434552626466V3241576172': 'm20',
    '30563,2H00313335374345527375V3241616672': 'V02',
    '30572,1H000204212325274550525456V3032364347': 'm20',
    '30572,1H0002042123252750525456V303236434547': 'm20',
    '30572,1H0002042123252750525456V313236434447': 'm20',
    '30580,3H002123252732505254566567V1231344547': 'm20',
    '30580,3H002123252742505254566567V1231344547': 'm20',
    '30581,1H000204212325273050525456737577V324347': 'm20',
    '30583,2H00022123252734364250525467V3145': 'm20',
    '30632,2H10172325415052546365V262732434556': 'm31',
    '30640,0H10121721232541505254616365V26273243455156': 'm20',
    '30641,1H212325273032364143475052547577V113445': 'm20',
    '30641,4H21232527444650527577V1030324365': 'm20',
    '30650,5H012123252736505254636567V203445': 'm20',
    '30651,2H21232527505254566567V10303234434547': 'm20',
    '30661,5H0121232527365052546567V203445': 'm20',
    '30662,2H00313335374345527375V023241616672': 'V21',
    '30670,3H01102123252736505254636567V12313445': 'm20',
    '30671,4H102123252736505254636567V313445': 'V12',
    '30682,3H21232527344350525456V1030324547': 'm20',
    '30780,2H1021232527304150525456737577V12324347': 'm20',
    '31020,3H0010222427333550V051214263237414363': 'm21',
    '31022,5H2224273335V0511142632374363': 'm21',
    '31033,4H00212325405254V132433414450': 'm30',
    '31043,0H000222334145506264V1314213442546061': 'm41',
    '31043,5H02212430505254V1113253345': 'm30',
    '31043,6H052224333537V1416324363': 'm21',
    '31061,5H21303335465254V10173237414550': 'm30',
    '31061,5H21333540465254V10173237414550': 'm30',
    '31121,2H01032325305254V10111226273541505576': 'm30',
    '31123,5H2224273335V05142632374363': 'V11',
    '31133,3H0003212325405254V132433414450': 'm30',
    '31140,2H1015233234656773V14222641444652616472': 'm21',
    '31142,3H0021333540465254V17203237414550': 'm41',
    '31210,0H112325434547525461V0110122731333550515557': 'm30',
    '31211,0H112325434547525461V01102227313335505155': 'm30',
    '31220,1H101523323465677375V14222641445261647277': 'm21',
    '31220,1H1015233234677375V1422264144526164667277': 'm21',
    '31224,6H23255254V262735415055': 'm21',
    '31233,3H11131520222433355153V26324044': 'm30',
    '31234,3H10212325405254V022433414450': 'm30',
    '31241,2H1012172224333550V030514263237414363': 'm21',
    '31261,3H0316202232343653555770V0513244143': 'm32',
    '31271,3H10121533425062646677V143234546061': 'V11',
    '31272,3H16202232343653555770V0513244143': 'H03',
    '31303,5H22243133535557V1125304245': 'm21',
    '31321,0H020406202527335052545677V12173541444665': 'm33',
    '31322,1H020406202527335052545677V1217354665': 'm41',
    '31340,2H0011172123253133365254V12131627414550': 'm30',
    '31345,1H00020406152427333541V13264363': 'm32',
    '31350,2H101214232527354150525456737577V324346': 'm32',
    '31350,3H0112142123252731334550525456V132644': 'm30',
    '31355,1H0002042123252750525456V303243': 'm41',
    '31360,1H0002042123252730505254567577V3234434547': 'm30',
    '31360,1H01112123252732343640637072V131620446264': 'm41',
    '31360,1H10121421232527354150525456737577V324346': 'm30',
    '31361,2H011121232527323440637072V1320446264': 'H36',
    '31370,2H10212325273041505254567577V3234434547': 'm30',
    '31370,3H10121533425062646677V11143234546061': 'm32',
    '31370,4H00031113151744465153555770V202143': 'm41',
    '31371,1H1012212325273041505254567577V32354346': 'm30',
    '31371,5H001113151744465153555770V2143': 'H03',
    '31382,2H21232527505254566567V303234434547': 'V10',
    '31382,5H001113151744465153555770V43': 'V21',
    '31400,0H000204061113151732415153555760626466V2240': 'm21',
    '31400,3H21232531335254617072V00112444506371': 'm30',
    '31401,3H011022245153555770V12203134435054': 'm21',
    '31403,6H22243133535557V11254245': 'm21',
    '31411,1H000207111315515355577073V041721244044': 'm21',
    '31412,3H0110222451535557V12203134435054': 'm21',
    '31413,4H10222451535557V203134435054': 'V12',
    '31414,5H212325273041434547V1042': 'm30',
    '31421,1H2123252731335254617072V10111242445063': 'm30',
    '31431,0H2123252731335254617072V1011121314445063': 'm30',
    '31452,1H000204212325274550525456V3032364347': 'm41',
    '31452,1H000204212325275052545677V3032354346': 'm41',
    '31452,1H0002042123252750525456V303236434547': 'm41',
    '31461,1H00022123252736505254567577V3032414347': 'm41',
    '31463,0H20222426313335374345526567V41546172': 'm32',
    '31465,4H2123252744505254V303245': 'm41',
    '31470,1H0002042123252730505254567577V3234434547': 'm30',
    '31470,1H0002212325273041505254567577V3234434647': 'm30',
    '31472,1H000221232527505254567577V3032414347': 'H36',
    '31474,3H21232527344350525456V303245': 'm41',
    '31480,0H10121416232527415052545671737577V21324347': 'm41',
    '31480,4H01101533424650535557V162124323644': 'm41',
    '31481,1H000204212325273050525456737577V324347': 'm30',
    '31481,2H21232527505254566567V10303234434547': 'm41',
    '31484,4H212325274446505267V303243': 'm41',
    '31484,4H212325274446505277V303243': 'm41',
    '31502,6H0222243133535557V11254245': 'm21',
    '31551,1H000221232527505254567577V303234414346': 'm41',
    '31553,0H21232527303234364244556062V46536675': 'm30',
    '31570,0H10121416232527415052545671737577V21324344': 'm41',
    '31570,0H10121416232527415052545671737577V21324345': 'm41',
    '31571,1H0002042123252730505254567577V32344345': 'V47',
    '31571,1H0002212325273041505254567577V32344346': 'V47',
    '31580,7H1533424650535557V1624323644': 'm21',
    '31583,0H030507222630345052545676V1725323364': 'm21',
    '31611,1H17323436404446516571737577V2742435470': 'm21',
    '31611,2H1017232541505254616365V262732434556': 'V60',
    '31620,2H1017232541505254616365V26273243455660': 'm21',
    '31621,0H10121721232541505254616365V262732434556': 'V51',
    '31621,2H1017232541505254616365V262732434556': 'm32',
    '31622,2H10172325415052546365V262732434556': 'H61',
    '31630,0H10121721232541505254616365V26273243455156': 'm30',
    '31630,1H1017232541505254616365V2126273243455660': 'm41',
    '31634,3H011522243033353751V14264363': 'm21',
    '31634,3H101522243033353751V14264363': 'm21',
    '31651,1H212325273032364143475052547577V113445': 'm30',
    '31680,2H1021232527304150525456737577V12324347': 'm30',
    '31712,1H17323436404446516571737577V27424354': 'V70',
    '32033,5H02212430505254V1113253345': 'm31',
    '32051,5H21333540465254V10173237414550': 'm31',
    '32052,4H2023253140435254V162433414450': 'V05',
    '32120,4H00232545475254V122124273235414355': 'm22',
    '32133,4H00212325405254V132433414450': 'm31',
    '32142,4H21333540465254V17203237414550': 'm31',
    '32151,3H202225334043465254V16273436414550': 'm33',
    '32151,4H2023253140435254V05162433414450': 'V12',
    '32152,5H21303335465254V173237414550': 'V10',
    '32153,4H2023253140435254V2433414450': 'V16',
    '32161,5H21303335465254V10173237414550': 'm31',
    '32162,6H0103050711131530333537V22': 'm22',
    '32180,4H01101533424650535557V162124323644': 'm22',
    '32210,3H101723253652546170V1221273035505562': 'm22',
    '32224,3H01102325405254V022433414450': 'm12',
    '32224,3H10212325405254V022433414450': 'm31',
    '32250,4H2023253140435254V0512162433414450': 'm22',
    '32251,3H0316202232343653555770V0513244143': 'm33',
    '32252,0H010305071216202332343646V001421274464': 'm22',
    '32253,0H000222334145506264V1314213442546061': 'm33',
    '32303,6H22243133535557V11254245': 'm31',
    '32310,3H04121530323446535567V00111314254257': 'm30',
    '32311,3H1723253652546170V1221273035505562': 'H10',
    '32312,4H1723253652546170V273035505562': 'V12',
    '32312,5H111315173133535557V10142542': 'm30',
    '32314,8H232544505254V3565': 'm22',
    '32315,6H2123252741434547V42': 'V10',
    '32330,0H00232545475254607073V12132124273235414355': 'm22',
    '32333,4H10222451535557V203134435054': 'm42',
    '32335,1H00020406152427333541V13264363': 'm34',
    '32340,3H0112142123252731334550525456V132644': 'm31',
    '32342,2H1121232527323440637072V1320446264': 'H01',
    '32350,0H020406202527335052545677V1217213541444665': 'm22',
    '32350,1H10121421232527354150525456737577V324346': 'm31',
    '32350,2H0011172123253133365254V12131627414550': 'm31',
    '32351,2H011121232527323440637072V1320446264': 'm31',
    '32360,2H10212325273041505254567577V3234434547': 'm31',
    '32361,1H1012212325273041505254567577V32354346': 'm31',
    '32363,4H172325305254V16273234414550': 'm22',
    '32370,3H0613152022242634364251535557V142143': 'm33',
    '32372,2H1620222426333547647577V1427436365': 'm33',
    '32380,5H0111131517333641535557V21374245': 'm42',
    '32381,1H000204212325273050525456737577V324347': 'm31',
    '32382,3H21232527505254566567V3234434547': 'm31',
    '32383,1H0211242630324143505254V1316343555': 'm22',
    '32400,3H21232531335254617072V00112444506371': 'm31',
    '32401,3H000407111315177072V12142132435263': 'm42',
    '32401,4H21232531335254617072V2444506371': 'V11',
    '32402,6H0222243133535557V11254245': 'm31',
    '32414,6H2123252741434547V1042': 'm31',
    '32420,0H112325434547525461V0110122731333550515557': 'm22',
    '32421,0H112325434547525461V01102227313335505155': 'm22',
    '32421,2H2123252731335254617072V101142445063': 'm31',
    '32431,1H2123252731335254617072V10111213445063': 'm31',
    '32435,4H000211131551535570V1724': 'm42',
    '32442,1H0002042123252750525456V313236434447': 'm42',
    '32443,3H2123253744525460V262732505561': 'm31',
    '32443,3H2123253744525460V273236505561': 'm31',
    '32451,2H00172123253133365254V12131627414550': 'H11',
    '32452,3H00172123253133365254V1627414550': 'V12',
    '32453,0H20222426313335374345526567V41546172': 'm33',
    '32460,1H0002042123252730505254567577V3234434547': 'm31',
    '32462,1H000221232527505254567577V3032414347': 'm31',
    '32470,3H10121533425062646677V11143234546061': 'm22',
    '32471,1H0002212325273041505254567577V32344346': 'm31',
    '32473,2H16202224263335647577V1427436365': 'H47',
    '32482,5H001113151744465153555770V43': 'm31',
    '32483,2H02242630324143505254V1316343555': 'm22',
    '32533,2H2123253744525460V26273142505561': 'm42',
    '32534,3H011522243033353751V14264363': 'm31',
    '32534,3H101522243033353751V14264363': 'm31',
    '32541,1H000221232527505254567577V303234414346': 'm31',
    '32563,0H21232527303234364244556062V46536675': 'm31',
    '32573,0H030507222630345052545676V1725323364': 'm31',
    '32573,1H2123252730323641434750525475V3445': 'V11',
    '32580,2H1021232527304150525456737577V12324347': 'm31',
    '32583,5H0011131517444651535557V43': 'H70',
    '32600,4H00040711131517307072V121432435263': 'm22',
    '32620,3H0004071113151730407072V121432435263': 'm22',
    '32621,1H101721232541505254616365V262732434556': 'm31',
    '32660,1H1017232541505254616365V2126273243455660': 'm22',
    '32661,1H212325273032364143475052547577V113445': 'm31',
    '32672,1H2123252730323641434750525475V113445': 'H77',
    '32780,0H10121416232527415052545671737577V21324344': 'm22',
    '32780,0H10121416232527415052545671737577V21324345': 'm22',
    '32780,0H10121416232527415052545671737577V21324347': 'm22',
    '33052,5H06223547525460V031434455051': 'm34',
    '33054,5H2235525460V031434455051': 'H47',
    '33063,5H223547525460V031434455051': 'H06',
    '33143,4H2023253140435254V2433414450': 'm32',
    '33144,6H50626466V142334436061': 'm43',
    '33151,3H0316202232343653555770V0513244143': 'm34',
    '33154,6H35525460V021434455051': 'm23',
    '33154,6H35525460V031434455051': 'm23',
    '33161,3H202225334043465254V16273436414550': 'm34',
    '33163,4H202232343653555770V13244143': 'V05',
    '33233,7H0350546264V1214346061': 'V55',
    '33234,6H10121434505254V132245': 'm34',
    '33234,8H03535567V22425657': 'm13',
    '33235,7H10121434505254V45': 'V13',
    '33245,8H2544505254V3565': 'm23',
    '33251,4H03202232343653555770V0513244143': 'm34',
    '33262,0H010305071216202332343646V001421274464': 'm32',
    '33262,4H202232343653555770V0513244143': 'H03',
    '33282,0H00020421333540465254V1317203237414550': 'm34',
    '33310,4H04121530323446535567V111314254257': 'm32',
    '33313,6H22243133535557V11254245': 'm32',
    '33321,0H020406202527335052545677V12173541444665': 'V21',
    '33322,2H0204062527335052545677V1217354665': 'm31',
    '33322,5H232545475254V21242732354155': 'm43',
    '33323,4H10121425274244505254V133565': 'm34',
    '33324,6H23255254V262735415055': 'm31',
    '33340,0H020406202527335052545677V1217213541444665': 'm32',
    '33340,4H00232545475254V122124273235414355': 'm43',
    '33343,2H000221232527505254567577V303243': 'm43',
    '33343,2H10122325273041505254567577V3243': 'm43',
    '33343,4H10222451535557V203134435054': 'm32',
    '33343,5H10121425274244505254V3565': 'm23',
    '33344,6H1214254244505254V3565': 'H27',
    '33345,7H122544505254V3565': 'H42',
    '33345,8H03535567V224257': 'V56',
    '33346,8H25445254V3565': 'H50',
    '33346,9H5355V224257': 'H67',
    '33347,10H5355V42': 'V22',
    '33350,4H03121533353741525460V131416224550': 'm34',
    '33353,0H000222334145506264V1314213442546061': 'm34',
    '33356,8H23535557V2242': 'm34',
    '33356,8H35525460V4550': 'm23',
    '33357,9H22535557V': 'm34',
    '33357,9H235355V42': 'H57',
    '33360,3H0613152022242634364251535557V142143': 'm34',
    '33362,2H1620222426333547647577V1427436365': 'm34',
    '33383,2H02242630324143505254V1316343555': 'm32',
    '33401,5H232531335254617072V2444506371': 'm32',
    '33414,6H101214163134505254V45': 'm34',
    '33421,3H2123252731335254617072V1011445063': 'm32',
    '33430,5H1315172125353743505254V23323455': 'm23',
    '33431,2H2123252731335254617072V101112445063': 'm32',
    '33432,7H0350546264V121434556061': 'm32',
    '33433,3H001721232531365254V1627414550': 'H33',
    '33438,7H1012144244V': 'm44',
    '33439,8H121444V': 'H42',
    '33441,5H13151725353743505254V23323455': 'm23',
    '33442,3H00172123253133365254V1627414550': 'm32',
    '33443,0H20222426313335374345526567V41546172': 'm34',
    '33444,4H10222451535557V2031344354': 'V50',
    '33445,3H17232541505254V2627324345': 'm43',
    '33458,9H225355V': 'H57',
    '33458,9H235355V': 'V42',
    '33463,0H21232527303234364244556062V46536675': 'm32',
    '33463,4H232527505254566567V32344345': 'm43',
    '33480,5H0111131517333641535557V21374245': 'm32',
    '33521,0H0002040611131517325153555760626466V2240': 'H41',
    '33530,0H000204061113151732415153555760626466V2240': 'm34',
    '33530,4H00232545475254V122124273235414355': 'm43',
    '33532,0H0002040611131517325153555760626466V22': 'V40',
    '33533,3H2123253744525460V262742505561': 'm32',
    '33533,3H2224263043535567V202145525766': 'm34',
    '33543,2H2123253744525460V26273142505561': 'm32',
    '33573,2H23252730323641434750525475V3445': 'm32',
    '33573,5H0011131517444651535557V43': 'm32',
    '34021,3H222527344144475355V11203133424657': 'm24',
    '34121,3H010314253240535567V04111333425657': 'm24',
    '34122,4H0114253240535567V041333425657': 'V11',
    '34131,4H04252731333550525477V0214234446': 'm24',
    '34132,5H01253240535567V041333425657': 'm24',
    '34141,3H0316202232343653555770V0513244143': 'm24',
    '34203,2H05212335374446535557V1114323352': 'm35',
    '34213,1H0002040615242731333541V1320264363': 'H11',
    '34222,1H000204061115242731333541V1320264363': 'm35',
    '34231,5H10121422303244535557V24333552': 'm44',
    '34235,7H24505254V13253345': 'm44',
    '34300,2H041012142527314244505254V131632356065': 'm24',
    '34302,0H03050726323441475355V1014273035424657': 'm24',
    '34311,2H1012142527314244505254V131632356065': 'H04',
    '34312,3H1012142527314244505254V13323565': 'V16',
    '34313,4H10121425274244505254V133565': 'H31',
    '34314,1H0002040615242731333541V13264363': 'V20',
    '34320,4H04121530323446535567V111314254257': 'm33',
    '34322,3H02040625275052545677V1217354665': 'm33',
    '34322,6H232545475254V212427354155': 'm33',
    '34323,0H050725344246576365V1314263345536667': 'm24',
    '34323,4H202224515355576062V33354244': 'm35',
    '34323,6H22243133535557V11254245': 'm33',
    '34325,1H00020406152427333541V13264363': 'H31',
    '34327,9H225355V52': 'm24',
    '34330,3H0112142123252731334550525456V132644': 'm32',
    '34331,1H0002212325273041505254567577V32344346': 'm43',
    '34331,1H10121416232527415052545671737577V3243': 'm43',
    '34332,2H0002042123252750525456737577V3243': 'm43',
    '34332,2H00020421232527505254567577V324345': 'm43',
    '34332,2H00020423252741505254567577V324345': 'm43',
    '34332,2H1012142325274150525456737577V3243': 'm43',
    '34332,2H1021232527304150525456737577V3243': 'm43',
    '34332,2H10212325273041505254567577V324345': 'm43',
    '34332,2H1121232527323440637072V1320446264': 'm32',
    '34332,4H1723253652546170V273035505562': 'm32',
    '34332,5H111315173133535557V10142542': 'm32',
    '34333,4H1022243153556677V2032525765': 'V37',
    '34333,5H101214163436505254V132245': 'V17',
    '34334,6H10121434505254V132245': 'H36',
    '34334,6H22243133535557V254245': 'V11',
    '34334,8H2550525456V354665': 'm24',
    '34335,6H2123252741434547V42': 'm32',
    '34335,8H2550525456V3565': 'V46',
    '34336,6H232527445052V3243': 'm43',
    '34336,7H24505254V253345': 'V13',
    '34336,8H22535557V3262': 'H51',
    '34337,7H23255052V3243': 'H44',
    '34337,9H22535557V': 'V32',
    '34411,5H232531335254617072V2444506371': 'm33',
    '34412,5H10121416313446505254V264245': 'V30',
    '34414,5H101214163134505254V2645': 'H46',
    '34422,5H2325335254617072V2444506371': 'H31',
    '34423,5H10121416313446505254V2645': 'V42',
    '34430,0H000204061113151732415153555760626466V2240': 'm44',
    '34430,4H00232545475254V122124273235414355': 'm33',
    '34431,3H2123252731335254617072V1011445063': 'm33',
    '344310,9H23V': 'H52',
    '34432,1H0002212325273041505254567577V324346': 'V34',
    '34432,1H101214162325274150525456737577V3243': 'H71',
    '34432,4H2123252731335254617072V445063': 'V11',
    '34433,0H20222426313335374345526567V41546172': 'm35',
    '34433,2H00020421232527505254567577V3243': 'V45',
    '34433,2H00020423252741505254567577V3243': 'V45',
    '34433,2H00022123252730505254567577V3243': 'V46',
    '34433,2H000221232527505254567577V303243': 'm33',
    '34433,2H10121423252741505254567577V3243': 'H73',
    '34433,2H10122325273041505254567577V3243': 'm33',
    '34433,2H10212325273041505254567577V3243': 'H73',
    '34433,5H2325335254617072V44506371': 'V24',
    '34434,3H0002212325275052545677V3243': 'H75',
    '34434,3H1012232527415052545677V3243': 'H75',
    '34434,3H2123252730415052545677V3243': 'H75',
    '34434,6H2325405254V2433414450': 'm44',
    '34435,4H002123252750525456V3243': 'H77',
    '34435,4H102325274150525456V3243': 'H77',
    '34435,4H232527304150525456V3243': 'H77',
    '34436,5H21232527505254V3243': 'H56',
    '34436,6H21232527414345V42': 'H47',
    '34437,6H2325275052V3243': 'H54',
    '34437,7H212325414345V': 'V42',
    '34438,7H232552V3243': 'H50',
    '34438,8H23254345V': 'H41',
    '34439,8H232552V': 'V43',
    '344410,9H23V': 'H43',
    '34443,4H20222426345355667577V435764': 'H36',
    '34444,5H212325335254617072V4450': 'V63',
    '34444,5H2224345355667577V435776': 'V64',
    '34445,4H10222446535557V20354244': 'm35',
    '34445,6H22243453556677V4357': 'H75',
    '34446,7H2224345355V4357': 'H77',
    '34447,8H22245355V43': 'H34',
    '34449,8H222444V': 'H42',
    '34500,5H0610121416313446505254V26304245': 'm35',
    '34501,2H14202235374446515355576062V31334042': 'H11',
    '34511,5H10121416313446505254V26304245': 'H06',
    '34523,2H20222431353740444653555772V3342': 'm35',
    '34532,1H0002040611131517325153555760626466V': 'm33',
    '34535,6H23255254V2433414450': 'H40',
    '345410,10HV': 'H53',
    '34543,3H2123253744525460V262742505561': 'm33',
    '34544,4H10222451535557V2031344354': 'm33',
    '34545,5H2224535557V2031344354': 'H51',
    '34546,6H22245355V20313443': 'H57',
    '34546,6H22245355V34434657': 'V23',
    '34547,7H22245355V3443': 'V20',
    '34547,7H22245355V4357': 'V46',
    '34548,8H22245355V': 'V43',
    '34549,9H2354V': 'H52',
    '34600,2H1114202235374446515355576062V31334042': 'm24',
    '40113,5H02212430505254V1113253345': 'm41',
    '40132,1H10213443515355577376V22232430445062': 'm30',
    '40140,0H12202231355153555761636570V13243342446667': 'm30',
    '40143,1H000222334145506264V13143442546061': 'm30',
    '40154,4H2430324143505254V13343555': 'm41',
    '40162,3H0021333540465254V17203237414550': 'm30',
    '40204,1H0331343641437577V12324447646667': 'm30',
    '40213,3H11131520222433355153V26324044': 'm50',
    '40223,2H11131520222433355361V2632445062': 'm50',
    '40233,1H10213443515355577376V232430445062': 'V22',
    '40333,5H212325273234505254V103045': 'm30',
    '40350,4H00031113151744465153555770V202143': 'm30',
    '40351,1H0002042123252730505254567577V32344345': 'm41',
    '40360,1H10121416232527415052545671737577V324345': 'm30',
    '40361,2H00020423252741505254567577V32344345': 'm30',
    '40361,2H1021232527304150525456737577V324347': 'V12',
    '40361,2H10212325273041505254567577V32344345': 'm41',
    '40362,1H000204212325275052545677V3032354346': 'm30',
    '40365,1H00020406152427333541V13264363': 'm30',
    '40370,2H1021232527304150525456737577V12324347': 'm41',
    '40370,5H0111131517333641535557V21374245': 'm30',
    '40371,1H00022123252736505254567577V3032414347': 'm30',
    '40371,1H000221232527505254567577V303234414346': 'm30',
    '40381,3H002123252732505254566567V31344547': 'V12',
    '40426,3H00021113151722414345V21': 'm30',
    '40430,4H0002111315323640515355V0623314650': 'm30',
    '40431,0H020406202527335052545677V12173541444665': 'm30',
    '40432,1H020406202527335052545677V1217354665': 'V44',
    '40440,2H101214232527354150525456737577V324346': 'm30',
    '40443,1H000204212325273550525456V30324346': 'm30',
    '40450,1H10121416232527415052545671737577V324344': 'm30',
    '40461,1H0002212325273041505254567577V32344346': 'm41',
    '40470,1H10121416232527415052545671737577V324347': 'm30',
    '40471,3H252730324143505254566567V34364547': 'm41',
    '40472,1H000204212325273050525456737577V3243': 'm41',
    '40480,3H002123252732505254566567V1231344547': 'm30',
    '40480,3H002123252742505254566567V1231344547': 'm30',
    '40483,2H00022123252734364250525467V3145': 'm30',
    '40483,4H2527303641434750525456V3445': 'm41',
    '40483,4H2527303641434750525475V3445': 'm41',
    '40541,0H00023133404547506264667577V143442536061': 'm30',
    '40551,1H1012212325273041505254567577V32354346': 'm41',
    '40552,1H000231333537434552626466V3241576172': 'm30',
    '40561,4H21232527444650525667V1030324366': 'm30',
    '40561,5H0121232527365052546567V203445': 'm30',
    '40562,1H000204212325274550525456V3032364347': 'm30',
    '40562,1H0002042123252750525456V303236434547': 'm30',
    '40562,1H0002042123252750525456V313236434447': 'm30',
    '40571,1H10121416232527415052545671737577V3243': 'V47',
    '40571,4H102123252736505254636567V313445': 'm30',
    '40573,0H20222426313335374345526567V41546172': 'm30',
    '40582,3H21232527344350525456V1030324547': 'm30',
    '40631,2H000407111315177072V1214213241435263': 'm30',
    '40635,1H00023133353743455264V32416172': 'H62',
    '40642,2H10172325415052546365V262732434556': 'm30',
    '40643,3H1723254150525465V262732434556': 'H63',
    '40644,1H0002313335374345526264V32416172': 'H66',
    '40651,4H21232527444650527577V1030324365': 'm30',
    '40653,1H000231333537434552626466V32416172': 'V57',
    '40660,5H012123252736505254636567V203445': 'm30',
    '40661,2H21232527505254566567V10303234434547': 'm30',
    '40663,2H00313335374345527375V3241616672': 'm30',
    '40746,2H0031333537434552V32416172': 'H73',
    '40755,2H003133353743455273V32416172': 'H75',
    '40764,2H00313335374345527375V32416172': 'V66',
    '41013,5H02212430505254V1113253345': 'm42',
    '41023,5H2224273335V05142632374363': 'm31',
    '41030,3H0010222427333550V051214263237414363': 'm31',
    '41040,0H12202231355153555761636570V13243342446667': 'm40',
    '41040,2H1015233234656773V14222641444652616472': 'm31',
    '41042,2H1012172224333550V0514263237414363': 'V03',
    '41053,0H000222334145506264V1314213442546061': 'm42',
    '41053,6H052224333537V1416324363': 'm31',
    '41113,6H022124505254V1113253345': 'm40',
    '41120,1H101523323465677375V14222641445261647277': 'm31',
    '41120,1H1015233234677375V1422264144526164667277': 'm31',
    '41131,3H10222427333550V051214263237414363': 'H00',
    '41141,2H1012172224333550V030514263237414363': 'm31',
    '41142,3H10222427333550V0514263237414363': 'V12',
    '41152,3H0021333540465254V17203237414550': 'm40',
    '41164,4H2430324143505254V13343555': 'm42',
    '41171,3H10121533425062646677V143234546061': 'm31',
    '41214,1H0331343641437577V12324447646667': 'm40',
    '41233,2H11131520222433355361V2632445062': 'm40',
    '41243,3H11131520222433355153V26324044': 'm31',
    '41270,5H0111131517333641535557V21374245': 'm40',
    '41271,1H0123323440515557627074V21424554636572': 'm31',
    '41272,4H16202232343653555770V05244143': 'm31',
    '41273,5H202232343653555770V244143': 'H16',
    '41300,0H000204061113151732415153555760626466V2240': 'm31',
    '41311,3H011022245153555770V12203134435054': 'm21',
    '41312,2H121416232527323454V21354363657476': 'm21',
    '41325,4H15222430333537V14264363': 'm42',
    '41351,2H00020421232527505254567577V32344345': 'm40',
    '41352,1H0002042123252750525456V303236434547': 'm51',
    '41353,2H000204212325275052545677V324346': 'V35',
    '41355,2H0002042123252750525456V3243': 'm31',
    '41360,4H00031113151744465153555770V202143': 'm40',
    '41361,1H0002042123252730505254567577V32344345': 'm42',
    '41361,1H0002212325273041505254567577V32344346': 'm42',
    '41371,3H002123252732505254566567V31344547': 'm40',
    '41371,3H252730324143505254566567V34364547': 'm42',
    '41372,3H21232527344350525456V1030324547': 'm51',
    '41380,2H1021232527304150525456737577V12324347': 'm42',
    '41380,4H01101533424650535557V162124323644': 'm42',
    '41383,2H00022123252734364250525467V3145': 'm40',
    '41422,1H020406202527335052545677V1217354665': 'm51',
    '41424,5H212325273234505254V3045': 'm51',
    '41426,4H001113151722414345V21': 'm40',
    '41430,5H00111315323640515355V0623314650': 'm40',
    '41431,6H111315323640515355V06234650': 'V31',
    '41441,0H00023133404547506264667577V143442536061': 'm40',
    '41442,1H000204212325274550525456V3032364347': 'm51',
    '41451,1H00022123252736505254567577V3032414347': 'm51',
    '41451,1H000221232527505254567577V303234414346': 'm51',
    '41452,2H000204212325274550525456V32364347': 'm31',
    '41452,2H000204212325275052545677V32354346': 'm31',
    '41452,2H0002042123252750525456V3236434547': 'm31',
    '41454,2H000204212325275052545677V3243': 'V46',
    '41454,2H0002042123252750525456V324347': 'V36',
    '41455,1H0002042123252750525456V303243': 'm51',
    '41461,2H10212325273041505254567577V32344345': 'm42',
    '41462,1H0002042123252750525456V313236434447': 'm40',
    '41463,2H0002042123252750525456V32364347': 'H45',
    '41465,5H2123252744505254V3245': 'm31',
    '41471,1H000204212325273050525456737577V324347': 'm42',
    '41471,4H102123252736505254636567V313445': 'm40',
    '41472,2H0002042123252750525456737577V3243': 'm40',
    '41472,5H21232527365052546567V203445': 'H01',
    '41473,3H21232527344350525456V30324547': 'V10',
    '41473,4H2527303641434750525456V3445': 'm42',
    '41473,4H2527303641434750525475V3445': 'm42',
    '41474,4H21232527344350525456V3245': 'm31',
    '41475,4H2123252744505254V303245': 'H57',
    '41480,3H0613152022242634364251535557V142143': 'm42',
    '41482,4H102123252736505254636567V3445': 'V31',
    '41483,5H21232527365052546567V3445': 'V20',
    '41484,5H212325274446505267V3243': 'm31',
    '41484,5H212325274446505277V3243': 'm31',
    '41512,3H0110222451535557V12203134435054': 'H70',
    '41513,4H10222451535557V203134435054': 'm31',
    '41552,1H000204212325275052545677V3032354346': 'm51',
    '41553,0H030507222630345052545676V1725323364': 'm42',
    '41553,2H0002042123252750525456V32364347': 'V45',
    '41560,5H012123252736505254636567V203445': 'm40',
    '41561,1H1012212325273041505254567577V32354346': 'm42',
    '41563,0H20222426313335374345526567V41546172': 'm40',
    '41570,7H1533424650535557V1624323644': 'm31',
    '41571,5H0121232527365052546567V203445': 'm40',
    '41571,5H2123252736505254636567V203445': 'H01',
    '41572,1H000204212325273050525456737577V3243': 'V47',
    '41574,3H21232527344350525456V303245': 'V47',
    '41574,4H212325274450525457V303245': 'V46',
    '41580,0H10121416232527415052545671737577V21324344': 'm42',
    '41580,0H10121416232527415052545671737577V21324345': 'm42',
    '41580,0H10121416232527415052545671737577V21324347': 'm42',
    '41581,2H21232527505254566567V10303234434547': 'm51',
    '41581,3H0613152022242634364251535557V1443': 'V21',
    '41582,5H21232527365052546567V203445': 'H01',
    '41584,4H212325274446505267V303243': 'H56',
    '41584,4H212325274446505277V303243': 'm51',
    '41640,1H1017232541505254616365V2126273243455660': 'm42',
    '41683,4H21232527444650525667V303243': 'm51',
    '41722,1H17323436404446516571737577V27424354': 'm31',
    '42023,5H02212430505254V1113253345': 'm32',
    '42030,0H12202231355153555761636570V13243342446667': 'm41',
    '42113,7H0224505254V1113253345': 'm41',
    '42123,4H00212325405254V132433414450': 'm32',
    '42123,4H2023253140435254V2433414450': 'm43',
    '42124,1H033436437577V122532444763646667': 'm32',
    '42141,3H202225334043465254V16273436414550': 'm32',
    '42142,5H21333540465254V173237414550': 'V10',
    '42151,5H21333540465254V10173237414550': 'm32',
    '42152,6H303335465254V173237414550': 'm32',
    '42153,0H000222334145506264V1314213442546061': 'm32',
    '42164,3H022430324143505254V13343555': 'm43',
    '42172,6H0103050711131530333537V22': 'm32',
    '42220,4H00232545475254V122124273235414355': 'm32',
    '42224,1H0331343641437577V12324447646667': 'm41',
    '42224,4H102325405254V022433414450': 'm32',
    '42225,2H033436437577V12324447646667': 'H41',
    '42237,6H01030507133335V': 'V22',
    '42246,6H01030507133335V22': 'H37',
    '42248,6H010305071335V': 'H33',
    '42255,6H0103050713333537V22': 'H15',
    '42259,6H0103050713V': 'H35',
    '42264,6H010305071315333537V22': 'H11',
    '42273,6H01030507111315333537V22': 'H30',
    '42280,4H01101533424650535557V162124323644': 'm32',
    '42280,5H0111131517333641535557V21374245': 'm41',
    '42310,0H000204061113151732415153555760626466V2240': 'm41',
    '42312,6H1113153240515355V06234650': 'H36',
    '42322,5H23253245475254V212427354155': 'm43',
    '42322,8H2325525460V2235505155': 'm22',
    '42333,2H0002071113155153555770V17244044': 'V21',
    '42334,3H0002071113155153555770V1724': 'V40',
    '42340,0H00232545475254607073V12132124273235414355': 'm32',
    '42342,1H0002042123252750525456V313236434447': 'm52',
    '42342,1H000221232527505254567577V3032414346': 'V34',
    '42353,4H172325305254V16273234414550': 'm32',
    '42357,7H313344V354043': 'm52',
    '42365,5H2123252744505254V3245': 'm41',
    '42370,1H101523323465677375V14222641445261647277': 'm52',
    '42370,1H1015233234677375V1422264144526164667277': 'm52',
    '42371,1H000204212325273050525456737577V324347': 'm32',
    '42371,1H0002212325273041505254567577V32344346': 'm32',
    '42371,1H0123323440515557627074V21424554636572': 'm41',
    '42380,3H0613152022242634364251535557V142143': 'm32',
    '42381,3H252730324143505254566567V34364547': 'm43',
    '42382,4H102123252736505254636567V3445': 'm41',
    '42382,4H232527505254566567V3234434547': 'm32',
    '42383,5H21232527365052546567V3445': 'H63',
    '42401,2H000407111315177072V1214213241435263': 'm52',
    '42411,2H0002071113155153555770V041721244044': 'm31',
    '42411,3H00040711131517407072V121432435263': 'H30',
    '42411,6H111315323640515355V06234650': 'm43',
    '42416,5H0002111315515355V17': 'H70',
    '42431,0H00023133404547506264667577V143442536061': 'm41',
    '42432,2H0002071113155153555770V1721244044': 'V04',
    '42433,2H00022123252734364250525467V3145': 'm44',
    '42433,2H2123253744525460V26273142505561': 'V10',
    '42433,4H10222451535557V203134435054': 'm52',
    '42435,3H00020711131551535570V1724': 'H57',
    '42435,5H000211131551535570V17': 'm32',
    '42436,4H001113151722414345V21': 'm41',
    '42437,5H0002111315515355V': 'V17',
    '42437,5H1113151722414345V': 'V21',
    '42438,6H001113155153V': 'H55',
    '42438,6H131517224345V': 'H41',
    '42439,7H11131553V': 'H51',
    '42441,1H000221232527505254567577V303234414346': 'm32',
    '424410,7H313335V': 'H43',
    '424410,8H3133V': 'H44',
    '42442,2H0002042123252750525456V3236434447': 'm32',
    '42443,4H23253744525460V262732505561': 'm32',
    '42443,4H23253744525460V273236505561': 'm32',
    '42449,6H31333543V41': 'm52',
    '42449,7H20313343V': 'm52',
    '42449,7H313343V30': 'm52',
    '42449,7H313344V40': 'V43',
    '42449,7H313344V41': 'm52',
    '42451,2H00020421232527505254567577V32344345': 'm41',
    '42452,1H000221232527505254567577V3032414347': 'm32',
    '42453,2H0002042123252750525456V32364347': 'm41',
    '42458,7H313344V4043': 'V35',
    '42460,2H10212325273041505254567577V3234434547': 'm32',
    '42461,1H0002042123252730505254567577V32344345': 'm32',
    '42461,1H1012212325273041505254567577V32354346': 'm32',
    '42462,2H0002042123252750525456737577V3243': 'm41',
    '42463,4H2527303641434750525456V3445': 'm32',
    '42465,5H2325273443505254V3245': 'H56',
    '42466,6H232527445052V3243': 'H46',
    '42471,5H2123252736505254636567V203445': 'm41',
    '42473,3H252730323641434750525475V3445': 'm43',
    '42474,5H232527344350525456V3245': 'm41',
    '42475,6H23252744465052V3243': 'm41',
    '42480,2H1021232527304150525456737577V12324347': 'm32',
    '42482,5H2123252736505254636567V3445': 'V20',
    '42482,5H21232527365052546567V203445': 'm41',
    '42483,2H16202224263335647577V1427436365': 'm32',
    '42483,6H232527365052546567V3445': 'm41',
    '42500,4H00040711131517307072V121432435263': 'm32',
    '42520,0H112325434547525461V0110122731333550515557': 'm32',
    '42520,3H0004071113151730407072V121432435263': 'm32',
    '42521,0H112325434547525461V01102227313335505155': 'm32',
    '42525,4H15222430333537V14264363': 'H51',
    '42534,4H1522243033353751V14264363': 'm32',
    '42543,2H0002042123252750525456V32364347': 'V44',
    '42553,1H000221232527505254567577V30324143': 'V47',
    '42553,1H0305072226345052545676V1725323364': 'm41',
    '42561,2H10212325273041505254567577V32344345': 'V47',
    '42563,0H030507222630345052545676V1725323364': 'm32',
    '42650,1H1017232541505254616365V2126273243455660': 'm32',
    '42670,2H1015233234656773V14222641444652616472': 'm52',
    '42680,0H10121416232527415052545671737577V21324344': 'm32',
    '42680,0H10121416232527415052545671737577V21324345': 'm32',
    '42680,0H10121416232527415052545671737577V21324347': 'm32',
    '42732,1H17323436404446516571737577V27424354': 'm41',
    '43035,6H0004071113151770V14': 'H72',
    '43046,6H00040711131517V14': 'H70',
    '43052,7H12155062646677V14346061': 'm44',
    '43053,8H5062646677V14346061': 'H15',
    '43054,6H35525460V021434455051': 'm33',
    '43054,6H35525460V031434455051': 'm33',
    '43122,6H303335465254V173237414550': 'm53',
    '43122,6H333540465254V173237414550': 'm53',
    '43123,5H212325405254V132433414450': 'm42',
    '43124,6H2325405254V2433414450': 'V13',
    '43133,4H2023253140435254V2433414450': 'm33',
    '43133,5H000407111315177072V121432': 'm53',
    '43134,6H000407111315177072V14': 'V12',
    '43134,8H03506264V14346061': 'V12',
    '43144,5H3250626466V142334436061': 'm53',
    '43144,7H50626466V1434436061': 'm33',
    '43145,8H506264V14346061': 'H03',
    '43159,6H0103050713V': 'm42',
    '43173,2H02242630324143505254V1316343555': 'm44',
    '43174,3H022430324143505254V13343555': 'H26',
    '43221,4H0023253245475254V12212427354155': 'm44',
    '43222,5H232545475254V21242732354155': 'H12',
    '43224,5H102325405254V2433414450': 'm42',
    '43233,8H03506264V1214346061': 'm33',
    '43244,7H32535567V1333425657': 'm53',
    '43254,6H23445557V143233455253': 'm53',
    '43256,8H23445557V3233': 'm53',
    '43262,1H06243335475256657377V26424553626475': 'm44',
    '43262,7H323437415355V1030424657': 'm44',
    '43262,7H323441475355V1030424657': 'm44',
    '43312,0H12141623252732344154V2135435063657476': 'm42',
    '43312,6H1113153240515355V06234650': 'p',
    '43321,5H2325305254636567V243241445061': 'm53',
    '43322,4H23253245475254V12212427354155': 'H00',
    '43322,6H1113153240515355V06234650': 'm42',
    '43322,6H23253052546567V2432414450': 'm53',
    '43322,6H232545475254V212427354155': 'm42',
    '43323,6H1113153240515355V062350': 'V46',
    '43324,4H102325405254V022433414450': 'm42',
    '43333,5H10222453556677V2032525765': 'm34',
    '43334,3H00022123252750525456V324347': 'V36',
    '43334,6H11131540515355V062350': 'H32',
    '43334,6H222440425355V20233252': 'V26',
    '43334,6H23253436505254V224245': 'm34',
    '43334,8H232544505254V3565': 'm32',
    '43334,8H2325525460V355055': 'V22',
    '43335,4H002123252750525456V3243': 'V47',
    '43335,5H2123252744505254V3245': 'm53',
    '43335,7H11131540515355V50': 'V23',
    '43335,7H2224405355V203252': 'V23',
    '43337,9H225355V52': 'm34',
    '43340,3H0023254547525470V122124273235414355': 'm53',
    '43341,1H0002212325273041505254567577V32344346': 'm53',
    '43341,1H10121416232527415052545671737577V3243': 'm53',
    '43341,5H13151725353743505254V23323455': 'm33',
    '43342,2H0002042123252750525456737577V3243': 'm53',
    '43342,2H00020421232527505254567577V324345': 'V34',
    '43342,2H00020423252741505254567577V324345': 'V34',
    '43342,2H1012142325274150525456737577V3243': 'm53',
    '43342,2H1021232527304150525456737577V3243': 'm53',
    '43342,2H10212325273041505254567577V324345': 'V34',
    '43343,1H000221232527505254567577V30324143': 'm53',
    '43343,1H1012212325273041505254567577V3243': 'm53',
    '43343,3H00022123252750525456V32364347': 'm53',
    '43345,7H32535567V33425657': 'V13',
    '43345,8H2544505254V3565': 'H23',
    '43345,8H25525460V355055': 'H23',
    '43346,6H232527445052V3243': 'm53',
    '43346,8H35525460V4550': 'm33',
    '43355,6H23445557V3233455253': 'V14',
    '43355,8H33535557V243244': 'm53',
    '43356,9H33535557V44': 'V24',
    '43357,8H234455V3233': 'H57',
    '43363,7H323437415355V30424657': 'V10',
    '43363,7H323441475355V30424657': 'V10',
    '43383,6H232527365052546567V3445': 'm42',
    '43410,0H000204061113151732415153555760626466V2240': 'm42',
    '43414,7H1012143134505254V45': 'm33',
    '43421,6H111315323640515355V06234650': 'm41',
    '43422,2H00023345475062646677V143442536061': 'm53',
    '43423,6H232545475254V2124273555': 'V41',
    '43424,6H212325273234505254V45': 'm41',
    '43425,7H10121434505254V45': 'H31',
    '43428,7H1113155355V': 'H40',
    '43441,2H00020421232527505254567577V32344345': 'm53',
    '43441,2H00020423252741505254567577V32344345': 'm53',
    '43441,2H10212325273041505254567577V32344345': 'm53',
    '434410,8H3335V': 'm42',
    '434410,9H22V': 'm45',
    '434410,9H23V': 'H44',
    '434410,9H32V': 'm45',
    '434410,9H33V': 'm42',
    '43443,3H001721232531365254V1627414550': 'm33',
    '43443,6H222440425355V2023263252': 'V46',
    '43444,3H1012232527343642505254V3244': 'm54',
    '43444,4H0017212325365254V27414550': 'H31',
    '43444,4H22253441445355V2031334257': 'V46',
    '43444,6H22243133535557V254245': 'm45',
    '43445,5H172123255254V27414550': 'H36',
    '43445,5H222541445355V20313342': 'H34',
    '43445,5H2325273443505254V3245': 'm45',
    '43446,6H1723255254V274550': 'V41',
    '43446,6H2225415355V203133': 'V42',
    '43446,6H232543505254V3245': 'H34',
    '43446,7H1022245355V2042': 'm45',
    '43446,7H2224535557V3542': 'm45',
    '43446,7H32345355V304257': 'V46',
    '43446,7H32535567V334257': 'V56',
    '43446,8H255254V355055': 'H60',
    '43447,10H5355V42': 'm33',
    '43447,8H22245355V42': 'V20',
    '43447,8H32345355V30': 'V42',
    '43447,8H355254V4550': 'H60',
    '43447,9H255254V35': 'V55',
    '43447,9H325355V42': 'm45',
    '43449,7H11131553V': 'H55',
    '43449,8H2344V32': 'V33',
    '43449,8H313343V': 'm42',
    '43452,6H222440425355V202326324652': 'm44',
    '43453,4H22253441445355V203133424657': 'H47',
    '43455,3H17232541505254V2627324345': 'm53',
    '43455,5H2022243344535557V3542': 'm53',
    '43455,7H32345355V30424657': 'H47',
    '43456,6H23445557V32335253': 'V45',
    '43457,7H234455V323352': 'H57',
    '43457,8H255254V3565': 'H44',
    '43457,9H33535557V': 'V44',
    '43458,10H5355V': 'm33',
    '43458,8H2344V3233': 'H55',
    '43458,9H255254V': 'V35',
    '43461,3H072225344144475355V16203133424657': 'm53',
    '43462,4H2225344144475355V203133424657': 'm53',
    '43463,3H252730323641434750525475V3445': 'm44',
    '43464,7H3234375355V30424657': 'H41',
    '43464,7H3234475355V30424657': 'H41',
    '43470,1H101523323465677375V14222641445261647277': 'm42',
    '43470,1H1015233234677375V1422264144526164667277': 'm42',
    '43473,4H232527505254566567V32344345': 'm53',
    '43481,3H252730324143505254566567V34364547': 'm44',
    '43513,3H00020411131517364244505254V45': 'm42',
    '43520,4H00232545475254V122124273235414355': 'm53',
    '43522,8H2325525460V2235505155': 'm42',
    '43527,7H111315405355V': 'H51',
    '43532,3H202235374446515355576062V313342': 'm54',
    '43533,4H16222440425355V203236525657': 'm44',
    '43533,4H202224515355576062V33354244': 'm54',
    '43533,4H2022313740444651535557V3342': 'm54',
    '43533,4H2022313740444653555772V3342': 'm54',
    '43533,4H22243043535567V202135525766': 'm44',
    '43533,4H22243043535567V202145525766': 'm33',
    '43533,4H22243043535567V203135525766': 'm44',
    '43533,4H23253744525460V262732505561': 'm52',
    '43533,4H23253744525460V273236505561': 'm52',
    '43533,8H2325525460V22355055': 'V51',
    '43534,5H10222453556677V20325257': 'V65',
    '43534,5H22243043535567V20215257': 'V66',
    '43534,5H22243043535567V20315257': 'V66',
    '43534,5H222440425355V2032525657': 'V36',
    '43535,6H1022245355V20325257': 'H77',
    '43535,6H20222453555762V3342': 'm54',
    '43535,6H222430435355V205257': 'H67',
    '43535,6H222433535557V254245': 'H31',
    '43535,6H2224405355V20325257': 'V56',
    '43535,6H22245355V2021325257': 'V46',
    '43536,7H1022245355V2052': 'V32',
    '43536,7H11131540515355V': 'V50',
    '43536,7H2224305355V2052': 'H43',
    '43536,7H2224335355V2542': 'H57',
    '43536,7H2224405355V2052': 'V32',
    '43536,7H22245355V202152': 'V32',
    '43537,8H22245355V42': 'H33',
    '43537,8H22245355V52': 'V20',
    '43538,7H1113155153V': 'H55',
    '43538,8H222453V42': 'm54',
    '43538,9H225355V': 'V52',
    '43538,9H325355V': 'V42',
    '43539,9H2453V': 'V42',
    '435410,10HV': 'H53',
    '435410,9H14V': 'H53',
    '435410,9H25V': 'H53',
    '43543,3H050720222426313353555760V3642': 'm44',
    '43543,3H202235374446515355576062V3342': 'V31',
    '43544,4H07202224263133535557V3642': 'H60',
    '43544,4H07202224265355576062V3342': 'm53',
    '43544,4H2022245153555760V33354244': 'H62',
    '43544,4H20223137404446535557V3342': 'H72',
    '43544,4H20223744465153555760V3342': 'H62',
    '43544,6H22245355V202132465257': 'm44',
    '43545,3H10122325273442505254V3244': 'H36',
    '43545,4H05071012141631535557V42': 'H43',
    '43545,5H001113151751535557V43': 'H44',
    '43545,5H2022242633535557V3642': 'H31',
    '43545,5H2022242643535557V3542': 'm44',
    '43545,5H2022242653555762V3342': 'H60',
    '43545,5H20222451535557V333542': 'H60',
    '43545,5H2022314446535557V3342': 'H40',
    '43545,5H2022444651535557V3342': 'H60',
    '43546,4H1223252742505254V3244': 'H34',
    '43546,5H0710121416315355V42': 'H57',
    '43546,6H0011131551535557V': 'V43',
    '43546,6H1022245355V203542': 'm44',
    '43546,6H20222426535557V42': 'H33',
    '43546,6H202224535557V3342': 'H62',
    '43546,6H202244535557V3342': 'H31',
    '43547,5H122325275254V3244': 'H50',
    '43547,6H101214165355V42': 'H31',
    '43547,6H22255355V203133': 'H41',
    '43547,7H111315515355V': 'H57',
    '43547,7H2022245355V42': 'H57',
    '43547,7H20225355V3342': 'H57',
    '43547,7H22245355V3542': 'V20',
    '43547,7H325355V334257': 'H67',
    '43548,10H5355V': 'V42',
    '43548,7H1012145355V': 'V42',
    '43548,7H22255355V33': 'V20',
    '43548,8H22245355V': 'V42',
    '43548,8H225355V33': 'V42',
    '43548,8H325355V33': 'V42',
    '43549,8H121453V': 'H55',
    '43549,8H222553V': 'H55',
    '43549,9H2253V': 'H55',
    '43549,9H3253V': 'H55',
    '43554,4H0507101214163143535557V42': 'm44',
    '43554,5H00111315174451535557V43': 'H46',
    '43558,9H335355V': 'H57',
    '43559,10H53V': 'H55',
    '43563,5H0011131517444651535557V43': 'm33',
    '43660,2H1015233234656773V14222641444652616472': 'm42',
    '44032,6H1215345761636570V13336667': 'm54',
    '44044,6H32535567V041333425657': 'm45',
    '44044,6H32535567V051333425657': 'm45',
    '44103,2H05212335374446535557V1114323352': 'm34',
    '44122,7H303335465254V1737414550': 'm43',
    '44122,7H333540465254V1737414550': 'm43',
    '44124,7H0224505254V13253345': 'V11',
    '44135,7H24505254V13253345': 'H02',
    '44149,6H0002040614V': 'm45',
    '44222,6H303335465254V173237414550': 'm54',
    '44222,6H333540465254V173237414550': 'm54',
    '44223,7H0224505254V1113253345': 'm54',
    '44236,8H506264V143460': 'V61',
    '44241,5H10121422303244535557V24333552': 'm45',
    '44245,8H506264V14346061': 'm43',
    '44247,8H506264V3460': 'V14',
    '44322,1H01233234405155627074V21424554636572': 'm54',
    '44330,4H00232545475254V122124273235414355': 'm34',
    '44331,5H232545475254V1221242732354155': 'H00',
    '44332,6H232545475254V212427354155': 'V32',
    '44333,2H00022123252734364250525467V3145': 'm54',
    '44333,4H202224515355576062V33354244': 'm34',
    '44338,7H1113155153V': 'm43',
    '44339,8H131553V': 'H51',
    '44342,4H2022242732424653555761V344552': 'm54',
    '44344,4H102224375355V112035424657': 'V33',
    '44344,6H22245355V202132465257': 'm35',
    '44345,5H3345506264V1434426061': 'm54',
    '44346,6H22245355V23354357': 'V46',
    '44346,6H33506264V34426061': 'V14',
    '44347,7H22245355V3543': 'V23',
    '44420,0H000204061113151732415153555760626466V2240': 'm43',
    '44421,5H2325305254636567V243241445061': 'm43',
    '44422,6H23253052546567V2432414450': 'm43',
    '44424,6H2325405254V2433414450': 'm54',
    '444310,10HV': 'm34',
    '44433,3H000221232527343650525467V3145': 'm42',
    '44438,7H1315174345V': 'm42',
    '44501,3H202235374446515355576062V31334042': 'm34',
    '44523,3H00020411131517364244505254V45': 'm43',
    '44523,3H202231353740444653555772V3342': 'm34',
    '44523,6H232530525467V2432414450': 'H65',
    '44533,3H16222440425355V20323436525657': 'm54',
    '44534,3H000204111315173644505254V45': 'H42',
    '44534,4H202224324253555761V344552': 'H27',
    '44534,6H2325305254V2432414450': 'H67',
    '44535,5H2022243242535561V3452': 'H57',
    '44535,5H2123252744505254V3245': 'm43',
    '44536,6H222432535561V3452': 'H42',
    '44537,7H2224325355V52': 'H61',
    '44538,8H22245355V': 'V52',
    '44542,1H0002040611131517325153555760626466V': 'm34',
    '44543,4H102224375355V11203335424657': 'm55',
    '44543,4H20222427324253555761V344552': 'H46',
    '44543,4H22243043535567V202135525766': 'm45',
    '44543,4H22243043535567V203135525766': 'm45',
    '44545,4H10222446535557V20354244': 'm34',
    '44545,4H1022245355V112035424657': 'H37',
    '44545,6H22245355V2335434657': 'H37',
    '44546,5H102224465355V203542': 'H57',
    '44546,5H1022245355V20354257': 'V46',
    '44547,6H122325275254V44': 'm43',
    '44548,7H1223255254V': 'V44',
    '44548,7H22245355V35': 'V43',
    '50020,2H122022355153555761636570V132433446667': 'm51',
    '50054,4H2430324143505254V13343555': 'm40',
    '50203,3H11131520222433355153V26324044': 'm60',
    '50213,2H11131520222433355361V2632445062': 'm60',
    '50243,1H000222334145506264V13143442546061': 'm40',
    '50265,1H00020406152427333541V13264363': 'm40',
    '50314,5H212325273234505254V3045': 'V10',
    '50315,1H103443515355577376V2324304462': 'H21',
    '50323,5H212325273234505254V103045': 'm40',
    '50324,1H10213443515355577376V2324304462': 'V50',
    '50333,1H10213443515355577376V232430445062': 'm40',
    '50350,1H10121416232527415052545671737577V324344': 'm40',
    '50350,1H10121416232527415052545671737577V324345': 'm40',
    '50351,2H00020423252741505254567577V32344345': 'm40',
    '50351,2H1021232527304150525456737577V324347': 'm40',
    '50351,2H10212325273041505254567577V32344345': 'm40',
    '50352,1H1012212325273041505254567577V324346': 'V35',
    '50361,1H00022123252736505254567577V3032414347': 'm40',
    '50361,1H000221232527505254567577V303234414346': 'm40',
    '50380,3H002123252742505254566567V1231344547': 'm40',
    '50383,4H2527303641434750525456V3445': 'm40',
    '50383,4H2527303641434750525475V3445': 'm40',
    '50416,1H103443515355577376V23244462': 'V30',
    '50422,1H020406202527335052545677V1217354665': 'm40',
    '50450,2H101214232527354150525456737577V324346': 'm40',
    '50451,1H0002212325273041505254567577V32344346': 'm40',
    '50451,1H1012212325273041505254567577V32354346': 'm40',
    '50453,1H000204212325273550525456V30324346': 'm40',
    '50461,2H1012142325274150525456737577V324346': 'H35',
    '50462,1H000204212325275052545677V3032354346': 'm40',
    '50462,1H0002042123252750525456V303236434547': 'm40',
    '50462,1H01233234405155627074V21424554636572': 'm51',
    '50464,1H0002042123252750525456V30324346': 'H35',
    '50482,3H21232527344350525456V1030324547': 'm40',
    '50546,1H000231333537434552V32416172': 'H64',
    '50551,4H21232527444650527577V1030324365': 'm40',
    '50552,1H000204212325274550525456V3032364347': 'm40',
    '50561,1H10121416232527415052545671737577V3243': 'm40',
    '50562,2H1012142325274150525456737577V3243': 'V46',
    '50565,1H0002042123252750525456V303243': 'V46',
    '50565,3H17232541505254V2627324345': 'H65',
    '50571,3H252730324143505254566567V34364547': 'm40',
    '50571,4H21232527444650525667V1030324366': 'm40',
    '50645,1H00023133353743455264V32416172': 'm40',
    '50646,2H0031333537434552V32416172': 'm40',
    '50653,3H1723254150525465V262732434556': 'm40',
    '50660,0H103133353743455255626466V3240414657606172': 'm40',
    '50664,3H1723254150525465V2627324345': 'V56',
    '50671,2H21232527505254566567V10303234434547': 'm40',
    '50731,2H000407111315177072V1214213241435263': 'm40',
    '50762,1H17323436404446516571737577V27424354': 'm51',
    '51030,2H122022355153555761636570V132433446667': 'm52',
    '51033,5H2224273335V05142632374363': 'm41',
    '51042,3H10172224333550V0514263237414363': 'm41',
    '51043,4H1722243335V0514263237414363': 'H50',
    '51043,4H2224273335V0514263237414363': 'H50',
    '51044,4H2430324143505254V13343555': 'm50',
    '51050,2H1015233234656773V14222641444652616472': 'm41',
    '51063,6H052224333537V1416324363': 'm41',
    '51122,0H12141623252732344154V2135435063657476': 'm52',
    '51130,1H101523323465677375V14222641445261647277': 'm41',
    '51130,1H1015233234677375V1422264144526164667277': 'm41',
    '51142,4H222427333550V0514263237414363': 'm41',
    '51161,3H10121533425062646677V143234546061': 'm41',
    '51212,2H121416232527323454V21354363657476': 'm41',
    '51243,2H11131520222433355361V2632445062': 'm41',
    '51253,3H11131520222433355153V26324044': 'm41',
    '51253,3H11131520222433355361V26324462': 'm61',
    '51254,2H000204213353555771V1022343563': 'm52',
    '51255,1H00020406152427333541V13264363': 'm50',
    '51301,3H00040711131517407072V121432435263': 'm52',
    '51325,5H152224333537V14264363': 'm41',
    '51326,3H31334472V15354043606163': 'm61',
    '51340,1H10121416232527415052545671737577V324344': 'm50',
    '51341,2H1021232527304150525456737577V324347': 'm50',
    '51343,1H000222334145506264V13143442546061': 'm50',
    '51351,1H00022123252736505254567577V3032414347': 'm50',
    '51351,1H000221232527505254567577V303234414346': 'm50',
    '51354,3H1012232527343642505254V3244': 'm41',
    '51362,1H0002042123252750525456V303236434547': 'm50',
    '51362,1H0002042123252750525456V313236434447': 'm41',
    '51370,3H002123252742505254566567V1231344547': 'm50',
    '51373,2H00022123252734364250525467V3145': 'm41',
    '51373,4H2527303641434750525456V3445': 'm50',
    '51373,4H2527303641434750525475V3445': 'm50',
    '51382,3H21232527344350525456V1030324547': 'm50',
    '51412,4H10222451535557V12203134435054': 'm31',
    '51414,5H212325273234505254V3045': 'm50',
    '51450,1H10121416232527415052545671737577V324345': 'm50',
    '51451,2H00020423252741505254567577V32344345': 'm50',
    '51451,2H10212325273041505254567577V32344345': 'm50',
    '51452,1H1012212325273041505254567577V324346': 'm50',
    '51465,1H0002042123252750525456V303243': 'm50',
    '51465,3H17232541505254V2627324345': 'm50',
    '51471,3H002123252732505254566567V31344547': 'm41',
    '51522,1H020406202527335052545677V1217354665': 'm50',
    '51536,1H1031333537434552V3240416172': 'm61',
    '51542,1H000204212325274550525456V3032364347': 'm50',
    '51551,1H0002212325273041505254567577V32344346': 'm50',
    '51551,1H10121416232527415052545671737577V3243': 'm50',
    '51552,2H1012142325274150525456737577V3243': 'm50',
    '51553,1H1012212325273041505254567577V3243': 'V46',
    '51560,7H1533424650535557V1624323644': 'm41',
    '51561,3H252730324143505254566567V34364547': 'm50',
    '51561,4H21232527444650527577V1030324365': 'm50',
    '51562,1H000204212325275052545677V3032354346': 'm50',
    '51562,1H01233234405155627074V21424554636572': 'm52',
    '51570,4H05232631445557V141632333640455253': 'm61',
    '51571,3H0613152022242634364251535557V1443': 'm41',
    '51572,3H002123252732505254566567V313445': 'V47',
    '51581,4H21232527444650525667V1030324366': 'm50',
    '51631,0H00023133404547506264667577V143442536061': 'm52',
    '51662,4H21232527444650527577V30324365': 'V10',
    '51663,0H20222426313335374345526567V41546172': 'm41',
    '51673,4H21232527444650525667V303243': 'V66',
    '51673,4H21232527444650527577V303243': 'V65',
    '51681,2H21232527505254566567V10303234434547': 'm50',
    '51682,4H21232527444650525667V30324366': 'V10',
    '51684,4H212325274446505277V303243': 'H75',
    '51752,1H17323436404446516571737577V27424354': 'm52',
    '52024,1H033436437577V122532444763646667': 'm42',
    '52025,2H033436435775V12324447646667': 'm42',
    '52030,1H122022355153555761636570V13243342446667': 'm42',
    '52034,4H2430324143505254V13343555': 'm51',
    '52043,5H1722243335V05142632374363': 'm51',
    '52043,5H2224273335V05142632374363': 'm51',
    '52073,6H052224333537V1416324363': 'm51',
    '52112,0H12141623252732344154V2135435063657476': 'm53',
    '52125,2H033436437577V12324447646667': 'm42',
    '52142,6H303335465254V173237414550': 'm42',
    '52142,6H333540465254V173237414550': 'm42',
    '52151,3H10121533425062646677V143234546061': 'm51',
    '52213,7H0224505254V1113253345': 'm42',
    '52222,5H0123253052546567V2432414450': 'm42',
    '52223,4H2023253140435254V2433414450': 'm42',
    '52241,3H202225334043465254V16273436414550': 'm42',
    '52243,5H111315202224333553V263244': 'm62',
    '52245,1H00020406152427333541V13264363': 'm51',
    '52253,4H11131520222433355153V263244': 'm51',
    '52253,4H111315202224333553V26324462': 'm51',
    '52270,1H101523323465677375V14222641445261647277': 'm62',
    '52270,1H1015233234677375V1422264144526164667277': 'm62',
    '52301,4H000407111315177072V121432435263': 'm42',
    '52320,4H00232545475254V122124273235414355': 'm42',
    '52333,1H000222334145506264V13143442546061': 'm51',
    '52335,5H152224333537V14264363': 'm51',
    '52336,3H31334472V15354043606163': 'm51',
    '52340,1H002325454752547073V12132124273235414355': 'm42',
    '52344,3H1012232527343642505254V3244': 'm51',
    '52352,1H0002042123252750525456V313236434447': 'm51',
    '52353,2H0002042123252750525456V32364347': 'm42',
    '52354,2H000204213353555771V1022343563': 'm53',
    '52355,5H2123252744505254V3245': 'm42',
    '52356,5H313344V153540436063': 'm62',
    '52357,6H2531334244V3541': 'm62',
    '52357,6H313344V35404360': 'V15',
    '52363,2H00022123252734364250525467V3145': 'm51',
    '52363,4H2527303641434750525456V3445': 'm51',
    '52363,4H2527303641434750525475V3445': 'm51',
    '52365,3H17232541505254V2627324345': 'm51',
    '52365,5H2325273443505254V3245': 'm42',
    '52372,4H232527505254566567V3234434547': 'm42',
    '52383,2H16202224263335647577V1427436365': 'm42',
    '52401,3H00040711131517407072V121432435263': 'm42',
    '52423,4H10222451535557V203134435054': 'm51',
    '52433,4H23253744525460V262732505561': 'm42',
    '52433,4H23253744525460V273236505561': 'm42',
    '52439,8H131553V': 'm42',
    '52440,1H10121416232527415052545671737577V324344': 'm51',
    '52441,2H1021232527304150525456737577V324347': 'm51',
    '52442,1H000221232527505254567577V3032414346': 'm42',
    '52443,2H0002042123252750525456V32364347': 'm42',
    '52448,7H2031334345V': 'V32',
    '52449,5H31333543V4161': 'm62',
    '52449,6H20313343V30': 'm53',
    '52449,6H25313344V41': 'H42',
    '52451,1H10121416232527415052545671737577V3243': 'm51',
    '52452,2H0002042123252750525456737577V3243': 'm42',
    '52453,1H000221232527505254567577V30324143': 'm42',
    '52453,1H1012212325273041505254567577V3243': 'm51',
    '52453,4H172325305254V16273234414550': 'm42',
    '52454,4H203133354345475254V123241': 'm53',
    '52455,5H2031333543454752V3241': 'V12',
    '52456,6H203133434547V3241': 'H35',
    '52456,6H232527445052V3243': 'm42',
    '52457,7H2031334345V32': 'H47',
    '52458,6H2531334244V41': 'V35',
    '52459,7H20313343V': 'H45',
    '52461,3H252730324143505254566567V34364547': 'm51',
    '52463,3H002123252732505254566567V3445': 'V31',
    '52463,3H002123252742505254566567V3445': 'V31',
    '52470,3H002123252742505254566567V1231344547': 'm51',
    '52471,1H0123323440515557627074V21424554636572': 'm42',
    '52472,3H002123252732505254566567V313445': 'm51',
    '52472,3H002123252742505254566567V313445': 'V12',
    '52501,2H000407111315177072V1214213241435263': 'm62',
    '52530,0H112325434547525461V0110122731333550515557': 'm42',
    '52531,0H00023133404547506264667577V143442536061': 'm42',
    '52531,0H112325434547525461V01101227313335505155': 'V57',
    '52531,0H112325434547525461V01102227313335505155': 'm42',
    '52531,0H112325434547525461V10122731323335505155': 'm42',
    '52532,1H1123254345475254V101227313335505155': 'H61',
    '52532,1H1123254345475254V102227313335505155': 'H61',
    '52541,1H0002212325273041505254567577V32344346': 'm51',
    '52541,1H10121416232527415052545671737577V3243': 'V44',
    '52542,2H1012142325274150525456737577V3243': 'm51',
    '52542,2H1021232527304150525456737577V3243': 'V47',
    '52543,1H000221232527505254567577V30324143': 'V46',
    '52543,1H0305072226345052545676V1725323364': 'm42',
    '52550,7H1533424650535557V1624323644': 'm51',
    '52551,2H00020421232527505254567577V32344345': 'm42',
    '52551,2H00020423252741505254567577V32344345': 'm51',
    '52551,2H10212325273041505254567577V32344345': 'm51',
    '52561,3H0613152022242634364251535557V1443': 'm51',
    '52570,2H1015233234656773V14222641444652616472': 'm62',
    '52570,5H05232631445557V1416323336455253': 'm51',
    '52571,3H002123252742505254566567V12313445': 'V47',
    '52572,1H01233234405155627074V21424554636572': 'H57',
    '52631,1H000231334547506264667577V143442536061': 'm51',
    '52742,1H17323436404446516571737577V27424354': 'm42',
    '53021,4H000407111315177072V121432435263': 'm63',
    '53032,4H000407111315177072V1214325263': 'V43',
    '53034,1H033436437577V122532444763646667': 'm52',
    '53034,5H24304143505254V13343555': 'm52',
    '53044,5H3250626466V142334436061': 'm63',
    '53051,3H10121533425062646677V143234546061': 'm52',
    '53052,4H121533425062646677V1434546061': 'V32',
    '53056,6H00040711131517V14': 'm43',
    '53132,6H303335465254V173237414550': 'm52',
    '53132,6H333540465254V173237414550': 'm52',
    '53133,4H000407111315177072V12143252': 'V63',
    '53153,1H062433354756657377V26424553626475': 'H52',
    '53154,2H06243335566573V26424553626475': 'H77',
    '53154,6H23445557V143233455253': 'H05',
    '53155,3H0624333573V26424553626475': 'H65',
    '53155,7H23445557V14323352': 'm54',
    '53163,6H0523445557V143233455253': 'V16',
    '53212,0H12141623252732344154V2135435063657476': 'm43',
    '53221,5H2325305254636567V243241445061': 'm52',
    '53222,6H23253052546567V2432414450': 'H63',
    '53223,5H20232540435254V2433414450': 'm52',
    '53224,6H2325405254V2433414450': 'm43',
    '53244,6H32535567V041333425657': 'm54',
    '53244,6H32535567V051333425657': 'm54',
    '53245,2H000204061524273335V13264363': 'm52',
    '53246,3H0002040624273335V264363': 'V13',
    '53251,6H052326445557V14163233455253': 'm63',
    '53252,1H06243335475256657377V26424553626475': 'm43',
    '53254,7H334250535557V243244': 'H15',
    '53255,3H0624333573V26424553626475': 'p',
    '53256,7H23445557V323352': 'V14',
    '53257,3H00020406243335V264363': 'H27',
    '53262,6H0523445557V14163233455253': 'H26',
    '53263,4H11131520222433355153V263244': 'm52',
    '53263,7H15334250535557V243244': 'V16',
    '53266,3H0624333573V264245536264': 'V75',
    '53267,8H243335V2642': 'm63',
    '53283,2H16202224263335647577V1427436365': 'm52',
    '53313,7H0224505254V1113253345': 'm52',
    '53314,3H21233234507173V243041446364': 'm63',
    '53322,5H0123253052546567V2432414450': 'm52',
    '53324,5H102325405254V2433414450': 'm43',
    '53332,3H202225334043465254V273436414550': 'V16',
    '53333,4H0720222426515355576062V3342': 'm54',
    '53334,3H1012232527343642505254V3244': 'm52',
    '53339,8H131553V': 'm52',
    '53340,2H0023254547525470V12132124273235414355': 'm52',
    '53341,3H202225334043465254V16273436414550': 'm52',
    '53342,1H01233234405155627074V21424554636572': 'm63',
    '53343,2H0002042123252750525456V32364347': 'm52',
    '53345,5H2123252744505254V3245': 'm52',
    '53349,7H13153253V': 'H55',
    '53353,1H000221232527505254567577V30324143': 'm52',
    '53353,1H1012212325273041505254567577V3243': 'm52',
    '53353,2H00022123252734364250525467V3145': 'm52',
    '53353,4H2527303641434750525456V3445': 'm52',
    '53353,4H2527303641434750525475V3445': 'm52',
    '53354,1H00020421253353555771V1022343563': 'H51',
    '53355,3H17232541505254V2627324345': 'm52',
    '53355,5H2022243344535557V3542': 'm54',
    '53355,5H2325273443505254V3245': 'm52',
    '53355,7H3342535557V243244': 'H50',
    '53361,3H072225344144475355V16203133424657': 'm54',
    '53361,3H252730324143505254566567V34364547': 'm52',
    '53362,4H2225344144475355V203133424657': 'm54',
    '53362,7H15334250535557V16243244': 'V36',
    '53373,6H232527365052546567V3445': 'm43',
    '53420,4H00232545475254V122124273235414355': 'm52',
    '53422,3H000233455062646677V143442536061': 'm43',
    '53424,2H27323436404244535571V15354662': 'm52',
    '53434,4H07202224265355576062V3342': 'H51',
    '53435,7H10121434505254V45': 'm33',
    '53438,8H13155355V': 'H51',
    '53439,9H1444V': 'm33',
    '53441,1H0002212325273041505254567577V32344346': 'm52',
    '53441,1H10121416232527415052545671737577V3243': 'm52',
    '534410,10HV': 'm43',
    '534410,9H14V': 'm43',
    '53442,2H0002042123252750525456737577V3243': 'm52',
    '53442,2H1012142325274150525456737577V3243': 'm52',
    '53442,2H1021232527304150525456737577V3243': 'm52',
    '53443,1H000221232527505254567577V30324143': 'm52',
    '53444,6H212325273234505254V45': 'm43',
    '53446,6H232527445052V3243': 'm52',
    '53448,7H1315325355V': 'm54',
    '53449,4H1031333543V2053': 'm63',
    '53449,7H2344V3252': 'm63',
    '53449,8H131553V': 'm43',
    '53450,7H1533424650535557V1624323644': 'm52',
    '53453,1H0002042125335153555771V1022343563': 'm54',
    '53454,3H203133354345475254V12324165': 'm54',
    '53459,6H20313343V30': 'H45',
    '53460,1H101523323465677375V14222641445261647277': 'm43',
    '53460,1H1015233234677375V1422264144526164667277': 'm43',
    '53461,7H15334250535557V1624323644': 'H46',
    '53463,4H0021232527505254566567V3445': 'm52',
    '53468,6H2031334345V30': 'm63',
    '53472,4H232527505254566567V3234434547': 'm52',
    '53522,2H00023345475062646677V143442536061': 'm63',
    '53541,2H00020421232527505254567577V32344345': 'm52',
    '53541,2H00020423252741505254567577V32344345': 'm52',
    '53541,2H10212325273041505254567577V32344345': 'm52',
    '53542,3H13152022242634364251535557V1443': 'H06',
    '53543,4H131520222426343651535557V43': 'V14',
    '53544,5H13152022243451535557V43': 'H36',
    '53545,6H1315202251535557V43': 'H34',
    '53546,7H131522515355V43': 'H57',
    '53547,8H1315515355V': 'V43',
    '53551,3H0613152022242634364251535557V1443': 'm52',
    '53553,4H172325305254V16273234414550': 'm52',
    '53560,2H1015233234656773V14222641444652616472': 'm43',
    '53573,4H232527505254566567V32344345': 'V47',
    '54026,6H00030710121416V13': 'm55',
    '54032,5H1215345761636570V1333536667': 'm55',
    '54034,6H244143505254V13343555': 'm53',
    '54035,7H24435052V13343555': 'H54',
    '54044,1H033436437577V122532444763646667': 'm53',
    '54122,6H303335465254V173237414550': 'm53',
    '54122,6H333540465254V173237414550': 'm53',
    '54127,6H00030710121416V': 'V13',
    '54128,7H0003121416V': 'H10',
    '541410,8H0204V': 'H14',
    '54144,6H32535567V041333425657': 'm44',
    '54144,6H32535567V051333425657': 'm44',
    '54149,7H01030513V': 'm44',
    '54223,5H1214162325273234V21354363': 'm64',
    '54227,9H233234V21': 'm55',
    '54320,1H121724333560627274V13212543465563667075': 'm55',
    '54320,1H1217243335607274V1321254346556163667075': 'm55',
    '54322,3H202225334043465254V273436414550': 'm53',
    '54323,4H0720222426515355576062V3342': 'm55',
    '54323,4H12141623252732345456V213543': 'm55',
    '54323,5H20232540435254V2433414450': 'm53',
    '54323,6H202224315355576062V3342': 'm55',
    '54323,7H0224505254V1113253345': 'm53',
    '54324,3H21233234507173V243041446364': 'm53',
    '54324,5H12142325273234V21354363': 'H16',
    '54324,6H2325405254V2433414450': 'm53',
    '54327,8H233234V2144': 'm64',
    '54328,9H233234V': 'V21',
    '54330,2H2022242732424653555761V15163443455270': 'm55',
    '54331,3H2022242732424653555761V1534434552': 'V16',
    '54331,3H202235374446515355576062V31334042': 'm55',
    '543310,10HV': 'm44',
    '54332,1H01233234405155627074V21424554636572': 'm53',
    '54334,4H121423252732345456V213543': 'H16',
    '54334,6H2022245355576062V3342': 'H31',
    '54335,5H122325273234V21354363': 'H14',
    '54335,5H3345506264V1434426061': 'm64',
    '54336,8H506264V346061': 'm44',
    '54337,7H506264V344260': 'V61',
    '54338,8H233234V44': 'm64',
    '54338,8H6264V3460': 'H50',
    '54339,9H2334V': 'H32',
    '54339,9H64V34': 'H62',
    '543410,10HV': 'm53',
    '543410,9H13V': 'H53',
    '543410,9H23V': 'H34',
    '543410,9HV33': 'H63',
    '54342,3H2022242732424653555761V34434552': 'V15',
    '54343,2H00022123252734364250525467V3145': 'm53',
    '54345,4H1223252732345456V213543': 'H14',
    '54345,5H2022243344535557V3542': 'm55',
    '54346,5H202224333553V263244': 'H15',
    '54346,5H2022243335V26324464': 'H15',
    '54346,7H506264V34426061': 'm44',
    '54347,6H2022243335V3244': 'V26',
    '54347,8H506264V3460': 'm44',
    '54348,7H2022243335V': 'V32',
    '54349,8H121454V': 'm55',
    '54349,8H222433V': 'H35',
    '54420,2H1217243335606274V13212541434655636675': 'm55',
    '54424,5H202325405254V2433414450': 'H43',
    '54432,3H202235374446515355576062V313342': 'V40',
    '54433,4H202224515355576062V33354244': 'm44',
    '54433,4H2022313740444651535557V3342': 'm55',
    '54433,4H2022313740444653555772V3342': 'm55',
    '54434,2H27323436404244535571V15354662': 'm53',
    '54434,3H1012232527343642505254V3244': 'm53',
    '54434,6H202224263335535557V42': 'm55',
    '54435,6H20222453555762V3342': 'H60',
    '54435,7H20222433535557V42': 'H35',
    '54436,7H131517335355V42': 'H57',
    '54436,8H2224535557V42': 'H33',
    '54437,8H13155355V42': 'H33',
    '54437,8H506264V3460': 'V61',
    '54438,9H135355V': 'V42',
    '544410,10HV': 'm34',
    '54443,1H00020406111315173251535557606264V': 'H66',
    '54444,2H0002041113151732515355576062V': 'H64',
    '54445,3H000211131517325153555760V': 'H62',
    '54445,7H10121434505254V45': 'm53',
    '54446,4H00111315173251535557V': 'H60',
    '54447,5H1012141635525456V': 'H50',
    '54448,6H111315325355V': 'H51',
    '54448,8H12145254V': 'm55',
    '54449,9H1353V': 'H55',
    '54532,1H0103050710121416355052545661636567V': 'm44',
    '54532,2H1123254345475254V1227313335505155': 'm52',
    '54532,2H1123254345475254V2227313335505155': 'm52',
    '54533,1H0305072226345052545676V1725323364': 'm52',
    '54533,3H11232545475254V27313335505155': 'H43',
    '54537,8H22245355V42': 'H57',
    '54538,8H232554V45': 'H52',
    '60010,2H122022355153555761636570V132433446667': 'm50',
    '60103,3H11131520222433355153V26324044': 'm61',
    '60135,2H000204213353555771V22343563': 'V10',
    '60136,3H00023353555771V22343563': 'H21',
    '60144,2H000204213353555771V1022343563': 'm61',
    '60203,2H11131520222433355361V2632445062': 'm70',
    '60316,1H103443515355577376V23244462': 'm50',
    '60362,1H01233234405155627074V21424554636572': 'm50',
    '60546,2H0031333537434552V32416172': 'm50',
    '60650,0H103133353743455255626466V3240414657606172': 'm50',
    '60721,2H000407111315177072V1214213241435263': 'm50',
    '60772,1H17323436404446516571737577V27424354': 'm50',
    '61000,0H01031113152022243335515362V14263240446473': 'm62',
    '61054,0H253133404244546277V03143541556473': 'm62',
    '61060,2H1015233234656773V14222641444652616472': 'm51',
    '61110,2H122022355153555761636570V132433446667': 'm60',
    '61112,2H121416232527323454V21354363657476': 'm51',
    '61113,3H11131520222433355153V26324044': 'm71',
    '61122,1H12141623252732344154V21354363657476': 'm51',
    '61140,1H101523323465677375V14222641445261647277': 'm51',
    '61140,1H1015233234677375V1422264144526164667277': 'm51',
    '61152,2H062426333551577476V15233743466364': 'm60',
    '61154,2H000204213353555771V1022343563': 'm51',
    '61253,2H11131520222433355361V2632445062': 'm51',
    '61316,3H31334472V15354043606163': 'm71',
    '61462,1H01233234405155627074V21424554636572': 'm60',
    '61477,0H20222426313335374345V416172': 'H67',
    '61536,0H1031333537434552V324041606172': 'm71',
    '61536,2H0031333537434552V32416172': 'm60',
    '61536,2H1031333537434552V32416172': 'm51',
    '61554,0H20222426313335374345526567V416172': 'V54',
    '61563,0H20222426313335374345526567V41546172': 'm51',
    '61565,0H202224263133353743455267V416172': 'H65',
    '61570,3H05232631445557V14163233364045525360': 'm71',
    '61576,0H2022242631333537434567V416172': 'H52',
    '61621,2H000407111315177072V1214213241435263': 'm60',
    '61672,1H17323436404446516571737577V27424354': 'm60',
    '61772,2H173234364446516571737577V27424354': 'm60',
    '62010,0H01031113152022243335515362V14263240446473': 'm63',
    '62025,2H033436437577V12324447646667': 'm52',
    '62035,2H033436435775V12324447646667': 'm52',
    '62052,2H062426333551577476V15233743466364': 'm61',
    '62053,5H1722243335V05142632374363': 'm52',
    '62053,5H2224273335V05142632374363': 'm52',
    '62064,0H253133404244546277V03143541556473': 'V27',
    '62073,0H253133404244546277V0314273541556473': 'm63',
    '62122,2H121416232527323454V21354363657476': 'm61',
    '62170,1H101523323465677375V14222641445261647277': 'm72',
    '62170,1H1015233234677375V1422264144526164667277': 'm72',
    '62173,6H052224333537V1416324363': 'm52',
    '62201,4H000407111315177072V121432435263': 'm52',
    '62210,2H122022355153555761636570V132433446667': 'm61',
    '62232,4H111315202224333553V2632446271': 'm72',
    '62243,4H111315202224333553V26324462': 'V71',
    '62245,5H253133404244V03143541': 'm63',
    '62246,6H253133404244V3541': 'V14',
    '62323,1H000222334145506264V13143442546061': 'm52',
    '62324,2H0002334145506264V143442546061': 'H22',
    '62345,5H152224333537V14264363': 'm52',
    '62346,3H31334472V15354043606163': 'm52',
    '62347,6H2531334244V3541': 'H40',
    '62356,4H31334472V153540436063': 'm72',
    '62362,1H01233234405155627074V21424554636572': 'm61',
    '62447,4H313335374345V416172': 'm72',
    '62449,4H3133353743V4161': 'V72',
    '62457,3H20313335374345V416172': 'm72',
    '62458,4H3133353743V416172': 'H45',
    '62465,3H203133354345475270V305153': 'm72',
    '62466,4H203133354345475270V30': 'V53',
    '62470,2H1015233234656773V14222641444652616472': 'm72',
    '62477,4H2031333543454752V30': 'H70',
    '62514,3H21233234507173V243041446364': 'm72',
    '62570,6H052326445557V1416323336455253': 'm52',
    '62601,2H000407111315177072V1214213241435263': 'm61',
    '62672,2H173234364446516571737577V27424354': 'm61',
    '63020,0H01031113152022243335515362V14263240446473': 'm64',
    '63035,2H033436437577V12324447646667': 'm62',
    '63045,2H033436435775V12324447646667': 'm62',
    '63052,3H0624263335577476V15233743466364': 'm62',
    '63054,5H3250626466V142334436061': 'm64',
    '63074,0H253133404244546277V03143541556473': 'V27',
    '63110,0H010311131520222433355361V1426324450626473': 'm64',
    '63121,4H000407111315177072V121432435263': 'm73',
    '63153,5H1722243335V05142632374363': 'm62',
    '63153,5H2224273335V05142632374363': 'm62',
    '63173,0H253133404244546277V0314273541556473': 'm64',
    '63183,2H16202224263335647577V1427436365': 'm53',
    '63202,2H111315202224333553V26324462647173': 'm64',
    '63220,2H122022355153555761636570V132433446667': 'm62',
    '63223,3H0002334550626466V143442536061': 'H77',
    '63224,4H023345506264V143442536061': 'H66',
    '63235,4H023345506264V1434426061': 'V53',
    '632410,8H5254V': 'm64',
    '63245,4H253133404244V0314354155': 'm73',
    '63257,4H000204243335V264363': 'm53',
    '63266,4H0624333573V2642536264': 'm53',
    '63267,7H243335V264262': 'm73',
    '63273,6H052224333537V1416324363': 'm62',
    '63322,3H000233455062646677V143442536061': 'm53',
    '63324,3H00023345506264V143442546061': 'm62',
    '63325,4H023345506264V1434426061': 'V54',
    '633410,7H5355V62': 'm73',
    '63351,6H052326445557V14163233455253': 'V36',
    '63352,1H01233234405155627074V21424554636572': 'm62',
    '63355,5H152224333537V14264363': 'm62',
    '63360,6H052326445557V1416323336455253': 'm73',
    '63414,3H21233234507173V243041446364': 'm62',
    '63438,3H1031333543V20444553': 'm73',
    '63448,5H23254453V325272': 'm64',
    '63449,3H1031333543V204553': 'V44',
    '63449,6H232544V3252': 'V72',
    '63477,5H20313335434547V30': 'm62',
    '63478,6H2031334345V30': 'H47',
    '63522,1H0002313345475062646677V143442536061': 'H75',
    '63621,1H000231334547506264667577V143442536061': 'm52',
    '64030,0H01031113152022243335515362V14263240446473': 'm74',
    '64034,1H043133447072V152235404360616364': 'm54',
    '64045,2H033436437577V12324447646667': 'm63',
    '64100,0H010311131520222433355361V1426324450626473': 'm74',
    '64102,2H111315202224333553V26324462647173': 'm74',
    '64123,1H01313344505372V121535404360616364': 'm74',
    '64134,5H3557616365V132433446667': 'H70',
    '64138,7H0003121416V': 'm54',
    '641410,9H03V': 'm54',
    '64211,0H1122313436434547536570V0413203246526374': 'm74',
    '64213,0H223436434547536570V0413203246526374': 'V40',
    '64230,2H122022355153555761636570V132433446667': 'm63',
    '64231,3H20223553555761636570V132433446667': 'H12',
    '64232,4H2235535761636570V132433446667': 'H20',
    '64233,5H1214162325273234V21354363': 'V74',
    '64233,5H355761636570V132433446667': 'H22',
    '64235,5H3345506264V1434426061': 'm63',
    '642410,10HV': 'm54',
    '642410,7H5254V64': 'm74',
    '642410,9H53V': 'm65',
    '64242,5H1113152022243335V2632446473': 'm74',
    '64312,0H11223436434547536570V0413203246526374': 'H31',
    '64312,0H223436434547536570V041320324046526374': 'm74',
    '64320,2H1217243335606274V13212541434655636675': 'm54',
    '64325,5H3345506264V1434426061': 'm63',
    '64327,7H233234V214464': 'm74',
    '64330,1H121724333560627274V13212543465563667075': 'm54',
    '64330,1H1217243335607274V1321254346556163667075': 'm54',
    '64334,3H21233234507173V243041446364': 'm54',
    '64338,7H233234V4464': 'm74',
    '643410,10HV': 'm54',
    '643410,8H5254V': 'm65',
    '64448,4H22244354V35556375': 'm74',
    '70103,2H11131520222433355361V2632445062': 'm80',
    '70146,3H00023353555771V22343563': 'm60',
    '70304,3H21233234507173V243041446364': 'm80',
    '70416,1H103443515355577376V23244462': 'm60',
    '70640,0H103133353743455255626466V3240414657606172': 'm60',
    '71040,1H101523323465677375V14222641445261647277': 'm61',
    '71040,1H1015233234677375V1422264144526164667277': 'm61',
    '71044,0H253133404244546277V03143541556473': 'm61',
    '71070,2H1015233234656773V14222641444652616472': 'm61',
    '71100,0H01031113152022243335515362V14263240446473': 'm61',
    '71123,3H11131520222433355153V26324044': 'm72',
    '71200,0H02212436435052727476V11133134354247545567': 'm81',
    '71216,3H31334472V15354043606163': 'm81',
    '71404,3H21233234507173V243041446364': 'm70',
    '71426,1H103443515355577376V23244462': 'm70',
    '71467,0H20222426313335374345V416172': 'm61',
    '71526,2H0031333537434552V32416172': 'm61',
    '71526,2H1031333537434552V32416172': 'm61',
    '71546,0H1031333537434552V324041606172': 'H64',
    '71570,2H0523263144555771V14163233364045525360': 'm72',
    '71645,0H103133353743455264V324041606172': 'm81',
    '72070,1H101523323465677375V14222641445261647277': 'm82',
    '72070,1H1015233234677375V1422264144526164667277': 'm82',
    '72101,4H000407111315177072V121432435263': 'm62',
    '72110,0H01031113152022243335515362V14263240446473': 'm71',
    '72121,0H031113152022243335515362V14263240446473': 'H01',
    '72132,2H121416232527323454V21354363657476': 'm62',
    '72133,3H11131520222433355153V26324044': 'm73',
    '72144,0H253133404244546277V03143541556473': 'm71',
    '72222,4H111315202224333553V2632446271': 'm82',
    '72300,0H02212436435052727476V11133134354247545567': 'm71',
    '72326,1H103443515355577376V23244462': 'm71',
    '72356,3H31334472V15354043606163': 'm62',
    '72370,2H1015233234656773V14222641444652616472': 'm82',
    '72436,3H0031333537434552V416172': 'm82',
    '72437,4H313335374345V416172': 'H52',
    '72447,3H10313335374345V416172': 'm82',
    '72455,3H203133354345475270V305153': 'H72',
    '72457,2H2022313335374345V416172': 'm82',
    '72470,2H0523263144555771V14163233364045525360': 'm73',
    '72504,3H21233234507173V243041446364': 'm71',
    '72560,6H052326445557V1416323336455253': 'm62',
    '72654,3H173234364446737577V27424354': 'H65',
    '72662,2H173234364446516571737577V27424354': 'm62',
    '72663,3H17323436444665737577V27424354': 'H71',
    '72665,3H1732343644467577V27424354': 'H73',
    '72676,3H17323436444677V27424354': 'H75',
    '72687,3H173234364446V27424354': 'H77',
    '73055,2H033436435775V12324447646667': 'm63',
    '73056,3H34364375V12324447646667': 'H57',
    '73062,3H0624263335577476V15233743466364': 'm63',
    '73111,4H000407111315177072V121432435263': 'm72',
    '73120,0H01031113152022243335515362V14263240446473': 'm72',
    '73120,0H010311131520222433355361V1426324450626473': 'm63',
    '73121,0H1122313436434547536570V0413203246526374': 'm72',
    '73131,0H031113152022243335515362V14263240446473': 'H01',
    '73131,0H0311131520222433355361V1426324450626473': 'H01',
    '73133,0H1113152022243335515362V263240446473': 'H03',
    '73133,0H11131520222433355361V26324450626473': 'H03',
    '73133,2H11131520222433355153V2632404464': 'm83',
    '73142,0H031113152022243335515362V263240446473': 'V14',
    '73142,0H0311131520222433355361V26324450626473': 'V14',
    '73142,2H121416232527323454V21354363657476': 'm72',
    '73173,2H16202224263335647577V1427436365': 'm63',
    '73211,0H22343643454751536570V041320324046526374': 'm72',
    '73212,2H111315202224333553V26324462647173': 'm63',
    '73244,0H253133404244546277V03143541556473': 'm72',
    '73244,2H2531334042445477V031435415564': 'm83',
    '73245,3H253133404244V031435415564': 'H77',
    '73246,1H173344505254567174V23244365': 'm83',
    '73253,5H1722243335V05142632374363': 'm63',
    '73253,5H2224273335V05142632374363': 'm63',
    '73257,5H0002243335V264363': 'm63',
    '73266,5H0624333573V26426264': 'm63',
    '73267,6H24333573V264262': 'H06',
    '73310,0H02212436435052727476V11133134354247545567': 'm72',
    '733410,6H535573V62': 'm74',
    '73365,5H152224333537V14264363': 'm63',
    '73373,6H052224333537V1416324363': 'm63',
    '73417,3H103133354143V20444553': 'm74',
    '73428,3H1031333543V20444553': 'H41',
    '73460,6H052326445557V1416323336455253': 'm72',
    '73470,1H052326314455577173V14163233364045525360': 'm74',
    '73587,3H173234364446V27424354': 'm72',
    '74000,0H010311131520222433355361V1426324450626473': 'm84',
    '74024,1H043133447072V152235404360616364': 'm64',
    '74035,2H043133447072V15354043606163': 'm64',
    '74036,3H31334472V15354043606163': 'H70',
    '74112,2H111315202224333553V26324462647173': 'm84',
    '74123,2H013133445072V121535404360616364': 'm64',
    '74124,2H043133447072V1535404360616364': 'm84',
    '74124,3H013133445072V15354043606163': 'V12',
    '74125,2H0431334472V1535404360616364': 'H70',
    '74136,3H31334472V15354043606163': 'm75',
    '74138,8H03121416V': 'm64',
    '74139,9H0314V': 'H12',
    '741410,10HV': 'm64',
    '741410,9H03V': 'H14',
    '74215,2H00021114212325273234V204464': 'H70',
    '74221,0H1122313436434547536570V0413203246526374': 'm84',
    '74223,1H01313344505372V121535404360616364': 'm84',
    '74226,2H31334472V1535404360616364': 'H04',
    '74227,7H233234V214464': 'H12',
    '74230,1H121724333560627274V13212543465563667075': 'm64',
    '74230,1H1217243335607274V1321254346556163667075': 'm64',
    '74231,2H121724333560627274V1325434663667075': 'V21',
    '74231,2H1217243335606274V132125434655636675': 'V41',
    '74231,2H1217243335607274V132543465561637075': 'V21',
    '742310,7H5355V63': 'm84',
    '74232,3H1217243335606274V13254346556375': 'V21',
    '74232,3H1724333560627274V13254346637075': 'H12',
    '74232,3H17243335607274V1325434661637075': 'H12',
    '74232,5H1113152022243335V2632446473': 'm84',
    '74233,2H11212325273234637072V1320446264': 'm84',
    '74233,4H1724333560627274V2543637075': 'V13',
    '74233,4H17243335606274V132543466375': 'H12',
    '74233,4H17243335607274V254361637075': 'V13',
    '74234,3H112123252732346370V13204464': 'H72',
    '74234,5H17243335606274V25436375': 'V13',
    '74235,4H11212325273234V13204464': 'H70',
    '74235,6H1223303234V13214464': 'm84',
    '74236,7H12233234V214464': 'V13',
    '742410,10HV': 'm64',
    '74310,1H022124364350527476V11133134354247545567': 'm73',
    '74328,7H233234V4464': 'm84',
    '74330,2H1217243335606274V13212541434655636675': 'm64',
    '74334,4H17243335607274V2543637075': 'H62',
    '74334,4H212332345073V243041446364': 'm64',
    '74335,5H172433356274V25436375': 'H60',
    '74335,5H2332345073V2430414464': 'H21',
    '74336,6H23323450V24304464': 'V41',
    '74337,5H1023323473V224464': 'V72',
    '74338,6H10233234V4464': 'V22',
    '743410,5H50525474V65': 'V24',
    '74345,4H102332347375V2244647277': 'H67',
    '74346,1H103443515355577376V23244462': 'm75',
    '74346,5H1023323473V22446472': 'H75',
    '74347,2H103443515355577376V2362': 'V44',
    '74348,3H43515355577376V2362': 'H34',
    '74349,4H4353555773V2362': 'H76',
    '74407,3H103133354143V20444553': 'm75',
    '74412,0H223436434547536570V041320324046526374': 'm84',
    '74438,4H22244354V35556375': 'm84',
    '80113,2H11131520222433355361V2632445062': 'm81',
    '80204,3H21233234507173V243041446364': 'm81',
    '80246,3H00023353555771V22343563': 'm70',
    '80540,0H103133353743455255626466V3240414657606172': 'm70',
    '81050,1H101523323465677375V14222641445261647277': 'm71',
    '81050,1H1015233234677375V1422264144526164667277': 'm71',
    '81100,0H02212436435052727476V11133134354247545567': 'm82',
    '81116,3H31334472V15354043606163': 'm82',
    '81123,2H11131520222433355361V2632445062': 'm82',
    '81170,2H1015233234656773V14222641444652616472': 'm71',
    '81214,3H21233234507173V243041446364': 'm82',
    '81256,3H00023353555771V22343563': 'm80',
    '81357,3H00023353555771V223563': 'V34',
    '81426,2H0031333537434552V32416172': 'm71',
    '81426,2H1031333537434552V32416172': 'm71',
    '81427,3H10313335374345V416172': 'H52',
    '81428,1H22243243547173V35556375': 'V41',
    '81457,0H20222426313335374345V416172': 'm71',
    '81527,1H22243243547173V3541556375': 'm80',
    '81550,0H103133353743455255626466V3240414657606172': 'm80',
    '81552,0H1031333537434552626466V32404157606172': 'H55',
    '81561,0H103133353743455255626466V32404157606172': 'V46',
    '81635,0H103133353743455264V324041606172': 'H62',
    '81644,0H10313335374345526264V324041606172': 'H66',
    '81653,0H1031333537434552626466V324041606172': 'V57',
    '82000,0H02212436435052727476V11133134354247545567': 'm83',
    '82004,3H013133445072V15354043606163': 'm83',
    '82015,3H0131334472V15354043606163': 'H50',
    '82016,3H31334472V15354043606163': 'H01',
    '82060,1H101523323465677375V14222641445261647277': 'm81',
    '82060,1H1015233234677375V1422264144526164667277': 'm81',
    '82112,3H0121233234507173V12243041446364': 'm83',
    '82116,3H31334472V15354043606163': 'm83',
    '82123,1H11131520222433355361V263244506264': 'm83',
    '82123,3H21233234507173V12243041446364': 'H01',
    '82126,3H31334472V15354043606163': 'm83',
    '82142,2H121416232527323454V21354363657476': 'm72',
    '82222,3H111315202224333553V263244626471': 'm83',
    '82224,3H21233234507173V243041446364': 'V12',
    '82226,1H103443515355577376V23244462': 'm72',
    '82270,2H1015233234656773V14222641444652616472': 'm81',
    '82357,4H00023353555771V2263': 'm81',
    '82426,3H0031333537434552V416172': 'm81',
    '82437,3H10313335374345V416172': 'm81',
    '82438,1H22243243547173V35556375': 'm81',
    '82457,1H202224313335374345V416172': 'm81',
    '83072,3H0624263335577476V15233743466364': 'm73',
    '83102,3H0121233234507173V12243041446364': 'm84',
    '83104,3H013133445072V15354043606163': 'm84',
    '83116,3H31334472V15354043606163': 'm84',
    '83123,0H11131520222433355361V26324450626473': 'm73',
    '83126,1H103443515355577376V23244462': 'm82',
    '83126,3H31334472V15354043606163': 'm84',
    '83131,0H1122313436434547536570V0413203246526374': 'm73',
    '83133,1H11131520222433355153V263240446473': 'm73',
    '83146,1H173344505254567174V23244365': 'm84',
    '83163,2H16202224263335647577V1427436365': 'm73',
    '83171,1H06343643545775V0312153235444763646667': 'm73',
    '83222,2H111315202224333553V26324462647173': 'm73',
    '83226,3H31334472V15354043606163': 'm84',
    '83244,1H2531334042445477V03143541556473': 'm73',
    '83257,6H02243335V264363': 'm73',
    '83263,5H1722243335V05142632374363': 'm73',
    '83263,5H2224273335V05142632374363': 'm73',
    '83265,5H152224333537V14264363': 'm73',
    '83268,6H22243335V4363': 'H26',
    '83272,5H1214162325273234V2135436374': 'm73',
    '83275,4H1316202224263335V274363': 'm84',
    '83275,4H16202224263335V14274363': 'm84',
    '83276,5H202224263335V274363': 'H16',
    '83277,6H2224263335V4363': 'V27',
    '83311,0H22343643454751536570V041320324046526374': 'm73',
    '83357,5H000233535557V2263': 'm82',
    '83363,6H052224333537V1416324363': 'm73',
    '83438,2H222432435473V35556375': 'm82',
    '84002,3H0121233234507173V12243041446364': 'm85',
    '84024,5H1023253234V132130354464': 'V02',
    '84024,5H2023253234V132130354464': 'V02',
    '840410,10HV': 'm74',
    '84114,3H013133445072V15354043606163': 'm74',
    '84121,1H01313344505372V0412153235404360616364': 'm85',
    '84122,2H111315202224333553V26324462647173': 'm85',
    '84123,5H1023253234V02132130354464': 'm85',
    '84123,5H2023253234V02132130354464': 'm85',
    '84124,1H043133447072V152235404360616364': 'm74',
    '84124,6H022325303234V13354464': 'V11',
    '84125,5H1023253234V2130354464': 'V13',
    '84125,5H2023253234V2130354464': 'V13',
    '84126,3H31334472V15354043606163': 'm74',
    '84133,2H11212325273234637072V1320446264': 'm85',
    '84135,6H2325303234V13354464': 'H02',
    '84136,1H103443515355577376V23244462': 'm83',
    '84136,3H31334472V15354043606163': 'm74',
    '841410,10HV': 'm74',
    '84215,3H001114212325273234V204464': 'm74',
    '84222,1H01313344505372V12153235404360616364': 'V04',
    '84222,5H1113152022243335V2632446473': 'm85',
    '84223,6H022325303234V1113354464': 'm85',
    '84225,4H11212325273234V13204464': 'm74',
    '84226,3H31334472V15354043606163': 'm74',
    '84226,5H23253234V2130354464': 'H20',
    '84227,6H23253234V354464': 'H30',
    '84228,7H233234V4464': 'm85',
    '84231,0H1122313436434547536570V0413203246526374': 'm83',
    '84235,5H122325303234V13214464': 'm85',
    '84236,6H2325303234V354464': 'V13',
    '84237,7H233234V214464': 'm85',
    '84323,1H01313344505372V121535404360616364': 'V32',
    '84327,5H23253234V30354464': 'V21',
    '84328,6H23253234V4464': 'V35',
    '843310,7H5355V63': 'V22',
    '84337,6H0534505254V2564': 'm85',
    '84338,7H345254V2564': 'H50',
    '84349,7H5254V2564': 'H34',
    '84411,0H22343643454751536570V041320324046526374': 'm83',
    '84438,3H2224324354V35556375': 'm83',
    '84512,0H223436434547536570V041320324046526374': 'H51',
}
