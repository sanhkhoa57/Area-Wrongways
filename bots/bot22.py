"""Wrongway agent - version 11 (v10 + an opening book, tested by playing it out).
Copy to submissions/MSSV.py.

How it decides, in short:
1. Rebuild the board from the observation.
2. New in v11: if this exact position is in the opening book (BOOK, about
   138 positions, mostly when I move first), play the prepared move at once.
   Every book move was chosen by playing whole arena games from that position
   and keeping the move that won (or at least drew) against a strong bot,
   for each of its replies I saw. A position that comes up a second time in
   the same game is left to the search (its repetition rules avoid loops).
   Outside the book, v11 searches exactly like v10, except that it thinks
   longer in knife-edge positions (see 7).
3. Look ahead several moves for both players with v4's alpha-beta (negamax)
   search: every pawn move plus the walls that cut the opponent's shortest
   path, and (v9) the opponent's best "setup" walls once it has shown it
   plays them. This engine is unchanged from v9.
4. Score a position (in path steps) as
       (opponent's path - my path) + value of my walls - value of theirs
   (normal start: 0.375 + half the damage one wall could do; v8: count all
   walls together vs an opponent that saves them; odd start: v7's learned
   evaluation).
5. From v10:
   - Exact pawn races once neither player has walls (solve_race_table).
   - Parking: when behind, stand on the only square the other pawn must
     cross and pass ({"type": "pass"}): a draw is worth more than a loss.
   - The stall breaker never pulls the pawn off a square it holds.
6. Never stall: forced wins are scored by length and re-proven each turn,
   loops count as no progress, repeated positions are penalised, progress
   becomes urgent near the turn limit and after 16 stuck turns (v8).
7. Safety: think 0.5 s per move (the limit is 1 s). New in v11: if, from
   depth 5 on, one more ply changed the best move, think up to 0.8 s.
   Keep fighting in lost positions, fall back to a greedy move on any error,
   and "freeze" the big memory tables so Python's garbage collector never
   pauses for long.

Only the standard library is used.
"""

import gc
import random
import time

SIZE = 9
TIME_BUDGET = 0.5        # seconds of thinking per move, all used (arena limit: 1 s)
TIME_UNSTABLE = 0.8      # think up to this long when the best move keeps changing (v11)
UNSTABLE_DEPTH = 5       # ... judged from this search depth on
MAX_DEPTH = 30
WALL_WEIGHT = 1.0        # base value of a wall in hand, in path steps
WALL_VALUE_MIN = 0.5     # wall values are kept between these two (v4's minimum was 1.0)
WALL_VALUE_MAX = 3.5
# Learned evaluation (T5) for games that did NOT start normally (opponent's first
# moves went sideways, or walls already placed). Fitted to ~42,000 positions.
E_W_ME, E_W_OP = 1.483, -1.652
E_DMG_ME, E_DMG_OP = 0.133, -0.098
E_RACE_ME, E_RACE_OP = -0.030, -0.032
E_W2_ME, E_W2_OP = -0.059, 0.070
PROGRESS_BONUS = 0.1     # small reward for really getting closer this turn
REPEAT_PENALTY = 1.0     # discourage going back to a recent square
REPEAT_WINDOW = 6        # how many of my previous squares count as "recent"
REPEAT_POSITION_PENALTY = 2.0  # per earlier time a whole position was created
URGENCY_START = 0.2      # after this fraction of max_turns, progress gets urgent
URGENCY_BONUS = 3.0      # extra reward per step of progress at the turn limit
STALL_START = 16         # turns (both players) with no wall and no new closest approach
STALL_GROWTH = 0.25      # then progress is worth this much more per further stalled turn
STALL_MAX = 8.0
SETUP_WALLS = 3          # opponent setup walls (see setup_walls) added to the search
SETUP_DEPTH = 4          # ... measured by what this many further walls could then add
SETUP_MIN_GAIN = 2       # ... and only if they add at least this many steps
GREEDY_BASE = 0.375      # vs a wall saver: wall value = base + slope * average greedy damage
GREEDY_SLOPE = 0.35
WIN = 100000             # the search found a forced win (minus moves needed)
WIN_BOUND = WIN - 1000   # scores beyond this are forced wins/losses
SURE = 10000             # a race that can no longer be stopped
INF = 10 ** 9
FAR = 255                # "no route" in a distance map
PASS = 99                # move id for standing still ({"type": "pass"} is legal, v10)
CONTEMPT = 1.0           # a draw counts as this many steps worse than an even race (v10)
PARK_MIN_WALLS = 6       # parking checks only once this many walls are on the board
PARK_LOSING = -2.0       # park on the opponent's only route when losing by more than this
EXACT, LOWER, UPPER = 0, 1, 2

# Cells are numbered 0..80: cell = row * 9 + col.
ROW = [i // SIZE for i in range(SIZE * SIZE)]
COL = [i % SIZE for i in range(SIZE * SIZE)]
DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))
BIT = [1 << i for i in range(SIZE * SIZE)]
GOAL_MASK = {g: sum(BIT[g * SIZE + c] for c in range(SIZE)) for g in range(SIZE)}
# Bitboards (bit i = cell i): cells whose step up/down/left/right is open, empty board.
CAN_UP0 = sum(BIT[i] for i in range(81) if i // SIZE > 0)
CAN_DOWN0 = sum(BIT[i] for i in range(81) if i // SIZE < SIZE - 1)
CAN_LEFT0 = sum(BIT[i] for i in range(81) if i % SIZE > 0)
CAN_RIGHT0 = sum(BIT[i] for i in range(81) if i % SIZE < SIZE - 1)
# Moves are numbered too: 0..80 = pawn moves to that cell, 100..227 = walls.
WALL_LIST = [(o, r, c) for o in "HV" for r in range(8) for c in range(8)]
WALL_ID = {w: 100 + i for i, w in enumerate(WALL_LIST)}
CANON_WALL = {w: w for w in WALL_LIST}  # one shared object per wall (cheap for the garbage collector)
# How each wall changes the four bitboards (up, down, left, right).
WALL_DELTA = {}
for _o, _r, _c in WALL_LIST:
    _i = _r * SIZE + _c
    if _o == "H":
        WALL_DELTA[(_o, _r, _c)] = (BIT[_i + 9] | BIT[_i + 10], BIT[_i] | BIT[_i + 1], 0, 0)
    else:
        WALL_DELTA[(_o, _r, _c)] = (0, 0, BIT[_i + 1] | BIT[_i + 10], BIT[_i] | BIT[_i + 9])

# Random numbers used to build a short "fingerprint" (hash) of a position.
_rng = random.Random(2026)
WALL_HASH = {w: _rng.getrandbits(64) for w in WALL_LIST}
POS_HASH = [[_rng.getrandbits(64) for _ in range(81)] for _ in range(2)]
COUNT_HASH = [[_rng.getrandbits(64) for _ in range(11)] for _ in range(2)]
SIDE_HASH = [0, _rng.getrandbits(64)]

# Board state. Index 0 is always ME, index 1 the OPPONENT.
down = [False] * 81      # down[i]: edge between cell i and the cell below is blocked
adj = [[] for _ in range(81)]  # adj[i]: open neighbours of cell i (kept in sync with walls)
right = [False] * 81     # right[i]: edge between cell i and the cell to its right is blocked
h_walls = set()
v_walls = set()
pos = [0, 0]
goal = [0, 0]
walls = [0, 0]
wall_key = 0             # fingerprint of the current wall layout
can_up, can_down, can_left, can_right = CAN_UP0, CAN_DOWN0, CAN_LEFT0, CAN_RIGHT0
wall_value = [1.0, 1.0]  # value of one wall in hand, for me and for the opponent

# Memory kept between turns of the same game.
map_cache = {}           # wall_key -> (distance map to my goal, to opponent's goal)
path_cache = {}          # (walls, square, map) -> walls cutting that shortest path
tt = {}                  # transposition table: position -> (depth, flag, score, best move, turn)
cache_goals = None
history = []             # my recent squares (for the repeat penalty)
odd_game = False         # True if this game did not start from normal development
moved_first = True       # True if I made the first move of this game
root_damage = [0, 0]     # most one wall by each player could add to the other's path
setup_list = [(), ()]    # setup walls each player's search also tries (only the opponent's, v9)
known_walls = set()      # walls on the board after my previous move
known_opponent_walls = [10]  # the opponent's walls in hand at my previous move
opponent_sets_up = False  # has the opponent placed a wall that did not lengthen my path?
race_tables = {}         # solved pawn races (no walls left), per wall layout (v10)
last_best = [0.0]        # my best root score last turn (v10)
# Opening book (v11): position key -> prepared action. Key: my goal row, my square,
# the opponent's square, my walls in hand, theirs, then the walls on the board.
# Moves: "M" + row + col, walls: "W" + orientation + row + col.
BOOK = {
    "0 54 34 10 10 H V": "WH54",
    "0 64 24 10 10 H V": "M54",
    "0 74 14 10 10 H V": "M64",
    "0 84 04 10 10 H V": "M74",
    "0 44 43 9 10 H54 V": "M34",
    "0 54 33 9 10 H54 V": "M44",
    "0 34 43 9 9 H23,54 V": "WH52",
    "0 34 44 8 9 H23,52,54 V": "WH44",
    "0 34 35 7 9 H23,44,52,54 V": "M33",
    "0 34 43 8 8 H23,25,52,54 V": "WH50",
    "0 34 43 8 8 H23,52,54 V32": "WH50",
    "0 33 35 7 8 H23,44,52,54 V22": "WV35",
    "0 34 44 7 8 H23,25,50,52,54 V": "WV55",
    "0 33 32 6 8 H23,25,50,52,54 V55": "M31",
    "0 34 43 6 8 H23,25,50,52,54 V55": "M33",
    "0 34 43 7 7 H23,25,50,52,54 V32": "WV45",
    "0 31 32 6 7 H20,23,25,50,52,54 V55": "M33",
    "0 33 32 6 7 H20,23,25,50,52,54 V55": "WV42",
    "0 33 35 6 7 H23,25,44,52,54 V22,35": "WV43",
    "0 34 42 5 8 H23,25,50,52,54 V35,55": "WV12",
    "0 34 44 6 7 H23,25,50,52,54 V32,45": "WV34",
    "0 33 32 6 6 H20,23,25,31,50,52,54 V55": "M43",
    "0 33 32 6 6 H20,23,25,50,52,54 V32,55": "M43",
    "0 34 43 6 6 H23,25,27,50,52,54 V32,45": "WH65",
    "0 34 43 6 6 H23,25,43,50,52,54 V32,45": "WH36",
    "0 34 54 5 7 H23,25,50,52,54 V32,34,45": "WH65",
    "0 43 22 6 6 H20,23,25,31,50,52,54 V55": "M33",
    "0 43 42 6 6 H20,23,25,50,52,54 V32,55": "M53",
    "0 53 32 6 6 H20,23,25,50,52,54 V32,55": "M43",
    "0 33 32 5 6 H20,23,25,50,52,54 V22,42,55": "M34",
    "0 33 35 5 6 H23,25,44,52,54 V22,35,43,50": "WV41",
    "0 34 42 4 7 H23,25,50,52,54 V12,32,35,55": "M44",
    "0 34 44 5 6 H23,25,27,50,52,54,65 V32,45": "WV34",
    "0 34 53 4 7 H23,25,50,52,54,65 V32,34,45": "WH36",
    "0 53 42 6 5 H20,23,25,41,50,52,54 V32,55": "WH46",
    "0 22 32 4 6 H23,25,44,52,54 V22,35,41,43,50": "M12",
    "0 32 42 4 6 H23,25,44,52,54 V22,35,41,43,50": "M22",
    "0 33 32 5 5 H20,23,25,31,46,50,52,54 V32,55": "WV12",
    "0 33 34 4 6 H23,25,44,52,54 V22,35,41,43,50": "M43",
    "0 34 32 5 5 H20,23,25,50,52,54 V22,36,42,55": "M44",
    "0 34 43 5 5 H23,25,27,36,43,50,52,54 V32,45": "M44",
    "0 34 54 4 6 H23,25,27,50,52,54,65 V32,34,45": "M44",
    "0 42 43 4 6 H23,25,44,52,54 V22,35,41,43,50": "M32",
    "0 42 48 5 5 H23,25,27,36,43,50,52,54 V32,45": "M41",
    "0 43 33 4 6 H23,25,44,52,54 V22,35,41,43,50": "M42",
    "0 44 33 5 5 H23,25,27,36,43,50,52,54 V32,45": "M45",
    "0 44 42 4 6 H23,25,50,52,54 V12,32,35,55,77": "WH31",
    "0 44 55 4 6 H23,25,27,50,52,54,65 V32,34,45": "WH36",
    "0 45 34 5 5 H23,25,27,36,43,50,52,54 V32,45": "M55",
    "0 52 38 5 5 H23,25,27,36,43,50,52,54 V32,45": "M42",
    "0 53 37 5 5 H23,25,27,36,43,50,52,54 V32,45": "M52",
    "0 53 42 5 5 H20,23,25,41,46,50,52,54 V32,55": "WH57",
    "0 54 36 5 5 H23,25,27,36,43,50,52,54 V32,45": "M53",
    "0 55 35 5 5 H23,25,27,36,43,50,52,54 V32,45": "M54",
    "0 12 32 4 5 H02,23,25,44,52,54 V22,35,41,43,50": "WH40",
    "0 34 53 3 6 H23,25,27,36,50,52,54,65 V32,34,45": "WV43",
    "0 44 32 5 4 H20,23,25,50,52,54 V22,36,42,55,56": "M45",
    "0 44 41 3 6 H23,25,31,50,52,54 V12,32,35,55,77": "WV30",
    "0 44 45 3 6 H23,25,27,36,50,52,54,65 V32,34,45": "M43",
    "0 45 22 5 4 H20,23,25,50,52,54 V22,36,42,55,56": "WH11",
    "0 53 42 4 5 H20,23,25,41,46,50,52,54,57 V32,55": "WH66",
    "0 12 31 3 5 H02,23,25,30,44,52,54 V22,35,41,43,50": "WH21",
    "0 12 31 3 5 H02,23,25,40,44,52,54 V22,35,41,43,50": "WH11",
    "0 33 32 4 4 H20,23,25,31,33,46,50,52,54 V12,32,55": "M34",
    "0 33 34 2 6 H23,25,27,36,50,52,54,65 V32,34,43,45": "M43",
    "0 34 43 2 6 H23,25,27,36,50,52,54,65 V32,34,43,45": "M33",
    "0 43 44 2 6 H23,25,27,36,50,52,54,65 V32,34,43,45": "M53",
    "0 43 45 3 5 H23,25,27,36,42,50,52,54,65 V32,34,45": "M44",
    "0 44 35 3 5 H23,25,27,36,42,50,52,54,65 V32,34,45": "M54",
    "0 44 51 2 6 H23,25,31,50,52,54 V12,30,32,35,55,77": "M43",
    "0 45 21 4 4 H11,20,23,25,50,52,54 V22,36,42,55,56": "M46",
    "0 51 48 3 5 H23,25,27,36,42,50,52,54,65 V32,34,45": "M41",
    "0 52 38 3 5 H23,25,27,36,42,50,52,54,65 V32,34,45": "M51",
    "0 52 55 2 6 H23,25,27,36,50,52,54,65 V32,34,43,45": "M42",
    "0 53 32 3 5 H20,23,25,41,46,50,52,54,57,66 V32,55": "M52",
    "0 53 37 3 5 H23,25,27,36,42,50,52,54,65 V32,34,45": "M52",
    "0 53 54 2 6 H23,25,27,36,50,52,54,65 V32,34,43,45": "M52",
    "0 54 36 3 5 H23,25,27,36,42,50,52,54,65 V32,34,45": "M53",
    "0 12 32 3 4 H02,04,23,25,40,44,52,54 V22,35,41,43,50": "WH11",
    "0 34 32 4 3 H20,23,25,27,31,33,46,50,52,54 V12,32,55": "M35",
    "0 41 48 3 4 H21,23,25,27,36,42,50,52,54,65 V32,34,45": "M31",
    "0 43 51 2 5 H23,25,31,43,50,52,54 V12,30,32,35,55,77": "M44",
    "0 44 50 2 5 H23,25,31,43,50,52,54 V12,30,32,35,55,77": "M45",
    "0 45 40 2 5 H23,25,31,43,50,52,54 V12,30,32,35,55,77": "M55",
    "0 12 31 2 4 H00,02,11,23,25,40,44,52,54 V22,35,41,43,50": "WV14",
    "0 12 31 2 4 H00,02,21,23,25,30,44,52,54 V22,35,41,43,50": "WV14",
    "0 12 21 1 4 H00,02,11,23,25,40,44,52,54 V14,22,35,41,43,50": "M13",
    "0 12 30 1 4 H00,02,21,23,25,30,44,52,54 V14,22,35,41,43,50": "M13",
    "0 12 32 2 3 H00,02,04,11,23,25,40,44,52,54 V22,35,41,43,50": "WV16",
    "0 12 32 1 2 H00,02,04,11,14,23,25,40,44,52,54 V16,22,35,41,43,50": "M13",
    "8 04 74 10 10 H V": "M14",
    "8 14 64 10 10 H V": "M24",
    "8 24 54 10 10 H V": "M34",
    "8 34 53 10 10 H V": "M44",
    "8 44 43 10 10 H V": "M54",
    "8 54 43 10 9 H53 V": "WH22",
    "8 54 43 9 8 H22,53,55 V": "WH24",
    "8 54 43 8 7 H22,24,53,55 V42": "WH20",
    "8 54 43 7 6 H20,22,24,53,55,57 V42": "WV33",
    "8 54 43 6 5 H20,22,24,51,53,55,57 V33,42": "WH26",
    "8 54 53 5 5 H20,22,24,26,51,53,55,57 V33,42": "WH17",
    "8 54 43 5 4 H20,22,24,26,51,53,55,57 V33,40,42": "WH17",
    "8 54 53 4 4 H17,20,22,24,26,51,53,55,57 V33,40,42": "M43",
    "8 54 53 4 4 H17,20,22,24,26,51,53,55,57,60 V33,42": "M43",
    "8 54 53 4 4 H17,20,22,24,26,51,53,55,57,70 V33,42": "M43",
    "8 43 53 4 3 H17,20,22,24,26,51,53,55,57 V31,33,40,42": "M33",
    "8 43 53 4 3 H17,20,22,24,26,51,53,55,57,60,62 V33,42": "M33",
    "8 43 53 4 3 H17,20,22,24,26,51,53,55,57,70,72 V33,42": "M33",
    "8 54 43 4 3 H17,20,22,24,26,51,53,55,57,70 V33,40,42": "WH15",
    "8 32 54 4 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,42": "M42",
    "8 32 54 4 2 H17,20,22,24,26,51,53,55,57,70 V31,33,40,42": "M42",
    "8 32 54 4 2 H17,20,22,24,26,51,53,55,57,70,72,74 V33,42": "M42",
    "8 33 53 4 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,42": "M32",
    "8 33 53 4 2 H17,20,22,24,26,51,53,55,57,70 V31,33,40,42": "M32",
    "8 33 53 4 2 H17,20,22,24,26,51,53,55,57,70,72,74 V33,42": "M32",
    "8 42 44 4 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,42": "WV36",
    "8 42 44 4 2 H17,20,22,24,26,51,53,55,57,70,72,74 V33,42": "WV37",
    "8 42 55 4 2 H17,20,22,24,26,51,53,55,57,70 V31,33,40,42": "M52",
    "8 54 53 3 3 H15,17,20,22,24,26,51,53,55,57,70 V33,40,42": "M43",
    "8 31 47 4 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,40,42": "M30",
    "8 33 54 3 2 H15,17,20,22,24,26,51,53,55,57,70 V31,33,40,42": "M32",
    "8 40 56 3 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,36,42": "M50",
    "8 41 46 3 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,36,42": "M40",
    "8 41 46 3 2 H17,20,22,24,26,51,53,55,57,70,72,74 V33,37,42": "M40",
    "8 41 46 4 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,40,42": "M31",
    "8 42 45 3 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,36,42": "M41",
    "8 42 45 3 2 H17,20,22,24,26,51,53,55,57,70,72,74 V33,37,42": "M41",
    "8 42 54 4 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,40,42": "WV37",
    "8 43 53 3 2 H15,17,20,22,24,26,51,53,55,57,70 V31,33,40,42": "M33",
    "8 50 57 3 2 H17,20,22,24,26,51,53,55,57,60,62,64 V33,36,42": "M60",
    "8 51 56 4 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,40,42": "M41",
    "8 52 55 4 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,40,42": "M51",
    "8 40 46 3 1 H17,20,22,24,26,40,51,53,55,57,70,72,74 V33,37,42": "M41",
    "8 41 47 3 1 H17,20,22,24,26,40,51,53,55,57,70,72,74 V33,37,42": "WH46",
    "8 42 55 3 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,37,40,42": "M52",
    "8 52 56 3 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,37,40,42": "WV46",
    "8 52 46 2 1 H17,20,22,24,26,51,53,55,57,70,72 V31,33,37,40,42,46": "WH35",
    "8 52 45 1 1 H17,20,22,24,26,35,51,53,55,57,70,72 V31,33,37,40,42,46": "M51",
}
book_used = set()        # book positions already answered this game (v11)
seen_positions = {}      # positions my moves created -> how many times
on_path = set()          # positions on the line currently being searched
generation = 0           # counts my turns; forced-win scores are only trusted this turn
last_turn = -1
stall = [0, FAR, FAR, -1]  # turn the stall began, best distances (me, opponent), walls on board
last_depth = 0           # how deep the last search got (handy for debugging)
deadline = 0.0


class OutOfTime(Exception):
    pass


# ---------------------------------------------------------------------------
# Board helpers
# ---------------------------------------------------------------------------

def set_wall(wall, blocked):
    """Place (blocked=True) or remove (blocked=False) a wall ("H"/"V", row, col)."""
    global wall_key, can_up, can_down, can_left, can_right
    wall_key ^= WALL_HASH[wall]
    orient, r, c = wall
    i = r * SIZE + c
    if orient == "H":
        can_down ^= BIT[i] | BIT[i + 1]
        can_up ^= BIT[i + 9] | BIT[i + 10]
        down[i] = down[i + 1] = blocked
        edges = ((i, i + 9), (i + 1, i + 10))
        if blocked:
            h_walls.add((r, c))
        else:
            h_walls.discard((r, c))
    else:
        can_right ^= BIT[i] | BIT[i + 9]
        can_left ^= BIT[i + 1] | BIT[i + 10]
        right[i] = right[i + SIZE] = blocked
        edges = ((i, i + 1), (i + 9, i + 10))
        if blocked:
            v_walls.add((r, c))
        else:
            v_walls.discard((r, c))
    for a, b in edges:
        if blocked:
            adj[a].remove(b)
            adj[b].remove(a)
        else:
            adj[a].append(b)
            adj[b].append(a)


def wall_fits(wall):
    """No overlap or crossing with walls already on the board."""
    orient, r, c = wall
    if orient == "H":
        if (r, c) in h_walls or (r, c - 1) in h_walls or (r, c + 1) in h_walls:
            return False
        return (r, c) not in v_walls
    if (r, c) in v_walls or (r - 1, c) in v_walls or (r + 1, c) in v_walls:
        return False
    return (r, c) not in h_walls


def dist_map(goal_row):
    """BFS from the whole goal row: steps from every cell to goal_row.

    Pawns are ignored. Stored as a bytearray (FAR = unreachable): compact,
    and Python's garbage collector never needs to scan it."""
    dist = bytearray(b"\xff" * 81)
    frontier = list(range(goal_row * SIZE, goal_row * SIZE + SIZE))
    for i in frontier:
        dist[i] = 0
    d = 0
    while frontier:
        d += 1
        nxt = []
        for i in frontier:
            for j in adj[i]:
                if dist[j] == FAR:
                    dist[j] = d
                    nxt.append(j)
        frontier = nxt
    return dist


def get_maps():
    """Distance maps for the current walls. Pawn positions don't matter, so the
    same wall layout reached by different move orders reuses the result."""
    maps = map_cache.get(wall_key)
    if maps is None:
        if len(map_cache) > 30000:
            map_cache.clear()
        maps = (dist_map(goal[0]), dist_map(goal[1]))
        map_cache[wall_key] = maps
    return maps


def path_walls(dmap, start):
    """Walls that cut one shortest path from start (following the distance map).
    Cached: the same walls + start square give the same answer."""
    key = (wall_key, start, dmap[0] == 0)      # dmap[0] == 0 only for a map to row 0
    cached = path_cache.get(key)
    if cached is not None:
        return cached
    if len(path_cache) > 50000:
        path_cache.clear()
    cached = path_cache[key] = _path_walls(dmap, start)
    return cached


def _path_walls(dmap, start):
    result = []
    i = start
    guard = 0
    while 0 < dmap[i] < FAR and guard < 81:
        guard += 1
        r = ROW[i]
        c = COL[i]
        d = dmap[i] - 1
        if r > 0 and not down[i - 9] and dmap[i - 9] == d:
            result += [("H", r - 1, c), ("H", r - 1, c - 1)]
            i -= 9
        elif r < 8 and not down[i] and dmap[i + 9] == d:
            result += [("H", r, c), ("H", r, c - 1)]
            i += 9
        elif c > 0 and not right[i - 1] and dmap[i - 1] == d:
            result += [("V", r, c - 1), ("V", r - 1, c - 1)]
            i -= 1
        else:
            result += [("V", r, c), ("V", r - 1, c)]
            i += 1
    out = []
    for w in result:
        if 0 <= w[1] < 8 and 0 <= w[2] < 8 and w not in out:
            out.append(CANON_WALL[w])
    return tuple(out)


def step(i, dr, dc):
    """Neighbouring cell of i in direction (dr, dc), or -1 if off-board/blocked."""
    r = ROW[i] + dr
    c = COL[i] + dc
    if not (0 <= r < SIZE and 0 <= c < SIZE):
        return -1
    if dr == -1:
        blocked = down[i - 9]
    elif dr == 1:
        blocked = down[i]
    elif dc == -1:
        blocked = right[i - 1]
    else:
        blocked = right[i]
    return -1 if blocked else r * SIZE + c


def pawn_moves(me, opp):
    """Pawn destinations with the arena's jump rules."""
    out = []
    for dr, dc in DIRS:
        j = step(me, dr, dc)
        if j < 0:
            continue
        if j != opp:
            out.append(j)
            continue
        k = step(j, dr, dc)                     # straight jump
        if k >= 0:
            out.append(k)
        else:                                   # blocked behind: go diagonal
            for sdr, sdc in ((dc, dr), (-dc, -dr)):
                k = step(j, sdr, sdc)
                if k >= 0 and k not in out:
                    out.append(k)
    return out


def bb_distance(start, goal_row, up, dn, lf, rt):
    """Steps from `start` to `goal_row` (pawns ignored), or FAR. Bitboard BFS:
    each step expands the whole frontier at once with a few integer operations,
    and it stops as soon as the goal row is reached."""
    goal_mask = GOAL_MASK[goal_row]
    reached = frontier = BIT[start]
    if frontier & goal_mask:
        return 0
    d = 0
    while True:
        d += 1
        frontier = (((frontier & up) >> 9) | ((frontier & dn) << 9)
                    | ((frontier & lf) >> 1) | ((frontier & rt) << 1)) & ~reached
        if not frontier:
            return FAR
        if frontier & goal_mask:
            return d
        reached |= frontier


def wall_dists(side, w, me, op, d_me, my_cuts):
    """Like wall_child, but only the two distances are needed: computed with the
    fast bitboard search, and side's own distance only if w cuts side's path."""
    if not wall_fits(w):
        return None
    du, dd, dl, dr = WALL_DELTA[w]              # the wall is only "imagined" here
    up, dn, lf, rt = can_up ^ du, can_down ^ dd, can_left ^ dl, can_right ^ dr
    nd_op = bb_distance(op, goal[1 - side], up, dn, lf, rt)
    nd_me = bb_distance(me, goal[side], up, dn, lf, rt) if w in my_cuts else d_me
    if nd_me == FAR or nd_op == FAR:
        return None
    return nd_me, nd_op


def wall_child(side, w, me, op):
    """(side's distance, other's distance) after `side` places wall w, or None if illegal."""
    if not wall_fits(w):
        return None
    set_wall(w, True)
    maps = get_maps()
    set_wall(w, False)
    nd_me, nd_op = maps[side][me], maps[1 - side][op]
    if nd_me == FAR or nd_op == FAR:
        return None                             # would close someone's route
    return nd_me, nd_op


# ---------------------------------------------------------------------------
# Evaluation and search
# ---------------------------------------------------------------------------

def evaluate(side, d_me, d_op, w_me, w_op):
    """Score for `side`, who is about to move (bigger = better for side)."""
    if d_me <= 1:
        return SURE                             # I step onto my goal now
    if w_op == 0 and d_me <= d_op:
        return SURE - d_me                      # they can't slow me any more
    if w_me == 0 and d_op < d_me:
        return -SURE + d_op                     # I can't slow them any more
    if odd_game:
        # Learned evaluation: walls x (base + damage bonus + race term + count
        # term). The count term is negative, so the last walls are worth most.
        m = d_op - d_me + 0.5
        dmg_me, dmg_op = root_damage[side], root_damage[1 - side]
        return ((d_op - d_me)
                + (E_W_ME + E_DMG_ME * dmg_me + E_RACE_ME * m + E_W2_ME * w_me) * w_me
                + (E_W_OP + E_DMG_OP * dmg_op + E_RACE_OP * m + E_W2_OP * w_op) * w_op)
    return (d_op - d_me) + wall_value[side] * w_me - wall_value[1 - side] * w_op


def ordered_children(side, me, op, moves, map_me, map_op, d_me, d_op, w_me, tt_move, setups=()):
    """Yield (move, side's distance after, other's distance after), best guesses first.

    Good ordering makes alpha-beta cut off more branches: the remembered best
    move, then the best pawn move, then walls by damage, then other pawn moves."""
    my_cuts = None                              # walls that cut my path, built on first need
    if tt_move >= 100:
        dists = None
        if w_me > 0:
            my_cuts = set(path_walls(map_me, me))
            dists = wall_dists(side, WALL_LIST[tt_move - 100], me, op, d_me, my_cuts)
        if dists is None:
            tt_move = -1
        else:
            yield tt_move, dists[0], dists[1]
    elif tt_move >= 0:
        if tt_move in moves:
            yield tt_move, map_me[tt_move], d_op
        else:
            tt_move = -1
    if moves and moves[0] != tt_move:
        yield moves[0], map_me[moves[0]], d_op
    if w_me > 0:
        if my_cuts is None:
            my_cuts = set(path_walls(map_me, me))
        children = []
        cutting = path_walls(map_op, op)
        for w in cutting:
            move = WALL_ID[w]
            if move == tt_move:
                continue
            dists = wall_dists(side, w, me, op, d_me, my_cuts)
            if dists is not None:
                children.append(((dists[1] - d_op) - (dists[0] - d_me), move, dists))
        for w in setups:                       # v9: walls that only set up a trap
            move = WALL_ID[w]
            if move == tt_move or w in cutting:
                continue
            dists = wall_dists(side, w, me, op, d_me, my_cuts)
            if dists is not None:
                children.append(((dists[1] - d_op) - (dists[0] - d_me) - 0.5, move, dists))
        children.sort(key=lambda x: -x[0])
        for _, move, dists in children:
            yield move, dists[0], dists[1]
    for t in moves[1:]:
        if t != tt_move:
            yield t, map_me[t], d_op


def leaf_children(side, me, op, moves, map_me, map_op, d_me, d_op, w_me, tt_move):
    """Children of a node one move before the search horizon. Every child is
    scored instantly, so try all (cheap) pawn moves first, then walls one at a
    time without sorting: a cutoff then skips the remaining path calculations."""
    if 0 <= tt_move < 100 and tt_move in moves:
        yield tt_move, map_me[tt_move], d_op
    for t in moves:
        if t != tt_move:
            yield t, map_me[t], d_op
    if w_me > 0:
        my_cuts = set(path_walls(map_me, me))
        if tt_move >= 100:
            dists = wall_dists(side, WALL_LIST[tt_move - 100], me, op, d_me, my_cuts)
            if dists is not None:
                yield tt_move, dists[0], dists[1]
        for w in path_walls(map_op, op):
            move = WALL_ID[w]
            if move != tt_move:
                dists = wall_dists(side, w, me, op, d_me, my_cuts)
                if dists is not None:
                    yield move, dists[0], dists[1]


def score_to_tt(score, ply):
    """Store forced wins as 'moves from this position', not from the root."""
    if score > WIN_BOUND:
        return score + ply
    if score < -WIN_BOUND:
        return score - ply
    return score


def score_from_tt(score, ply):
    if score > WIN_BOUND:
        return score - ply
    if score < -WIN_BOUND:
        return score + ply
    return score


def search(side, depth, alpha, beta, ply):
    """Negamax alpha-beta: best score `side` (to move) can force within `depth` moves.

    `ply` = moves played since the root; a win in fewer moves scores higher."""
    if time.perf_counter() > deadline:
        raise OutOfTime
    other = 1 - side
    me, op = pos[side], pos[other]
    moves = pawn_moves(me, op)
    for t in moves:
        if ROW[t] == goal[side]:
            return WIN - ply                    # winning move; sooner = higher
    maps = get_maps()
    map_me, map_op = maps[side], maps[other]
    d_me, d_op = map_me[me], map_op[op]
    w_me, w_op = walls[side], walls[other]
    if depth == 0:
        return evaluate(side, d_me, d_op, w_me, w_op)

    # Seen this exact position before (from another move order or turn)?
    key = (wall_key ^ POS_HASH[0][pos[0]] ^ POS_HASH[1][pos[1]]
           ^ COUNT_HASH[0][walls[0]] ^ COUNT_HASH[1][walls[1]] ^ SIDE_HASH[side])
    if key in on_path:
        # Back to a position already on this line: a loop proves nothing,
        # so judge it like a leaf instead of trusting any stored "win".
        return evaluate(side, d_me, d_op, w_me, w_op)
    tt_move = -1
    entry = tt.get(key)
    if entry is not None:
        e_depth, e_flag, e_value, tt_move, e_gen = entry
        e_value = score_from_tt(e_value, ply)
        if (e_value > WIN_BOUND or e_value < -WIN_BOUND) and e_gen != generation:
            e_depth = -1                        # old forced result: re-prove it this turn
        if e_depth >= depth:
            if e_flag == EXACT:
                return e_value
            if e_flag == LOWER and e_value >= beta:
                return e_value
            if e_flag == UPPER and e_value <= alpha:
                return e_value

    moves.sort(key=map_me.__getitem__)
    alpha0 = alpha
    best, best_move = -INF, -1
    on_path.add(key)
    if depth == 1:
        children = leaf_children(side, me, op, moves, map_me, map_op, d_me, d_op, w_me, tt_move)
    else:
        # The opponent's setup walls are tried as its direct reply to my move.
        setups = setup_list[side] if ply == 1 else ()
        children = ordered_children(side, me, op, moves, map_me, map_op, d_me, d_op, w_me,
                                    tt_move, setups)
    for move, nd_me, nd_op in children:
        if move < 100:                          # pawn move
            if depth == 1:
                score = -evaluate(other, nd_op, nd_me, w_op, w_me)
            else:
                pos[side] = move
                score = -search(other, depth - 1, -beta, -alpha, ply + 1)
                pos[side] = me
        else:                                   # wall
            if depth == 1:
                score = -evaluate(other, nd_op, nd_me, w_op, w_me - 1)
            else:
                w = WALL_LIST[move - 100]
                set_wall(w, True)
                walls[side] -= 1
                score = -search(other, depth - 1, -beta, -alpha, ply + 1)
                walls[side] += 1
                set_wall(w, False)
        if score > best:
            best, best_move = score, move
            if best > alpha:
                alpha = best
                if alpha >= beta:
                    break                       # the opponent will avoid this line

    on_path.discard(key)
    if best <= alpha0:
        flag = UPPER
    elif best >= beta:
        flag = LOWER
    else:
        flag = EXACT
    if len(tt) > 150000:
        tt.clear()
    tt[key] = (depth, flag, score_to_tt(best, ply), best_move, generation)
    return best


def route_cells(dmap, start):
    """Cells on one shortest route from start (following the distance map)."""
    cells = [start]
    i = start
    while 0 < dmap[i] < FAR and len(cells) < 81:
        d = dmap[i] - 1
        for j in adj[i]:
            if dmap[j] == d:
                i = j
                break
        else:
            break
        cells.append(i)
    return cells


def setup_walls(attacker):
    """The attacker's best "setup" walls: walls near the victim's route that may
    add nothing now, but after which the attacker's remaining walls (placed one
    after another, see greedy_damage) could add much more. The search only
    generates walls that cut the victim's current shortest path, so without
    this list it never sees a barrier being prepared (v9)."""
    victim = 1 - attacker
    k = min(SETUP_DEPTH, walls[attacker] - 1)
    if k <= 0:
        return ()
    maps = get_maps()
    a_pos, v_pos = pos[attacker], pos[victim]
    d_a, d_v = maps[attacker][a_pos], maps[victim][v_pos]
    threat_now = greedy_damage(victim, k)
    near = set()
    for cell in route_cells(maps[victim], v_pos):
        r, c = ROW[cell], COL[cell]
        for rr in (r - 2, r - 1, r, r + 1):
            for cc in (c - 2, c - 1, c, c + 1):
                if 0 <= rr < 8 and 0 <= cc < 8:
                    near.add(CANON_WALL[("H", rr, cc)])
                    near.add(CANON_WALL[("V", rr, cc)])
    scored = []
    for w in near:
        if not wall_fits(w):
            continue
        set_wall(w, True)
        m = get_maps()
        if m[attacker][a_pos] <= d_a and m[victim][v_pos] < FAR:
            gain = (m[victim][v_pos] - d_v) + greedy_damage(victim, k) - threat_now
            if gain >= SETUP_MIN_GAIN:
                scored.append((gain, w))
        set_wall(w, False)
    scored.sort(key=lambda x: (-x[0], WALL_ID[x[1]]))   # ties: same order every run
    return tuple(w for _, w in scored[:SETUP_WALLS])


def greedy_damage(victim, count):
    """Total damage `count` walls could do to victim, placed one after another,
    each the one that hurts most at that moment (pawns stay put). A wall that adds
    nothing yet is still placed if nothing better exists: walls that only work
    together, like a barrier across the board, then count fully."""
    attacker = 1 - victim
    placed = []
    total = 0
    for _ in range(count):
        maps = get_maps()
        d = maps[victim][pos[victim]]
        attacker_cuts = set(path_walls(maps[attacker], pos[attacker]))
        best, best_w = -1, None
        for w in path_walls(maps[victim], pos[victim]):
            dists = wall_dists(attacker, w, pos[attacker], pos[victim],
                               maps[attacker][pos[attacker]], attacker_cuts)
            if dists is not None and dists[1] - d > best:
                best, best_w = dists[1] - d, w
        if best_w is None:
            break
        set_wall(best_w, True)
        placed.append(best_w)
        total += best
    for w in reversed(placed):
        set_wall(w, False)
    return total


def best_wall_damage(victim):
    """Most steps a single wall could add to `victim`'s shortest path right now."""
    attacker = 1 - victim
    maps = get_maps()
    d = maps[victim][pos[victim]]
    attacker_cuts = set(path_walls(maps[attacker], pos[attacker]))
    best = 0
    for w in path_walls(maps[victim], pos[victim]):
        dists = wall_dists(attacker, w, pos[attacker], pos[victim],
                           maps[attacker][pos[attacker]], attacker_cuts)
        if dists is not None:
            best = max(best, dists[1] - d)
    return best


# ---------------------------------------------------------------------------
# Pawns that stand still: parking and pure races (v10)
# ---------------------------------------------------------------------------

def pawn_path_length(me, blocker, goal_row):
    """Steps for the pawn at `me` to reach goal_row while the other pawn stands
    still at `blocker` (jumping over it as the rules allow), or FAR if the
    standing pawn closes every route. The distance maps ignore pawns, so they
    cannot see a pawn parked in a one-square-wide passage."""
    if ROW[me] == goal_row:
        return 0
    seen = {me}
    frontier = [me]
    d = 0
    while frontier:
        d += 1
        nxt = []
        for c in frontier:
            for t in pawn_moves(c, blocker):
                if t not in seen:
                    if ROW[t] == goal_row:
                        return d
                    seen.add(t)
                    nxt.append(t)
        frontier = nxt
    return FAR


def cell_steps(start):
    """Steps (pawns ignored) from square `start` to every square."""
    dist = bytearray(b"\xff" * 81)
    dist[start] = 0
    frontier = [start]
    d = 0
    while frontier:
        d += 1
        nxt = []
        for i in frontier:
            for j in adj[i]:
                if dist[j] == FAR:
                    dist[j] = d
                    nxt.append(j)
        frontier = nxt
    return dist


def can_park(blocker, blocker_moves_next):
    """Can player `blocker` reach a square that the other pawn must cross, before
    it gets there? If every route is closed while the blocker stands on that
    square, it can simply pass there for the rest of the game: a draw (a draw
    is worth 1 point, a loss 0). bombaclat saves lost games this way."""
    victim = 1 - blocker
    v, b, gv = pos[victim], pos[blocker], goal[victim]
    dmap = get_maps()[victim]
    if dmap[v] >= FAR:
        return False
    steps = None
    for t_v, f in enumerate(route_cells(dmap, v)):
        if t_v == 0:
            continue
        if f == b:
            t_b = 0
        else:
            if steps is None:
                steps = cell_steps(b)
            t_b = steps[f]
        if t_b > t_v or (t_b == t_v and not blocker_moves_next):
            continue                            # the other pawn gets there first
        if pawn_path_length(v, f, gv) >= FAR:
            return True
    return False


def solve_race_table():
    """Exact results once neither player has walls left. The board can no longer
    change, so there are only 81 x 81 pawn squares x 2 players to move: solve
    them all backwards from the positions that win at once (retrograde analysis,
    as in chess endgame tables). Entry (p0 * 81 + p1) * 2 + side: plies until
    the player to move wins (> 0) or loses (< 0); 0 = draw (passing is legal, so
    two pawns can hold each other off forever)."""
    size = 81 * 81 * 2
    value = [0] * size
    count = [0] * size
    preds = [[] for _ in range(size)]
    queue = []
    g0, g1 = goal[0], goal[1]
    for p0 in range(81):
        if ROW[p0] == g0:
            continue
        for p1 in range(81):
            if p1 == p0 or ROW[p1] == g1:
                continue
            base = (p0 * 81 + p1) * 2
            for side in (0, 1):
                i = base + side
                if side == 0:
                    moves = pawn_moves(p0, p1)
                    targets = [(t * 81 + p1) * 2 + 1 for t in moves]
                    won = any(ROW[t] == g0 for t in moves)
                else:
                    moves = pawn_moves(p1, p0)
                    targets = [(p0 * 81 + t) * 2 for t in moves]
                    won = any(ROW[t] == g1 for t in moves)
                if won:
                    value[i] = 1                # steps onto the goal row now
                    queue.append(i)
                    continue
                count[i] = len(targets) + 1     # every move, plus passing
                for j in targets:
                    preds[j].append(i)
                preds[i ^ 1].append(i)          # passing: same squares, other player
    head = 0
    while head < len(queue):                    # shortest results first
        j = queue[head]
        head += 1
        v = value[j]
        for i in preds[j]:
            if value[i]:
                continue
            if v < 0:                           # a move into a lost position: win
                value[i] = 1 - v
                queue.append(i)
            else:
                count[i] -= 1
                if count[i] == 0:               # every option loses
                    value[i] = -(v + 1)
                    queue.append(i)
    return value


def race_table_move(observation):
    """Best move by the exact table (both players out of walls): the quickest
    win that ends before the turn limit, else a draw, else the longest loss."""
    key = (wall_key, goal[0], goal[1])
    table = race_tables.get(key)
    if table is None:
        if len(race_tables) > 8:
            race_tables.clear()
        table = race_tables[key] = solve_race_table()
    left = (observation.get("max_turns") or 500) - observation["turn"]
    p0, p1 = pos[0], pos[1]
    best, best_key = None, None
    for t in [r * SIZE + c for r, c in observation["legal_moves"]] + [PASS]:
        square = p0 if t == PASS else t
        v = table[(square * 81 + p1) * 2 + 1]   # the opponent is to move then
        if v < 0 and 1 - v <= left:
            rank = (2, v)                       # I win in 1 - v plies: sooner is better
        elif v > 0 and v + 1 <= left:
            rank = (0, v)                       # I lose in v + 1 plies: later is better
        else:
            repeats = seen_positions.get((square, p1, wall_key, 0, 0), 0)
            rank = (1, -repeats, -pawn_path_length(square, p1, goal[0]))
        if best_key is None or rank > best_key:
            best, best_key = t, rank
    return best


# ---------------------------------------------------------------------------
# Turn handling
# ---------------------------------------------------------------------------

def greedy_action(observation):
    """Safe fallback: step to the legal square closest to my goal."""
    moves = observation["legal_moves"]
    dist = observation["distances"]
    if not moves:
        return {"type": "pass"}
    best = min(moves, key=lambda m: dist[m[0]][m[1]] if dist[m[0]][m[1]] >= 0 else 999)
    return {"type": "move", "to": list(best)}


def book_key(observation):
    """Opening-book key: my goal row, both pawns, walls in hand, walls on the board."""
    you, other = observation["you"], observation["opponent"]
    walls_on = observation["walls"]
    return "%d %d%d %d%d %d %d H%s V%s" % (
        you["goal_row"], you["position"][0], you["position"][1],
        other["position"][0], other["position"][1],
        you["walls_remaining"], other["walls_remaining"],
        ",".join("%d%d" % (r, c) for r, c in sorted(map(tuple, walls_on["horizontal"]))),
        ",".join("%d%d" % (r, c) for r, c in sorted(map(tuple, walls_on["vertical"]))))


def book_move(observation, legal_moves, legal_walls):
    """The prepared move for this position (v11), or None if it has none, it is not legal,
    or the position came up before in this game (then the search, with its repetition
    penalties, decides: replaying a prepared move could go round in circles)."""
    key = book_key(observation)
    entry = BOOK.get(key)
    if entry is None or key in book_used:
        return None
    book_used.add(key)
    if entry == "PASS":
        return PASS
    if entry[0] == "M":
        square = int(entry[1]) * SIZE + int(entry[2])
        return square if square in legal_moves else None
    wall = (entry[1], int(entry[2]), int(entry[3]))
    return WALL_ID[wall] if wall in legal_walls else None


def to_action(move):
    if move == PASS:
        return {"type": "pass"}
    if move < 100:
        return {"type": "move", "to": [ROW[move], COL[move]]}
    o, r, c = WALL_LIST[move - 100]
    return {"type": "wall", "orientation": o, "row": r, "col": c}


def load_board(observation):
    global wall_key, cache_goals, can_up, can_down, can_left, can_right
    can_up, can_down, can_left, can_right = CAN_UP0, CAN_DOWN0, CAN_LEFT0, CAN_RIGHT0
    for i in range(81):
        down[i] = right[i] = False
        adj[i] = [i + dr * SIZE + dc for dr, dc in DIRS
                  if 0 <= ROW[i] + dr < SIZE and 0 <= COL[i] + dc < SIZE]
    h_walls.clear()
    v_walls.clear()
    wall_key = 0
    for r, c in observation["walls"]["horizontal"]:
        set_wall(("H", r, c), True)
    for r, c in observation["walls"]["vertical"]:
        set_wall(("V", r, c), True)
    you, other = observation["you"], observation["opponent"]
    pos[0] = you["position"][0] * SIZE + you["position"][1]
    pos[1] = other["position"][0] * SIZE + other["position"][1]
    goal[0], goal[1] = you["goal_row"], other["goal_row"]
    walls[0], walls[1] = you["walls_remaining"], other["walls_remaining"]
    if cache_goals != (goal[0], goal[1]):       # new game / other side: forget
        map_cache.clear()
        tt.clear()
        cache_goals = (goal[0], goal[1])


def think(observation):
    global deadline, last_depth, last_turn, generation, odd_game, moved_first, opponent_sets_up
    start = time.perf_counter()
    deadline = start + TIME_BUDGET
    generation += 1
    on_path.clear()
    load_board(observation)
    if observation["turn"] <= last_turn:        # a new game started
        del history[:]
        seen_positions.clear()
        stall[:] = [observation["turn"], FAR, FAR, -1]
        race_tables.clear()
        last_best[0] = 0.0
        book_used.clear()
    last_turn = observation["turn"]
    moved_first = observation["turn"] % 2 == 0  # I am the player who started this game
    board_walls = set(CANON_WALL[("H", r, c)] for r, c in observation["walls"]["horizontal"])
    board_walls.update(CANON_WALL[("V", r, c)] for r, c in observation["walls"]["vertical"])
    if not history:
        # First move of this game: did it start normally (no walls, both pawns
        # still in the centre column)? Decided once, for the whole game.
        w = observation["walls"]
        odd_game = bool(w["horizontal"] or w["vertical"]
                        or observation["you"]["position"][1] != 4
                        or observation["opponent"]["position"][1] != 4)
        opponent_sets_up = False
    else:
        # Did the opponent just place a wall that did not lengthen my path? Then
        # it plays "setup" walls (preparing barriers), so my search must expect
        # them (v9). Opponents that never do this are searched exactly like v8.
        placed = known_opponent_walls[0] - observation["opponent"]["walls_remaining"]
        for w in (board_walls - known_walls) if placed > 0 else ():
            set_wall(w, False)
            before = get_maps()[0][pos[0]]
            set_wall(w, True)
            if get_maps()[0][pos[0]] <= before:
                opponent_sets_up = True
    known_walls.clear()
    known_walls.update(board_walls)
    known_opponent_walls[0] = observation["opponent"]["walls_remaining"]
    recent = set(history[-REPEAT_WINDOW:])
    history.append(pos[0])

    legal_moves = [r * SIZE + c for r, c in observation["legal_moves"]]
    for t in legal_moves:
        if ROW[t] == goal[0]:
            return to_action(t)                 # win right now
    legal_walls = {(w["orientation"], w["row"], w["col"]) for w in observation["legal_walls"]}

    move = book_move(observation, legal_moves, legal_walls) if BOOK else None
    if move is not None:                        # v11: a prepared opening move
        if move < 100:
            square = pos[0] if move == PASS else move
            key = (square, pos[1], wall_key, walls[0], walls[1])
            seen_positions[key] = seen_positions.get(key, 0) + 1
        else:
            known_walls.add(WALL_LIST[move - 100])
        return to_action(move)

    if walls[0] == 0 and walls[1] == 0:
        # v10: no walls left on either side, so the race is solved exactly.
        move = race_table_move(observation)
        if move is not None:
            square = pos[0] if move == PASS else move
            key = (square, pos[1], wall_key, 0, 0)
            seen_positions[key] = seen_positions.get(key, 0) + 1
            return to_action(move)

    maps = get_maps()
    d_me, d_op = maps[0][pos[0]], maps[1][pos[1]]
    my_pos = pos[0]
    # Stall detection: nobody placed a wall and nobody got closer than before.
    on_board = len(h_walls) + len(v_walls)
    if on_board != stall[3]:
        stall[:] = [observation["turn"], d_me, d_op, on_board]
    elif d_me < stall[1] or d_op < stall[2]:
        stall[:] = [observation["turn"], min(d_me, stall[1]), min(d_op, stall[2]), on_board]
    stalled = observation["turn"] - stall[0]
    # v10: parking, only when I was behind last turn (a draw then beats a loss)
    # and enough walls make one-square passages possible.
    park_check = on_board >= PARK_MIN_WALLS and last_best[0] < 0
    holding = park_check and pawn_path_length(pos[1], my_pos, goal[1]) >= FAR
    # A wall in hand is worth about what it could do to the other player now.
    for side in (0, 1):
        damage = best_wall_damage(1 - side)
        root_damage[side] = damage
        value = WALL_WEIGHT * (0.375 + 0.5 * damage)   # v4 used 0.5 + 0.5 * damage, min 1.0
        # v8: if I moved first and the opponent saved more walls than me, count
        # what all walls in hand could do together (fully at 3+ walls more).
        saved = min(1.0, max(0.0, (walls[1] - walls[0]) / 3.0))
        if moved_first and saved > 0 and walls[side] > 0:
            greedy = greedy_damage(1 - side, walls[side]) / float(walls[side])
            value += saved * (WALL_WEIGHT * (GREEDY_BASE + GREEDY_SLOPE * greedy) - value)
        wall_value[side] = min(WALL_VALUE_MAX, max(WALL_VALUE_MIN, value))

    # Root candidates: [move, bonus, ordering score, race margin after the move].
    root = []
    # The longer the game runs, the more real progress matters (a draw is bad).
    fraction = observation["turn"] / float(observation.get("max_turns") or 500)
    urgency = max(0.0, (fraction - URGENCY_START) / (1.0 - URGENCY_START))
    progress_bonus = PROGRESS_BONUS + URGENCY_BONUS * urgency
    stall_mode = stalled > STALL_START and not holding   # (never leave a square I hold, v10)
    if stall_mode:                              # break the deadlock: go forward
        progress_bonus += min(STALL_MAX, STALL_GROWTH * (stalled - STALL_START))
        # v10: count progress around a pawn that stands in the way (a detour)
        here = pawn_path_length(my_pos, pos[1], goal[0])
    for t in legal_moves:
        if stall_mode and here < FAR:
            after = pawn_path_length(t, pos[1], goal[0])
            gain = here - after if after < FAR else d_me - maps[0][t]
        else:
            gain = d_me - maps[0][t]
        bonus = 0.01 + progress_bonus * gain
        if t in recent and maps[0][t] >= d_me:
            bonus -= REPEAT_PENALTY
        repeats = seen_positions.get((t, pos[1], wall_key, walls[0], walls[1]), 0)
        bonus -= REPEAT_POSITION_PENALTY * repeats
        root.append([t, bonus, -maps[0][t], d_op - maps[0][t] + 0.01])
    if walls[0] > 0:
        for w in path_walls(maps[1], pos[1]):
            if w in legal_walls:
                dists = wall_child(0, w, pos[0], pos[1])
                if dists is not None:
                    root.append([WALL_ID[w], 0.0, (dists[1] - d_op) - (dists[0] - d_me) - 1,
                                 dists[1] - dists[0]])
    # The opponent's best setup walls this turn, tried as its reply to my move
    # (v9) - only once it has shown it plays them, and only after a normal
    # start (in odd games this tested worse than v8).
    setup_list[1] = setup_walls(1) if opponent_sets_up and not odd_game else ()
    if holding:
        # v10: I stand on the only square the opponent can pass: standing still
        # (a legal pass) keeps it locked out.
        root.append([PASS, 0.0, -d_me - 0.5, d_op - d_me])
    if not root:
        return greedy_action(observation)
    # v10 parking: when I am losing, can I reach (or already hold) a square the
    # opponent must cross, and hold it to a draw by standing still?
    check_me = park_check
    for entry in root:
        entry.append(False)
        if not check_me:
            continue
        move = entry[0]
        if move < 100:
            pos[0] = my_pos if move == PASS else move
            entry[4] = can_park(0, False)
            pos[0] = my_pos
        else:
            set_wall(WALL_LIST[move - 100], True)
            entry[4] = can_park(0, False)
            set_wall(WALL_LIST[move - 100], False)
    draw_score = -CONTEMPT
    root.sort(key=lambda x: -x[2])

    # Iterative deepening: search 1 move ahead, then 2, 3, ... until time runs out.
    best_move = root[0][0]
    proven_loss = False
    root_key = (wall_key ^ POS_HASH[0][pos[0]] ^ POS_HASH[1][pos[1]]
                ^ COUNT_HASH[0][walls[0]] ^ COUNT_HASH[1][walls[1]] ^ SIDE_HASH[0])
    previous_best = None
    for depth in range(1, MAX_DEPTH + 1):
        depth_best = None
        on_path.clear()
        on_path.add(root_key)
        try:
            best_total = -INF
            for entry in root:
                move, bonus = entry[0], entry[1]
                alpha = best_total - bonus
                if move == PASS:
                    value = -search(1, depth - 1, -INF, -alpha, 1)
                elif move < 100:
                    pos[0] = move
                    value = -search(1, depth - 1, -INF, -alpha, 1)
                    pos[0] = my_pos
                else:
                    w = WALL_LIST[move - 100]
                    set_wall(w, True)
                    walls[0] -= 1
                    value = -search(1, depth - 1, -INF, -alpha, 1)
                    walls[0] += 1
                    set_wall(w, False)
                total = value + bonus
                if entry[4] and value < PARK_LOSING:
                    # I can park and hold a lost game to a draw (v10)
                    total = draw_score + 0.001 * max(-1000.0, value)
                entry[2] = total
                if total > best_total:
                    best_total, depth_best = total, move
        except OutOfTime:
            if depth_best is not None:          # the previous best was re-checked first
                best_move = depth_best
            break
        best_move = depth_best
        last_depth = depth
        if depth >= UNSTABLE_DEPTH and best_move != previous_best:
            # v11: one more ply changed my mind, so this is a knife-edge position:
            # give the search more time (still far below the 1 s limit)
            deadline = max(deadline, start + TIME_UNSTABLE)
        previous_best = best_move
        root.sort(key=lambda x: -x[2])          # best first for the next depth
        if best_total >= WIN_BOUND and WIN - best_total <= depth + 1:
            break                               # a win short enough to be fully seen
        if best_total <= -WIN_BOUND:
            proven_loss = True
            break
    if proven_loss:
        # Every line loses against perfect play. Opponents are not perfect, so
        # keep fighting: take the action that leaves the best race margin
        # (usually the wall that delays the opponent the most).
        best_move = max(root, key=lambda entry: entry[3])[0]
    for entry in root:
        if entry[0] == best_move:
            last_best[0] = entry[2]
    if best_move < 100:
        square = my_pos if best_move == PASS else best_move
        key = (square, pos[1], wall_key, walls[0], walls[1])
        seen_positions[key] = seen_positions.get(key, 0) + 1
    else:
        known_walls.add(WALL_LIST[best_move - 100])
    return to_action(best_move)


def choose_action(observation):
    try:
        action = think(observation)
    except Exception:
        action = greedy_action(observation)
    gc.freeze()
    return action
