"""Agent Wrongway: tìm kiếm alpha-beta trên mô hình bàn cờ dạng bitboard.

Ý tưởng chính
-------------
1. Biểu diễn bàn cờ bằng số nguyên Python dùng như tập bit:
   - Ô (r, c) ứng với bit r*9 + c (81 bit).
   - Bốn mặt nạ up/dn/lf/rt: bit bật nếu từ ô đó đi được lên/xuống/trái/phải
     (đã trừ biên bàn cờ và các cạnh bị tường chặn).
   - Tường ngang/dọc lưu bằng bit tại vị trí neo r*8 + c (64 bit).
   Nhờ vậy BFS chỉ tốn vài phép dịch bit cho mỗi "lớp" khoảng cách.
2. Hàm đánh giá = (đường đi của đối thủ - đường đi của mình)
                  + giá trị số tường còn trong tay + lợi thế được đi trước.
   Khi một bên đã hết tường, cuộc đua gần như đã ngã ngũ nên được chấm riêng.
3. Negamax + cắt tỉa alpha-beta + bảng chuyển vị, đào sâu dần (iterative
   deepening) cho tới khi hết ngân sách thời gian (thấp hơn nhiều so với 1 giây).
4. Chỉ xét những tường cắt đường đi ngắn nhất hiện tại của đối thủ: tường không
   chạm vào đường đó thì chắc chắn không làm đối thủ phải đi xa hơn.

Chỉ dùng thư viện chuẩn, chạy độc lập trong một file.
"""

import gc
import time
from array import array

# Bộ đệm có hàng trăm nghìn đối tượng; một lần dọn rác thế hệ 2 có thể tốn
# hàng trăm mili giây và làm quá giờ. Mã này không tạo tham chiếu vòng nên
# tắt bộ dọn rác vòng là an toàn (bộ đếm tham chiếu vẫn giải phóng bộ nhớ).
gc.disable()


# ----------------------------------------------------------------- tham số
NEXT_ITER_FRAC = 0.8   # chỉ mở vòng đào sâu mới khi chưa dùng quá phần này của ngân sách
TIME_BUDGET = 0.8      # giây suy nghĩ tối đa cho một nước (giới hạn là 1 giây)
MAX_DEPTH = 30         # độ sâu tối đa của iterative deepening
WALL_WEIGHT = 1.5      # giá trị (tính theo số bước) của một tường còn trong tay
WALL_VALUE = [WALL_WEIGHT * w for w in range(11)]  # giá trị của w tường trong tay
TEMPO = 0.5            # lợi thế của bên đang được đi
MIN_WALL_GAIN = 1      # chỉ xét tường làm lợi ít nhất bấy nhiêu bước
LMR_FROM = 2           # từ nước con thứ bấy nhiêu (đếm từ 0) thử nông hơn 1 lớp trước
NULL_EPS = 0.01        # độ rộng cửa sổ rỗng của PVS (điểm là bội của 0,25)
DEFENSE_MIN = 2        # chỉ phòng thủ khi đối thủ có thể làm mình dài thêm >= bấy nhiêu bước bằng 1 tường
MAX_DEFENSE = 4        # số tường phòng thủ tốt nhất đưa vào gốc
DEFENSE_TIME = 0.15    # giây tối đa dành cho việc tìm tường phòng thủ
MAX_WALLS = 4          # số tường tốt nhất được xét ở mỗi nút trong cây tìm kiếm
REPEAT_PENALTY = 0.75  # phạt nước đi dẫn về thế cờ đã lặp lại (tránh hòa)
BACK_PENALTY = 0.75    # phạt mỗi bước nước đi quân làm đường của mình dài thêm (né/lùi)
VISIT_PENALTY = 0.5    # phạt nước đi quân quay lại ô mình đã đứng trong ván này
WEAK_WALL_PENALTY = (0.25, 0.12)  # phạt tường lợi ròng <=0 / đúng 1 bước khi đối thủ chưa gấp
OPENING_WALL_PENALTY = 0.15  # khai cuộc: giữ tường, chỉ đặt khi lợi ròng >= 3
WIN_REPEAT_PLIES = 20  # khi đang thắng, mỗi lần lặp thế coi như thắng chậm thêm bấy nhiêu nửa-nước
DIST_CACHE_LIMIT = 20000   # giới hạn số phần tử bộ đệm để giữ bộ nhớ vừa phải
TT_LIMIT = 50000
W1_TIME = 0.45        # giây tối đa mỗi lượt để dựng bảng 1-vs-0 tường (tính tiếp ở lượt sau)
PROOF_TIME = 0.3      # giây tối đa để chứng minh thắng chắc bằng đua chính xác khi đối thủ hết tường
CHILD_LIMIT = 40000  # giới hạn bộ đệm nước con / tường ứng viên
NEAR_END = 64      # khi còn ít lượt hơn bấy nhiêu: tính giới hạn lượt, bỏ bảng chuyển vị

WIN = 100000.0         # điểm của thế thắng thật sự
SURE = 1000.0          # điểm của thế "chắc thắng" (đối thủ hết tường, mình nhanh hơn)
WIN_BOUND = WIN - 1000.0  # điểm vượt ngưỡng này là thắng/thua đã chứng minh
SURE_BOUND = SURE / 2     # điểm vượt ngưỡng này là "chắc thắng/thua", đã trừ theo ply
UNREACH = 99           # khoảng cách của ô không tới được đích
NEG_INF = float("-inf")
POS_INF = float("inf")


# ------------------------------------------------------- bảng tính sẵn
def _bit(row, col):
    return 1 << (row * 9 + col)


ALL_CELLS = (1 << 81) - 1
ROW_MASK = [sum(_bit(r, c) for c in range(9)) for r in range(9)]
COL_MASK = [sum(_bit(r, c) for r in range(9)) for c in range(9)]
# gid 0: đích là hàng 0 (người chơi 0); gid 1: đích là hàng 8 (người chơi 1).
GOAL_MASK = (ROW_MASK[0], ROW_MASK[8])
BASE_UP = ALL_CELLS ^ ROW_MASK[0]
BASE_DN = ALL_CELLS ^ ROW_MASK[8]
BASE_LF = ALL_CELLS ^ COL_MASK[0]
BASE_RT = ALL_CELLS ^ COL_MASK[8]

# Mã nước đi: 0..80 là ô đích của quân; 100 + a là tường ngang tại neo a;
# 200 + a là tường dọc tại neo a, với a = row * 8 + col.
H_BASE = 100
V_BASE = 200

H_KEEP_UP = [0] * 64   # mặt nạ "giữ lại" sau khi đặt tường ngang (đã đảo bit)
H_KEEP_DN = [0] * 64
V_KEEP_LF = [0] * 64
V_KEEP_RT = [0] * 64
H_SAME = [0] * 64      # các neo tường ngang chồng lấn với tường ngang tại a
V_SAME = [0] * 64
for _r in range(8):
    for _c in range(8):
        _a = _r * 8 + _c
        H_KEEP_DN[_a] = ALL_CELLS ^ (_bit(_r, _c) | _bit(_r, _c + 1))
        H_KEEP_UP[_a] = ALL_CELLS ^ (_bit(_r + 1, _c) | _bit(_r + 1, _c + 1))
        V_KEEP_RT[_a] = ALL_CELLS ^ (_bit(_r, _c) | _bit(_r + 1, _c))
        V_KEEP_LF[_a] = ALL_CELLS ^ (_bit(_r, _c + 1) | _bit(_r + 1, _c + 1))
        H_SAME[_a] = sum(1 << (_r * 8 + cc) for cc in (_c - 1, _c, _c + 1) if 0 <= cc < 8)
        V_SAME[_a] = sum(1 << (rr * 8 + _c) for rr in (_r - 1, _r, _r + 1) if 0 <= rr < 8)


def _anchors(pairs, base):
    return tuple(base + r * 8 + c for r, c in pairs if 0 <= r < 8 and 0 <= c < 8)


# Các tường chặn cạnh đi từ ô p theo từng hướng.
EDGE_UP = []
EDGE_DN = []
EDGE_LF = []
EDGE_RT = []
for _p in range(81):
    _r, _c = divmod(_p, 9)
    EDGE_UP.append(_anchors(((_r - 1, _c - 1), (_r - 1, _c)), H_BASE))
    EDGE_DN.append(_anchors(((_r, _c - 1), (_r, _c)), H_BASE))
    EDGE_LF.append(_anchors(((_r - 1, _c - 1), (_r, _c - 1)), V_BASE))
    EDGE_RT.append(_anchors(((_r - 1, _c), (_r, _c)), V_BASE))


# ------------------------------------------------- trạng thái toàn cục
class _Timeout(Exception):
    pass


_dist_cache = {}   # (tường ngang, tường dọc, gid) -> các lớp BFS
_path_cache = {}   # (ô, id các lớp BFS) -> tường cắt đường ngắn nhất (dọn cùng _dist_cache)
_opts_cache = {}   # (pm, po, gm, tường) -> mọi tường ứng viên của _wall_options
_child_cache = {}  # (pm, po, còn tường?, gm, tường) -> nước con đã sắp xếp
_tt = {}           # bảng chuyển vị: thế cờ -> (độ sâu, điểm, cờ, nước tốt nhất)
_deadline = 0.0
_history = {}      # số lần mỗi thế cờ đã xuất hiện sau nước đi của mình
_visited = set()   # các ô quân mình đã đứng trong ván này
_last_turn = -1
_killers = [[-1, -1] for _ in range(MAX_DEPTH + 8)]  # nước gây cắt tỉa gần đây theo ply
_last_depth = 0    # độ sâu đã tìm xong ở nước gần nhất (để theo dõi)
_turns_left = 10 ** 9  # số lượt (của cả hai) còn lại trước khi ván hòa vì hết lượt


# ------------------------------------------------------------ luật chơi
def _dist_map(gid, hw, vw, up, dn, lf, rt):
    """BFS ngược từ hàng đích, bỏ qua quân cờ.

    Trả về danh sách lớp: layers[d] là tập bit các ô cách đích đúng d bước.
    """
    key = (hw, vw, gid)
    layers = _dist_cache.get(key)
    if layers is not None:
        return layers
    frontier = seen = GOAL_MASK[gid]
    layers = []
    while frontier:
        layers.append(frontier)
        # Cạnh mở theo cả hai chiều nên có thể loang xuôi từ hàng đích.
        frontier = (((frontier & up) >> 9) | ((frontier & dn) << 9)
                    | ((frontier & lf) >> 1) | ((frontier & rt) << 1)) & ~seen
        seen |= frontier
    layers = tuple(layers)
    _dist_cache[key] = layers
    return layers


def _dist(layers, p):
    """Khoảng cách từ ô p tới đích theo các lớp BFS."""
    b = 1 << p
    d = 0
    for layer in layers:
        if layer & b:
            return d
        d += 1
    return UNREACH


def _pawn_moves(pm, po, up, dn, lf, rt):
    """Các ô quân ở pm đi tới được, gồm cả nhảy qua và đi chéo vòng qua po."""
    moves = []
    if (up >> pm) & 1:
        n = pm - 9
        if n != po:
            moves.append(n)
        elif (up >> po) & 1:
            moves.append(po - 9)
        else:
            if (lf >> po) & 1:
                moves.append(po - 1)
            if (rt >> po) & 1:
                moves.append(po + 1)
    if (dn >> pm) & 1:
        n = pm + 9
        if n != po:
            moves.append(n)
        elif (dn >> po) & 1:
            moves.append(po + 9)
        else:
            if (lf >> po) & 1:
                moves.append(po - 1)
            if (rt >> po) & 1:
                moves.append(po + 1)
    if (lf >> pm) & 1:
        n = pm - 1
        if n != po:
            moves.append(n)
        elif (lf >> po) & 1:
            moves.append(po - 1)
        else:
            if (up >> po) & 1:
                moves.append(po - 9)
            if (dn >> po) & 1:
                moves.append(po + 9)
    if (rt >> pm) & 1:
        n = pm + 1
        if n != po:
            moves.append(n)
        elif (rt >> po) & 1:
            moves.append(po + 1)
        else:
            if (up >> po) & 1:
                moves.append(po - 9)
            if (dn >> po) & 1:
                moves.append(po + 9)
    return moves


def _place_wall(code, hw, vw, up, dn, lf, rt):
    """Trả về trạng thái tường mới, hoặc None nếu tường chồng lấn/cắt nhau."""
    if code < V_BASE:
        a = code - H_BASE
        if hw & H_SAME[a] or (vw >> a) & 1:
            return None
        return hw | (1 << a), vw, up & H_KEEP_UP[a], dn & H_KEEP_DN[a], lf, rt
    a = code - V_BASE
    if vw & V_SAME[a] or (hw >> a) & 1:
        return None
    return hw, vw | (1 << a), up, dn, lf & V_KEEP_LF[a], rt & V_KEEP_RT[a]


def _path_walls(p, layers, up, dn, lf, rt, gid):
    """Các tường cắt một đường đi ngắn nhất từ p tới đích gid.

    Cùng ô và cùng cấu hình tường thì kết quả như nhau (lớp BFS xác định cả tường
    lẫn đích), nên nhớ theo id của bộ lớp: bộ lớp sống trong _dist_cache tới khi
    cả hai bộ đệm cùng được dọn, id không bị dùng lại giữa chừng.
    """
    key = (p, id(layers))
    walls = _path_cache.get(key)
    if walls is None:
        walls = _path_cache[key] = _find_path_walls(p, layers, up, dn, lf, rt, gid)
    return walls


def _find_path_walls(p, layers, up, dn, lf, rt, gid):
    d = _dist(layers, p)
    if d >= UNREACH:
        return ()
    walls = []
    seen = set()
    forward_up = gid == 0
    while d > 0:
        d -= 1
        layer = layers[d]
        # Ưu tiên bước thẳng về đích, sau đó sang ngang, cuối cùng là lùi.
        if forward_up and (up >> p) & 1 and (layer >> (p - 9)) & 1:
            edge = EDGE_UP[p]
            p -= 9
        elif not forward_up and (dn >> p) & 1 and (layer >> (p + 9)) & 1:
            edge = EDGE_DN[p]
            p += 9
        elif (lf >> p) & 1 and (layer >> (p - 1)) & 1:
            edge = EDGE_LF[p]
            p -= 1
        elif (rt >> p) & 1 and (layer >> (p + 1)) & 1:
            edge = EDGE_RT[p]
            p += 1
        elif (up >> p) & 1 and (layer >> (p - 9)) & 1:
            edge = EDGE_UP[p]
            p -= 9
        else:
            edge = EDGE_DN[p]
            p += 9
        for code in edge:
            if code not in seen:
                seen.add(code)
                walls.append(code)
    return tuple(walls)


def _vulnerability(p, gid, hw, vw, up, dn, lf, rt):
    """Số bước tối đa đối thủ có thể bắt quân ở p đi thêm chỉ bằng một tường."""
    layers = _dist_map(gid, hw, vw, up, dn, lf, rt)
    d = _dist(layers, p)
    worst = 0
    for code in _path_walls(p, layers, up, dn, lf, rt, gid):
        state = _place_wall(code, hw, vw, up, dn, lf, rt)
        if state is None:
            continue
        nd = _dist(_dist_map(gid, *state), p)
        if nd < UNREACH and nd - d > worst:
            worst = nd - d
    return worst


def _defensive_walls(pm, po, gm, hw, vw, up, dn, lf, rt, dm, do, legal_walls, taken, stop):
    """Tường "trung tính" (không làm dài đường mình) bịt chỗ hở trên đường của mình.

    Đối thủ dựng mê cung bằng cách đặt tường vào chỗ hở; A trước đây chỉ biết tường
    tấn công nên không có cách nào chặn trước. Trả về tối đa MAX_DEFENSE tường giảm
    độ hở ít nhất DEFENSE_MIN bước, tốt nhất trước.
    """
    v0 = _vulnerability(pm, gm, hw, vw, up, dn, lf, rt)
    if v0 < DEFENSE_MIN:
        return []
    go = gm ^ 1
    found = []
    for code in legal_walls:
        if code in taken:
            continue
        if time.perf_counter() > stop:
            break
        state = _place_wall(code, hw, vw, up, dn, lf, rt)
        if state is None:
            continue
        if _dist(_dist_map(gm, *state), pm) != dm:
            continue
        new_o = _dist(_dist_map(go, *state), po)
        if new_o >= UNREACH:
            continue
        v1 = _vulnerability(pm, gm, *state)
        if v1 <= v0 - DEFENSE_MIN:
            found.append((v0 - v1, code, state, new_o))
    found.sort(key=lambda item: (-item[0], item[1]))
    return found[:MAX_DEFENSE]


# ------------------------------------------------------------- đánh giá
def _evaluate(dm, do, wm, wo):
    """Điểm theo góc nhìn bên đang tới lượt (m = mình, o = đối thủ).

    Hai quân có thể nhảy qua nhau một lần (lợi một bước), nên chỉ coi là "chắc"
    khi bên dẫn trước còn dư ít nhất một bước sau khi tính lượt đi.
    """
    if dm <= 1:
        return SURE + 100.0         # về đích ngay ở nước này
    if wo == 0 and dm < do:
        return SURE + (do - dm)     # đối thủ hết tường và chậm hơn hẳn: chắc thắng
    if wm == 0 and do < dm - 1:
        return -SURE - (dm - 1 - do)  # mình hết tường và chậm hơn hẳn: chắc thua
    return (do - dm) + WALL_VALUE[wm] - WALL_VALUE[wo] + TEMPO


def _children(pm, po, wm, gm, hw, vw, up, dn, lf, rt, moves, map_m, map_o, dm, do):
    """Sinh và sắp xếp nước con.

    Mỗi phần tử: (điểm sắp xếp, mã nước, trạng thái tường mới hoặc None,
    khoảng cách của mình sau nước đi, khoảng cách của đối thủ sau nước đi).
    Kết quả chỉ phụ thuộc vị trí hai quân, tường và việc còn tường hay không, nên
    được nhớ lại: đào sâu dần thăm lại cùng các nút ở mỗi vòng. Trả về list mới
    (bên gọi đổi thứ tự tại chỗ).
    """
    key = (pm, po, wm > 0, gm, hw, vw)
    cached = _child_cache.get(key)
    if cached is not None:
        return list(cached)
    children = []
    for m in moves:
        nd = _dist(map_m, m)
        children.append((dm - nd, m, None, nd, do))
    if wm > 0:
        walls = []
        for code, state, new_m, new_o in _wall_list(pm, po, gm, hw, vw, up, dn, lf, rt,
                                                    map_m, map_o, dm, do):
            gain = (new_o - do) - (new_m - dm)
            walls.append((gain - WALL_WEIGHT, code, state, new_m, new_o))
        if len(walls) > MAX_WALLS:
            walls.sort(key=lambda child: -child[0])
            del walls[MAX_WALLS:]
        children.extend(walls)
    children.sort(key=lambda child: -child[0])
    _child_cache[key] = tuple(children)
    return children


def _wall_list(pm, po, gm, hw, vw, up, dn, lf, rt, map_m, map_o, dm, do):
    """Như _wall_options nhưng trả về (và nhớ) toàn bộ danh sách."""
    key = (pm, po, gm, hw, vw)
    opts = _opts_cache.get(key)
    if opts is None:
        opts = _opts_cache[key] = tuple(_wall_options(pm, po, gm, hw, vw, up, dn, lf, rt,
                                                      map_m, map_o, dm, do))
    return opts


def _wall_options(pm, po, gm, hw, vw, up, dn, lf, rt, map_m, map_o, dm, do):
    """Duyệt các tường hợp lệ cắt đường của đối thủ và làm lợi đủ nhiều.

    Sinh ra (mã, trạng thái mới, khoảng cách mới của mình, của đối thủ).
    Tường không chạm đường ngắn nhất của mình thì khoảng cách của mình giữ
    nguyên, khỏi phải BFS lại.
    """
    go = gm ^ 1
    mine = set(_path_walls(pm, map_m, up, dn, lf, rt, gm))
    for code in _path_walls(po, map_o, up, dn, lf, rt, go):
        state = _place_wall(code, hw, vw, up, dn, lf, rt)
        if state is None:
            continue
        new_o = _dist(_dist_map(go, *state), po)
        if new_o >= UNREACH:
            continue                # tường chặn kín đường: không hợp lệ
        if code in mine:
            new_m = _dist(_dist_map(gm, *state), pm)
            if new_m >= UNREACH:
                continue
        else:
            new_m = dm
        if (new_o - do) - (new_m - dm) >= MIN_WALL_GAIN:
            yield code, state, new_m, new_o


def _leaf(dm, do, wm, wo, ply):
    """Như _evaluate nhưng điểm chắc thắng/thua giảm dần theo ply, để giữa
    nhiều nước cùng "chắc thắng" bot chọn nước về đích sớm nhất (không lùi)."""
    score = _evaluate(dm, do, wm, wo)
    if _turns_left < NEAR_END:
        # Nước cuối của bên đi ở ply + 2(dm-1), của đối thủ ở ply + 2do - 1; ván dừng
        # (hòa) khi ply chạm _turns_left. Ai không kịp về đích thì không thắng được.
        late_m = ply + 2 * dm - 2 >= _turns_left
        late_o = ply + 2 * do - 1 >= _turns_left
        if late_m and late_o or score > SURE_BOUND and late_m or score < -SURE_BOUND and late_o:
            return 0.0
    if score > SURE_BOUND:
        return score - ply
    if score < -SURE_BOUND:
        return score + ply
    return score


# ------------------------------------------------------------ tìm kiếm
def _search(depth, alpha, beta, pm, po, wm, wo, gm, hw, vw, up, dn, lf, rt, ply):
    """Negamax alpha-beta; trả về điểm theo góc nhìn bên đang tới lượt."""
    if time.perf_counter() > _deadline:
        raise _Timeout()
    if ply >= _turns_left:
        return 0.0                  # ván đã hòa vì hết lượt trước nước này

    moves = _pawn_moves(pm, po, up, dn, lf, rt)
    goal = GOAL_MASK[gm]
    for m in moves:
        if (goal >> m) & 1:
            return WIN - ply

    go = gm ^ 1
    map_m = _dist_map(gm, hw, vw, up, dn, lf, rt)
    map_o = _dist_map(go, hw, vw, up, dn, lf, rt)
    dm = _dist(map_m, pm)
    do = _dist(map_o, po)
    if depth <= 0 or (wo == 0 and dm < do) or (wm == 0 and do < dm - 1):
        return _leaf(dm, do, wm, wo, ply)

    key = (pm, po, wm, wo, gm, hw, vw)
    entry = _tt.get(key) if _turns_left >= NEAR_END else None
    tt_move = -1
    if entry is not None:
        e_depth, e_score, e_flag, tt_move = entry
        # Bảng lưu điểm thắng theo khoảng cách từ nút này; đổi về theo gốc hiện tại.
        if e_score > SURE_BOUND:
            e_score -= ply
        elif e_score < -SURE_BOUND:
            e_score += ply
        if e_depth >= depth:
            if e_flag == 0:
                return e_score
            if e_flag == 1 and e_score >= beta:
                return e_score
            if e_flag == 2 and e_score <= alpha:
                return e_score

    alpha0 = alpha
    best = NEG_INF
    best_code = -1
    if depth == 1:
        # Con là nút lá: đánh giá trực tiếp. Xét nước đi quân trước vì rẻ,
        # chỉ tính tới tường (cần BFS) khi chưa đủ để cắt tỉa.
        for m in moves:
            score = -_leaf(do, _dist(map_m, m), wo, wm, ply + 1)
            if score > best:
                best = score
                best_code = m
        if best < beta and wm > 0:
            # Danh sách đã có sẵn thì dùng lại; chưa có thì sinh dần (thường cắt sớm).
            opts = _opts_cache.get((pm, po, gm, hw, vw))
            if opts is None:
                opts = _wall_options(pm, po, gm, hw, vw, up, dn, lf, rt, map_m, map_o, dm, do)
            for code, _, new_m, new_o in opts:
                score = -_leaf(new_o, new_m, wo, wm - 1, ply + 1)
                if score > best:
                    best = score
                    best_code = code
                    if best >= beta:
                        break
        if best_code < 0:
            return _leaf(dm, do, wm, wo, ply)
    else:
        children = _children(pm, po, wm, gm, hw, vw, up, dn, lf, rt,
                             moves, map_m, map_o, dm, do)
        if not children:
            return _leaf(dm, do, wm, wo, ply)
        # Thứ tự: nước của bảng chuyển vị, rồi hai nước "killer" (từng gây cắt
        # tỉa ở cùng ply), rồi theo lợi ích trước mắt.
        killers = _killers[ply]
        front = 0
        for wanted in (tt_move, killers[0], killers[1]):
            if wanted < 0:
                continue
            for i in range(front, len(children)):
                if children[i][1] == wanted:
                    children.insert(front, children.pop(i))
                    front += 1
                    break
        for i, (_, code, state, _, _) in enumerate(children):
            if state is None:
                child = (po, code, wo, wm, go, hw, vw, up, dn, lf, rt, ply + 1)
            else:
                child = (po, pm, wo, wm - 1, go) + state + (ply + 1,)
            if i == 0:
                score = -_search(depth - 1, -beta, -alpha, *child)
            else:
                # PVS: các nước sau chỉ cần chứng minh không hơn nước tốt nhất
                # (cửa sổ rỗng); LMR: nước xếp cuối thử nông hơn 1 lớp trước.
                reduce = (1 if i >= LMR_FROM and depth >= 3 and i >= front
                          and -SURE_BOUND < alpha < SURE_BOUND else 0)
                score = -_search(depth - 1 - reduce, -alpha - NULL_EPS, -alpha, *child)
                if reduce and score > alpha:
                    score = -_search(depth - 1, -alpha - NULL_EPS, -alpha, *child)
                if alpha < score < beta:
                    score = -_search(depth - 1, -beta, -score, *child)
            if score > best:
                best = score
                best_code = code
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        if killers[0] != code:
                            killers[1] = killers[0]
                            killers[0] = code
                        break

    if best <= alpha0:
        flag = 2
    elif best >= beta:
        flag = 1
    else:
        flag = 0
    # Lưu điểm thắng tính từ nút này (không phụ thuộc ply), để khi dùng lại ở
    # lượt sau hay ở độ sâu khác, nước thắng nhanh hơn vẫn được ưu tiên đúng.
    stored = best
    if best > SURE_BOUND:
        stored = best + ply
    elif best < -SURE_BOUND:
        stored = best - ply
    if _turns_left >= NEAR_END:
        _tt[key] = (depth, stored, flag, best_code)
    return best


# ------------------------------------------------- cuộc đua khi hết tường
_race_cache = {}   # (tường ngang, tường dọc, gm) -> bảng giá trị cuộc đua


def _race_table(gm, hw, vw, up, dn, lf, rt):
    """Giải trọn cuộc đua khi CẢ HAI đã hết tường (chỉ còn đi quân và đứng yên).

    Trạng thái (a, b, s): a là ô của mình, b của đối thủ, s = 0 nếu mình đi.
    Giá trị theo góc nhìn bên đi: (1, n) thắng sau n nửa-nước, (-1, n) thua,
    không có trong bảng = hòa (hai bên nhường nhau mãi, hoặc một bên đứng chắn lối).
    Duyệt ngược từ các thế về đích được ngay, nên kết quả là chính xác.
    """
    key = (hw, vw, gm)
    table = _race_cache.get(key)
    if table is not None:
        return table
    goal_m, goal_o = GOAL_MASK[gm], GOAL_MASK[gm ^ 1]
    free_m = [p for p in range(81) if not (goal_m >> p) & 1]
    free_o = [p for p in range(81) if not (goal_o >> p) & 1]
    table = {}
    left = {}
    preds = {}
    queue = []
    for a in free_m:
        for b in free_o:
            if a == b:
                continue
            for s in (0, 1):
                if s == 0:
                    moves = _pawn_moves(a, b, up, dn, lf, rt)
                    if any((goal_m >> m) & 1 for m in moves):
                        table[(a, b, 0)] = (1, 1)
                        queue.append((a, b, 0))
                        continue
                    nexts = [(m, b, 1) for m in moves]
                    nexts.append((a, b, 1))
                else:
                    moves = _pawn_moves(b, a, up, dn, lf, rt)
                    if any((goal_o >> m) & 1 for m in moves):
                        table[(a, b, 1)] = (1, 1)
                        queue.append((a, b, 1))
                        continue
                    nexts = [(a, m, 0) for m in moves]
                    nexts.append((a, b, 0))
                state = (a, b, s)
                left[state] = len(nexts)
                for nxt in nexts:
                    preds.setdefault(nxt, []).append(state)
    head = 0
    while head < len(queue):
        state = queue[head]
        head += 1
        res, n = table[state]
        for prev in preds.get(state, ()):
            if prev in table:
                continue
            if res < 0:
                table[prev] = (1, n + 1)
                queue.append(prev)
            else:
                left[prev] -= 1
                if left[prev] == 0:
                    # mọi nước đều để đối thủ thắng: thua muộn nhất có thể
                    table[prev] = (-1, n + 1)
                    queue.append(prev)
    if len(_race_cache) > 32:
        _race_cache.clear()
    _race_cache[key] = table
    return table


def _race_action(pm, po, gm, hw, vw, up, dn, lf, rt, legal_moves, turns_left, map_m):
    """Nước tốt nhất khi cả hai hết tường; None nếu không tính được."""
    table = _race_table(gm, hw, vw, up, dn, lf, rt)
    best = None
    best_key = None
    for code in legal_moves + [-1]:          # -1: đứng yên
        a = pm if code < 0 else code
        res, n = table.get((a, po, 1), (0, 0))
        res, n = -res, n + 1                 # đổi sang góc nhìn của mình
        if res and n > turns_left:
            res = 0                          # hết 500 lượt trước khi phân thắng thua
        dist = _dist(map_m, a)
        # Thắng: nhanh nhất. Hòa: tiến gần đích nhất (chờ đối thủ sai), không đứng yên
        # nếu không cần. Thua: chậm nhất (có thể chạm giới hạn lượt hoặc đối thủ sai).
        if res > 0:
            rank = (2, -n, -dist, code >= 0)
        elif res == 0:
            rank = (1, 0, -dist, code >= 0)
        else:
            rank = (0, n, -dist, code >= 0)
        if best_key is None or rank > best_key:
            best_key = rank
            best = code
    if best is None:
        return None
    return {"type": "pass"} if best < 0 else _to_action(best)


def _proven_win(pm, po, gm, hw, vw, up, dn, lf, rt, legal_moves, legal_walls, map_m, stop):
    """Đối thủ đã hết tường: tìm nước THẮNG CHẮC đã chứng minh, hoặc None.

    Công thức "đối thủ hết tường + mình gần hơn = thắng" sai khi quân đối thủ đứng
    chắn được lối hẹp của mình. Bảng đua chính xác (coi như mình cũng không đặt thêm
    tường) cho chặn dưới: nước nào thắng trong bảng thì thắng thật. Thử đi quân/đứng
    yên trước, sau đó từng tường (gần hai quân trước) tới khi hết giờ.
    """
    table = _race_table(gm, hw, vw, up, dn, lf, rt)
    best = None
    for code in legal_moves + [-1]:
        a = pm if code < 0 else code
        res, n = table.get((a, po, 1), (0, 0))
        if res < 0 and n + 1 <= _turns_left:
            key = (n, _dist(map_m, a), code < 0)
            if best is None or key < best[0]:
                best = (key, code)
    if best is not None:
        return {"type": "pass"} if best[1] < 0 else _to_action(best[1])
    rm, cm = divmod(pm, 9)
    ro, co = divmod(po, 9)

    def near(code):
        r, c = divmod(code % 100, 8)
        return min(abs(r + 0.5 - rm) + abs(c + 0.5 - cm), abs(r + 0.5 - ro) + abs(c + 0.5 - co))

    for code in sorted(legal_walls, key=lambda w: (near(w), w)):
        if time.perf_counter() > stop:
            break
        state = _place_wall(code, hw, vw, up, dn, lf, rt)
        if state is None:
            continue
        res, n = _race_table(gm, *state).get((pm, po, 1), (0, 0))
        if res < 0 and n + 1 <= _turns_left:
            return _to_action(code)
    return None


# ------------------------------------------- 1 tường vs 0 tường (chính xác)
# Trạng thái i = s*6561 + a*81 + b: a là ô của mình, b của đối thủ, s = 0 nếu mình đi.
_AB_A = [ab // 81 for ab in range(6561)]
_AB_B = [ab % 81 for ab in range(6561)]
_KEY_WIN = 2000        # khóa lối ra theo góc nhìn bên giữ tường: thắng 2000-n, hòa 1000, thua n
_KEY_DRAW = 1000
_w1 = {"key": None}    # bảng 1-vs-0 đang dựng cho cấu hình tường hiện tại (giữ qua các lượt)


def _fast_race(gm, up, dn, lf, rt, exit_keys=None, holder=0):
    """Giải trọn cuộc đua đi quân + đứng yên bằng duyệt ngược theo mức (chỉ số nguyên).

    Trả về (res, lev, valid): res[i] = 1 bên đi thắng / -1 thua / 0 hòa, lev[i] = số
    nửa-nước. exit_keys (theo ab, góc nhìn bên `holder`): lối ra "đặt tường cuối" tốt
    nhất ở các trạng thái holder đi — biến bài toán thành 1 tường vs 0 tường.
    """
    nbr = []
    for p in range(81):
        lst = []
        if (up >> p) & 1:
            lst.append(p - 9)
        if (dn >> p) & 1:
            lst.append(p + 9)
        if (lf >> p) & 1:
            lst.append(p - 1)
        if (rt >> p) & 1:
            lst.append(p + 1)
        nbr.append(lst)
    special = {}
    rev = {}
    for m in range(81):
        for o in nbr[m]:
            dests = _pawn_moves(m, o, up, dn, lf, rt)
            special[(m, o)] = dests
            for d in dests:
                rev.setdefault((d, o), []).append(m)
    g0, g1 = GOAL_MASK[gm], GOAL_MASK[gm ^ 1]
    deg = [len(x) for x in nbr]
    step0 = [any((g0 >> m) & 1 for m in nbr[p]) for p in range(81)]
    step1 = [any((g1 >> m) & 1 for m in nbr[p]) for p in range(81)]
    res = [0] * 13122
    lev = [0] * 13122
    left = [0] * 13122
    valid = bytearray(13122)
    wins = []
    row_valid = bytes(0 if (g1 >> b) & 1 else 1 for b in range(81))
    row_left1 = [deg[b] + 1 for b in range(81)]
    for a in range(81):
        if (g0 >> a) & 1:
            continue
        o0 = a * 81
        o1 = 6561 + o0
        valid[o0:o0 + 81] = row_valid
        valid[o1:o1 + 81] = row_valid
        valid[o0 + a] = 0
        valid[o1 + a] = 0
        if step0[a]:
            for b in range(81):
                if row_valid[b] and b != a:
                    res[o0 + b] = lev[o0 + b] = 1
                    wins.append(o0 + b)
        else:
            left[o0:o0 + 81] = [deg[a] + 1] * 81
        left[o1:o1 + 81] = row_left1
        for b in range(81):
            if step1[b] and row_valid[b] and b != a:
                res[o1 + b] = lev[o1 + b] = 1
                wins.append(o1 + b)
        for b in nbr[a]:                          # hai quân kề nhau: nhảy / đi chéo
            if not row_valid[b]:
                continue
            for i, mv, g in ((o0 + b, special[(a, b)], g0), (o1 + b, special[(b, a)], g1)):
                was = lev[i]
                if any((g >> m) & 1 for m in mv):
                    if not was:
                        res[i] = lev[i] = 1
                        wins.append(i)
                else:
                    res[i] = lev[i] = 0
                    left[i] = len(mv) + 1
    buckets = [[] for _ in range(400)]
    buckets[1] = [i for i in wins if lev[i] == 1]
    if exit_keys is not None:
        base = holder * 6561
        for ab in range(6561):
            key = exit_keys[ab]
            i = base + ab
            if key < 0 or not valid[i] or lev[i]:
                continue
            if key >= _KEY_DRAW:
                left[i] += 1 << 20                                    # có lối thắng/hòa: không thua
                if key > _KEY_DRAW:
                    buckets[min(_KEY_WIN - key, 398)].append(~i)      # thắng qua lối ra ở mức n
            else:
                left[i] += 1
                buckets[min(key, 398)].append(-i - 13123)             # lối ra thua
    for k in range(1, 399):
        bucket = buckets[k]
        nxt = buckets[k + 1]
        j = 0
        while j < len(bucket):
            i = bucket[j]
            j += 1
            if i < 0:
                if i <= -13123:
                    t = -i - 13123
                    if not lev[t]:
                        left[t] -= 1
                        if not left[t]:
                            res[t] = -1
                            lev[t] = k + 1
                            nxt.append(t)
                    continue
                t = ~i
                if not lev[t]:
                    res[t] = 1
                    lev[t] = k
                    bucket.append(t)
                continue
            if lev[i] != k:
                continue
            win_for_pred = res[i] < 0
            if i < 6561:                          # trước đó đối thủ đi: đứng yên hoặc b2 -> b
                a, b = divmod(i, 81)
                base = 6561 + a * 81
                cand = [base + b]
                for b2 in nbr[b]:
                    if b2 != a and a not in nbr[b2]:
                        cand.append(base + b2)
                for b2 in rev.get((b, a), ()):
                    cand.append(base + b2)
            else:                                 # trước đó mình đi: đứng yên hoặc a2 -> a
                a, b = divmod(i - 6561, 81)
                cand = [a * 81 + b]
                for a2 in nbr[a]:
                    if a2 != b and b not in nbr[a2]:
                        cand.append(a2 * 81 + b)
                for a2 in rev.get((a, b), ()):
                    cand.append(a2 * 81 + b)
            for p in cand:
                if lev[p] or not valid[p]:
                    continue
                if win_for_pred:
                    res[p] = 1
                    lev[p] = k + 1
                    nxt.append(p)
                else:
                    left[p] -= 1
                    if not left[p]:
                        res[p] = -1
                        lev[p] = k + 1
                        nxt.append(p)
    return res, lev, valid


def _w1_build(pm, po, gm, holder, hw, vw, up, dn, lf, rt, stop):
    """Dựng dần bảng 1-vs-0 (holder: 0 mình giữ tường, 1 đối thủ giữ). Trả về trạng thái _w1.

    Mỗi tường cuối có thể đặt -> một cuộc đua 0/0 trên cấu hình mới; giữ mảng khóa (góc
    nhìn bên giữ tường) của từng tường và mảng lớn nhất. Tường gần hai quân tính trước.
    """
    key = (hw, vw, gm, holder)
    st = _w1
    if st["key"] != key:
        rm, cm = divmod(pm, 9)
        ro, co = divmod(po, 9)

        def near(code):
            r, c = divmod(code % 100, 8)
            return min(abs(r + 0.5 - rm) + abs(c + 0.5 - cm), abs(r + 0.5 - ro) + abs(c + 0.5 - co))

        todo = [code for base in (H_BASE, V_BASE) for code in range(base, base + 64)
                if _place_wall(code, hw, vw, up, dn, lf, rt) is not None]
        todo.sort(key=lambda code: (-near(code), -code))   # lấy ra từ cuối: gần trước
        st.clear()
        st.update(key=key, todo=todo, walls={}, best=[-1] * 6561, table=None, done_walls=0)
    other = (1 - holder) * 6561
    go = gm ^ 1
    while st["todo"] and time.perf_counter() < stop:
        code = st["todo"].pop()
        state = _place_wall(code, hw, vw, up, dn, lf, rt)
        reach_m = 0
        for layer in _dist_map(gm, *state):
            reach_m |= layer
        reach_o = 0
        for layer in _dist_map(go, *state):
            reach_o |= layer
        res, lev, valid = _fast_race(gm, *state[2:])
        keys = array("h", [-1]) * 6561
        for ab in range(6561):
            i = other + ab
            if not valid[i] or not (reach_m >> _AB_A[ab]) & 1 or not (reach_o >> _AB_B[ab]) & 1:
                continue
            r = res[i]
            keys[ab] = _KEY_WIN - lev[i] - 1 if r < 0 else (_KEY_DRAW if r == 0 else lev[i] + 1)
        st["walls"][code] = keys
        st["best"] = list(map(max, st["best"], keys))
        st["table"] = None
    if st["table"] is None and time.perf_counter() < stop + 0.05:
        st["table"] = _fast_race(gm, up, dn, lf, rt, st["best"], holder)
        st["done_walls"] = len(st["walls"])
    return st


def _w1_values(st, pm, po, legal_moves, legal_walls, holder):
    """Giá trị (kết quả, số nửa-nước, mã) theo góc nhìn mình của từng hành động."""
    res, lev, valid = st["table"]
    out = []
    for code in legal_moves + [-1]:
        a = pm if code < 0 else code
        i = 6561 + a * 81 + po
        if valid[i]:
            out.append((-res[i], lev[i] + 1, code))
    if holder == 0:
        ab = pm * 81 + po
        for code, keys in st["walls"].items():
            if code not in legal_walls:
                continue
            k = keys[ab]
            if k < 0:
                continue
            if k > _KEY_DRAW:
                out.append((1, _KEY_WIN - k, code))
            elif k == _KEY_DRAW:
                out.append((0, 0, code))
            else:
                out.append((-1, k, code))
    return out


def _pick(values, turns_left, map_m):
    """Chọn hành động: thắng nhanh nhất > hòa (tiến gần đích, hạn chế đứng yên) > thua chậm nhất."""
    best = None
    for r, n, code in values:
        if r and n > turns_left:
            r = 0
        dist = _dist(map_m, code) if 0 <= code < 81 else 99
        if r > 0:
            rank = (2, -n, -dist, code >= 0)
        elif r == 0:
            rank = (1, 0, -dist, code >= 0)
        else:
            rank = (0, n, -dist, code >= 0)
        if best is None or rank > best[0]:
            best = (rank, r, code)
    return best


# ---------------------------------------------------------- giao tiếp arena
def _greedy_action(observation):
    moves = observation["legal_moves"]
    if not moves:
        return {"type": "pass"}
    dist = observation["distances"]
    best = min(moves, key=lambda m: dist[m[0]][m[1]] if dist[m[0]][m[1]] >= 0 else 999)
    return {"type": "move", "to": [int(best[0]), int(best[1])]}


def _to_action(code):
    if code < 81:
        return {"type": "move", "to": [code // 9, code % 9]}
    base = H_BASE if code < V_BASE else V_BASE
    a = code - base
    return {"type": "wall", "orientation": "H" if base == H_BASE else "V",
            "row": a // 8, "col": a % 8}


def _decide(observation, started):
    global _deadline, _last_turn, _last_depth, _turns_left

    you = observation["you"]
    opp = observation["opponent"]
    turn = observation.get("turn", 0)
    _turns_left = observation.get("max_turns", 500) - turn
    if turn < _last_turn or turn <= 1:
        _history.clear()        # ván mới
        _visited.clear()
    _last_turn = turn
    # Dọn bộ đệm ở đầu lượt (giải phóng nhiều đối tượng tốn vài chục ms,
    # làm giữa lúc tìm kiếm sẽ dễ vượt hạn giờ).
    if len(_dist_cache) > DIST_CACHE_LIMIT:
        _dist_cache.clear()
        _path_cache.clear()
    if len(_tt) > TT_LIMIT:
        _tt.clear()
    if len(_child_cache) > CHILD_LIMIT or len(_opts_cache) > CHILD_LIMIT:
        _child_cache.clear()
        _opts_cache.clear()

    pm = you["position"][0] * 9 + you["position"][1]
    po = opp["position"][0] * 9 + opp["position"][1]
    gm = 0 if you["goal_row"] == 0 else 1
    go = gm ^ 1
    wm = you["walls_remaining"]
    wo = opp["walls_remaining"]
    hw = vw = 0
    up, dn, lf, rt = BASE_UP, BASE_DN, BASE_LF, BASE_RT
    for r, c in observation["walls"]["horizontal"]:
        a = r * 8 + c
        hw |= 1 << a
        up &= H_KEEP_UP[a]
        dn &= H_KEEP_DN[a]
    for r, c in observation["walls"]["vertical"]:
        a = r * 8 + c
        vw |= 1 << a
        lf &= V_KEEP_LF[a]
        rt &= V_KEEP_RT[a]

    legal_moves = sorted(m[0] * 9 + m[1] for m in observation["legal_moves"])
    legal_walls = set()
    for w in observation["legal_walls"]:
        base = H_BASE if w["orientation"] == "H" else V_BASE
        legal_walls.add(base + w["row"] * 8 + w["col"])

    goal = GOAL_MASK[gm]
    for m in legal_moves:
        if (goal >> m) & 1:
            return _to_action(m)

    map_m = _dist_map(gm, hw, vw, up, dn, lf, rt)
    map_o = _dist_map(go, hw, vw, up, dn, lf, rt)
    dm = _dist(map_m, pm)
    do = _dist(map_o, po)

    if wm == 0 and wo == 0:
        # Hết tường cả hai: giải chính xác (nhảy qua nhau, đứng chắn lối, đứng yên).
        action = _race_action(pm, po, gm, hw, vw, up, dn, lf, rt, legal_moves, _turns_left, map_m)
        if action is not None:
            return action
    veto = set()
    if wm + wo == 1 and _turns_left >= NEAR_END:
        holder = 0 if wm else 1
        st = _w1_build(pm, po, gm, holder, hw, vw, up, dn, lf, rt, started + W1_TIME)
        if st["table"] is not None:
            values = _w1_values(st, pm, po, legal_moves, legal_walls, holder)
            complete = not st["todo"] and st["done_walls"] == len(st["walls"])
            best = _pick(values, _turns_left, map_m)
            # Bảng đủ: chính xác. Thiếu tường: nếu mình giữ tường thì bảng là chặn dưới
            # (thắng là thắng thật); nếu đối thủ giữ thì bảng lạc quan cho mình, nên chỉ
            # loại được những nước thua ngay cả trong bảng đó.
            if best is not None and (complete or holder == 0 and best[1] > 0):
                code = best[2]
                return {"type": "pass"} if code < 0 else _to_action(code)
            if holder == 1 and best is not None and best[1] >= 0:
                veto = {code for r, n, code in values if r < 0 and not (n > _turns_left)}
    if wo == 0 and wm > 0 and _turns_left >= NEAR_END:
        action = _proven_win(pm, po, gm, hw, vw, up, dn, lf, rt, legal_moves, sorted(legal_walls),
                             map_m, started + PROOF_TIME)
        if action is not None:
            return action

    # Nước ở gốc: mọi nước đi hợp lệ + các tường có lợi (đã kiểm với arena).
    root = []
    for m in legal_moves:
        root.append((dm - _dist(map_m, m), m, None))
    if wm > 0:
        for code, state, new_m, new_o in _wall_options(pm, po, gm, hw, vw, up, dn, lf, rt,
                                                       map_m, map_o, dm, do):
            if code in legal_walls:
                root.append(((new_o - do) - (new_m - dm) - WALL_WEIGHT, code, state))
        if wo > 0:
            taken = {child[1] for child in root}
            for saved, code, state, new_o in _defensive_walls(
                    pm, po, gm, hw, vw, up, dn, lf, rt, dm, do, sorted(legal_walls), taken,
                    started + DEFENSE_TIME):
                root.append((new_o - do + saved - 2 * WALL_WEIGHT, code, state))
    if wm == 1 and wo == 0 and dm < do and _turns_left >= NEAR_END:
        # Chưa chứng minh được thắng chắc: tường cuối là mối đe dọa giữ đối thủ khỏi
        # đứng chắn lối. Chỉ đặt nó nếu sau khi đặt, cuộc đua (đã giải chính xác) vẫn thắng.
        kept = []
        for child in root:
            if child[2] is not None and time.perf_counter() < started + 2 * PROOF_TIME:
                res, _ = _race_table(gm, *child[2]).get((pm, po, 1), (0, 0))
                if res >= 0:
                    continue
            kept.append(child)
        if kept:
            root = kept
    if veto:
        root = [child for child in root if child[1] not in veto] or root
    if not root:
        return {"type": "pass"}
    root.sort(key=lambda child: (-child[0], child[1]))

    def repeat_key(code, state):
        if state is None:
            return (code, po, hw, vw)
        return (pm, po, state[0], state[1])

    repeats = [_history.get(repeat_key(code, state), 0) for _, code, state in root]
    penalties = [REPEAT_PENALTY * count for count in repeats]
    # Không có động lực tiến thì khi dẫn đua bot cứ đi qua đi lại chờ đối thủ,
    # mất nhịp tới lúc bị vượt. Phạt nhẹ nước lùi/né, quay lại ô cũ, và tường
    # lợi ít khi chưa gấp (tường đặt muộn trên bàn chật thường lợi hơn nhiều).
    opening = (dm >= 6 and do >= 6
               and len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"]) <= 2)
    for i, (_, code, state) in enumerate(root):
        if state is None:
            penalties[i] += BACK_PENALTY * max(0, _dist(map_m, code) - dm)
            if code in _visited:
                penalties[i] += VISIT_PENALTY
        else:
            net = (_dist(_dist_map(go, *state), po) - do) - (_dist(_dist_map(gm, *state), pm) - dm)
            if net < 2 and do > 3:
                penalties[i] += WEAK_WALL_PENALTY[0] if net <= 0 else WEAK_WALL_PENALTY[1]
            if opening and net < 3:
                penalties[i] += OPENING_WALL_PENALTY
    _visited.add(pm)

    _deadline = started + TIME_BUDGET
    best_code = root[0][1]
    iter_best = None
    try:
        for depth in range(1, MAX_DEPTH + 1):
            alpha = NEG_INF
            iter_best = None
            scored = []
            for index, (order, code, state) in enumerate(root):
                penalty = penalties[index]
                # Cửa sổ dịch theo mức phạt để alpha-beta vẫn đúng.
                bound = -(alpha + penalty) if alpha != NEG_INF else POS_INF
                if state is None:
                    raw = -_search(depth - 1, NEG_INF, bound, po, code, wo, wm, go,
                                   hw, vw, up, dn, lf, rt, 1)
                else:
                    raw = -_search(depth - 1, NEG_INF, bound, po, pm, wo, wm - 1, go,
                                   *state, 1)
                score = raw - penalty
                if raw > SURE_BOUND and repeats[index]:
                    # Đang thắng mà quay về thế cũ là phí nước (và dễ thành hòa
                    # 500 lượt): phạt theo số nửa-nước. Cửa sổ ở trên dùng mức
                    # phạt nhỏ nên vẫn đúng khi mức phạt thật lớn hơn.
                    score = raw - WIN_REPEAT_PLIES * repeats[index]
                scored.append((score, index))
                if score > alpha:
                    alpha = score
                    iter_best = code
            best_code = iter_best
            _last_depth = depth
            # Đưa nước tốt nhất lên đầu cho vòng sau (sắp xếp ổn định).
            scored.sort(key=lambda item: -item[0])
            root = [root[i] for _, i in scored]
            penalties = [penalties[i] for _, i in scored]
            repeats = [repeats[i] for _, i in scored]
            # Thắng đã chứng minh trong k nửa-nước: chỉ dừng khi đã tìm đủ sâu k
            # để chắc không còn đường thắng ngắn hơn (bảng chuyển vị có thể đưa
            # về kết quả thắng "xa" của lần tìm trước cho một nước lùi).
            if alpha > WIN_BOUND and depth >= WIN - alpha:
                break
            if alpha < -WIN_BOUND:
                break               # thua đã chứng minh, tìm thêm cũng vô ích
            if time.perf_counter() - started > TIME_BUDGET * NEXT_ITER_FRAC:
                break               # vòng sau gần như chắc chắn không kịp
    except _Timeout:
        # Vòng dang dở: nước tốt nhất mới chỉ được thay khi đã tìm xong đầy đủ.
        if iter_best is not None:
            best_code = iter_best

    for _, code, state in root:
        if code == best_code:
            key = repeat_key(code, state)
            _history[key] = _history.get(key, 0) + 1
            break
    return _to_action(best_code)


def choose_action(observation):
    started = time.perf_counter()
    try:
        action = _decide(observation, started)
    except Exception:
        return _greedy_action(observation)
    if action["type"] == "move" and action["to"] not in observation["legal_moves"]:
        return _greedy_action(observation)
    if action["type"] == "wall":
        compact = {"orientation": action["orientation"], "row": action["row"], "col": action["col"]}
        if compact not in observation["legal_walls"]:
            return _greedy_action(observation)
    return action
