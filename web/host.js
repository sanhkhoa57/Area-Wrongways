'use strict';
/* =====================================================================
   Máy chủ phòng chạy NGAY TRONG TRÌNH DUYỆT của chủ phòng.
   Đây là bản chuyển từ app.py: cùng các loại tin nhắn (create / join / ready /
   room / countdown / round_start / turn / round_end / match_end ...).
   Luật chơi vẫn là wrongway/environment.py, chạy bằng Pyodide trong
   worker_engine.js. Bot của từng người chơi chạy trên máy chính họ; chủ phòng
   chỉ gửi quan sát (act_req) và nhận lại nước đi (act_res), nên code của đối
   thủ không bao giờ rời khỏi máy của họ.
   ===================================================================== */

const HOST_CFG = {
  maxTurns: 500,
  turnDelayMs: 250,          // nghỉ giữa hai lượt để người xem kịp nhìn
  countdownSeconds: 3,
  pauseBetweenRoundsMs: 2000,
  nameMax: 32,
  prepareTimeoutMs: 120000,  // chờ cả hai máy nạp xong Python + code bot
  actWatchdogMs: 60000,      // phòng khi máy đối phương treo mà không đóng kết nối
};
const MSSV_RE = /^[A-Za-z0-9]{3,20}$/;
const sleepMs = ms => new Promise(r => setTimeout(r, ms));

/* Bản JS của visualizer.render_board (cùng định dạng ASCII). */
function renderBoard(state, names) {
  const size = state.board_size;
  const width = 4 * size - 1;
  const canvas = Array.from({ length: 2 * size - 1 }, () => Array(width).fill(' '));
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) canvas[2 * r][4 * c + 1] = '.';
  for (const [r, c] of state.walls.horizontal) for (let x = 4 * c; x < 4 * c + 7; x++) canvas[2 * r + 1][x] = '=';
  for (const [r, c] of state.walls.vertical) for (let y = 2 * r; y < 2 * r + 3; y++) canvas[y][4 * c + 3] = '#';
  state.positions.forEach(([r, c], p) => { canvas[2 * r][4 * c + 1] = String(p); });
  const cols = Array.from({ length: size }, (_, i) => i).join('   ');
  const lines = ['     ' + cols];
  canvas.forEach((line, i) => {
    const label = i % 2 === 0 ? String(i / 2) : ' ';
    lines.push(` ${label}  ${line.join('').replace(/\s+$/, '')}`);
  });
  names = names || ['Player 0', 'Player 1'];
  lines.push([0, 1].map(p =>
    `P${p} ${names[p]}: ${state.walls_remaining[p]} walls, goal row ${state.goal_rows[p]}`).join(' | '));
  return lines.join('\n');
}

const INITIAL_STATE = {
  board_size: 9, positions: [[8, 4], [0, 4]], goal_rows: [0, 8],
  walls_remaining: [10, 10], walls: { horizontal: [], vertical: [] },
};

function cleanName(value) {
  if (typeof value !== 'string') return null;
  const name = value.split(/\s+/).filter(Boolean).join(' ');
  if (!name || [...name].length > HOST_CFG.nameMax || /[\u0000-\u001f]/.test(name)) return null;
  return name;
}

function randomId() {
  const a = new Uint8Array(4);
  crypto.getRandomValues(a);
  return [...a].map(b => b.toString(16).padStart(2, '0')).join('');
}

class Player {
  constructor(conn, name, mssv) {
    this.id = randomId();
    this.conn = conn;
    this.name = name;
    this.mssv = mssv;
    this.room = null;
    this.ready = false;
    this.hasBot = false;
  }
  send(msg) { this.conn.send(msg); }
  pub(room) {
    return {
      id: this.id, name: this.name, mssv: this.mssv, ready: this.ready, has_bot: this.hasBot,
      is_host: room.hostId === this.id, role: room.roleOf(this.id),
    };
  }
}

class Room {
  constructor(id) {
    this.id = id;
    this.players = [];
    this.hostId = null;
    this.status = 'lobby';
    this.round = 0;
    this.roles = [null, null];
    this.results = [];
    this.token = 0;            // tăng mỗi lần bắt đầu/hủy trận; tiến trình cũ thấy token đổi sẽ tự dừng
    this.reqId = 0;
    this.requests = new Map(); // id -> { playerId, resolve }
  }
  get busy() { return this.status === 'countdown' || this.status === 'running'; }
  get(id) { return this.players.find(p => p.id === id) || null; }
  defaultRoles() {
    const others = this.players.filter(p => p.id !== this.hostId).map(p => p.id);
    return [this.hostId, others[0] ?? null];
  }
  roleOf(id) { return this.roles[0] === id ? 'P0' : this.roles[1] === id ? 'P1' : null; }
  resetLobby() { this.status = 'lobby'; this.round = 0; this.roles = this.defaultRoles(); }
  broadcast(msg) { for (const p of this.players) p.send(msg); }
  sync() { this.broadcast({ type: 'room', room: this.pub() }); }
  pub() {
    const data = {
      id: this.id, status: this.status, round: this.round, host: this.hostId, roles: [...this.roles],
      players: this.players.map(p => p.pub(this)), results: this.results,
      config: { timeout: window.WW.ACTION_TIMEOUT_MS / 1000, max_turns: HOST_CFG.maxTurns },
    };
    if (this.status === 'lobby') {
      const names = this.roles.map(pid => this.get(pid)?.name ?? '(trống)');
      data.preview = renderBoard(INITIAL_STATE, names);
    }
    return data;
  }

  /* Gửi một yêu cầu cho người chơi và chờ trả lời có cùng id. */
  request(player, msg, timeoutMs) {
    const id = ++this.reqId;
    return new Promise(resolve => {
      const timer = setTimeout(() => { this.requests.delete(id); resolve({ status: 'timeout', error: 'Không nhận được phản hồi từ máy người chơi.' }); }, timeoutMs);
      this.requests.set(id, { playerId: player.id, resolve: v => { clearTimeout(timer); resolve(v); } });
      player.send({ ...msg, id });
    });
  }
  settle(id, playerId, value) {
    const r = this.requests.get(id);
    if (r && r.playerId === playerId) { this.requests.delete(id); r.resolve(value); }
  }
  cancelRequests() {
    for (const r of this.requests.values()) r.resolve({ status: 'aborted' });
    this.requests.clear();
  }
}

class Host {
  /* roomId: mã 4 số đã được PeerJS cấp; engine: WW.Engine; closeAll: đóng mọi kết nối. */
  constructor(roomId, engine, closeAll) {
    this.room = new Room(roomId);
    this.engine = engine;
    this.closeAll = closeAll;
  }

  err(conn, message) { conn.send({ type: 'error', message }); }

  handle(conn, msg) {
    const room = this.room;
    const kind = msg.type;
    let player = conn.player;

    if (kind === 'create' || kind === 'join') {
      if (player && player.room) return this.err(conn, 'Bạn đang ở trong một phòng khác.');
      const name = cleanName(msg.name);
      const mssv = String(msg.mssv ?? '').trim();
      if (!name) return this.err(conn, `Tên không hợp lệ (1–${HOST_CFG.nameMax} ký tự).`);
      if (!MSSV_RE.test(mssv)) return this.err(conn, 'MSSV chỉ gồm chữ/số, dài 3–20 ký tự.');
      if (kind === 'join') {
        if (room.players.length >= 2) return this.err(conn, `Phòng ${room.id} đã đủ 2 người.`);
        if (room.players.some(p => p.mssv === mssv)) return this.err(conn, 'MSSV này đã có trong phòng.');
      } else if (room.players.length) {
        return this.err(conn, 'Phòng đã tồn tại.');
      }
      player = new Player(conn, name, mssv);
      conn.player = player;
      player.room = room;
      room.players.push(player);
      if (room.hostId === null) room.hostId = player.id;
      if (room.status === 'finished') room.results = [];
      room.resetLobby();
      player.send({ type: 'joined', you: player.id, room: room.pub() });
      if (kind === 'join') {
        for (const other of room.players) if (other !== player) other.send({ type: 'notice', message: `${name} đã vào phòng.` });
      }
      room.sync();
      return;
    }

    if (!player || !player.room) return this.err(conn, 'Bạn chưa ở trong phòng nào.');

    if (kind === 'act_res' || kind === 'prepared') {
      room.settle(msg.id, player.id, msg);
      return;
    }

    if (kind === 'leave') {
      conn.send({ type: 'left' });
      this.leave(player);
      return;
    }

    if (kind === 'ready') {
      if (room.busy) return player.send({ type: 'error', message: 'Trận đấu đang diễn ra.' });
      player.hasBot = true;
      player.ready = true;
      if (room.status === 'finished') { room.results = []; room.resetLobby(); }
      room.sync();
      if (room.players.length === 2 && room.players.every(p => p.ready)) this.startMatch();
      return;
    }

    if (kind === 'unready') {
      if (room.busy) return player.send({ type: 'error', message: 'Trận đấu đang diễn ra, không thể hủy.' });
      player.ready = false;
      if (room.status === 'finished') { room.results = []; room.resetLobby(); }
      room.sync();
      return;
    }

    if (kind === 'ping') return player.send({ type: 'pong' });
    this.err(conn, 'Lệnh không hợp lệ.');
  }

  /* Kết nối đứt (đóng tab, mất mạng) hay bấm Rời phòng đều đi chung một đường. */
  drop(conn) {
    if (conn.player) this.leave(conn.player);
  }

  abortMatch(reason) {
    const room = this.room;
    room.token++;
    room.cancelRequests();
    room.results = [];
    for (const p of room.players) p.ready = false;
    room.resetLobby();
    room.broadcast({ type: 'aborted', reason });
  }

  leave(player) {
    const room = this.room;
    if (!player.room) return;
    const wasHost = room.hostId === player.id;
    room.players = room.players.filter(p => p !== player);
    player.room = null;
    player.conn.player = null;
    player.ready = false;

    if (wasHost) {
      // Chủ phòng cũng là máy chủ: chủ phòng thoát thì phòng đóng.
      room.token++;
      room.cancelRequests();
      room.broadcast({ type: 'closed', reason: `${player.name} (chủ phòng) đã thoát, phòng đã đóng.` });
      room.players = [];
      this.closeAll();
      return;
    }
    if (room.busy) this.abortMatch(`${player.name} đã rời phòng, trận đấu bị hủy.`);
    room.broadcast({ type: 'notice', message: `${player.name} đã rời phòng.` });
    if (room.status === 'finished') room.results = [];
    room.resetLobby();
    room.sync();
  }

  startMatch() {
    const room = this.room;
    room.token++;
    room.results = [];
    this.runSeries(room.token);   // tự bắt mọi lỗi bên trong
  }

  async runSeries(token) {
    const room = this.room;
    const alive = () => room.token === token;
    const order = room.defaultRoles();            // [chủ phòng, người thứ hai]
    const info = {};
    for (const pid of order) {
      const p = room.get(pid);
      info[pid] = { id: p.id, name: p.name, mssv: p.mssv };
    }
    const seed = Math.floor(Math.random() * 1_000_000);   // hai hiệp dùng chung seed cho công bằng

    try {
      try { await this.engine.ready; } catch (err) {
        throw new Error(`không tải được Python (${err.message})`);
      }
      for (const roundNo of [1, 2]) {
        const roles = roundNo === 1 ? order : [...order].reverse();
        const names = roles.map(pid => info[pid].name);
        room.round = roundNo;
        room.roles = [...roles];
        room.status = 'countdown';
        room.sync();
        room.broadcast({ type: 'countdown', round: roundNo, seconds: HOST_CFG.countdownSeconds, swapped: roundNo === 2 });

        // Trong lúc đếm ngược, mỗi máy nạp bot của mình (seed của bot = seed * 2 + chỉ số bên).
        const prepared = Promise.all(roles.map((pid, i) =>
          room.request(room.get(pid), { type: 'prepare', round: roundNo, seed: seed * 2 + i }, HOST_CFG.prepareTimeoutMs)));
        await Promise.all([sleepMs((HOST_CFG.countdownSeconds + 0.5) * 1000), prepared]);
        if (!alive()) return;

        room.status = 'running';
        room.sync();
        room.broadcast({
          type: 'round_start', round: roundNo, roles, names,
          files: roles.map(pid => `${info[pid].mssv}.py`), seed,
          frame: renderBoard(INITIAL_STATE, names),
        });

        const replay = await this.playRound(roles, names, roundNo, alive);
        if (!alive() || !replay) return;

        const winner = replay.winner;
        const result = {
          round: roundNo,
          p0: { ...info[roles[0]] }, p1: { ...info[roles[1]] },
          winner, winner_id: winner !== null ? roles[winner] : null,
          reason: replay.reason, total_turns: replay.totalTurns, stats: replay.stats,
        };
        room.results.push(result);
        room.broadcast({ type: 'round_end', round: roundNo, result });
        room.sync();
        if (roundNo === 1) { await sleepMs(HOST_CFG.pauseBetweenRoundsMs); if (!alive()) return; }
      }

      // Tổng kết theo cách tính điểm của mùa giải: thắng 3, hòa 1, thua 0.
      const table = {};
      for (const pid of order) table[pid] = { ...info[pid], points: 0, wins: 0, draws: 0, losses: 0 };
      for (const r of room.results) {
        if (r.winner_id === null) {
          for (const pid of order) { table[pid].points += 1; table[pid].draws += 1; }
        } else {
          const loser = order.find(pid => pid !== r.winner_id);
          table[r.winner_id].points += 3; table[r.winner_id].wins += 1; table[loser].losses += 1;
        }
      }
      const [first, second] = order.map(pid => table[pid]);
      const overall = first.points === second.points ? null : (first.points > second.points ? first : second);
      const summary = { rounds: room.results, table: [first, second], winner: overall, seed };

      for (const p of room.players) p.ready = false;
      room.status = 'finished';
      room.sync();
      room.broadcast({ type: 'match_end', summary });
    } catch (err) {
      if (alive()) {
        room.token++;
        room.cancelRequests();
        for (const p of room.players) p.ready = false;
        room.results = [];
        room.resetLobby();
        room.broadcast({ type: 'aborted', reason: `Lỗi khi chạy trận: ${err.message}` });
        room.sync();
      }
    }
  }

  /* Một hiệp: lặp hỏi bot của người đang tới lượt, áp dụng vào engine (environment.py). */
  async playRound(roles, names, roundNo, alive) {
    const room = this.room;
    const stats = [0, 1].map(() => ({ ok: 0, pass: 0, invalid: 0, timeout: 0, error: 0 }));
    let snap = await this.engine.call('new_game', HOST_CFG.maxTurns, 0);
    let last = null;
    while (!snap.done) {
      if (!alive()) return null;
      const cur = snap.current_player;
      const obs = await this.engine.call('observation', cur);
      const reply = await room.request(room.get(roles[cur]), { type: 'act_req', obs }, HOST_CFG.actWatchdogMs);
      if (!alive() || reply.status === 'aborted') return null;
      let status = ['ok', 'timeout', 'error'].includes(reply.status) ? reply.status : 'error';
      const elapsed = Number.isFinite(reply.elapsed) ? Math.round(reply.elapsed * 1e6) / 1e6 : 0;
      const rec = await this.engine.call('apply', status, JSON.stringify(reply.action ?? null),
        typeof reply.error === 'string' ? reply.error.slice(0, 2000) : null, names);
      if (!alive()) return null;
      stats[cur][rec.status] += 1;
      snap = rec.state;
      last = rec;
      room.broadcast({
        type: 'turn', round: roundNo, turn: rec.turn, player: rec.player, status: rec.status,
        action: rec.action, reason: rec.reason, error: rec.error, elapsed,
        header: rec.header, frame: renderBoard(rec.state, names),
        walls_remaining: rec.state.walls_remaining, positions: rec.state.positions,
      });
      if (!rec.done && HOST_CFG.turnDelayMs) await sleepMs(HOST_CFG.turnDelayMs);
    }
    return { winner: last.winner, reason: last.termination_reason, totalTurns: last.turn, stats };
  }
}

window.WW = Object.assign(window.WW || {}, { Host, renderBoard });
