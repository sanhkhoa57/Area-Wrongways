"""Wrongway Arena - bài nộp của MSSV xxxxxxxx (Bản nâng cấp _11 Pro v2 Optimized).

File độc lập, chỉ dùng thư viện chuẩn (random, time); cần Python 3.10 trở lên.

Các chức năng cốt lõi & thuật toán trọng tâm:
1. Đánh giá bậc thoát `_onward_deg` bằng bitboard an toàn vào `pawns.sort` và `_eval_from_dists`.
2. Hệ thống tường phòng thủ `CONFLICT` ở `_root_walls` để ngăn đối thủ chặn đường.
3. Thưởng `JUMP_BONUS` khi có thể nhảy tiến/ngang qua đầu đối thủ.
4. Quản lý thời gian SOFT/HARD linh hoạt và cắt tỉa nhánh với Fail-Soft TT.
5. Tối ưu tốc độ duyệt cây với Lazy BFS và bitmask `WALL_CELLS` loại bỏ cấp phát dư thừa.
"""

import random
import time

# Các biến cấu hình và trọng số tĩnh của Bot
CELLS = 81
INF = 99
DIRS = (-9, 1, 9, -1)
_OP_DIRS = ((0, 2, 1, 3), (2, 0, 1, 3))
_FWD_SIDE_DIRS = ((0, 1, 3), (2, 1, 3))
WALL0 = 81
RED, BLUE = 0, 1
START = (76, 4)
GOAL = (sum(1 << c for c in range(0, 9)), sum(1 << c for c in range(72, 81)))
GOAL_ROW = (0, 8)
WIN = 1000000

SOFT_TIME = 0.58
HARD_TIME = 0.78
PRE_TIME = 0.06

ROOT_WALLS = 20
INNER_WALLS = 9
W_DIST = 100
W_WALLS = 210
WALL_POW = 0.8
TEMPO = 20
CENTER_W = 2
DEG_W = 3
JUMP_BONUS = 55
TT_SIZE = 250000

_WPOW = tuple(int(round(W_WALLS * (i ** WALL_POW))) for i in range(16))
_CENTER_DIST = tuple(abs((c % 9) - 4) for c in range(CELLS))

_rng = random.Random(20240607)
ZP = [[_rng.getrandbits(64) for _ in range(CELLS)] for _ in range(2)]
ZW = [_rng.getrandbits(64) for _ in range(128)]
ZLEFT = [_rng.getrandbits(64) for _ in range(128)]
ZT = _rng.getrandbits(64)

def wall_info(w):
    """Chuyển đổi mã tường 0..127 sang (hướng 'h'/'v', row, col)."""
    return ('h' if w < 64 else 'v', (w & 63) >> 3, w & 7)

def wall_id(orient, r, c):
    """Chuyển đổi tọa độ và hướng sang mã tường 0..127."""
    return (0 if orient == 'h' else 64) + r * 8 + c

WALL_EDGES, WALL_PTS, CONFLICT, WALL_CELLS = [], [], [], []
for _w in range(128):
    _o, _r, _c = wall_info(_w)
    if _o == 'h':
        _e = [(_r * 9 + _c, 2), (_r * 9 + _c + 1, 2), ((_r + 1) * 9 + _c, 0), ((_r + 1) * 9 + _c + 1, 0)]
        _p = [(_r + 1) * 10 + _c, (_r + 1) * 10 + _c + 1, (_r + 1) * 10 + _c + 2]
        _cf = [wall_id('h', _r, _c), wall_id('v', _r, _c)]
        if _c > 0: _cf.append(wall_id('h', _r, _c - 1))
        if _c < 7: _cf.append(wall_id('h', _r, _c + 1))
    else:
        _e = [(_r * 9 + _c, 1), ((_r + 1) * 9 + _c, 1), (_r * 9 + _c + 1, 3), ((_r + 1) * 9 + _c + 1, 3)]
        _p = [_r * 10 + _c + 1, (_r + 1) * 10 + _c + 1, (_r + 2) * 10 + _c + 1]
        _cf = [wall_id('v', _r, _c), wall_id('h', _r, _c)]
        if _r > 0: _cf.append(wall_id('v', _r - 1, _c))
        if _r < 7: _cf.append(wall_id('v', _r + 1, _c))
    WALL_EDGES.append(tuple(_e))
    WALL_PTS.append(tuple(_p))
    CONFLICT.append(tuple(_cf))
    _cmask = 0
    for _cell_idx, _ in _e: _cmask |= 1 << _cell_idx
    WALL_CELLS.append(_cmask)
WALL_CELLS = tuple(WALL_CELLS)

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
        if r > 0: can[0] |= 1 << cell
        if c < 8: can[1] |= 1 << cell
        if r < 8: can[2] |= 1 << cell
        if c > 0: can[3] |= 1 << cell
    return can

class BF:
    """BFS ngược từ hàng đích bằng bitboard để tính khoảng cách và bậc thoát (hỗ trợ đánh giá lười)."""
    __slots__ = ("layers", "seen", "c0", "c1", "c2", "c3")

    def __init__(self, goal_mask, can):
        self.layers = [goal_mask]
        self.seen = goal_mask
        self.c0, self.c1, self.c2, self.c3 = can

    def dist(self, cell):
        """Tính số bước ngắn nhất từ cell tới đích."""
        bit = 1 << cell
        k = 0
        for layer in self.layers:
            if layer & bit: return k
            k += 1
        layers, seen = self.layers, self.seen
        f = layers[-1]
        c0, c1, c2, c3 = self.c0, self.c1, self.c2, self.c3
        while True:
            f = (((f << 9) & c0) | ((f >> 1) & c1) | ((f >> 9) & c2) | ((f << 1) & c3)) & ~seen
            if not f: return INF
            seen |= f
            self.seen = seen
            layers.append(f)
            if f & bit: return k
            k += 1

    def onward_deg(self, cell):
        """Đếm số hướng bước tiếp để thoát ra khỏi vùng kẹt tường."""
        dm = self.dist(cell)
        if dm <= 0 or dm >= INF: return 0
        prev = self.layers[dm - 1]
        return ((((self.c0 & (prev << 9)) >> cell) & 1) +
                (((self.c1 & (prev >> 1)) >> cell) & 1) +
                (((self.c2 & (prev >> 9)) >> cell) & 1) +
                (((self.c3 & (prev << 1)) >> cell) & 1))

class Board:
    """Quản lý trạng thái bàn cờ, hỗ trợ make/unmake nước đi nhanh và Zobrist Hashing."""
    def __init__(self):
        self.pos = [START[0], START[1]]
        self.left = [10, 10]
        self.can = _initial_can()
        self.forbid = [0] * 128
        self.pts = [0] * 100
        for R in range(10):
            for C in range(10):
                if R in (0, 9) or C in (0, 9): self.pts[R * 10 + C] = 1
        self.turn = RED
        self.whash = 0

    def copy(self):
        b = Board.__new__(Board)
        b.pos, b.left, b.can, b.forbid, b.pts = self.pos[:], self.left[:], self.can[:], self.forbid[:], self.pts[:]
        b.turn, b.whash = self.turn, self.whash
        return b

    def fields(self):
        c = self.can
        return (BF(GOAL[0], c), BF(GOAL[1], c))

    def dist(self, start, goal):
        cur = 1 << start
        if cur & goal: return 0
        seen = cur
        c0, c1, c2, c3 = self.can
        d = 0
        while cur:
            d += 1
            nxt = (((cur & c0) >> 9) | ((cur & c1) << 1) | ((cur & c2) << 9) | ((cur & c3) >> 1)) & ~seen
            if nxt & goal: return d
            seen |= nxt
            cur = nxt
        return INF

    def pawn_moves(self, player=None):
        """Sinh các nước đi quân hợp lệ bao gồm cả cơ chế nhảy chéo/thẳng."""
        t = self.turn if player is None else player
        me, op = self.pos[t], self.pos[1 - t]
        can, out = self.can, []
        for d in _OP_DIRS[t]:
            if not (can[d] >> me) & 1: continue
            tgt = me + DIRS[d]
            if tgt != op:
                out.append(tgt)
            elif (can[d] >> tgt) & 1:
                out.append(tgt + DIRS[d])
            else:
                for s in ((d + 1) & 3, (d + 3) & 3):
                    if (can[s] >> tgt) & 1: out.append(tgt + DIRS[s])
        return out

    def _edges(self, w, on):
        can = self.can
        if on:
            for cell, d in WALL_EDGES[w]: can[d] |= 1 << cell
        else:
            for cell, d in WALL_EDGES[w]: can[d] &= ~(1 << cell)

    def put_wall(self, w):
        self._edges(w, False)
        for x in CONFLICT[w]: self.forbid[x] += 1
        for p in WALL_PTS[w]: self.pts[p] += 1
        self.whash ^= ZW[w]

    def remove_wall(self, w):
        self._edges(w, True)
        for x in CONFLICT[w]: self.forbid[x] -= 1
        for p in WALL_PTS[w]: self.pts[p] -= 1
        self.whash ^= ZW[w]

    def zkey(self):
        return (self.whash ^ ZP[0][self.pos[0]] ^ ZP[1][self.pos[1]] ^
                ZLEFT[self.left[0] * 11 + self.left[1]] ^ (ZT if self.turn else 0))

class _Timeout(Exception):
    pass

class Bot:
    """Bộ máy tìm kiếm Negamax tối ưu kết hợp Alpha-Beta, Lazy BFS, DAG Walls và TT."""
    def __init__(self, max_depth=30):
        self.max_depth = max_depth
        self.tt = {}
        self.nodes, self.depth_reached, self.deadline = 0, 0, 0.0
        self.killers = [None] * 64

    def _dag_walls(self, b, f_op, op_pos, op_id):
        """Trích xuất tường hợp lệ cắt đồ thị đường đi ngắn nhất (DAG)."""
        d = f_op.dist(op_pos)
        if d <= 0 or d >= INF: return []
        layers, can, forbid, pts, op_dirs = f_op.layers, b.can, b.forbid, b.pts, _OP_DIRS[op_id]
        prim_u, dag_mask, seen_w, raw = op_pos, 1 << op_pos, set(), []

        for step in range(d):
            prev_layer, nxt_dag, layer_width = layers[d - 1 - step], 0, dag_mask.bit_count()
            nxt_prim = -1
            if prim_u >= 0:
                for dir_idx in op_dirs:
                    if (can[dir_idx] >> prim_u) & 1:
                        v = prim_u + DIRS[dir_idx]
                        if (prev_layer >> v) & 1:
                            if nxt_prim < 0: nxt_prim = v
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
            prim_u, dag_mask = nxt_prim, nxt_dag
            if not dag_mask: break
        return raw

    def _wall_cands(self, b, f_op, op_pos, op_id, limit):
        """Sinh ứng viên tường được đánh giá O(1) để giảm tải duyệt không cần thiết."""
        raw = self._dag_walls(b, f_op, op_pos, op_id)
        if not raw or len(raw) <= limit: return [item[0] for item in raw]
        scored = []
        for w, step, is_prim, touch, width in raw:
            s = is_prim * 6 - step * 3
            if step == 0: s += 12
            elif step == 1: s += 6
            if touch >= 2: s += 14
            elif touch == 1: s += 6
            if width == 1: s += 8
            elif width == 2: s += 3
            scored.append((-s, w))
        scored.sort()
        return [w for _, w in scored[:limit]]

    def _root_walls(self, b, F, root_ok):
        """Đánh giá chi tiết lợi ích tĩnh tại gốc cho tường tấn công và tường phòng thủ."""
        t, o = b.turn, 1 - b.turn
        me, op = b.pos[t], b.pos[o]
        d_me, d_op = F[t].dist(me), F[o].dist(op)
        lead = d_me < d_op
        raw = self._dag_walls(b, F[o], op, o)
        seen_w, def_bonus = {item[0] for item in raw}, {}

        if b.left[o] > 0 and d_me <= d_op + 4:
            threats = []
            for w_th, step, _, touch, _ in self._dag_walls(b, F[t], me, t)[:12]:
                b.put_wall(w_th)
                dm2, do2 = b.dist(me, GOAL[t]), b.dist(op, GOAL[o])
                b.remove_wall(w_th)
                if dm2 < INF and do2 < INF and (dm2 - d_me) - (do2 - d_op) >= 2:
                    threats.append(((dm2 - d_me) - (do2 - d_op), w_th))
            threats.sort(reverse=True)
            for harm, w_th in threats[:2]:
                for w_def in CONFLICT[w_th]:
                    if not b.forbid[w_def]:
                        if w_def not in def_bonus or harm > def_bonus[w_def]:
                            def_bonus[w_def] = harm
                        if w_def not in seen_w:
                            seen_w.add(w_def)
                            p0, p1, p2 = WALL_PTS[w_def]
                            raw.append((w_def, 2, 0, (b.pts[p0] > 0) + (b.pts[p1] > 0) + (b.pts[p2] > 0), 2))

        scored_pos, scored_zero = [], []
        for w, step, is_prim, touch, width in raw:
            mv = WALL0 + w
            if root_ok and not root_ok(mv): continue
            b.put_wall(w)
            do2 = b.dist(op, GOAL[o])
            if do2 >= INF:
                b.remove_wall(w)
                continue
            dm2 = b.dist(me, GOAL[t])
            b.remove_wall(w)
            if dm2 >= INF: continue

            gain, net, d_harm = do2 - d_op, (do2 - d_op) - (dm2 - d_me), def_bonus.get(w, 0)
            if net < 0 and (d_harm < 3 or net < -1): continue
            if (net > 0 and gain > 0) or d_harm >= 2:
                scored_pos.append((net * 20 + gain * 8 + d_harm * 14 + touch * 4 + is_prim * 2 - step, mv))
            elif net == 0 and not (lead and b.left[t] <= b.left[o]):
                scored_zero.append((touch * 5 + is_prim * 3 - step, mv))

        scored_pos.sort(reverse=True)
        scored_zero.sort(reverse=True)
        out = [mv for _, mv in scored_pos[:ROOT_WALLS]]
        if len(out) < ROOT_WALLS:
            out.extend(mv for _, mv in scored_zero[:min(6, ROOT_WALLS - len(out))])
        return out

    def _race_safe(self, b, F, pre_deadline):
        """Xác nhận có thể đảm bảo thắng cuộc đua về đích thuần túy kể cả khi bị chặn hay không."""
        me, op = b.turn, 1 - b.turn
        d_me, d_op = F[me].dist(b.pos[me]), F[op].dist(b.pos[op])
        if b.left[op] == 0: return d_me <= d_op
        if d_me + 1 >= d_op: return False

        sim, base_me, base_op, cur_me, cur_op = b.copy(), d_me, d_op, d_me, d_op
        sim.turn = op

        for j in range(1, sim.left[op] + 1):
            if time.perf_counter() > pre_deadline: return False
            best_w, best_net, best_touch, best_dm, best_do = -1, -1, -1, cur_me, cur_op
            for w, step, _, touch, _ in self._dag_walls(sim, BF(GOAL[me], sim.can), sim.pos[me], me)[:18]:
                if sim.forbid[w]: continue
                sim.put_wall(w)
                dm2 = sim.dist(sim.pos[me], GOAL[me])
                if dm2 < INF:
                    do2 = sim.dist(sim.pos[op], GOAL[op])
                    if do2 < INF:
                        net = (dm2 - cur_me) - (do2 - cur_op)
                        if net > best_net or (net == best_net and touch > best_touch):
                            best_net, best_touch, best_w, best_dm, best_do = net, touch, w, dm2, do2
                sim.remove_wall(w)
            if best_w < 0: break
            sim.put_wall(best_w)
            cur_me, cur_op = best_dm, best_do
            if base_me + ((cur_me - base_me) - (cur_op - base_op)) + 2 > base_op + j: return False
        return True

    def _eval_from_dists(self, b, d_me, d_op, F=None):
        t, o = b.turn, 1 - b.turn
        if d_me <= 1 or (b.left[o] == 0 and d_me <= d_op): return WIN // 2 - d_me
        if b.left[t] == 0 and d_op < d_me: return -(WIN // 2 - d_op)

        me_pos, op_pos = b.pos[t], b.pos[o]
        wv = _WPOW[b.left[t]] - _WPOW[b.left[o]]
        min_d = min(d_me, d_op)
        if min_d <= 4: wv = (wv * (min_d + 2)) // 6

        score = (W_DIST * (d_op - d_me) + wv + TEMPO + CENTER_W * (_CENTER_DIST[op_pos] - _CENTER_DIST[me_pos]))
        if F: score += DEG_W * (F[t].onward_deg(me_pos) - F[o].onward_deg(op_pos))

        diff = op_pos - me_pos
        if diff in (-9, 9, -1, 1):
            can = b.can
            for d in _FWD_SIDE_DIRS[t]:
                if DIRS[d] == diff and ((can[d] >> me_pos) & 1):
                    if (((can[d] >> op_pos) & 1) or ((can[(d + 1) & 3] >> op_pos) & 1) or ((can[(d + 3) & 3] >> op_pos) & 1)):
                        score += JUMP_BONUS
                    break
        return score

    def _eval(self, b, F):
        return self._eval_from_dists(b, F[b.turn].dist(b.pos[b.turn]), F[1 - b.turn].dist(b.pos[1 - b.turn]), F)

    def _search(self, b, depth, alpha, beta, F, ply):
        self.nodes += 1
        if (self.nodes & 31) == 0 and time.perf_counter() > self.deadline: raise _Timeout
        t, o = b.turn, 1 - b.turn
        if b.pos[o] // 9 == GOAL_ROW[o]: return -(WIN - ply)
        if depth <= 0: return self._eval(b, F)

        key = b.zkey()
        ent = self.tt.get(key)
        tt_move = None
        if ent:
            e_depth, e_flag, e_val, tt_move = ent
            if e_depth >= depth:
                e_val += ply if e_val < -WIN + 500 else (-ply if e_val > WIN - 500 else 0)
                if e_flag == 0: return e_val
                if e_flag == 1 and e_val > alpha: alpha = e_val
                elif e_flag == 2 and e_val < beta: beta = e_val
                if alpha >= beta: return e_val

        a0, best, best_mv = alpha, -WIN * 2, None
        me, op, f_me = b.pos[t], b.pos[o], F[t]

        pawns = b.pawn_moves(t)
        pawns.sort(key=lambda m: (f_me.dist(m), -f_me.onward_deg(m), _CENTER_DIST[m]))
        if tt_move and tt_move < WALL0 and tt_move in pawns: pawns.insert(0, pawns.pop(pawns.index(tt_move)))

        for mv in pawns:
            if mv // 9 == GOAL_ROW[t]:
                val = WIN - (ply + 1)
            else:
                b.pos[t], b.turn = mv, o
                val = -self._search(b, depth - 1, -beta, -alpha, F, ply + 1)
                b.pos[t], b.turn = me, t
            if val > best:
                best, best_mv = val, mv
                if val > alpha:
                    alpha = val
                    if alpha >= beta:
                        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                        return best

        if b.left[t] > 0:
            cands = self._wall_cands(b, F[o], op, o, INNER_WALLS)
            kw = self.killers[ply] if ply < 64 else None
            if kw is not None and not b.forbid[kw] and kw != (tt_move - WALL0 if tt_move and tt_move >= WALL0 else -1):
                if kw in cands: cands.remove(kw)
                cands.insert(0, kw)
            if tt_move and tt_move >= WALL0 and not b.forbid[tt_move - WALL0]:
                if tt_move - WALL0 in cands: cands.remove(tt_move - WALL0)
                cands.insert(0, tt_move - WALL0)

            if depth == 1:
                d_me_cur, d_op_cur, me_seen, pts, lead = f_me.dist(me), F[o].dist(op), f_me.seen, b.pts, f_me.dist(me) < F[o].dist(op)
                thresh = alpha if alpha > best else best
                for w in cands:
                    wp0, wp1, wp2 = WALL_PTS[w]
                    need_conn = ((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2
                    b.put_wall(w)
                    d_op_new = b.dist(op, GOAL[o])
                    if d_op_new >= INF or (d_op_new - d_op_cur <= 0 and lead):
                        b.remove_wall(w)
                        continue

                    b.left[t] -= 1
                    b.turn = o
                    ub = -self._eval_from_dists(b, d_op_new, d_me_cur)
                    if ub <= thresh:
                        if ub > best and not need_conn: best = ub
                        b.turn, b.left[t] = t, b.left[t] + 1
                        b.remove_wall(w)
                        continue

                    if not (WALL_CELLS[w] & me_seen) and not need_conn:
                        val = ub
                    else:
                        d_me_new = b.dist(me, GOAL[t])
                        if d_me_new >= INF:
                            b.turn, b.left[t] = t, b.left[t] + 1
                            b.remove_wall(w)
                            continue
                        val = ub if d_me_new == d_me_cur else -self._eval_from_dists(b, d_op_new, d_me_new)

                    b.turn, b.left[t] = t, b.left[t] + 1
                    b.remove_wall(w)
                    if val > best:
                        best, best_mv = val, WALL0 + w
                        if val > alpha:
                            alpha = thresh = val
                            if alpha >= beta:
                                if ply < 64: self.killers[ply] = w
                                self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                                return best
            else:
                pts = b.pts
                for w_idx, w in enumerate(cands):
                    wp0, wp1, wp2 = WALL_PTS[w]
                    b.put_wall(w)
                    F2 = b.fields()
                    if (((pts[wp0] > 0) + (pts[wp1] > 0) + (pts[wp2] > 0)) >= 2) and (F2[0].dist(b.pos[0]) >= INF or F2[1].dist(b.pos[1]) >= INF):
                        b.remove_wall(w)
                        continue

                    b.left[t] -= 1
                    b.turn = o
                    if w_idx >= 3 and depth >= 3 and alpha > -WIN + 1000:
                        val = -self._search(b, depth - 2, -alpha - 1, -alpha, F2, ply + 1)
                        if val > alpha: val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    else:
                        val = -self._search(b, depth - 1, -beta, -alpha, F2, ply + 1)
                    
                    b.turn, b.left[t] = t, b.left[t] + 1
                    b.remove_wall(w)
                    if val > best:
                        best, best_mv = val, WALL0 + w
                        if val > alpha:
                            alpha = val
                            if alpha >= beta:
                                if ply < 64: self.killers[ply] = w
                                self._store_tt(key, depth, best, a0, beta, best_mv, ply)
                                return best

        self._store_tt(key, depth, best, a0, beta, best_mv, ply)
        return best

    def _store_tt(self, key, depth, best, a0, beta, best_mv, ply):
        stored = best + ply if best > WIN - 500 else (best - ply if best < -WIN + 500 else best)
        flag = 2 if best <= a0 else (1 if best >= beta else 0)
        old = self.tt.get(key)
        if not old or old[0] <= depth: self.tt[key] = (depth, flag, stored, best_mv)

    def choose(self, board, started, root_ok=None, repeat=None, force_fwd=False):
        """Hàm chính để chọn nước đi với Iterative Deepening."""
        if len(self.tt) > TT_SIZE: self.tt.clear()
        b, repeat, self.nodes = board.copy(), dict(repeat) if repeat else {}, 0
        t, o = b.turn, 1 - b.turn
        soft_dl, hard_dl = started + SOFT_TIME, started + HARD_TIME
        self.deadline = soft_dl

        for mv in b.pawn_moves(t):
            if mv // 9 == GOAL_ROW[t] and (root_ok is None or root_ok(mv)): return mv

        F, f_me, me, op = b.fields(), b.fields()[t], b.pos[t], b.pos[o]
        d_me, d_op = f_me.dist(me), F[o].dist(op)
        pawns = [mv for mv in b.pawn_moves(t) if root_ok is None or root_ok(mv)]
        fwd = [m for m in pawns if f_me.dist(m) < d_me]

        if force_fwd and fwd:
            if b.left[t] == 0 and b.left[o] == 0: pawns = fwd
            else:
                for m in pawns:
                    if f_me.dist(m) >= d_me: repeat[m] = repeat.get(m, 0) + 85
        elif b.left[t] == 0 and fwd: pawns = fwd

        pawns.sort(key=lambda m: (f_me.dist(m), repeat.get(m, 0), -f_me.onward_deg(m), _CENTER_DIST[m]))

        is_safe = False
        if fwd and d_me <= d_op:
            try: is_safe = self._race_safe(b, F, min(soft_dl, time.perf_counter() + PRE_TIME))
            except Exception: pass

        if is_safe:
            if len(fwd) == 1: return fwd[0]
            pawns, walls = [m for m in pawns if f_me.dist(m) < d_me], []
        else:
            walls = self._root_walls(b, F, root_ok) if b.left[t] > 0 else []

        actions = pawns + walls
        if not actions: return None
        if len(actions) == 1: return actions[0]

        best_action, prev_best, prev_score, unstable, self.depth_reached = actions[0], actions[0], None, False, 0
        try:
            for depth in range(1, self.max_depth + 1):
                if time.perf_counter() >= (hard_dl if depth > 2 and unstable else soft_dl): break
                self.deadline = hard_dl if depth > 2 and unstable else soft_dl
                alpha, beta, cur_best, cur_score, scores = -WIN * 2, WIN * 2, None, -WIN * 2, {}

                for mv in actions:
                    if time.perf_counter() > self.deadline: raise _Timeout
                    if mv < WALL0:
                        pen = repeat.get(mv, 0)
                        b.pos[t], b.turn = mv, o
                        val = -self._search(b, depth - 1, -beta, -(alpha + pen), F, 1) - pen
                        b.pos[t], b.turn = me, t
                    else:
                        w = mv - WALL0
                        b.put_wall(w)
                        b.left[t], b.turn = b.left[t] - 1, o
                        try: val = -self._search(b, depth - 1, -beta, -alpha, b.fields(), 1)
                        finally:
                            b.left[t], b.turn = b.left[t] + 1, t
                            b.remove_wall(w)
                    scores[mv] = val
                    if val > cur_score:
                        cur_score, cur_best = val, mv
                        if val > alpha: alpha = val

                if cur_best is not None:
                    unstable = cur_best != prev_best or (prev_score is not None and cur_score < prev_score - 100)
                    best_action, prev_best, prev_score, self.depth_reached = cur_best, cur_best, cur_score, depth
                    actions.sort(key=lambda a: -scores.get(a, -WIN * 2))
                if abs(cur_score) >= WIN - 500 and depth >= WIN - abs(cur_score): break
        except _Timeout: pass
        return best_action

# API tương tác trực tiếp với JSON của hệ thống Arena
_BOT, _STATE_VISITS, _WASTE, _LAST_TURN = None, {}, {0: [], 1: []}, {}
_REP_PEN = (0, 0, 60, 160, 320)

def board_from_observation(obs):
    pid, b = obs["player_id"], Board()
    b.pos[pid], b.pos[1 - pid] = obs["you"]["position"][0] * 9 + obs["you"]["position"][1], obs["opponent"]["position"][0] * 9 + obs["opponent"]["position"][1]
    b.left[pid], b.left[1 - pid] = obs["you"]["walls_remaining"], obs["opponent"]["walls_remaining"]
    for r, c in obs["walls"]["horizontal"]: b.put_wall(wall_id('h', r, c))
    for r, c in obs["walls"]["vertical"]: b.put_wall(wall_id('v', r, c))
    b.turn = pid
    return b

def move_to_action(mv):
    if mv < WALL0: return {"type": "move", "to": [int(mv // 9), int(mv % 9)]}
    orient, r, c = wall_info(mv - WALL0)
    return {"type": "wall", "orientation": orient.upper(), "row": int(r), "col": int(c)}

def _fallback(observation):
    try:
        moves = observation["legal_moves"]
        if not moves: return {"type": "pass"}
        best = min(moves, key=lambda m: (observation["distances"][m[0]][m[1]] if observation["distances"][m[0]][m[1]] >= 0 else 10**6) * 10 + abs(m[1] - 4))
        return {"type": "move", "to": [int(best[0]), int(best[1])]}
    except Exception: return {"type": "pass"}

def _choose(observation):
    global _BOT
    started, pid, turn = time.perf_counter(), observation["player_id"], observation["turn"]
    if turn <= 2 or turn < _LAST_TURN.get(pid, 0):
        for k in [k for k in _STATE_VISITS if k[0] == pid]: del _STATE_VISITS[k]
        _WASTE[pid] = []
    _LAST_TURN[pid] = turn

    legal = {r * 9 + c for r, c in observation["legal_moves"]} | {WALL0 + wall_id(w["orientation"].lower(), w["row"], w["col"]) for w in observation["legal_walls"]}
    if not legal: return {"type": "pass"}

    board = board_from_observation(observation)
    if _BOT is None: _BOT = Bot()
    
    me_cell, opp_cell, nw = board.pos[pid], board.pos[1 - pid], len(observation["walls"]["horizontal"]) + len(observation["walls"]["vertical"])
    cur_skey = (pid, me_cell, opp_cell, nw)
    _STATE_VISITS[cur_skey] = _STATE_VISITS.get(cur_skey, 0) + 1

    repeat, d_cur = {}, board.dist(me_cell, GOAL[pid])
    for m in legal:
        if m < WALL0:
            s_cnt = _STATE_VISITS.get((pid, m, opp_cell, nw), 0)
            if s_cnt > 0 and not (s_cnt < 3 and board.dist(m, GOAL[pid]) < d_cur): repeat[m] = _REP_PEN[min(s_cnt, 4)]

    mv = _BOT.choose(board, started, root_ok=lambda m: m in legal, repeat=repeat, force_fwd=sum(_WASTE[pid][-6:]) >= 2)
    if mv is None or mv not in legal: return _fallback(observation)

    _WASTE[pid].append(1 if mv < WALL0 and board.dist(mv, GOAL[pid]) >= d_cur else 0)
    return move_to_action(mv)

def choose_action(observation):
    try: return _choose(observation)
    except Exception: return _fallback(observation)