"""Wrongway Arena - bot v4: động cơ Negamax của Final/Combo3 + các mô-đun học từ steve.py.

File độc lập, chỉ dùng thư viện chuẩn (random, time, collections); cần Python 3.9 trở lên.
Hàm nộp: choose_action(observation). Thời gian mỗi lượt: MOVE_TIME_LIMIT = 0.70 giây (đo thực tế nước chậm nhất ~0.74 < 0.8).

Thay đổi so với Final
---------------------
1. Bỏ STUCK_GUARD (ép tiến khi loanh quanh): nó chặn các nước đi ngang/lùi lấy nhịp nên bot tự đâm vào
   thế xấu. Bộ lọc "hết tường thì chỉ xét bước tiến" ở gốc cũng bỏ. Chống lặp thế giờ chỉ còn
   REPEAT_PENALTY (phạt mềm) cộng giới hạn số lần PASS.
2. Thế đối đầu trực diện (standoff), sửa cho người đi trước: hai quân cùng cột/hàng cách một ô trống
   thường rơi vào lượt của Player 0 (do chẵn lẻ), và bước vào ô giữa là bị nhảy qua mất 1 nhịp. Bản cũ
   dùng tường làm nước chờ (tốn tường, đo thử kém hơn). Bản này dùng PASS làm nước chờ miễn phí
   (tối đa STANDOFF_WAITS lần mỗi thế) để ép địch bước vào ô giữa trước; sau đó tìm kiếm quyết định.
3. Tàn cuộc tuyệt đối: khi đối thủ còn 0 tường, giải ngược toàn bộ 2 x 81 x 81 trạng thái hai quân
   (_PawnEndgame, ý tưởng từ steve.py, có tính nhảy quân và PASS) rồi đi nước thắng nhanh nhất /
   thua chậm nhất / hòa tốt nhất. Nếu mình còn tường mà bảng chưa chứng minh thắng chắc thì quay về
   tìm kiếm (để còn dùng tường).
4. Hàm đánh giá: MULTI_PATH_BONUS (nhiều hướng đi tối ưu), BOTTLENECK_PENALTY (đường về bị bóp thành
   1 ô) và dò pháo đài _reach_mask (quân kia đứng yên mãi chặn kín đường -> coi như hòa, CONTEMPT).
5. Tìm kiếm: thêm Killer moves (2 mỗi độ sâu), Countermove, History heuristic cho tường; Safe
   Null-Move Pruning (chỉ khi hai quân xa nhau, chưa quân nào sát đích, điểm tĩnh vượt beta + biên
   an toàn); nước PASS trong cây khi hai quân gần nhau.
6. An toàn: mọi nước ra đều kiểm tra lại với legal_moves / legal_walls; lỗi bất kỳ -> nước dự phòng.

Kết quả đo cục bộ (arena tự viết theo luật Quoridor chuẩn, 0.1 giây/nước, khai cuộc ngẫu nhiên, đổi bên):
  v4 - steve: 11-9 (hai bên đều dùng PASS); v4 không PASS - steve: 8-12; Final - steve: 1-7;
  v4 - Final: 10-14 (mẫu nhỏ, chưa phân biệt được với 50%). Vì vậy PASS được giữ bật mặc định.

Lưu ý: PASS lấy theo steve.py (hành động {"type": "pass"} luôn hợp lệ). Nếu Arena của bạn từ chối
PASS, đặt ALLOW_PASS = False ở phần cấu hình.
"""

import random
import time
from collections import deque

# =============================================================================
# 1. CẤU HÌNH CHUNG
# =============================================================================

# Số giây tối đa cho mỗi lượt (yêu cầu: dưới 0.8 giây, tính cả dựng bàn cờ và tra bảng tàn cuộc).
MOVE_TIME_LIMIT = 0.70

# =============================================================================
# 2. ĐỘNG CƠ TÌM KIẾM (giữ nguyên thuật toán của 25127128_11)
# =============================================================================

# ------------------------------------------------------------------ hằng số --
CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)              # 0: lên, 1: phải, 2: xuống, 3: trái
WALL0 = 81                         # nước đi >= WALL0 là đặt tường (81..208)
RED, BLUE = 0, 1                   # người chơi 0 (đích hàng 0) và 1 (đích hàng 8)
START = (76, 4)                    # [8, 4] và [0, 4]
GOAL = (sum(1 << c for c in range(0, 9)),        # bitboard hàng 0
        sum(1 << c for c in range(72, 81)))      # bitboard hàng 8
GOAL_ROW = (0, 8)
WIN = 100000

TIME_BUDGET = MOVE_TIME_LIMIT         # dùng chung hằng số ở đầu file
ROOT_WALLS = 18                    # số tường ứng viên tối đa ở gốc
INNER_WALLS = 12                   # số tường ứng viên tối đa ở các nút trong cây (đo thử: 12 tốt hơn 8)
REPEAT_PENALTY = 25                # phạt cho mỗi lần lặp lại thế cờ ở gốc
W_DIST = 10                        # trọng số khoảng cách
W_WALLS = 22                       # trọng số tường (đường cong lõm ^0.8)
WALL_POW = 0.8
TEMPO = 2
TT_SIZE = 250000
# --- Nước 'đứng yên' (PASS): theo steve.py, PASS là hành động hợp lệ của Arena ({"type": "pass"}).
# Nếu Arena của bạn KHÔNG cho phép PASS thì đặt ALLOW_PASS = False: bot sẽ không bao giờ PASS
# (bảng tàn cuộc, thế đối đầu và Null-Move Pruning đều tự tắt phần dựa vào PASS).
ALLOW_PASS = True
PASS = -2                          # mã nước PASS trong tìm kiếm (không trùng ô 0..80 hay tường 81..208)
PASS_NEAR = 3                      # chỉ thử PASS khi hai quân cách nhau (Manhattan) <= ngần này
PASS_PENALTY = 6                   # phạt nhẹ khi PASS ở gốc (10 điểm = 1 bước đi)
MAX_PASSES = 14                    # tối đa số lần PASS mỗi ván (chống kéo hòa vô hạn)
STANDOFF_WAITS = 2                 # thế đối đầu: số lần "chờ" bằng PASS trước khi để tìm kiếm quyết định

# --- Tàn cuộc (bảng giải ngược 2 quân cờ)
ENDGAME_ON = True
TABLEBASE_BUDGET = 0.30            # giây tối đa để giải bảng (chỉ giải lại khi tường thay đổi)

# --- Hàm đánh giá mở rộng
MULTI_PATH_BONUS = 2               # thưởng khi quân có >= 2 hướng đi tối ưu (nếu địch còn tường)
BOTTLENECK_PENALTY = 1             # phạt khi 2 bước tới đích bị bóp thành đúng 1 ô
BLOCK_MIN_WALLS = 8                # chỉ dò pháo đài khi trên bàn có >= ngần này tường
BLOCK_MIN_S = 15                   # ...và điểm tĩnh lệch hơn ngần này
CONTEMPT = 25                      # hòa bị coi là hơi xấu cho gốc

# --- Cắt tỉa
NMP_ON = True
NMP_R = 2                          # độ giảm chiều sâu của nước rỗng
NMP_MARGIN = 18                    # biên an toàn trên beta khi địch còn tường

# Bảng lũy thừa 0.8 tính sẵn cho số tường 0..10
_WPOW = tuple(i ** WALL_POW for i in range(16))

# Số ngẫu nhiên cố định cho băm Zobrist (độc lập chủ sở hữu tường).
_rng = random.Random(20240607)
ZP = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
ZW = [_rng.getrandbits(64) for _ in range(128)]
ZLEFT = [_rng.getrandbits(64) for _ in range(128)]
ZT = _rng.getrandbits(64)


def wall_info(w):
    """Mã tường 0..127 -> (hướng 'h'/'v', row, col)."""
    return ('h' if w < 64 else 'v', (w & 63) >> 3, w & 7)


def wall_id(orient, r, c):
    """(hướng 'h'/'v', row, col) -> mã tường 0..127."""
    return (0 if orient == 'h' else 64) + r * 8 + c


# Tiền xử lý hình học cho 128 vị trí tường: cạnh bị chặn, điểm lưới, tường xung đột.
WALL_EDGES, WALL_PTS, CONFLICT = [], [], []
for _w in range(128):
    _o, _r, _c = wall_info(_w)
    if _o == 'h':
        _e = [(_r * 9 + _c, 2), (_r * 9 + _c + 1, 2),
              ((_r + 1) * 9 + _c, 0), ((_r + 1) * 9 + _c + 1, 0)]
        _p = [(_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2]
        _cf = [wall_id('h', _r, _c), wall_id('v', _r, _c)]
        if _c > 0:
            _cf.append(wall_id('h', _r, _c - 1))
        if _c < 7:
            _cf.append(wall_id('h', _r, _c + 1))
    else:
        _e = [(_r * 9 + _c, 1), ((_r + 1) * 9 + _c, 1),
              (_r * 9 + _c + 1, 3), ((_r + 1) * 9 + _c + 1, 3)]
        _p = [_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1]
        _cf = [wall_id('v', _r, _c), wall_id('h', _r, _c)]
        if _r > 0:
            _cf.append(wall_id('v', _r - 1, _c))
        if _r < 7:
            _cf.append(wall_id('v', _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_PTS.append(tuple(_p))
    CONFLICT.append(tuple(_cf))

# BLOCKERS[ô][hướng] = các tường (0..127) chặn bước đi từ `ô` theo `hướng`.
BLOCKERS = [[() for _ in range(4)] for _ in range(CELLS)]
for _cell in range(CELLS):
    for _d in range(4):
        _lst = []
        for _w in range(128):
            if (_cell, _d) in WALL_EDGES[_w]:
                _lst.append(_w)
        BLOCKERS[_cell][_d] = tuple(_lst)


def _initial_can():
    """can[d] = bitboard các ô có thể bước theo hướng d (khi chưa có tường)."""
    can = [0, 0, 0, 0]
    for cell in range(CELLS):
        r, c = divmod(cell, 9)
        if r > 0:
            can[0] |= 1 << cell
        if c < 8:
            can[1] |= 1 << cell
        if r < 8:
            can[2] |= 1 << cell
        if c > 0:
            can[3] |= 1 << cell
    return can


# ------------------------------------------------ trường khoảng cách (BF) --
class BF:
    """BFS ngược từ hàng đích bằng bitboard, mở rộng lười (lazy) theo từng lớp.

    Với can[0]=lên, can[1]=phải, can[2]=xuống, can[3]=trái:
    Một ô `u` bước được vào tập `f` ở khoảng cách trước nếu:
    * đi xuống (d=2) vào f  -> (f >> 9) & can[2]
    * đi lên   (d=0) vào f  -> (f << 9) & can[0]
    * đi phải  (d=1) vào f  -> (f >> 1) & can[1]
    * đi trái  (d=3) vào f  -> (f << 1) & can[3]
    """
    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can

    def dist(self, cell):
        """Số bước ngắn nhất từ `cell` tới đích; trả về INF nếu bị bịt kín."""
        bit = 1 << cell
        k = 0
        for layer in self.layers:            # các lớp đã tính sẵn
            if layer & bit:
                return k
            k += 1
        # Chưa đủ lớp: mở rộng thêm từng lớp cho tới khi gặp `cell`.
        layers = self.layers
        seen = self.seen
        f = layers[-1]
        c0, c1, c2, c3 = self.c0, self.c1, self.c2, self.c3
        while True:
            f = (((f << 9) & c0) | ((f >> 1) & c1) |
                 ((f >> 9) & c2) | ((f << 1) & c3)) & ~seen
            if not f:
                return INF
            seen |= f
            self.seen = seen
            layers.append(f)
            if f & bit:
                return k
            k += 1


_FCACHE = {}            # băm tường -> cặp trường khoảng cách (BF) dùng chung giữa các nút/độ sâu


# ------------------------------------------------------------------ bàn cờ --
class Board:
    """Trạng thái ván cờ, hỗ trợ make/unmake nhanh và kiểm tra điểm chạm lưới."""

    def __init__(self):
        self.pos = [START[0], START[1]]
        self.left = [10, 10]
        self.can = _initial_can()
        self.forbid = [0] * 128              # >0: tường bị trùng/cắt/chồng lấn
        self.pts = [0] * 100                 # số tường/biên chạm vào mỗi điểm lưới 10x10
        for R in range(10):
            for C in range(10):
                if R in (0, 9) or C in (0, 9):
                    self.pts[R * 10 + C] = 1
        self.turn = RED
        self.whash = 0                       # Zobrist hash của riêng các tường trên bàn
        self.nwalls = 0                      # số tường đang có trên bàn

    def copy(self):
        b = Board.__new__(Board)
        b.pos = self.pos[:]
        b.left = self.left[:]
        b.can = self.can[:]
        b.forbid = self.forbid[:]
        b.pts = self.pts[:]
        b.turn = self.turn
        b.whash = self.whash
        b.nwalls = self.nwalls
        return b

    def fields(self):
        """Tạo cặp trường khoảng cách lười (BF cho RED, BF cho BLUE)."""
        f = _FCACHE.get(self.whash)
        if f is None:
            if len(_FCACHE) > 20000:
                _FCACHE.clear()
            c = self.can
            f = (BF(GOAL[0], c), BF(GOAL[1], c))
            _FCACHE[self.whash] = f
        return f

    def dist(self, start, goal):
        """BFS bitboard trực tiếp (dùng khi kiểm tra nhanh 1 lần)."""
        cur = 1 << start
        if cur & goal:
            return 0
        seen = cur
        c0, c1, c2, c3 = self.can
        d = 0
        while cur:
            d += 1
            nxt = (((cur & c0) >> 9) | ((cur & c1) << 1) |
                   ((cur & c2) << 9) | ((cur & c3) >> 1)) & ~seen
            if nxt & goal:
                return d
            seen |= nxt
            cur = nxt
        return INF

    def pawn_moves(self, player=None):
        """Các ô hợp lệ mà quân của `player` có thể bước/nhảy tới."""
        t = self.turn if player is None else player
        me, op = self.pos[t], self.pos[1 - t]
        can = self.can
        out = []
        for d in range(4):
            if not (can[d] >> me) & 1:
                continue
            tgt = me + DIRS[d]
            if tgt != op:
                out.append(tgt)
            elif (can[d] >> tgt) & 1:                    # nhảy thẳng qua đầu đối thủ
                out.append(tgt + DIRS[d])
            else:                                        # nhảy chéo khi sau lưng địch có tường
                for s in ((d + 1) & 3, (d + 3) & 3):
                    if (can[s] >> tgt) & 1:
                        out.append(tgt + DIRS[s])
        return out

    def _edges(self, w, on):
        can = self.can
        if on:
            for cell, d in WALL_EDGES[w]:
                can[d] |= 1 << cell
        else:
            for cell, d in WALL_EDGES[w]:
                can[d] &= ~(1 << cell)

    def put_wall(self, w):
        """Đặt tường `w` lên bàn cờ (chưa trừ số tường còn lại)."""
        self._edges(w, False)
        for x in CONFLICT[w]:
            self.forbid[x] += 1
        for p in WALL_PTS[w]:
            self.pts[p] += 1
        self.whash ^= ZW[w]
        self.nwalls += 1

    def remove_wall(self, w):
        """Gỡ tường `w` khỏi bàn cờ."""
        self._edges(w, True)
        for x in CONFLICT[w]:
            self.forbid[x] -= 1
        for p in WALL_PTS[w]:
            self.pts[p] -= 1
        self.whash ^= ZW[w]
        self.nwalls -= 1

    def wall_ok(self, w, player=None):
        """Kiểm tra đầy đủ tính hợp lệ của tường `w`."""
        t = self.turn if player is None else player
        if self.left[t] <= 0 or self.forbid[w]:
            return False
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        if (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0) >= 2:
            self._edges(w, False)
            ok = (self.dist(self.pos[0], GOAL[0]) < INF and
                  self.dist(self.pos[1], GOAL[1]) < INF)
            self._edges(w, True)
            return ok
        return True

    def zkey(self):
        """Khóa băm Zobrist đầy đủ của trạng thái hiện tại."""
        return (self.whash ^ ZP[0][self.pos[0]] ^ ZP[1][self.pos[1]] ^
                ZLEFT[self.left[0] * 11 + self.left[1]] ^
                (ZT if self.turn else 0))


# ------------------------------------------------------ tàn cuộc 2 quân cờ --
class _EGTimeout(Exception):
    """Hết thời gian dành cho việc giải bảng tàn cuộc."""


def _moves_with(can, me, op):
    """Các ô quân ở `me` đi/nhảy tới được khi quân kia đứng ở `op` (theo bitboard `can`)."""
    out = []
    for d in range(4):
        if not (can[d] >> me) & 1:
            continue
        tgt = me + DIRS[d]
        if tgt != op:
            out.append(tgt)
        elif (can[d] >> tgt) & 1:
            out.append(tgt + DIRS[d])
        else:
            for s in ((d + 1) & 3, (d + 3) & 3):
                if (can[s] >> tgt) & 1:
                    out.append(tgt + DIRS[s])
    return out


class _PawnEndgame:
    """Bảng tàn cuộc: giải ngược (retrograde) toàn bộ 2 x 81 x 81 trạng thái hai quân khi không ai
    còn đặt thêm tường. Kết quả mỗi trạng thái tính theo người TỚI LƯỢT: +1 thắng, -1 thua, 0 hòa
    (lặp vô hạn), kèm số nửa nước (ply) tới khi kết thúc. Nếu ALLOW_PASS thì 'đứng yên' là nước hợp lệ.

    Ý tưởng lấy từ _PawnEndgame của steve.py, viết lại theo bitboard `can` của file này."""
    __slots__ = ("key", "graph", "contact", "maps", "result", "distance")

    def __init__(self, key, can, deadline):
        self.key = key
        graph = []
        for i in range(CELLS):
            graph.append(tuple(i + DIRS[d] for d in range(4) if (can[d] >> i) & 1))
        contact = {}
        for i in range(CELLS):
            for j in graph[i]:
                contact[i * CELLS + j] = tuple(_moves_with(can, i, j))
        self.graph, self.contact = graph, contact
        maps = []
        for g in (0, 1):
            bf = BF(GOAL[g], can)
            maps.append([bf.dist(i) for i in range(CELLS)])
        self.maps = maps
        r0, r1 = maps
        span = CELLS * CELLS
        result = [0] * (2 * span)
        distance = [0] * (2 * span)
        remaining = [0] * (2 * span)
        longest = [0] * (2 * span)
        previous = [[] for _ in range(2 * span)]
        pending = deque()
        push = pending.append
        cget = contact.get
        extra = 1 if ALLOW_PASS else 0
        for p0 in range(CELLS):
            if (p0 & 7) == 0 and time.perf_counter() > deadline:
                raise _EGTimeout
            if r0[p0] >= INF:
                continue
            won0 = p0 // 9 == GOAL_ROW[0]
            base0 = p0 * CELLS
            for p1 in range(CELLS):
                if p1 == p0 or r1[p1] >= INF:
                    continue
                pair = base0 + p1
                if won0 or p1 // 9 == GOAL_ROW[1]:
                    w0 = 1 if won0 else -1
                    result[pair] = w0
                    result[span + pair] = -w0
                    push(pair)
                    push(span + pair)
                    continue
                # RED (p0) tới lượt
                mv = cget(pair)
                if mv is None:
                    mv = graph[p0]
                remaining[pair] = len(mv) + extra
                for d in mv:
                    previous[span + d * CELLS + p1].append(pair)
                if extra:
                    previous[span + pair].append(pair)
                # BLUE (p1) tới lượt
                st = span + pair
                mv = cget(p1 * CELLS + p0)
                if mv is None:
                    mv = graph[p1]
                remaining[st] = len(mv) + extra
                for d in mv:
                    previous[base0 + d].append(st)
                if extra:
                    previous[pair].append(st)
        popleft = pending.popleft
        processed = 0
        while pending:
            state = popleft()
            processed += 1
            if (processed & 1023) == 0 and time.perf_counter() > deadline:
                raise _EGTimeout
            rs = result[state]
            ds = distance[state]
            for parent in previous[state]:
                if result[parent]:
                    continue
                if rs == -1:                       # con thua -> cha thắng
                    result[parent] = 1
                    distance[parent] = ds + 1
                    push(parent)
                else:
                    remaining[parent] -= 1
                    if ds > longest[parent]:
                        longest[parent] = ds
                    if remaining[parent] == 0:     # mọi con đều thắng -> cha thua
                        result[parent] = -1
                        distance[parent] = longest[parent] + 1
                        push(parent)
        self.result, self.distance = result, distance

    def moves(self, own, enemy):
        mv = self.contact.get(own * CELLS + enemy)
        return self.graph[own] if mv is None else mv

    def probe(self, p0, p1, turn):
        s = turn * CELLS * CELLS + p0 * CELLS + p1
        return self.result[s], self.distance[s]

    def action(self, p0, p1, turn, turns_left, legal, visits):
        """Chọn ô đích tốt nhất (hoặc đứng yên nếu ALLOW_PASS): thắng nhanh nhất, thua chậm nhất,
        hòa thì ưu tiên tiến gần đích, tạo áp lực, tránh lặp thế."""
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = None, None
        opts = list(self.moves(own, enemy))
        if ALLOW_PASS:
            opts.append(own)
        for dest in opts:
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
                replies = list(self.moves(enemy, dest))
                if ALLOW_PASS:
                    replies.append(enemy)
                chances = 0
                for reply in replies:
                    ap0, ap1 = (dest, reply) if turn == 0 else (reply, dest)
                    rr, rd = self.probe(ap0, ap1, turn)
                    chances += (rr == 1 and rd <= turns_left - 2)
                pressure = chances / max(1, len(replies))
            key = (outcome, pace, progress, pressure, -visits.get((dest, enemy), 0), dest != own)
            if best_key is None or key > best_key:
                best, best_key = dest, key
        return best


_EG = None             # bảng tàn cuộc đang dùng (giữ lại khi tường không đổi)
_EG_VISITS = {}        # (ô_mình, ô_địch) -> số lần đã đứng ở thế này (tránh lặp khi hòa)
_EG_LAST_TURN = -1


def _endgame_action(observation, board, legal_cells, started, max_turns):
    """Đối thủ hết tường: tra bảng tàn cuộc. Trả None nếu bảng không đủ cơ sở để quyết định
    (ví dụ mình còn tường mà chưa có đường thắng chắc) -> khi đó dùng tìm kiếm thường."""
    global _EG, _EG_VISITS, _EG_LAST_TURN
    pid = observation["player_id"]
    turn = observation["turn"]
    goal_row = GOAL_ROW[pid]
    for r, c in observation["legal_moves"]:
        if r == goal_row:
            return {"type": "move", "to": [int(r), int(c)]}
    turns_left = max_turns - turn
    if turns_left <= 0:
        return None
    key = tuple(board.can)
    try:
        if _EG is None or _EG.key != key:
            _EG = None
            _EG = _PawnEndgame(key, board.can, started + TABLEBASE_BUDGET)
    except _EGTimeout:
        return None
    p0, p1 = board.pos[0], board.pos[1]
    outcome, dist = _EG.probe(p0, p1, pid)
    if board.left[pid] != 0 and not (outcome == 1 and dist <= turns_left):
        return None
    if turn <= _EG_LAST_TURN:
        _EG_VISITS = {}
    _EG_LAST_TURN = turn
    own, enemy = board.pos[pid], board.pos[1 - pid]
    _EG_VISITS[(own, enemy)] = min(32, _EG_VISITS.get((own, enemy), 0) + 1)
    if len(_EG_VISITS) > 256:
        _EG_VISITS = {(own, enemy): _EG_VISITS[(own, enemy)]}
    dest = _EG.action(p0, p1, pid, turns_left, legal_cells, _EG_VISITS)
    if dest is None:
        return None
    if dest == own:
        return {"type": "pass"}
    return {"type": "move", "to": [int(dest // 9), int(dest % 9)]}


# ---------------------------------------------------------------- tìm kiếm --
class _Timeout(Exception):
    """Ngắt tìm kiếm khi hết thời gian cho phép."""


class Bot:
    """Bộ máy tìm kiếm Negamax + Alpha-Beta + Lazy BF + Transposition Table,
    thêm Killer/Countermove/History, Safe Null-Move Pruning và nước 'đứng yên' (PASS)."""

    def __init__(self, max_depth=30, time_limit=TIME_BUDGET):
        self.max_depth = max_depth
        self.time_limit = time_limit
        self.tt = {}
        self.nodes = 0
        self.depth_reached = 0
        self.deadline = 0.0
        self.root = 0                         # người chơi đang là "gốc" (để định dấu điểm hòa)
        self.killers = [[None, None] for _ in range(64)]
        self.cm = {}                          # nước vừa đi của địch -> nước phản công gây cắt tỉa
        self.hist = [0] * 128                 # history heuristic cho tường
        self.fcache = {}                      # bộ nhớ đệm dò pháo đài

    def _wall_cands(self, b, f_op, op_pos, limit, check=None):
        """Sinh danh sách mã tường (0..127) cắt đường ngắn nhất của đối thủ,
        sắp xếp từ gần đối thủ nhất trở đi (tận dụng các lớp đã tính của `f_op`)."""
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF:
            return []
        layers = f_op.layers
        can = b.can
        forbid = b.forbid
        out = []
        seen = set()

        # Đi dọc theo đường ngắn nhất chính của đối thủ (ưu tiên nhánh phụ ở bước đầu)
        u = op_pos
        cur_d = d
        while cur_d > 0:
            prev = layers[cur_d - 1]
            nxt = -1
            for dir_idx in range(4):
                if (can[dir_idx] >> u) & 1:
                    v = u + DIRS[dir_idx]
                    if (prev >> v) & 1:
                        for w in BLOCKERS[u][dir_idx]:
                            if not forbid[w] and w not in seen:
                                seen.add(w)
                                # lọc hợp lệ TRƯỚC khi tính vào limit (check = người chơi cần kiểm tra)
                                if check is None or b.wall_ok(w, check):
                                    out.append(w)
                        if nxt < 0:
                            nxt = v
            if nxt < 0 or len(out) >= limit:
                break
            u = nxt
            cur_d -= 1
        return out[:limit]

    # ------------------------------------------------ nhận thức không gian --
    def _flex(self, b, f, cell):
        """Điểm không gian của quân ở `cell`: + nếu có >= 2 hướng đi tối ưu (MULTI_PATH_BONUS),
        - nếu 2 bước tới bị bóp thành đúng 1 ô (BOTTLENECK_PENALTY)."""
        d = f.dist(cell)
        if d <= 0 or d >= INF:
            return 0
        c0, c1, c2, c3 = b.can
        layers = f.layers
        bit = 1 << cell
        s1 = (((bit & c0) >> 9) | ((bit & c1) << 1) | ((bit & c2) << 9) | ((bit & c3) >> 1)) & layers[d - 1]
        if s1 & (s1 - 1):
            return MULTI_PATH_BONUS
        if d >= 2 and s1:
            s2 = (((s1 & c0) >> 9) | ((s1 & c1) << 1) | ((s1 & c2) << 9) | ((s1 & c3) >> 1)) & layers[d - 2]
            if s2 and not (s2 & (s2 - 1)):
                return -BOTTLENECK_PENALTY
        return 0

    def _reach(self, b, oc, goal):
        """Các ô còn về được đích nếu quân ở `oc` đứng yên mãi (có tính nhảy qua quân đó)."""
        c0, c1, c2, c3 = b.can
        can = b.can
        obit = 1 << oc
        jumps = []
        for d in range(4):
            m = oc - DIRS[d]
            if m < 0 or m >= CELLS or not (can[d] >> m) & 1:
                continue
            if (can[d] >> oc) & 1:
                dest = 1 << (oc + DIRS[d])
            else:
                dest = 0
                for s in ((d + 1) & 3, (d + 3) & 3):
                    if (can[s] >> oc) & 1:
                        dest |= 1 << (oc + DIRS[s])
            if dest:
                jumps.append((1 << m, dest))
        seen = goal | obit
        front = goal & ~obit
        while front:
            nxt = (((front << 9) & c0) | ((front >> 1) & c1) |
                   ((front >> 9) & c2) | ((front << 1) & c3)) & ~seen
            for m, dest in jumps:
                if (front & dest) and not (seen & m):
                    nxt |= m
            seen |= nxt
            front = nxt
        return seen & ~obit

    def _fortress(self, b, x):
        """Quân `x` bị 'pháo đài' chặn kín: đường về đích đóng hẳn nếu quân kia đứng yên mãi."""
        key = (b.pos[1 - x], b.whash, x)
        mask = self.fcache.get(key)
        if mask is None:
            if len(self.fcache) > 20000:
                self.fcache.clear()
            mask = self._reach(b, b.pos[1 - x], GOAL[x])
            self.fcache[key] = mask
        return not (mask >> b.pos[x]) & 1

    def _draw(self, t):
        """Điểm hòa theo góc nhìn người tới lượt `t` (hòa hơi bất lợi cho gốc: CONTEMPT)."""
        return -CONTEMPT if t == self.root else CONTEMPT

    def _eval(self, b, F):
        """Hàm đánh giá từ góc nhìn của người tới lượt `b.turn`."""
        t = b.turn
        o = 1 - t
        d_me = F[t].dist(b.pos[t])
        d_op = F[o].dist(b.pos[o])
        # Đối thủ đã hết tường và mình kịp về đích trước/bằng -> thắng chắc cuộc đua
        if b.left[o] == 0 and d_me <= d_op:
            return WIN // 2 - d_me
        # Mình đã hết tường và đối thủ gần đích hơn hẳn -> thua chắc cuộc đua
        if b.left[t] == 0 and d_op < d_me:
            return -(WIN // 2 - d_op)
        wv = _WPOW[b.left[t]] - _WPOW[b.left[o]]
        s = W_DIST * (d_op - d_me) + W_WALLS * wv + TEMPO
        # Không gian: chỉ có ý nghĩa khi bên kia còn tường để chặn mình
        if b.left[o] > 0:
            s += self._flex(b, F[t], b.pos[t])
        if b.left[t] > 0:
            s -= self._flex(b, F[o], b.pos[o])
        # Pháo đài: bên đang dẫn trước nhưng bị quân kia chặn kín đường -> coi như hòa
        if b.nwalls >= BLOCK_MIN_WALLS and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
            if self._fortress(b, t if s > 0 else o):
                return self._draw(t)
        return s

    def _search(self, b, depth, alpha, beta, F, ply, prev=None, nopass=False):
        self.nodes += 1
        if (self.nodes & 63) == 0 and time.perf_counter() > self.deadline:
            raise _Timeout

        t = b.turn
        o = 1 - t
        # Kiểm tra nếu đối thủ vừa bước vào hàng đích ở nước trước
        if b.pos[o] // 9 == GOAL_ROW[o]:
            return -(WIN - ply)
        if depth <= 0:
            return self._eval(b, F)

        key = b.zkey()
        ent = self.tt.get(key)
        tt_move = None
        if ent is not None:
            e_depth, e_flag, e_val, tt_move = ent
            if e_depth >= depth:
                if e_val > WIN - 200:
                    e_val -= ply
                elif e_val < -WIN + 200:
                    e_val += ply
                if e_flag == 0:
                    return e_val
                if e_flag == 1 and e_val > alpha:
                    alpha = e_val
                elif e_flag == 2 and e_val < beta:
                    beta = e_val
                if alpha >= beta:
                    return e_val

        a0 = alpha
        best = -WIN * 2
        best_mv = None
        me = b.pos[t]
        op = b.pos[o]
        f_me = F[t]
        near = abs(me // 9 - op // 9) + abs(me % 9 - op % 9)

        # 0. Safe Null-Move Pruning: hai quân xa nhau, chưa quân nào sát đích, mà ngay cả khi
        #    "đứng yên" vẫn vượt beta với biên an toàn -> cắt (PASS là nước hợp lệ nên an toàn).
        if (NMP_ON and ALLOW_PASS and not nopass and depth >= 3 and near > 2
                and -WIN + 200 < beta < WIN - 200):
            if f_me.dist(me) > 2 and F[o].dist(op) > 2:
                margin = NMP_MARGIN if b.left[o] > 0 else 0
                if self._eval(b, F) >= beta + margin:
                    b.turn = o
                    val = -self._search(b, depth - 1 - NMP_R, -beta, -beta + 1, F, ply + 1, PASS, True)
                    b.turn = t
                    if beta <= val < WIN - 200:
                        return val

        # 1. Xét các nước đi quân trước (không làm đổi tường -> tái sử dụng F tốn 0 BFS)
        pawns = b.pawn_moves(t)
        pawns.sort(key=f_me.dist)
        if tt_move is not None and tt_move >= 0 and tt_move < WALL0 and tt_move in pawns:
            pawns.remove(tt_move)
            pawns.insert(0, tt_move)
        if ALLOW_PASS and not nopass and near <= PASS_NEAR and depth >= 2:
            if tt_move == PASS:
                pawns.insert(0, PASS)
            else:
                pawns.append(PASS)

        for mv in pawns:
            if mv == PASS:
                b.turn = o
                val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1, PASS, True)
                b.turn = t
            else:
                b.pos[t] = mv
                b.turn = o
                val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1, mv)
                b.turn = t
                b.pos[t] = me
            if val > best:
                best = val
                best_mv = mv
                if val > alpha:
                    alpha = val
                    if alpha >= beta:
                        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                        return best

        # 2. Chỉ khi nước đi quân chưa cắt tỉa beta mới sinh và thử các nước đặt tường
        if b.left[t] > 0:
            cands = self._wall_cands(b, F[o], op, INNER_WALLS)
            hist = self.hist
            cands.sort(key=lambda w: -hist[w])            # ổn định: cùng điểm giữ thứ tự gần đối thủ
            special = []
            if tt_move is not None and tt_move >= WALL0:
                special.append(tt_move - WALL0)
            kl = self.killers[ply if ply < 63 else 63]
            for k in kl:
                if k is not None and k - WALL0 not in special:
                    special.append(k - WALL0)
            cmv = self.cm.get(prev)
            if cmv is not None and cmv - WALL0 not in special:
                special.append(cmv - WALL0)
            special = [w for w in special if not b.forbid[w]]
            if special:
                cands = special + [w for w in cands if w not in special]

            pts = b.pts
            p0_pos, p1_pos = b.pos[0], b.pos[1]
            for w in cands:
                # Trước khi đặt, kiểm tra xem tường có chạm >= 2 điểm đã có tường/biên không
                wp0, wp1, wp2 = WALL_PTS[w]
                need_conn_check = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2

                b.put_wall(w)
                F2 = b.fields()
                if need_conn_check and (F2[0].dist(p0_pos) >= INF or F2[1].dist(p1_pos) >= INF):
                    b.remove_wall(w)
                    continue

                b.left[t] -= 1
                b.turn = o
                val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1, WALL0 + w)
                b.turn = t
                b.left[t] += 1
                b.remove_wall(w)

                if val > best:
                    best = val
                    best_mv = WALL0 + w
                    if val > alpha:
                        alpha = val
                        if alpha >= beta:
                            code = WALL0 + w
                            hist[w] += depth * depth
                            if kl[0] != code:
                                kl[1] = kl[0]
                                kl[0] = code
                            if prev is not None:
                                self.cm[prev] = code
                            self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                            return best

        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
        return best

    def _store_tt(self, key, depth, best, a0, beta, best_mv, ply):
        if len(self.tt) > TT_SIZE:
            self.tt.clear()
        stored = best
        if stored > WIN - 200:
            stored += ply
        elif stored < -WIN + 200:
            stored -= ply
        flag = 2 if best <= a0 else (1 if best >= beta else 0)
        old = self.tt.get(key)
        if old is None or old[0] <= depth:
            self.tt[key] = (depth, flag, stored, best_mv)

    def _root_walls(self, b, F, t, root_ok):
        """Tường ứng viên ở gốc: mọi tường chặn một cạnh nằm trên MỘT đường ngắn nhất nào đó của địch,
        xếp hạng theo chênh lệch bước đi thật (địch chậm thêm - mình chậm thêm), lấy ROOT_WALLS tốt nhất.
        (Học từ _gen_walls của steve.py: chọn top-K theo chênh lệch thật thay vì theo độ gần.)"""
        o = 1 - t
        op, me = b.pos[o], b.pos[t]
        d_op = F[o].dist(op)
        d_me = F[t].dist(me)
        if d_op <= 0 or d_op >= INF:
            return []
        layers = F[o].layers
        c = b.can
        forbid = b.forbid
        cur = 1 << op
        cand = set()
        for k in range(d_op, 0, -1):
            nxt_layer = layers[k - 1]
            nxt = 0
            x = cur
            while x:
                low = x & -x
                x ^= low
                u = low.bit_length() - 1
                for dir_idx in range(4):
                    if (c[dir_idx] >> u) & 1:
                        v = u + DIRS[dir_idx]
                        if (nxt_layer >> v) & 1:
                            nxt |= 1 << v
                            for w in BLOCKERS[u][dir_idx]:
                                if not forbid[w]:
                                    cand.add(w)
            cur = nxt
        pts = b.pts
        scored = []
        p0_pos, p1_pos = b.pos[0], b.pos[1]
        for w in cand:
            if root_ok is not None and not root_ok(WALL0 + w):
                continue
            wp0, wp1, wp2 = WALL_PTS[w]
            need = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2
            b.put_wall(w)
            F2 = b.fields()
            ndo = F2[o].dist(op)
            ndm = F2[t].dist(me)
            ok = ndo < INF and ndm < INF
            if ok and need:
                ok = F2[0].dist(p0_pos) < INF and F2[1].dist(p1_pos) < INF
            b.remove_wall(w)
            if ok:
                scored.append((-(W_DIST * ((ndo - d_op) - (ndm - d_me))) - (self.hist[w] >> 6), w))
        scored.sort()
        return [w for _, w in scored[:ROOT_WALLS]]

    def _standoff(self, b):
        """Thế đối đầu trực diện: hai quân cùng hàng/cột, cách đúng 1 ô trống, tới lượt mình.
        Bước vào ô giữa thì địch nhảy qua đầu và lời 1 nhịp -> bên nào buộc phải bước trước là bên thiệt."""
        t = b.turn
        o = 1 - t
        p, q = b.pos[t], b.pos[o]
        pr, pc, qr, qc = p // 9, p % 9, q // 9, q % 9
        if not ((pc == qc and abs(pr - qr) == 2) or (pr == qr and abs(pc - qc) == 2)):
            return False
        mid = ((pr + qr) // 2) * 9 + (pc + qc) // 2
        if mid not in b.pawn_moves(t):
            return False                                  # có tường chắn giữa hai quân
        b.pos[t] = mid
        jumpable = False
        for m in b.pawn_moves(o):
            dr, dc = abs(m // 9 - qr), abs(m % 9 - qc)
            if (dr == 2 and dc == 0) or (dr == 0 and dc == 2):
                jumpable = True
                break
        b.pos[t] = p
        return jumpable

    def choose(self, board, root_ok=None, repeat=None, can_pass=False):
        """Chọn nước đi tốt nhất ở gốc bằng Iterative Deepening."""
        b = board.copy()
        t = b.turn
        o = 1 - t
        repeat = repeat or {}
        self.nodes = 0
        self.root = t
        self.deadline = time.perf_counter() + self.time_limit
        for i in range(128):                              # history giảm dần qua từng lượt
            self.hist[i] >>= 1
        self.killers = [[None, None] for _ in range(64)]
        self.cm.clear()

        # 1. Nếu có bước về đích ngay lập tức -> đi luôn
        for mv in b.pawn_moves(t):
            if mv // 9 == GOAL_ROW[t] and (root_ok is None or root_ok(mv)):
                return mv

        F = b.fields()
        me, op = b.pos[t], b.pos[o]

        # 2. Thế đối đầu: "chờ" bằng PASS (miễn phí) để ép địch phải bước vào ô giữa trước.
        #    Chỉ chờ tối đa STANDOFF_WAITS lần ở cùng một thế; sau đó để tìm kiếm quyết định.
        if can_pass and STANDOFF_WAITS > 0 and self._standoff(b):
            if repeat.get(PASS, 0) < STANDOFF_WAITS:
                return PASS

        # 3. Xây dựng danh sách nước đi ở gốc: đi quân trước, PASS (nếu gần địch), đặt tường sau
        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        pawns.sort(key=lambda m: (F[t].dist(m), repeat.get(m, 0)))
        near = abs(me // 9 - op // 9) + abs(me % 9 - op % 9)
        if can_pass and near <= PASS_NEAR:
            pawns.append(PASS)

        walls = []
        if b.left[t] > 0:
            for w in self._root_walls(b, F, t, root_ok):
                walls.append(WALL0 + w)

        actions = pawns + walls
        if not actions:
            return None
        if len(actions) == 1:
            return actions[0]

        best_action = actions[0]
        self.depth_reached = 0

        try:
            for depth in range(1, self.max_depth + 1):
                alpha, beta = -WIN * 2, WIN * 2
                cur_best, cur_score = None, -WIN * 2
                scores = {}

                for mv in actions:
                    if mv == PASS:
                        pen = PASS_PENALTY + REPEAT_PENALTY * repeat.get(PASS, 0)
                        b.turn = o
                        val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1, PASS, True) - pen
                        b.turn = t
                    elif mv < WALL0:
                        pen = REPEAT_PENALTY * repeat.get(mv, 0)
                        b.pos[t] = mv
                        b.turn = o
                        val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1, mv) - pen
                        b.turn = t
                        b.pos[t] = me
                    else:
                        w = mv - WALL0
                        b.put_wall(w)
                        F2 = b.fields()
                        if F2[0].dist(b.pos[0]) >= INF or F2[1].dist(b.pos[1]) >= INF:
                            b.remove_wall(w)
                            continue
                        b.left[t] -= 1
                        b.turn = o
                        try:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, 1, mv)
                        finally:
                            b.turn = t
                            b.left[t] += 1
                            b.remove_wall(w)

                    scores[mv] = val
                    if val > cur_score:
                        cur_score, cur_best = val, mv
                        if val > alpha:
                            alpha = val

                if cur_best is not None:
                    best_action = cur_best
                    self.depth_reached = depth
                    actions.sort(key=lambda a: -scores.get(a, -WIN * 2))

                if abs(cur_score) >= WIN - 200:
                    if depth >= WIN - abs(cur_score):
                        break
        except _Timeout:
            pass

        return best_action


# ------------------------------------------------- cầu nối với arena (JSON) --
_BOT = None          # giữ bảng nhớ (TT) xuyên suốt các lượt
_VISITS = {}         # (pid, ô_mình, ô_đối_thủ, số_tường) -> số lần đã đi vào; 'P' = số lần đứng yên
_LAST_TURN = {}      # pid -> số lượt gần nhất (để xóa lịch sử khi sang ván mới)
_PASSES = {}         # pid -> số lần đã PASS trong ván này


def board_from_observation(obs):
    """Dựng lại đối tượng Board từ observation của arena."""
    pid = obs["player_id"]
    b = Board()
    b.pos[pid] = obs["you"]["position"][0] * 9 + obs["you"]["position"][1]
    b.pos[1 - pid] = obs["opponent"]["position"][0] * 9 + obs["opponent"]["position"][1]
    b.left[pid] = obs["you"]["walls_remaining"]
    b.left[1 - pid] = obs["opponent"]["walls_remaining"]

    for r, c in obs["walls"]["horizontal"]:
        b.put_wall(wall_id('h', r, c))
    for r, c in obs["walls"]["vertical"]:
        b.put_wall(wall_id('v', r, c))

    b.turn = pid
    return b


def move_to_action(mv):
    """Mã số nguyên nước đi -> dict hành động theo giao thức của Arena."""
    if mv == PASS:
        return {"type": "pass"}
    if mv < WALL0:
        return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = wall_info(mv - WALL0)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}


def _fallback(observation):
    """Dự phòng an toàn: bước tới ô hợp lệ gần đích nhất (ưu tiên gần cột giữa)."""
    try:
        moves = observation["legal_moves"]
        if not moves:
            return {"type": "pass"}
        distances = observation["distances"]
        best = min(
            moves,
            key=lambda m: (distances[m[0]][m[1]] if distances[m[0]][m[1]] >= 0 else 10 ** 6) * 10
            + abs(m[1] - 4),
        )
        return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception:
        return {"type": "pass"}


def _choose(observation):
    global _BOT
    started = time.perf_counter()
    pid = observation["player_id"]
    turn = observation["turn"]
    max_turns = observation.get("max_turns", 500)

    # Nhận diện ván mới để làm sạch bộ đếm lặp lại thế cờ
    if turn <= 2 or turn < _LAST_TURN.get(pid, 0):
        for k in [k for k in _VISITS if k[0] == pid]:
            del _VISITS[k]
        _PASSES[pid] = 0
    _LAST_TURN[pid] = turn

    legal = set()
    for r, c in observation["legal_moves"]:
        legal.add(r * 9 + c)
    for w in observation["legal_walls"]:
        legal.add(WALL0 + wall_id(w["orientation"].lower(), w["row"], w["col"]))
    if not legal:
        return {"type": "pass"}

    board = board_from_observation(observation)
    if _BOT is None:
        _BOT = Bot()

    # Tàn cuộc: đối thủ hết tường -> tra bảng giải sẵn (chính xác tuyệt đối)
    if ENDGAME_ON and observation["opponent"]["walls_remaining"] == 0:
        act = _endgame_action(observation, board, legal, started, max_turns)
        if act is not None:
            if act["type"] != "pass" or ALLOW_PASS:
                return act

    nw = len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"])
    me_cell = board.pos[pid]
    opp_cell = board.pos[1 - pid]
    repeat = {
        m: _VISITS.get((pid, m, opp_cell, nw), 0)
        for m in legal
        if m < WALL0 and (pid, m, opp_cell, nw) in _VISITS
    }
    pkey = (pid, 'P', me_cell, opp_cell, nw)
    if pkey in _VISITS:
        repeat[PASS] = _VISITS[pkey]
    can_pass = ALLOW_PASS and _PASSES.get(pid, 0) < MAX_PASSES

    _BOT.time_limit = max(0.05, TIME_BUDGET - (time.perf_counter() - started))
    mv = _BOT.choose(board, root_ok=lambda m: m in legal, repeat=repeat, can_pass=can_pass)
    if mv is None or (mv != PASS and mv not in legal) or (mv == PASS and not can_pass):
        return _fallback(observation)

    if mv == PASS:
        _VISITS[pkey] = _VISITS.get(pkey, 0) + 1
        _PASSES[pid] = _PASSES.get(pid, 0) + 1
    elif mv < WALL0:
        vkey = (pid, mv, opp_cell, nw)
        _VISITS[vkey] = _VISITS.get(vkey, 0) + 1
    return move_to_action(mv)


def choose_action(observation):
    """Hàm giao tiếp chính được Arena gọi mỗi lượt."""
    try:
        return _choose(observation)
    except Exception:
        return _fallback(observation)