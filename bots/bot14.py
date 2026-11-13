"""
xxxxxxxx.py
Luật mà bot giả định (theo cách đọc observation và sinh hành động):
  - Lưới 9x9, hai quân; phe 0 xuất phát ở ô 76 (hàng 8, cột 4) và đi về hàng 0; phe 1 xuất phát ở
    ô 4 (hàng 0, cột 4) và đi về hàng 8.
  - Mỗi bên có 10 tường (ngang "H" / dọc "V"), mỗi tường dài 2 ô, đặt tại điểm neo (row, col) với
    row, col trong 0..7. Đặt tường không được làm mất mọi đường tới đích của một trong hai quân.
  - Được nhảy qua quân đối phương (thẳng, hoặc chéo khi phía sau quân đó bị chặn).
  - Được PASS. Ván dài tối đa max_turns lượt (mặc định 500 nếu observation không cung cấp).
 
File độc lập, chỉ dùng thư viện chuẩn (time, random, collections); cú pháp tương thích Python 3.8+.
Điểm vào: choose_action(observation) -> dict hành động:
    {"type": "move", "to": [hàng, cột]}
    {"type": "wall", "orientation": "H" | "V", "row": r, "col": c}
    {"type": "pass"}
 
Các trường observation được dùng: player_id, turn, max_turns, you / opponent (position,
walls_remaining), walls (horizontal, vertical), legal_moves, legal_walls, distances.
 
=====================================================================================
0. BỐ CỤC FILE
=====================================================================================
  (a) Tầng ngoài (đầu file): hằng số chung, bảng tàn cuộc _PawnEndgame và hàm
      _try_endgame_tablebase.
  (b) Chuỗi _SRC (tuple hai chuỗi mã nguồn): _SRC[0] = động cơ P0 (phe đi trước),
      _SRC[1] = động cơ P1 (phe đi sau). Mỗi chuỗi là một chương trình hoàn chỉnh có Board, BF,
      Bot, _choose, choose_action riêng.
  (c) Tầng ngoài (cuối file): _NS, _get(k), _fallback, choose_action (điểm vào thật sự).
 
  Trạng thái toàn cục:
    Tầng ngoài: _EG (bảng tàn cuộc hiện hành), _EG_VISITS, _EG_LAST_TURN, _NS (namespace đã nạp).
    Mỗi động cơ (trong namespace riêng): _BOT (đối tượng Bot với bảng chuyển vị, killer, lịch sử
    tường), _STATE_VISITS, _CELL_VISITS, _WASTE, _LAST_TURN. Hai động cơ không dùng chung biến nào.
 
=====================================================================================
1. LUỒNG QUYẾT ĐỊNH MỖI LƯỢT (choose_action ở tầng ngoài)
=====================================================================================
  Bước 1. started = time.perf_counter(); gọi _try_endgame_tablebase(observation, started).
          Nếu trả về hành động thì dùng ngay.
  Bước 2. Nếu không: k = P0_BOT nếu player_id == 0, ngược lại P1_BOT (mặc định 0 và 1, là chỉ số
          vào _SRC). _get(k) biên dịch _SRC[k] bằng exec vào namespace riêng ở lần gọi đầu
          (lười) rồi gọi choose_action của namespace đó, truyền started để thời gian tàn cuộc đã
          dùng được tính vào ngân sách lượt.
  Bước 3. Mọi ngoại lệ ở bất kỳ bước nào => _fallback(observation).
  Trong từng động cơ, choose_action cũng có try/except riêng => _fallback của động cơ.
  _fallback: chọn trong legal_moves ô có distances nhỏ nhất (distances < 0 coi là rất lớn), hòa
  thì ưu tiên gần cột giữa (độ lệch so với cột 4); nếu không có nước đi hoặc lỗi thì PASS.
 
=====================================================================================
2. TẦNG TÀN CUỘC CHUNG (_try_endgame_tablebase, _PawnEndgame)
=====================================================================================
  2.1 Khi nào chạy
    - ENDGAME_ON = True, đối thủ có walls_remaining == 0, và max_turns - turn > 0.
    - Nếu có nước đi chạm hàng đích ngay thì trả nước đó, không dùng bảng.
 
  2.2 Dựng đồ thị di chuyển
    - _eg_build_can: can[4] là 4 bitmask 81 bit cho 4 hướng (lên, phải, xuống, trái). Bit ô i của
      can[d] bật nghĩa là đi được từ ô i theo hướng d (đã loại cạnh biên và cạnh bị tường chặn).
    - _eg_dist_map: BFS ngược theo bitboard từ hàng đích cho mọi ô, INF = 99 nếu không tới được.
    - _moves_with(can, me, op): các ô đến được từ me khi quân kia ở op: bước thường, nhảy thẳng
      qua quân, nhảy chéo khi phía sau quân bị chặn.
 
  2.3 Bảng giải ngược (_PawnEndgame; __slots__: key, graph, contact, maps, result, distance)
    - Không gian: 2 (bên đi) x 81 x 81 (vị trí hai quân) trạng thái.
    - graph[i]: các ô kề đi được; contact[i*81+j]: các ô đến được từ i khi quân kia ở j (kề i).
    - Khởi tạo: result = 2 (sentinel cho trạng thái không hợp lệ hoặc quân không tới được đích).
      Trạng thái hợp lệ nhận 0 (chưa biết); trạng thái kết thúc (quân phe 0 ở hàng 0 hoặc
      quân phe 1 ở hàng 8) được gán nhãn thắng/thua cho cả hai bên đi và đưa vào hàng đợi.
    - remaining[s] = số nước đi (cộng 1 nếu ALLOW_PASS) của bên đi ở trạng thái s.
    - Lan ngược theo hàng đợi: trạng thái cha có một nước dẫn tới trạng thái con mà bên kia thua
      => cha thắng, khoảng cách = con + 1. Mỗi lần có con thắng cho bên kia thì remaining của cha
      giảm 1 và longest[cha] cập nhật; khi remaining = 0 cha thua với khoảng cách = longest + 1.
      Trạng thái không được gán nhãn là hòa.
    - Tiền nhiệm: đa số trạng thái dùng thẳng graph_plus (kề + đứng yên khi cho phép PASS).
      Các cặp ô (u, v) mà quân ở v có thể ảnh hưởng nước đi của quân ở u (kề, cách 1 bước, hoặc
      có nhảy) có danh sách tiền nhiệm đặc biệt special_preds, tiền tính một lần. Nhờ vậy không
      phải lưu danh sách previous cho từng trạng thái.
    - Hạn chót: deadline tuyệt đối = started + TABLEBASE_BUDGET (0.22s); kiểm tra mỗi 16 ô p0
      ở bước khởi tạo và mỗi 1024 trạng thái ở bước lan ngược; quá hạn ném _EGTimeout =>
      _try_endgame_tablebase trả None.
    - probe(p0, p1, turn) => (kết quả, khoảng cách tính theo lượt); sentinel 2 đổi thành 0.
    - Cache: _EG giữ bảng; chỉ dựng lại khi can_key (tập tường) khác _EG.key.
 
  2.4 Quy tắc dùng bảng
    - Nếu mình còn tường và bảng chưa có / khác cấu hình: nếu d_mình > d_đối thủ + 1 thì bỏ qua
      (trả None) vì không thể thắng chỉ bằng đi quân, khỏi tốn thời gian giải.
    - Nếu mình còn tường: chỉ dùng bảng khi bảng chứng minh thắng và số lượt tới thắng <=
      turns_left; ngược lại trả None để động cơ tìm kiếm tính cả nước đặt tường.
    - Nếu mình cũng hết tường: luôn dùng nước tốt nhất theo bảng, kể cả hòa hoặc thua.
    - Chọn nước (_PawnEndgame.action): xét các ô đến được cộng nước đứng yên (PASS), bỏ ô không
      nằm trong legal_moves. Khóa so sánh (lớn hơn là tốt hơn):
        (kết quả, nhịp, tiến độ, áp lực, ít lặp, ưu tiên đi hơn PASS)
        kết quả: thắng > hòa > thua; kết quả bị coi là hòa nếu số lượt cần vượt turns_left.
        nhịp: thắng thì càng nhanh càng tốt, thua thì càng lâu càng tốt.
        tiến độ: ô đích có khoảng cách tới hàng đích của mình nhỏ hơn.
        áp lực: (khi hòa và còn >= 2 lượt) tỉ lệ nước đáp trả của đối thủ mà sau đó mình vẫn
        còn đường thắng trong turns_left - 2.
        ít lặp: ít ghé cặp (ô mình mới, ô đối thủ) trong _EG_VISITS.
    - _EG_VISITS: số lần ghé từng cặp (ô mình, ô đối thủ), trần 32 mỗi cặp; xóa khi
      turn <= 2 hoặc turn <= lần gọi trước; nếu vượt 256 mục thì rút còn cặp hiện tại.
    - Nếu action trả về chính ô đang đứng thì phát {"type": "pass"}.
 
=====================================================================================
3. HAI ĐỘNG CƠ TÌM KIẾM (P0 và P1)
=====================================================================================
  3.1 Biểu diễn bàn cờ (giống nhau ở cả hai)
    - Ô 0..80 (hàng * 9 + cột); tường 0..127 (0..63 ngang, 64..127 dọc; id = (0|64) + r*8 + c).
    - Hằng số chung: INF = 99 (không tới được), WIN = 1.000.000, điểm thắng/thua chắc dùng
      ngưỡng WIN - 500, bước DIRS = (-9, +1, +9, -1).
    - Bảng tiền tính cho 128 tường: WALL_EDGES (4 cạnh bị chặn), WALL_PTS (3 điểm lưới 10x10 mà
      tường chạm), CONFLICT (các tường trở nên bất hợp lệ khi đặt tường này: cùng điểm neo, cùng
      hướng chồng lên, và hướng còn lại cùng neo), WALL_CELL_MASK (các ô chịu ảnh hưởng),
      WALL_MASKS (hướng, bitmask bật/tắt cạnh), BLOCKERS[ô][hướng] (các tường chặn cạnh đó).
    - Board: pos, left, can[4], forbid[128] (đếm số tường đã đặt làm tường này bất hợp lệ),
      pts[100] (đếm số tường/biên chạm từng điểm lưới), turn, whash.
    - put_wall/remove_wall: bật/tắt cạnh bằng WALL_MASKS (O(1)), cập nhật forbid theo CONFLICT, pts
      và whash.
    - wall_ok: tường hợp lệ khi forbid == 0 và còn tường; chỉ khi chạm >= 2 điểm lưới đã bị chiếm
      (pts) mới đặt thử và BFS xác nhận cả hai quân còn đường tới đích.
    - BF (BFS ngược lười): layers (các lớp khoảng cách từ hàng đích), seen, can của 4 hướng, cache
      dist. dist(ô) mở rộng lớp tới khi gặp ô; trả INF nếu hết lớp.
      fields_after_wall: nếu tường nằm ngoài vùng seen thì clone BF cũ (không BFS lại), ngược lại
      dựng BF mới từ đầu.
    - pawn_moves: bước, nhảy thẳng, nhảy chéo; duyệt hướng theo thứ tự ưu tiên về phía đích của
      bên đi. KHÔNG sinh PASS: cây tìm kiếm chỉ có nước đi quân và đặt tường.
    - zkey: Zobrist 64 bit: whash ^ ZP[0][pos0] ^ ZP[1][pos1] ^ ZLEFT[left0*11+left1] ^ (ZT nếu
      phe 1 đi). Hạt giống cố định (Random(20240607)) nên khóa tái lập được.
    - Bảng chuyển vị: self.tt[key] = (depth, cờ, giá trị, nước tốt nhất); cờ 0 chính xác, 1 cận
      dưới, 2 cận trên. Điểm thắng/thua lưu kèm ply và hoàn lại khi đọc. Chỉ ghi đè mục cũ nếu
      độ sâu mới >= độ sâu cũ. TT giữ nguyên giữa các lượt (không xóa khi sang ván mới); chỉ xóa
      sạch khi vượt TT_SIZE = 320000.
    - Killer: killers1[ply], killers2[ply] (ply < 64); whist[phe][tường] (lịch sử tường) cộng
      depth^2 mỗi lần gây cắt tỉa; mỗi lần gọi choose ở gốc, whist của cả hai phe bị dịch phải 2 bit.
 
  3.2 Luồng ở gốc (Bot.choose; giống hệt nhau ở P0 và P1)
    - Có nước đi chạm đích hợp lệ => trả ngay.
    - Dựng F = (BF phe 0, BF phe 1), d_me, d_op; pawns = nước đi quân hợp lệ qua root_ok;
      fwd = các nước làm d_me giảm.
    - force_fwd (xem mục 4): cộng SOFT_FWD_PEN (80) vào repeat[] của mọi nước đi quân không tiến
      (khi có ít nhất một nước tiến).
    - Sắp pawns theo (khoảng cách tới đích, repeat, độ lệch cột giữa).
    - _race_safe (mục 3.5): chạy khi có nước tiến và d_me < d_op, giới hạn thời gian
      min(soft_dl, now + 0.06). Nếu an toàn: chỉ xét nước tiến (chỉ có một thì trả luôn), không
      xét tường. Nếu không an toàn: nếu mình hết tường và có nước tiến thì chỉ xét nước tiến;
      nếu còn tường thì gọi _root_walls.
    - Lặp sâu dần depth = 1..max_depth (30). Với mỗi nước ở gốc (theo thứ tự đã sắp):
        quá deadline => ném _Timeout;
        nước đi quân: cộng bộ phạt repeat vào cửa sổ và trừ khỏi điểm;
        nước đặt tường: đặt, fields_after_wall, giảm left, tìm, rồi hoàn tác trong finally;
        nước đầu tiên tìm cửa sổ đầy đủ; từ nước thứ hai, khi depth >= 3 và alpha > -WIN + 1000,
        tìm cửa sổ 0 rồi tìm lại cửa sổ đầy đủ nếu kết quả > alpha.
    - Không dùng aspiration window; beta luôn là 2*WIN.
    - Cập nhật tức thời: mỗi khi một nước vượt điểm tốt nhất của vòng (cur_score), gán ngay
      best_action. Nên nếu hết giờ giữa vòng vẫn có nước tốt nhất đã biết.
    - Cuối mỗi vòng: tính cờ bất ổn, sắp lại actions theo điểm vòng này, và dừng sớm nếu
      |điểm| >= WIN - 500 với depth >= WIN - |điểm| (đã thấy thắng/thua chắc trong tầm).
 
  3.3 Cây tìm kiếm (_search; negamax + PVS)
    - Kết thúc nếu đối thủ vừa chạm đích: trả -(WIN - ply). Lá (depth <= 0): _eval, không có
      quiescence.
    - Đọc TT: nếu độ sâu đủ thì dùng cờ để cắt (cờ 0 trả thẳng; cờ 1 nâng alpha; cờ 2 hạ beta).
    - Thứ tự duyệt: (1) các nước đi quân, đưa nước TT lên đầu nếu có; mỗi nước chạm đích trả
      WIN - (ply + 1) mà không cần đệ quy; từ nước thứ hai khi depth >= 2 và đã có alpha > a0
      dùng cửa sổ 0, tìm lại đầy đủ nếu kết quả nằm giữa alpha và beta. (2) các nước đặt tường
      (nếu còn tường).
    - Ứng viên tường: _wall_cands(b, F[o], op, o, INNER_WALLS); đặt nước tường của TT lên đầu,
      sau đó killer, rồi theo lịch sử tường.
    - depth == 1 (nhánh riêng cho tường):
        bỏ qua tường không chạm vùng ô đã mở rộng trong BFS của đối thủ (và không chạm >= 2 điểm
        lưới đã chiếm) khi mình đang dẫn (d_me < d_op);
        bỏ qua tường không làm đối thủ chậm thêm khi mình đang dẫn;
        tính cận trên ub bằng _eval_from_dists; bỏ tường nếu ub <= ngưỡng (alpha hoặc best);
        chỉ BFS lại khoảng cách của mình khi tường chạm vùng BFS của mình hoặc chạm >= 2 điểm
        lưới đã chiếm; bỏ tường nếu mình hết đường đi.
    - depth >= 2 (nhánh thường cho tường): đặt tường, fields_after_wall; nếu tường chạm >= 2
      điểm đã chiếm mà một trong hai quân hết đường thì bỏ; sau đó tìm PVS (cửa sổ 0 khi
      alpha > a0) hoặc LMR (xem 3.7).
    - Gây cắt tỉa bằng tường: ghi killer và cộng depth^2 vào lịch sử tường; lưu TT với cờ.
 
  3.4 Hàm đánh giá (_eval_from_dists), góc nhìn bên đang đi
    - Thắng/thua sớm (trả điểm gần WIN//2):
        d_mình <= 1 và (quân đối thủ không đứng ngay trước mặt hoặc có nước chạm đích)
          => WIN//2 - d_mình;
        đối thủ hết tường và d_mình <= d_đối thủ => WIN//2 - d_mình;
        mình hết tường và d_đối thủ < d_mình => -(WIN//2 - d_đối thủ).
    - Điểm thường: W_DIST (100) * (d_đối thủ - d_mình) + chênh lệch tường + TEMPO (20)
      + CENTER_W (2) * (độ lệch cột giữa của đối thủ - của mình).
    - Chênh lệch tường = _WPOW[tường mình] - _WPOW[tường đối thủ], _WPOW[k] = round(W_WALLS * k^0.8)
      (WALL_POW = 0.8). Khi min(d_mình, d_đối thủ) <= 4, nhân với (min_d + 2) rồi chia nguyên 6.
    - Thưởng nhảy JUMP_BONUS: khi d_mình >= 2 và quân đối thủ kề quân mình đúng hướng tiến, nếu
      nhảy được qua quân (thẳng, hoặc chéo khi phía sau quân kia bị chặn) và ô đáp nằm trên đường
      ngắn nhất của mình (thuộc lớp d_mình - 2) thì cộng JUMP_BONUS (một lần).
 
  3.5 Kiểm tra cuộc đua an toàn (_race_safe)
    - Mục đích: nếu mình dẫn và chạy thẳng vẫn thắng dù đối thủ dùng hết tường để cản, thì khỏi
      cân nhắc tường, tiết kiệm thời gian.
    - Nếu đối thủ hết tường: P0 an toàn khi d_mình < d_đối thủ; P1 an toàn khi d_mình <= d_đối thủ.
    - Ngược lại nếu d_mình + 2 > d_đối thủ thì không an toàn.
    - Giả lập (trên bản sao bàn cờ, lượt đối thủ): lặp j = 1..số tường đối thủ còn: sinh ứng viên
      chặn đường ngắn nhất của mình (P0: _wall_cands tối đa 16; P1: _dag_walls, xét 18 đầu), đặt
      thử từng tường, đo net = (mình chậm thêm) - (đối thủ chậm thêm); chọn net lớn nhất (> 0).
      Nếu không có net > 0 thì cho phép tối đa MỘT tường net = 0 không làm mình chậm thêm
      (P0 chọn tường chạm nhiều điểm lưới nhất; P1 chọn theo touch*4 + is_prim*2 - step), và
      chỉ khi j < số tường còn. Sau mỗi tường: nếu d_mình_gốc + thiệt hại_ròng + 2 >
      d_đối thủ_gốc + j thì kết luận không an toàn. Hết giờ (PRE_TIME 0.06s) hoặc ngoại lệ
      => không an toàn.
 
  3.6 Quản lý thời gian
    - P0: SOFT_TIME = 0.66s, HARD_TIME = 0.76s. P1: SOFT_TIME = 0.67s, HARD_TIME = 0.77s;
      tính từ started (gồm cả thời gian bảng tàn cuộc đã tốn).
    - Đầu mỗi vòng lặp sâu: deadline = HARD nếu depth > 2 và vòng trước bất ổn, ngược lại SOFT;
      nếu now >= deadline thì dừng.
    - Bất ổn: depth >= 2 và (nước tốt nhất đổi so với vòng trước hoặc điểm tụt hơn 120). Ngoài ra,
      giữa vòng, nếu nước đầu tiên (PV) có điểm thấp hơn điểm vòng trước hơn 120 (depth > 2) thì
      deadline chuyển sang HARD ngay.
    - Trong cây, kiểm tra giờ định kỳ: mỗi 64 nút (P0), mỗi 32 nút (P1). Quá deadline => _Timeout,
      trả best_action hiện có. Vòng đang chạy dở bị cắt ngay tại deadline.
 
  3.7 Khác biệt P0 và P1
    - Sinh tường ở gốc:
        P0: _wall_cands(ROOT_POOL = 40): duyệt dọc đường ngắn nhất chính của đối thủ (các tường
            chặn mỗi bước theo thứ tự hướng ưu tiên), sau đó mở rộng sang các nhánh ngắn nhất
            khác (DAG bằng bitboard) tới khi đủ giới hạn. Mỗi tường được chấm
            net = (đối thủ chậm thêm) - (mình chậm thêm); loại net < 0 (trừ ngoại lệ phòng thủ
            bên dưới); sắp theo net giảm dần, hòa theo thứ tự trong hồ; giữ ROOT_WALLS = 20.
        P1: _dag_walls duyệt toàn bộ DAG đường ngắn nhất của đối thủ, trả cho mỗi tường
            (w, step, is_prim, touch, layer_width): bước tính từ vị trí đối thủ, có thuộc đường
            chính không, số điểm lưới đã chiếm, số ô của lớp DAG tại bước đó. Chấm net như P0.
            Tường net > 0 (và gain > 0) xếp theo priority = net*20 + gain*8 + touch*4 +
            is_prim*2 - step. Nếu chưa đủ ROOT_WALLS = 20 thì bổ sung tối đa 6 tường net <= 0,
            xếp theo priority = (12 nếu là tường phòng thủ) + touch*5 + is_prim*3 - step; nhóm
            net <= 0 này không dùng khi mình đang dẫn mà số tường mình <= số tường đối thủ.
    - Phòng thủ chống bẫy tường (cả P0 và P1): khi đối thủ còn tường và d_mình <= d_đối thủ + 3,
      lấy tối đa 12 tường mà ĐỐI THỦ có thể đặt vào đường ngắn nhất của mình, tính
      harm = (mình chậm thêm) - (đối thủ chậm thêm); chọn tối đa 2 tường có harm lớn nhất và
      >= 2, rồi thêm vào hồ các tường CONFLICT[tường đó] (đặt chúng khiến tường đe dọa không
      thể đặt được). Nếu harm >= 3, các tường phòng thủ này được giữ ngay cả khi net = -1.
      (P1 gán cho chúng step = 3, is_prim = 0, layer_width = 2.)
    - Tường trong cây: P0: INNER_WALLS = 12 (sắp theo lịch sử tường khi > 2 ứng viên); P1:
      INNER_WALLS = 9; nếu nhiều hơn giới hạn thì chấm điểm cấu trúc: is_prim*6 - step*3, +12 nếu
      step = 0, +6 nếu step = 1, +14 nếu chạm >= 2 điểm (hoặc +6 nếu chạm 1), +8 nếu lớp DAG
      rộng 1 (hoặc +3 nếu rộng 2), hòa theo lịch sử tường.
    - LMR (giảm độ sâu cho tường xếp muộn): P0 dùng bộ đếm wi, chỉ đếm tường đã qua kiểm tra
      hợp lệ, áp dụng khi wi >= LMR_FROM = 4 và depth >= 3; P1 dùng chỉ số trong danh sách
      w_idx >= 3 và depth >= 3. Cả hai đều tìm cửa sổ 0 ở depth - 2 và chỉ tìm lại ở depth - 1
      nếu vượt alpha; chỉ bật khi alpha > -WIN + 1000.
    - Hằng số đánh giá: P0: W_WALLS = 220, JUMP_BONUS = 45; P1: W_WALLS = 210, JUMP_BONUS = 65.
    - BLOCKERS: P1 sắp các tường theo độ gần cột; P0 giữ thứ tự tự nhiên.
    - Chống lặp: xem mục 4 (cách tính phạt khác nhau).
 
=====================================================================================
4. CHỐNG LẶP VÀ ĐI VÒNG (_choose của mỗi động cơ)
=====================================================================================
  Dữ liệu: _STATE_VISITS: (pid, ô, ô đối thủ, số tường trên bàn); _CELL_VISITS: (pid, ô, số tường
  trên bàn); _WASTE[pid]: dãy 0/1, 1 nếu nước đi quân vừa rồi không làm khoảng cách giảm (nước
  đặt tường ghi 0). Bị xóa khi turn <= 2 hoặc turn nhỏ hơn lần gọi trước của cùng người chơi.
  Kết quả: từ điển repeat (ô => điểm phạt) và force_fwd = (tổng 6 mục _WASTE gần nhất >= 2).
  Điểm phạt chỉ áp dụng cho nước đi quân ở gốc.
    - P0: phạt = số lần lặp trạng thái đích * STATE_REPEAT_PEN (250) + phạt ô, trong đó phạt ô
      = 45 nếu ô đã ghé đúng 1 lần và không tiến gần đích, ngược lại (số lần ghé - 1) *
      CELL_REPEAT_PEN (90). Bộ đếm trạng thái được ghi SAU khi chọn nước đi quân (cho ô đích).
    - P1: dùng bảng _REP_PEN = (0, 0, 60, 160, 320) và _CELL_PEN = (0, 45, 110, 220, 350), chỉ số
      cắt ở 4. Bộ đếm ghi NGAY ĐẦU lượt cho trạng thái hiện tại. Nước tiến mới (số lần lặp
      trạng thái < 3, số lần ghé ô < 2, khoảng cách giảm) không bị phạt. Phạt = max(phạt trạng
      thái, phạt ô); phạt ô dùng chỉ số c_cnt nếu nước không tiến, c_cnt - 1 nếu tiến.
 
=====================================================================================
5. THAM SỐ CẤU HÌNH
=====================================================================================
  Tầng ngoài: ENDGAME_ON, ALLOW_PASS, TABLEBASE_BUDGET (0.22s), P0_BOT, P1_BOT.
  Trong từng động cơ: SOFT_TIME, HARD_TIME, PRE_TIME (P0 đặt tên PRE_TIME_LIMIT = 0.06),
  ROOT_WALLS (20), ROOT_POOL (40, chỉ P0), INNER_WALLS (P0 12, P1 9), LMR_FROM (4, chỉ P0),
  W_DIST (100), W_WALLS (P0 220, P1 210), WALL_POW (0.8), TEMPO (20), CENTER_W (2), JUMP_BONUS (P0 45,
  P1 65), SOFT_FWD_PEN (80), STATE_REPEAT_PEN (250) và CELL_REPEAT_PEN (90) chỉ ở P0, _REP_PEN
  và _CELL_PEN chỉ ở P1, TT_SIZE (320000), max_depth (30).
"""
import time
from collections import deque

P0_BOT, P1_BOT = 0, 1      # chỉ số động cơ trong _SRC: 0 = P0 (phe đi trước), 1 = P1 (phe đi sau)

# =============================================================================
# TẦNG CHUNG: BẢNG GIẢI NGƯỢC TÀN CUỘC 2 QUÂN CỜ (_PawnEndgame tối ưu đối xứng)
# =============================================================================
CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)
GOAL = (sum(1 << c for c in range(0, 9)),
        sum(1 << c for c in range(72, 81)))
GOAL_ROW = (0, 8)

ENDGAME_ON = True
ALLOW_PASS = True
TABLEBASE_BUDGET = 0.22


class _EGTimeout(Exception):
    """Hết thời gian dành cho việc giải bảng tàn cuộc."""


def _eg_wall_id(orient, r, c):
    return (0 if orient == 'h' else 64) + r * 8 + c


_EG_WALL_EDGES = []
for _w in range(128):
    _o, _r, _c = ('h' if _w < 64 else 'v', (_w & 63) >> 3, _w & 7)
    if _o == 'h':
        _e = ((_r * 9 + _c, 2), (_r * 9 + _c + 1, 2),
              ((_r + 1) * 9 + _c, 0), ((_r + 1) * 9 + _c + 1, 0))
    else:
        _e = ((_r * 9 + _c, 1), ((_r + 1) * 9 + _c, 1),
              (_r * 9 + _c + 1, 3), ((_r + 1) * 9 + _c + 1, 3))
    _EG_WALL_EDGES.append(_e)


def _eg_build_can(obs):
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
    for r, c in obs["walls"]["horizontal"]:
        for cell, d in _EG_WALL_EDGES[_eg_wall_id('h', r, c)]:
            can[d] &= ~(1 << cell)
    for r, c in obs["walls"]["vertical"]:
        for cell, d in _EG_WALL_EDGES[_eg_wall_id('v', r, c)]:
            can[d] &= ~(1 << cell)
    return tuple(can)


def _eg_dist_map(goal_mask, can):
    c0, c1, c2, c3 = can
    layers = [goal_mask]
    seen = goal_mask
    f = goal_mask
    while True:
        f = (((f << 9) & c0) | ((f >> 1) & c1) |
             ((f >> 9) & c2) | ((f << 1) & c3)) & ~seen
        if not f:
            break
        seen |= f
        layers.append(f)
    out = [INF] * CELLS
    for d, layer in enumerate(layers):
        rem = layer
        while rem:
            low = rem & -rem
            out[low.bit_length() - 1] = d
            rem ^= low
    return out


def _moves_with(can, me, op):
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
    """Bảng tàn cuộc giải ngược 2 x 81 x 81 trạng thái hai quân khi đối thủ hết tường.
    Sử dụng đồ thị đối xứng + special_preds giúp loại bỏ hoàn toàn 13.122 list previous."""
    __slots__ = ("key", "graph", "contact", "maps", "result", "distance")

    def __init__(self, key, can, deadline):
        self.key = key
        graph = [tuple(i + DIRS[d] for d in range(4) if (can[d] >> i) & 1) for i in range(CELLS)]
        graph_plus = [g + (i,) for i, g in enumerate(graph)] if ALLOW_PASS else graph
        adj_mask = [0] * CELLS
        contact = {}
        for i in range(CELLS):
            m = 0
            for j in graph[i]:
                m |= 1 << j
                contact[i * CELLS + j] = tuple(_moves_with(can, i, j))
            adj_mask[i] = m
        self.graph, self.contact = graph, contact

        # Tiền tính toán danh sách tiền nhiệm đặc biệt chỉ cho các cặp (u, v) cách nhau <= 2 bước (~900 cặp)
        special_preds = {}
        for v in range(CELLS):
            gv = graph[v]
            v_adj = adj_mask[v]
            affected = set(gv)
            for w in gv:
                affected.update(graph_plus[w])
                affected.update(contact[w * CELLS + v])
            affected.discard(v)
            for u in affected:
                preds = [x for x in graph_plus[u] if x != v and not ((v_adj >> x) & 1)]
                for w in gv:
                    if u in contact[w * CELLS + v] or (ALLOW_PASS and u == w):
                        preds.append(w)
                special_preds[u * CELLS + v] = tuple(preds)

        maps = [_eg_dist_map(GOAL[0], can), _eg_dist_map(GOAL[1], can)]
        self.maps = maps
        r0, r1 = maps
        span = CELLS * CELLS
        # Khởi tạo bằng 2 (sentinel cho trạng thái không hợp lệ/không tới được đích) để lọc O(1)
        result = [2] * (2 * span)
        distance = [0] * (2 * span)
        remaining = [0] * (2 * span)
        longest = [0] * (2 * span)
        pending = deque()
        push = pending.append
        cget = contact.get
        sp_get = special_preds.get
        extra = 1 if ALLOW_PASS else 0
        deg_plus = [len(g) + extra for g in graph]

        for p0 in range(CELLS):
            if (p0 & 15) == 0 and time.perf_counter() > deadline:
                raise _EGTimeout
            if r0[p0] >= INF:
                continue
            won0 = p0 // 9 == GOAL_ROW[0]
            base0 = p0 * CELLS
            dp0 = deg_plus[p0]
            for p1 in range(CELLS):
                if p1 == p0 or r1[p1] >= INF:
                    continue
                pair = base0 + p1
                st = span + pair
                if won0 or p1 // 9 == GOAL_ROW[1]:
                    w0 = 1 if won0 else -1
                    result[pair] = w0
                    result[st] = -w0
                    push(pair)
                    push(st)
                    continue
                result[pair] = 0
                result[st] = 0
                mv0 = cget(pair)
                remaining[pair] = dp0 if mv0 is None else (len(mv0) + extra)
                mv1 = cget(p1 * CELLS + p0)
                remaining[st] = deg_plus[p1] if mv1 is None else (len(mv1) + extra)

        popleft = pending.popleft
        processed = 0
        while pending:
            state = popleft()
            processed += 1
            if (processed & 1023) == 0 and time.perf_counter() > deadline:
                raise _EGTimeout
            rs = result[state]
            ds = distance[state]
            nd = ds + 1
            if state >= span:
                idx = state - span
                p0, p1 = divmod(idx, CELLS)
                for p0_prev in sp_get(idx, graph_plus[p0]):
                    parent = p0_prev * CELLS + p1
                    if result[parent]:
                        continue
                    if rs == -1:
                        result[parent] = 1
                        distance[parent] = nd
                        push(parent)
                    else:
                        remaining[parent] -= 1
                        if ds > longest[parent]:
                            longest[parent] = ds
                        if remaining[parent] == 0:
                            result[parent] = -1
                            distance[parent] = longest[parent] + 1
                            push(parent)
            else:
                p0, p1 = divmod(state, CELLS)
                base = span + p0 * CELLS
                for p1_prev in sp_get(p1 * CELLS + p0, graph_plus[p1]):
                    parent = base + p1_prev
                    if result[parent]:
                        continue
                    if rs == -1:
                        result[parent] = 1
                        distance[parent] = nd
                        push(parent)
                    else:
                        remaining[parent] -= 1
                        if ds > longest[parent]:
                            longest[parent] = ds
                        if remaining[parent] == 0:
                            result[parent] = -1
                            distance[parent] = longest[parent] + 1
                            push(parent)

        self.result, self.distance = result, distance

    def moves(self, own, enemy):
        mv = self.contact.get(own * CELLS + enemy)
        return self.graph[own] if mv is None else mv

    def probe(self, p0, p1, turn):
        s = turn * CELLS * CELLS + p0 * CELLS + p1
        r = self.result[s]
        return (0 if r == 2 else r), self.distance[s]

    def action(self, p0, p1, turn, turns_left, legal_cells, visits):
        own, enemy = (p0, p1) if turn == 0 else (p1, p0)
        best, best_key = None, None
        opts = list(self.moves(own, enemy))
        if ALLOW_PASS:
            opts.append(own)
        for dest in opts:
            if dest != own and dest not in legal_cells:
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


_EG = None
_EG_VISITS = {}
_EG_LAST_TURN = -1


def _try_endgame_tablebase(observation, started):
    """Nếu đối thủ còn 0 tường và bảng tàn cuộc chứng minh được nước thắng (hoặc cả 2 đều hết tường),
    trả về hành động tối ưu tuyệt đối từ _PawnEndgame."""
    global _EG, _EG_VISITS, _EG_LAST_TURN
    if not ENDGAME_ON or observation["opponent"]["walls_remaining"] != 0:
        return None
    pid = observation["player_id"]
    turn = observation["turn"]
    goal_row = GOAL_ROW[pid]
    for r, c in observation["legal_moves"]:
        if r == goal_row:
            return {"type": "move", "to": [int(r), int(c)]}
    max_turns = observation.get("max_turns", 500)
    turns_left = max_turns - turn
    if turns_left <= 0:
        return None

    me_cell = observation["you"]["position"][0] * 9 + observation["you"]["position"][1]
    op_cell = observation["opponent"]["position"][0] * 9 + observation["opponent"]["position"][1]
    can_key = _eg_build_can(observation)

    # Thoát sớm nếu mình vẫn còn tường nhưng đang kém đối thủ >= 2 bước (không thể thắng nếu chỉ đi quân)
    if observation["you"]["walls_remaining"] != 0 and (_EG is None or _EG.key != can_key):
        d_me = _eg_dist_map(GOAL[pid], can_key)[me_cell]
        d_op = _eg_dist_map(GOAL[1 - pid], can_key)[op_cell]
        if d_me > d_op + 1:
            return None

    try:
        if _EG is None or _EG.key != can_key:
            _EG = None
            _EG = _PawnEndgame(can_key, can_key, started + TABLEBASE_BUDGET)
    except _EGTimeout:
        return None

    p0, p1 = (me_cell, op_cell) if pid == 0 else (op_cell, me_cell)
    outcome, dist = _EG.probe(p0, p1, pid)
    # Nếu mình vẫn còn tường mà bảng chưa chứng minh thắng chắc bằng đi quân -> nhường cho Alpha-Beta tìm tường
    if observation["you"]["walls_remaining"] != 0 and not (outcome == 1 and dist <= turns_left):
        return None

    if turn <= 2 or turn <= _EG_LAST_TURN:
        _EG_VISITS = {}
    _EG_LAST_TURN = turn
    _EG_VISITS[(me_cell, op_cell)] = min(32, _EG_VISITS.get((me_cell, op_cell), 0) + 1)
    if len(_EG_VISITS) > 256:
        _EG_VISITS = {(me_cell, op_cell): _EG_VISITS[(me_cell, op_cell)]}

    legal_cells = {r * 9 + c for r, c in observation["legal_moves"]}
    dest = _EG.action(p0, p1, pid, turns_left, legal_cells, _EG_VISITS)
    if dest is None:
        return None
    if dest == me_cell:
        return {"type": "pass"} if ALLOW_PASS else None
    return {"type": "move", "to": [int(dest // 9), int(dest % 9)]}


# =============================================================================
# HAI ĐỘNG CƠ TÌM KIẾM CHUYÊN BIỆT: P0 (PHE ĐI TRƯỚC) VÀ P1 (PHE ĐI SAU)
# =============================================================================
_SRC = (
r'''"""Động cơ P0 (phe đi trước).
- Sinh tường: duyệt dọc đường ngắn nhất chính của đối thủ rồi mở rộng sang các nhánh ngắn nhất khác
  (INNER_WALLS = 12 trong cây; ROOT_POOL = 40 ứng viên ở gốc, chấm net và giữ ROOT_WALLS = 20).
- Hàm đánh giá: chênh lệch khoảng cách, giá trị tường theo lũy thừa _WPOW (W_WALLS = 220), tempo, độ lệch
  cột giữa, thưởng nhảy qua quân (JUMP_BONUS = 45), nhận diện thắng/thua sớm.
- Tìm kiếm: lặp sâu + negamax PVS + LMR, bảng chuyển vị Zobrist, 2 killer mỗi ply, lịch sử tường,
  cận trên để cắt tỉa ở depth == 1, BF-clone O(1) qua fields_after_wall, WALL_MASKS bitboard O(1).
- Ở gốc: phòng thủ CONFLICT chống tường đe dọa, kiểm tra _race_safe, cập nhật best_action tức thời.
"""

import random
import time

CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)
_OP_DIRS = ((0, 2, 1, 3), (2, 0, 1, 3))

WALL0 = 81
RED, BLUE = 0, 1
START = (76, 4)
GOAL = (sum(1 << c for c in range(0, 9)),
        sum(1 << c for c in range(72, 81)))
GOAL_ROW = (0, 8)
WIN = 1000000

SOFT_TIME = 0.66
HARD_TIME = 0.76
PRE_TIME_LIMIT = 0.06

ROOT_WALLS = 20
ROOT_POOL = 40
INNER_WALLS = 12
LMR_FROM = 4

W_DIST = 100
W_WALLS = 220
WALL_POW = 0.8
TEMPO = 20
CENTER_W = 2
JUMP_BONUS = 45

STATE_REPEAT_PEN = 250
CELL_REPEAT_PEN = 90
SOFT_FWD_PEN = 80
TT_SIZE = 320000

_WPOW = tuple(int(round(W_WALLS * (i ** WALL_POW))) for i in range(16))
_CENTER_DIST = tuple(abs((c % 9) - 4) for c in range(CELLS))

_rng = random.Random(20240607)
ZP = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
ZW = [_rng.getrandbits(64) for _ in range(128)]
ZLEFT = [_rng.getrandbits(64) for _ in range(128)]
ZT = _rng.getrandbits(64)


def wall_info(w):
    return ('h' if w < 64 else 'v', (w & 63) >> 3, w & 7)


def wall_id(orient, r, c):
    return (0 if orient == 'h' else 64) + r * 8 + c


WALL_EDGES, WALL_PTS, CONFLICT, WALL_CELL_MASK, WALL_MASKS = [], [], [], [], []
for _w in range(128):
    _o, _r, _c = wall_info(_w)
    if _o == 'h':
        _c1, _c2 = _r * 9 + _c, _r * 9 + _c + 1
        _c3, _c4 = (_r + 1) * 9 + _c, (_r + 1) * 9 + _c + 1
        _e = [(_c1, 2), (_c2, 2), (_c3, 0), (_c4, 0)]
        _mA = (1 << _c1) | (1 << _c2)
        _mB = (1 << _c3) | (1 << _c4)
        _wm = (2, _mA, ~_mA, 0, _mB, ~_mB)
        _p = [(_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2]
        _cf = [wall_id('h', _r, _c), wall_id('v', _r, _c)]
        if _c > 0:
            _cf.append(wall_id('h', _r, _c - 1))
        if _c < 7:
            _cf.append(wall_id('h', _r, _c + 1))
    else:
        _c1, _c2 = _r * 9 + _c, (_r + 1) * 9 + _c
        _c3, _c4 = _r * 9 + _c + 1, (_r + 1) * 9 + _c + 1
        _e = [(_c1, 1), (_c2, 1), (_c3, 3), (_c4, 3)]
        _mA = (1 << _c1) | (1 << _c2)
        _mB = (1 << _c3) | (1 << _c4)
        _wm = (1, _mA, ~_mA, 3, _mB, ~_mB)
        _p = [_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1]
        _cf = [wall_id('v', _r, _c), wall_id('h', _r, _c)]
        if _r > 0:
            _cf.append(wall_id('v', _r - 1, _c))
        if _r < 7:
            _cf.append(wall_id('v', _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_PTS.append(tuple(_p))
    CONFLICT.append(tuple(_cf))
    WALL_CELL_MASK.append(_mA | _mB)
    WALL_MASKS.append(_wm)
WALL_CELL_MASK = tuple(WALL_CELL_MASK)
WALL_MASKS = tuple(WALL_MASKS)

BLOCKERS = [[() for _ in range(4)] for _ in range(CELLS)]
for _cell in range(CELLS):
    for _d in range(4):
        _lst = []
        for _w in range(128):
            if (_cell, _d) in WALL_EDGES[_w]:
                _lst.append(_w)
        BLOCKERS[_cell][_d] = tuple(_lst)


def _initial_can():
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


class BF:
    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3", "cache")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can
        self.cache = {}

    def clone(self, can):
        obj = BF.__new__(BF)
        obj.layers = self.layers[:]
        obj.seen = self.seen
        obj.c0, obj.c1, obj.c2, obj.c3 = can
        obj.cache = self.cache.copy()
        return obj

    def dist(self, cell):
        res = self.cache.get(cell)
        if res is not None:
            return res
        bit = 1 << cell
        seen = self.seen
        if seen & bit:
            for k, layer in enumerate(self.layers):
                if layer & bit:
                    self.cache[cell] = k
                    return k
        layers = self.layers
        k = len(layers)
        f = layers[-1]
        c0, c1, c2, c3 = self.c0, self.c1, self.c2, self.c3
        while True:
            f = (((f << 9) & c0) | ((f >> 1) & c1) |
                 ((f >> 9) & c2) | ((f << 1) & c3)) & ~seen
            if not f:
                self.cache[cell] = INF
                return INF
            seen |= f
            self.seen = seen
            layers.append(f)
            if f & bit:
                self.cache[cell] = k
                return k
            k += 1


class Board:
    def __init__(self):
        self.pos = [START[0], START[1]]
        self.left = [10, 10]
        self.can = _initial_can()
        self.forbid = [0] * 128
        self.pts = [0] * 100
        for R in range(10):
            for C in range(10):
                if R in (0, 9) or C in (0, 9):
                    self.pts[R * 10 + C] = 1
        self.turn = RED
        self.whash = 0

    def copy(self):
        b = Board.__new__(Board)
        b.pos = self.pos[:]
        b.left = self.left[:]
        b.can = self.can[:]
        b.forbid = self.forbid[:]
        b.pts = self.pts[:]
        b.turn = self.turn
        b.whash = self.whash
        return b

    def fields(self):
        c = self.can
        return (BF(GOAL[0], c), BF(GOAL[1], c))

    def fields_after_wall(self, F, w):
        c = self.can
        m = WALL_CELL_MASK[w]
        f0, f1 = F
        return (
            BF(GOAL[0], c) if (f0.seen & m) else f0.clone(c),
            BF(GOAL[1], c) if (f1.seen & m) else f1.clone(c),
        )

    def dist(self, start, goal):
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
        t = self.turn if player is None else player
        me, op = self.pos[t], self.pos[1 - t]
        can = self.can
        out = []
        for d in _OP_DIRS[t]:
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

    def put_wall(self, w):
        dA, _, clrA, dB, _, clrB = WALL_MASKS[w]
        can = self.can
        can[dA] &= clrA
        can[dB] &= clrB
        forbid = self.forbid
        for x in CONFLICT[w]:
            forbid[x] += 1
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        pts[p0] += 1
        pts[p1] += 1
        pts[p2] += 1
        self.whash ^= ZW[w]

    def remove_wall(self, w):
        dA, setA, _, dB, setB, _ = WALL_MASKS[w]
        can = self.can
        can[dA] |= setA
        can[dB] |= setB
        forbid = self.forbid
        for x in CONFLICT[w]:
            forbid[x] -= 1
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        pts[p0] -= 1
        pts[p1] -= 1
        pts[p2] -= 1
        self.whash ^= ZW[w]

    def wall_ok(self, w, player=None):
        t = self.turn if player is None else player
        if self.left[t] <= 0 or self.forbid[w]:
            return False
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        if (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0) >= 2:
            dA, setA, clrA, dB, setB, clrB = WALL_MASKS[w]
            can = self.can
            can[dA] &= clrA
            can[dB] &= clrB
            ok = (self.dist(self.pos[0], GOAL[0]) < INF and
                  self.dist(self.pos[1], GOAL[1]) < INF)
            can[dA] |= setA
            can[dB] |= setB
            return ok
        return True

    def zkey(self):
        return (self.whash ^ ZP[0][self.pos[0]] ^ ZP[1][self.pos[1]] ^
                ZLEFT[self.left[0] * 11 + self.left[1]] ^
                (ZT if self.turn else 0))


class _Timeout(Exception):
    pass


class Bot:
    def __init__(self, max_depth=30):
        self.max_depth = max_depth
        self.tt = {}
        self.nodes = 0
        self.depth_reached = 0
        self.deadline = 0.0
        self.killers1 = [None] * 64
        self.killers2 = [None] * 64
        self.whist = [[0] * 128, [0] * 128]

    def _record_killer(self, ply, w, t, depth):
        self.whist[t][w] += depth * depth
        if ply < 64 and self.killers1[ply] != w:
            self.killers2[ply] = self.killers1[ply]
            self.killers1[ply] = w

    def _wall_cands(self, b, f_op, op_pos, op_id, limit, check=None):
        """Sinh tường chặn đường ngắn nhất của đối thủ: dọc đường chính, rồi mở rộng sang các nhánh DAG."""
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF:
            return []
        layers = f_op.layers
        can = b.can
        forbid = b.forbid
        op_dirs = _OP_DIRS[op_id]
        out = []
        seen = set()

        # 1. Đi dọc theo đường ngắn nhất chính của đối thủ
        u = op_pos
        cur_d = d
        while cur_d > 0:
            prev = layers[cur_d - 1]
            nxt = -1
            for dir_idx in op_dirs:
                if (can[dir_idx] >> u) & 1:
                    v = u + DIRS[dir_idx]
                    if (prev >> v) & 1:
                        for w in BLOCKERS[u][dir_idx]:
                            if not forbid[w] and w not in seen:
                                seen.add(w)
                                if check is None or b.wall_ok(w, check):
                                    out.append(w)
                        if nxt < 0:
                            nxt = v
            if nxt < 0 or len(out) >= limit:
                break
            u = nxt
            cur_d -= 1

        # 2. Mở rộng thêm các nhánh đường ngắn nhất thay thế (DAG_EXTRA bằng bitboard)
        if len(out) < limit and d > 0:
            dag_mask = 1 << op_pos
            cd = d
            while cd > 0 and dag_mask and len(out) < limit:
                prev = layers[cd - 1]
                nxt_mask = 0
                rem = dag_mask
                while rem:
                    low = rem & -rem
                    u2 = low.bit_length() - 1
                    rem ^= low
                    for dir_idx in op_dirs:
                        if (can[dir_idx] >> u2) & 1:
                            v = u2 + DIRS[dir_idx]
                            if (prev >> v) & 1:
                                nxt_mask |= 1 << v
                                for w in BLOCKERS[u2][dir_idx]:
                                    if not forbid[w] and w not in seen:
                                        seen.add(w)
                                        if check is None or b.wall_ok(w, check):
                                            out.append(w)
                dag_mask = nxt_mask
                cd -= 1

        return out[:limit]

    def _root_walls(self, b, F, root_ok):
        """ROOT_BY_NET (ROOT_POOL = 40) + tường phòng thủ CONFLICT, lọc bỏ tường tự hại mình (net < 0)."""
        t = b.turn
        o = 1 - t
        me, op = b.pos[t], b.pos[o]
        d_me0 = F[t].dist(me)
        d_op0 = F[o].dist(op)
        me_seen = F[t].seen
        op_seen = F[o].seen

        pool = self._wall_cands(b, F[o], op, o, ROOT_POOL)
        seen_w = set(pool)
        threat_def_walls = set()

        if b.left[o] > 0 and d_me0 <= d_op0 + 3:
            my_cands = self._wall_cands(b, F[t], me, t, 12)
            threats = []
            pts = b.pts
            for w_th in my_cands:
                wp0, wp1, wp2 = WALL_PTS[w_th]
                need_chk = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2
                b.put_wall(w_th)
                dm2 = b.dist(me, GOAL[t])
                do2 = b.dist(op, GOAL[o]) if (op_seen & WALL_CELL_MASK[w_th]) or need_chk else d_op0
                b.remove_wall(w_th)
                if dm2 >= INF or do2 >= INF:
                    continue
                harm = (dm2 - d_me0) - (do2 - d_op0)
                if harm >= 2:
                    threats.append((harm, w_th))
            threats.sort(reverse=True)
            for harm_val, w_th in threats[:2]:
                for w_def in CONFLICT[w_th]:
                    if not b.forbid[w_def] and w_def not in seen_w:
                        if root_ok is not None and not root_ok(WALL0 + w_def):
                            continue
                        seen_w.add(w_def)
                        pool.append(w_def)
                        if harm_val >= 3:
                            threat_def_walls.add(w_def)

        scored = []
        pts = b.pts
        for idx, w in enumerate(pool):
            mv = WALL0 + w
            if root_ok is not None and not root_ok(mv):
                continue
            wp0, wp1, wp2 = WALL_PTS[w]
            need_chk = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2
            b.put_wall(w)
            if (op_seen & WALL_CELL_MASK[w]) or need_chk:
                d_op2 = b.dist(op, GOAL[o])
                if d_op2 >= INF:
                    b.remove_wall(w)
                    continue
            else:
                d_op2 = d_op0
            if (me_seen & WALL_CELL_MASK[w]) or need_chk:
                d_me2 = b.dist(me, GOAL[t])
            else:
                d_me2 = d_me0
            b.remove_wall(w)
            if d_me2 >= INF:
                continue
            net = (d_op2 - d_op0) - (d_me2 - d_me0)
            if net < 0 and not (net == -1 and w in threat_def_walls):
                continue
            scored.append((-net, idx, mv))

        scored.sort()
        return [mv for _n, _i, mv in scored[:ROOT_WALLS]]

    def _race_safe(self, b, F, pre_deadline):
        me = b.turn
        op = 1 - me
        d_me = F[me].dist(b.pos[me])
        d_op = F[op].dist(b.pos[op])
        if b.left[op] == 0:
            return d_me < d_op
        if d_me + 2 > d_op:
            return False

        sim = b.copy()
        sim.turn = op
        base_me, base_op = d_me, d_op
        cur_me, cur_op = d_me, d_op
        zero_streak = 0

        for j in range(1, sim.left[op] + 1):
            if time.perf_counter() > pre_deadline:
                return False
            f_me = BF(GOAL[me], sim.can)
            cands = self._wall_cands(sim, f_me, sim.pos[me], me, 16)
            best_w = -1
            best_net = 0
            best_dm, best_do = cur_me, cur_op
            best_zero_w = -1
            best_zero_touch = -1
            pts = sim.pts
            for w in cands:
                if sim.forbid[w]:
                    continue
                wp0, wp1, wp2 = WALL_PTS[w]
                touch = (pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)
                sim.put_wall(w)
                dm2 = sim.dist(sim.pos[me], GOAL[me])
                if dm2 < INF:
                    do2 = sim.dist(sim.pos[op], GOAL[op])
                    if do2 < INF:
                        net = (dm2 - cur_me) - (do2 - cur_op)
                        if net > best_net:
                            best_net = net
                            best_w = w
                            best_dm, best_do = dm2, do2
                        elif net == 0 and dm2 == cur_me and touch > best_zero_touch:
                            best_zero_touch = touch
                            best_zero_w = w
                sim.remove_wall(w)
            if best_w < 0:
                if zero_streak == 0 and j < sim.left[op] and best_zero_w >= 0:
                    zero_streak = 1
                    best_w = best_zero_w
                    best_dm, best_do = cur_me, cur_op
                else:
                    break
            else:
                zero_streak = 0
            sim.put_wall(best_w)
            cur_me, cur_op = best_dm, best_do
            harm = (cur_me - base_me) - (cur_op - base_op)
            if base_me + harm + 2 > base_op + j:
                return False
        return True

    def _eval_from_dists(self, b, d_me, d_op, f_me=None):
        t = b.turn
        o = 1 - t
        me_pos, op_pos = b.pos[t], b.pos[o]
        if d_me <= 1:
            fwd_tgt = me_pos + (-9 if t == 0 else 9)
            if op_pos != fwd_tgt or any(m // 9 == GOAL_ROW[t] for m in b.pawn_moves(t)):
                return WIN // 2 - d_me
        if b.left[o] == 0 and d_me <= d_op:
            return WIN // 2 - d_me
        if b.left[t] == 0 and d_op < d_me:
            return -(WIN // 2 - d_op)

        wv = _WPOW[b.left[t]] - _WPOW[b.left[o]]
        min_d = d_me if d_me < d_op else d_op
        if min_d <= 4:
            wv = (wv * (min_d + 2)) // 6

        score = (W_DIST * (d_op - d_me) + wv + TEMPO +
                 CENTER_W * (_CENTER_DIST[op_pos] - _CENTER_DIST[me_pos]))

        diff = op_pos - me_pos
        if d_me >= 2 and diff in (-9, 9, -1, 1):
            can = b.can
            target_layer = f_me.layers[d_me - 2] if f_me is not None else None
            for d in _OP_DIRS[t]:
                if DIRS[d] == diff and ((can[d] >> me_pos) & 1):
                    if (can[d] >> op_pos) & 1:
                        jc = op_pos + diff
                        ok = ((target_layer >> jc) & 1) if target_layer is not None else (b.dist(jc, GOAL[t]) <= d_me - 2)
                        if ok:
                            score += JUMP_BONUS
                    else:
                        for s in ((d + 1) & 3, (d + 3) & 3):
                            if (can[s] >> op_pos) & 1:
                                jc = op_pos + DIRS[s]
                                ok = ((target_layer >> jc) & 1) if target_layer is not None else (b.dist(jc, GOAL[t]) <= d_me - 2)
                                if ok:
                                    score += JUMP_BONUS
                                    break
                    break

        return score

    def _eval(self, b, F):
        t = b.turn
        o = 1 - t
        f_me = F[t]
        return self._eval_from_dists(b, f_me.dist(b.pos[t]), F[o].dist(b.pos[o]), f_me)

    def _search(self, b, depth, alpha, beta, F, ply):
        self.nodes += 1
        if (self.nodes & 63) == 0 and time.perf_counter() > self.deadline:
            raise _Timeout

        t = b.turn
        o = 1 - t
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
                if e_val > WIN - 500:
                    e_val -= ply
                elif e_val < -WIN + 500:
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

        # 1. Xét các nước đi quân trước (tái sử dụng F tốn 0 BFS + PVS)
        pawns = b.pawn_moves(t)
        pawns.sort(key=lambda m: (f_me.dist(m), _CENTER_DIST[m]))
        if tt_move is not None and tt_move < WALL0 and tt_move in pawns:
            pawns.remove(tt_move)
            pawns.insert(0, tt_move)

        for p_idx, mv in enumerate(pawns):
            if mv // 9 == GOAL_ROW[t]:
                val = WIN - (ply + 1)
            else:
                b.pos[t] = mv
                b.turn = o
                if p_idx > 0 and depth >= 2 and alpha > a0:
                    val = -self._search(b, depth - 1, -alpha - 1, -alpha, F, ply + 1)
                    if alpha < val < beta:
                        val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
                else:
                    val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
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

        # 2. Sinh và thử các nước đặt tường
        if b.left[t] > 0:
            cands = self._wall_cands(b, F[o], op, o, INNER_WALLS)
            if len(cands) > 2:
                wh = self.whist[t]
                cands = [w for _, w in sorted(enumerate(cands), key=lambda x: (-wh[x[1]], x[0]))]
            tw = (tt_move - WALL0) if (tt_move is not None and tt_move >= WALL0) else -1
            if ply < 64:
                for kw in (self.killers2[ply], self.killers1[ply]):
                    if kw is not None and not b.forbid[kw] and kw != tw:
                        if kw in cands:
                            cands.remove(kw)
                        cands.insert(0, kw)
            if tw >= 0 and not b.forbid[tw]:
                if tw in cands:
                    cands.remove(tw)
                cands.insert(0, tw)

            if depth == 1:
                d_me_cur = f_me.dist(me)
                d_op_cur = F[o].dist(op)
                me_seen = f_me.seen
                op_seen = F[o].seen
                pts = b.pts
                lead = d_me_cur < d_op_cur
                thresh = alpha if alpha > best else best
                for w in cands:
                    wp0, wp1, wp2 = WALL_PTS[w]
                    need_conn_check = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2
                    w_mask = WALL_CELL_MASK[w]
                    if not (op_seen & w_mask) and not need_conn_check:
                        if lead:
                            continue
                        d_op_new = d_op_cur
                        b.put_wall(w)
                    else:
                        b.put_wall(w)
                        d_op_new = b.dist(op, GOAL[o])
                        if d_op_new >= INF:
                            b.remove_wall(w)
                            continue
                    gain = d_op_new - d_op_cur
                    if gain <= 0 and lead:
                        b.remove_wall(w)
                        continue

                    b.left[t] -= 1
                    b.turn = o
                    ub = -self._eval_from_dists(b, d_op_new, d_me_cur)
                    if ub <= thresh:
                        b.turn = t
                        b.left[t] += 1
                        b.remove_wall(w)
                        continue

                    if (me_seen & w_mask) or need_conn_check:
                        d_me_new = b.dist(me, GOAL[t])
                        if d_me_new >= INF:
                            b.turn = t
                            b.left[t] += 1
                            b.remove_wall(w)
                            continue
                    else:
                        d_me_new = d_me_cur

                    val = ub if d_me_new == d_me_cur else -self._eval_from_dists(b, d_op_new, d_me_new)
                    b.turn = t
                    b.left[t] += 1
                    b.remove_wall(w)

                    if val > best:
                        best = val
                        best_mv = WALL0 + w
                        if val > alpha:
                            alpha = val
                            thresh = alpha
                            if alpha >= beta:
                                self._record_killer(ply, w, t, depth)
                                self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                                return best
            else:
                pts = b.pts
                p0_pos, p1_pos = b.pos[0], b.pos[1]
                wi = 0
                for w in cands:
                    wp0, wp1, wp2 = WALL_PTS[w]
                    need_conn_check = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2

                    b.put_wall(w)
                    F2 = b.fields_after_wall(F, w)
                    if need_conn_check and (F2[0].dist(p0_pos) >= INF or F2[1].dist(p1_pos) >= INF):
                        b.remove_wall(w)
                        continue

                    b.left[t] -= 1
                    b.turn = o
                    if depth >= 3 and wi >= LMR_FROM and alpha > -WIN + 1000:
                        val = -self._search(b, depth - 2, -alpha - 1, -alpha, F2, ply + 1)
                        if val > alpha:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    elif depth >= 2 and alpha > a0:
                        val = -self._search(b, depth - 1, -alpha - 1, -alpha, F2, ply + 1)
                        if alpha < val < beta:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    else:
                        val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    wi += 1
                    b.turn = t
                    b.left[t] += 1
                    b.remove_wall(w)

                    if val > best:
                        best = val
                        best_mv = WALL0 + w
                        if val > alpha:
                            alpha = val
                            if alpha >= beta:
                                self._record_killer(ply, w, t, depth)
                                self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                                return best

        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
        return best

    def _store_tt(self, key, depth, best, a0, beta, best_mv, ply):
        stored = best
        if stored > WIN - 500:
            stored += ply
        elif stored < -WIN + 500:
            stored -= ply
        flag = 2 if best <= a0 else (1 if best >= beta else 0)
        old = self.tt.get(key)
        if old is None or old[0] <= depth:
            self.tt[key] = (depth, flag, stored, best_mv if best_mv is not None else (old[3] if old else None))

    def choose(self, board, started, root_ok=None, repeat=None, force_fwd=False):
        if len(self.tt) > TT_SIZE:
            self.tt.clear()
        for p in (0, 1):
            wh = self.whist[p]
            for i in range(128):
                wh[i] >>= 2

        b = board.copy()
        t = b.turn
        o = 1 - t
        repeat = dict(repeat) if repeat else {}
        self.nodes = 0
        soft_dl = started + SOFT_TIME
        hard_dl = started + HARD_TIME
        self.deadline = soft_dl

        for mv in b.pawn_moves(t):
            if mv // 9 == GOAL_ROW[t] and (root_ok is None or root_ok(mv)):
                return mv

        F = b.fields()
        me, op = b.pos[t], b.pos[o]
        d_me = F[t].dist(me)
        d_op = F[o].dist(op)

        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        fwd = [m for m in pawns if F[t].dist(m) < d_me]

        if force_fwd and fwd:
            for m in pawns:
                if F[t].dist(m) >= d_me:
                    repeat[m] = repeat.get(m, 0) + SOFT_FWD_PEN

        pawns.sort(key=lambda m: (F[t].dist(m), repeat.get(m, 0), _CENTER_DIST[m]))

        is_safe = False
        if fwd and d_me < d_op:
            try:
                is_safe = self._race_safe(b, F, min(soft_dl, time.perf_counter() + PRE_TIME_LIMIT))
            except Exception:
                is_safe = False

        if is_safe:
            if len(fwd) == 1:
                return fwd[0]
            pawns = fwd
            walls = []
        else:
            if b.left[t] == 0 and fwd:
                pawns = fwd
            walls = self._root_walls(b, F, root_ok) if b.left[t] > 0 else []

        actions = pawns + walls
        if not actions:
            return None
        if len(actions) == 1:
            return actions[0]

        best_action = actions[0]
        prev_best = best_action
        prev_score = None
        unstable = False
        self.depth_reached = 0

        try:
            for depth in range(1, self.max_depth + 1):
                now = time.perf_counter()
                self.deadline = hard_dl if (depth > 2 and unstable) else soft_dl
                if now >= self.deadline:
                    break

                alpha, beta = -WIN * 2, WIN * 2
                cur_best, cur_score = None, -WIN * 2
                scores = {}

                for idx_mv, mv in enumerate(actions):
                    if time.perf_counter() > self.deadline:
                        raise _Timeout
                    if mv < WALL0:
                        pen = repeat.get(mv, 0)
                        b.pos[t] = mv
                        b.turn = o
                        if idx_mv > 0 and depth >= 3 and alpha > -WIN + 1000:
                            val = -self._search(b, depth - 1, -(alpha + pen) - 1, -(alpha + pen), F, 1) - pen
                            if val > alpha:
                                val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1) - pen
                        else:
                            val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1) - pen
                        b.turn = t
                        b.pos[t] = me
                    else:
                        w = mv - WALL0
                        b.put_wall(w)
                        F2 = b.fields_after_wall(F, w)
                        b.left[t] -= 1
                        b.turn = o
                        try:
                            if idx_mv > 0 and depth >= 3 and alpha > -WIN + 1000:
                                val = -self._search(b, depth - 1, -alpha - 1, -alpha, F2, 1)
                                if val > alpha:
                                    val = -self._search(b, depth - 1, -beta, -alpha, F2, 1)
                            else:
                                val = -self._search(b, depth - 1, -beta, -alpha, F2, 1)
                        finally:
                            b.turn = t
                            b.left[t] += 1
                            b.remove_wall(w)

                    scores[mv] = val
                    if idx_mv == 0 and depth > 2 and prev_score is not None and val < prev_score - 120:
                        unstable = True
                        self.deadline = hard_dl

                    if val > cur_score:
                        cur_score, cur_best = val, mv
                        best_action = cur_best
                        if val > alpha:
                            alpha = val

                if cur_best is not None:
                    unstable = (depth >= 2 and (cur_best != prev_best or
                                (prev_score is not None and cur_score < prev_score - 120)))
                    best_action = cur_best
                    prev_best = cur_best
                    prev_score = cur_score
                    self.depth_reached = depth
                    actions.sort(key=lambda a: -scores.get(a, -WIN * 2))

                if abs(cur_score) >= WIN - 500:
                    if depth >= WIN - abs(cur_score):
                        break
        except _Timeout:
            pass

        return best_action


_BOT = None
_STATE_VISITS = {}
_CELL_VISITS = {}
_WASTE = {0: [], 1: []}
_LAST_TURN = {}


def board_from_observation(obs):
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
    if mv < WALL0:
        return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = wall_info(mv - WALL0)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}


def _fallback(observation):
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


def _choose(observation, started=None):
    global _BOT
    if started is None:
        started = time.perf_counter()
    pid = observation["player_id"]
    turn = observation["turn"]

    if turn <= 2 or turn < _LAST_TURN.get(pid, 0):
        for k in [k for k in _STATE_VISITS if k[0] == pid]:
            del _STATE_VISITS[k]
        for k in [k for k in _CELL_VISITS if k[0] == pid]:
            del _CELL_VISITS[k]
        _WASTE[pid] = []
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

    nw = len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"])
    me_cell = board.pos[pid]
    opp_cell = board.pos[1 - pid]

    cur_ckey = (pid, me_cell, nw)
    _CELL_VISITS[cur_ckey] = _CELL_VISITS.get(cur_ckey, 0) + 1

    repeat = {}
    d_cur = board.dist(me_cell, GOAL[pid])
    for m in legal:
        if m < WALL0:
            s_cnt = _STATE_VISITS.get((pid, m, opp_cell, nw), 0)
            c_cnt = _CELL_VISITS.get((pid, m, nw), 0)
            c_pen = (45 if (c_cnt == 1 and board.dist(m, GOAL[pid]) >= d_cur)
                     else (max(0, c_cnt - 1) * CELL_REPEAT_PEN))
            pen = s_cnt * STATE_REPEAT_PEN + c_pen
            if pen > 0:
                repeat[m] = pen

    force_fwd = sum(_WASTE[pid][-6:]) >= 2
    mv = _BOT.choose(board, started, root_ok=lambda m: m in legal, repeat=repeat, force_fwd=force_fwd)
    if mv is None or mv not in legal:
        return _fallback(observation)

    if mv < WALL0:
        skey = (pid, mv, opp_cell, nw)
        _STATE_VISITS[skey] = _STATE_VISITS.get(skey, 0) + 1
        d_before = d_cur
        d_after = board.dist(mv, GOAL[pid])
        _WASTE[pid].append(1 if d_after >= d_before else 0)
    else:
        _WASTE[pid].append(0)

    return move_to_action(mv)


def choose_action(observation, started=None):
    try:
        return _choose(observation, started=started)
    except Exception:
        return _fallback(observation)
''',
r'''"""Động cơ P1 (phe đi sau).
- Sinh tường: _dag_walls duyệt toàn bộ DAG đường ngắn nhất của đối thủ, gán (bước, thuộc đường chính,
  mức chạm, độ rộng lớp) để xếp hạng (INNER_WALLS = 9 trong cây; gốc giữ ROOT_WALLS = 20 theo priority).
- Hàm đánh giá: chênh lệch khoảng cách, giá trị tường theo lũy thừa _WPOW (W_WALLS = 210), tempo, độ lệch
  cột giữa, thưởng nhảy qua quân (JUMP_BONUS = 65), nhận diện thắng 1 bước ở nút lá (d_me <= 1).
- Tìm kiếm: lặp sâu + negamax PVS + LMR, bảng chuyển vị Zobrist, 2 killer mỗi ply, lịch sử tường,
  cận trên để cắt tỉa ở depth == 1 (dùng lại ub nếu khoảng cách của mình không đổi), BF-clone O(1) qua
  fields_after_wall, WALL_MASKS bitboard O(1).
- Ở gốc: phòng thủ CONFLICT chống tường đe dọa, kiểm tra _race_safe, cập nhật best_action tức thời.
"""

import random
import time

CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)
_OP_DIRS = ((0, 2, 1, 3), (2, 0, 1, 3))

WALL0 = 81
RED, BLUE = 0, 1
START = (76, 4)
GOAL = (sum(1 << c for c in range(0, 9)),
        sum(1 << c for c in range(72, 81)))
GOAL_ROW = (0, 8)
WIN = 1000000

SOFT_TIME = 0.67
HARD_TIME = 0.77
PRE_TIME = 0.06

ROOT_WALLS = 20
INNER_WALLS = 9
SOFT_FWD_PEN = 80
W_DIST = 100
W_WALLS = 210
WALL_POW = 0.8
TEMPO = 20
CENTER_W = 2
JUMP_BONUS = 65
TT_SIZE = 320000

_WPOW = tuple(int(round(W_WALLS * (i ** WALL_POW))) for i in range(16))
_CENTER_DIST = tuple(abs((c % 9) - 4) for c in range(CELLS))

_rng = random.Random(20240607)
ZP = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
ZW = [_rng.getrandbits(64) for _ in range(128)]
ZLEFT = [_rng.getrandbits(64) for _ in range(128)]
ZT = _rng.getrandbits(64)


def wall_info(w):
    return ('h' if w < 64 else 'v', (w & 63) >> 3, w & 7)


def wall_id(orient, r, c):
    return (0 if orient == 'h' else 64) + r * 8 + c


WALL_EDGES, WALL_PTS, CONFLICT, WALL_CELL_MASK, WALL_MASKS = [], [], [], [], []
for _w in range(128):
    _o, _r, _c = wall_info(_w)
    if _o == 'h':
        _c1, _c2 = _r * 9 + _c, _r * 9 + _c + 1
        _c3, _c4 = (_r + 1) * 9 + _c, (_r + 1) * 9 + _c + 1
        _e = [(_c1, 2), (_c2, 2), (_c3, 0), (_c4, 0)]
        _mA = (1 << _c1) | (1 << _c2)
        _mB = (1 << _c3) | (1 << _c4)
        _wm = (2, _mA, ~_mA, 0, _mB, ~_mB)
        _p = [(_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2]
        _cf = [wall_id('h', _r, _c), wall_id('v', _r, _c)]
        if _c > 0:
            _cf.append(wall_id('h', _r, _c - 1))
        if _c < 7:
            _cf.append(wall_id('h', _r, _c + 1))
    else:
        _c1, _c2 = _r * 9 + _c, (_r + 1) * 9 + _c
        _c3, _c4 = _r * 9 + _c + 1, (_r + 1) * 9 + _c + 1
        _e = [(_c1, 1), (_c2, 1), (_c3, 3), (_c4, 3)]
        _mA = (1 << _c1) | (1 << _c2)
        _mB = (1 << _c3) | (1 << _c4)
        _wm = (1, _mA, ~_mA, 3, _mB, ~_mB)
        _p = [_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1]
        _cf = [wall_id('v', _r, _c), wall_id('h', _r, _c)]
        if _r > 0:
            _cf.append(wall_id('v', _r - 1, _c))
        if _r < 7:
            _cf.append(wall_id('v', _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_PTS.append(tuple(_p))
    CONFLICT.append(tuple(_cf))
    WALL_CELL_MASK.append(_mA | _mB)
    WALL_MASKS.append(_wm)
WALL_CELL_MASK = tuple(WALL_CELL_MASK)
WALL_MASKS = tuple(WALL_MASKS)

BLOCKERS = [[() for _ in range(4)] for _ in range(CELLS)]
for _cell in range(CELLS):
    _r, _c = divmod(_cell, 9)
    for _d in range(4):
        _lst = []
        for _w in range(128):
            if (_cell, _d) in WALL_EDGES[_w]:
                _lst.append(_w)
        _lst.sort(key=lambda w: abs((w & 7) - min(_c, 7)))
        BLOCKERS[_cell][_d] = tuple(_lst)


def _initial_can():
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


class BF:
    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3", "cache")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can
        self.cache = {}

    def clone(self, can):
        obj = BF.__new__(BF)
        obj.layers = self.layers[:]
        obj.seen = self.seen
        obj.c0, obj.c1, obj.c2, obj.c3 = can
        obj.cache = self.cache.copy()
        return obj

    def dist(self, cell):
        res = self.cache.get(cell)
        if res is not None:
            return res
        bit = 1 << cell
        seen = self.seen
        if seen & bit:
            for k, layer in enumerate(self.layers):
                if layer & bit:
                    self.cache[cell] = k
                    return k
        layers = self.layers
        k = len(layers)
        f = layers[-1]
        c0, c1, c2, c3 = self.c0, self.c1, self.c2, self.c3
        while True:
            f = (((f << 9) & c0) | ((f >> 1) & c1) |
                 ((f >> 9) & c2) | ((f << 1) & c3)) & ~seen
            if not f:
                self.cache[cell] = INF
                return INF
            seen |= f
            self.seen = seen
            layers.append(f)
            if f & bit:
                self.cache[cell] = k
                return k
            k += 1


class Board:
    def __init__(self):
        self.pos = [START[0], START[1]]
        self.left = [10, 10]
        self.can = _initial_can()
        self.forbid = [0] * 128
        self.pts = [0] * 100
        for R in range(10):
            for C in range(10):
                if R in (0, 9) or C in (0, 9):
                    self.pts[R * 10 + C] = 1
        self.turn = RED
        self.whash = 0

    def copy(self):
        b = Board.__new__(Board)
        b.pos = self.pos[:]
        b.left = self.left[:]
        b.can = self.can[:]
        b.forbid = self.forbid[:]
        b.pts = self.pts[:]
        b.turn = self.turn
        b.whash = self.whash
        return b

    def fields(self):
        c = self.can
        return (BF(GOAL[0], c), BF(GOAL[1], c))

    def fields_after_wall(self, F, w):
        c = self.can
        m = WALL_CELL_MASK[w]
        f0, f1 = F
        return (
            BF(GOAL[0], c) if (f0.seen & m) else f0.clone(c),
            BF(GOAL[1], c) if (f1.seen & m) else f1.clone(c),
        )

    def dist(self, start, goal):
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
        t = self.turn if player is None else player
        me, op = self.pos[t], self.pos[1 - t]
        can = self.can
        out = []
        for d in _OP_DIRS[t]:
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

    def put_wall(self, w):
        dA, _, clrA, dB, _, clrB = WALL_MASKS[w]
        can = self.can
        can[dA] &= clrA
        can[dB] &= clrB
        forbid = self.forbid
        for x in CONFLICT[w]:
            forbid[x] += 1
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        pts[p0] += 1
        pts[p1] += 1
        pts[p2] += 1
        self.whash ^= ZW[w]

    def remove_wall(self, w):
        dA, setA, _, dB, setB, _ = WALL_MASKS[w]
        can = self.can
        can[dA] |= setA
        can[dB] |= setB
        forbid = self.forbid
        for x in CONFLICT[w]:
            forbid[x] -= 1
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        pts[p0] -= 1
        pts[p1] -= 1
        pts[p2] -= 1
        self.whash ^= ZW[w]

    def wall_ok(self, w, player=None):
        t = self.turn if player is None else player
        if self.left[t] <= 0 or self.forbid[w]:
            return False
        p0, p1, p2 = WALL_PTS[w]
        pts = self.pts
        if (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0) >= 2:
            dA, setA, clrA, dB, setB, clrB = WALL_MASKS[w]
            can = self.can
            can[dA] &= clrA
            can[dB] &= clrB
            ok = (self.dist(self.pos[0], GOAL[0]) < INF and
                  self.dist(self.pos[1], GOAL[1]) < INF)
            can[dA] |= setA
            can[dB] |= setB
            return ok
        return True

    def zkey(self):
        return (self.whash ^ ZP[0][self.pos[0]] ^ ZP[1][self.pos[1]] ^
                ZLEFT[self.left[0] * 11 + self.left[1]] ^
                (ZT if self.turn else 0))


class _Timeout(Exception):
    pass


class Bot:
    def __init__(self, max_depth=30):
        self.max_depth = max_depth
        self.tt = {}
        self.nodes = 0
        self.depth_reached = 0
        self.deadline = 0.0
        self.killers1 = [None] * 64
        self.killers2 = [None] * 64
        self.whist = [[0] * 128, [0] * 128]

    def _record_killer(self, ply, w, t, depth):
        self.whist[t][w] += depth * depth
        if ply < 64 and self.killers1[ply] != w:
            self.killers2[ply] = self.killers1[ply]
            self.killers1[ply] = w

    def _dag_walls(self, b, f_op, op_pos, op_id):
        """Duyệt DAG đường ngắn nhất của đối thủ; trả (tường, bước, thuộc đường chính, mức chạm, độ rộng lớp)."""
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF:
            return []
        layers = f_op.layers
        can = b.can
        forbid = b.forbid
        pts = b.pts
        op_dirs = _OP_DIRS[op_id]

        prim_u = op_pos
        dag_mask = 1 << op_pos
        seen_w = set()
        raw = []

        for step in range(d):
            prev_layer = layers[d - 1 - step]
            nxt_dag = 0
            layer_width = bin(dag_mask).count("1")

            nxt_prim = -1
            if prim_u >= 0:
                for dir_idx in op_dirs:
                    if (can[dir_idx] >> prim_u) & 1:
                        v = prim_u + DIRS[dir_idx]
                        if (prev_layer >> v) & 1:
                            if nxt_prim < 0:
                                nxt_prim = v
                            for w in BLOCKERS[prim_u][dir_idx]:
                                if not forbid[w] and w not in seen_w:
                                    seen_w.add(w)
                                    p0, p1, p2 = WALL_PTS[w]
                                    touch = (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0)
                                    raw.append((w, step, 1, touch, layer_width))

            rem = dag_mask
            while rem:
                low = rem & -rem
                u = low.bit_length() - 1
                rem ^= low
                for dir_idx in op_dirs:
                    if (can[dir_idx] >> u) & 1:
                        v = u + DIRS[dir_idx]
                        if (prev_layer >> v) & 1:
                            nxt_dag |= 1 << v
                            if u != prim_u:
                                for w in BLOCKERS[u][dir_idx]:
                                    if not forbid[w] and w not in seen_w:
                                        seen_w.add(w)
                                        p0, p1, p2 = WALL_PTS[w]
                                        touch = (pts[p0] > 0) + (pts[p1] > 0) + (pts[p2] > 0)
                                        raw.append((w, step, 0, touch, layer_width))

            prim_u = nxt_prim
            dag_mask = nxt_dag
            if not dag_mask:
                break

        return raw

    def _wall_cands(self, b, f_op, op_pos, op_id, limit):
        raw = self._dag_walls(b, f_op, op_pos, op_id)
        if not raw:
            return []
        wh = self.whist[b.turn]
        if len(raw) <= limit:
            if len(raw) > 2:
                raw.sort(key=lambda item: -wh[item[0]])
            return [item[0] for item in raw]

        def _score(item):
            w, step, is_prim, touch, width = item
            s = is_prim * 6 - step * 3
            if step == 0:
                s += 12
            elif step == 1:
                s += 6
            if touch >= 2:
                s += 14
            elif touch == 1:
                s += 6
            if width == 1:
                s += 8
            elif width == 2:
                s += 3
            return (-s, -wh[w])

        raw.sort(key=_score)
        return [item[0] for item in raw[:limit]]

    def _root_walls(self, b, F, root_ok):
        t = b.turn
        o = 1 - t
        me, op = b.pos[t], b.pos[o]
        d_me = F[t].dist(me)
        d_op = F[o].dist(op)
        me_seen = F[t].seen
        op_seen = F[o].seen
        lead = d_me < d_op

        raw = self._dag_walls(b, F[o], op, o)
        seen_w = {item[0] for item in raw}
        threat_def_walls = set()

        if b.left[o] > 0 and d_me <= d_op + 3:
            threats = []
            for w_th, step, _, touch, _ in self._dag_walls(b, F[t], me, t)[:12]:
                b.put_wall(w_th)
                dm2 = b.dist(me, GOAL[t])
                do2 = b.dist(op, GOAL[o]) if (op_seen & WALL_CELL_MASK[w_th]) or touch >= 2 else d_op
                b.remove_wall(w_th)
                if dm2 < INF and do2 < INF:
                    harm = (dm2 - d_me) - (do2 - d_op)
                    if harm >= 2:
                        threats.append((harm, w_th))
            threats.sort(reverse=True)
            for harm_val, w_th in threats[:2]:
                for w_def in CONFLICT[w_th]:
                    if not b.forbid[w_def] and w_def not in seen_w:
                        seen_w.add(w_def)
                        p0, p1, p2 = WALL_PTS[w_def]
                        touch = (b.pts[p0] > 0) + (b.pts[p1] > 0) + (b.pts[p2] > 0)
                        raw.append((w_def, 3, 0, touch, 2))
                        if harm_val >= 3:
                            threat_def_walls.add(w_def)

        scored_pos = []
        scored_zero = []
        for w, step, is_prim, touch, width in raw:
            mv = WALL0 + w
            if root_ok is not None and not root_ok(mv):
                continue
            b.put_wall(w)
            if (op_seen & WALL_CELL_MASK[w]) or touch >= 2:
                do2 = b.dist(op, GOAL[o])
                if do2 >= INF:
                    b.remove_wall(w)
                    continue
            else:
                do2 = d_op
            if (me_seen & WALL_CELL_MASK[w]) or touch >= 2:
                dm2 = b.dist(me, GOAL[t])
            else:
                dm2 = d_me
            b.remove_wall(w)
            if dm2 >= INF:
                continue

            gain = do2 - d_op
            net = gain - (dm2 - d_me)
            if net < 0 and not (net == -1 and w in threat_def_walls):
                continue
            if net > 0 and gain > 0:
                priority = net * 20 + gain * 8 + touch * 4 + is_prim * 2 - step
                scored_pos.append((priority, mv))
            elif net <= 0 and not (lead and b.left[t] <= b.left[o]):
                priority = (12 if w in threat_def_walls else 0) + touch * 5 + is_prim * 3 - step
                scored_zero.append((priority, mv))

        scored_pos.sort(reverse=True)
        scored_zero.sort(reverse=True)
        out = [mv for _, mv in scored_pos[:ROOT_WALLS]]
        if len(out) < ROOT_WALLS:
            need = min(6, ROOT_WALLS - len(out))
            out.extend(mv for _, mv in scored_zero[:need])
        return out

    def _race_safe(self, b, F, pre_deadline):
        me = b.turn
        op = 1 - me
        d_me = F[me].dist(b.pos[me])
        d_op = F[op].dist(b.pos[op])
        if b.left[op] == 0:
            return d_me <= d_op
        if d_me + 2 > d_op:
            return False

        sim = b.copy()
        sim.turn = op
        base_me, base_op = d_me, d_op
        cur_me, cur_op = d_me, d_op
        zero_streak = 0

        for j in range(1, sim.left[op] + 1):
            if time.perf_counter() > pre_deadline:
                return False
            f_me = BF(GOAL[me], sim.can)
            cands = self._dag_walls(sim, f_me, sim.pos[me], me)
            best_w = -1
            best_net = 0
            best_dm, best_do = cur_me, cur_op
            best_zero_w = -1
            best_zero_score = -999
            for w, step, is_prim, touch, _ in cands[:18]:
                if sim.forbid[w]:
                    continue
                sim.put_wall(w)
                dm2 = sim.dist(sim.pos[me], GOAL[me])
                if dm2 < INF:
                    do2 = sim.dist(sim.pos[op], GOAL[op])
                    if do2 < INF:
                        net = (dm2 - cur_me) - (do2 - cur_op)
                        if net > best_net:
                            best_net = net
                            best_w = w
                            best_dm, best_do = dm2, do2
                        elif net == 0 and dm2 == cur_me:
                            z_sc = touch * 4 + is_prim * 2 - step
                            if z_sc > best_zero_score:
                                best_zero_score = z_sc
                                best_zero_w = w
                sim.remove_wall(w)
            if best_w < 0:
                if zero_streak == 0 and j < sim.left[op] and best_zero_w >= 0:
                    zero_streak = 1
                    best_w = best_zero_w
                    best_dm, best_do = cur_me, cur_op
                else:
                    break
            else:
                zero_streak = 0
            sim.put_wall(best_w)
            cur_me, cur_op = best_dm, best_do
            harm = (cur_me - base_me) - (cur_op - base_op)
            if base_me + harm + 2 > base_op + j:
                return False
        return True

    def _eval_from_dists(self, b, d_me, d_op, f_me=None):
        """Điểm đánh giá theo khoảng cách, giá trị tường, tempo, cột giữa và thưởng nhảy; nhận diện thắng/thua sớm."""
        t = b.turn
        o = 1 - t
        me_pos, op_pos = b.pos[t], b.pos[o]
        if d_me <= 1:
            fwd_tgt = me_pos + (-9 if t == 0 else 9)
            if op_pos != fwd_tgt or any(m // 9 == GOAL_ROW[t] for m in b.pawn_moves(t)):
                return WIN // 2 - d_me
        if b.left[o] == 0 and d_me <= d_op:
            return WIN // 2 - d_me
        if b.left[t] == 0 and d_op < d_me:
            return -(WIN // 2 - d_op)

        wv = _WPOW[b.left[t]] - _WPOW[b.left[o]]
        min_d = d_me if d_me < d_op else d_op
        if min_d <= 4:
            wv = (wv * (min_d + 2)) // 6

        score = (W_DIST * (d_op - d_me) + wv + TEMPO +
                 CENTER_W * (_CENTER_DIST[op_pos] - _CENTER_DIST[me_pos]))

        diff = op_pos - me_pos
        if d_me >= 2 and diff in (-9, 9, -1, 1):
            can = b.can
            target_layer = f_me.layers[d_me - 2] if f_me is not None else None
            for d in _OP_DIRS[t]:
                if DIRS[d] == diff and ((can[d] >> me_pos) & 1):
                    if (can[d] >> op_pos) & 1:
                        jc = op_pos + diff
                        ok = ((target_layer >> jc) & 1) if target_layer is not None else (b.dist(jc, GOAL[t]) <= d_me - 2)
                        if ok:
                            score += JUMP_BONUS
                    else:
                        for s in ((d + 1) & 3, (d + 3) & 3):
                            if (can[s] >> op_pos) & 1:
                                jc = op_pos + DIRS[s]
                                ok = ((target_layer >> jc) & 1) if target_layer is not None else (b.dist(jc, GOAL[t]) <= d_me - 2)
                                if ok:
                                    score += JUMP_BONUS
                                    break
                    break

        return score

    def _eval(self, b, F):
        t = b.turn
        o = 1 - t
        f_me = F[t]
        return self._eval_from_dists(b, f_me.dist(b.pos[t]), F[o].dist(b.pos[o]), f_me)

    def _search(self, b, depth, alpha, beta, F, ply):
        self.nodes += 1
        if (self.nodes & 31) == 0 and time.perf_counter() > self.deadline:
            raise _Timeout

        t = b.turn
        o = 1 - t
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
                if e_val > WIN - 500:
                    e_val -= ply
                elif e_val < -WIN + 500:
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

        # 1. Xét các nước đi quân trước (tái sử dụng F tốn 0 BFS + PVS)
        pawns = b.pawn_moves(t)
        pawns.sort(key=lambda m: (f_me.dist(m), _CENTER_DIST[m]))
        if tt_move is not None and tt_move < WALL0 and tt_move in pawns:
            pawns.remove(tt_move)
            pawns.insert(0, tt_move)

        for p_idx, mv in enumerate(pawns):
            if mv // 9 == GOAL_ROW[t]:
                val = WIN - (ply + 1)
            else:
                b.pos[t] = mv
                b.turn = o
                if p_idx > 0 and depth >= 2 and alpha > a0:
                    val = -self._search(b, depth - 1, -alpha - 1, -alpha, F, ply + 1)
                    if alpha < val < beta:
                        val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
                else:
                    val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
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

        # 2. Sinh và thử các nước đặt tường
        if b.left[t] > 0:
            cands = self._wall_cands(b, F[o], op, o, INNER_WALLS)
            tw = (tt_move - WALL0) if (tt_move is not None and tt_move >= WALL0) else -1
            if ply < 64:
                for kw in (self.killers2[ply], self.killers1[ply]):
                    if kw is not None and not b.forbid[kw] and kw != tw:
                        if kw in cands:
                            cands.remove(kw)
                        cands.insert(0, kw)
            if tw >= 0 and not b.forbid[tw]:
                if tw in cands:
                    cands.remove(tw)
                cands.insert(0, tw)

            if depth == 1:
                d_me_cur = f_me.dist(me)
                d_op_cur = F[o].dist(op)
                me_seen = f_me.seen
                op_seen = F[o].seen
                pts = b.pts
                lead = d_me_cur < d_op_cur
                thresh = alpha if alpha > best else best
                for w in cands:
                    wp0, wp1, wp2 = WALL_PTS[w]
                    need_conn_check = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2
                    w_mask = WALL_CELL_MASK[w]
                    if not (op_seen & w_mask) and not need_conn_check:
                        if lead:
                            continue
                        d_op_new = d_op_cur
                        b.put_wall(w)
                    else:
                        b.put_wall(w)
                        d_op_new = b.dist(op, GOAL[o])
                        if d_op_new >= INF:
                            b.remove_wall(w)
                            continue
                    gain = d_op_new - d_op_cur
                    if gain <= 0 and lead:
                        b.remove_wall(w)
                        continue

                    b.left[t] -= 1
                    b.turn = o
                    ub = -self._eval_from_dists(b, d_op_new, d_me_cur)
                    if ub <= thresh:
                        b.turn = t
                        b.left[t] += 1
                        b.remove_wall(w)
                        continue

                    if (me_seen & w_mask) or need_conn_check:
                        d_me_new = b.dist(me, GOAL[t])
                        if d_me_new >= INF:
                            b.turn = t
                            b.left[t] += 1
                            b.remove_wall(w)
                            continue
                    else:
                        d_me_new = d_me_cur

                    val = ub if d_me_new == d_me_cur else -self._eval_from_dists(b, d_op_new, d_me_new)
                    b.turn = t
                    b.left[t] += 1
                    b.remove_wall(w)

                    if val > best:
                        best = val
                        best_mv = WALL0 + w
                        if val > alpha:
                            alpha = val
                            thresh = alpha
                            if alpha >= beta:
                                self._record_killer(ply, w, t, depth)
                                self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                                return best
            else:
                pts = b.pts
                p0_pos, p1_pos = b.pos[0], b.pos[1]
                for w_idx, w in enumerate(cands):
                    wp0, wp1, wp2 = WALL_PTS[w]
                    need_conn_check = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2

                    b.put_wall(w)
                    F2 = b.fields_after_wall(F, w)
                    if need_conn_check and (F2[0].dist(p0_pos) >= INF or F2[1].dist(p1_pos) >= INF):
                        b.remove_wall(w)
                        continue

                    b.left[t] -= 1
                    b.turn = o
                    if w_idx >= 3 and depth >= 3 and alpha > -WIN + 1000:
                        val = -self._search(b, depth - 2, -alpha - 1, -alpha, F2, ply + 1)
                        if val > alpha:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    elif depth >= 2 and alpha > a0:
                        val = -self._search(b, depth - 1, -alpha - 1, -alpha, F2, ply + 1)
                        if alpha < val < beta:
                            val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    else:
                        val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    b.turn = t
                    b.left[t] += 1
                    b.remove_wall(w)

                    if val > best:
                        best = val
                        best_mv = WALL0 + w
                        if val > alpha:
                            alpha = val
                            if alpha >= beta:
                                self._record_killer(ply, w, t, depth)
                                self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                                return best

        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
        return best

    def _store_tt(self, key, depth, best, a0, beta, best_mv, ply):
        stored = best
        if stored > WIN - 500:
            stored += ply
        elif stored < -WIN + 500:
            stored -= ply
        flag = 2 if best <= a0 else (1 if best >= beta else 0)
        old = self.tt.get(key)
        if old is None or old[0] <= depth:
            self.tt[key] = (depth, flag, stored, best_mv if best_mv is not None else (old[3] if old else None))

    def choose(self, board, started, root_ok=None, repeat=None, force_fwd=False):
        if len(self.tt) > TT_SIZE:
            self.tt.clear()
        for p in (0, 1):
            wh = self.whist[p]
            for i in range(128):
                wh[i] >>= 2

        b = board.copy()
        t = b.turn
        o = 1 - t
        repeat = dict(repeat) if repeat else {}
        self.nodes = 0
        soft_dl = started + SOFT_TIME
        hard_dl = started + HARD_TIME
        self.deadline = soft_dl

        for mv in b.pawn_moves(t):
            if mv // 9 == GOAL_ROW[t] and (root_ok is None or root_ok(mv)):
                return mv

        F = b.fields()
        me, op = b.pos[t], b.pos[o]
        d_me = F[t].dist(me)
        d_op = F[o].dist(op)

        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        fwd = [m for m in pawns if F[t].dist(m) < d_me]

        if force_fwd and fwd:
            for m in pawns:
                if F[t].dist(m) >= d_me:
                    repeat[m] = repeat.get(m, 0) + SOFT_FWD_PEN

        pawns.sort(key=lambda m: (F[t].dist(m), repeat.get(m, 0), _CENTER_DIST[m]))

        is_safe = False
        if fwd and d_me < d_op:
            try:
                is_safe = self._race_safe(b, F, min(soft_dl, time.perf_counter() + PRE_TIME))
            except Exception:
                is_safe = False

        if is_safe:
            if len(fwd) == 1:
                return fwd[0]
            pawns = fwd
            walls = []
        else:
            if b.left[t] == 0 and fwd:
                pawns = fwd
            walls = self._root_walls(b, F, root_ok) if b.left[t] > 0 else []

        actions = pawns + walls
        if not actions:
            return None
        if len(actions) == 1:
            return actions[0]

        best_action = actions[0]
        prev_best = best_action
        prev_score = None
        unstable = False
        self.depth_reached = 0

        try:
            for depth in range(1, self.max_depth + 1):
                now = time.perf_counter()
                self.deadline = hard_dl if (depth > 2 and unstable) else soft_dl
                if now >= self.deadline:
                    break

                alpha, beta = -WIN * 2, WIN * 2
                cur_best, cur_score = None, -WIN * 2
                scores = {}

                for idx_mv, mv in enumerate(actions):
                    if time.perf_counter() > self.deadline:
                        raise _Timeout
                    if mv < WALL0:
                        pen = repeat.get(mv, 0)
                        b.pos[t] = mv
                        b.turn = o
                        if idx_mv > 0 and depth >= 3 and alpha > -WIN + 1000:
                            val = -self._search(b, depth - 1, -(alpha + pen) - 1, -(alpha + pen), F, 1) - pen
                            if val > alpha:
                                val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1) - pen
                        else:
                            val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1) - pen
                        b.turn = t
                        b.pos[t] = me
                    else:
                        w = mv - WALL0
                        b.put_wall(w)
                        F2 = b.fields_after_wall(F, w)
                        b.left[t] -= 1
                        b.turn = o
                        try:
                            if idx_mv > 0 and depth >= 3 and alpha > -WIN + 1000:
                                val = -self._search(b, depth - 1, -alpha - 1, -alpha, F2, 1)
                                if val > alpha:
                                    val = -self._search(b, depth - 1, -beta, -alpha, F2, 1)
                            else:
                                val = -self._search(b, depth - 1, -beta, -alpha, F2, 1)
                        finally:
                            b.turn = t
                            b.left[t] += 1
                            b.remove_wall(w)

                    scores[mv] = val
                    if idx_mv == 0 and depth > 2 and prev_score is not None and val < prev_score - 120:
                        unstable = True
                        self.deadline = hard_dl

                    if val > cur_score:
                        cur_score, cur_best = val, mv
                        best_action = cur_best
                        if val > alpha:
                            alpha = val

                if cur_best is not None:
                    unstable = (depth >= 2 and (cur_best != prev_best or
                                (prev_score is not None and cur_score < prev_score - 120)))
                    best_action = cur_best
                    prev_best = cur_best
                    prev_score = cur_score
                    self.depth_reached = depth
                    actions.sort(key=lambda a: -scores.get(a, -WIN * 2))

                if abs(cur_score) >= WIN - 500:
                    if depth >= WIN - abs(cur_score):
                        break
        except _Timeout:
            pass

        return best_action


_BOT = None
_CELL_VISITS = {}
_STATE_VISITS = {}
_WASTE = {0: [], 1: []}
_REP_PEN = (0, 0, 60, 160, 320)
_CELL_PEN = (0, 45, 110, 220, 350)
_LAST_TURN = {}


def board_from_observation(obs):
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
    if mv < WALL0:
        return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = wall_info(mv - WALL0)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}


def _fallback(observation):
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


def _choose(observation, started=None):
    global _BOT
    if started is None:
        started = time.perf_counter()
    pid = observation["player_id"]
    turn = observation["turn"]

    if turn <= 2 or turn < _LAST_TURN.get(pid, 0):
        for k in [k for k in _CELL_VISITS if k[0] == pid]:
            del _CELL_VISITS[k]
        for k in [k for k in _STATE_VISITS if k[0] == pid]:
            del _STATE_VISITS[k]
        _WASTE[pid] = []
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

    nw = len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"])
    me_cell = board.pos[pid]
    opp_cell = board.pos[1 - pid]

    cur_ckey = (pid, me_cell, nw)
    _CELL_VISITS[cur_ckey] = _CELL_VISITS.get(cur_ckey, 0) + 1
    cur_skey = (pid, me_cell, opp_cell, nw)
    _STATE_VISITS[cur_skey] = _STATE_VISITS.get(cur_skey, 0) + 1

    repeat = {}
    d_cur = board.dist(me_cell, GOAL[pid])
    for m in legal:
        if m < WALL0:
            s_cnt = _STATE_VISITS.get((pid, m, opp_cell, nw), 0)
            c_cnt = _CELL_VISITS.get((pid, m, nw), 0)
            if s_cnt == 0 and c_cnt == 0:
                continue
            d_m = board.dist(m, GOAL[pid])
            if s_cnt < 3 and c_cnt < 2 and d_m < d_cur:
                continue
            s_pen = _REP_PEN[min(s_cnt, 4)]
            c_pen = _CELL_PEN[min(c_cnt, 4)] if d_m >= d_cur else _CELL_PEN[min(max(0, c_cnt - 1), 4)]
            pen = max(s_pen, c_pen)
            if pen > 0:
                repeat[m] = pen

    force_fwd = sum(_WASTE[pid][-6:]) >= 2
    mv = _BOT.choose(board, started, root_ok=lambda m: m in legal, repeat=repeat, force_fwd=force_fwd)
    if mv is None or mv not in legal:
        return _fallback(observation)

    if mv < WALL0:
        d_before = d_cur
        d_after = board.dist(mv, GOAL[pid])
        _WASTE[pid].append(1 if d_after >= d_before else 0)
    else:
        _WASTE[pid].append(0)

    return move_to_action(mv)


def choose_action(observation, started=None):
    try:
        return _choose(observation, started=started)
    except Exception:
        return _fallback(observation)
''')
_NS = {}


def _get(k):
    ns = _NS.get(k)
    if ns is None:
        ns = {"__name__": "_merged_bot%d" % k}
        exec(compile(_SRC[k], "<bot%d>" % k, "exec"), ns)
        _NS[k] = ns
    return ns


def _fallback(obs):
    try:
        moves = obs["legal_moves"]
        d = obs["distances"]
        m = min(moves, key=lambda x: (d[x[0]][x[1]] if d[x[0]][x[1]] >= 0 else 10 ** 6) * 10 + abs(x[1] - 4))
        return {"type": "move", "to": [int(m[0]), int(m[1])]}
    except Exception:
        return {"type": "pass"}


def choose_action(observation):
    started = time.perf_counter()
    try:
        # 1. Tàn cuộc khi đối thủ hết tường: dùng bảng giải ngược chính xác
        eg_act = _try_endgame_tablebase(observation, started)
        if eg_act is not None:
            return eg_act
        # 2. Còn lại: gọi động cơ tìm kiếm theo phe (P0 hoặc P1)
        k = P0_BOT if observation["player_id"] == 0 else P1_BOT
        return _get(k)["choose_action"](observation, started=started)
    except Exception:
        return _fallback(observation)