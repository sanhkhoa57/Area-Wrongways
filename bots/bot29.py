"""Bot Wrongway (testcode.py): BFS + DFS tìm đường, Minimax (Negamax) Alpha-Beta đào sâu dần,
cơ sở dữ liệu thế cờ giữ qua các lượt và hàm đánh giá thế cờ kiểu cờ vua.

Mỗi lượt Arena gọi choose_action(observation) (tối đa 1 giây):
  1. Đọc observation thành bitboard: mỗi ô là 1 bit của một số nguyên 81 bit,
     nên BFS chạy bằng phép dịch bit thay vì duyệt từng ô.
  2. Dọn cơ sở dữ liệu: thế cờ nào thiếu một tường đã đặt (hoặc còn nhiều tường
     trong tay hơn thực tế) thì không bao giờ xảy ra nữa -> xóa. Các nhánh còn
     lại (đúng nước địch vừa đi) được giữ và dùng tiếp.
  3. BFS tính khoảng cách tới đích của ta và địch; DFS lần theo một đường ngắn
     nhất để biết những tường nào cắt đường đó (ứng viên đặt tường).
  4. Negamax + Alpha-Beta + PVS + LMR, đào sâu dần tới khi hết 0,8 giây. Mỗi bên
     được xét cả đi quân lẫn đặt tường, luôn giả định đối phương đi nước tốt nhất.
  5. Chọn nước có điểm cao nhất (sau khi trừ phạt đi lặp/lùi/tường yếu).

Điểm thế cờ tính theo đơn vị "bước", từ góc nhìn bên tới lượt: dương là đang
ưu thế, âm là thất thế (như điểm thế cờ trong cờ vua). Các hệ số của hàm đánh giá
được khớp với kết quả ~3000 ván thật (evolve/tune_tc_eval.py) để điểm không "ảo
giác": thế chấm >= 3 điểm mà đoán sai bên thắng giảm từ 10,7% xuống 7,5%.

Khi nào đặt tường? Một tường trong tay có giá trị (1,05 + 0,1 × số bước một tường
đang gây được cho đối thủ, thường 1,2-1,7 bước). Đặt tường chỉ đáng khi nó làm
đường địch dài thêm nhiều hơn giá trị đó (cộng phần mình bị dài thêm), xét cả các
nước trả đũa trong cây tìm kiếm. Vì vậy bot giữ tường khi địch còn đường rộng/sát
đích (tường rẻ, chỉ lợi 1-2 bước) và đặt khi một tường cắt được nhiều bước.
Tường lợi ít (<= 1 bước) hoặc đặt sớm ở khai cuộc bị phạt thêm ở nút gốc.
Chỉ dùng thư viện chuẩn.
"""

import time

# ==========================================
# 1. THAM SỐ
# ==========================================
TIME_BUDGET = 0.8       # giây suy nghĩ tối đa mỗi nước (Arena cho 1 giây)
SAFETY = 0.03           # trừ hao cho phần việc sau khi dừng tìm kiếm
NEXT_DEPTH_FRAC = 0.6   # chỉ mở độ sâu mới khi chưa dùng quá phần này của thời gian
MAX_DEPTH = 30
MAX_WALLS = 4           # số tường tốt nhất xét ở mỗi nút bên trong cây
LMR_FROM = 3            # từ nước thứ bấy nhiêu (đếm từ 0) thử tìm nông hơn 1 lớp trước

# Hàm đánh giá (đơn vị: bước đi)
WALL_VALUE = 1.5        # giá tường ước lượng, chỉ dùng để sắp thứ tự nước thử trong cây
TEMPO = 0.375           # lợi thế của bên đang tới lượt (khớp dữ liệu)
MONO_BONUS = 0.3        # mỗi tường đáng thêm bấy nhiêu khi đối thủ đã hết tường (khớp dữ liệu)
WALL_DYN_BASE = 1.05     # giá trị tường = WALL_DYN_BASE + WALL_DYN_SLOPE × (số bước
WALL_DYN_SLOPE = 0.1     #   một tường đang gây được cho đối thủ),
WALL_VALUE_MIN = 0.5     #   kẹp trong khoảng [WALL_VALUE_MIN, WALL_VALUE_MAX]
WALL_VALUE_MAX = 3.5

# Phạt ở nút gốc (chỉ ảnh hưởng việc chọn nước, không ảnh hưởng cây tìm kiếm)
REPEAT_PENALTY = 0.75           # nước dẫn về thế cờ đã gặp (tránh đi lặp vô tận)
BACK_PENALTY = 0.75             # mỗi bước đi quân làm đường mình dài thêm
WEAK_WALL_PENALTY = (0.25, 0.12)  # tường lợi ròng <= 0 / đúng 1 bước khi địch chưa gấp
OPENING_WALL_PENALTY = 0.15     # khai cuộc: giữ tường, chỉ đặt khi lợi ròng >= 3
URGENCY_START = 0.2     # sau phần này của giới hạn lượt, tiến về đích được thưởng thêm (tránh hòa)
URGENCY_BONUS = 3.0     # thưởng mỗi bước tiến khi chạm giới hạn lượt

# Tường phòng thủ: bịt trước chỗ hở trên đường của mình
DEFENSE_MIN = 2         # chỉ phòng thủ khi địch có thể làm mình dài thêm >= 2 bước bằng 1 tường
MAX_DEFENSE = 4
DEFENSE_TIME = 0.12

TT_LIMIT = 300000       # số thế cờ tối đa trong cơ sở dữ liệu
DIST_LIMIT = 30000      # số bảng BFS tối đa trong bộ đệm

WIN = 100000.0          # tới đích thật sự
WIN_BOUND = WIN - 1000.0
SURE = 1000.0           # "chắc thắng": đối thủ hết tường và chậm hơn hẳn
SURE_BOUND = SURE / 2
UNREACH = 99
EPS = 0.01              # độ rộng cửa sổ rỗng của PVS
EXACT, LOWER, UPPER = 0, 1, 2
NEG_INF = float("-inf")
POS_INF = float("inf")

# ==========================================
# 2. BITBOARD VÀ BẢNG TÍNH SẴN
# ==========================================
# Ô (r, c) là bit r*9 + c. Bốn mặt nạ up/dn/lf/rt chứa các ô đi được theo hướng đó
# (không bị biên hay tường chặn). Tường có mã: 100 + a (ngang) hoặc 200 + a (dọc),
# với neo a = r*8 + c; nước đi quân có mã là số ô đích 0..80.


def _bit(r, c):
    return 1 << (r * 9 + c)


ALL = (1 << 81) - 1
ROW = [sum(_bit(r, c) for c in range(9)) for r in range(9)]
COL = [sum(_bit(r, c) for r in range(9)) for c in range(9)]
GOAL_ROW = (0, 8)                   # gid 0: đích hàng 0; gid 1: đích hàng 8
GOAL_MASK = (ROW[0], ROW[8])
START_MASKS = (ALL ^ ROW[0], ALL ^ ROW[8], ALL ^ COL[0], ALL ^ COL[8])
H, V = 100, 200

KEEP_UP, KEEP_DN, KEEP_LF, KEEP_RT = [0] * 64, [0] * 64, [0] * 64, [0] * 64
H_CONFLICT, V_CONFLICT = [0] * 64, [0] * 64
for _r in range(8):
    for _c in range(8):
        _a = _r * 8 + _c
        # Tường ngang tại (r, c) chặn cạnh giữa hàng r và r+1 ở cột c, c+1.
        KEEP_DN[_a] = ALL ^ (_bit(_r, _c) | _bit(_r, _c + 1))
        KEEP_UP[_a] = ALL ^ (_bit(_r + 1, _c) | _bit(_r + 1, _c + 1))
        # Tường dọc tại (r, c) chặn cạnh giữa cột c và c+1 ở hàng r, r+1.
        KEEP_RT[_a] = ALL ^ (_bit(_r, _c) | _bit(_r + 1, _c))
        KEEP_LF[_a] = ALL ^ (_bit(_r, _c + 1) | _bit(_r + 1, _c + 1))
        # Tường cùng hướng lệch 1 ô thì chồng lấn.
        H_CONFLICT[_a] = sum(1 << (_r * 8 + cc) for cc in (_c - 1, _c, _c + 1) if 0 <= cc < 8)
        V_CONFLICT[_a] = sum(1 << (rr * 8 + _c) for rr in (_r - 1, _r, _r + 1) if 0 <= rr < 8)


def _anchors(pairs, base):
    return tuple(base + r * 8 + c for r, c in pairs if 0 <= r < 8 and 0 <= c < 8)


# Các tường chặn bước đi từ ô p theo từng hướng (dùng khi DFS lần đường).
EDGE_UP, EDGE_DN, EDGE_LF, EDGE_RT = [], [], [], []
for _p in range(81):
    _r, _c = divmod(_p, 9)
    EDGE_UP.append(_anchors(((_r - 1, _c - 1), (_r - 1, _c)), H))
    EDGE_DN.append(_anchors(((_r, _c - 1), (_r, _c)), H))
    EDGE_LF.append(_anchors(((_r - 1, _c - 1), (_r, _c - 1)), V))
    EDGE_RT.append(_anchors(((_r - 1, _c), (_r, _c)), V))


class SearchTimeout(Exception):
    """Hết giờ: bỏ dở độ sâu đang tìm, dùng kết quả tốt nhất đã chắc chắn."""


# ==========================================
# 3. BỘ NHỚ GIỮ QUA CÁC LƯỢT ("CƠ SỞ DỮ LIỆU")
# ==========================================
_tt = {}            # thế cờ -> (độ sâu, điểm, loại điểm, nước tốt nhất)
_dist_cache = {}    # (tường ngang, tường dọc, gid) -> các lớp BFS
_history = {}       # số lần mỗi thế cờ đã xuất hiện sau nước của mình
_killers = [[None, None] for _ in range(MAX_DEPTH + 8)]  # nước gây cắt tỉa theo ply
_on_path = set()    # các thế cờ trên nhánh đang xét (phát hiện đi vòng)
_wall_value = [WALL_VALUE, WALL_VALUE]  # giá trị một tường trong tay của bên có đích gid 0 / gid 1
_last_wall_value = [0.0, 0.0]
_deadline = 0.0
_nodes = 0
_last_turn = -1
_prediction = None  # (thế cờ sau nước của mình, nước địch dự đoán)
last_info = {}      # thông tin lượt vừa rồi (để theo dõi, không ảnh hưởng quyết định)


# ==========================================
# 4. BFS + DFS: ĐƯỜNG ĐI NGẮN NHẤT
# ==========================================
def bfs_layers(gid, hw, vw, up, dn, lf, rt):
    """BFS ngược từ hàng đích (bỏ qua quân cờ, như Arena).

    layers[d] là tập bit các ô cách đích đúng d bước. Mỗi lớp loang ra 4 hướng chỉ
    bằng vài phép dịch bit. Kết quả được lưu theo cách đặt tường để dùng lại.
    """
    key = (hw, vw, gid)
    layers = _dist_cache.get(key)
    if layers is not None:
        return layers
    frontier = seen = GOAL_MASK[gid]
    out = []
    while frontier:
        out.append(frontier)
        frontier = (((frontier & up) >> 9) | ((frontier & dn) << 9)
                    | ((frontier & lf) >> 1) | ((frontier & rt) << 1)) & ~seen
        seen |= frontier
    layers = tuple(out)
    _dist_cache[key] = layers
    return layers


def dist(layers, p):
    """Khoảng cách từ ô p tới đích theo các lớp BFS."""
    b = 1 << p
    for d, layer in enumerate(layers):
        if layer & b:
            return d
    return UNREACH


def path_walls(p, layers, gid, up, dn, lf, rt):
    """DFS: từ quân đi sâu dần về đích, mỗi bước sang một ô kề ở lớp BFS nhỏ hơn 1.

    Nhờ lớp BFS nên nhánh nào DFS chọn cũng tới đích, không phải quay lui. Trả về
    các tường cắt đường ngắn nhất đó: đặt tường ở đây mới làm đường dài thêm.
    """
    d = dist(layers, p)
    if d >= UNREACH:
        return []
    walls = []
    seen = set()
    forward_up = gid == 0
    while d > 0:
        d -= 1
        layer = layers[d]
        # Ưu tiên bước thẳng về đích, rồi sang ngang, cuối cùng là lùi.
        if forward_up and (up >> p) & 1 and (layer >> (p - 9)) & 1:
            edge, p = EDGE_UP[p], p - 9
        elif not forward_up and (dn >> p) & 1 and (layer >> (p + 9)) & 1:
            edge, p = EDGE_DN[p], p + 9
        elif (lf >> p) & 1 and (layer >> (p - 1)) & 1:
            edge, p = EDGE_LF[p], p - 1
        elif (rt >> p) & 1 and (layer >> (p + 1)) & 1:
            edge, p = EDGE_RT[p], p + 1
        elif (up >> p) & 1 and (layer >> (p - 9)) & 1:
            edge, p = EDGE_UP[p], p - 9
        else:
            edge, p = EDGE_DN[p], p + 9
        for code in edge:
            if code not in seen:
                seen.add(code)
                walls.append(code)
    return walls


# ==========================================
# 5. LUẬT CHƠI
# ==========================================
def pawn_moves(pm, po, up, dn, lf, rt):
    """Các ô quân ở pm đi tới được: 1 bước, nhảy qua địch, hoặc đi chéo khi phía sau bị chặn."""
    masks = (up, dn, lf, rt)
    moves = []
    for i, step in ((0, -9), (1, 9), (2, -1), (3, 1)):
        if not (masks[i] >> pm) & 1:
            continue
        n = pm + step
        if n != po:
            moves.append(n)
        elif (masks[i] >> po) & 1:
            moves.append(po + step)          # nhảy thẳng qua đầu địch
        else:                                # phía sau bị chặn: đi chéo vòng qua
            for j, side in (((2, -1), (3, 1)) if i < 2 else ((0, -9), (1, 9))):
                if (masks[j] >> po) & 1:
                    moves.append(po + side)
    return moves


def place_wall(code, hw, vw, up, dn, lf, rt):
    """Trạng thái tường mới, hoặc None nếu tường chồng lấn/cắt nhau."""
    if code < V:
        a = code - H
        if hw & H_CONFLICT[a] or (vw >> a) & 1:
            return None
        return hw | (1 << a), vw, up & KEEP_UP[a], dn & KEEP_DN[a], lf, rt
    a = code - V
    if vw & V_CONFLICT[a] or (hw >> a) & 1:
        return None
    return hw, vw | (1 << a), up, dn, lf & KEEP_LF[a], rt & KEEP_RT[a]


def vulnerability(p, gid, hw, vw, up, dn, lf, rt):
    """Số bước tối đa địch có thể bắt quân ở p đi thêm chỉ bằng một tường."""
    layers = bfs_layers(gid, hw, vw, up, dn, lf, rt)
    d = dist(layers, p)
    worst = 0
    for code in path_walls(p, layers, gid, up, dn, lf, rt):
        state = place_wall(code, hw, vw, up, dn, lf, rt)
        if state is None:
            continue
        nd = dist(bfs_layers(gid, *state), p)
        if nd < UNREACH and nd - d > worst:
            worst = nd - d
    return worst


# ==========================================
# 6. HÀM ĐÁNH GIÁ THẾ CỜ
# ==========================================
def evaluate(dm, do, wm, wo, gm):
    """Điểm thế cờ từ góc nhìn bên tới lượt (m = mình, o = đối thủ), đơn vị bước.

    - Hiệu số đường đi: địch còn xa hơn mình bao nhiêu bước.
    - Tường trong tay: đáng đúng số bước một tường đang có thể bắt đối thủ đi thêm
      (tính lại mỗi lượt): đối thủ gần đích, đường rộng thì tường rẻ; đường hẹp thì đắt.
    - Lượt đi: bên tới lượt được đi trước TEMPO bước.
    - Độc quyền tường: khi một bên hết tường, bên kia rào mà không bị đáp trả,
      mỗi tường đáng thêm MONO_BONUS.
    - Thế chắc thắng/thua: đối thủ hết tường và chậm hơn hẳn thì cuộc đua đã ngã ngũ.
    """
    if dm <= 1:
        return SURE + 100.0                 # về đích ngay ở nước này
    if wo == 0 and dm < do:
        return SURE + (do - dm)
    if wm == 0 and do < dm - 1:
        return -SURE - (dm - 1 - do)
    score = (do - dm) + _wall_value[gm] * wm - _wall_value[gm ^ 1] * wo + TEMPO
    if wo == 0:
        score += MONO_BONUS * wm
    elif wm == 0:
        score -= MONO_BONUS * wo
    return score


def leaf_score(dm, do, wm, wo, gm, ply):
    """Như evaluate, nhưng thế "chắc thắng" càng xa càng kém, để bot về đích sớm nhất."""
    score = evaluate(dm, do, wm, wo, gm)
    if score > SURE_BOUND:
        return score - ply
    if score < -SURE_BOUND:
        return score + ply
    return score


def to_tt(score, ply):
    """Điểm thắng/thua phụ thuộc ply -> đổi về tương đối theo nút trước khi lưu."""
    if score > SURE_BOUND:
        return score + ply
    if score < -SURE_BOUND:
        return score - ply
    return score


def from_tt(score, ply):
    if score > SURE_BOUND:
        return score - ply
    if score < -SURE_BOUND:
        return score + ply
    return score


# ==========================================
# 7. MINIMAX (NEGAMAX) + ALPHA-BETA
# ==========================================
def gen_children(pm, po, gm, wm, hw, vw, up, dn, lf, rt, lm, lo, dm, do, wall_limit):
    """Nước con: (điểm sắp xếp, mã nước, trạng thái tường mới hoặc None,
    đường mình sau nước đi, đường địch sau nước đi)."""
    go = gm ^ 1
    children = []
    for n in pawn_moves(pm, po, up, dn, lf, rt):
        nd = dist(lm, n)
        children.append((dm - nd, n, None, nd, do))
    if wm > 0:
        mine = set(path_walls(pm, lm, gm, up, dn, lf, rt))
        walls = []
        for code in path_walls(po, lo, go, up, dn, lf, rt):
            state = place_wall(code, hw, vw, up, dn, lf, rt)
            if state is None:
                continue
            new_o = dist(bfs_layers(go, *state), po)
            if new_o >= UNREACH:
                continue                     # chặn kín đường địch: không hợp lệ
            if code in mine:
                new_m = dist(bfs_layers(gm, *state), pm)
                if new_m >= UNREACH:
                    continue
            else:
                new_m = dm                   # không chạm đường mình: đường mình giữ nguyên
            gain = (new_o - do) - (new_m - dm)
            if gain >= 1:
                walls.append((gain - WALL_VALUE, code, state, new_m, new_o))
        walls.sort(key=lambda w: -w[0])
        children.extend(walls[:wall_limit])
    children.sort(key=lambda ch: -ch[0])
    return children


def negamax(depth, alpha, beta, pm, po, gm, wm, wo, hw, vw, up, dn, lf, rt, ply):
    """Điểm tốt nhất bên tới lượt (đang ở pm, đích gm) đạt được, giả định địch cũng chơi tốt nhất."""
    global _nodes
    _nodes += 1
    if time.perf_counter() > _deadline:
        raise SearchTimeout()
    go = gm ^ 1
    lm = bfs_layers(gm, hw, vw, up, dn, lf, rt)
    lo = bfs_layers(go, hw, vw, up, dn, lf, rt)
    dm, do = dist(lm, pm), dist(lo, po)
    if depth <= 0:
        return leaf_score(dm, do, wm, wo, gm, ply)

    # Tra cơ sở dữ liệu: điểm alpha-beta có thể chỉ là chặn dưới/trên, nên lưu kèm loại điểm.
    key = (gm, pm, po, hw, vw, wm, wo)
    if key in _on_path:
        # Quay lại một thế đã có trên nhánh này: đi vòng không chứng minh được gì,
        # chấm như lá thay vì tin một "thắng" lưu trong bộ nhớ.
        return leaf_score(dm, do, wm, wo, gm, ply)
    entry = _tt.get(key)
    tt_move = None
    if entry is not None:
        e_depth, e_score, e_flag, tt_move = entry
        if e_depth >= depth:
            s = from_tt(e_score, ply)
            if e_flag == EXACT or (e_flag == LOWER and s >= beta) or (e_flag == UPPER and s <= alpha):
                return s

    children = gen_children(pm, po, gm, wm, hw, vw, up, dn, lf, rt, lm, lo, dm, do, MAX_WALLS)
    if not children:                         # bị kẹt: đứng yên nhường lượt
        return -negamax(depth - 1, -beta, -alpha, po, pm, go, wo, wm, hw, vw, up, dn, lf, rt, ply + 1)

    # Sắp xếp: nước tốt nhất đã lưu, rồi nước gây cắt tỉa gần đây (killer), rồi theo điểm sắp xếp.
    killers = _killers[ply]
    front = [c for c in children if c[1] == tt_move] + [c for c in children if c[1] in killers and c[1] != tt_move]
    if front:
        rest = [c for c in children if c[1] != tt_move and c[1] not in killers]
        children = front + rest

    alpha_orig = alpha
    best, best_code = NEG_INF, None
    goal_row = GOAL_ROW[gm]
    _on_path.add(key)
    for i, (_, code, state, nd_me, nd_op) in enumerate(children):
        new_wm = wm if state is None else wm - 1
        if state is None and code // 9 == goal_row:
            score = WIN - (ply + 1)          # đi tới đích: thắng ngay
        elif depth == 1:
            # Lá: khoảng cách sau nước đi đã biết, chấm luôn từ góc nhìn địch (khỏi gọi đệ quy)
            score = -leaf_score(nd_op, nd_me, wo, new_wm, go, ply + 1)
        else:
            if state is None:
                args = (po, code, go, wo, wm, hw, vw, up, dn, lf, rt, ply + 1)
            else:
                args = (po, pm, go, wo, new_wm) + state + (ply + 1,)
            if i == 0:
                score = -negamax(depth - 1, -beta, -alpha, *args)
            else:
                # PVS: thử cửa sổ rỗng (nông hơn 1 lớp với nước xếp cuối), chỉ tìm lại khi có hy vọng
                reduce = 1 if i >= LMR_FROM and depth >= 3 else 0
                score = -negamax(depth - 1 - reduce, -alpha - EPS, -alpha, *args)
                if score > alpha and (reduce or score < beta):
                    score = -negamax(depth - 1, -beta, -alpha, *args)
        if score > best:
            best, best_code = score, code
        if score > alpha:
            alpha = score
        if alpha >= beta:                    # cắt tỉa alpha-beta
            if code != killers[0]:
                killers[1], killers[0] = killers[0], code
            break
    _on_path.discard(key)

    flag =UPPER if best <= alpha_orig else LOWER if best >= beta else EXACT
    _tt[key] = (depth, to_tt(best, ply), flag, best_code)
    return best


# ==========================================
# 8. NÚT GỐC: CHỌN NƯỚC ĐI
# ==========================================
def wall_code(w):
    return (H if w["orientation"] == "H" else V) + w["row"] * 8 + w["col"]


def to_action(code):
    if code < H:
        return {"type": "move", "to": [code // 9, code % 9]}
    base = H if code < V else V
    a = code - base
    return {"type": "wall", "orientation": "H" if base == H else "V", "row": a // 8, "col": a % 8}


def defensive_walls(pm, po, gm, hw, vw, up, dn, lf, rt, dm, legal, taken, stop):
    """Tường "trung tính": không làm dài đường mình nhưng bịt trước chỗ địch có thể rào."""
    v0 = vulnerability(pm, gm, hw, vw, up, dn, lf, rt)
    if v0 < DEFENSE_MIN:
        return []
    go = gm ^ 1
    found = []
    for code in legal:
        if code in taken or time.perf_counter() > stop:
            continue
        state = place_wall(code, hw, vw, up, dn, lf, rt)
        if state is None or dist(bfs_layers(gm, *state), pm) != dm:
            continue
        if dist(bfs_layers(go, *state), po) >= UNREACH:
            continue
        v1 = vulnerability(pm, gm, *state)
        if v1 <= v0 - DEFENSE_MIN:
            found.append((v0 - v1, code, state))
    found.sort(key=lambda f: (-f[0], f[1]))
    return found[:MAX_DEFENSE]


def prune_database(gm, wm, wo, hw, vw):
    """Xóa các thế cờ không bao giờ xảy ra nữa: thiếu một tường đã đặt, hoặc một bên
    còn nhiều tường trong tay hơn thực tế. Phần còn lại là các nhánh khớp với nước
    địch vừa đi, được giữ để tìm tiếp mà không phải tính lại."""
    if len(_tt) > TT_LIMIT:
        _tt.clear()
    for key in list(_tt):
        kg, _, _, khw, kvw, kw1, kw2 = key
        mine, theirs = (kw1, kw2) if kg == gm else (kw2, kw1)
        if khw & hw != hw or kvw & vw != vw or mine > wm or theirs > wo:
            del _tt[key]
    if len(_dist_cache) > DIST_LIMIT:
        _dist_cache.clear()
    for key in list(_dist_cache):
        if key[0] & hw != hw or key[1] & vw != vw:
            del _dist_cache[key]


def decide(observation):
    global _deadline, _nodes, _last_turn, _prediction
    start = time.perf_counter()
    _deadline = start + TIME_BUDGET - SAFETY
    _nodes = 0

    you, opp = observation["you"], observation["opponent"]
    gm = 0 if you["goal_row"] == 0 else 1
    pm = you["position"][0] * 9 + you["position"][1]
    po = opp["position"][0] * 9 + opp["position"][1]
    wm, wo = you["walls_remaining"], opp["walls_remaining"]
    hw = vw = 0
    up, dn, lf, rt = START_MASKS
    for r, c in observation["walls"]["horizontal"]:
        a = r * 8 + c
        hw |= 1 << a
        up &= KEEP_UP[a]
        dn &= KEEP_DN[a]
    for r, c in observation["walls"]["vertical"]:
        a = r * 8 + c
        vw |= 1 << a
        lf &= KEEP_LF[a]
        rt &= KEEP_RT[a]

    turn = observation.get("turn", 0)
    if turn < _last_turn:                    # ván mới: xóa toàn bộ bộ nhớ
        _tt.clear()
        _dist_cache.clear()
        _history.clear()
        _prediction = None
    _last_turn = turn

    # Địch có đi đúng nước đã dự đoán không? (đúng thì cơ sở dữ liệu dùng tiếp được nhiều)
    if _prediction is not None:
        hw_after, vw_after, expected = _prediction   # tường ngay sau nước của mình, nước địch dự đoán
        if hw != hw_after:
            actual = H + (hw ^ hw_after).bit_length() - 1
        elif vw != vw_after:
            actual = V + (vw ^ vw_after).bit_length() - 1
        else:
            actual = po
        last_info["predicted_hit"] = None if expected is None else actual == expected
    prune_database(gm, wm, wo, hw, vw)
    db_size = len(_tt)

    go = gm ^ 1
    lm = bfs_layers(gm, hw, vw, up, dn, lf, rt)
    lo = bfs_layers(go, hw, vw, up, dn, lf, rt)
    dm, do = dist(lm, pm), dist(lo, po)

    # Giá trị một tường trong tay = cỡ số bước một tường đang có thể gây cho đối thủ.
    for side, victim, gv in ((gm, po, go), (go, pm, gm)):
        damage = vulnerability(victim, gv, hw, vw, up, dn, lf, rt)
        value = min(WALL_VALUE_MAX, max(WALL_VALUE_MIN, WALL_DYN_BASE + WALL_DYN_SLOPE * damage))
        _wall_value[side] = round(value * 20) / 20
    if max(abs(a - b) for a, b in zip(_wall_value, _last_wall_value)) >= 0.25:
        # Giá tường đổi nhiều: điểm cũ trong cơ sở dữ liệu không còn đúng. Đổi ít thì giữ lại.
        _tt.clear()
        _last_wall_value[:] = _wall_value

    # --- Các nước ở gốc: mọi nước đi quân hợp lệ + mọi tường cắt đường địch + tường phòng thủ
    legal_moves = [r * 9 + c for r, c in observation["legal_moves"]]
    legal_walls = [wall_code(w) for w in observation["legal_walls"]] if wm > 0 else []
    legal_wall_set = set(legal_walls)
    for code in legal_moves:
        if code // 9 == GOAL_ROW[gm]:
            return to_action(code), {"depth": 0, "reason": "về đích"}

    # Càng gần giới hạn lượt, mỗi bước tiến thật về đích càng đáng giá (hòa chỉ được 1 điểm).
    fraction = turn / float(observation.get("max_turns") or 500)
    urgency = URGENCY_BONUS * max(0.0, (fraction - URGENCY_START) / (1.0 - URGENCY_START))

    root = []           # [mã, trạng thái tường hoặc None, phạt, điểm sắp xếp, cách biệt cuộc đua]
    for code in legal_moves:
        nd = dist(lm, code)
        penalty = BACK_PENALTY * max(0, nd - dm) - urgency * (dm - nd)
        penalty += REPEAT_PENALTY * _history.get((code, po, hw, vw), 0)
        root.append([code, None, penalty, dm - nd, do - nd])
    if wm > 0:
        mine = set(path_walls(pm, lm, gm, up, dn, lf, rt))
        taken = set()
        for code in path_walls(po, lo, go, up, dn, lf, rt):
            if code not in legal_wall_set:
                continue
            state = place_wall(code, hw, vw, up, dn, lf, rt)
            new_o = dist(bfs_layers(go, *state), po)
            new_m = dist(bfs_layers(gm, *state), pm) if code in mine else dm
            net = (new_o - do) - (new_m - dm)
            if net < 1:
                continue
            penalty = 0.0
            if do > 3 and net <= 1:
                penalty += WEAK_WALL_PENALTY[1]
            if turn < 6 and net < 3:
                penalty += OPENING_WALL_PENALTY
            root.append([code, state, penalty, net - WALL_VALUE, new_o - new_m])
            taken.add(code)
        stop = min(start + DEFENSE_TIME, _deadline)
        for _, code, state in defensive_walls(pm, po, gm, hw, vw, up, dn, lf, rt, dm, legal_walls, taken, stop):
            penalty = WEAK_WALL_PENALTY[0] if do > 3 else 0.0
            root.append([code, state, penalty, -WALL_VALUE, do - dm])
    if not root:
        return {"type": "pass"}, {"depth": 0, "reason": "không còn nước"}

    root.sort(key=lambda r: -r[3])
    best_code = root[0][0]                   # dự phòng: nước tiến gần đích nhất
    best_score = None
    done_depth = 0
    for depth in range(1, MAX_DEPTH + 1):
        if time.perf_counter() - start > TIME_BUDGET * NEXT_DEPTH_FRAC:
            break
        iter_best, iter_adj = None, NEG_INF
        scores = {}
        _on_path.clear()
        _on_path.add((gm, pm, po, hw, vw, wm, wo))
        try:
            for i, (code, state, penalty, _, _) in enumerate(root):
                if state is None:
                    args = (po, code, go, wo, wm, hw, vw, up, dn, lf, rt, 1)
                else:
                    args = (po, pm, go, wo, wm - 1) + state + (1,)
                if i == 0:
                    raw = -negamax(depth - 1, NEG_INF, POS_INF, *args)
                else:
                    # Chỉ cần biết nước này có vượt nước tốt nhất (sau khi trừ phạt) hay không
                    target = iter_adj + penalty
                    raw = -negamax(depth - 1, -target - EPS, -target, *args)
                    if raw > target:
                        raw = -negamax(depth - 1, NEG_INF, -target, *args)
                adj = raw - penalty
                scores[code] = adj
                if adj > iter_adj:
                    iter_best, iter_adj = code, adj
        except SearchTimeout:
            # Độ sâu dở dang: nước nào đã tìm xong và hơn nước tốt nhất cũ thì vẫn dùng được
            if iter_best is not None and scores.get(root[0][0]) is not None and iter_best != root[0][0]:
                best_code, best_score = iter_best, iter_adj
            break
        best_code, best_score, done_depth = iter_best, iter_adj, depth
        root.sort(key=lambda r: -scores.get(r[0], NEG_INF))
        if abs(iter_adj) > WIN_BOUND:        # đã thấy thắng/thua chắc chắn
            break
    if best_score is not None and best_score < -WIN_BOUND:
        # Mọi nhánh đều thua nếu địch chơi hoàn hảo. Địch thật không hoàn hảo, nên vẫn
        # chống cự: chọn nước giữ cách biệt cuộc đua tốt nhất (thường là tường làm địch chậm nhất).
        best_code = max(root, key=lambda r: r[4])[0]

    # Ghi lịch sử thế cờ sau nước của mình + dự đoán nước địch (lấy từ cơ sở dữ liệu)
    if best_code < H:
        after = (best_code, po, hw, vw)
        _history[after] = _history.get(after, 0) + 1
        child_key = (go, po, best_code, hw, vw, wo, wm)
        nhw, nvw = hw, vw
    else:
        nhw, nvw = place_wall(best_code, hw, vw, up, dn, lf, rt)[:2]
        child_key = (go, po, pm, nhw, nvw, wo, wm - 1)
    child = _tt.get(child_key)
    _prediction = (nhw, nvw, child[3] if child else None)

    info = {"depth": done_depth, "score": best_score, "nodes": _nodes, "db_kept": db_size,
            "db_size": len(_tt), "ms": round(1000 * (time.perf_counter() - start))}
    return to_action(best_code), info


def choose_action(observation):
    try:
        action, info = decide(observation)
        last_info.update(info)
        return action
    except Exception:
        # Lỗi bất ngờ: không để mất lượt, đi nước gần đích nhất theo distances của Arena
        legal_moves = observation.get("legal_moves") or []
        if not legal_moves:
            return {"type": "pass"}
        distances = observation["distances"]
        best = min(legal_moves, key=lambda m: distances[m[0]][m[1]] if distances[m[0]][m[1]] >= 0 else 999)
        return {"type": "move", "to": list(best)}
