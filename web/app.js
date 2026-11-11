'use strict';

/* =====================================================================
   Wrongway Online – client
   Không có máy chủ riêng: chủ phòng chạy Host (host.js) ngay trong trình duyệt,
   mọi người nối với nhau qua broker MQTT công cộng (WebSocket). Mọi thay đổi trạng thái đều do Host
   gửi xuống (type: room / countdown / round_start / turn / ...).
   ===================================================================== */

const $ = (sel, root = document) => root.querySelector(sel);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d = '') { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* chế độ riêng tư */ } },
};

const S = {
  bot: null,           // WW.Bot: worker Python chạy bot của mình
  readyCode: null,     // code đã được duyệt khi bấm Sẵn sàng (chỉ nằm trên máy mình)
  me: null,            // id của mình
  room: null,          // trạng thái phòng mới nhất từ server
  mode: 'create',
  termMode: 'idle',    // idle: đang xem bàn cờ xem trước; live: đang/đã chiếu trận
  live: null,          // { round, roles, walls }
  cards: new Map(),    // id người chơi -> phần tử thẻ (giữ nguyên để làm hiệu ứng đổi chỗ)
  emptyCards: [null, null],
  myPanel: null,
  drawerOpen: false,
  pending: false,      // đã gửi "ready", đang chờ server trả lời
  template: '',
  lastSummary: null,
};

const REASON = {
  goal: 'về đích', max_turns: 'hết lượt',
};
const STATUS_VI = { ok: 'ok', pass: 'pass', invalid: 'invalid', timeout: 'timeout', error: 'error' };

/* ------------------------------------------------------------ Mạng (MQTT qua WebSocket) */
/* Mọi máy nối ra một broker MQTT công cộng bằng WebSocket (cổng HTTPS), nên không phụ thuộc
   NAT/tường lửa của từng nhà như WebRTC. Broker chỉ chuyển tin; tin được mã hóa AES-GCM bằng
   khóa sinh từ mã phòng. Code bot KHÔNG bao giờ được gửi đi, chỉ có tín hiệu, bàn cờ và nước đi.
   Chủ phòng chạy Host (host.js) ngay trong trang; người vào phòng chỉ gửi/nhận tin. */
const BROKERS = [
  'wss://broker.emqx.io:8084/mqtt',
  'wss://broker.hivemq.com:8884/mqtt',
  'wss://test.mosquitto.org:8081',
];
const TOPIC = 'wrongway-area/v1/';
const te = new TextEncoder();
const td = new TextDecoder();
const rid = () => Math.random().toString(36).slice(2, 10);

async function deriveKey(room) {
  const base = await crypto.subtle.importKey('raw', te.encode('wrongway-area-v1:' + room), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: te.encode('wrongway-area-salt-v1'), iterations: 50000, hash: 'SHA-256' },
    base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
function toB64(u8) { let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000)); return btoa(s); }
function fromB64(s) { const b = atob(s); const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
async function seal(key, obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, te.encode(JSON.stringify(obj))));
  const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12);
  return toB64(out);
}
async function unseal(key, text) {
  const raw = fromB64(text);
  const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: raw.subarray(0, 12) }, key, raw.subarray(12));
  return JSON.parse(td.decode(pt));
}

function connectOne(url, clientId, will) {
  return new Promise((resolve, reject) => {
    if (typeof mqtt === 'undefined') return reject({ type: 'no-lib' });
    const c = mqtt.connect(url, { clientId, clean: true, keepalive: 15, reconnectPeriod: 0, connectTimeout: 8000, will });
    let settled = false;
    const fail = e => { if (settled) return; settled = true; clearTimeout(timer); try { c.end(true); } catch { /* đã đóng */ } reject(e); };
    const timer = setTimeout(() => fail({ type: 'timeout' }), 9500);
    c.once('connect', () => { if (settled) return; settled = true; clearTimeout(timer); resolve(c); });
    c.once('error', e => fail(e));
    c.once('close', () => fail({ type: 'closed' }));
  });
}
function subscribe(c, topic) {
  return new Promise((resolve, reject) => c.subscribe(topic, { qos: 1 }, err => (err ? reject(err) : resolve())));
}

/* Gửi tin theo đúng thứ tự (mã hóa là bất đồng bộ nên cần xếp hàng). */
class Chan {
  constructor(client, key) { this.client = client; this.key = key; this.q = Promise.resolve(); }
  send(topic, obj) {
    this.q = this.q.then(async () => {
      const payload = await seal(this.key, obj);
      this.client.publish(topic, payload, { qos: 1 });
    }).catch(() => { /* bỏ qua tin lỗi, giữ hàng đợi sống */ });
  }
}
/* Nhận tin theo đúng thứ tự, tự loại tin trùng (QoS 1 có thể gửi lặp). */
function inbox(key, handle) {
  let q = Promise.resolve();
  const seen = new Set();
  return payload => {
    q = q.then(async () => {
      let env;
      try { env = await unseal(key, payload.toString()); } catch { return; }
      if (!env || typeof env.i !== 'string' || seen.has(env.i)) return;
      seen.add(env.i);
      if (seen.size > 800) seen.delete(seen.values().next().value);
      handle(env);
    }).catch(() => { /* bỏ qua tin lỗi */ });
  };
}

function netError(e, fallback) {
  const t = e && e.type;
  if (t === 'no-lib') return 'Không tải được thư viện kết nối (MQTT). Kiểm tra mạng rồi tải lại trang.';
  if (t === 'no-broker') return 'Không kết nối được tới máy chủ trung chuyển. Hãy kiểm tra mạng, tắt VPN hoặc tiện ích chặn quảng cáo rồi thử lại.';
  if (t === 'peer-unavailable') return 'Không tìm thấy phòng này. Kiểm tra lại mã phòng, và chủ phòng phải đang mở trang.';
  return fallback || 'Không kết nối được. Kiểm tra mạng rồi thử lại.';
}

function dispatch(msg) {
  const fn = handlers[msg && msg.type];
  if (fn) fn(msg);
}

const Net = {
  client: null,
  chan: null,
  code: null,
  clientId: null,
  conn: null,        // người vào phòng: đối tượng gửi tin tới chủ phòng
  host: null,        // chủ phòng: Host đang chạy trong trang
  engine: null,
  localConn: null,
  remotes: new Map(),
  isHost: false,

  send(msg) {
    if (this.isHost && this.host) queueMicrotask(() => this.host && this.host.handle(this.localConn, msg));
    else if (this.conn && this.conn.open) this.conn.send(msg);
    else toast('Chưa kết nối tới phòng.', 'bad');
  },

  async startHost(name, mssv) {
    this.close();
    this.isHost = true;
    this.engine = new WW.Engine();            // bắt đầu tải Python (luật chơi) ngay
    let found = null;
    for (let i = 0; i < 20 && !found; i++) {
      const code = String(Math.floor(Math.random() * 10000)).padStart(4, '0');
      const hostTopic = `${TOPIC}${code}/host`;
      let client = null, lastErr = null;
      for (const url of BROKERS) {
        try { client = await connectOne(url, 'wwh-' + rid(), { topic: hostTopic, payload: '', qos: 1, retain: true }); break; }
        catch (e) { lastErr = e; }
      }
      if (!client) throw (lastErr && lastErr.type === 'no-lib') ? lastErr : { type: 'no-broker' };
      // Mã đã có chủ phòng khác (tin "host" được giữ lại trên broker) thì chọn mã khác.
      const taken = await new Promise(resolve => {
        let seen = false;
        const on = (t, p) => { if (t === hostTopic && p.length > 0) seen = true; };
        client.on('message', on);
        subscribe(client, hostTopic).then(() => setTimeout(() => { client.off('message', on); resolve(seen); }, 1200), () => resolve(true));
      });
      if (taken) { client.end(true); continue; }
      found = { code, client };
    }
    if (!found) throw { type: 'no-broker' };
    const { code, client } = found;
    const key = await deriveKey(code);
    this.client = client; this.code = code;
    this.chan = new Chan(client, key);
    client.publish(`${TOPIC}${code}/host`, '1', { qos: 1, retain: true });

    this.localConn = { player: null, send: m => queueMicrotask(() => dispatch(m)) };
    this.host = new WW.Host(code, this.engine, () => setTimeout(() => backHomeIfHost(), 0));
    const wrapFor = id => {
      let w = this.remotes.get(id);
      if (!w) {
        w = { player: null, send: m => this.chan && this.chan.send(`${TOPIC}${code}/c/${id}`, { i: rid(), m }) };
        this.remotes.set(id, w);
      }
      return w;
    };
    await subscribe(client, `${TOPIC}${code}/h`);
    const hTopic = `${TOPIC}${code}/h`;
    const receive = inbox(key, env => {
      if (!this.host || typeof env.c !== 'string' || !env.m || typeof env.m !== 'object') return;
      const wrap = wrapFor(env.c);
      if (env.m.type === '__gone') { this.remotes.delete(env.c); this.host.drop(wrap); return; }
      this.host.handle(wrap, env.m);
    });
    client.on('message', (t, p) => { if (t === hTopic) receive(p); });
    client.on('close', () => { if (this.client === client && S.room) { toast('Mất kết nối mạng. Phòng đã đóng.', 'bad'); backHome(); } });
    this.send({ type: 'create', name, mssv });
  },

  async startJoin(name, mssv, room) {
    this.close();
    this.isHost = false;
    const key = await deriveKey(room);
    const id = 'wwc-' + rid();
    const hTopic = `${TOPIC}${room}/h`;
    const hostTopic = `${TOPIC}${room}/host`;
    const myTopic = `${TOPIC}${room}/c/${id}`;
    const gone = await seal(key, { i: rid(), c: id, m: { type: '__gone' } });
    // Thử lần lượt từng broker; phòng nằm ở broker nào thì tin "host" được giữ lại ở đó.
    let client = null, sawBroker = false;
    for (const url of BROKERS) {
      let c;
      try { c = await connectOne(url, id, { topic: hTopic, payload: gone, qos: 1, retain: false }); }
      catch (e) { if (e && e.type === 'no-lib') throw e; continue; }
      sawBroker = true;
      const present = await new Promise(resolve => {
        let done = false;
        const on = (t, p) => { if (!done && t === hostTopic && p.length > 0) { done = true; resolve(true); } };
        c.on('message', on);
        subscribe(c, hostTopic).then(() => setTimeout(() => { if (!done) { done = true; resolve(false); } }, 2500), () => resolve(false));
      });
      if (present) { client = c; break; }
      c.end(true);
    }
    if (!client) throw { type: sawBroker ? 'peer-unavailable' : 'no-broker' };
    await subscribe(client, myTopic);
    this.client = client; this.code = room; this.clientId = id;
    this.chan = new Chan(client, key);
    const receive = inbox(key, env => { if (env.m && typeof env.m === 'object') dispatch(env.m); });
    client.on('message', (t, p) => {
      if (t === myTopic) receive(p);
      else if (t === hostTopic && p.length === 0 && this.client === client && S.room) {
        toast('Chủ phòng đã thoát, phòng đã đóng.', 'bad'); backHome();
      }
    });
    client.on('close', () => { if (this.client === client && S.room) { toast('Mất kết nối mạng. Bạn đã bị đưa về sảnh.', 'bad'); backHome(); } });
    this.conn = { open: true, send: m => this.chan && this.chan.send(hTopic, { i: rid(), c: id, m }) };
    this.send({ type: 'join', name, mssv, room });
  },

  close() {
    const { client, engine, chan, code, isHost, clientId } = this;
    this.client = null; this.chan = null; this.code = null; this.clientId = null;
    this.conn = null; this.host = null; this.engine = null;
    this.localConn = null; this.isHost = false; this.remotes.clear();
    if (engine) engine.dispose();
    if (!client) return;
    try {
      if (isHost) chan.q = chan.q.then(() => client.publish(`${TOPIC}${code}/host`, '', { qos: 1, retain: true }));   // xóa dấu "phòng đang mở" sau các tin còn xếp hàng
      else if (chan) chan.send(`${TOPIC}${code}/h`, { i: rid(), c: clientId, m: { type: '__gone' } });
    } catch { /* đã đóng */ }
    // Chờ một chút để tin cuối (closed / left) kịp tới nơi trước khi ngắt kết nối.
    setTimeout(() => { try { client.end(true); } catch { /* đã đóng */ } }, 800);
  },
};

function backHomeIfHost() { if (Net.isHost && S.room) backHome(); }

const handlers = {
  error(m) {
    S.pending = false;
    if (!S.room) { showFormError(m.message); setFormBusy(false); Net.close(); }
    else { toast(m.message, 'bad'); renderPanels(); }
  },
  joined(m) { S.me = m.you; S.room = m.room; enterArena(); },
  room(m) {
    S.room = m.room;
    S.pending = false;
    renderRoom();
  },
  notice(m) { toast(m.message); },
  code_error(m) { S.pending = false; showCodeError(m.message); renderPanels(); },
  countdown(m) { closeModal(); runCountdown(m); },
  round_start(m) { startRound(m); },
  turn(m) { onTurn(m); },
  round_end(m) { onRoundEnd(m.result); },
  match_end(m) { S.lastSummary = m.summary; setTimeout(() => showResults(m.summary), 700); },
  aborted(m) {
    hideCountdown();
    termLog(`✖ ${m.reason}`, 'bad');
    toast(m.reason, 'bad');
    S.termMode = 'idle';
    S.live = null;
    renderRoom();
  },
  closed(m) { toast(m.reason, 'bad'); backHome(); },
  left() { backHome(); },
  pong() {},

  /* Chủ phòng nhờ máy mình nạp bot cho hiệp mới. */
  prepare(m) {
    const bot = S.bot;
    if (!bot || S.readyCode == null) {
      send({ type: 'prepared', id: m.id, ok: false, error: 'Chưa có code bot' });
      return;
    }
    bot.load(S.readyCode, m.seed).then(r => send({ type: 'prepared', id: m.id, ok: r.ok, error: r.error ?? null }));
  },
  /* Chủ phòng hỏi nước đi: chạy choose_action trên máy mình (có giới hạn thời gian). */
  act_req(m) {
    const bot = S.bot;
    if (!bot) { send({ type: 'act_res', id: m.id, status: 'error', action: null, elapsed: 0, error: 'Chưa có bot' }); return; }
    bot.act(m.obs).then(r => send({
      type: 'act_res', id: m.id, status: r.status, action: r.action, elapsed: r.elapsed, error: r.error,
    }));
  },
};

function send(msg) { Net.send(msg); }

/* -------------------------------------------------------------- Sảnh */
function openForm(mode, roomId = '') {
  S.mode = mode;
  const form = $('#entryForm');
  $('#choices').classList.add('hidden');
  form.classList.remove('hidden');
  $('#formTitle').textContent = mode === 'create' ? 'Tạo phòng mới' : 'Vào phòng';
  $('#submitBtn').textContent = mode === 'create' ? 'Tạo phòng' : 'Vào phòng';
  $('#roomField').classList.toggle('hidden', mode !== 'join');
  form.name.value = store.get('ww_name');
  form.mssv.value = store.get('ww_mssv');
  form.room.value = roomId;
  showFormError('');
  setFormBusy(false);
  (form.name.value ? (mode === 'join' && !roomId ? form.room : form.mssv.value ? $('#submitBtn') : form.mssv) : form.name).focus();
}

function closeForm() {
  $('#entryForm').classList.add('hidden');
  $('#choices').classList.remove('hidden');
}

function showFormError(text) { $('#formError').textContent = text || ''; }
function setFormBusy(busy) {
  const b = $('#submitBtn');
  b.disabled = busy;
  if (busy) b.textContent = 'Đang kết nối…';
  else b.textContent = S.mode === 'create' ? 'Tạo phòng' : 'Vào phòng';
}

async function submitEntry(e) {
  e.preventDefault();
  const f = e.target;
  const name = f.name.value.trim().replace(/\s+/g, ' ');
  const mssv = f.mssv.value.trim();
  const room = f.room.value.trim();
  if (!name) return showFormError('Bạn chưa nhập tên.');
  if (!/^[A-Za-z0-9]{3,20}$/.test(mssv)) return showFormError('MSSV chỉ gồm chữ/số, dài 3–20 ký tự.');
  if (S.mode === 'join' && !/^\d{4}$/.test(room)) return showFormError('ID phòng gồm đúng 4 chữ số.');
  store.set('ww_name', name);
  store.set('ww_mssv', mssv);
  showFormError('');
  setFormBusy(true);
  try {
    if (S.mode === 'create') await Net.startHost(name, mssv);
    else await Net.startJoin(name, mssv, room);
  } catch (err) {
    Net.close();
    showFormError(netError(err));
    setFormBusy(false);
  }
}

function enterArena() {
  $('#home').classList.add('hidden');
  $('#arena').classList.remove('hidden');
  S.cards.forEach(el => el.remove());
  S.cards.clear();
  S.emptyCards = [null, null];
  S.myPanel = null;
  $('#botPanels').replaceChildren();
  $('#log').replaceChildren();
  S.termMode = 'idle';
  S.live = null;
  S.drawerOpen = false;
  history.replaceState(null, '', `?room=${S.room.id}`);
  termLog(`Đã vào phòng ${S.room.id}. Bấm ADD BOT, dán code theo TEMPLATE.py rồi bấm Sẵn sàng.`, 'muted');
  // Bot chạy ngay trên máy mình bằng Python trong trình duyệt (Pyodide): bắt đầu tải từ giờ.
  if (S.bot) S.bot.dispose();
  S.bot = new WW.Bot();
  S.readyCode = null;
  termLog('Đang tải Python trong trình duyệt (lần đầu mất vài giây)…', 'muted');
  S.bot.rpc.ready.then(() => termLog('Python đã sẵn sàng.', 'muted'),
    err => termLog(`Không tải được Python: ${err.message}`, 'bad'));
  renderRoom();
}

function backHome() {
  S.room = null; S.me = null; S.live = null; S.pending = false; S.readyCode = null;
  if (S.bot) { S.bot.dispose(); S.bot = null; }
  Net.close();
  hideCountdown(); closeModal();
  $('#arena').classList.add('hidden');
  $('#home').classList.remove('hidden');
  closeForm();
  setFormBusy(false);
  history.replaceState(null, '', location.pathname);
}

/* ------------------------------------------------------------ Phòng */
function playerById(id) { return S.room?.players.find(p => p.id === id) || null; }
function busy() { return S.room && (S.room.status === 'countdown' || S.room.status === 'running'); }

function renderRoom() {
  const r = S.room;
  if (!r) return;
  $('#roomId').textContent = r.id;
  const statusText = {
    lobby: r.players.length < 2
      ? 'Đang chờ người chơi thứ hai. Gửi mã phòng cho bạn đấu.'
      : 'Hai bên dán code bot và bấm Sẵn sàng để bắt đầu.',
    countdown: `Chuẩn bị hiệp ${r.round}…`,
    running: `Đang đấu hiệp ${r.round}/2.`,
    finished: 'Đã xong 2 hiệp. Bấm Sẵn sàng để đấu ván mới.',
  }[r.status];
  $('#roomStatus').textContent = statusText;
  renderRounds();
  placeCards();
  renderPanels();
  if (S.termMode === 'idle' && r.preview) {
    setFrame(r.preview);
    $('#termTitle').textContent = 'wrongway — arena.py';
    $('#termMeta').textContent = r.players.length < 2 ? 'chờ đối thủ' : 'sảnh chờ';
  }
}

function resultText(res) {
  if (!res) return '';
  if (res.winner === null || res.winner === undefined) return `Hòa · ${res.total_turns} lượt`;
  const w = res.winner === 0 ? res.p0 : res.p1;
  return `${w.name} thắng (P${res.winner}) · ${res.total_turns} lượt`;
}

function renderRounds() {
  const r = S.room;
  const box = $('#rounds');
  box.replaceChildren();
  for (const n of [1, 2]) {
    const res = r.results.find(x => x.round === n);
    const li = document.createElement('li');
    let text = 'chưa đấu';
    if (res) { li.className = 'done'; text = resultText(res); }
    else if (busy() && r.round === n) { li.className = 'live'; text = r.status === 'countdown' ? 'đếm ngược…' : 'đang đấu'; }
    li.innerHTML = `<b>Hiệp ${n}</b><span class="r-res">${esc(text)}</span>`;
    box.appendChild(li);
  }
}

/* Thẻ người chơi: P0 góc dưới trái, P1 góc trên phải.
   Khi đổi bên, phần tử thẻ được chuyển sang slot kia và trượt tới vị trí mới (FLIP). */
function initials(name) {
  const parts = name.trim().split(/\s+/);
  const last = parts[parts.length - 1] || '?';
  return (parts.length > 1 ? parts[0][0] + last[0] : last.slice(0, 2)).toUpperCase();
}

function fillCard(el, p, idx) {
  const role = `P${idx}`;
  const isMe = p.id === S.me;
  const fighting = busy();
  let walls = '';
  if (S.live && S.room.status !== 'lobby' && S.live.roles[idx] === p.id) walls = `Tường ${S.live.walls[idx]}/10`;
  const readyHtml = fighting
    ? `<span class="ready fight">⚔ Đang thi đấu</span>`
    : `<span class="ready ${p.ready ? 'on' : ''}">${p.ready ? '● Đã sẵn sàng' : '○ Chưa sẵn sàng'}</span>`;
  el.className = `card role-${role.toLowerCase()}${isMe ? ' me' : ''}`;
  el.dataset.pid = p.id;
  el.innerHTML = `
    <div class="card-top">
      <span class="role-badge">${role}</span>
      ${p.is_host ? '<span class="tag host">♛ Chủ phòng</span>' : ''}
      ${isMe ? '<span class="tag you">Bạn</span>' : ''}
    </div>
    <div class="card-main">
      <div class="avatar">${esc(initials(p.name))}</div>
      <div><div class="pname">${esc(p.name)}</div><div class="mssv">MSSV ${esc(p.mssv)}</div></div>
    </div>
    <div class="card-foot">${readyHtml}<span class="walls">${walls}</span></div>
    <div class="goal">${idx === 0 ? 'Quân 0 · xuất phát hàng 8 → đích hàng 0' : 'Quân 1 · xuất phát hàng 0 → đích hàng 8'}</div>`;
}

function emptyCard(idx) {
  if (!S.emptyCards[idx]) S.emptyCards[idx] = document.createElement('div');
  const el = S.emptyCards[idx];
  el.className = `card empty role-p${idx}`;
  el.innerHTML = `<div><span class="pulse"></span>Đang chờ người chơi…</div>
    <div style="margin-top:8px">Mã phòng <b>${esc(S.room.id)}</b></div>`;
  return el;
}

function cardFor(pid, idx) {
  let el = S.cards.get(pid);
  if (!el) { el = document.createElement('div'); S.cards.set(pid, el); }
  fillCard(el, playerById(pid), idx);
  return el;
}

function placeCards() {
  const r = S.room;
  for (const [pid, el] of S.cards) {
    if (!playerById(pid)) { el.remove(); S.cards.delete(pid); }
  }
  const before = new Map();
  for (const el of S.cards.values()) if (el.isConnected) before.set(el, el.getBoundingClientRect());

  const els = [0, 1].map(i => (r.roles[i] && playerById(r.roles[i])) ? cardFor(r.roles[i], i) : emptyCard(i));
  const slots = [$('#slotP0'), $('#slotP1')];
  slots.forEach((slot, i) => { if (slot.firstElementChild !== els[i]) slot.replaceChildren(els[i]); });

  for (const el of els) {
    const a = before.get(el);
    if (!a) continue;
    const b = el.getBoundingClientRect();
    const dx = a.left - b.left, dy = a.top - b.top;
    if (Math.abs(dx) + Math.abs(dy) > 4) {
      el.animate(
        [{ transform: `translate(${dx}px, ${dy}px) scale(.94)`, zIndex: 5 }, { transform: 'none', zIndex: 5 }],
        { duration: 750, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
  }
}

function updateWalls() {
  if (!S.live) return;
  [$('#slotP0'), $('#slotP1')].forEach((slot, i) => {
    const w = slot.querySelector('.walls');
    if (w && slot.firstElementChild?.dataset.pid === S.live.roles[i]) w.textContent = `Tường ${S.live.walls[i]}/10`;
  });
}

/* ------------------------------------------------------- Khu vực bot */
function buildMyPanel() {
  const el = document.createElement('div');
  el.className = 'bot-panel mine';
  el.innerHTML = `
    <div class="bot-head">
      <div class="bot-who"><b>Bot của bạn</b><small class="bot-file"></small></div>
      <span class="chip"></span>
    </div>
    <button class="btn add-bot" type="button"><span>＋ ADD BOT</span><span class="chev">▾</span></button>
    <div class="drawer">
      <div class="drawer-inner">
        <div class="editor-tools">
          <label class="btn tiny">Tải file .py<input type="file" accept=".py,text/x-python,text/plain" hidden></label>
          <button class="btn tiny" type="button" data-act="template">Dán TEMPLATE</button>
          <button class="btn tiny" type="button" data-act="clear">Xóa</button>
          <span class="code-meta"></span>
        </div>
        <textarea class="code" spellcheck="false" autocapitalize="off" autocomplete="off"
          placeholder="def choose_action(observation):&#10;    ...&#10;    return {&quot;type&quot;: &quot;pass&quot;}"></textarea>
        <p class="code-error"></p>
        <button class="btn go full ready-btn" type="button">Sẵn sàng</button>
      </div>
    </div>`;

  const ta = $('.code', el);
  ta.value = store.get('ww_code') || S.template;
  const meta = () => {
    const lines = ta.value ? ta.value.split('\n').length : 0;
    $('.code-meta', el).textContent = `${lines} dòng · ${(new Blob([ta.value]).size / 1024).toFixed(1)} KB`;
  };
  meta();
  ta.addEventListener('input', () => { store.set('ww_code', ta.value); showCodeError(''); meta(); });
  ta.addEventListener('keydown', e => {
    if (e.key === 'Tab' && !ta.readOnly) {          // Tab chèn 4 dấu cách thay vì nhảy ô
      e.preventDefault();
      const { selectionStart: s, selectionEnd: t } = ta;
      ta.setRangeText('    ', s, t, 'end');
      ta.dispatchEvent(new Event('input'));
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('.ready-btn', el).click(); }
  });

  $('.add-bot', el).addEventListener('click', () => { S.drawerOpen = !S.drawerOpen; renderPanels(); if (S.drawerOpen) setTimeout(() => ta.focus(), 200); });
  $('input[type=file]', el).addEventListener('change', async e => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 3000000) return showCodeError('File quá lớn (tối đa 3 MB).');
    ta.value = await file.text();
    ta.dispatchEvent(new Event('input'));
    toast(`Đã nạp ${file.name}`, 'good');
  });
  $('[data-act=template]', el).addEventListener('click', () => {
    if (ta.value.trim() && ta.value !== S.template && !confirmReplace()) return;
    ta.value = S.template; ta.dispatchEvent(new Event('input'));
  });
  $('[data-act=clear]', el).addEventListener('click', () => { ta.value = ''; ta.dispatchEvent(new Event('input')); ta.focus(); });
  $('.ready-btn', el).addEventListener('click', () => {
    const me = playerById(S.me);
    if (!me || busy() || S.pending) return;
    if (me.ready) { send({ type: 'unready' }); return; }
    if (!ta.value.trim()) { showCodeError('Bạn chưa dán mã nguồn bot.'); return; }
    S.pending = true;
    showCodeError('');
    renderPanels();
    // Code được kiểm tra và giữ ngay trên máy mình; chỉ có tín hiệu "sẵn sàng" gửi đi.
    const code = ta.value;
    S.bot.check(code).then(problem => {
      if (problem) { S.pending = false; showCodeError(problem); renderPanels(); return; }
      S.readyCode = code;
      send({ type: 'ready' });
    });
  });
  return el;
}

/* Không dùng window.confirm để tránh hộp thoại chặn trang: nhấn 2 lần để xác nhận. */
let replaceArmed = 0;
function confirmReplace() {
  const now = Date.now();
  if (now - replaceArmed < 2500) { replaceArmed = 0; return true; }
  replaceArmed = now;
  toast('Bấm “Dán TEMPLATE” lần nữa để thay code hiện tại.');
  return false;
}

function showCodeError(text) {
  if (!S.myPanel) return;
  $('.code-error', S.myPanel).textContent = text || '';
  if (text && !S.drawerOpen) { S.drawerOpen = true; renderPanels(); }
}

function renderPanels() {
  const r = S.room;
  if (!r) return;
  const list = $('#botPanels');
  const me = playerById(S.me);
  const opp = r.players.find(p => p.id !== S.me) || null;
  if (!S.myPanel) S.myPanel = buildMyPanel();
  const el = S.myPanel;
  const locked = busy();
  const drawerOpen = S.drawerOpen && !locked;

  if (me) {
    const role = r.roles[0] === me.id ? 'P0' : r.roles[1] === me.id ? 'P1' : '';
    $('.bot-who b', el).textContent = `Bot của bạn${role ? ' · ' + role : ''}`;
    $('.bot-file', el).textContent = `${me.mssv}.py`;
    const chip = $('.chip', el);
    chip.className = 'chip' + (locked ? ' fight' : me.ready ? ' on' : '');
    chip.textContent = locked ? 'Đang thi đấu' : me.ready ? 'Đã sẵn sàng' : 'Chưa sẵn sàng';
    const addBtn = $('.add-bot', el);
    addBtn.disabled = locked;
    addBtn.classList.toggle('open', drawerOpen);
    $('.drawer', el).classList.toggle('open', drawerOpen);
    const ta = $('.code', el);
    ta.readOnly = me.ready || locked;
    el.querySelectorAll('.editor-tools .btn').forEach(b => {
      b.classList.toggle('disabled', ta.readOnly);
      if (b.tagName === 'BUTTON') b.disabled = ta.readOnly;
    });
    $('input[type=file]', el).disabled = ta.readOnly;
    const rb = $('.ready-btn', el);
    rb.disabled = locked || S.pending;
    rb.className = 'btn full ready-btn ' + (me.ready ? 'warn' : 'go');
    rb.textContent = S.pending ? 'Đang kiểm tra code…' : me.ready ? 'Hủy sẵn sàng' : 'Sẵn sàng';
  }

  const oppEl = document.createElement('div');
  oppEl.className = 'bot-panel other';
  if (opp) {
    const role = r.roles[0] === opp.id ? 'P0' : r.roles[1] === opp.id ? 'P1' : '';
    const chipCls = locked ? 'fight' : opp.ready ? 'on' : '';
    const chipTxt = locked ? 'Đang thi đấu' : opp.ready ? 'Đã sẵn sàng' : (opp.has_bot ? 'Đang sửa bot' : 'Chưa sẵn sàng');
    oppEl.innerHTML = `
      <div class="bot-head">
        <div class="bot-who"><b>Bot của ${esc(opp.name)}${role ? ' · ' + role : ''}</b><small>${esc(opp.mssv)}.py</small></div>
        <span class="chip ${chipCls}">${chipTxt}</span>
      </div>
      <button class="btn add-bot" type="button" disabled><span>🔒 ADD BOT</span></button>
      <p class="hint">Chỉ ${esc(opp.name)} thao tác được phần này. Code của đối thủ không được gửi tới máy bạn.</p>`;
  } else {
    oppEl.innerHTML = `
      <div class="bot-head"><div class="bot-who"><b>Người chơi thứ hai</b><small>chưa vào phòng</small></div><span class="chip">Trống</span></div>
      <button class="btn add-bot" type="button" disabled><span>ADD BOT</span></button>`;
  }

  if (list.firstElementChild !== el) list.prepend(el);
  while (list.children.length > 1) list.lastElementChild.remove();
  list.appendChild(oppEl);
}

/* ---------------------------------------------------------- Terminal */
function colorFrame(text) {
  // Bố cục của visualizer.render_board: dòng 0 là số cột, dòng cuối là thông tin,
  // ở giữa mỗi dòng có 4 ký tự nhãn hàng rồi tới phần bàn cờ.
  const lines = text.split('\n');
  const last = lines.length - 1;
  return lines.map((ln, i) => {
    if (i === 0) return `<span class="dim">${esc(ln)}</span>`;
    if (i === last) return `<span class="info">${esc(ln)}</span>`;
    let out = `<span class="dim">${esc(ln.slice(0, 4))}</span>`;
    for (const ch of ln.slice(4)) {
      if (ch === '0') out += '<span class="p0">0</span>';
      else if (ch === '1') out += '<span class="p1">1</span>';
      else if (ch === '=' || ch === '#') out += `<span class="w">${ch}</span>`;
      else if (ch === '.') out += '<span class="dot">.</span>';
      else out += esc(ch);
    }
    return out;
  }).join('\n');
}

function setFrame(text) { $('#frame').innerHTML = `<code>${colorFrame(text)}</code>`; }

function termLog(text, cls = '') {
  const log = $('#log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const div = document.createElement('div');
  div.className = 'ln ' + cls;
  div.textContent = text;
  log.appendChild(div);
  while (log.childElementCount > 600) log.firstElementChild.remove();
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function startRound(m) {
  S.termMode = 'live';
  S.live = { round: m.round, roles: m.roles, walls: [10, 10] };
  if (m.round === 1) $('#log').replaceChildren();
  termLog(`$ python arena.py --player1 ${m.files[0]} --player2 ${m.files[1]} --seed ${m.seed} --render`, 'cmd');
  termLog(`── Hiệp ${m.round}/2 · P0 ${m.names[0]}  vs  P1 ${m.names[1]} ──`, 'head');
  setFrame(m.frame);
  $('#termTitle').textContent = `arena.py — Hiệp ${m.round}/2`;
  $('#termMeta').textContent = `Lượt 0/${S.room?.config?.max_turns ?? 500}`;
  placeCards();
}

function onTurn(m) {
  if (!S.live || S.live.round !== m.round) return;
  S.live.walls = m.walls_remaining;
  setFrame(m.frame);
  const cls = m.status === 'ok' ? `t${m.player}` : (m.status === 'pass' ? 'muted' : 'warn');
  termLog(m.header, cls);
  if (m.error) termLog('  ' + m.error, 'bad');
  $('#termMeta').textContent = `Lượt ${m.turn}/${S.room?.config?.max_turns ?? 500}`;
  updateWalls();
}

function onRoundEnd(res) {
  const st = res.stats;
  if (res.winner === null) termLog(`= Hiệp ${res.round}: HÒA sau ${res.total_turns} lượt (${res.reason})`, 'win');
  else {
    const w = res.winner === 0 ? res.p0 : res.p1;
    termLog(`★ Hiệp ${res.round}: P${res.winner} ${w.name} (${w.mssv}) THẮNG sau ${res.total_turns} lượt (${res.reason})`, 'win');
  }
  termLog(`  P0 ${res.p0.name}: ${JSON.stringify(st[0])}`, 'muted');
  termLog(`  P1 ${res.p1.name}: ${JSON.stringify(st[1])}`, 'muted');
  toast(`Hiệp ${res.round}: ${resultText(res)}`, 'good');
}

/* ---------------------------------------------------------- Đếm ngược */
let cdToken = 0;
async function runCountdown(m) {
  const token = ++cdToken;
  const r = S.room;
  const n0 = playerById(r.roles[0])?.name ?? '?';
  const n1 = playerById(r.roles[1])?.name ?? '?';
  $('#cdRound').textContent = `Hiệp ${m.round} / 2`;
  $('#cdSub').innerHTML = (m.swapped ? '<span class="cd-swap">⇄ Đổi bên</span>' : '') +
    `<span class="c0">P0 ${esc(n0)}</span> &nbsp;vs&nbsp; <span class="c1">P1 ${esc(n1)}</span>`;
  $('#countdown').classList.remove('hidden');
  const num = $('#cdNum');
  for (let n = m.seconds; n >= 1; n--) {
    if (token !== cdToken) return;
    pop(num, String(n), false);
    await sleep(1000);
  }
  if (token !== cdToken) return;
  pop(num, 'GO!', true);
  await sleep(500);
  if (token === cdToken) $('#countdown').classList.add('hidden');
}

function pop(el, text, go) {
  el.textContent = text;
  el.classList.toggle('go', go);
  el.classList.remove('pop');
  void el.offsetWidth;          // khởi động lại animation
  el.classList.add('pop');
}

function hideCountdown() { cdToken++; $('#countdown').classList.add('hidden'); }

/* ------------------------------------------------------------ Kết quả */
function showResults(sum) {
  if (!S.room) return;
  const w = sum.winner;
  const champion = w
    ? `<div class="champion"><div class="trophy">🏆</div><div>
         <div class="label">Thắng chung cuộc</div>
         <div class="who">${esc(w.name)}</div>
         <div class="meta">MSSV ${esc(w.mssv)} · ${w.points} điểm (${w.wins}T-${w.draws}H-${w.losses}B)</div></div></div>`
    : `<div class="champion draw"><div class="trophy">🤝</div><div>
         <div class="label">Kết quả chung cuộc</div>
         <div class="who">Hòa</div>
         <div class="meta">Hai bên bằng điểm sau 2 hiệp</div></div></div>`;

  const rows = sum.rounds.map(r => {
    const res = r.winner === null
      ? 'Hòa'
      : `<span class="c${r.winner}">P${r.winner} ${esc((r.winner === 0 ? r.p0 : r.p1).name)} thắng</span>`;
    return `<tr>
      <td><b>Hiệp ${r.round}</b></td>
      <td><span class="c0">${esc(r.p0.name)}</span><small>${esc(r.p0.mssv)}</small></td>
      <td><span class="c1">${esc(r.p1.name)}</span><small>${esc(r.p1.mssv)}</small></td>
      <td>${res}<small class="txt">${REASON[r.reason] || esc(r.reason)}</small></td>
      <td class="num">${r.total_turns}</td></tr>`;
  }).join('');

  const table = sum.table.map(t => `<tr>
      <td><b>${esc(t.name)}</b><small>${esc(t.mssv)}</small></td>
      <td class="num">${t.wins}</td><td class="num">${t.draws}</td><td class="num">${t.losses}</td>
      <td class="num"><b>${t.points}</b></td></tr>`).join('');

  $('#resultBody').innerHTML = `
    <h2>Tổng kết trận đấu</h2>
    <p class="sub">Phòng ${esc(S.room.id)} · seed ${sum.seed} · thắng 3 điểm, hòa 1, thua 0</p>
    ${champion}
    <table class="res">
      <thead><tr><th>Hiệp</th><th>P0 (đi lên)</th><th>P1 (đi xuống)</th><th>Kết quả</th><th class="num">Lượt</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <table class="res">
      <thead><tr><th>Người chơi</th><th class="num">Thắng</th><th class="num">Hòa</th><th class="num">Thua</th><th class="num">Điểm</th></tr></thead>
      <tbody>${table}</tbody>
    </table>
    <div class="modal-actions">
      <button class="btn ghost" type="button" data-act="close">Đóng</button>
      <button class="btn" type="button" data-act="edit">Đổi code bot</button>
      <button class="btn go" type="button" data-act="again">Chơi lại (giữ bot)</button>
    </div>`;
  $('#resultModal').classList.remove('hidden');
}

function closeModal() { $('#resultModal').classList.add('hidden'); }

$('#resultModal').addEventListener('click', e => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (e.target.id === 'resultModal' || act === 'close') return closeModal();
  if (act === 'again') {
    closeModal();
    const me = playerById(S.me);
    if (me && !me.ready && S.myPanel) $('.ready-btn', S.myPanel).click();
    toast('Đã sẵn sàng. Chờ đối thủ bấm Sẵn sàng…');
  }
  if (act === 'edit') {
    closeModal();
    const me = playerById(S.me);
    if (me && me.ready) send({ type: 'unready' });
    S.drawerOpen = true;
    renderPanels();
    setTimeout(() => $('.code', S.myPanel)?.focus(), 250);
  }
});

/* -------------------------------------------------------------- Toast */
function toast(text, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = text;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, 3200);
}

/* --------------------------------------------------------------- Khởi động */
document.querySelectorAll('.choice').forEach(b => b.addEventListener('click', () => openForm(b.dataset.mode)));
$('#backBtn').addEventListener('click', closeForm);
$('#entryForm').addEventListener('submit', submitEntry);
$('#entryForm').room.addEventListener('input', e => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 4); });
$('#leaveBtn').addEventListener('click', () => send({ type: 'leave' }));
$('#copyBtn').addEventListener('click', async () => {
  const link = `${location.origin}${location.pathname}?room=${S.room?.id ?? ''}`;
  try { await navigator.clipboard.writeText(link); toast('Đã sao chép link mời: ' + link, 'good'); }
  catch { toast('Link mời: ' + link); }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

fetch('wrongway/submissions/TEMPLATE.py').then(r => r.text()).then(t => {
  S.template = t;
  if (S.myPanel) {
    const ta = $('.code', S.myPanel);
    if (!ta.value) { ta.value = t; ta.dispatchEvent(new Event('input')); }
  }
}).catch(() => {});

const preset = new URLSearchParams(location.search).get('room');
if (preset && /^\d{4}$/.test(preset)) openForm('join', preset);

/* Thông báo riêng tư: hiện mỗi lần mở trang, không ghi nhớ "đã xem". */
$('#privacyOk').addEventListener('click', () => $('#privacyModal').classList.add('hidden'));
$('#privacyModal').addEventListener('click', e => { if (e.target.id === 'privacyModal') e.target.classList.add('hidden'); });
$('#privacyOk').focus();
