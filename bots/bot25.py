"""
BOT WRONGWAY - BẢN CHỐT: PROFILE P  (Python 3.9+, chỉ dùng thư viện chuẩn)

ĐÃ CHỐT PROFILE P = "ĐI TRƯỚC DÙNG v4, ĐI SAU DÙNG I":
  - Khi đi trước (P0): y hệt v4 (không giải tàn cuộc, không tích trữ tường).
  - Khi đi sau (P1): giải tàn cuộc chính xác + tích trữ tường mạnh (tường chỉ đặt nếu làm địch dài
    thêm >= 3 bước khi địch còn xa đích và mình đang dẫn).
Lý do: đo được I thắng khi đi sau những ván v4 thua, nhưng thua khi đi trước những ván v4 thắng;
ghép như vậy giữ lợi thế của v4 khi đi trước và dùng I khi đi sau. Các profile khác giữ nguyên bên
dưới; đổi dòng PROFILE = "P" gần cuối file nếu cần.

MỤC ĐÍCH: v4 và v5 đấu với `kronos` cho ra CÙNG MỘT kết quả (thua cả hai bên, cùng số lượt, cùng số
tường). Bot xác định đấu với bot xác định luôn ra cùng một ván, nên muốn thử cách chơi khác thì phải
đổi cách bot chọn nước. File này gom nhiều biến thể trong MỘT file: bạn chỉ đổi 1 dòng
        PROFILE = "A"
ở gần cuối file, chạy lại trận với kronos ở cả hai bên rồi ghi kết quả từng chữ cái.

  A = v4 y hệt (không giải tàn cuộc, không tích trữ tường)
  B = v10 (v4 + giải tàn cuộc chính xác)
  C = v5 (v10 + tích trữ tường khi mình dẫn xa)
  D = như B nhưng tường rẻ hơn (WALL_WEIGHT 0.5): dùng tường nhiều hơn, quyết liệt hơn
  E = như B nhưng tường quý hơn (WALL_WEIGHT 2.0): giữ tường lâu hơn
  F = như B nhưng thưởng nhảy gấp đôi (JUMP_BONUS 2.0)
  G = như B nhưng nghĩ lâu hơn (TIME_BUDGET 0.45)
  H = như B nhưng thưởng lượt đi 1.0 (TEMPO_BONUS)
  I = tích trữ mạnh hơn v5 (tường xa đích chỉ khi gain >= 3, "sắp tới đích" tính từ 6 bước)
  J = như D nhưng có tích trữ kiểu v5 (tường rẻ, nhưng chỉ đặt khi có lợi lớn lúc mình đang dẫn)
  P = ĐI TRƯỚC dùng v4, ĐI SAU dùng I (bản chốt)
  K = "ĐI TRƯỚC THÌ CÔNG, ĐI SAU THÌ THỦ": đi trước (P0) dùng tường rẻ (WALL_WEIGHT 0.5) và đặt tường
      thoải mái; đi sau (P1) giữ tường (WALL_WEIGHT 2.0, chỉ tích trữ khi mình dẫn xa). Cơ chế này
      chạy bằng SIDE_PARAMS: tham số riêng cho từng bên, chọn theo observation["player_id"].
      (Đo: K được 8/16 so với v4; chỉ công khi đi trước 7/16; chỉ thủ khi đi sau 7/16; đảo ngược
      "đi trước thủ, đi sau công" chỉ 4/16 -> hướng K không sai nhưng chưa có lợi.)
  L = A (v4) + NHIỄU NHỎ trong hàm đánh giá (EVAL_NOISE 0.3)
  M = B (v10) + nhiễu 0.3
  N = C (v5) + nhiễu 0.3
      NHIỄU làm bot KHÔNG còn xác định: cùng một đối thủ, mỗi lần chạy có thể ra ván khác. Gợi ý từ
      diễn đàn game AI: dùng hiệu số đường đi "cộng một chút ngẫu nhiên". Đo: L đấu v4 được 24/48
      (không yếu đi); trong thử nghiệm của tôi, một ván mà v4 thua cố định (đi sau trước wall_builder)
      thì có nhiễu thắng 5/6 lần. Arena đặt seed cho random theo --seed, nên muốn lấy nhiều mẫu cùng
      một cặp đấu, chạy lại arena.py với --seed 1, 2, 3...

LƯU Ý: A, B, C đã đấu tập kỹ với các bot cũ. D đến J chỉ là thay hằng số mà các lần đo trước cho
kết quả gần ngang bản gốc; chúng KHÔNG được chứng minh mạnh hơn. Dùng để tìm biến thể thắng được
một đối thủ cụ thể, rồi mới đo lại với nhiều đối thủ khác.

V5 = v10 (v4 + giải tàn cuộc chính xác) đổi HƯỚNG DÙNG TƯỜNG: khi địch còn xa đích
(> NEAR_DIST bước) và mình đang dẫn xa (hơn LEAD_MARGIN bước), chỉ đặt tường làm địch dài thêm >= FAR_MIN_GAIN
bước; khi địch sắp tới đích hoặc đang dẫn trước thì đặt tường như bình thường. Mục đích:
giữ tường cho đòn quyết định thay vì đốt sớm. Đo được: ngang v10 với bot yếu hơn, kém
v10 với bot minimax mạnh -> dùng như phương án khác, KHÔNG thay v4.

V10 = v4 + BỘ GIẢI TÀN CUỘC: khi cả hai bên hết tường, bot giải chính xác ván chạy đua
(thắng/thua/hòa, kể cả nhảy qua quân) bằng tính ngược, rồi đi nước thắng nhanh nhất
(hoặc thua chậm nhất). Xem khối "TÀN CUỘC CHÍNH XÁC" trước BƯỚC 8.

Cách dùng: sao chép file này thành submissions/MSSV.py rồi chạy bằng arena.py.

Cùng thuật toán với v3 (Negamax + Alpha-Beta + BFS, giữ tường, chống lặp,
thưởng cú nhảy) nhưng viết lại để CHẠY NHANH HƠN, nhờ đó nhìn sâu hơn trong
cùng 0.35 giây. Ba mẹo tăng tốc (đều có trong các bài viết về Quoridor AI):
  1. Mỗi ô là một SỐ NGUYÊN 0..80 (thay vì tuple (hàng, cột)); mỗi cạnh là một số
     nguyên -> tra cứu trong set nhanh hơn nhiều.
  2. Bảng tra sẵn: hàng xóm của mỗi ô, các tường chặn từng cạnh, các tường xung
     đột nhau. Tính một lần khi nạp file.
  3. Khi thử một tường, chỉ chạy BFS cho NGƯỜI MÌNH nếu tường đó chạm vào đường
     đi ngắn nhất của mình. Nếu không chạm thì đường của mình chắc chắn không đổi.

BẢN ĐỒ FILE
  BƯỚC 0 Hằng số | BƯỚC 1 Bảng tra sẵn | BƯỚC 2 State | BƯỚC 3 BFS
  BƯỚC 4 Nước đi quân cờ | BƯỚC 5 Liệt kê hành động | BƯỚC 6 Cho điểm
  BƯỚC 7 Negamax | BƯỚC 8 Hàm Arena gọi
"""

import random
import time

# =====================================================================
# BƯỚC 0: HẰNG SỐ
# =====================================================================
SIZE = 9
TIME_BUDGET = 0.35     # giây nghĩ mỗi lượt (Arena cho 1 giây; chừa chỗ cho máy chậm)
MAX_DEPTH = 16
WALL_WEIGHT = 1.0      # mỗi tường còn lại đáng bao nhiêu "bước đi" (đo được: 1.0 hơn 0.35 và 2.0;
                       # 3.5 thì thua rõ -> giữ tường là tốt nhưng đừng coi nó quá quý)
TEMPO_BONUS = 0.5      # người tới lượt được cộng chút điểm (bỏ đi thì yếu hơn khi đo)
JUMP_BONUS = 1.0       # lợi thế khi nhảy qua được đối thủ (bỏ đi thì yếu hơn khi đo)
WIN_SCORE = 1000.0
REPEAT_LIMIT = 2       # chống lặp: thế cờ lặp quá số lần này thì đổi nước
REPEAT_TOLERANCE = 2.0
GOAL_ROW = [0, 8]
EVAL_NOISE = 0.0   # 0 = tắt; >0: cộng nhiễu ngẫu nhiên +-EVAL_NOISE điểm vào hàm đánh giá
FAR_MIN_GAIN = 2    # khi địch còn XA đích và mình không bị bỏ xa: chỉ đặt tường có gain >= số này
LEAD_MARGIN = 4     # đặt tường bình thường khi (đường địch - đường mình) <= số này; chỉ tích trữ khi mình dẫn > 4 bước
NEAR_DIST = 4       # địch còn tối đa bấy nhiêu bước tới đích thì coi là 'sắp tới đích'

# =====================================================================
# BƯỚC 1: BẢNG TRA SẴN (tính một lần khi nạp file)
# =====================================================================
# Ô (hàng, cột) -> số nguyên i = hàng*9 + cột.  Hướng: 0 lên, 1 xuống, 2 trái, 3 phải.
# Cạnh từ ô i sang ô j -> số nguyên i*81 + j ("mã cạnh").
DR = (-1, 1, 0, 0)
DC = (0, 0, -1, 1)
NB = []                       # NB[i][d] = ô kề theo hướng d, hoặc -1 nếu ra ngoài bàn
for _i in range(81):
    _r, _c = divmod(_i, 9)
    _row = []
    for _d in range(4):
        _nr, _nc = _r + DR[_d], _c + DC[_d]
        _row.append(_nr * 9 + _nc if 0 <= _nr < 9 and 0 <= _nc < 9 else -1)
    NB.append(tuple(_row))
NEIGH = [[(j, i * 81 + j) for j in NB[i] if j >= 0] for i in range(81)]
SIDE = ((2, 3), (2, 3), (0, 1), (0, 1))   # hai hướng "bên cạnh" khi nhảy chéo

# Tường: mã tường = loại*64 + hàng*8 + cột  (loại 0 = ngang H, 1 = dọc V)
WALL_EDGES = {}     # mã tường -> tập mã cạnh bị chặn (cả hai chiều)
WALL_INFO = {}      # mã tường -> ("H"/"V", hàng, cột)
CONFLICT = {}       # mã tường -> tập tường không được đặt cùng (chồng lấn / cắt chéo)
EDGE_WALLS = {}     # mã cạnh -> danh sách tường chặn được cạnh đó
for _o in (0, 1):
    for _r in range(8):
        for _c in range(8):
            _w = _o * 64 + _r * 8 + _c
            _a = _r * 9 + _c
            _pairs = [(_a, _a + 9), (_a + 1, _a + 10)] if _o == 0 else [(_a, _a + 1), (_a + 9, _a + 10)]
            _codes = set()
            for _x, _y in _pairs:
                _codes.add(_x * 81 + _y)
                _codes.add(_y * 81 + _x)
            WALL_EDGES[_w] = frozenset(_codes)
            WALL_INFO[_w] = ("H" if _o == 0 else "V", _r, _c)
            for _code in _codes:
                EDGE_WALLS.setdefault(_code, []).append(_w)
            _bad = {_w}
            if _o == 0:
                if _c > 0: _bad.add(_r * 8 + _c - 1)
                if _c < 7: _bad.add(_r * 8 + _c + 1)
                _bad.add(64 + _r * 8 + _c)
            else:
                if _r > 0: _bad.add(64 + (_r - 1) * 8 + _c)
                if _r < 7: _bad.add(64 + (_r + 1) * 8 + _c)
                _bad.add(_r * 8 + _c)
            CONFLICT[_w] = frozenset(_bad)


# =====================================================================
# BƯỚC 2: STATE - "ẢNH CHỤP" MỘT THẾ CỜ
# =====================================================================
#   pos: (ô người 0, ô người 1)   walls_left: (tường còn của người 0, người 1)
#   walls: tập mã tường đã đặt    blocked: tập mã cạnh bị chặn    turn: người tới lượt
class State:
    __slots__ = ("pos", "walls_left", "walls", "blocked", "turn")

    def __init__(self, pos, walls_left, walls, blocked, turn):
        self.pos = pos
        self.walls_left = walls_left
        self.walls = walls
        self.blocked = blocked
        self.turn = turn

    def after_move(self, cell):
        pos = list(self.pos)
        pos[self.turn] = cell
        return State(tuple(pos), self.walls_left, self.walls, self.blocked, 1 - self.turn)

    def after_wall(self, wall):
        left = list(self.walls_left)
        left[self.turn] -= 1
        return State(self.pos, tuple(left), self.walls | {wall},
                     self.blocked | WALL_EDGES[wall], 1 - self.turn)


# =====================================================================
# BƯỚC 3: BFS - SỐ BƯỚC TỚI ĐÍCH CỦA MỌI Ô (có nhớ kết quả)
# =====================================================================
_DIST_CACHE = {}


def distance_map(state, player):
    goal = GOAL_ROW[player]
    key = (goal, state.walls)
    cached = _DIST_CACHE.get(key)
    if cached is not None:
        return cached
    dist = [-1] * 81
    queue = []
    for c in range(9):                  # cả hàng đích có khoảng cách 0
        dist[goal * 9 + c] = 0
        queue.append(goal * 9 + c)
    blocked = state.blocked
    head = 0
    while head < len(queue):            # loang từng lớp
        i = queue[head]
        head += 1
        nd = dist[i] + 1
        for j, code in NEIGH[i]:
            if dist[j] < 0 and code not in blocked:
                dist[j] = nd
                queue.append(j)
    _DIST_CACHE[key] = dist
    return dist


def path_edges(state, player):
    """Tập mã cạnh nằm trên MỘT trong các đường ngắn nhất của `player` tới đích."""
    dist = distance_map(state, player)
    start = state.pos[player]
    blocked = state.blocked
    edges = set()
    seen = {start}
    stack = [start]
    while stack:
        i = stack.pop()
        if dist[i] <= 0:
            continue
        nd = dist[i] - 1
        for j, code in NEIGH[i]:
            if dist[j] == nd and code not in blocked:
                edges.add(code)
                if j not in seen:
                    seen.add(j)
                    stack.append(j)
    return edges


# =====================================================================
# BƯỚC 4: NƯỚC ĐI CỦA QUÂN CỜ (gồm nhảy qua đối thủ)
# =====================================================================
def pawn_moves(pos, opp, blocked):
    result = []
    for d in range(4):
        a = NB[pos][d]
        if a < 0 or pos * 81 + a in blocked:
            continue
        if a != opp:
            result.append(a)
            continue
        b = NB[a][d]                                   # ô sau lưng đối thủ
        if b >= 0 and a * 81 + b not in blocked:
            result.append(b)                           # nhảy thẳng
        else:
            for sd in SIDE[d]:                         # đi vòng sang hai bên đối thủ
                s = NB[a][sd]
                if s >= 0 and a * 81 + s not in blocked:
                    result.append(s)
    return result


# =====================================================================
# BƯỚC 5: LIỆT KÊ HÀNH ĐỘNG ĐÁNG XÉT
# =====================================================================
# Hành động nội bộ: (0, ô_đích) là đi quân, (1, mã_tường) là đặt tường.
def list_actions(state):
    """Trả về list (độ_ưu_tiên, hành_động, state_con), sắp từ tốt tới kém.

    - Nước đi quân cờ: ưu tiên = số ô tiến được tới đích (nhảy qua đối thủ = +2).
    - Tường: chỉ xét tường CHẶN MỘT CẠNH TRÊN ĐƯỜNG NGẮN NHẤT CỦA ĐỊCH, vì tường ở nơi
      địch không đi qua thì không làm đường của địch dài thêm. Tính
          gain = (địch bị dài thêm) - (mình bị dài thêm)
      và chỉ giữ tường có gain >= 1. Ví dụ: địch từ 4 bước thành 6 bước, mình giữ
      nguyên -> gain = 2 -> tường tốt.
    Độ ưu tiên chỉ để sắp thứ tự thử (nước tốt thử trước thì Alpha-Beta cắt nhánh
    được nhiều hơn); điểm thật do negamax tính.
    """
    me = state.turn
    opp = 1 - me
    my_pos, opp_pos = state.pos[me], state.pos[opp]
    my_map = distance_map(state, me)
    opp_map = distance_map(state, opp)
    d_me = my_map[my_pos]
    d_opp = opp_map[opp_pos]
    out = []

    for cell in pawn_moves(my_pos, opp_pos, state.blocked):
        out.append((d_me - my_map[cell], (0, cell), state.after_move(cell)))

    if state.walls_left[me] > 0:
        candidates = set()
        for code in path_edges(state, opp):          # chỉ tường chặn đường ngắn nhất của địch
            candidates.update(EDGE_WALLS[code])
        my_path = None
        walls = state.walls
        for wall in candidates:
            if not CONFLICT[wall].isdisjoint(walls):  # chồng lấn / cắt chéo
                continue
            child = state.after_wall(wall)
            d_opp_after = distance_map(child, opp)[opp_pos]
            if d_opp_after < 0:                      # chặn kín đường địch: không hợp lệ
                continue
            if my_path is None:
                my_path = path_edges(state, me)
            if WALL_EDGES[wall].isdisjoint(my_path):
                d_me_after = d_me                    # không chạm đường mình -> không đổi
            else:
                d_me_after = distance_map(child, me)[my_pos]
                if d_me_after < 0:                   # chặn kín đường mình: không hợp lệ
                    continue
            gain = (d_opp_after - d_opp) - (d_me_after - d_me)
            if gain >= (1 if (d_opp <= NEAR_DIST or d_opp - d_me <= LEAD_MARGIN) else FAR_MIN_GAIN):
                out.append((gain - 0.5, (1, wall), child))

    out.sort(key=lambda item: -item[0])
    return out


# =====================================================================
# BƯỚC 6: CHO ĐIỂM MỘT THẾ CỜ (cho người đang tới lượt)
# =====================================================================
def evaluate(state):
    """Điểm = (đường địch - đường mình) + TEMPO + WALL_WEIGHT*(tường mình - tường địch)
    (+ JUMP_BONUS nếu hai quân sát nhau và người tới lượt nhảy được).
    Ví dụ: mình còn 5 bước, địch 6, hai bên cùng 10 tường:
        điểm = (6 - 5) + 0.5 + 0 = 1.5     (dương = người tới lượt đang có lợi)
    Vì BFS bỏ qua quân cờ nên không thấy cú nhảy; ta bù bằng JUMP_BONUS.
    """
    me = state.turn
    opp = 1 - me
    my_pos, opp_pos = state.pos[me], state.pos[opp]
    my_map = distance_map(state, me)
    d_me = my_map[my_pos]
    d_opp = distance_map(state, opp)[opp_pos]
    score = ((d_opp - d_me) + TEMPO_BONUS
             + WALL_WEIGHT * (state.walls_left[me] - state.walls_left[opp]))
    if EVAL_NOISE:
        score += EVAL_NOISE * (2.0 * random.random() - 1.0)   # nhiễu nhỏ: cùng một thế cờ có thể cho ván khác nhau
    diff = my_pos - opp_pos
    if diff == 9 or diff == -9 or ((diff == 1 or diff == -1) and my_pos // 9 == opp_pos // 9):
        for cell in pawn_moves(my_pos, opp_pos, state.blocked):   # hai quân sát nhau
            if my_map[cell] <= d_me - 2:                          # nhảy được: tiến 2 ô
                score += JUMP_BONUS
                break
    return score


# =====================================================================
# BƯỚC 7: NEGAMAX + ALPHA-BETA
# =====================================================================
class SearchTimeout(Exception):
    pass


def negamax(state, depth, alpha, beta, deadline):
    """Điểm của `state` cho người đang tới lượt, giả sử cả hai chơi tốt nhất.

    - depth: còn được nhìn trước bao nhiêu nước.
    - Negamax: điểm tốt của địch = điểm xấu của mình, nên mỗi tầng đệ quy ta ĐỔI DẤU:
          điểm của mình = -(điểm của địch ở thế cờ kế tiếp).
    - alpha/beta: nếu một nhánh chắc chắn không hơn lựa chọn đã có thì `break`.
    - Kiểm tra giờ ở MỌI nút: hết giờ thì ném SearchTimeout để thoát ngay.
    """
    if time.perf_counter() > deadline:
        raise SearchTimeout
    previous = 1 - state.turn
    if state.pos[previous] // 9 == GOAL_ROW[previous]:
        return -WIN_SCORE - depth                 # người vừa đi đã tới đích -> mình thua
    if depth == 0:
        return evaluate(state)
    best = -1e9
    for _, _, child in list_actions(state):
        value = -negamax(child, depth - 1, -beta, -alpha, deadline)
        if value > best:
            best = value
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best



# =====================================================================
# TÀN CUỘC CHÍNH XÁC: khi CẢ HAI hết tường, trò chơi chỉ còn hai quân cờ chạy đua.
# Khi đó tường cố định, nên ta GIẢI TRỌN: mỗi thế (ô người 0, ô người 1, lượt) là
# thắng / thua / hòa cho người tới lượt. Cách giải: "tính ngược" từ các thế thắng ngay.
# =====================================================================
ENDGAME = 1                 # 0 = tắt
_ENDGAME = {}               # tập tường -> {thế: (kết quả, độ sâu)}  kết quả +1 thắng, -1 thua


def solve_endgame(state, deadline):
    key = state.walls
    cached = _ENDGAME.get(key)
    if cached is not None:
        return cached
    blocked = state.blocked
    goal = GOAL_ROW
    start = (state.pos[0], state.pos[1], state.turn)
    succ = {}
    win_now = []
    seen = {start}
    stack = [start]
    while stack:
        if time.perf_counter() > deadline:
            return None
        st = stack.pop()
        p0, p1, t = st
        mover, other = (p0, p1) if t == 0 else (p1, p0)
        nxt = []
        immediate = False
        for cell in pawn_moves(mover, other, blocked):
            if cell // 9 == goal[t]:
                immediate = True
                break
            ns = (cell, p1, 1) if t == 0 else (p0, cell, 0)
            nxt.append(ns)
            if ns not in seen:
                seen.add(ns)
                stack.append(ns)
        if immediate:
            win_now.append(st)
        else:
            succ[st] = nxt
    result = {}
    queue = []
    for st in win_now:
        result[st] = (1, 1)
        queue.append(st)
    preds = {}
    remaining = {}
    for st, nxt in succ.items():
        remaining[st] = len(nxt)
        for n in nxt:
            preds.setdefault(n, []).append(st)
    head = 0
    while head < len(queue):
        st = queue[head]
        head += 1
        res, dep = result[st]
        for p in preds.get(st, ()):
            if p in result:
                continue
            if res == -1:                       # p có đường tới thế đối thủ thua -> p thắng
                result[p] = (1, dep + 1)
                queue.append(p)
            else:
                remaining[p] -= 1
                if remaining[p] == 0:           # mọi nước của p đều dẫn tới thế đối thủ thắng
                    result[p] = (-1, dep + 1)
                    queue.append(p)
    _ENDGAME[key] = result
    return result


def endgame_move(state, table):
    """Chọn nước theo bảng giải; None nếu thế này chưa phân thắng/thua (hòa)."""
    t = state.turn
    p0, p1 = state.pos
    mover, other = (p0, p1) if t == 0 else (p1, p0)
    here = table.get((p0, p1, t))
    best = None
    best_key = None
    for cell in pawn_moves(mover, other, state.blocked):
        ns = (cell, p1, 1) if t == 0 else (p0, cell, 0)
        r = table.get(ns)
        if here is not None and here[0] == 1:
            if r is not None and r[0] == -1 and (best_key is None or r[1] < best_key):
                best, best_key = cell, r[1]       # thắng nhanh nhất
        elif here is not None and here[0] == -1:
            if r is not None and r[0] == 1 and (best_key is None or r[1] > best_key):
                best, best_key = cell, r[1]       # thua chậm nhất
    return best


# =====================================================================
# BƯỚC 8: HÀM ARENA GỌI
# =====================================================================
_SEEN = {}


def build_state(observation):
    me = observation["player_id"]
    opp = 1 - me
    GOAL_ROW[me] = observation["you"]["goal_row"]
    GOAL_ROW[opp] = observation["opponent"]["goal_row"]
    walls = set()
    blocked = set()
    for r, c in observation["walls"]["horizontal"]:
        walls.add(r * 8 + c)
        blocked |= WALL_EDGES[r * 8 + c]
    for r, c in observation["walls"]["vertical"]:
        walls.add(64 + r * 8 + c)
        blocked |= WALL_EDGES[64 + r * 8 + c]
    pos = [0, 0]
    pos[me] = observation["you"]["position"][0] * 9 + observation["you"]["position"][1]
    pos[opp] = observation["opponent"]["position"][0] * 9 + observation["opponent"]["position"][1]
    left = [0, 0]
    left[me] = observation["you"]["walls_remaining"]
    left[opp] = observation["opponent"]["walls_remaining"]
    return State(tuple(pos), tuple(left), frozenset(walls), frozenset(blocked), me)


def safe_move(observation):
    moves = observation["legal_moves"]
    if not moves:
        return {"type": "pass"}
    dist = observation["distances"]
    best, best_d = moves[0], 10 ** 9
    for r, c in moves:
        if 0 <= dist[r][c] < best_d:
            best, best_d = [r, c], dist[r][c]
    return {"type": "move", "to": [int(best[0]), int(best[1])]}


def to_action(action):
    kind, data = action
    if kind == 0:
        return {"type": "move", "to": [data // 9, data % 9]}
    o, r, c = WALL_INFO[data]
    return {"type": "wall", "orientation": o, "row": r, "col": c}


SIDE_PARAMS = {}     # {0: {...}, 1: {...}}: tham số riêng cho người đi trước (0) / đi sau (1)
_BASE = {}


def apply_side(me):
    """Đặt tham số theo bên: bên đi trước (P0) và bên đi sau (P1) có thể chơi khác nhau."""
    keys = set()
    for d in SIDE_PARAMS.values():
        keys.update(d)
    for k in keys:
        if k not in _BASE:
            _BASE[k] = globals()[k]
    for k in keys:
        globals()[k] = _BASE[k]
    globals().update(SIDE_PARAMS.get(me, {}))


def think(observation):
    """Chọn hành động: tăng dần độ sâu 1, 2, 3... đến hết giờ, dùng kết quả của độ sâu
    xong gần nhất (iterative deepening), rồi chống lặp."""
    apply_side(observation["player_id"])
    deadline = time.perf_counter() + TIME_BUDGET
    if len(_DIST_CACHE) > 30000:
        _DIST_CACHE.clear()
    state = build_state(observation)

    for r, c in observation["legal_moves"]:             # về đích được thì đi luôn
        if r == observation["you"]["goal_row"]:
            return {"type": "move", "to": [int(r), int(c)]}

    if ENDGAME and state.walls_left == (0, 0):          # cả hai hết tường: giải chính xác
        table = solve_endgame(state, deadline)
        if table is not None:
            cell = endgame_move(state, table)
            legal_now = {m[0] * 9 + m[1] for m in observation["legal_moves"]}
            if cell is not None and cell in legal_now:      # luôn kiểm tra lại với Arena
                return {"type": "move", "to": [cell // 9, cell % 9]}

    legal_moves = {m[0] * 9 + m[1] for m in observation["legal_moves"]}
    legal_walls = set()
    for w in observation["legal_walls"]:
        legal_walls.add((0 if w["orientation"] == "H" else 64) + w["row"] * 8 + w["col"])
    root = [item for item in list_actions(state)
            if (item[1][0] == 0 and item[1][1] in legal_moves)
            or (item[1][0] == 1 and item[1][1] in legal_walls)]
    if not root:
        return safe_move(observation)

    order = root
    ranked = []
    for depth in range(1, MAX_DEPTH + 1):
        try:
            scored = []
            alpha = -1e9
            for priority, action, child in order:
                value = -negamax(child, depth - 1, -1e9, -alpha, deadline)
                scored.append((value, priority, action, child))
                if value > alpha:
                    alpha = value
            scored.sort(key=lambda item: -item[0])
            ranked = scored
            order = [(s[1], s[2], s[3]) for s in scored]
            if scored[0][0] > WIN_SCORE / 2:
                break
        except SearchTimeout:
            break

    if not ranked:
        return to_action(root[0][1])

    top_value = ranked[0][0]
    chosen = ranked[0]
    for item in ranked:                                   # chống lặp
        key = (item[3].pos, item[3].walls)
        if _SEEN.get(key, 0) < REPEAT_LIMIT and item[0] >= top_value - REPEAT_TOLERANCE:
            chosen = item
            break
    key = (chosen[3].pos, chosen[3].walls)
    _SEEN[key] = _SEEN.get(key, 0) + 1
    return to_action(chosen[2])


def choose_action(observation):
    """Hàm Arena gọi mỗi lượt. Có lỗi gì cũng trả về một nước đi hợp lệ (safe_move)."""
    try:
        return think(observation)
    except Exception:
        return safe_move(observation)


# =====================================================================
# CHỌN BIẾN THỂ: đổi chữ cái bên dưới (A đến J) rồi chạy lại.
# =====================================================================
PROFILE = "P"

PROFILES = {
    "A": {"ENDGAME": 0, "FAR_MIN_GAIN": 1},
    "B": {"ENDGAME": 1, "FAR_MIN_GAIN": 1},
    "C": {"ENDGAME": 1, "FAR_MIN_GAIN": 2, "NEAR_DIST": 4, "LEAD_MARGIN": 4},
    "D": {"ENDGAME": 1, "FAR_MIN_GAIN": 1, "WALL_WEIGHT": 0.5},
    "E": {"ENDGAME": 1, "FAR_MIN_GAIN": 1, "WALL_WEIGHT": 2.0},
    "F": {"ENDGAME": 1, "FAR_MIN_GAIN": 1, "JUMP_BONUS": 2.0},
    "G": {"ENDGAME": 1, "FAR_MIN_GAIN": 1, "TIME_BUDGET": 0.45},
    "H": {"ENDGAME": 1, "FAR_MIN_GAIN": 1, "TEMPO_BONUS": 1.0},
    "I": {"ENDGAME": 1, "FAR_MIN_GAIN": 3, "NEAR_DIST": 6, "LEAD_MARGIN": 2},
    "J": {"ENDGAME": 1, "FAR_MIN_GAIN": 2, "NEAR_DIST": 4, "LEAD_MARGIN": 4, "WALL_WEIGHT": 0.5},
    "K": {"ENDGAME": 1, "FAR_MIN_GAIN": 1,
          "SIDE_PARAMS": {0: {"WALL_WEIGHT": 0.5, "FAR_MIN_GAIN": 1},
                          1: {"WALL_WEIGHT": 2.0, "FAR_MIN_GAIN": 2, "NEAR_DIST": 4, "LEAD_MARGIN": 4}}},
    "P": {"ENDGAME": 0, "FAR_MIN_GAIN": 1,
          "SIDE_PARAMS": {0: {"ENDGAME": 0, "FAR_MIN_GAIN": 1},
                          1: {"ENDGAME": 1, "FAR_MIN_GAIN": 3, "NEAR_DIST": 6, "LEAD_MARGIN": 2}}},
    "L": {"ENDGAME": 0, "FAR_MIN_GAIN": 1, "EVAL_NOISE": 0.3},
    "M": {"ENDGAME": 1, "FAR_MIN_GAIN": 1, "EVAL_NOISE": 0.3},
    "N": {"ENDGAME": 1, "FAR_MIN_GAIN": 2, "NEAR_DIST": 4, "LEAD_MARGIN": 4, "EVAL_NOISE": 0.3},
}
globals().update(PROFILES.get(PROFILE, PROFILES["A"]))
