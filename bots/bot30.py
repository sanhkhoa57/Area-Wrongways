"""Agent Wrongway v7 (Tối ưu hóa): Negamax + PVS + TT xuyên lượt + Killer + Quiescence (như v3)
+ lõi BFS/Bitboard nhanh hơn + chống đi qua đi lại + "sốt ruột" khi gần hết lượt
+ co giãn thời gian an toàn (Adaptive Margin) + tương thích hoàn toàn với bộ train ES.

TÓM TẮT CÁCH CHỌN NƯỚC
  1. Bàn cờ là bitboard 81 bit; 4 mặt nạ U, D, L, R cho biết ô nào đi lên / xuống /
     trái / phải được. Đặt tường = xóa vài bit (bảng tính sẵn cho 128 vị trí tường).
  2. Tìm kiếm Negamax alpha-beta, đào sâu dần tới khi hết TIME_BUDGET giây; PVS (cửa sổ
     hẹp), killer cho tường, bảng chuyển vị (TT) giữ xuyên lượt, quiescence khi hai quân
     sát nhau. Nước được xét: mọi nước đi quân + các tường làm đối thủ đi xa hơn (tối đa
     10 bức ở nút trong, 6 ở nút sát lá, 24 ở gốc), sắp theo lợi ích (do2 - dm2).
  3. Hàm đánh giá (giữ nguyên v3): (đường đối thủ - đường mình, dẫn quá xa thì nén lại)
     + lượt đi + 1.25 điểm / bức tường trong tay; bức tường ĐẦU TIÊN đáng
     0.5 * (1 + thiệt hại lớn nhất một tường gây ra lúc này), kẹp trong [1.25, 3.5];
     phạt 2 điểm khi mình hết tường mà đối thủ còn; đua thuần túy đã rõ thì kết luận luôn.

MỚI Ở v7 & BẢN TỐI ƯU SO VỚI v3
  A. Lõi nhanh hơn, KHÔNG đổi kết quả: ở độ sâu cố định, v7 cho đúng cùng nước đi và
     cùng điểm với v3 trên 172 thế cờ (độ sâu 5) và 40 thế cờ (độ sâu 7), nhưng nhanh
     hơn đáng kể:
       * pinfo(): BFS từ quân giữ lại từng lớp + DAG đường ngắn nhất (lưu cache).
       * cut_mask() & banned_walls(): tính trực tiếp bằng phép dịch bit 64/81-bit O(1)
         thay vì quét bảng, nhanh gấp ~2.5 lần và không tốn bộ nhớ _BANNED_CACHE.
       * dist_with_wall(): biết tường có làm đường dài ra hay không mà KHÔNG cần BFS
         (lan truyền trong DAG qua 1-3 lớp quanh tường, tách nhánh ngang/dọc); nếu có thì
         "BFS sửa chữa" chạy tiếp từ lớp đầu tiên chạm tường thay vì chạy lại từ đầu.
       * goal_layers(): BFS từ cả hàng đích kèm từ điển khoảng cách theo ô (dmap), dùng
         chung cho mọi vị trí quân -> khoảng cách của nước đi quân chỉ cần tra bảng O(1).
       * pawn_moves(): trải phẳng 4 nhánh hướng đi, nhanh gấp ~1.6 lần.
       * Danh sách tường của mỗi nút được lưu cache; nút sát lá chấm điểm con tại chỗ.
  B. Chống đi qua đi lại (SHUFFLE_PENALTY): khi đọc các ván thua v4, bot thường dẫn
     xa rồi đứng ở cửa hành lang đi qua đi lại (có ván 12 nước liền) vì cứ tiến vào là
     thấy bị tường nhốt, còn đứng chờ thì mối đe dọa bị đẩy ra ngoài tầm nhìn (hiệu ứng
     chân trời), trong khi đối thủ cứ đi về đích. Ở gốc, nước đi quân quay lại một ô vừa
     đứng trong 6 lượt gần nhất mà không gần đích hơn bị trừ 1 điểm.
  C. Hòa chỉ được 1 điểm (thắng 3): thế cờ lặp lại bị coi là hơi xấu cho mình
     (DRAW_CONTEMPT), và sau 20% số lượt tối đa thì càng về cuối càng thưởng cho mỗi bước
     tiến về đích (URG_BONUS). Trước đó có ván hòa 500 lượt với MSSV_old vì hai bên
     cùng đứng chờ.

ĐÃ THỬ NHƯNG KHÔNG DÙNG (để vấn đáp; số liệu ở tools/res/)
  * USE_THREAT: tính lại giá trị tường đầu tiên ở TỪNG nút sát lá (thay vì một lần ở
    gốc), có cận trên / dưới để khỏi tính khi không cần. Ở cùng độ sâu thắng 63.5%
    (độ sâu 4) nhưng chỉ 46.5% (độ sâu 5): lệch theo chẵn / lẻ vì nước đi quân cuối cùng
    được đánh giá bằng đe dọa ở ô CŨ. Từ khai cuộc cố định, khi đi trước bot thua v4 và
    MSSV_old gần hết -> tắt. (USE_THREAT = 2: chỉ tính ở nút đối thủ đi nước cuối: trung tính.)
  * Tính đe dọa chính xác ở mọi lá: tốt (+7..+20% ở cùng độ sâu) nhưng chậm 5-7 lần.
  * Định giá MỌI bức tường theo thiệt hại (như v4): 50% với v4 (bot ôm tường).
  * NO_WALL_PENALTY = 0, DMG_MAX = 6: tốt khi tự đấu ở độ sâu cố định nhưng kém hơn từ
    khai cuộc cố định.
  * Null-move pruning: số nút giảm một nửa nhưng thua v4 nhiều hơn (66% so với 82%).
  * History heuristic, aspiration window, giảm độ sâu nước đi quân lùi, ít tường hơn
    ở nút trong: không đổi hoặc tệ hơn.
  * LMR (giảm 1 ply cho tường xếp sau): tới cùng độ sâu nhanh gấp ~2 lần, nhưng tự đấu
    cùng thời gian chỉ 50-53% -> lợi về độ sâu bị mất vì bỏ sót nước tường hay.
  * Phạt bình phương chênh lệch số tường (để khỏi tiêu tường nhanh hơn đối thủ): bot ôm
    tường, thắng v4 tụt từ 66% xuống 42%.
  * TT lớn hơn: lúc TT đầy, chỉ 1-10% mục còn dùng được (thế cờ ít tường hơn hiện tại
    không bao giờ gặp lại) -> vô ích.
  * Sách khai cuộc (nghĩ 8 s ở 7 nước đầu): từ thế xuất phát chuẩn 58/72 so với 57/72.
  -> Đo được: cho bot gấp đôi thời gian thì thắng chính nó 63%, nên tăng TIME_BUDGET
     từ 0.58 lên 0.64 s (vẫn dưới mục tiêu 0.70 s; arena thật tối đa ~0.66 s).

Chỉ dùng thư viện chuẩn. Mọi lỗi đều được bắt và trả về một nước an toàn.
"""

import gc
import time
from operator import itemgetter

# ---------------------------------------------------------------------------
# 1. HẰNG SỐ, CÔNG TẮC VÀ CẤU HÌNH HUẤN LUYỆN
# ---------------------------------------------------------------------------

TIME_BUDGET = 0.64      # giây cho mỗi nước (giới hạn thật 1 s, mục tiêu < 0.70 s)
TIME_LIMIT = 0.64       # đồng bộ tên biến với bộ huấn luyện
N = 9
INF = 999
WIN = 100000            # thắng chắc (trừ dần theo ply để thắng sớm hơn)
DECIDED = 5000          # cuộc đua thuần túy đã phân thắng bại
WALL_VALUE = 1.25       # giá trị một bức tường còn trong tay
TEMPO = 0.5             # lợi thế của bên đang tới lượt
MAX_WALL_CHILDREN = 10  # số tường tối đa xét ở nút trong
FRONTIER_WALLS = 6      # số tường tối đa xét ở nút sát lá
ROOT_WALLS = 24         # số tường tối đa xét ở gốc
NO_WALL_PENALTY = 2.0   # phạt khi mình hết tường mà đối thủ còn
LEAD_CAP = 3.0          # dẫn quá (LEAD_CAP + tường đối thủ) thì phần dư bị nén
LEAD_SLOPE = 0.3
EPS = 0.001             # độ rộng cửa sổ hẹp (null window) của PVS
DMG_SCALE = 0.5         # giá trị tường đầu tiên = DMG_SCALE * (1 + thiệt hại lớn nhất)
DMG_MAX = 3.5           # ... kẹp trong [WALL_VALUE, DMG_MAX]
QS_DEPTH = 2            # số ply tối đa của quiescence
MAX_DEPTH = 40
TT_LIMIT = 200000       # số mục tối đa của TT trước khi xóa

USE_THREAT = 0          # 1: tính lại giá trị tường đầu tiên ở mọi nút sát lá (đã thử, tắt)
USE_NMP = 0             # null-move pruning (đã thử, tắt)
NMP_R = 2
NMP_MIN_DEPTH = 3
SHUFFLE_PENALTY = 1.0   # phạt nước đi quân quay lại ô vừa đứng mà không gần đích hơn
SHUFFLE_WINDOW = 6      # ... trong ngần này lượt gần nhất của mình
URG_START = 0.2         # sau 20% số lượt tối đa thì bắt đầu "sốt ruột"
URG_BONUS = 3.0         # thưởng / bước tiến về đích khi hết lượt (tăng tuyến tính từ 0)
DRAW_CONTEMPT = 0.5     # thế cờ lặp (hòa) bị coi là xấu ngần này điểm cho mình
DRAW_CONTEMPT_URG = 3.0  # ... cộng thêm theo mức sốt ruột

DMG_SPAN = DMG_MAX - WALL_VALUE   # phần thưởng lớn nhất của bức tường đầu tiên

# Cấu hình tương thích trực tiếp với trình huấn luyện Evolution Strategy
PARAM_NAMES = (
    "WALL_VALUE", "TEMPO", "NO_WALL_PENALTY", "LEAD_CAP", "LEAD_SLOPE",
    "DMG_SCALE", "DMG_MAX", "SHUFFLE_PENALTY", "URG_BONUS", "DRAW_CONTEMPT"
)
DEFAULT_W = (1.25, 0.5, 2.0, 3.0, 0.3, 0.5, 3.5, 1.0, 3.0, 0.5)
PARAM_MIN = (0.40, 0.0, 0.0, 1.0, 0.05, 0.1, 1.5, 0.0, 0.0, 0.0)
PARAM_MAX = (3.00, 1.5, 6.0, 8.0, 0.90, 1.5, 6.0, 3.5, 8.0, 3.0)

FULL = (1 << 81) - 1
BIT = [1 << i for i in range(81)]
ROWMASK = [((1 << 9) - 1) << (9 * r) for r in range(9)]
_COL0 = sum(BIT[r * 9] for r in range(9))
_COL8 = sum(BIT[r * 9 + 8] for r in range(9))
U0 = FULL & ~ROWMASK[0]
D0 = FULL & ~ROWMASK[8]
L0 = FULL & ~_COL0
R0 = FULL & ~_COL8

# NEAR2[a]: các ô cách ô a không quá 2 bước Manhattan (dùng cho quiescence)
NEAR2 = []
for _a in range(81):
    _m = 0
    for _b in range(81):
        if abs(_a // 9 - _b // 9) + abs(_a % 9 - _b % 9) <= 2:
            _m |= 1 << _b
    NEAR2.append(_m)


def _abit(r, c):
    """Bit của vị trí neo tường (r, c) trong mặt nạ 64 bit; 0 nếu ngoài khoảng."""
    if 0 <= r < 8 and 0 <= c < 8:
        return 1 << (r * 8 + c)
    return 0


# Tường w = 0..127: w < 64 là tường ngang H, còn lại là dọc V.
W_ROW, W_COL, W_ABIT = [], [], []
CLR_U, CLR_D, CLR_L, CLR_R = [], [], [], []       # các ô mất cạnh đi lên/xuống/trái/phải
KEEP_U, KEEP_D, KEEP_L, KEEP_R = [], [], [], []   # FULL trừ CLR_*: đặt tường = U & KEEP_U[w]
for _w in range(128):
    _o = 0 if _w < 64 else 1
    _r, _c = (_w % 64) // 8, _w % 8
    W_ROW.append(_r)
    W_COL.append(_c)
    W_ABIT.append(_abit(_r, _c))
    if _o == 0:
        CLR_D.append(BIT[_r * 9 + _c] | BIT[_r * 9 + _c + 1])
        CLR_U.append(BIT[(_r + 1) * 9 + _c] | BIT[(_r + 1) * 9 + _c + 1])
        CLR_L.append(0)
        CLR_R.append(0)
    else:
        CLR_R.append(BIT[_r * 9 + _c] | BIT[(_r + 1) * 9 + _c])
        CLR_L.append(BIT[_r * 9 + _c + 1] | BIT[(_r + 1) * 9 + _c + 1])
        CLR_U.append(0)
        CLR_D.append(0)
    KEEP_U.append(FULL & ~CLR_U[-1])
    KEEP_D.append(FULL & ~CLR_D[-1])
    KEEP_L.append(FULL & ~CLR_L[-1])
    KEEP_R.append(FULL & ~CLR_R[-1])

# WTOUCH[w]: 4 ô có cạnh bị tường w chặn
WTOUCH = [CLR_U[w] | CLR_D[w] | CLR_L[w] | CLR_R[w] for w in range(128)]


def cut_mask(su, sd, sl, sr):
    """Mặt nạ 128 bit các tường chặn ít nhất một cạnh của DAG (su, sd, sl, sr).
    Tính trực tiếp bằng phép dịch bit 81-bit rồi nén từng hàng 9-bit sang 8-bit (nhanh gấp 2.5 lần).
    """
    eh = sd | (sd >> 1) | (su >> 9) | (su >> 10)
    ev = sr | (sr >> 9) | (sl >> 1) | (sl >> 10)
    h = (
        (eh & 0xFF)
        | ((eh >> 1) & 0xFF00)
        | ((eh >> 2) & 0xFF0000)
        | ((eh >> 3) & 0xFF000000)
        | ((eh >> 4) & 0xFF00000000)
        | ((eh >> 5) & 0xFF0000000000)
        | ((eh >> 6) & 0xFF000000000000)
        | ((eh >> 7) & 0xFF00000000000000)
    )
    v = (
        (ev & 0xFF)
        | ((ev >> 1) & 0xFF00)
        | ((ev >> 2) & 0xFF0000)
        | ((ev >> 3) & 0xFF000000)
        | ((ev >> 4) & 0xFF00000000)
        | ((ev >> 5) & 0xFF0000000000)
        | ((ev >> 6) & 0xFF000000000000)
        | ((ev >> 7) & 0xFF00000000000000)
    )
    return h | (v << 64)


def banned_walls(hw, vw):
    """Mặt nạ 128 bit các tường không thể đặt vì chồng / cắt tường đã có (dịch bit 64-bit O(1))."""
    ban_h = hw | ((hw & 0xFEFEFEFEFEFEFEFE) >> 1) | ((hw & 0x7F7F7F7F7F7F7F7F) << 1) | vw
    ban_v = vw | (vw >> 8) | ((vw << 8) & 0xFFFFFFFFFFFFFFFF) | hw
    return ban_h | (ban_v << 64)


class _Timeout(Exception):
    """Ném ra khi hết ngân sách thời gian để dừng tìm kiếm ngay."""


_BANNED_CACHE = {}      # giữ biến trống để tương thích với hàm _reset_bot_memory của trainer
_DIST_CACHE = {}        # (ô, hàng đích, tường) -> khoảng cách
_PI_CACHE = {}          # (ô, hàng đích, tường) -> thông tin BFS (xem pinfo)
_GL_CACHE = {}          # (hàng đích, tường) -> (các lớp BFS từ đích, cache khoảng cách theo ô)
_OW_CACHE = {}          # (ô nạn nhân, đích, tường) -> các tường làm nạn nhân đi xa hơn
_WL_CACHE = {}          # (pm, po, gm, tường) -> danh sách tường ứng viên của nút
_FV_CACHE = {}          # (nạn nhân, đích, kẻ đặt, tường) -> giá trị tường đầu tiên
_TT = {}                # bảng chuyển vị, giữ xuyên lượt trong một ván
_HISTORY = {}           # số lần mỗi thế cờ đã xuất hiện trong ván
_LAST_TURN = [-1]
_DEBUG = {}
WV = [WALL_VALUE] * 9   # WV[hàng đích]: giá trị tường đầu tiên tính ở gốc
_S = {
    "deadline": 0.0,
    "nodes": 0,
    "reps": 0,
    "root_g": 0,
    "contempt": 0.0,
    "urg": 0.0,
    "margin": 0.0,      # độ trễ dự phòng tự động co giãn nếu máy chấm quá tải
}
_KILLERS = {}
_MYPOS = []             # các ô mình đã đứng trong ván (chống đi qua đi lại)

gc.disable()            # tự gọi gc.collect(0) đầu mỗi lượt (tránh dừng giữa chừng)
_now = time.perf_counter
_K0 = itemgetter(0)


def set_weights(w):
    """Cập nhật vector trọng số W (tương thích với cả abot và anzu_v2 trong trainer)."""
    global WALL_VALUE, TEMPO, NO_WALL_PENALTY, LEAD_CAP, LEAD_SLOPE
    global DMG_SCALE, DMG_MAX, SHUFFLE_PENALTY, URG_BONUS, DRAW_CONTEMPT, DMG_SPAN
    (
        WALL_VALUE, TEMPO, NO_WALL_PENALTY, LEAD_CAP, LEAD_SLOPE,
        DMG_SCALE, DMG_MAX, SHUFFLE_PENALTY, URG_BONUS, DRAW_CONTEMPT
    ) = [float(x) for x in w]
    if DMG_MAX < WALL_VALUE:
        DMG_MAX = WALL_VALUE
    DMG_SPAN = DMG_MAX - WALL_VALUE
    WV[:] = [WALL_VALUE] * len(WV)
    _TT.clear()
    _WL_CACHE.clear()
    _FV_CACHE.clear()


# ---------------------------------------------------------------------------
# 2. MÔ PHỎNG LUẬT VÀ KHOẢNG CÁCH
# ---------------------------------------------------------------------------

def masks_from_walls(hw, vw):
    """Từ tập tường (mặt nạ neo H, V) tính 4 mặt nạ di chuyển U, D, L, R."""
    u, d, l, r = U0, D0, L0, R0
    x = hw
    while x:
        low = x & -x
        w = low.bit_length() - 1
        u &= KEEP_U[w]
        d &= KEEP_D[w]
        x ^= low
    x = vw
    while x:
        low = x & -x
        w = 64 + low.bit_length() - 1
        l &= KEEP_L[w]
        r &= KEEP_R[w]
        x ^= low
    return u, d, l, r


def bfs_dist(p, goal_row, U, D, L, R, hw, vw):
    """Số bước ít nhất từ ô p tới hàng đích (bỏ qua quân cờ). INF nếu không có đường."""
    key = (p, goal_row, hw, vw)
    val = _DIST_CACHE.get(key)
    if val is not None:
        return val
    goal = ROWMASK[goal_row]
    seen = front = BIT[p]
    dist = 0
    if not (front & goal):
        while True:
            dist += 1
            nb = (((front & U) >> 9) | ((front & D) << 9)
                  | ((front & L) >> 1) | ((front & R) << 1))
            front = nb & ~seen
            if not front:
                dist = INF
                break
            if front & goal:
                break
            seen |= front
    _DIST_CACHE[key] = dist
    return dist


def pinfo(p, goal_row, U, D, L, R, hw, vw):
    """BFS từ ô p tới hàng đích, giữ lại từng lớp để dùng lại.

    Trả về tuple (d, layers, seens, cut, dl, dsuf, su, sd, sl, sr):
      d         khoảng cách (INF nếu không có đường)
      layers[k] các ô cách p đúng k bước; seens[k] = hợp layers[0..k]
      cut       mặt nạ 128 bit các tường chặn ít nhất 1 cạnh của DAG đường ngắn nhất
                (chỉ những tường này mới có thể làm đường đi dài ra)
      dl[k]     các ô ở lớp k nằm trên một đường ngắn nhất; dsuf[k] = hợp dl[k..d]
      su..sr    cạnh của DAG theo 4 hướng (bit tại ô xuất phát của cạnh)
    """
    key = (p, goal_row, hw, vw)
    val = _PI_CACHE.get(key)
    if val is not None:
        return val
    goal = ROWMASK[goal_row]
    front = seen = BIT[p]
    layers = [front]
    seens = [seen]
    if front & goal:
        val = (0, layers, seens, 0, None, None, 0, 0, 0, 0)
    else:
        while True:
            nb = (((front & U) >> 9) | ((front & D) << 9)
                  | ((front & L) >> 1) | ((front & R) << 1))
            front = nb & ~seen
            if not front:
                val = (INF, layers, seens, 0, None, None, 0, 0, 0, 0)
                break
            seen |= front
            layers.append(front)
            seens.append(seen)
            if front & goal:
                # Lần ngược từ đích về p: giữ các ô / cạnh thuộc một đường ngắn nhất nào đó
                d = len(layers) - 1
                sp = front & goal
                dl = [0] * (d + 1)
                dsuf = [0] * (d + 1)
                dl[d] = dsuf[d] = acc = sp
                su = sd = sl = sr = 0
                for k in range(d - 1, -1, -1):
                    lay = layers[k]
                    cu = lay & U & (sp << 9)
                    cd = lay & D & (sp >> 9)
                    cl = lay & L & (sp << 1)
                    cr = lay & R & (sp >> 1)
                    su |= cu
                    sd |= cd
                    sl |= cl
                    sr |= cr
                    sp = cu | cd | cl | cr
                    dl[k] = sp
                    acc |= sp
                    dsuf[k] = acc
                val = (d, layers, seens, cut_mask(su, sd, sl, sr), dl, dsuf, su, sd, sl, sr)
                break
    _PI_CACHE[key] = val
    _DIST_CACHE[key] = val[0]
    return val


def dist_with_wall(p, goal_row, info, w, U, D, L, R, hw, vw):
    """Khoảng cách từ p tới đích sau khi đặt thêm tường w (w chặn ít nhất 1 cạnh của DAG).

    Bước 1 (không cần BFS): vẫn còn đường ngắn nhất cũ nếu trong DAG đi được mà tránh
      2 cạnh bị chặn. Lớp DAG trước lớp đầu tiên chạm tường không bị ảnh hưởng, lớp
      sau lớp cuối cùng chạm tường cũng vậy -> chỉ lan truyền qua 1-3 lớp quanh tường.
    Bước 2 (chỉ khi đường bị cắt): BFS "sửa chữa": các lớp BFS cũ trước lớp đầu tiên
      chạm tường vẫn đúng, nên chạy tiếp từ lớp đó. Kết quả được lưu cache.
    """
    dl = info[4]
    touch = WTOUCH[w]
    k = 0
    while not (dl[k] & touch):
        k += 1
    dsuf = info[5]
    last = info[0]
    front = dl[k]
    if w < 64:
        su = info[6] & KEEP_U[w]
        sd = info[7] & KEEP_D[w]
        sl = info[8]
        sr = info[9]
        while k < last and (dsuf[k] & touch):
            front = (((front & su) >> 9) | ((front & sd) << 9)
                     | ((front & sl) >> 1) | ((front & sr) >> 1))
            if not front:
                break
            k += 1
        else:
            return last                   # vẫn còn một đường ngắn nhất cũ
        hw2 = hw | W_ABIT[w]
        vw2 = vw
        key = (p, goal_row, hw2, vw2)
        val = _DIST_CACHE.get(key)
        if val is not None:
            return val
        U2 = U & KEEP_U[w]
        D2 = D & KEEP_D[w]
        L2, R2 = L, R
    else:
        su = info[6]
        sd = info[7]
        sl = info[8] & KEEP_L[w]
        sr = info[9] & KEEP_R[w]
        while k < last and (dsuf[k] & touch):
            front = (((front & su) >> 9) | ((front & sd) << 9)
                     | ((front & sl) >> 1) | ((front & sr) >> 1))
            if not front:
                break
            k += 1
        else:
            return last                   # vẫn còn một đường ngắn nhất cũ
        hw2 = hw
        vw2 = vw | W_ABIT[w]
        key = (p, goal_row, hw2, vw2)
        val = _DIST_CACHE.get(key)
        if val is not None:
            return val
        U2, D2 = U, D
        L2 = L & KEEP_L[w]
        R2 = R & KEEP_R[w]

    layers = info[1]
    while k > 0 and (layers[k - 1] & touch):
        k -= 1
    front = layers[k]
    seen = info[2][k]
    goal = ROWMASK[goal_row]
    dist = k
    while True:
        dist += 1
        nb = (((front & U2) >> 9) | ((front & D2) << 9)
              | ((front & L2) >> 1) | ((front & R2) << 1))
        front = nb & ~seen
        if not front:
            dist = INF
            break
        if front & goal:
            break
        seen |= front
    _DIST_CACHE[key] = dist
    return dist


def goal_layers(goal_row, U, D, L, R, hw, vw):
    """BFS từ cả hàng đích: trả về (layers, dmap) để dùng chung và tra cứu O(1) cho mọi vị trí."""
    key = (goal_row, hw, vw)
    ent = _GL_CACHE.get(key)
    if ent is None:
        front = seen = ROWMASK[goal_row]
        layers = [front]
        while True:
            nb = (((front & U) >> 9) | ((front & D) << 9)
                  | ((front & L) >> 1) | ((front & R) << 1))
            front = nb & ~seen
            if not front:
                break
            seen |= front
            layers.append(front)
        ent = (layers, {})
        _GL_CACHE[key] = ent
    return ent


def near_dist(ent, m, d):
    """Khoảng cách tới đích của ô m, có lưu cache dmap theo từng ô (nhanh gấp 4.5 lần)."""
    layers, dmap = ent
    k = dmap.get(m)
    if k is None:
        k = d - 2 if d > 2 else 0
        b = BIT[m]
        n = len(layers)
        while k < n and not (layers[k] & b):
            k += 1
        if k >= n:
            k = INF
        dmap[m] = k
    return k


def pawn_moves(p, q, U, D, L, R):
    """Các ô quân ở p đi tới được khi đối thủ ở q (trải phẳng 4 hướng để tăng tốc)."""
    res = []
    b = BIT[p]
    if b & U:
        n = p - 9
        if n != q:
            res.append(n)
        else:
            nb = BIT[n]
            if nb & U:
                res.append(n - 9)          # nhảy thẳng qua đối thủ
            else:
                if nb & L:
                    res.append(n - 1)      # đi chéo
                if nb & R:
                    res.append(n + 1)
    if b & D:
        n = p + 9
        if n != q:
            res.append(n)
        else:
            nb = BIT[n]
            if nb & D:
                res.append(n + 9)
            else:
                if nb & L:
                    res.append(n - 1)
                if nb & R:
                    res.append(n + 1)
    if b & L:
        n = p - 1
        if n != q:
            res.append(n)
        else:
            nb = BIT[n]
            if nb & L:
                res.append(n - 1)
            else:
                if nb & U:
                    res.append(n - 9)
                if nb & D:
                    res.append(n + 9)
    if b & R:
        n = p + 1
        if n != q:
            res.append(n)
        else:
            nb = BIT[n]
            if nb & R:
                res.append(n + 1)
            else:
                if nb & U:
                    res.append(n - 9)
                if nb & D:
                    res.append(n + 9)
    return res


# ---------------------------------------------------------------------------
# 3. SINH TƯỜNG VÀ ĐO "ĐE DỌA TƯỜNG"
# ---------------------------------------------------------------------------

def opp_walls(pv, gv, U, D, L, R, hw, vw):
    """Các tường (w, d2) làm quân ở pv (đích gv) đi xa hơn mà không chặn hết đường của nó."""
    key = (pv, gv, hw, vw)
    res = _OW_CACHE.get(key)
    if res is not None:
        return res
    info = pinfo(pv, gv, U, D, L, R, hw, vw)
    dv = info[0]
    cand = info[3] & ~banned_walls(hw, vw)
    res = []
    while cand:
        low = cand & -cand
        cand ^= low
        w = low.bit_length() - 1
        d2 = dist_with_wall(pv, gv, info, w, U, D, L, R, hw, vw)
        if d2 == dv or d2 >= INF:
            continue
        res.append((w, d2))
    _OW_CACHE[key] = res
    return res


def wall_list(pm, po, gm, go, U, D, L, R, hw, vw, dm, do):
    """Tường của bên đang đi (ở pm) làm đối thủ (ở po) chậm đi.

    List (thứ tự tĩnh, mã nước, dm2, do2) theo w tăng dần, có cache theo nút.
    """
    key = (pm, po, gm, hw, vw)
    lst = _WL_CACHE.get(key)
    if lst is not None:
        return lst
    lst = []
    ow = opp_walls(po, go, U, D, L, R, hw, vw)
    if ow:
        info = pinfo(pm, gm, U, D, L, R, hw, vw)
        mycut = info[3]
        for w, do2 in ow:
            if (mycut >> w) & 1:
                dm2 = dist_with_wall(pm, gm, info, w, U, D, L, R, hw, vw)
                if dm2 >= INF:
                    continue      # chặn hết đường của chính mình: bất hợp lệ
            else:
                dm2 = dm
            lst.append((do2 - dm2 - WALL_VALUE, 100 + w, dm2, do2))
    _WL_CACHE[key] = lst
    return lst


def wall_damage(pv, gv, pa, ga, U, D, L, R, hw, vw):
    """Số bước nhiều nhất mà MỘT bức tường của quân ở pa bắt quân ở pv đi thêm."""
    ow = opp_walls(pv, gv, U, D, L, R, hw, vw)
    if not ow:
        return 0
    dv = bfs_dist(pv, gv, U, D, L, R, hw, vw)
    acut = None
    best = 0
    for w, d2 in ow:
        if d2 - dv <= best:
            continue
        if acut is None:
            ainfo = pinfo(pa, ga, U, D, L, R, hw, vw)
            acut = ainfo[3]
        if (acut >> w) & 1 and dist_with_wall(pa, ga, ainfo, w, U, D, L, R, hw, vw) >= INF:
            continue                      # tường này chặn hết đường của kẻ đặt: bất hợp lệ
        best = d2 - dv
    return best


def first_wall_value(pv, gv, pa, ga, U, D, L, R, hw, vw):
    """Giá trị bức tường đầu tiên trong tay quân ở pa (dùng để chặn quân ở pv).

    = DMG_SCALE * (1 + thiệt hại lớn nhất một tường gây ra lúc này),
    kẹp trong [WALL_VALUE, DMG_MAX]. Quân pv đi vào hành lang hẹp -> tường đáng giá hơn.
    """
    key = (pv, gv, pa, hw, vw)
    v = _FV_CACHE.get(key)
    if v is None:
        v = DMG_SCALE * (1 + wall_damage(pv, gv, pa, ga, U, D, L, R, hw, vw))
        if v < WALL_VALUE:
            v = WALL_VALUE
        elif v > DMG_MAX:
            v = DMG_MAX
        _FV_CACHE[key] = v
    return v


# ---------------------------------------------------------------------------
# 4. HÀM ĐÁNH GIÁ (nhìn từ phía bên đang tới lượt)
# ---------------------------------------------------------------------------

def evaluate(dm, do, wm, wo, vm, vo):
    """dm, do: quãng đường của mình / đối thủ; wm, wo: số tường còn lại;
    vm, vo: giá trị bức tường đầu tiên của mình / đối thủ (các bức sau = WALL_VALUE).
    """
    if wo == 0 and dm <= do:
        return DECIDED + (do - dm) * 10 - dm     # đối thủ hết tường, mình đi trước, không xa hơn
    if wm == 0 and dm > do:
        return -DECIDED - (dm - do) * 10 + do    # mình hết tường và xa hơn
    lead = do - dm
    cap = LEAD_CAP + wo
    if lead > cap:
        lead = cap + (lead - cap) * LEAD_SLOPE   # dẫn quá xa: phần dư ít giá trị
    score = lead + TEMPO + WALL_VALUE * (wm - wo)
    if wm > 0:
        score += vm - WALL_VALUE
    elif wo > 0:
        score -= NO_WALL_PENALTY
    if wo > 0:
        score -= vo - WALL_VALUE
    elif wm > 0:
        score += NO_WALL_PENALTY
    return score


def _to_tt(score, ply):
    if score > WIN - 1000:
        return score + ply
    if score < -(WIN - 1000):
        return score - ply
    return score


def _from_tt(score, ply):
    if score > WIN - 1000:
        return score - ply
    if score < -(WIN - 1000):
        return score + ply
    return score


# ---------------------------------------------------------------------------
# 5. TÌM KIẾM
# ---------------------------------------------------------------------------

def qsearch(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply, qd):
    """Tìm kiếm tĩnh: khi hai quân sát nhau chỉ xét thêm nước đi quân (đứng yên = stand pat)."""
    S = _S
    S["nodes"] += 1
    if (S["nodes"] & 15) == 0 and _now() > S["deadline"]:
        raise _Timeout()
    stand = evaluate(dm, do, wm, wo, WV[gm], WV[go])
    if qd <= 0 or stand >= beta:
        return stand
    if not (NEAR2[pm] & BIT[po]):
        return stand
    best = stand
    if stand > alpha:
        alpha = stand
    goal = ROWMASK[gm]
    gl = goal_layers(gm, U, D, L, R, hw, vw)
    dmap = gl[1]
    for m in pawn_moves(pm, po, U, D, L, R):
        if BIT[m] & goal:
            return WIN - ply - 1
        dm2 = dmap.get(m)
        if dm2 is None:
            dm2 = near_dist(gl, m, dm)
        s = -qsearch(po, m, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm2,
                     -beta, -alpha, ply + 1, qd - 1)
        if s > best:
            best = s
            if s > alpha:
                alpha = s
                if alpha >= beta:
                    break
    return best


def wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, max_w, killers):
    """Tường ứng viên đã sắp xếp: killer (+5) rồi theo lợi ích tĩnh, lấy max_w bức đầu."""
    wl = wall_list(pm, po, gm, go, U, D, L, R, hw, vw, dm, do)
    if killers:
        ws = [((t[0] + 5.0, t[1], t[2], t[3]) if t[1] in killers else t) for t in wl]
        ws.sort(key=_K0, reverse=True)
    else:
        ws = sorted(wl, key=_K0, reverse=True)
    return ws[:max_w]


def frontier(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply,
             moves, key, tt_code, reps0):
    """Nút sát lá (còn đúng 1 ply): chấm điểm mọi con ngay tại chỗ.

    Với con không ngã ngũ và hai quân không sát nhau:
        điểm = s0 + a * [mình còn tường] - b * [đối thủ còn tường]
      s0 = điểm khi coi bức tường đầu tiên của cả hai chỉ đáng WALL_VALUE,
      a  = giá trị tường đầu tiên của mình - WALL_VALUE  (đe dọa của mình lên đối thủ ở po)
      b  = giá trị tường đầu tiên của đối thủ - WALL_VALUE (đe dọa của đối thủ lên mình ở pm)
    Khi USE_THREAT = 0 (mặc định), a, b là giá trị tính ở gốc (WV) nên luôn biết chính xác
    và hàm này chỉ là "chấm điểm các con ngay tại chỗ", giống hệt negamax của v3 ở độ sâu 1.
    """
    alpha0 = alpha
    if USE_THREAT and (USE_THREAT == 1 or gm != _S["root_g"]):
        a = 0.0 if wm == 0 else _FV_CACHE.get((po, go, pm, hw, vw))
        b = 0.0 if wo == 0 else _FV_CACHE.get((pm, gm, po, hw, vw))
        if a is not None and wm:
            a -= WALL_VALUE
        if b is not None and wo:
            b -= WALL_VALUE
    else:
        a = WV[gm] - WALL_VALUE
        b = WV[go] - WALL_VALUE
    recs = []                       # (s0, m1, m2, mã nước) của từng con đã chấm
    best_lo = best_up = -INF * WIN  # cận dưới / cận trên của giá trị nút
    code_lo = None
    killers = _KILLERS.get(ply, ())
    lazy = tt_code is None or tt_code < 100
    exact = a is not None and b is not None
    for phase in (0, 1):
        if phase == 0:
            gl = goal_layers(gm, U, D, L, R, hw, vw)
            dmap = gl[1]
            kids = []
            for m in moves:
                dm2 = dmap.get(m)
                if dm2 is None:
                    dm2 = near_dist(gl, m, dm)
                kids.append((do - dm2, m, dm2, do))
            if not lazy and wm > 0:
                kids.extend(wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do,
                                      FRONTIER_WALLS, killers))
        else:
            if not lazy or wm == 0:
                break
            if not exact and best_up >= beta:
                # Các nước đi quân có thể đã đủ để cắt tỉa: tính a, b rồi kiểm tra lại
                a, b = _exact_ab(a, b, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
                exact = True
                best_lo, code_lo = _best_exact(recs, a, b)
                best_up = best_lo
                if best_lo >= beta:
                    break
            kids = wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, FRONTIER_WALLS, killers)
            if not exact and wm:
                # Vừa sinh tường lên đối thủ: phần lớn công tính a đã xong (cache)
                a = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw) - WALL_VALUE
                exact = b is not None
                if exact:
                    best_lo, code_lo = _best_exact(recs, a, b)
                    best_up = best_lo
        kids.sort(key=_K0, reverse=True)
        if tt_code is not None:
            for i, k in enumerate(kids):
                if k[1] == tt_code:
                    if i:
                        kids.insert(0, kids.pop(i))
                    break
        A_lo = a if a is not None else 0.0
        A_hi = a if a is not None else DMG_SPAN
        B_lo = b if b is not None else 0.0
        B_hi = b if b is not None else DMG_SPAN
        for _, code, dm2, do2 in kids:
            if code < 100:
                pm2 = code
                wm2 = wm
                if NEAR2[po] & BIT[pm2]:
                    lo = max(alpha0, best_lo)
                    s = -qsearch(po, pm2, go, gm, U, D, L, R, hw, vw, wo, wm2, do2, dm2,
                                 -beta, -lo, ply + 1, QS_DEPTH)
                    recs.append((s, 0.0, 0.0, code))
                    lo_s = up_s = s
                else:
                    lo_s = up_s = None
            else:
                pm2 = pm
                wm2 = wm - 1
                if NEAR2[po] & BIT[pm2]:
                    w = code - 100
                    if w < 64:
                        hw2 = hw | W_ABIT[w]
                        vw2 = vw
                        U2, D2, L2, R2 = U & KEEP_U[w], D & KEEP_D[w], L, R
                    else:
                        hw2 = hw
                        vw2 = vw | W_ABIT[w]
                        U2, D2, L2, R2 = U, D, L & KEEP_L[w], R & KEEP_R[w]
                    lo = max(alpha0, best_lo)
                    s = -qsearch(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2, wo, wm2, do2, dm2,
                                 -beta, -lo, ply + 1, QS_DEPTH)
                    recs.append((s, 0.0, 0.0, code))
                    lo_s = up_s = s
                else:
                    lo_s = up_s = None
            if lo_s is None:
                # Đánh giá từ phía đối thủ (đối thủ đi tiếp): dm = do2, do = dm2
                s0 = -evaluate(do2, dm2, wo, wm2, WALL_VALUE, WALL_VALUE)
                if (wm2 == 0 and do2 <= dm2) or (wo == 0 and do2 > dm2):
                    m1 = m2 = 0.0           # ván đã ngã ngũ: giá trị tường không còn ý nghĩa
                else:
                    m1 = 1.0 if wm2 > 0 else 0.0
                    m2 = 1.0 if wo > 0 else 0.0
                recs.append((s0, m1, m2, code))
                lo_s = s0 + A_lo * m1 - B_hi * m2
                up_s = s0 + A_hi * m1 - B_lo * m2
            if up_s > best_up:
                best_up = up_s
            if lo_s > best_lo:
                best_lo = lo_s
                code_lo = code
                if best_lo >= beta:
                    break
        if best_lo >= beta:
            break
    if best_lo >= beta:
        # Cắt tỉa beta: giá trị thật >= best_lo
        if code_lo is not None and code_lo >= 100:
            ks = _KILLERS.setdefault(ply, [])
            if code_lo not in ks:
                ks.insert(0, code_lo)
                del ks[2:]
        if _S["reps"] == reps0:
            _TT[key] = (1, 1, _to_tt(best_lo, ply), code_lo)
        return best_lo
    if not recs:
        return None                 # không có nước nào (bị kẹt): để negamax xử lý
    if best_up <= alpha0:
        # Thua cửa sổ dù a, b lớn nhất: giá trị thật <= best_up
        if _S["reps"] == reps0:
            _TT[key] = (1, 2, _to_tt(best_up, ply), code_lo)
        return best_up
    if not exact:
        a, b = _exact_ab(a, b, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
    best, code = _best_exact(recs, a, b)
    if best >= beta and code is not None and code >= 100:
        ks = _KILLERS.setdefault(ply, [])
        if code not in ks:
            ks.insert(0, code)
            del ks[2:]
    if _S["reps"] == reps0:
        flag = 0
        if best <= alpha0:
            flag = 2
        elif best >= beta:
            flag = 1
        _TT[key] = (1, flag, _to_tt(best, ply), code)
    return best


def _exact_ab(a, b, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    """Tính nốt a, b chưa biết (xem frontier)."""
    if a is None:
        a = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw) - WALL_VALUE
    if b is None:
        b = first_wall_value(pm, gm, po, go, U, D, L, R, hw, vw) - WALL_VALUE
    return a, b


def _best_exact(recs, a, b):
    best, code = -INF * WIN, None
    for s0, m1, m2, c in recs:
        s = s0 + a * m1 - b * m2
        if s > best:
            best, code = s, c
    return best, code


def negamax(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, depth, alpha, beta, ply,
            can_null=True):
    """Điểm tốt nhất cho bên đang đi (ở pm) khi còn depth ply."""
    S = _S
    S["nodes"] += 1
    if (S["nodes"] & 15) == 0 and _now() > S["deadline"]:
        raise _Timeout()
    if do == 0:
        return -(WIN - ply)                 # đối thủ vừa về đích
    if depth <= 0:
        if NEAR2[pm] & BIT[po]:
            return qsearch(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply,
                           QS_DEPTH)
        return evaluate(dm, do, wm, wo, WV[gm], WV[go])
    goal = ROWMASK[gm]
    moves = pawn_moves(pm, po, U, D, L, R)
    for m in moves:
        if BIT[m] & goal:
            return WIN - ply - 1            # về đích ngay
    if wm == 0 and wo == 0 and abs(dm - do) >= 2:
        return evaluate(dm, do, wm, wo, WALL_VALUE, WALL_VALUE)

    key = (pm, po, gm, hw, vw, wm, wo)
    if ply > 0 and _HISTORY.get(key, 0) >= 2:
        S["reps"] += 1                      # điểm này phụ thuộc lịch sử ván: không lưu TT
        # Lặp thế cờ = hòa. Hòa chỉ được 1 điểm (thắng 3) nên coi là hơi xấu cho mình.
        return -S["contempt"] if gm == S["root_g"] else S["contempt"]
    tt = _TT
    entry = tt.get(key)
    tt_code = None
    alpha0 = alpha
    if entry is not None:
        e_depth, e_flag, e_score, tt_code = entry
        if e_depth >= depth:
            e_score = _from_tt(e_score, ply)
            if e_flag == 0:
                return e_score
            if e_flag == 1 and e_score > alpha:
                alpha = e_score
            elif e_flag == 2 and e_score < beta:
                beta = e_score
            if alpha >= beta:
                return e_score
    reps0 = S["reps"]

    if depth == 1:
        r = frontier(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo, dm, do, alpha, beta, ply,
                     moves, key, tt_code, reps0)
        if r is not None:
            return r
        return -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm, 0, -beta, -alpha, ply + 1)

    if (USE_NMP and can_null and depth >= NMP_MIN_DEPTH and beta < DECIDED
            and not (NEAR2[pm] & BIT[po]) and evaluate(dm, do, wm, wo, WV[gm], WV[go]) >= beta):
        # Null move: thử "đứng yên" với độ sâu giảm; vẫn >= beta thì cắt tỉa luôn
        score = -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm, do, dm,
                         depth - 1 - NMP_R, -beta, -beta + EPS, ply + 1, False)
        if score >= beta:
            return score

    lazy = tt_code is None or tt_code < 100
    killers = _KILLERS.get(ply, ())
    best = -INF * WIN
    best_code = None
    searched = 0
    done = False
    d1 = depth - 1
    for phase in (0, 1):
        # Sinh nước lười: nếu nước tốt nhất đã biết không phải tường thì xét đi quân trước
        if phase == 0:
            gl = goal_layers(gm, U, D, L, R, hw, vw)
            dmap = gl[1]
            kids = []
            for m in moves:
                dm2 = dmap.get(m)
                if dm2 is None:
                    dm2 = near_dist(gl, m, dm)
                kids.append((do - dm2, m, dm2, do))
            if not lazy and wm > 0:
                kids.extend(wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do,
                                      MAX_WALL_CHILDREN, killers))
        else:
            if not lazy or wm == 0:
                break
            kids = wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, MAX_WALL_CHILDREN, killers)
        kids.sort(key=_K0, reverse=True)
        if tt_code is not None:
            for i, k in enumerate(kids):
                if k[1] == tt_code:
                    if i:
                        kids.insert(0, kids.pop(i))
                    break
        for _, code, dm2, do2 in kids:
            if code < 100:
                pm2 = code
                U2, D2, L2, R2, hw2, vw2, wm2 = U, D, L, R, hw, vw, wm
            else:
                w = code - 100
                pm2 = pm
                if w < 64:
                    hw2 = hw | W_ABIT[w]
                    vw2 = vw
                    U2 = U & KEEP_U[w]
                    D2 = D & KEEP_D[w]
                    L2, R2 = L, R
                else:
                    hw2 = hw
                    vw2 = vw | W_ABIT[w]
                    U2, D2 = U, D
                    L2 = L & KEEP_L[w]
                    R2 = R & KEEP_R[w]
                wm2 = wm - 1
            if searched == 0:
                score = -negamax(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2,
                                 wo, wm2, do2, dm2, d1, -beta, -alpha, ply + 1)
            else:
                # PVS: cửa sổ hẹp, chỉ tìm lại đầy đủ nếu nước này có vẻ tốt hơn
                score = -negamax(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2,
                                 wo, wm2, do2, dm2, d1, -alpha - EPS, -alpha, ply + 1)
                if score > alpha and score < beta:
                    score = -negamax(po, pm2, go, gm, U2, D2, L2, R2, hw2, vw2,
                                     wo, wm2, do2, dm2, d1, -beta, -alpha, ply + 1)
            searched += 1
            if score > best:
                best = score
                best_code = code
            if score > alpha:
                alpha = score
            if alpha >= beta:
                if code >= 100:
                    ks = _KILLERS.setdefault(ply, [])
                    if code not in ks:
                        ks.insert(0, code)
                        del ks[2:]
                done = True
                break
        if done:
            break
    if searched == 0:
        # Bị kẹt không đi được: đứng yên nhường lượt
        return -negamax(po, pm, go, gm, U, D, L, R, hw, vw, wo, wm,
                        do, dm, depth - 1, -beta, -alpha, ply + 1)
    if S["reps"] == reps0:
        flag = 0
        if best <= alpha0:
            flag = 2            # cận trên
        elif best >= beta:
            flag = 1            # cận dưới
        tt[key] = (depth, flag, _to_tt(best, ply), best_code)
    return best


def root_children(pm, po, gm, go, U, D, L, R, hw, vw, wm, dm, do):
    """Con của gốc: mọi nước đi quân + tối đa ROOT_WALLS tường."""
    out = []
    gl = goal_layers(gm, U, D, L, R, hw, vw)
    dmap = gl[1]
    for m in pawn_moves(pm, po, U, D, L, R):
        dm2 = dmap.get(m)
        if dm2 is None:
            dm2 = near_dist(gl, m, dm)
        out.append((do - dm2, m, dm2, do))
    if wm > 0:
        out.extend(wall_kids(pm, po, gm, go, U, D, L, R, hw, vw, dm, do, ROOT_WALLS,
                             _KILLERS.get(0, ())))
    out.sort(key=_K0, reverse=True)
    return out


def child_state(kid, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    """Trạng thái (nhìn từ phía đối thủ) sau khi bên đang đi chơi nước kid."""
    _, code, dm2, do2 = kid
    if code < 100:
        return (po, code, go, gm, U, D, L, R, hw, vw, wo, wm, do2, dm2)
    w = code - 100
    if w < 64:
        hw2, vw2 = hw | W_ABIT[w], vw
        U2, D2, L2, R2 = U & KEEP_U[w], D & KEEP_D[w], L, R
    else:
        hw2, vw2 = hw, vw | W_ABIT[w]
        U2, D2, L2, R2 = U, D, L & KEEP_L[w], R & KEEP_R[w]
    return (po, pm, go, gm, U2, D2, L2, R2, hw2, vw2, wo, wm - 1, do2, dm2)


def search_root(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo):
    """Đào sâu dần depth = 1, 2, 3... tới khi hết giờ. Trả về (mã nước, điểm, độ sâu)."""
    dm = bfs_dist(pm, gm, U, D, L, R, hw, vw)
    do = bfs_dist(po, go, U, D, L, R, hw, vw)
    kids = root_children(pm, po, gm, go, U, D, L, R, hw, vw, wm, dm, do)
    if not kids:
        return None, 0, 0
    entry = _TT.get((pm, po, gm, hw, vw, wm, wo))
    if entry is not None and entry[3] is not None:
        for i, k in enumerate(kids):
            if k[1] == entry[3]:
                kids.insert(0, kids.pop(i))
                break
    states = {k[1]: child_state(k, pm, po, gm, go, U, D, L, R, hw, vw, wm, wo) for k in kids}
    recent = set(_MYPOS[-SHUFFLE_WINDOW - 1:-1]) if SHUFFLE_PENALTY else ()
    best_code, best_score, done_depth = kids[0][1], -INF * WIN, 0
    prev_code = None
    scores = {}
    depth = 1
    while depth <= MAX_DEPTH:
        if scores:
            kids.sort(key=lambda t: -scores.get(t[1], -INF * WIN))
        alpha, beta = -INF * WIN, INF * WIN
        iter_best, iter_code = -INF * WIN, None
        new_scores = {}
        first = kids[0][1]
        proven = None
        try:
            for i, k in enumerate(kids):
                code = k[1]
                args = states[code]
                if i == 0:
                    s = -negamax(*args, depth - 1, -beta, -alpha, 1)
                else:
                    s = -negamax(*args, depth - 1, -alpha - EPS, -alpha, 1)
                    if s > alpha:
                        proven = code       # đã chứng minh tốt hơn: hết giờ vẫn chọn được
                        s = -negamax(*args, depth - 1, -beta, -alpha, 1)
                        proven = None
                a = args
                rep = _HISTORY.get((a[0], a[1], a[2], a[8], a[9], a[10], a[11]), 0)
                if rep >= 1 and s > 0:
                    s -= 0.3 * rep          # phạt nhẹ nước dẫn tới thế cờ đã lặp
                if code < 100:
                    urg = _S["urg"]
                    if code in recent and k[2] >= dm:
                        s -= SHUFFLE_PENALTY * (1.0 + urg)   # đi qua đi lại không tiến lên
                    if urg:
                        s += URG_BONUS * urg * (dm - k[2])   # gần hết lượt: ưu tiên tiến lên
                new_scores[code] = s
                if s > iter_best or iter_code is None:
                    iter_best, iter_code = s, code
                if s > alpha:
                    alpha = s
        except _Timeout:
            if done_depth > 0:
                if proven is not None:
                    best_code = proven
                elif iter_code is not None and iter_code != first:
                    best_code, best_score = iter_code, iter_best
            break
        scores = new_scores
        if iter_best <= -(WIN - 1000) and done_depth > 0 and best_score > -(WIN - 1000):
            # Mọi nước đều thua bắt buộc: giữ nước của độ sâu trước (vẫn tiến về đích)
            prev_code = best_code
            break
        best_code, best_score, done_depth = iter_code, iter_best, depth
        if abs(best_score) >= WIN - 1000:
            break
        if len(kids) == 1:
            break
        depth += 1
    if prev_code is not None:
        return prev_code, best_score, done_depth
    return best_code, best_score, done_depth


# ---------------------------------------------------------------------------
# 6. GIAO TIẾP VỚI ARENA
# ---------------------------------------------------------------------------

def safe_action(observation):
    """Nước an toàn: ô trong legal_moves có distances nhỏ nhất; không có thì pass."""
    try:
        moves = observation.get("legal_moves") or []
        dist = observation.get("distances")
        best, best_d = None, None
        for mv in moves:
            d = dist[mv[0]][mv[1]] if dist else 0
            if d < 0:
                d = 10 ** 6
            if best is None or d < best_d:
                best, best_d = mv, d
        if best is not None:
            return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception:
        pass
    return {"type": "pass"}


def _record_history(key):
    _HISTORY[key] = _HISTORY.get(key, 0) + 1


def decide(observation, start):
    """Phân tích observation, chạy tìm kiếm và trả về hành động hợp lệ."""
    you, opp = observation["you"], observation["opponent"]
    pm = you["position"][0] * 9 + you["position"][1]
    po = opp["position"][0] * 9 + opp["position"][1]
    gm, go = int(you["goal_row"]), int(opp["goal_row"])
    wm, wo = int(you["walls_remaining"]), int(opp["walls_remaining"])
    legal_moves = [tuple(m) for m in observation.get("legal_moves") or []]
    legal_walls = set()
    for lw in observation.get("legal_walls") or []:
        legal_walls.add((lw["orientation"], int(lw["row"]), int(lw["col"])))

    # 1) Về đích được ngay thì đi ngay
    for m in legal_moves:
        if m[0] == gm:
            return {"type": "move", "to": [int(m[0]), int(m[1])]}

    hw = vw = 0
    for r, c in observation["walls"]["horizontal"]:
        hw |= _abit(int(r), int(c))
    for r, c in observation["walls"]["vertical"]:
        vw |= _abit(int(r), int(c))
    U, D, L, R = masks_from_walls(hw, vw)

    # Bộ nhớ đệm: xóa khi ván mới bắt đầu hoặc quá lớn
    turn = int(observation.get("turn", 0))
    new_game = turn < _LAST_TURN[0]
    for cache, limit in ((_DIST_CACHE, 300000), (_PI_CACHE, 60000), (_GL_CACHE, 60000),
                         (_OW_CACHE, 60000), (_WL_CACHE, 100000), (_FV_CACHE, 100000),
                         (_TT, TT_LIMIT)):
        if new_game or len(cache) > limit:
            cache.clear()
    if new_game:
        _HISTORY.clear()
        del _MYPOS[:]
        _S["margin"] = 0.0
    _MYPOS.append(pm)
    _LAST_TURN[0] = turn
    gc.collect(0)
    _record_history((pm, po, gm, hw, vw, wm, wo))

    # 2) Đối thủ hết tường và mình dẫn ít nhất 2 bước: đua chắc thắng, đi thẳng.
    if wo == 0:
        dm = bfs_dist(pm, gm, U, D, L, R, hw, vw)
        do = bfs_dist(po, go, U, D, L, R, hw, vw)
        if dm <= do - 2:
            best = min(pawn_moves(pm, po, U, D, L, R),
                       key=lambda m: bfs_dist(m, gm, U, D, L, R, hw, vw), default=None)
            if best is not None and (best // 9, best % 9) in legal_moves:
                return {"type": "move", "to": [best // 9, best % 9]}

    # Giá trị tường đầu tiên ở gốc (dùng ở các lá ngoài nút sát lá)
    WV[gm] = first_wall_value(po, go, pm, gm, U, D, L, R, hw, vw)
    WV[go] = first_wall_value(pm, gm, po, go, U, D, L, R, hw, vw)
    frac = turn / float(observation.get("max_turns") or 500)
    urg = max(0.0, (frac - URG_START) / (1.0 - URG_START))
    _S["urg"] = urg
    _S["root_g"] = gm
    _S["contempt"] = DRAW_CONTEMPT + DRAW_CONTEMPT_URG * urg
    budget = min(TIME_BUDGET, TIME_LIMIT)
    _S["deadline"] = start + max(0.10, budget - _S["margin"])
    _S["nodes"] = 0
    _S["reps"] = 0
    _KILLERS.clear()
    code, score, depth = search_root(pm, po, gm, go, U, D, L, R, hw, vw, wm, wo)
    _DEBUG["last"] = (depth, score, _S["nodes"])
    if code is None:
        return safe_action(observation)

    if code < 100:
        mv = (code // 9, code % 9)
        if mv in legal_moves:
            _record_history((po, code, go, hw, vw, wo, wm))
            return {"type": "move", "to": [mv[0], mv[1]]}
        return safe_action(observation)
    w = code - 100
    ori = "H" if w < 64 else "V"
    if (ori, W_ROW[w], W_COL[w]) in legal_walls:
        hw2 = hw | W_ABIT[w] if w < 64 else hw
        vw2 = vw | W_ABIT[w] if w >= 64 else vw
        _record_history((po, pm, go, hw2, vw2, wo, wm - 1))
        return {"type": "wall", "orientation": ori, "row": W_ROW[w], "col": W_COL[w]}
    return safe_action(observation)


def choose_action(observation):
    """Hàm Arena gọi mỗi lượt. Không bao giờ ném exception."""
    start = time.perf_counter()
    try:
        action = decide(observation, start)
    except Exception:
        action = safe_action(observation)
    late = time.perf_counter() - _S.get("deadline", start + TIME_BUDGET)
    if late > 0.02:
        # Máy chấm đang chậm đột xuất: tự động co ngắn thời gian các lượt kế tiếp
        _S["margin"] = min(0.25, _S.get("margin", 0.0) + 2.0 * late)
    return action