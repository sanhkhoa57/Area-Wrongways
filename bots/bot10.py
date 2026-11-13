"""xxxxxxxx.py - agent Wrongway chính (stockfish -> redvelvet -> allahuakbar -> bombaclat -> bản này)

=== A. NỀN TẢNG (đủ để giải thích khi vấn đáp) ===

1. BITBOARD: bàn 9x9 = 81 ô, ô (r, c) = bit r*9 + c. Bốn mặt nạ U, D, L, R cho biết từ
   ô nào được đi lên / xuống / trái / phải; BFS là vài phép dịch bit trên cả lớp ô.
2. ĐÁNH GIÁ: 10 x (quãng đường đối thủ - mình) + giá trị số tường còn lại + 5 cho bên
   tới lượt; một bên hết tường thì cuộc đua gần như đã định -> cộng/trừ thêm.
3. TÌM KIẾM: đào sâu lặp, PVS, bảng chuyển vị 2 thế hệ, killer; thứ tự xét nước: nước
   đã nhớ -> killer -> di chuyển -> 9 tường lợi nhất -> đứng yên. Tăng tốc bằng đồ thị
   đường ngắn nhất (DAG) và danh sách tường ứng viên có nhớ.
4. BẢNG TÀN CUỘC: khi cả hai hết tường chỉ còn 81 x 81 x 2 thế -> giải trọn bằng phân
   tích ngược (thắng / thua sau k nước / hòa); thế "một bên hết tường, bên kia còn đúng
   1 tường" cũng giải trọn; còn nhiều tường hơn thì dùng làm cận để cắt tỉa.
5. LUẬT ARENA: được đứng yên, hòa sau 500 lượt, 3-1-0 -> hòa bị coi hơi xấu (CONTEMPT);
   nhận ra thế "chặn cửa" (đối thủ đứng đúng cửa ải duy nhất) là hòa.
6. AN TOÀN: tự giới hạn 0.8 giây/lượt, kiểm tra lại hành động với legal_moves /
   legal_walls, có phương án dự phòng nếu lỗi.

=== B. BOMBACLAT THÊM GÌ SO VỚI allahuakbar

7. NGHĨ HỘ (ponder, từ allahuakbar): sau khi trả lời, một luồng nền tìm kiếm tiếp các thế
   đối thủ có thể đáp lại (dồn trước cho nước đáp được dự đoán), ghi vào bảng chuyển vị;
   tới lượt mình tìm kiếm đã có sẵn kết quả. Luồng dừng ngay khi có lượt mới. Đo trên
   ván thật: khai cuộc nhìn sâu 7-8 tầng thay vì 6 tầng của allahuakbar. Tắt: PONDER = False.

8. NHÁNH CHỐNG ĐỐI THỦ ĐỨNG IM BÍT ĐƯỜNG (mới, _stall_action): đối thủ cứ đứng yên trên
   đường ngắn nhất thì redvelvet chỉ biết đứng chờ -> hòa dù có đường vòng chỉ dài hơn
   vài ô. bombaclat đếm số lượt liền đối thủ không đổi gì:
   - >= 2 lượt và cả hai hết tường: chọn nước đi vòng làm "quãng đường khi đối thủ đứng
     yên" (_static_path, có nhảy/đi chéo đúng luật) ngắn đi mà bảng tàn cuộc chính xác vẫn
     báo KHÔNG thua -> luôn an toàn, không lặp thế cũ;
   - >= 24 lượt liền (STALL_GAMBLE_PASSES): coi là bot "đỗ xe" và đi vòng bất chấp bảng
     tàn cuộc (còn đủ lượt trước giới hạn 500). Có rủi ro nếu đối thủ biết phản công nên
     chỉ bật sau thời gian chờ dài; chỉnh/tắt bằng hằng số.
   Kiểm tra (150 bàn ngẫu nhiên đã cố định tường, đối thủ đứng im mãi, có đường vòng thắng):
   redvelvet thắng 146/148, bombaclat thắng 148/148.

9. HIỆU CHỈNH ĐÁNH GIÁ HỌC TỪ TỰ PHÂN TÍCH (EVAL2, _DA/_DB/_DV): engine tự chơi ~4.500 thế
   cờ (mở đầu ngẫu nhiên, chỉ dùng chính nó, không dữ liệu của bot nào khác), mỗi thế
   được tìm kiếm sâu 80.000 nút để lấy điểm "thật"; khớp lại giá trị k tường theo quãng
   đường còn lại của quân bị chặn (tường chặn quân còn xa đích thì đáng giá, chặn quân
   sát đích thì ít). Sai số giảm 29% trên dữ liệu giữ lại, NHƯNG khi đo bằng tự đấu thì
   không chơi hay hơn: đủ liều 0.438 +- 0.106 (80 ván), giảm còn 40% 0.477 +- 0.047
   (400 ván; điểm > 0.5 mới là mạnh hơn). Kết luận: dự đoán điểm chính xác hơn không làm
   chơi hay hơn, nên EVAL2 = False (TẮT); bảng giữ lại để tham khảo/tiếp tục thử.

10. SÁCH KHAI CUỘC TỪ TỰ PHÂN TÍCH (_BOOK, 306 thế): bản "sâu" (100.000 nút/lượt) của chính
    engine đấu bản "nông" của chính nó (50 mức nông x 2 bên = 100 ván, 82 ván thắng); chỉ giữ
    nước đi của các ván thắng (biểu quyết khi trùng thế, không có xung đột). Không dùng
    dữ liệu của bot nào khác. Đây là nơi sức mạnh tìm kiếm sâu được "đóng băng" lại cho khai
    cuộc, nơi tìm kiếm 0.8 giây yếu nhất, nhất là khi đi sau. Thế ngoài sách -> tìm kiếm
    bình thường.

11. ĐỖ XE ÉP HÒA (PARK, _can_park, _steps) + KIỂM TRA CHẶN CỬA Ở NÚT LÁ (LEAF_BLOCK):
    redvelvet chỉ nhận ra "chặn cửa" khi quân kia ĐÃ đứng đúng cửa ải, và chỉ ở nút sát lá.
    Bản này nhìn trước một bước nữa: "cửa ải" = ô duy nhất mà mọi đường ngắn nhất của quân
    bị chặn đều qua (lớp DAG chỉ có một ô) VÀ nếu quân kia đứng đó mãi thì quân bị chặn hết
    đường (tính cả nhảy / đi chéo). Nếu một quân tới kịp ô đó trước khi quân kia đi qua
    (đếm bước bằng dịch bit):
      - bên đang THUA: coi thế là hòa thay vì thua (chủ động đỗ xe ép hòa, chỉ khi hòa tốt
        hơn điểm đang có);
      - bên đang THẮNG: coi thế là hòa vì đối thủ sẽ kịp đỗ xe -> tránh các thế sắp bị cầm hòa.
    Phép kiểm tra chạy cả ở nút lá. Bật/tắt: PARK, LEAF_BLOCK. Mới cài, đang đo bằng tự đấu
    theo số nút cố định (lô nhỏ); chưa kết luận về độ mạnh.

12. THAM SỐ RIÊNG CHO TỪNG BÊN (SIDE_PARAMS): giá trị tường (wall_scale), tempo, contempt
    đặt riêng cho bên đi trước (0) và bên đi sau (1), áp dụng một lần mỗi ván (_apply_side).
    Đo trên arena thật (30 ván/nhóm, các ván dùng chung dòng khai cuộc nên không hoàn toàn
    độc lập), hệ số giá trị tường wall_scale:
      - đi trước vs xxxxxxxx: 1.5 -> 0/15; 1.0 -> 8/15; 0.7 -> 20/20; 0.5 -> 13/20;
      - đi sau  vs xxxxxxxx: 1.0 -> 15/15 (cộng 10/10 trước đó); 0.7 -> 6 thắng 1 hòa 8 thua;
      - cả hai bên 0.7 vs đi trước 15/15, đi sau 14/15; vs allahuakbar: 15/15, 13/15.
    => đi trước dùng 0.7, đi sau 1.0. Bản tách bên vs xxxxxxxx: đi trước 14/1/0, đi sau 15/0/0.
    Phép kiểm tra hồi quy (300 thế cờ thật, 6000 nút): tắt PARK/LEAF_BLOCK và sách thì
    nước đi giống hệt engine 300/300; bật hai cờ đó khác 4/300.

14. (E) GIÁ TRỊ TƯỜNG THEO SỨC PHÁ THỰC TẾ (DYN_WALLS, _dyn_tables): mỗi lượt đo xem một bức
    tường của mỗi bên làm quân kia chậm thêm tối đa bao nhiêu bước (dmg); một tường trong tay
    đáng 0.375 + 0.5 x dmg bước (giới hạn 0.5..3.5). Nếu mình đi trước mà đối thủ để dành nhiều
    tường hơn: tính theo sức phá trung bình của cả loạt tường đặt lần lượt (_greedy_damage).
    Hàm đánh giá nhận thêm hàng đích của bên đang đi để biết tường nào là của mình. Sửa lỗi
    "tiêu hết tường sớm rồi bị đối thủ để dành tường dựng mê cung sát đích".
15. (F) TƯỜNG DỰNG BẪY CỦA ĐỐI THỦ (SETUP_WALLS, _setup_walls): mỗi lượt tìm 3 bức tường của
    đối thủ (gần đường đi của mình, không làm nó chậm) mà sau đó k bức tiếp theo làm mình
    chậm hơn hẳn; đưa chúng vào các nước trả lời của đối thủ ở tầng 1 của cây tìm kiếm. Trước
    đây engine chỉ xét tường cắt đường ngắn nhất hiện tại nên không thấy hàng rào đang dựng.
    Ý tưởng E, F học từ agent JiggleMyBallsToNiagraFalls, viết lại trên bitboard của engine này.
    Đo (arena 1 giây/lượt) với Jiggle: bản cũ đi trước 0/6, đi sau 0 thắng 2 hòa 4 thua;
    bản này đi trước 10/10 (+10/10), đi sau 9/1 và 5/5 thắng/thua.

13. CÔNG CỤ ĐO: NODE_CAP (giới hạn số nút thay giới hạn thời gian để ván tự đấu lặp lại
    được); EXPLORE_EPS (khi tạo sách: thỉnh thoảng đi nước tốt thứ hai ở khai cuộc để khám
    phá nhánh khác, mặc định tắt); biến môi trường PONDER / TTCAP chỉ dùng khi thử nghiệm.
"""

import gc
import os
import threading
from array import array
from time import perf_counter

# Tắt bộ dọn rác tự động: chương trình không tạo vòng tham chiếu (đã kiểm tra:
# gc.collect() không tìm thấy rác nào), còn mỗi lần quét các bộ nhớ đệm lớn tốn
# tới ~0.3 giây - lãng phí thời gian suy nghĩ.
gc.disable()

# ---------------------------------------------------------------------------
# Hằng số bitboard
# ---------------------------------------------------------------------------
SIZE = 9
ALL = (1 << 81) - 1
ROW = [((1 << 9) - 1) << (9 * r) for r in range(9)]
COL = [sum(1 << (9 * r + c) for r in range(9)) for c in range(9)]
ANCHOR = sum(1 << (9 * r + c) for r in range(8) for c in range(8))  # neo tường hợp lệ
U0 = ALL & ~ROW[0]   # ô có thể đi lên (chưa có tường)
D0 = ALL & ~ROW[8]   # ô có thể đi xuống
L0 = ALL & ~COL[0]   # ô có thể sang trái
R0 = ALL & ~COL[8]   # ô có thể sang phải
INF = 1000
_RC_R = [i // 9 for i in range(81)]   # hàng của ô theo chỉ số bit
_RC_C = [i % 9 for i in range(81)]    # cột của ô theo chỉ số bit

# Mã hóa nước đi bằng số nguyên: 0..80 = đi tới ô, 100+a = tường H tại neo a,
# 200+a = tường V tại neo a, PASS = đứng yên.
H_BASE = 100
V_BASE = 200
PASS = 999

# ---------------------------------------------------------------------------
# Tham số đánh giá (đơn vị: 10 = một bước đi)
# ---------------------------------------------------------------------------
STEP = 10           # giá trị 1 bước chênh lệch quãng đường
TEMPO = 5           # lợi thế của bên đang tới lượt
RACE_BONUS = 60     # cuộc đua đã "chắc chắn" (một bên hết tường)
DYN_WALLS = True      # (E) giá trị tường theo sức phá thực tế lúc này (ý tưởng từ Jiggle, viết lại)
_DYN = [None]         # (bảng giá trị k tường của mình, của đối thủ) - tính ở gốc mỗi lượt
UNANSWERED_WALL = 10  # (A) cộng thêm cho mỗi tường khi đối thủ đã hết tường để đáp trả
# Giá trị của k bức tường còn lại (đã tinh chỉnh bằng tự đấu): mỗi tường ~1.2
# bước, riêng 3 tường cuối cùng quý hơn (2.4 / 1.8 / 1.4 bước) vì cuối ván, bên
# còn tường có thể chặn quyết định.  WALL_VALUE[k] = tổng giá trị của k tường.
_WALL_MARGINAL = [24, 18, 14, 12, 12, 12, 12, 12, 12, 12]
WALL_VALUE = [sum(_WALL_MARGINAL[:k]) for k in range(11)]
EVAL2 = False       # bật hiệu chỉnh đánh giá học được (bảng _DA/_DB/_DV bên dưới)
_BK = [0, 0, 1, 2, 3, 4, 4, 5, 5, 5] + [6] * 100   # nhóm khoảng cách tới đích: 1,2,3,4,5-6,7-9,10+
_DA = 0.0
_DB = 0.0
_DV = [[0.0] * 7 for _ in range(11)]
NODE_CAP = 0        # >0: giới hạn số nút mỗi lượt (chỉ dùng khi đo/huấn luyện để ván đấu lặp lại được)
TIME_LIMIT = 0.86   # giây cho mỗi lượt (arena cho phép 1 giây; redvelvet dùng 0.8; chừa ~0.14s; tự giảm nếu bị trễ)
TOP_WALLS = 9       # số tường tối đa xét tại một nút trong cây
FRONTIER_KILLERS = True  # ở nút sát lá, thử tường killer/TT trước
TB = True           # bảng tàn cuộc chính xác khi cả hai đã hết tường
TB_MAX = 12         # số bảng mới tối đa được tính trong một lượt
TB_TIME = 0.45      # chỉ tính bảng mới trong 45% đầu thời gian của lượt
TB_BOUND_PLY = 2    # dùng cận (một bên hết tường) chỉ khi bảng có sẵn hoặc ở <= 2 tầng đầu
TB1 = True          # giải trọn tiền tàn cuộc: một bên hết tường, bên kia còn đúng 1 tường
TB1_MAXK = 2        # còn <= 2 tường thì vẫn dùng bảng "1 tường" làm cận
TB1_TIME = 0.5      # tối đa 50% thời gian lượt để dựng bảng (phần còn lại để lượt sau)
ITER_FACTOR = 1.0   # bắt đầu độ sâu mới nếu còn ít nhất bằng thời gian lượt lặp vừa rồi
WIN = 100000

# Bộ nhớ dùng chung giữa các lượt trong một ván
_TT = {}
_TT_OLD = {}         # thế hệ trước của bảng chuyển vị (giữ lại khi bảng chính đầy)
TT2 = True
TT_CAP = 75000
_STATE = {"last_turn": -1, "margin": 0.0, "moves": 0}
_HIST = set()        # các thế cờ đã xuất hiện trong ván (để nhận ra lặp lại)
_ROOT_GOAL = [0]     # hàng đích của "mình" ở lượt hiện tại (để tính điểm hòa)
CONTEMPT = 25        # hòa chỉ được 1 điểm (thắng 3): coi hòa là hơi xấu cho mình
_BLOCK = [False]     # bật kiểm tra "chặn cửa" khi trên bàn đã đủ nhiều tường
_BCACHE = {}
_LCACHE = {}         # nhớ các lớp BFS ngược theo (hàng đích, tường)
_WCACHE = {}         # nhớ danh sách tường ứng viên theo (quân đối thủ, tường)
_DCACHE = {}         # nhớ kết quả BFS: (ô, hàng đích, tường H, tường V) -> khoảng cách
_GCACHE = {}         # nhớ "đồ thị đường ngắn nhất" của một quân theo (ô, hàng đích, tường)
_TBC = {}            # bảng tàn cuộc 0-0 theo (tường H, tường V)
_TB1C = {}           # bảng tiền tàn cuộc "1 tường vs 0" theo (tường H, tường V, bên còn tường là N?)
BLOCK_MIN_WALLS = 8
BLOCK_MIN_S = 15    # chỉ kiểm tra khi một bên dẫn rõ rệt


MATE_ZONE = WIN - 1000


class _TimeUp(Exception):
    pass


def _to_tt(score, ply):
    """Điểm thắng/thua phụ thuộc độ sâu: lưu dạng tương đối để tái sử dụng đúng."""
    if score > MATE_ZONE:
        return score + ply
    if score < -MATE_ZONE:
        return score - ply
    return score


def _draw(goal):
    """Điểm của thế HÒA theo góc nhìn bên có hàng đích `goal` đang tới lượt.

    Thể thức 3-1-0: hòa kém hơn một ván cân bằng, nên với "mình" hòa = -CONTEMPT,
    với đối thủ hòa = +CONTEMPT (giữ tính đối xứng của negamax).
    """
    return -CONTEMPT if goal == _ROOT_GOAL[0] else CONTEMPT


# ---------------------------------------------------------------------------
# Các hàm cơ bản trên bitboard
# ---------------------------------------------------------------------------
def _dist(start, goal, U, D, L, R):
    """BFS bitboard: số bước ít nhất từ ô `start` (1 bit) tới hàng `goal`."""
    if start & goal:
        return 0
    seen = front = start
    d = 0
    while True:
        d += 1
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        if not front:
            return INF
        if front & goal:
            return d
        seen |= front


def _layers(goal, target, U, D, L, R, hw, vw):
    """BFS ngược từ hàng đích, có bộ nhớ đệm: các lớp BFS ngược từ hàng đích chỉ phụ
    thuộc vào TƯỜNG (không phụ thuộc vị trí quân), nên tính một lần cho mỗi bộ tường.

    Trả về (layers đầy đủ, chỉ số lớp chứa target).
    """
    key = (goal, hw, vw)
    lay = _LCACHE.get(key)
    if lay is None:
        lay = [goal]
        seen = front = goal
        while True:
            front = (((front & U) >> 9) | ((front & D) << 9)
                     | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
            if not front:
                break
            seen |= front
            lay.append(front)
        _LCACHE[key] = lay
    k = 0
    for f in lay:
        if f & target:
            return lay, k
        k += 1
    return lay, -1


def _sp_edges(pos, layers, d, U, D, L, R):
    """Các cạnh nằm trên ÍT NHẤT MỘT đường đi ngắn nhất từ pos tới đích.

    Trả về 4 mặt nạ: ô xuất phát của cạnh đi lên / xuống / trái / phải.
    """
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
    return up, dn, lt, rt


def _dag(pos, goal, U, D, L, R, hw, vw):
    """Đồ thị các đường ngắn nhất (DAG) của quân ở ô `pos`, có bộ nhớ đệm.

    Trả về (d, layers, F, up, dn, lt, rt): d = quãng đường; F[k] = các ô cách đích
    k bước nằm trên ít nhất một đường ngắn nhất xuất phát từ pos; up/dn/lt/rt =
    ô xuất phát của các cạnh thuộc DAG (như _sp_edges).
    """
    key = (pos, goal, hw, vw)
    ent = _GCACHE.get(key)
    if ent is not None:
        return ent
    lay, d = _layers(goal, pos, U, D, L, R, hw, vw)
    up = dn = lt = rt = 0
    if d < 0:
        ent = (INF, lay, None, 0, 0, 0, 0)
        _GCACHE[key] = ent
        return ent
    F = [0] * (d + 1)
    F[d] = cur = pos
    for k in range(d, 0, -1):
        nxt = lay[k - 1]
        u = cur & U & (nxt << 9)
        w = cur & D & (nxt >> 9)
        l = cur & L & (nxt << 1)
        r = cur & R & (nxt >> 1)
        up |= u
        dn |= w
        lt |= l
        rt |= r
        cur = (u >> 9) | (w << 9) | (l >> 1) | (r << 1)
        F[k - 1] = cur
    ent = (d, lay, F, up, dn, lt, rt)
    _GCACHE[key] = ent
    return ent


def _newdist(pos, goal, d, lay, F, src, U2, D2, L2, R2, hw2, vw2):
    """Quãng đường mới của quân sau khi chặn các cạnh DAG xuất phát từ ô `src`.

    Chính xác như BFS lại từ đầu, nhưng nhanh hơn: chỉ đi lại các lớp DAG từ chỗ
    bị chặn trở xuống; nếu DAG vẫn thông (một đường ngắn nhất còn nguyên) thì
    quãng đường không đổi; chỉ khi mọi đường ngắn nhất bị cắt mới phải BFS lại.
    """
    kmax = kmin = 0
    rest = src
    for k in range(d, 0, -1):
        f = F[k] & rest
        if f:
            if not kmax:
                kmax = k
            kmin = k
            rest ^= f
            if not rest:
                break
    cur = F[kmax]
    for j in range(kmax, 0, -1):
        cur = (((cur & U2) >> 9) | ((cur & D2) << 9)
               | ((cur & L2) >> 1) | ((cur & R2) << 1)) & lay[j - 1]
        if not cur:
            break
        if j <= kmin and cur == F[j - 1]:
            return d
    else:
        return d
    ck = (pos, goal, hw2, vw2)
    nd = _DCACHE.get(ck)
    if nd is None:
        nd = _DCACHE[ck] = _dist(pos, goal, U2, D2, L2, R2)
    return nd


def _pawn_moves(a, b, U, D, L, R):
    """Các ô quân `a` đi được (đúng luật arena: nhảy thẳng, chéo khi bị chặn)."""
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


def _apply_wall_code(code, U, D, L, R, hw, vw):
    """Mặt nạ sau khi đặt bức tường có mã `code`."""
    if code < V_BASE:
        w = 1 << (code - H_BASE)
        return U & ~((w << 9) | (w << 10)), D & ~(w | (w << 1)), L, R, hw | w, vw
    w = 1 << (code - V_BASE)
    return U, D, L & ~((w << 1) | (w << 10)), R & ~(w | (w << 9)), hw, vw | w


def _greedy_damage(victim, gv, k, U, D, L, R, hw, vw):
    """Tổng số bước k bức tường làm quân victim chậm thêm, đặt lần lượt, mỗi lần bức tốt nhất."""
    total = 0
    for _ in range(k):
        d, lst = _opp_walls(victim, gv, U, D, L, R, hw, vw)
        if not lst:
            break
        v = lst[0]
        total += (v >> 10) - d
        U, D, L, R, hw, vw = _apply_wall_code(v & 1023, U, D, L, R, hw, vw)
    return total


SETUP_WALLS = True    # (F) xét cả "tường dựng bẫy" của đối thủ khi nó đã từng chơi kiểu đó
SETUP_TOP = 3
SETUP_ALWAYS = True  # tính mỗi lượt (cả tường vừa lời ngay vừa dựng bẫy), không chờ đối thủ lộ kiểu chơi
SETUP_MIN_GAIN = 2
_SETUP = [None]       # (hàng đích của bên đặt, các mã tường dựng bẫy) - tính ở gốc mỗi lượt


def _masks(hw, vw):
    """Bốn mặt nạ đi lại khi trên bàn có đúng các tường hw / vw."""
    U, D, L, R = U0, D0, L0, R0
    m = hw
    while m:
        w = m & -m
        m ^= w
        D &= ~(w | (w << 1))
        U &= ~((w << 9) | (w << 10))
    m = vw
    while m:
        w = m & -m
        m ^= w
        R &= ~(w | (w << 9))
        L &= ~((w << 1) | (w << 10))
    return U, D, L, R


def _setup_walls(victim, gv, attacker, gatt, w_att, U, D, L, R, hw, vw):
    """(F) Tường "dựng bẫy" tốt nhất của attacker chống victim: có thể chưa làm victim chậm
    ngay, nhưng sau nó k bức tường tiếp theo (đặt lần lượt, mỗi lần bức tốt nhất) làm victim
    chậm hơn hẳn. Tìm kiếm thường chỉ sinh tường cắt đường ngắn nhất hiện tại nên không thấy
    một hàng rào đang được chuẩn bị."""
    k = min(4, w_att - 1)
    if k <= 0:
        return ()
    dv, lay, F, up, dn, lt, rt = _dag(victim, gv, U, D, L, R, hw, vw)
    if F is None:
        return ()
    da = _dist(attacker, gatt, U, D, L, R)
    threat_now = _greedy_damage(victim, gv, k, U, D, L, R, hw, vw)
    x = 0
    for f in F:
        x |= f
    for _ in range(2):
        x |= ((x & U0) >> 9) | ((x & D0) << 9) | ((x & L0) >> 1) | ((x & R0) << 1)
    near = (x | (x >> 9) | (x >> 1) | (x >> 10)) & ANCHOR
    scored = []
    for base, mask in ((H_BASE, near & ~(hw | (hw << 1) | (hw >> 1) | vw)),
                       (V_BASE, near & ~(vw | (vw << 9) | (vw >> 9) | hw))):
        while mask:
            w = mask & -mask
            mask ^= w
            code = base + w.bit_length() - 1
            U2, D2, L2, R2, hw2, vw2 = _apply_wall_code(code, U, D, L, R, hw, vw)
            if _dist(attacker, gatt, U2, D2, L2, R2) > da:
                continue                        # không được làm chính attacker chậm đi
            nv = _dist(victim, gv, U2, D2, L2, R2)
            if nv >= INF:
                continue
            gain = (nv - dv) + _greedy_damage(victim, gv, k, U2, D2, L2, R2, hw2, vw2) - threat_now
            if gain >= SETUP_MIN_GAIN:
                scored.append((gain, code))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return tuple(c for _, c in scored[:SETUP_TOP])


def _dyn_tables(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, moved_first):
    """(E) Giá trị một bức tường trong tay = 0.375 + 0.5 x (số bước nhiều nhất một bức tường
    làm quân kia chậm thêm NGAY LÚC NÀY), giới hạn [0.5, 3.5] bước. Nếu mình đi trước mà đối
    thủ để dành nhiều tường hơn: kéo về sức phá trung bình của CẢ loạt tường đặt lần lượt."""
    vals = []
    for victim, gv, w_own, w_other in ((b, gb, wa, wb), (a, ga, wb, wa)):
        d, lst = _opp_walls(victim, gv, U, D, L, R, hw, vw)
        dmg = ((lst[0] >> 10) - d) if lst else 0
        v = 0.375 + 0.5 * dmg
        saved = min(1.0, max(0.0, (wb - wa) / 3.0))
        if moved_first and saved > 0 and w_own > 0:
            greedy = _greedy_damage(victim, gv, w_own, U, D, L, R, hw, vw) / float(w_own)
            v += saved * ((0.375 + 0.35 * greedy) - v)
        v = min(3.5, max(0.5, v))
        vals.append(v)
    return tuple([STEP * v * k for k in range(11)] for v in vals)


def _static(dm, do, wm, wo, g=0):
    """Điểm thế cờ theo góc nhìn bên ĐANG TỚI LƯỢT.

    dm/do: quãng đường của mình/đối thủ; wm/wo: số tường còn lại; g: hàng đích (mặt nạ) của
    bên đang đi - để biết tường nào là của "mình" (bên tính ở gốc) khi giá trị tường động (E).
    """
    dyn = _DYN[0]
    if dyn is not None and g:
        if g == _ROOT_GOAL[0]:
            s = STEP * (do - dm) + TEMPO + dyn[0][wm] - dyn[1][wo]
        else:
            s = STEP * (do - dm) + TEMPO + dyn[1][wm] - dyn[0][wo]
    else:
        s = STEP * (do - dm) + TEMPO + WALL_VALUE[wm] - WALL_VALUE[wo]
    if EVAL2:
        # hiệu chỉnh học từ tự phân tích: giá trị k tường phụ thuộc quân bị chặn còn cách đích bao xa
        s += (_DA * (do - dm) + _DB + _DV[wm][_BK[do if do < 100 else 99]]
              - _DV[wo][_BK[dm if dm < 100 else 99]])
    if wo == 0 and dm <= do:
        s += RACE_BONUS        # đối thủ không thể làm mình chậm lại nữa
    elif wm == 0 and dm > do:
        s -= RACE_BONUS        # mình không thể làm đối thủ chậm lại nữa
    if UNANSWERED_WALL:
        # (A) tường "không bị đáp trả": bên kia đã hết tường thì không chặn lại được, mỗi
        # tường còn giữ có thể dựng mê cung sát đích -> đáng giá hơn một bước
        if wm == 0:
            s -= UNANSWERED_WALL * wo
        elif wo == 0:
            s += UNANSWERED_WALL * wm
    return s


def _jumps(o, U, D, L, R):
    """Các bước "nhảy qua" quân o: list (ô đứng cạnh o, các ô đáp xuống)."""
    js = []
    if o & D:                          # đứng dưới o, nhảy lên
        dest = (o >> 9) if o & U else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o << 9, dest))
    if o & U:                          # đứng trên o, nhảy xuống
        dest = (o << 9) if o & D else (((o >> 1) if o & L else 0) | ((o << 1) if o & R else 0))
        if dest:
            js.append((o >> 9, dest))
    if o & L:                          # đứng bên trái o, nhảy sang phải
        dest = (o << 1) if o & R else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o >> 1, dest))
    if o & R:                          # đứng bên phải o, nhảy sang trái
        dest = (o >> 1) if o & L else (((o >> 9) if o & U else 0) | ((o << 9) if o & D else 0))
        if dest:
            js.append((o << 1, dest))
    return js


def _reach_mask(o, goal, U, D, L, R):
    """Tập ô có thể tới hàng `goal` khi quân o đứng yên mãi (được nhảy qua o).

    BFS ngược từ đích, không đi vào ô o; cạnh "nhảy" m -> dest được xét ngược:
    nếu dest tới được đích thì m cũng tới được.
    """
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
    """True nếu quân a KHÔNG thể tới đích khi quân o đứng yên mãi.

    Luật cho phép đứng yên, nên nếu o đứng đúng "cửa ải" duy nhất thì bên kia
    chỉ có thể hòa -> nhận ra thế "chặn cửa". Kết quả được nhớ theo (o, tường).
    """
    key = (o, hw, vw, goal)
    m = _BCACHE.get(key)
    if m is None:
        m = _reach_mask(o, goal, U, D, L, R)
        _BCACHE[key] = m
    return not (m & a)


def _may_block(a, o, goal_row, d):
    """Điều kiện cần để o chặn được a: o nằm trên một đường ngắn nhất của a.

    Kiểm tra nhanh bằng khoảng cách Manhattan (cận dưới của quãng đường thật).
    """
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


PARK = True           # nhận ra sớm thế "đỗ xe": một quân tới kịp ô cửa ải duy nhất trước quân kia
LEAF_BLOCK = True     # kiểm tra "chặn cửa" cả ở nút lá (trước đây chỉ ở nút sát lá)
PARK_DETOUR = True    # (B) đỗ xe cũng tính khi đường vòng của nạn nhân >= đường về đích của bên đỗ
_PCACHE = {}          # nhớ độ dài đường vòng theo (nạn nhân, ô đỗ, đích, tường)


def _detour(vic, f, gv, U, D, L, R, hw, vw):
    """Đường ngắn nhất của vic tới đích khi có quân đứng yên ở ô f (có nhảy / đi chéo)."""
    key = (vic, f, gv, hw, vw)
    v = _PCACHE.get(key)
    if v is None:
        if len(_PCACHE) > 50000:
            _PCACHE.clear()
        v = _PCACHE[key] = _static_path(vic, f, gv, U, D, L, R)[0]
    return v


def _standoff(vic, f, gv, gb, vic_walls, U, D, L, R, hw, vw, dv):
    """Quân đứng yên ở ô f có cầm hòa được quân vic không?

    - vic hết hẳn đường khi f bị chiếm (có tính nhảy) -> hòa (như redvelvet); hoặc
    - (B) vic đã hết tường và đường vòng của vic >= đường về đích của quân ở f: vic đi vòng
      thì quân kia bỏ chỗ chạy về thắng, quân kia rời chỗ thì vic lọt qua -> giằng co, hòa.
    """
    if _blocked(vic, f, gv, U, D, L, R, hw, vw, dv):
        return True
    if not PARK_DETOUR or vic_walls or not gb:
        return False
    db = _layers(gb, f, U, D, L, R, hw, vw)[1]
    return db >= 0 and _detour(vic, f, gv, U, D, L, R, hw, vw) >= db


def _steps(a, target, U, D, L, R, cap=12):
    """Số bước ít nhất (bỏ qua quân kia) từ ô a tới ô target; INF nếu quá cap."""
    if a & target:
        return 0
    seen = front = a
    d = 0
    while d < cap:
        d += 1
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        if not front:
            return INF
        if front & target:
            return d
        seen |= front
    return INF


def _can_park(blk, vic, gv, dv, first_is_blk, U, D, L, R, hw, vw, gb=0, vic_walls=1):
    """Quân blk có tới kịp một "cửa ải duy nhất" trên đường của quân vic (đích gv, cách dv
    bước) trước khi vic đi qua không? Cửa ải = ô mọi đường ngắn nhất đều qua VÀ nếu blk
    đứng đó mãi thì vic không còn đường nào (có tính nhảy) -> blk đứng im là hòa."""
    ent = _dag(vic, gv, U, D, L, R, hw, vw)
    F = ent[2]
    if F is None:
        return False
    for k in range(ent[0] - 1, 0, -1):
        f = F[k]
        if f & (f - 1) or f == blk:
            continue
        t_v = ent[0] - k                      # vic cần t_v bước để tới ô f
        if _may_block_cell(blk, f, t_v) and _standoff(vic, f, gv, gb, vic_walls, U, D, L, R, hw, vw, dv):
            t_b = _steps(blk, f, U, D, L, R, t_v)
            if t_b <= t_v if first_is_blk else t_b < t_v:
                return True
    return False


def _may_block_cell(blk, f, t_v):
    ib = blk.bit_length() - 1
    jf = f.bit_length() - 1
    dr = _RC_R[ib] - _RC_R[jf]
    dc = _RC_C[ib] - _RC_C[jf]
    return (dr if dr >= 0 else -dr) + (dc if dc >= 0 else -dc) <= t_v


def _evalb(dm, do, wm, wo, me, op, gm, go, U, D, L, R, hw, vw):
    """Đánh giá tĩnh có xét thế "chặn cửa" (bên đang thắng bị chặn -> hòa)."""
    s = _static(dm, do, wm, wo, gm)
    if _BLOCK[0] and (s > BLOCK_MIN_S or s < -BLOCK_MIN_S):
        if s > 0:
            if (_may_block(me, op, 0 if gm & 1 else 8, dm)
                    and _standoff(me, op, gm, go, wm, U, D, L, R, hw, vw, dm)):
                return _draw(gm)
            if PARK and _can_park(op, me, gm, dm, False, U, D, L, R, hw, vw, go, wm):
                return _draw(gm)       # đối thủ (đang thua) sẽ kịp đỗ xe -> thực chất hòa
        elif s < 0:
            if (_may_block(op, me, 0 if go & 1 else 8, do)
                    and _standoff(op, me, go, gm, wo, U, D, L, R, hw, vw, do)):
                return _draw(gm)
            if PARK and _can_park(me, op, go, do, True, U, D, L, R, hw, vw, gm, wo):
                d = _draw(gm)          # mình (đang thua) kịp đỗ xe -> ép hòa nếu tốt hơn thua
                if d > s:
                    return d
    return s


def _wall_list(hw, vw):
    """Đổi mặt nạ neo tường sang danh sách bit (dùng khi cần)."""
    out = []
    for mask, base in ((hw, H_BASE), (vw, V_BASE)):
        while mask:
            low = mask & -mask
            out.append(base + low.bit_length() - 1)
            mask ^= low
    return out


# ---------------------------------------------------------------------------
# Sinh nước đi cho một nút
#   Mỗi "con" là tuple:
#   (điểm sắp xếp, mã nước, a', U', D', L', R', hw', vw', wa', d_a', d_b')
#   Điểm sắp xếp = đánh giá tĩnh sau nước đi, theo góc nhìn bên vừa đi.
# ---------------------------------------------------------------------------
def _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    """Các nước di chuyển quân. Trả về (mã nước thắng ngay hoặc None, list con)."""
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
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
        out.append((-_evalb(db, nd, wb, wa, b, x, gb, ga, U, D, L, R, hw, vw), x.bit_length() - 1,
                    x, U, D, L, R, hw, vw, wa, nd, db))
    return None, out


def _pawn_nd(x, ga, U, D, L, R, lay_m, dm):
    """Quãng đường mới của quân sau khi đi tới ô x (tra lớp BFS ngược, như _gen_pawns)."""
    nlay = len(lay_m)
    hi = dm + 3 if dm + 3 <= nlay else nlay
    for k in range(dm - 2 if dm >= 2 else 0, hi):
        if x & lay_m[k]:
            return k
    return _dist(x, ga, U, D, L, R)


def _pawn_children(pm, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db):
    """Như _gen_pawns nhưng dùng danh sách nước đi quân đã có (không ô nào về đích)."""
    out = []
    nlay = len(lay_m)
    lo = dm - 2 if dm >= 2 else 0
    hi = dm + 3 if dm + 3 <= nlay else nlay
    for x in pm:
        nd = -1
        for k in range(lo, hi):
            if x & lay_m[k]:
                nd = k
                break
        if nd < 0:
            nd = _dist(x, ga, U, D, L, R)
        out.append((-_evalb(db, nd, wb, wa, b, x, gb, ga, U, D, L, R, hw, vw), x.bit_length() - 1,
                    x, U, D, L, R, hw, vw, wa, nd, db))
    return out


def _opp_walls(b, gb, U, D, L, R, hw, vw):
    """Danh sách tường ứng viên chống quân b (đối thủ), có bộ nhớ đệm.

    Chỉ phụ thuộc (vị trí b, tường trên bàn) - KHÔNG phụ thuộc quân của mình - nên
    dùng lại được cho rất nhiều nút. Sắp xếp giảm dần theo quãng đường mới của b.
    Trả về (quãng đường hiện tại của b, mảng gọn các số ndo*1024 + mã tường) - lưu
    gọn để bộ nhớ đệm không phình to (mặt nạ tường tính lại khi cần, rất rẻ).
    """
    key = (b, gb, hw, vw)
    ent = _WCACHE.get(key)
    if ent is not None:
        return ent
    do, lay_o, F, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    lst = []
    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            if is_h:
                e1 = w | (w << 1)
                e2 = (w << 9) | (w << 10)
                D2 = D & ~e1
                U2 = U & ~e2
                L2 = L
                R2 = R
                hw2 = hw | w
                vw2 = vw
                code = H_BASE + w.bit_length() - 1
                src_ = (od & e1) | (ou & e2)
            else:
                e1 = w | (w << 9)
                e2 = (w << 1) | (w << 10)
                R2 = R & ~e1
                L2 = L & ~e2
                U2 = U
                D2 = D
                hw2 = hw
                vw2 = vw | w
                code = V_BASE + w.bit_length() - 1
                src_ = (orr & e1) | (ol & e2)
            ndo = _newdist(b, gb, do, lay_o, F, src_, U2, D2, L2, R2, hw2, vw2)
            if ndo >= INF:
                continue
            lst.append((ndo, code))
    lst.sort(key=lambda t: -t[0])
    ent = (do, array("i", [t[0] * 1024 + t[1] for t in lst]))
    _WCACHE[key] = ent
    return ent


def _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, top, defensive,
               stop_at=None, floor=-WIN * 4):
    """Các bức tường đáng xét (nhanh): dùng danh sách đã sắp xếp của _opp_walls.

    Mẹo cắt chính xác: tường không thể làm mình đi NGẮN hơn, nên điểm của mọi tường
    còn lại <= điểm "nếu mình không bị ảnh hưởng" của tường đang xét (danh sách xếp
    theo quãng đường mới của đối thủ giảm dần) -> dừng sớm mà không sai kết quả.
    """
    if defensive:
        return _gen_walls_full(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm,
                               top, defensive, stop_at)
    do, lst = _opp_walls(b, gb, U, D, L, R, hw, vw)
    if not lst:
        return []
    dm, lay_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    wa2 = wa - 1
    out = []
    best = floor          # (nút sát lá) chỉ cần tường tốt hơn nước tốt nhất đã có
    best_item = None
    thr = -WIN * 4
    for v in lst:
        ndo = v >> 10
        bound = -_static(ndo, dm, wb, wa2, gb)
        if stop_at is not None:
            if bound <= best:
                break
        elif top is not None and len(out) >= top and bound <= thr:
            break
        code = v & 1023
        if code < V_BASE:
            w = 1 << (code - H_BASE)
            e1 = w | (w << 1)
            e2 = (w << 9) | (w << 10)
            U2 = U & ~e2
            D2 = D & ~e1
            L2 = L
            R2 = R
            hw2 = hw | w
            vw2 = vw
            src_ = (md & e1) | (mu & e2)
        else:
            w = 1 << (code - V_BASE)
            e1 = w | (w << 9)
            e2 = (w << 1) | (w << 10)
            R2 = R & ~e1
            L2 = L & ~e2
            U2 = U
            D2 = D
            hw2 = hw
            vw2 = vw | w
            src_ = (mr & e1) | (ml & e2)
        if src_:
            ndm = _newdist(a, ga, dm, lay_m, Fm, src_, U2, D2, L2, R2, hw2, vw2)
            if ndm >= INF:
                continue
            sc = -_static(ndo, ndm, wb, wa2, gb)
        else:
            ndm = dm
            sc = bound
        item = (sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo)
        if stop_at is not None:
            if sc > best:
                best = sc
                best_item = item
                if sc >= stop_at:
                    break
        else:
            out.append(item)
            if top is not None and len(out) >= top:
                out.sort(reverse=True)
                del out[top:]
                thr = out[-1][0]
    if stop_at is not None:
        return [best_item] if best_item is not None else []
    out.sort(reverse=True)
    if top is not None and len(out) > top:
        del out[top:]
    return out


def _gen_walls_full(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, top, defensive,
                    stop_at=None):
    """Các bức tường đáng xét, đã sắp xếp giảm dần theo lợi ích, tối đa `top`.

    Ứng viên = tường chặn ít nhất một cạnh trên một đường ngắn nhất của đối thủ.
    `defensive`: thêm tường chiếm chỗ những tường đối thủ có thể dùng để chặn mình.
    """
    mu, md, ml, mr = _sp_edges(a, lay_m, dm, U, D, L, R)
    lay_o, do = _layers(gb, b, U, D, L, R, hw, vw)
    ou, od, ol, orr = _sp_edges(b, lay_o, do, U, D, L, R)
    avail_h = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    avail_v = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    cand_h = ((ou >> 9) | (ou >> 10) | od | (od >> 1)) & avail_h
    cand_v = (orr | (orr >> 9) | (ol >> 1) | (ol >> 10)) & avail_v
    if defensive:
        # Tường "phòng thủ": chiếm trước chỗ (chồng lấn / cắt ngang) của những tường
        # mà đối thủ có thể dùng để chặn đường ngắn nhất của mình.
        threat_h = ((mu >> 9) | (mu >> 10) | md | (md >> 1)) & ANCHOR
        threat_v = (mr | (mr >> 9) | (ml >> 1) | (ml >> 10)) & ANCHOR
        cand_h |= ((threat_h << 1) | (threat_h >> 1) | threat_v) & avail_h
        cand_v |= ((threat_v << 9) | (threat_v >> 9) | threat_h) & avail_v
    wa2 = wa - 1
    walls = []
    dcache = _DCACHE
    for is_h, mask in ((1, cand_h), (0, cand_v)):
        while mask:
            w = mask & -mask
            mask ^= w
            if is_h:
                e1 = w | (w << 1)
                e2 = (w << 9) | (w << 10)
                D2 = D & ~e1
                U2 = U & ~e2
                L2 = L
                R2 = R
                touch_me = (md & e1) or (mu & e2)
                touch_op = (od & e1) or (ou & e2)
                hw2 = hw | w
                vw2 = vw
                code = H_BASE + w.bit_length() - 1
            else:
                e1 = w | (w << 9)
                e2 = (w << 1) | (w << 10)
                R2 = R & ~e1
                L2 = L & ~e2
                U2 = U
                D2 = D
                touch_me = (mr & e1) or (ml & e2)
                touch_op = (orr & e1) or (ol & e2)
                hw2 = hw
                vw2 = vw | w
                code = V_BASE + w.bit_length() - 1
            if touch_op:
                ck = (b, gb, hw2, vw2)
                ndo = dcache.get(ck)
                if ndo is None:
                    ndo = _dist(b, gb, U2, D2, L2, R2)
                    dcache[ck] = ndo
                if ndo >= INF:
                    continue
            else:
                ndo = do
            if touch_me:
                ck = (a, ga, hw2, vw2)
                ndm = dcache.get(ck)
                if ndm is None:
                    ndm = _dist(a, ga, U2, D2, L2, R2)
                    dcache[ck] = ndm
                if ndm >= INF:
                    continue
            else:
                ndm = dm
            sc = -_static(ndo, ndm, wb, wa2, gb)
            if stop_at is not None and sc >= stop_at:
                # đủ để cắt tỉa ở nút sát lá: khỏi xét các tường còn lại
                return [(sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo)]
            walls.append((sc, code, a, U2, D2, L2, R2, hw2, vw2, wa2, ndm, ndo))
    walls.sort(reverse=True)
    if top is not None and len(walls) > top:
        del walls[top:]
    return walls


def _wall_child(code, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw):
    """Dựng con cho một mã tường cụ thể (từ bảng chuyển vị / killer); None nếu sai luật."""
    if wa <= 0:
        return None
    do, lay_o, Fo, ou, od, ol, orr = _dag(b, gb, U, D, L, R, hw, vw)
    dm, lay_m, Fm, mu, md, ml, mr = _dag(a, ga, U, D, L, R, hw, vw)
    if code >= V_BASE:
        w = 1 << (code - V_BASE)
        if not (w & ANCHOR) or (w & (vw | (vw << 9) | (vw >> 9) | hw)):
            return None
        e1 = w | (w << 9)
        e2 = (w << 1) | (w << 10)
        R2 = R & ~e1
        L2 = L & ~e2
        U2, D2, hw2, vw2 = U, D, hw, vw | w
        so = (orr & e1) | (ol & e2)
        sm = (mr & e1) | (ml & e2)
    else:
        w = 1 << (code - H_BASE)
        if not (w & ANCHOR) or (w & (hw | (hw << 1) | (hw >> 1) | vw)):
            return None
        e1 = w | (w << 1)
        e2 = (w << 9) | (w << 10)
        D2 = D & ~e1
        U2 = U & ~e2
        L2, R2, hw2, vw2 = L, R, hw | w, vw
        so = (od & e1) | (ou & e2)
        sm = (md & e1) | (mu & e2)
    ndo = _newdist(b, gb, do, lay_o, Fo, so, U2, D2, L2, R2, hw2, vw2) if so else do
    if ndo >= INF:
        return None
    ndm = _newdist(a, ga, dm, lay_m, Fm, sm, U2, D2, L2, R2, hw2, vw2) if sm else dm
    if ndm >= INF:
        return None
    return (0, code, a, U2, D2, L2, R2, hw2, vw2, wa - 1, ndm, ndo)


# ---------------------------------------------------------------------------
# Bảng tàn cuộc (endgame tablebase) khi CẢ HAI đã hết tường
#   Lúc đó bàn cờ cố định, chỉ còn 81 x 81 x 2 = 13122 thế cờ (hai quân + bên đi),
#   nên giải TRỌN được bằng phân tích ngược (retrograde analysis) như tablebase
#   cờ vua: xuất phát từ các thế về đích ngay, lan ngược ra mọi thế khác.
#   Kết quả chính xác tuyệt đối: thắng/thua sau đúng bao nhiêu nước, hay hòa
#   (cả hai đứng chờ "giữ thế đối mặt" - không ai dám lại gần để bị nhảy qua).
#   Giá trị theo góc nhìn bên đi: +k = thắng, nước về đích là nước thứ k (tính cả
#   nước đối thủ); -k = thua, đối thủ về đích ở nước thứ k; 0 = hòa.
#   N = quân có đích hàng 0, S = quân có đích hàng 8; chỉ số = ô_N * 81 + ô_S.
# ---------------------------------------------------------------------------
def _tb_graph(U, D, L, R):
    """Đồ thị nước đi của quân trên một bàn cố định: (láng giềng, nước khi kề quân
    kia (có nhảy), nhảy ngược)."""
    nb = []
    for c in range(81):
        m = 1 << c
        lst = []
        if m & U:
            lst.append(c - 9)
        if m & D:
            lst.append(c + 9)
        if m & L:
            lst.append(c - 1)
        if m & R:
            lst.append(c + 1)
        nb.append(lst)

    def jumps(x, y):
        """Ô đáp khi quân ở x nhảy qua quân ở y (kề nhau), đúng luật arena."""
        my = 1 << y
        dd = y - x
        if dd == -9:
            if my & U:
                return (y - 9,)
            return tuple(c for c, o in ((y - 1, my & L), (y + 1, my & R)) if o)
        if dd == 9:
            if my & D:
                return (y + 9,)
            return tuple(c for c, o in ((y - 1, my & L), (y + 1, my & R)) if o)
        if dd == -1:
            if my & L:
                return (y - 1,)
            return tuple(c for c, o in ((y - 9, my & U), (y + 9, my & D)) if o)
        if my & R:
            return (y + 1,)
        return tuple(c for c, o in ((y - 9, my & U), (y + 9, my & D)) if o)

    adjmv = {}      # nước đi của x khi y đứng kề (gồm cả nhảy)
    rj = {}         # nhảy ngược: (y, ô đáp) -> các ô xuất phát
    for x in range(81):
        for y in nb[x]:
            j = jumps(x, y)
            adjmv[x * 81 + y] = [c for c in nb[x] if c != y] + list(j)
            for z in j:
                rj.setdefault(y * 81 + z, []).append(x)
    return nb, adjmv, rj


def _tb_solve(U, D, L, R):
    nb, adjmv, rj = _tb_graph(U, D, L, R)
    NN = 6561
    valN = [0] * NN
    valS = [0] * NN
    cntN = [0] * NN
    cntS = [0] * NN
    queue = []
    qa = queue.append
    # khởi tạo: thế về đích ngay (thắng sau 1 nước) và số nước đi của mỗi thế
    for n in range(9, 81):
        base = nb[n]
        bwin = n < 18 and (n - 9) in base
        bcnt = len(base) + 1
        nadj = set(base)
        off = n * 81
        for s in range(0, 72):
            if s == n:
                continue
            i = off + s
            if s in nadj:
                mv = adjmv[n * 81 + s]
                if any(c < 9 for c in mv):
                    valN[i] = 1
                    qa(i)
                else:
                    cntN[i] = len(mv) + 1
            elif bwin:
                valN[i] = 1
                qa(i)
            else:
                cntN[i] = bcnt
    for s in range(0, 72):
        base = nb[s]
        bwin = s >= 63 and (s + 9) in base
        bcnt = len(base) + 1
        sadj = set(base)
        for n in range(9, 81):
            if n == s:
                continue
            i = n * 81 + s
            if n in sadj:
                mv = adjmv[s * 81 + n]
                if any(c >= 72 for c in mv):
                    valS[i] = 1
                    qa(NN + i)
                else:
                    cntS[i] = len(mv) + 1
            elif bwin:
                valS[i] = 1
                qa(NN + i)
            else:
                cntS[i] = bcnt
    # lan ngược theo thứ tự quãng đường tăng dần (BFS): thế nào có MỘT nước dẫn tới
    # thế thua của đối thủ là thắng; thế nào MỌI nước (kể cả đứng yên) đều dẫn tới
    # thế thắng của đối thủ là thua; còn lại là hòa.
    head = 0
    while head < len(queue):
        q = queue[head]
        head += 1
        if q < NN:
            i = q
            v = valN[i]
            n, s = divmod(i, 81)
            off = n * 81
            extra = rj.get(n * 81 + s)
            lst = nb[s] + [s] if extra is None else nb[s] + [s] + extra
            for sp in lst:
                if sp == n or sp >= 72:
                    continue
                j = off + sp
                if valS[j]:
                    continue
                if v < 0:
                    valS[j] = 1 - v
                    qa(NN + j)
                else:
                    c = cntS[j] - 1
                    cntS[j] = c
                    if c == 0:
                        valS[j] = -1 - v
                        qa(NN + j)
        else:
            i = q - NN
            v = valS[i]
            n, s = divmod(i, 81)
            extra = rj.get(s * 81 + n)
            lst = nb[n] + [n] if extra is None else nb[n] + [n] + extra
            for np_ in lst:
                if np_ == s or np_ < 9:
                    continue
                j = np_ * 81 + s
                if valN[j]:
                    continue
                if v < 0:
                    valN[j] = 1 - v
                    qa(j)
                else:
                    c = cntN[j] - 1
                    cntN[j] = c
                    if c == 0:
                        valN[j] = -1 - v
                        qa(j)
    return array("h", valN), array("h", valS)


def _reach(goal, U, D, L, R):
    """Các ô có đường tới hàng đích (bỏ qua quân - đúng luật kiểm tra tường của arena)."""
    seen = front = goal
    while front:
        front = (((front & U) >> 9) | ((front & D) << 9)
                 | ((front & L) >> 1) | ((front & R) << 1)) & ~seen
        seen |= front
    return seen


def _walls_of(U, D, L, R, hw, vw):
    """Mọi chỗ đặt tường không chồng/cắt tường cũ: (hw', vw', U', D', L', R')."""
    out = []
    m = ANCHOR & ~(hw | (hw << 1) | (hw >> 1) | vw)
    while m:
        w = m & -m
        m ^= w
        out.append((hw | w, vw, U & ~((w << 9) | (w << 10)), D & ~(w | (w << 1)), L, R))
    m = ANCHOR & ~(vw | (vw << 9) | (vw >> 9) | hw)
    while m:
        w = m & -m
        m ^= w
        out.append((hw, vw | w, U, D, L & ~((w << 1) | (w << 10)), R & ~(w | (w << 9))))
    return out


def _tb1_build(U, D, L, R, hw, vw, hN, until):
    """Dựng bảng "1 tường vs 0 tường" (bên H còn 1 tường, bên kia hết) cho bàn hiện tại.

    Trước hết cần bảng 0-0 của MỌI bàn sau khi H đặt nốt bức tường cuối (dựng dần,
    có thể qua nhiều lượt, dừng khi hết thời gian `until`). Sau đó phân tích ngược
    một lần nữa: ngoài đi quân / đứng yên, bên H còn "lối ra" là đặt tường w ->
    giá trị lấy từ bảng 0-0 của bàn có thêm w. Trả về True nếu đã dựng xong.
    """
    ws = _walls_of(U, D, L, R, hw, vw)
    for hw2, vw2, U2, D2, L2, R2 in ws:
        if (hw2, vw2) not in _TBC:
            if perf_counter() > until:
                return False
            _TBC[(hw2, vw2)] = _tb_solve(U2, D2, L2, R2)
    if perf_counter() > until:
        return False
    nb, adjmv, rj = _tb_graph(U, D, L, R)
    NN = 6561
    val = [[0] * NN, [0] * NN]      # [N đi, S đi]
    cnt = [[0] * NN, [0] * NN]
    buckets = {}

    def push(d, ev):
        bk = buckets.get(d)
        if bk is None:
            buckets[d] = [ev]
        else:
            bk.append(ev)

    for n in range(9, 81):
        base = nb[n]
        bwin = n < 18 and (n - 9) in base
        bcnt = len(base) + 1
        nadj = set(base)
        off = n * 81
        for s in range(0, 72):
            if s == n:
                continue
            i = off + s
            if s in nadj:
                mv = adjmv[n * 81 + s]
                if any(c < 9 for c in mv):
                    push(1, (0, i, 1))
                else:
                    cnt[0][i] = len(mv) + 1
            elif bwin:
                push(1, (0, i, 1))
            else:
                cnt[0][i] = bcnt
    for s in range(0, 72):
        base = nb[s]
        bwin = s >= 63 and (s + 9) in base
        bcnt = len(base) + 1
        sadj = set(base)
        for n in range(9, 81):
            if n == s:
                continue
            i = n * 81 + s
            if n in sadj:
                mv = adjmv[s * 81 + n]
                if any(c >= 72 for c in mv):
                    push(1, (1, i, 1))
                else:
                    cnt[1][i] = len(mv) + 1
            elif bwin:
                push(1, (1, i, 1))
            else:
                cnt[1][i] = bcnt
    # lối ra: H đặt bức tường cuối -> thế 0-0 trên bàn mới, bên kia đi
    hs = 0 if hN else 1
    cH = cnt[hs]
    decs = {}
    for hw2, vw2, U2, D2, L2, R2 in ws:
        rN = _reach(ROW[0], U2, D2, L2, R2)
        rS = _reach(ROW[8], U2, D2, L2, R2)
        t = _TBC[(hw2, vw2)][1 - hs]
        for n in range(9, 81):
            if not (rN >> n) & 1:
                continue
            off = n * 81
            for s in range(0, 72):
                if s == n or not (rS >> s) & 1:
                    continue
                i = off + s
                if cH[i] == 0:
                    continue          # H về đích ngay được rồi
                cH[i] += 1            # thêm một lựa chọn: H chỉ thua nếu MỌI lựa chọn đều thua
                v = t[i]
                if v < 0:
                    push(1 - v, (hs, i, 1 - v))
                elif v > 0:
                    dl = decs.get(v)
                    if dl is None:
                        decs[v] = [i]
                    else:
                        dl.append(i)
    valN, valS = val
    cntN, cntS = cnt
    j = 1
    while buckets or decs:
        evs = buckets.pop(j, None)
        if evs:
            k = 0
            while k < len(evs):
                side, i, v = evs[k]
                k += 1
                arr = val[side]
                if arr[i]:
                    continue
                arr[i] = v
                n, s = divmod(i, 81)
                if side == 0:
                    off = n * 81
                    extra = rj.get(n * 81 + s)
                    lst = nb[s] + [s] if extra is None else nb[s] + [s] + extra
                    for sp in lst:
                        if sp == n or sp >= 72:
                            continue
                        jj = off + sp
                        if valS[jj]:
                            continue
                        if v < 0:
                            push(1 - v, (1, jj, 1 - v))
                        else:
                            c = cntS[jj] - 1
                            cntS[jj] = c
                            if c == 0:
                                push(v + 1, (1, jj, -1 - v))
                else:
                    extra = rj.get(s * 81 + n)
                    lst = nb[n] + [n] if extra is None else nb[n] + [n] + extra
                    for np_ in lst:
                        if np_ == s or np_ < 9:
                            continue
                        jj = np_ * 81 + s
                        if valN[jj]:
                            continue
                        if v < 0:
                            push(1 - v, (0, jj, 1 - v))
                        else:
                            c = cntN[jj] - 1
                            cntN[jj] = c
                            if c == 0:
                                push(v + 1, (0, jj, -1 - v))
        dl = decs.pop(j, None)
        if dl:
            arr = val[hs]
            for i in dl:
                if arr[i]:
                    continue
                c = cH[i] - 1
                cH[i] = c
                if c == 0:
                    push(j + 1, (hs, i, -(j + 1)))
        j += 1
    _TB1C[(hw, vw, hN)] = (array("h", valN), array("h", valS))
    return True


def _tb_score(v, ply, ga, da, db, turn, max_turns):
    """Đổi giá trị bảng tàn cuộc sang điểm tìm kiếm (theo góc nhìn bên đi)."""
    if v > 0:
        if turn + ply + v - 1 < max_turns:
            return WIN - (ply + v - 1)
    elif v < 0:
        if turn + ply - v - 1 < max_turns:
            return -(WIN - (ply - v - 1))
    # hòa: thêm chút ưu tiên cho bên đang dẫn trong cuộc đua (để còn cơ hội nếu
    # đối thủ đi sai), nhưng không bao giờ vượt quá +-4 quanh điểm hòa
    t = db - da
    if t > 4:
        t = 4
    elif t < -4:
        t = -4
    return _draw(ga) + t


# ---------------------------------------------------------------------------
# Tìm kiếm Negamax + Alpha-Beta (PVS, bảng chuyển vị, killer move)
# ---------------------------------------------------------------------------
class _Searcher:
    stop = [False]       # cờ dừng ngoài (chỉ luồng "nghĩ hộ" mới đặt riêng)

    def __init__(self, deadline, turn, max_turns):
        self.deadline = deadline
        self.turn = turn
        self.max_turns = max_turns
        self.nodes = 0
        self.killers = [[None, None] for _ in range(128)]
        self.path = set()
        self.tb_left = TB_MAX if TB else 0
        self.tb_until = deadline - TIME_LIMIT * (1.0 - TB_TIME)

    def search(self, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db,
               depth, alpha, beta, ply):
        self.nodes += 1
        if (self.nodes & 7) == 0 and (perf_counter() > self.deadline or self.stop[0]
                                      or (NODE_CAP and self.nodes > NODE_CAP)):
            raise _TimeUp()
        if self.turn + ply >= self.max_turns:
            return _draw(ga)               # hết giới hạn lượt -> hòa
        if (wa == 0 or wb == 0) and TB:
            # Bảng tàn cuộc: hết tường cả hai -> giá trị CHÍNH XÁC. Chỉ một bên hết
            # tường -> bên còn tường ít nhất cũng đạt được giá trị của bảng (cứ việc
            # không đặt tường nữa) -> dùng làm cận để cắt tỉa.
            both = wa == 0 and wb == 0
            exact = both
            t = None
            if not both and _TB1C:
                # bảng "1 tường vs 0": chính xác nếu bên còn tường còn đúng 1 tường,
                # còn nhiều hơn thì là cận (bên đó ít nhất làm được như khi chỉ dùng 1)
                t = _TB1C.get((hw, vw, (ga if wa else gb) & 1))
                if t is not None:
                    exact = wa + wb == 1
            if t is None:
                t = _TBC.get((hw, vw))
                if (t is None and self.tb_left > 0 and (both or ply <= TB_BOUND_PLY)
                        and perf_counter() < self.tb_until):
                    t = _tb_solve(U, D, L, R)
                    _TBC[(hw, vw)] = t
                    self.tb_left -= 1
            if t is not None:
                ia = a.bit_length() - 1
                ib = b.bit_length() - 1
                v = t[0][ia * 81 + ib] if ga & 1 else t[1][ib * 81 + ia]
                sc = _tb_score(v, ply, ga, da, db, self.turn, self.max_turns)
                if exact:
                    return sc
                if wb == 0:                # mình còn tường: sc là cận dưới
                    if sc >= beta or sc > MATE_ZONE:
                        return sc
                elif sc <= alpha or sc < -MATE_ZONE:   # mình hết tường: cận trên
                    return sc
        if depth <= 0:
            if LEAF_BLOCK and _BLOCK[0]:
                return _evalb(da, db, wa, wb, a, b, ga, gb, U, D, L, R, hw, vw)
            return _static(da, db, wa, wb, ga)

        key = (a, b, wa, wb, hw, vw, ga)
        if key in self.path or key in _HIST:
            return _draw(ga)               # lặp lại thế cũ -> coi như hòa
        entry = _TT.get(key)
        if entry is None and TT2:
            entry = _TT_OLD.get(key)
        tt_move = None
        if entry is not None:
            e_depth, e_score, e_flag, tt_move = entry
            if e_score > MATE_ZONE:
                e_score -= ply
            elif e_score < -MATE_ZONE:
                e_score += ply
            if e_depth >= depth:
                if e_flag == 0:
                    return e_score
                if e_flag == 1:
                    if e_score >= beta:
                        return e_score
                elif e_score <= alpha:
                    return e_score

        # các lớp BFS ngược tới đích của mình (theo bộ tường); quãng đường của mình
        # đã được tính chính xác ở nút cha nên khỏi tra lại
        lay_m = _LCACHE.get((ga, hw, vw))
        if lay_m is None:
            lay_m = _layers(ga, a, U, D, L, R, hw, vw)[0]
        dm = da
        if depth == 1 and not _BLOCK[0]:
            # Nút sát lá, chưa cần kiểm tra "chặn cửa": điểm của nước đi quân chỉ phụ
            # thuộc quãng đường mới -> chỉ cần tìm nước làm quãng đường ngắn nhất
            # (cùng kết quả với cách tính đầy đủ bên dưới, nhanh hơn).
            nlay = len(lay_m)
            lo = dm - 2 if dm >= 2 else 0
            hi = dm + 3 if dm + 3 <= nlay else nlay
            bnd = INF + 1
            bx = 0
            for x in _pawn_moves(a, b, U, D, L, R):
                if x & ga:
                    return WIN - ply       # về đích ngay
                nd = -1
                for k in range(lo, hi):
                    if x & lay_m[k]:
                        nd = k
                        break
                if nd < 0:
                    nd = _dist(x, ga, U, D, L, R)
                if nd < bnd:
                    bnd = nd
                    bx = x
            best = -_static(db, da, wb, wa, gb)
            best_move = PASS
            if bx:
                sc = -_static(db, bnd, wb, wa, gb)
                if sc > best:
                    best = sc
                    best_move = bx.bit_length() - 1
            pawns = None
        elif depth == 1:
            win, pawns = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
            if win is not None:
                return WIN - ply               # về đích ngay
        else:
            # Nút trong: chỉ sinh danh sách nước đi quân; các "con" (kèm đánh giá để
            # sắp xếp) chỉ dựng khi thật sự tới lượt xét - phần lớn nút bị cắt tỉa
            # ngay ở nước trong bảng chuyển vị nên khỏi tốn công.
            pm = _pawn_moves(a, b, U, D, L, R)
            for x in pm:
                if x & ga:
                    return WIN - ply           # về đích ngay
            pawns = None
        if depth == 1:
            # Nút sát lá: giá trị = nước tốt nhất theo đánh giá tĩnh.
            if pawns is not None:
                best = -_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw)
                best_move = PASS
                for k in pawns:
                    if k[0] > best:
                        best = k[0]
                        best_move = k[1]
            if best < beta and wa > 0:
                # thử trước tường đã nhớ (bảng chuyển vị / killer): hay cắt tỉa được ngay
                if FRONTIER_KILLERS:
                    for km in (tt_move, self.killers[ply][0], self.killers[ply][1]):
                        if km is not None and km >= H_BASE and km != PASS:
                            c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                sc = -_static(c[11], c[10], wb, c[9], gb)
                                if sc > best:
                                    best = sc
                                    best_move = km
                                    if best >= beta:
                                        break
                if best < beta:
                    walls = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                       lay_m, dm, 1, False, beta, best)
                    if walls and walls[0][0] > best:
                        best = walls[0][0]
                        best_move = walls[0][1]
            if best >= beta and best_move is not None and best_move >= H_BASE and best_move != PASS:
                kl = self.killers[ply]
                if kl[0] != best_move:
                    kl[1] = kl[0]
                    kl[0] = best_move
            _TT[key] = (1, _to_tt(best, ply), 1 if best >= beta else 0, best_move)
            return best

        self.path.add(key)
        orig_alpha = alpha
        best = -WIN * 2
        best_move = None
        nd = depth - 1
        np1 = ply + 1
        tried = set()
        first = True
        killers = self.killers[ply]
        cutoff = False
        stage = 0
        while stage < 5 and not cutoff:
            # Thứ tự xét: nước trong bảng chuyển vị -> killer -> di chuyển -> tường -> đứng yên
            if stage == 0:
                lst = []
                if tt_move is not None:
                    if tt_move == PASS:
                        lst = [(0, PASS, a, U, D, L, R, hw, vw, wa, da, db)]
                    elif tt_move < H_BASE:
                        x = 1 << tt_move
                        if x in pm:
                            lst = [(0, tt_move, x, U, D, L, R, hw, vw, wa,
                                    _pawn_nd(x, ga, U, D, L, R, lay_m, dm), db)]
                    else:
                        c = _wall_child(tt_move, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                        if c is not None:
                            lst = [c]
            elif stage == 1:
                lst = []
                if wa > 0:
                    for km in killers:
                        if km is not None and km not in tried:
                            c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                            if c is not None:
                                lst.append(c)
                    su = _SETUP[0]
                    if ply == 1 and su is not None and su[0] == ga:
                        for km in su[1]:
                            if km not in tried:
                                c = _wall_child(km, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw)
                                if c is not None:
                                    lst.append(c)
            elif stage == 2:
                lst = _pawn_children(pm, a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
                lst.sort(reverse=True)
            elif stage == 3:
                lst = _gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm,
                                 TOP_WALLS, False) if wa > 0 else ()
            else:
                lst = [(0, PASS, a, U, D, L, R, hw, vw, wa, da, db)]
            stage += 1
            for k in lst:
                code = k[1]
                if code in tried:
                    continue
                tried.add(code)
                if first:
                    sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], nd, -beta, -alpha, np1)
                    first = False
                else:
                    sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                      k[7], k[8], k[11], k[10], nd, -alpha - 1, -alpha, np1)
                    if alpha < sc < beta:
                        sc = -self.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                          k[6], k[7], k[8], k[11], k[10], nd, -beta,
                                          -alpha, np1)
                if sc > best:
                    best = sc
                    best_move = code
                    if sc > alpha:
                        alpha = sc
                        if alpha >= beta:
                            cutoff = True
                            if code >= H_BASE and code != PASS:
                                if killers[0] != code:
                                    killers[1] = killers[0]
                                    killers[0] = code
                            break
        self.path.discard(key)
        if best <= orig_alpha:
            flag = 2
        elif best >= beta:
            flag = 1
        else:
            flag = 0
        _TT[key] = (depth, _to_tt(best, ply), flag, best_move)
        return best


def _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn, max_turns, t0):
    """Đào sâu lặp ở gốc; trả về mã nước đi tốt nhất."""
    da = _dist(a, ga, U, D, L, R)
    db = _dist(b, gb, U, D, L, R)
    lay_m, dm = _layers(ga, a, U, D, L, R, hw, vw)
    win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
    if win is not None:
        return win, None
    if wa > 0:
        kids.extend(_gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                               lay_m, dm, None, True))
    kids.append((-_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw) - 1, PASS,
                 a, U, D, L, R, hw, vw, wa, da, db))
    kids.sort(key=lambda k: -k[0])
    best_move = kids[0][1]
    _STATE["depth"] = 1
    _STATE["score"] = kids[0][0]
    if len(kids) == 1:
        return best_move, kids[0]

    deadline = t0 + TIME_LIMIT - _STATE["margin"]
    s = _Searcher(deadline, turn, max_turns)
    depth = 1
    scores = {}
    while depth < 60:
        depth += 1
        it_start = perf_counter()
        alpha = -WIN * 2
        beta = WIN * 2
        cur_best = None
        cur_best_sc = -WIN * 2
        new_scores = {}
        try:
            first = True
            for k in kids:
                if first:
                    sc = -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                   k[7], k[8], k[11], k[10], depth - 1, -beta, -alpha, 1)
                    first = False
                else:
                    sc = -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                                   k[7], k[8], k[11], k[10], depth - 1, -alpha - 1,
                                   -alpha, 1)
                    if sc > alpha:
                        # Đã chắc chắn tốt hơn nước tốt nhất hiện tại: ghi nhận
                        # ngay, phòng khi hết giờ trong lúc tính lại cho chính xác.
                        prev_best, prev_sc = cur_best, cur_best_sc
                        cur_best, cur_best_sc = k[1], sc
                        sc = -s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5],
                                       k[6], k[7], k[8], k[11], k[10], depth - 1,
                                       -beta, -alpha, 1)
                        cur_best, cur_best_sc = prev_best, prev_sc
                new_scores[k[1]] = sc
                if sc > cur_best_sc:
                    cur_best_sc = sc
                    cur_best = k[1]
                    if sc > alpha:
                        alpha = sc
        except _TimeUp:
            # Lượt lặp dở dang: nước đầu tiên (tốt nhất cũ) đã xét xong thì dùng
            # kết quả tốt nhất tìm được trong lượt lặp này.
            if cur_best is not None:
                best_move = cur_best
            break
        best_move = cur_best
        scores = new_scores
        _STATE["root"] = new_scores
        _STATE["depth"] = depth
        _STATE["nodes"] = s.nodes
        _STATE["score"] = cur_best_sc
        # Lượt lặp sau: nước tốt nhất lên đầu, còn lại theo điểm.
        kids.sort(key=lambda k: -scores.get(k[1], -WIN * 3))
        if abs(cur_best_sc) > MATE_ZONE and WIN - abs(cur_best_sc) <= depth:
            break  # thắng/thua chắc chắn đã được chứng minh trong tầm tìm kiếm
        now = perf_counter()
        if now + (now - it_start) * ITER_FACTOR > deadline:
            break  # không đủ thời gian cho độ sâu tiếp theo
    for k in kids:
        if k[1] == best_move:
            return best_move, k
    return best_move, None


# ---------------------------------------------------------------------------
# Giao tiếp với arena
# ---------------------------------------------------------------------------
def _bit(rc):
    return 1 << (rc[0] * 9 + rc[1])


def _fallback(observation):
    """Phương án dự phòng: đi theo bản đồ khoảng cách của arena."""
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
    global _TT, _TT_OLD
    you = observation["you"]
    opp = observation["opponent"]
    a = _bit(you["position"])
    b = _bit(opp["position"])
    ga = ROW[you["goal_row"]]
    gb = ROW[opp["goal_row"]]
    wa = you["walls_remaining"]
    wb = opp["walls_remaining"]
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
    wa = max(0, min(10, wa))
    wb = max(0, min(10, wb))

    turn = observation.get("turn", 0)
    if turn < _STATE["last_turn"]:
        _TT.clear()          # ván mới
        _TT_OLD.clear()
        _HIST.clear()
        _STATE["sets_up"] = False
        _STATE["seen_w"] = None
    elif len(_TT) > TT_CAP:
        # bảng đầy: chuyển sang "thế hệ cũ" thay vì xóa sạch, vẫn tra cứu được
        _TT_OLD = _TT if TT2 else {}
        _TT = {}
    _STATE["last_turn"] = turn
    max_turns = observation.get("max_turns", 500)

    _BLOCK[0] = (bin(hw).count("1") + bin(vw).count("1")) >= BLOCK_MIN_WALLS
    if len(_BCACHE) > 20000:
        _BCACHE.clear()      # kết quả "chặn cửa" chỉ phụ thuộc (quân chặn, tường): giữ qua các lượt
    # giới hạn kích thước các bộ nhớ đệm (giữ tiến trình quanh ~100 MB)
    if len(_DCACHE) > 60000:
        _DCACHE.clear()
    if len(_LCACHE) > 10000:
        _LCACHE.clear()
    if len(_WCACHE) > 20000:
        _WCACHE.clear()
    if len(_GCACHE) > 10000:
        _GCACHE.clear()
    if _TBC:
        # tường không bao giờ bị gỡ: bảng nào thiếu một bức tường đang có trên bàn
        # thì không còn dùng được nữa
        for k in [k for k in _TBC if (k[0] & hw) != hw or (k[1] & vw) != vw]:
            del _TBC[k]
    if _TB1C:
        for k in [k for k in _TB1C if (k[0] & hw) != hw or (k[1] & vw) != vw]:
            del _TB1C[k]
    _ROOT_GOAL[0] = ga
    _SETUP[0] = None
    if SETUP_WALLS:
        try:
            seen = _STATE.get("seen_w")
            if seen is not None and not _STATE.get("sets_up"):
                # tường mới của đối thủ mà KHÔNG làm mình chậm đi -> nó chơi kiểu dựng bẫy
                d_now = _dist(a, ga, U, D, L, R)
                for is_h, mask in ((True, hw & ~seen[0]), (False, vw & ~seen[1])):
                    while mask:
                        w = mask & -mask
                        mask ^= w
                        U_, D_, L_, R_ = _masks(hw & ~w, vw) if is_h else _masks(hw, vw & ~w)
                        if _dist(a, ga, U_, D_, L_, R_) >= d_now:
                            _STATE["sets_up"] = True
            if (SETUP_ALWAYS or _STATE.get("sets_up")) and wb >= 2:
                su = _setup_walls(a, ga, b, gb, wb, U, D, L, R, hw, vw)
                if su:
                    _SETUP[0] = (gb, su)
        except Exception:
            _SETUP[0] = None
    _DYN[0] = None
    if DYN_WALLS and (wa or wb):
        try:
            _DYN[0] = _dyn_tables(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                  observation.get("player_id", 0) == 0)
        except Exception:
            _DYN[0] = None
    if TB and TB1 and (wa == 0) != (wb == 0) and wa + wb <= TB1_MAXK:
        hN = (ga if wa else gb) & 1
        if (hw, vw, hN) not in _TB1C:
            _tb1_build(U, D, L, R, hw, vw, hN, t0 + TIME_LIMIT * TB1_TIME)
    _HIST.add((a, b, wa, wb, hw, vw, ga))
    code, kid = _root_search(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, turn,
                             max_turns, t0)
    _STATE["seen_w"] = (kid[7], kid[8]) if kid is not None else (hw, vw)
    _PON["args"] = None
    if kid is not None:
        # ghi nhớ thế cờ sau nước đi của mình (đối thủ tới lượt)
        _HIST.add((b, kid[2], wb, kid[9], kid[7], kid[8], gb))
        if PONDER and code != PASS and kid[2] & ga == 0:
            _PON["args"] = (b, kid[2], wb, kid[9], gb, ga, kid[3], kid[4], kid[5],
                            kid[6], kid[7], kid[8], kid[11], kid[10], turn + 1, max_turns)
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
    kind = action.get("type")
    if kind == "pass":
        return True
    if kind == "move":
        return list(action["to"]) in [list(m) for m in observation["legal_moves"]]
    if kind == "wall":
        for w in observation["legal_walls"]:
            if (w["orientation"] == action["orientation"] and w["row"] == action["row"]
                    and w["col"] == action["col"]):
                return True
    return False


PONDER = os.environ.get("PONDER", "1") == "1"
TT_CAP = int(os.environ.get("TTCAP", TT_CAP))


def _bkey(o):
    """Khóa thế cờ cho sách khai cuộc: (quân mình, quân đối thủ, tường còn lại, tường trên bàn)."""
    y, p = o["you"], o["opponent"]
    return (y["goal_row"], tuple(y["position"]), tuple(p["position"]), y["walls_remaining"],
            p["walls_remaining"], tuple(sorted(map(tuple, o["walls"]["horizontal"]))),
            tuple(sorted(map(tuple, o["walls"]["vertical"]))))


def _decode_book(v):
    if v[0] == "m":
        return {"type": "move", "to": [v[1], v[2]]}
    if v[0] == "p":
        return {"type": "pass"}
    return {"type": "wall", "orientation": v[0], "row": v[1], "col": v[2]}


# Sách khai cuộc: khóa = (hàng đích, quân mình, quân đối thủ, tường mình, tường đối thủ,
# tường ngang, tường dọc) -> nước đi ('m', hàng, cột) | ('p',) | ('H'/'V', hàng, cột).
# Tạo bằng tìm kiếm sâu (100.000 nút/lượt) của CHÍNH engine đấu bản nông của chính nó
# (50 mức nông x 2 bên); chỉ giữ các nước từ ván thắng. Không dùng dữ liệu của bot nào khác.
_BOOK = {
    (0, (4, 3), (5, 4), 10, 10, (), ()): ('H', 5, 3),
    (0, (5, 3), (4, 4), 10, 10, (), ()): ('m', 4, 3),
    (0, (5, 4), (3, 4), 10, 10, (), ()): ('m', 5, 3),
    (0, (6, 4), (2, 4), 10, 10, (), ()): ('m', 5, 4),
    (0, (7, 4), (1, 4), 10, 10, (), ()): ('m', 6, 4),
    (0, (8, 4), (0, 4), 10, 10, (), ()): ('m', 7, 4),
    (0, (4, 3), (5, 5), 9, 10, ((5, 3),), ()): ('H', 5, 5),
    (0, (4, 3), (5, 4), 8, 10, ((5, 3), (5, 5)), ()): ('m', 4, 4),
    (0, (4, 3), (5, 4), 9, 9, ((2, 2), (5, 3)), ()): ('H', 5, 5),
    (0, (4, 4), (5, 3), 8, 10, ((5, 3), (5, 5)), ()): ('V', 4, 2),
    (0, (4, 3), (5, 3), 8, 9, ((2, 2), (5, 3), (5, 5)), ()): ('V', 5, 2),
    (0, (4, 4), (4, 3), 7, 10, ((5, 3), (5, 5)), ((4, 2),)): ('V', 2, 2),
    (0, (4, 3), (5, 4), 8, 8, ((2, 2), (2, 4), (5, 3), (5, 5)), ()): ('V', 4, 2),
    (0, (3, 4), (4, 4), 6, 9, ((5, 3), (5, 5)), ((2, 2), (4, 2), (5, 7))): ('H', 7, 7),
    (0, (4, 3), (5, 3), 7, 8, ((2, 2), (2, 4), (5, 3), (5, 5)), ((4, 2),)): ('H', 3, 3),
    (0, (4, 3), (5, 3), 7, 8, ((2, 2), (2, 4), (5, 3), (5, 5)), ((5, 2),)): ('V', 2, 0),
    (0, (4, 4), (4, 3), 6, 9, ((5, 3), (5, 5)), ((2, 2), (4, 2), (5, 7))): ('m', 3, 4),
    (0, (2, 4), (3, 4), 5, 9, ((5, 3), (5, 5), (7, 7)), ((2, 2), (4, 2), (5, 7))): ('V', 6, 6),
    (0, (3, 4), (4, 4), 5, 9, ((5, 3), (5, 5), (7, 7)), ((2, 2), (4, 2), (5, 7))): ('m', 2, 4),
    (0, (4, 3), (5, 4), 7, 7, ((2, 2), (2, 4), (5, 3), (5, 5)), ((3, 5), (4, 2))): ('H', 3, 3),
    (0, (2, 4), (3, 5), 4, 9, ((5, 3), (5, 5), (7, 7)), ((2, 2), (4, 2), (5, 7), (6, 6))): ('H', 2, 5),
    (0, (4, 3), (5, 3), 6, 7, ((2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 5), (4, 2))): ('V', 2, 3),
    (0, (4, 3), (5, 3), 6, 7, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5)), ((2, 0), (5, 2))): ('V', 3, 2),
    (0, (2, 4), (3, 6), 3, 9, ((2, 5), (5, 3), (5, 5), (7, 7)), ((2, 2), (4, 2), (5, 7), (6, 6))): ('V', 3, 5),
    (0, (4, 3), (3, 3), 5, 7, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5)), ((2, 0), (3, 2), (5, 2))): ('H', 5, 7),
    (0, (4, 3), (5, 4), 6, 6, ((2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((3, 5), (4, 2), (5, 7))): ('H', 6, 7),
    (0, (4, 3), (3, 4), 4, 7, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((2, 0), (3, 2), (5, 2))): ('m', 4, 4),
    (0, (4, 3), (5, 3), 5, 6, ((2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (5, 7))): ('V', 4, 6),
    (0, (4, 4), (3, 5), 4, 7, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((2, 0), (3, 2), (5, 2))): ('V', 2, 5),
    (0, (2, 4), (3, 6), 2, 8, ((2, 5), (5, 3), (5, 5), (7, 7)), ((2, 2), (2, 7), (3, 5), (4, 2), (5, 7), (6, 6))): ('m', 1, 4),
    (0, (2, 4), (3, 6), 2, 8, ((2, 5), (5, 3), (5, 5), (7, 7)), ((2, 2), (3, 5), (4, 2), (4, 6), (5, 7), (6, 6))): ('m', 2, 5),
    (0, (4, 3), (5, 4), 4, 6, ((2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 4, 4),
    (0, (4, 3), (5, 4), 5, 5, ((2, 2), (2, 4), (3, 3), (4, 6), (5, 3), (5, 5), (6, 7)), ((3, 5), (4, 2), (5, 7))): ('V', 6, 6),
    (0, (4, 4), (4, 5), 4, 6, ((2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 4, 6),
    (0, (4, 6), (3, 6), 4, 6, ((2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 2, 6),
    (0, (1, 4), (3, 6), 2, 7, ((0, 4), (2, 5), (5, 3), (5, 5), (7, 7)), ((2, 2), (2, 7), (3, 5), (4, 2), (5, 7), (6, 6))): ('H', 0, 1),
    (0, (1, 5), (1, 6), 4, 5, ((0, 5), (2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 4),
    (0, (1, 6), (2, 6), 4, 5, ((0, 5), (2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 5),
    (0, (2, 5), (3, 6), 2, 7, ((2, 5), (5, 3), (5, 5), (7, 7)), ((1, 5), (2, 2), (3, 5), (4, 2), (4, 6), (5, 7), (6, 6))): ('V', 1, 4),
    (0, (2, 6), (3, 6), 4, 5, ((0, 5), (2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 6),
    (0, (3, 4), (4, 5), 3, 6, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('V', 1, 3),
    (0, (4, 4), (3, 5), 3, 6, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 4),
    (0, (1, 4), (1, 6), 4, 4, ((0, 3), (0, 5), (2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('H', 3, 6),
    (0, (1, 5), (2, 6), 4, 4, ((0, 3), (0, 5), (2, 2), (2, 4), (3, 3), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('H', 3, 7),
    (0, (1, 5), (5, 6), 1, 7, ((2, 5), (5, 3), (5, 5), (7, 7)), ((1, 4), (1, 5), (2, 2), (3, 5), (4, 2), (4, 6), (5, 7), (6, 6))): ('m', 0, 5),
    (0, (2, 5), (4, 6), 1, 7, ((2, 5), (5, 3), (5, 5), (7, 7)), ((1, 4), (1, 5), (2, 2), (3, 5), (4, 2), (4, 6), (5, 7), (6, 6))): ('m', 1, 5),
    (0, (3, 4), (4, 6), 2, 6, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 5),
    (0, (3, 5), (3, 6), 2, 6, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 5),
    (0, (4, 3), (5, 4), 4, 4, ((2, 2), (2, 4), (3, 3), (4, 6), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 2), (5, 7), (6, 6))): ('H', 5, 1),
    (0, (4, 5), (2, 6), 2, 6, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 6),
    (0, (4, 6), (1, 6), 2, 6, ((2, 2), (2, 4), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 7),
    (0, (1, 4), (1, 5), 3, 4, ((0, 3), (0, 5), (2, 2), (2, 4), (3, 3), (3, 6), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('H', 4, 7),
    (0, (1, 4), (3, 6), 1, 6, ((0, 1), (0, 4), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (4, 2), (5, 7), (6, 6))): ('H', 2, 3),
    (0, (1, 5), (1, 6), 3, 4, ((0, 3), (0, 5), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('V', 1, 1),
    (0, (4, 7), (1, 6), 2, 5, ((2, 2), (2, 4), (2, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 8),
    (0, (4, 7), (1, 6), 2, 5, ((2, 2), (2, 4), (2, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 6),
    (0, (1, 4), (3, 7), 0, 6, ((0, 1), (0, 4), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (4, 2), (5, 7), (6, 6))): ('m', 1, 5),
    (0, (1, 5), (1, 7), 2, 4, ((0, 3), (0, 5), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('H', 1, 6),
    (0, (3, 6), (0, 4), 2, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('V', 1, 7),
    (0, (4, 3), (5, 4), 3, 3, ((1, 0), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 2), (5, 7), (6, 6))): ('V', 4, 0),
    (0, (4, 6), (0, 5), 2, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (4, 6), (1, 6), 2, 4, ((2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 7),
    (0, (4, 7), (1, 5), 2, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 6),
    (0, (4, 7), (1, 5), 2, 4, ((2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 8),
    (0, (4, 8), (0, 5), 2, 4, ((2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('V', 0, 4),
    (0, (4, 8), (1, 6), 2, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 4, 7),
    (0, (1, 4), (1, 5), 2, 3, ((0, 3), (0, 5), (2, 2), (2, 4), (3, 3), (3, 6), (4, 7), (5, 3), (5, 5)), ((0, 2), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('V', 1, 1),
    (0, (1, 5), (3, 7), 0, 5, ((0, 1), (0, 4), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (3, 6), (4, 2), (5, 7), (6, 6))): ('m', 1, 6),
    (0, (1, 5), (3, 7), 0, 5, ((0, 1), (0, 4), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (4, 1), (4, 2), (5, 7), (6, 6))): ('m', 1, 6),
    (0, (3, 6), (0, 3), 1, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 7),
    (0, (3, 7), (1, 3), 1, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 8),
    (0, (3, 8), (2, 3), 1, 4, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 2, 8),
    (0, (4, 8), (1, 5), 1, 4, ((2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 8),
    (0, (1, 5), (1, 7), 1, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 2), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('V', 0, 7),
    (0, (1, 5), (1, 7), 1, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 4), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('V', 0, 7),
    (0, (1, 6), (3, 7), 0, 4, ((0, 1), (0, 4), (0, 6), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (3, 6), (4, 2), (5, 7), (6, 6))): ('m', 1, 7),
    (0, (1, 6), (3, 7), 0, 4, ((0, 1), (0, 4), (0, 6), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (4, 1), (4, 2), (5, 7), (6, 6))): ('m', 1, 7),
    (0, (1, 8), (2, 2), 1, 3, ((1, 2), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 0, 8),
    (0, (2, 8), (2, 3), 1, 3, ((1, 2), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 1, 8),
    (0, (3, 3), (3, 2), 2, 2, ((1, 0), (1, 2), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 4, 2),
    (0, (3, 5), (3, 4), 2, 2, ((1, 0), (1, 2), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 3, 3),
    (0, (3, 7), (0, 3), 1, 3, ((0, 0), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 8),
    (0, (3, 7), (0, 3), 1, 3, ((1, 2), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 8),
    (0, (3, 7), (0, 3), 1, 3, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 8),
    (0, (3, 7), (0, 3), 1, 3, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 1), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 8),
    (0, (3, 7), (2, 5), 1, 3, ((0, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('H', 1, 5),
    (0, (3, 8), (1, 5), 1, 3, ((0, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 7),
    (0, (3, 8), (1, 5), 1, 3, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('V', 0, 7),
    (0, (4, 2), (5, 2), 2, 2, ((1, 0), (1, 2), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 5, 1),
    (0, (4, 3), (5, 4), 2, 2, ((1, 0), (1, 2), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 4, 4),
    (0, (4, 4), (4, 5), 2, 2, ((1, 0), (1, 2), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 3, 5),
    (0, (5, 1), (4, 1), 2, 2, ((1, 0), (1, 2), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 3, 1),
    (0, (1, 4), (1, 5), 1, 2, ((0, 3), (0, 5), (1, 4), (2, 2), (2, 4), (3, 3), (3, 6), (4, 7), (5, 3), (5, 5)), ((0, 2), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 3),
    (0, (1, 5), (1, 7), 0, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 2), (0, 7), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 6),
    (0, (1, 5), (1, 7), 0, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 4), (0, 7), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 6),
    (0, (1, 6), (1, 5), 0, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 2), (0, 7), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 7),
    (0, (1, 6), (1, 5), 0, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 4), (0, 7), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 1, 7),
    (0, (1, 7), (1, 4), 0, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 2), (0, 7), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 0, 7),
    (0, (1, 7), (2, 5), 0, 3, ((0, 3), (0, 5), (1, 6), (2, 2), (2, 4), (3, 3), (3, 7), (5, 3), (5, 5)), ((0, 4), (0, 7), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7))): ('m', 0, 7),
    (0, (1, 7), (3, 7), 0, 3, ((0, 1), (0, 4), (0, 6), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (3, 6), (4, 2), (5, 7), (6, 6), (7, 2))): ('m', 1, 8),
    (0, (1, 7), (3, 7), 0, 3, ((0, 1), (0, 4), (0, 6), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (4, 1), (4, 2), (5, 7), (6, 6), (7, 2))): ('m', 1, 8),
    (0, (1, 8), (2, 7), 0, 3, ((0, 1), (0, 4), (0, 6), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (3, 6), (4, 2), (5, 7), (6, 6), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (2, 7), 0, 3, ((0, 1), (0, 4), (0, 6), (2, 3), (2, 5), (5, 3), (5, 5), (7, 7)), ((1, 3), (2, 2), (2, 7), (3, 5), (4, 1), (4, 2), (5, 7), (6, 6), (7, 2))): ('m', 0, 8),
    (0, (2, 6), (0, 4), 0, 3, ((0, 5), (1, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 2, 7),
    (0, (3, 1), (4, 1), 2, 1, ((1, 0), (1, 2), (1, 4), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('H', 7, 0),
    (0, (3, 6), (1, 4), 0, 3, ((0, 5), (1, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 2, 6),
    (0, (3, 6), (1, 4), 0, 3, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 2, 6),
    (0, (3, 7), (2, 4), 0, 3, ((0, 5), (1, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 7), (2, 4), 0, 3, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 8), (0, 3), 1, 2, ((0, 0), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (3, 8), (0, 3), 1, 2, ((1, 2), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (3, 8), (0, 3), 1, 2, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (3, 8), (0, 3), 1, 2, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 1), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 0))): ('m', 2, 8),
    (0, (3, 8), (2, 5), 0, 3, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 7),
    (0, (1, 3), (1, 5), 1, 1, ((0, 3), (0, 5), (1, 4), (2, 2), (2, 4), (3, 3), (3, 6), (4, 7), (5, 3), (5, 5)), ((0, 2), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7), (7, 1))): ('m', 2, 3),
    (0, (1, 7), (0, 3), 0, 2, ((0, 3), (0, 5), (1, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 0, 7),
    (0, (1, 8), (1, 3), 1, 1, ((0, 0), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 1), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (1, 3), 1, 1, ((1, 2), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 1), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (1, 3), 1, 1, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 1), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (1, 3), 1, 1, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 1), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 0))): ('m', 0, 8),
    (0, (2, 6), (1, 4), 0, 2, ((1, 4), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 2, 7),
    (0, (2, 7), (0, 4), 0, 2, ((0, 3), (0, 5), (1, 5), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 1, 7),
    (0, (2, 8), (0, 3), 1, 1, ((0, 0), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 1), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 3), 1, 1, ((1, 2), (2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 1), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 3), 1, 1, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 1), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 3), 1, 1, ((2, 2), (2, 4), (2, 6), (3, 7), (4, 0), (5, 1), (5, 3), (5, 5), (5, 7)), ((1, 3), (1, 7), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 0))): ('m', 1, 8),
    (0, (3, 7), (2, 5), 0, 2, ((0, 1), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 7), (2, 5), 0, 2, ((1, 2), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 7), (2, 5), 0, 2, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 7), (2, 5), 0, 2, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 0), (0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 7), (2, 5), 0, 2, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 2), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2))): ('m', 3, 6),
    (0, (3, 7), (2, 5), 0, 2, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1))): ('m', 3, 6),
    (0, (1, 2), (1, 3), 1, 0, ((0, 3), (0, 5), (1, 4), (2, 2), (2, 4), (3, 3), (3, 6), (4, 7), (5, 3), (5, 5), (6, 2)), ((0, 2), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7), (7, 1))): ('m', 0, 2),
    (0, (1, 8), (0, 3), 0, 1, ((0, 1), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (0, 3), 0, 1, ((1, 2), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (0, 3), 0, 1, ((1, 4), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (0, 3), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (0, 3), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 0), (0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (0, 3), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 2), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 0, 8),
    (0, (1, 8), (0, 3), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 2))): ('m', 0, 8),
    (0, (2, 2), (1, 4), 1, 0, ((0, 3), (0, 5), (1, 4), (2, 2), (2, 4), (3, 3), (3, 6), (4, 7), (5, 3), (5, 5), (6, 2)), ((0, 2), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7), (7, 1))): ('m', 1, 2),
    (0, (2, 3), (1, 5), 1, 0, ((0, 3), (0, 5), (1, 4), (2, 2), (2, 4), (3, 3), (3, 6), (4, 7), (5, 3), (5, 5), (6, 2)), ((0, 2), (1, 1), (2, 3), (2, 5), (4, 2), (4, 6), (5, 7), (7, 1))): ('m', 2, 2),
    (0, (2, 6), (2, 4), 0, 1, ((0, 1), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 7),
    (0, (2, 6), (2, 4), 0, 1, ((1, 2), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 7),
    (0, (2, 6), (2, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 7),
    (0, (2, 6), (2, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 0), (0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 7),
    (0, (2, 6), (2, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 2), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 7),
    (0, (2, 6), (2, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 2))): ('m', 2, 7),
    (0, (2, 7), (1, 4), 0, 1, ((0, 1), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (2, 7), (1, 4), 0, 1, ((1, 2), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (2, 7), (1, 4), 0, 1, ((1, 4), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (2, 7), (1, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (2, 7), (1, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 0), (0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (2, 7), (1, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 2), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 8),
    (0, (2, 7), (1, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 2))): ('m', 2, 8),
    (0, (2, 8), (0, 4), 0, 1, ((0, 1), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 4), 0, 1, ((1, 2), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 4), 0, 1, ((1, 4), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 0), (0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 2), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 1, 8),
    (0, (2, 8), (0, 4), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 2))): ('m', 1, 8),
    (0, (3, 1), (4, 1), 1, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('H', 7, 2),
    (0, (3, 6), (2, 5), 0, 1, ((0, 1), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 6),
    (0, (3, 6), (2, 5), 0, 1, ((1, 2), (1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 6),
    (0, (3, 6), (2, 5), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 6),
    (0, (3, 6), (2, 5), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 0), (0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 6),
    (0, (3, 6), (2, 5), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 2), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (7, 2))): ('m', 2, 6),
    (0, (3, 6), (2, 5), 0, 1, ((1, 6), (2, 2), (2, 4), (2, 7), (3, 6), (4, 0), (5, 3), (5, 5), (5, 7)), ((0, 4), (0, 7), (1, 3), (2, 0), (2, 5), (3, 2), (4, 4), (5, 2), (6, 1), (7, 2))): ('m', 2, 6),
    (0, (1, 8), (7, 3), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 0, 8),
    (0, (2, 1), (3, 1), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 2),
    (0, (2, 2), (3, 0), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 3),
    (0, (2, 3), (4, 0), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 4),
    (0, (2, 4), (5, 0), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 5),
    (0, (2, 5), (6, 0), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 6),
    (0, (2, 6), (7, 0), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 7),
    (0, (2, 7), (7, 1), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 8),
    (0, (2, 8), (7, 2), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 1, 8),
    (0, (3, 1), (4, 1), 0, 0, ((1, 0), (1, 2), (1, 4), (1, 6), (2, 2), (2, 4), (3, 3), (4, 6), (5, 1), (5, 3), (5, 5), (6, 7), (7, 0), (7, 2)), ((3, 1), (3, 5), (4, 0), (4, 2), (5, 7), (6, 6))): ('m', 2, 1),
    (8, (0, 4), (7, 4), 10, 10, (), ()): ('m', 1, 4),
    (8, (1, 4), (6, 4), 10, 10, (), ()): ('m', 2, 4),
    (8, (2, 4), (5, 4), 10, 10, (), ()): ('m', 3, 4),
    (8, (3, 4), (4, 4), 10, 10, (), ()): ('m', 5, 4),
    (8, (3, 4), (5, 3), 10, 10, (), ()): ('m', 4, 4),
    (8, (4, 4), (4, 3), 10, 10, (), ()): ('m', 5, 4),
    (8, (5, 4), (4, 3), 10, 9, ((5, 3),), ()): ('H', 2, 2),
    (8, (5, 4), (4, 4), 10, 9, ((5, 4),), ()): ('H', 3, 4),
    (8, (5, 4), (4, 3), 9, 8, ((2, 2), (5, 3), (5, 5)), ()): ('H', 2, 4),
    (8, (5, 4), (4, 4), 9, 8, ((3, 4), (5, 2), (5, 4)), ()): ('H', 3, 2),
    (8, (5, 4), (4, 4), 8, 8, ((2, 2), (2, 4), (5, 3), (5, 5)), ()): ('V', 4, 5),
    (8, (5, 4), (4, 5), 8, 8, ((3, 2), (3, 4), (5, 2), (5, 4)), ()): ('m', 5, 5),
    (8, (5, 5), (4, 6), 8, 8, ((3, 2), (3, 4), (5, 2), (5, 4)), ()): ('m', 5, 6),
    (8, (5, 4), (4, 4), 8, 7, ((3, 2), (3, 4), (5, 2), (5, 4), (5, 6)), ()): ('H', 3, 6),
    (8, (5, 6), (4, 6), 8, 7, ((3, 2), (3, 4), (5, 2), (5, 4), (5, 6)), ()): ('H', 3, 6),
    (8, (5, 6), (4, 6), 8, 7, ((3, 2), (3, 4), (5, 2), (5, 4), (6, 5)), ()): ('H', 3, 6),
    (8, (5, 4), (4, 4), 7, 7, ((2, 2), (2, 4), (5, 3), (5, 5)), ((1, 5), (4, 5))): ('H', 3, 4),
    (8, (5, 4), (4, 4), 7, 6, ((3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6)), ()): ('V', 4, 2),
    (8, (5, 4), (5, 3), 6, 7, ((2, 2), (2, 4), (3, 4), (5, 3), (5, 5)), ((1, 5), (4, 5))): ('H', 3, 2),
    (8, (5, 6), (4, 6), 7, 6, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (5, 6)), ((5, 7),)): ('H', 2, 7),
    (8, (5, 6), (4, 6), 7, 6, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ()): ('m', 4, 5),
    (8, (4, 5), (4, 6), 7, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5),)): ('V', 6, 4),
    (8, (4, 5), (4, 6), 7, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((6, 4),)): ('V', 4, 5),
    (8, (5, 4), (5, 2), 5, 7, ((2, 2), (2, 4), (3, 2), (3, 4), (5, 3), (5, 5)), ((1, 5), (4, 5))): ('V', 6, 2),
    (8, (4, 5), (4, 7), 6, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (6, 4))): ('V', 4, 7),
    (8, (5, 4), (4, 4), 6, 5, ((3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (7, 7)), ((4, 2),)): ('H', 2, 7),
    (8, (5, 6), (4, 6), 6, 5, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (5, 6), (6, 7)), ((5, 7),)): ('V', 2, 6),
    (8, (4, 2), (5, 8), 5, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (6, 4))): ('H', 2, 7),
    (8, (4, 3), (6, 8), 5, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (6, 4))): ('m', 4, 2),
    (8, (4, 4), (6, 7), 5, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (6, 4))): ('m', 4, 3),
    (8, (4, 5), (5, 7), 5, 5, ((3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (6, 4))): ('m', 4, 4),
    (8, (5, 4), (4, 5), 5, 5, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (7, 7)), ((4, 2),)): ('V', 3, 5),
    (8, (5, 4), (5, 2), 4, 6, ((2, 2), (2, 4), (3, 2), (3, 4), (5, 3), (5, 5)), ((1, 5), (3, 0), (4, 5), (6, 2))): ('H', 2, 0),
    (8, (5, 6), (4, 5), 5, 5, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 7))): ('m', 5, 5),
    (8, (4, 2), (4, 1), 5, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('H', 2, 1),
    (8, (4, 4), (4, 3), 5, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 4, 2),
    (8, (5, 4), (5, 5), 4, 5, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (7, 7)), ((3, 5), (4, 2))): ('m', 5, 6),
    (8, (5, 4), (6, 2), 3, 6, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 3), (5, 5)), ((1, 5), (3, 0), (4, 5), (6, 2))): ('m', 5, 3),
    (8, (5, 5), (4, 5), 5, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 4, 4),
    (8, (5, 6), (5, 7), 4, 5, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (7, 7)), ((3, 5), (4, 2))): ('m', 5, 8),
    (8, (5, 8), (4, 8), 4, 5, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (7, 7)), ((3, 5), (4, 2))): ('m', 6, 8),
    (8, (4, 2), (5, 8), 4, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (6, 4))): ('V', 5, 1),
    (8, (5, 3), (6, 2), 3, 5, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5)), ((1, 5), (3, 0), (4, 5), (6, 2))): ('H', 6, 1),
    (8, (5, 3), (6, 2), 3, 5, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 3), (5, 5)), ((1, 1), (1, 5), (3, 0), (4, 5), (6, 2))): ('m', 5, 2),
    (8, (5, 4), (5, 2), 3, 5, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 3), (5, 5)), ((1, 1), (1, 5), (3, 0), (4, 5), (6, 2))): ('m', 5, 3),
    (8, (6, 8), (4, 8), 4, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (6, 6), (7, 7)), ((3, 5), (4, 2))): ('V', 5, 7),
    (8, (4, 0), (3, 0), 4, 3, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 5, 0),
    (8, (4, 1), (3, 8), 3, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (5, 1), (6, 4))): ('m', 5, 1),
    (8, (4, 2), (4, 1), 4, 3, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 4, 0),
    (8, (4, 2), (4, 8), 3, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (5, 1), (6, 4))): ('m', 4, 1),
    (8, (5, 0), (2, 0), 4, 3, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 6, 0),
    (8, (5, 1), (3, 7), 3, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (5, 1), (6, 4))): ('m', 6, 1),
    (8, (5, 2), (6, 2), 3, 4, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5)), ((1, 1), (1, 5), (3, 0), (4, 5), (6, 2))): ('H', 6, 1),
    (8, (6, 0), (2, 1), 4, 3, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('H', 1, 1),
    (8, (6, 1), (3, 6), 3, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 5), (6, 7)), ((4, 5), (4, 7), (5, 1), (6, 4))): ('V', 7, 1),
    (8, (6, 8), (3, 8), 3, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (6, 6), (7, 7)), ((3, 5), (4, 2), (5, 7))): ('V', 7, 5),
    (8, (4, 0), (4, 1), 4, 2, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('H', 1, 0),
    (8, (5, 3), (6, 2), 2, 4, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 5), (6, 2), (7, 1))): ('V', 7, 0),
    (8, (6, 0), (2, 0), 3, 3, ((1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('H', 0, 0),
    (8, (6, 0), (2, 0), 4, 2, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 6, 1),
    (8, (6, 1), (2, 1), 4, 2, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('m', 6, 2),
    (8, (6, 8), (3, 7), 2, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (6, 6), (7, 7)), ((3, 5), (4, 2), (5, 7), (7, 5))): ('m', 7, 8),
    (8, (7, 8), (3, 6), 2, 4, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (6, 6), (7, 7)), ((3, 5), (4, 2), (5, 7), (7, 5))): ('m', 7, 7),
    (8, (4, 0), (3, 0), 3, 2, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('V', 2, 0),
    (8, (5, 2), (6, 2), 2, 3, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 1), (1, 5), (3, 0), (4, 5), (6, 2), (7, 1))): ('V', 7, 0),
    (8, (6, 0), (2, 1), 2, 3, ((0, 0), (1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((2, 6), (5, 3), (5, 7))): ('V', 7, 0),
    (8, (6, 1), (3, 6), 2, 3, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 1), (6, 5), (6, 7)), ((4, 5), (4, 7), (5, 1), (6, 4), (7, 1))): ('m', 6, 0),
    (8, (6, 2), (2, 1), 4, 1, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 6), (5, 3), (5, 7))): ('V', 6, 2),
    (8, (7, 6), (2, 6), 2, 3, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (6, 6), (7, 7)), ((2, 6), (3, 5), (4, 2), (5, 7), (7, 5))): ('m', 8, 6),
    (8, (7, 7), (3, 6), 2, 3, ((2, 7), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2), (5, 4), (5, 6), (6, 6), (7, 7)), ((2, 6), (3, 5), (4, 2), (5, 7), (7, 5))): ('m', 7, 6),
    (8, (4, 0), (4, 1), 3, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 4, 2),
    (8, (4, 0), (4, 1), 3, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 7)), ((1, 1), (2, 6), (5, 0), (5, 3), (5, 7))): ('H', 6, 1),
    (8, (4, 2), (3, 1), 3, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 4, 3),
    (8, (4, 3), (3, 2), 3, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 5, 3),
    (8, (5, 3), (3, 3), 3, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('H', 2, 3),
    (8, (5, 3), (6, 2), 1, 3, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 4), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 5, 2),
    (8, (6, 0), (3, 6), 2, 2, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 1), (6, 5), (6, 7)), ((2, 6), (4, 5), (4, 7), (5, 1), (6, 4), (7, 1))): ('m', 7, 0),
    (8, (6, 2), (1, 1), 3, 1, ((2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 6), (5, 3), (5, 7), (6, 2))): ('H', 0, 0),
    (8, (7, 0), (2, 6), 2, 2, ((2, 7), (3, 2), (3, 4), (3, 6), (4, 2), (5, 2), (5, 4), (6, 1), (6, 5), (6, 7)), ((2, 6), (4, 5), (4, 7), (5, 1), (6, 4), (7, 1))): ('m', 8, 0),
    (8, (4, 0), (3, 0), 2, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('H', 2, 3),
    (8, (4, 0), (3, 1), 2, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 1), (6, 7)), ((1, 1), (2, 6), (5, 0), (5, 3), (5, 7))): ('V', 7, 0),
    (8, (5, 0), (6, 0), 1, 2, ((0, 7), (2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 4), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 7, 0),
    (8, (5, 0), (6, 0), 1, 2, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 1), (1, 4), (1, 5), (3, 0), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 7, 0),
    (8, (5, 1), (6, 1), 1, 2, ((0, 7), (2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 4), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 5, 0),
    (8, (5, 1), (6, 1), 1, 2, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 1), (1, 4), (1, 5), (3, 0), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 5, 0),
    (8, (5, 2), (3, 5), 2, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('H', 2, 5),
    (8, (5, 2), (6, 2), 1, 2, ((0, 7), (2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 4), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 5, 1),
    (8, (5, 2), (6, 2), 1, 2, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 1), (1, 4), (1, 5), (3, 0), (4, 5), (6, 2), (7, 0), (7, 1))): ('m', 5, 1),
    (8, (5, 3), (3, 4), 2, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 5, 2),
    (8, (6, 0), (2, 1), 1, 2, ((0, 0), (1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 6, 1),
    (8, (6, 1), (2, 2), 1, 2, ((0, 0), (1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 6, 2),
    (8, (6, 2), (1, 2), 2, 1, ((0, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 6), (5, 3), (5, 7), (6, 2))): ('H', 0, 2),
    (8, (6, 2), (2, 3), 1, 2, ((0, 0), (1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 7, 2),
    (8, (7, 0), (8, 0), 1, 2, ((0, 7), (2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 4), (4, 5), (6, 2), (7, 0), (7, 1))): ('H', 4, 1),
    (8, (7, 0), (8, 0), 1, 2, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 1), (1, 4), (1, 5), (3, 0), (4, 5), (6, 2), (7, 0), (7, 1))): ('H', 4, 1),
    (8, (4, 0), (3, 2), 1, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 1), (6, 7)), ((1, 1), (2, 6), (5, 0), (5, 3), (5, 7), (7, 0))): ('m', 5, 0),
    (8, (4, 0), (5, 0), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 4, 1),
    (8, (4, 1), (4, 0), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 4, 2),
    (8, (4, 2), (4, 1), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 4, 3),
    (8, (4, 3), (3, 1), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 5, 3),
    (8, (5, 0), (3, 3), 1, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 1), (6, 7)), ((1, 1), (2, 6), (5, 0), (5, 3), (5, 7), (7, 0))): ('m', 6, 0),
    (8, (5, 1), (3, 3), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 5), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 6, 1),
    (8, (5, 1), (3, 4), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 6, 1),
    (8, (5, 2), (3, 3), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 5, 1),
    (8, (5, 2), (3, 4), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 5), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 5, 1),
    (8, (5, 3), (3, 2), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 5, 2),
    (8, (6, 0), (2, 3), 1, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 1), (6, 7)), ((1, 1), (2, 6), (5, 0), (5, 3), (5, 7), (7, 0))): ('m', 7, 0),
    (8, (6, 1), (3, 2), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 5), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 6, 2),
    (8, (6, 1), (3, 5), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 6, 2),
    (8, (6, 2), (1, 3), 1, 1, ((0, 0), (0, 2), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 6), (5, 3), (5, 7), (6, 2))): ('V', 1, 3),
    (8, (6, 2), (2, 5), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 7, 2),
    (8, (6, 2), (3, 1), 1, 1, ((1, 0), (2, 1), (2, 3), (2, 5), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 7, 2),
    (8, (7, 0), (1, 3), 1, 1, ((1, 0), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 1), (6, 7)), ((1, 1), (2, 6), (5, 0), (5, 3), (5, 7), (7, 0))): ('m', 8, 0),
    (8, (7, 0), (8, 0), 0, 2, ((0, 7), (2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (4, 1), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 5), (3, 0), (4, 4), (4, 5), (6, 2), (7, 0), (7, 1))): ('p',),
    (8, (7, 0), (8, 0), 0, 2, ((2, 0), (2, 2), (2, 4), (3, 2), (3, 4), (4, 1), (5, 1), (5, 3), (5, 5), (6, 1)), ((1, 1), (1, 4), (1, 5), (3, 0), (4, 5), (6, 2), (7, 0), (7, 1))): ('p',),
    (8, (7, 2), (2, 3), 1, 1, ((0, 0), (1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 1)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 7, 3),
    (8, (6, 2), (2, 3), 0, 1, ((0, 0), (0, 2), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((1, 3), (2, 6), (5, 3), (5, 7), (6, 2))): ('m', 7, 2),
    (8, (7, 1), (1, 5), 1, 0, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 8, 1),
    (8, (7, 1), (3, 0), 1, 0, ((1, 0), (2, 1), (2, 3), (2, 5), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 8, 1),
    (8, (7, 1), (3, 4), 0, 1, ((0, 0), (0, 2), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((1, 3), (2, 6), (5, 3), (5, 7), (6, 2))): ('m', 8, 1),
    (8, (7, 2), (2, 5), 1, 0, ((1, 0), (2, 1), (2, 3), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 0), (2, 6), (5, 0), (5, 3), (5, 7))): ('m', 7, 1),
    (8, (7, 2), (3, 1), 1, 0, ((1, 0), (2, 1), (2, 3), (2, 5), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((2, 6), (5, 0), (5, 3), (5, 7))): ('m', 7, 1),
    (8, (7, 2), (3, 3), 0, 1, ((0, 0), (0, 2), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 2)), ((1, 3), (2, 6), (5, 3), (5, 7), (6, 2))): ('m', 7, 1),
    (8, (7, 3), (2, 3), 1, 0, ((0, 0), (1, 1), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 1), (7, 3)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('H', 1, 3),
    (8, (7, 3), (2, 4), 0, 0, ((0, 0), (1, 1), (1, 3), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 1), (7, 3)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 7, 4),
    (8, (7, 4), (2, 5), 0, 0, ((0, 0), (1, 1), (1, 3), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 1), (7, 3)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 7, 5),
    (8, (7, 5), (1, 5), 0, 0, ((0, 0), (1, 1), (1, 3), (2, 1), (2, 7), (3, 2), (3, 4), (3, 6), (4, 1), (5, 2), (5, 4), (5, 6), (6, 0), (6, 7), (7, 1), (7, 3)), ((2, 6), (5, 3), (5, 7), (7, 0))): ('m', 8, 5),
}
PONDER_PHASE1 = float(os.environ.get("PP1", "0.55"))   # giây đầu "nghĩ hộ" dồn cho nước đáp trả được dự đoán
_PON = {"args": None, "thread": None}
_PSTOP = [False]


def _ponder_main(args, stop):
    """Tận dụng lúc đối thủ suy nghĩ: tìm kiếm trước các thế có thể xảy ra sau nước
    đi của mình, để bảng chuyển vị đã sẵn kết quả khi tới lượt (như "ponder" của
    stockfish). Luồng này chỉ chạy khi tiến trình chính đang chờ đọc lượt kế tiếp."""
    try:
        (a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, da, db, turn, max_turns) = args
        lay_m, dm = _layers(ga, a, U, D, L, R, hw, vw)
        win, kids = _gen_pawns(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw, lay_m, dm, db)
        if win is not None:
            return
        if wa > 0:
            kids.extend(_gen_walls(a, b, wa, wb, ga, gb, U, D, L, R, hw, vw,
                                   lay_m, dm, None, True))
        kids.append((-_evalb(db, da, wb, wa, b, a, gb, ga, U, D, L, R, hw, vw) - 1, PASS,
                     a, U, D, L, R, hw, vw, wa, da, db))
        kids.sort(key=lambda k: -k[0])
        ent = _TT.get((a, b, wa, wb, hw, vw, ga))
        if ent is not None and ent[3] is not None:
            for i, k in enumerate(kids):
                if k[1] == ent[3]:
                    kids.insert(0, kids.pop(i))
                    break
        s = _Searcher(1e18, turn, max_turns)
        s.stop = stop
        s.tb_left = 0
        start = perf_counter()

        def run(k, d):
            s.search(b, k[2], wb, k[9], gb, ga, k[3], k[4], k[5], k[6],
                     k[7], k[8], k[11], k[10], d, -WIN * 2, WIN * 2, 1)

        s.deadline = start + PONDER_PHASE1
        try:
            for d in range(2, 40):
                run(kids[0], d)
        except _TimeUp:
            pass
        s.deadline = 1e18
        if stop[0]:
            return
        for d in range(2, 40):
            for k in kids[1:]:
                run(k, d)
    except _TimeUp:
        pass
    except Exception:
        pass


def _stop_ponder():
    th = _PON["thread"]
    if th is not None:
        _PSTOP[0] = True
        th.join(0.3)
        _PON["thread"] = None


def _start_ponder():
    args = _PON["args"]
    _PON["args"] = None
    if args is None:
        return
    _PSTOP[0] = False
    th = threading.Thread(target=_ponder_main, args=(args, _PSTOP), daemon=True)
    _PON["thread"] = th
    th.start()


# ---------------------------------------------------------------------------
# Chống "đứng im bít đường" (stall): đối thủ đứng yên mãi trên đường ngắn nhất
# ---------------------------------------------------------------------------
STALL_SAFE_PASSES = 2      # đối thủ đứng yên >= 2 lượt liền -> chỉ đi tiếp nước KHÔNG làm xấu kết quả chính xác
STALL_GAMBLE_PASSES = 24   # đứng yên >= 24 lượt liền (dấu hiệu bot "đỗ xe") -> đi vòng dù bảng tàn cuộc báo rủi ro
STALL_SAFE_GAMBLE = True   # (C) chỉ đi vòng khi đường vòng không dài hơn đường về đích của quân đang đỗ
_STALL = {"sig": None, "count": 0, "seen": set(), "turn": -1}


def _stall_update(observation):
    """Đếm số lượt liên tiếp đối thủ không đổi gì (không đi, không đặt tường)."""
    opp = observation["opponent"]
    turn = observation.get("turn", 0)
    if turn < _STALL["turn"]:
        _STALL["count"] = 0
        _STALL["seen"].clear()
        _STALL["sig"] = None
    _STALL["turn"] = turn
    w = observation["walls"]
    sig = (tuple(opp["position"]), opp["walls_remaining"], len(w["horizontal"]) + len(w["vertical"]))
    _STALL["count"] = _STALL["count"] + 1 if sig == _STALL["sig"] else 0
    _STALL["sig"] = sig


def _parse_board(observation):
    you, opp = observation["you"], observation["opponent"]
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
    return (_bit(you["position"]), _bit(opp["position"]), ROW[you["goal_row"]], ROW[opp["goal_row"]],
            U, D, L, R, hw, vw)


def _static_path(a, b, ga, U, D, L, R):
    """Đường ngắn nhất của a tới đích khi quân b ĐỨNG YÊN mãi (có nhảy / đi chéo đúng luật).

    Trả về (số bước, ô đầu tiên phải đi) hoặc (INF, None) nếu b chặn hẳn.
    """
    if a & ga:
        return 0, None
    parent = {a: None}
    frontier = [a]
    d = 0
    while frontier:
        d += 1
        nxt = []
        for c in frontier:
            for x in _pawn_moves(c, b, U, D, L, R):
                if x in parent:
                    continue
                parent[x] = c
                if x & ga:
                    while parent[x] != a:
                        x = parent[x]
                    return d, x
                nxt.append(x)
        frontier = nxt
    return INF, None


def _tbv(t, mover, other, mover_goal):
    """Giá trị bảng tàn cuộc (góc nhìn bên đi): >0 thắng, <0 thua, 0 hòa."""
    im = mover.bit_length() - 1
    io = other.bit_length() - 1
    return t[0][im * 81 + io] if mover_goal & 1 else t[1][io * 81 + im]


def _stall_action(observation):
    """Nước đi riêng khi đối thủ cứ đứng yên bít đường.

    A) Hết tường cả hai (bảng tàn cuộc chính xác): chọn nước đi vòng làm quãng đường
       "khi đối thủ đứng yên" ngắn đi mà bảng vẫn báo KHÔNG thua -> luôn an toàn; nếu đối
       thủ cứ đứng im thì ta tới đích, nếu nó phản ứng thì kết quả vẫn không tệ hơn hòa.
    B) Đối thủ đứng yên rất lâu (>= STALL_GAMBLE_PASSES lượt): coi như bot đỗ xe, đi
       thẳng theo đường vòng nếu còn đủ lượt. Có rủi ro với đối thủ biết phản công nên
       chỉ bật sau thời gian chờ dài.
    """
    cnt = _STALL["count"]
    if cnt < STALL_SAFE_PASSES:
        return None
    a, b, ga, gb, U, D, L, R, hw, vw = _parse_board(observation)
    wa, wb = observation["you"]["walls_remaining"], observation["opponent"]["walls_remaining"]
    ia, ib = a.bit_length() - 1, b.bit_length() - 1
    _STALL["seen"].add((ia, ib))
    for x in _pawn_moves(a, b, U, D, L, R):
        if x & ga:
            return {"type": "move", "to": [(x.bit_length() - 1) // 9, (x.bit_length() - 1) % 9]}
    ds0, first = _static_path(a, b, ga, U, D, L, R)
    if first is None:
        return None
    if wa == 0 and wb == 0:
        t = _TBC.get((hw, vw))
        if t is None:
            t = _tb_solve(U, D, L, R)
            _TBC[(hw, vw)] = t
        if _tbv(t, a, b, ga) > 0:
            return None                     # đã thắng chắc: để engine chọn đường nhanh nhất
        best = None
        for x in _pawn_moves(a, b, U, D, L, R):
            if _tbv(t, b, x, gb) > 0:       # nước này cho đối thủ thế thắng -> bỏ
                continue
            ds, _ = _static_path(x, b, ga, U, D, L, R)
            key = ((x.bit_length() - 1, ib) in _STALL["seen"], ds)
            if ds < ds0 and (best is None or key < best[0]):
                best = (key, x)
        if best is not None and not best[0][0]:
            i = best[1].bit_length() - 1
            return {"type": "move", "to": [i // 9, i % 9]}
    if cnt >= STALL_GAMBLE_PASSES:
        left = (observation.get("max_turns", 500) - observation.get("turn", 0)) // 2
        if ds0 + 1 <= left:
            # (C) chỉ đi vòng khi vẫn thắng cuộc đua nếu quân kia bỏ chỗ chạy về đích ngay
            # (mỗi tường còn lại của nó có thể làm mình chậm thêm ~2 bước)
            db = _layers(gb, b, U, D, L, R, hw, vw)[1]
            if not STALL_SAFE_GAMBLE or (db >= 0 and ds0 + 2 * wb <= db):
                i = first.bit_length() - 1
                return {"type": "move", "to": [i // 9, i % 9]}
    return None


SIDE_PARAMS = {0: {"wall_scale": 0.7, "tempo": 5, "contempt": 25},     # đi trước: tường rẻ hơn -> dùng tường sớm hơn
               1: {"wall_scale": 1.0, "tempo": 5, "contempt": 25}}     # đi sau: giữ giá trị gốc
EXPLORE_EPS = 0.0      # chỉ dùng khi tạo sách: xác suất đi nước tốt thứ hai ở khai cuộc
EXPLORE_PLIES = 24
EXPLORE_MARGIN = 45


def _apply_side(pid):
    """Tham số riêng cho từng bên (đi trước / đi sau), áp dụng một lần mỗi ván."""
    global TEMPO, CONTEMPT, WALL_VALUE
    if _STATE.get("side") == pid:
        return
    p = SIDE_PARAMS[pid]
    TEMPO = p["tempo"]
    CONTEMPT = p["contempt"]
    WALL_VALUE = [sum(_WALL_MARGINAL[:k]) * p["wall_scale"] for k in range(11)]
    _STATE["side"] = pid
    _TT.clear()
    _TT_OLD.clear()


def _code_action(code):
    if code == PASS:
        return {"type": "pass"}
    if code < H_BASE:
        return {"type": "move", "to": [code // 9, code % 9]}
    if code < V_BASE:
        i = code - H_BASE
        return {"type": "wall", "orientation": "H", "row": i // 9, "col": i % 9}
    i = code - V_BASE
    return {"type": "wall", "orientation": "V", "row": i // 9, "col": i % 9}


def _explore(action, observation):
    """Khi tạo sách: đôi khi chọn nước tốt thứ hai (trong biên EXPLORE_MARGIN) để khám phá nhánh khác."""
    import random
    sc = _STATE.get("root")
    if not sc or random.random() >= EXPLORE_EPS or observation["turn"] >= EXPLORE_PLIES:
        return action
    ranked = sorted(sc.items(), key=lambda kv: -kv[1])
    if len(ranked) < 2 or ranked[0][1] - ranked[1][1] > EXPLORE_MARGIN:
        return action
    alt = _code_action(ranked[1][0])
    return alt if _is_legal(alt, observation) else action


def choose_action(observation):
    t0 = perf_counter()
    action = None
    _stop_ponder()
    try:
        _apply_side(observation["player_id"])
    except Exception:
        pass
    try:
        _stall_update(observation)
        sa = _stall_action(observation)
        if sa is not None and _is_legal(sa, observation):
            _PON["args"] = None
            return sa
    except Exception:
        pass
    if _BOOK:
        try:
            ba = _BOOK.get(_bkey(observation))
            if ba is not None:
                ba = _decode_book(ba)
                if _is_legal(ba, observation):
                    _PON["args"] = None
                    try:
                        _b = _parse_board(observation)
                        hw_, vw_ = _b[8], _b[9]
                        if ba["type"] == "wall":
                            w_ = 1 << (ba["row"] * 9 + ba["col"])
                            hw_, vw_ = (hw_ | w_, vw_) if ba["orientation"] == "H" else (hw_, vw_ | w_)
                        _STATE["seen_w"] = (hw_, vw_)
                    except Exception:
                        pass
                    return ba
        except Exception:
            pass
    try:
        _STATE["moves"] += 1
        action = _decide(observation, t0)
        if not _is_legal(action, observation):
            action = None
    except Exception:
        action = None
    if action is None:
        action = _fallback(observation)
    elif EXPLORE_EPS:
        action = _explore(action, observation)
    # Nếu lượt này bị trễ (máy chấm đang bận), giảm thời gian các lượt sau.
    late = perf_counter() - (t0 + TIME_LIMIT - _STATE["margin"])
    if late > 0.02:
        _STATE["margin"] = min(0.4, _STATE["margin"] + 2 * late)
    try:
        _start_ponder()
    except Exception:
        pass
    return action

