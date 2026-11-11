'use strict';
/* Bot của chính mình (chạy trên máy mình) và engine luật chơi (chỉ chủ phòng dùng). */

const ACTION_TIMEOUT_MS = 2000;     // giới hạn mỗi lần gọi choose_action (Pyodide chậm hơn CPython bản gốc)
const STARTUP_TIMEOUT_MS = 5000;    // giới hạn thời gian chạy phần import của file bot
const WORKER_BOOT_TIMEOUT_MS = 90000;
const WEB_BASE = new URL('.', document.currentScript.src).href;

class WorkerRpc {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.worker = null;
    this.ready = null;
    this.pending = new Map();
    this.spawn();
  }

  spawn() {
    const w = new Worker(this.url);
    this.worker = w;
    this.ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Tải Python quá lâu')), WORKER_BOOT_TIMEOUT_MS);
      w.addEventListener('message', function onMsg(e) {
        if (e.data.type === 'booted') { clearTimeout(timer); w.removeEventListener('message', onMsg); resolve(); }
        else if (e.data.type === 'boot_error') { clearTimeout(timer); reject(new Error(e.data.error)); }
      });
      w.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Không tạo được worker')); });
    });
    this.ready.catch(() => {});
    w.addEventListener('message', e => {
      const p = this.pending.get(e.data.id);
      if (!p) return;
      if (e.data.type === 'stage') { p.onStage?.(e.data.stage); return; }
      if (e.data.type === 'done') { this.pending.delete(e.data.id); p.resolve(e.data); }
    });
  }

  /* Gửi một lệnh. timeoutMs chỉ bắt đầu tính từ lúc worker báo stage `startAt` (nếu có). */
  call(msg, { timeoutMs = 0, startAt = null } = {}) {
    const id = this.nextId++;
    const worker = this.worker;
    return new Promise(resolve => {
      let timer = null;
      const arm = () => {
        if (!timeoutMs || timer) return;
        timer = setTimeout(() => {
          this.pending.delete(id);
          resolve({ timedOut: true });
        }, timeoutMs);
      };
      this.pending.set(id, {
        resolve: data => { clearTimeout(timer); resolve(data); },
        onStage: stage => { if (stage === startAt) arm(); },
      });
      if (!startAt) arm();
      worker.postMessage({ ...msg, id });
    });
  }

  /* Dừng hẳn worker hiện tại (đang bị treo) và dựng worker mới ở nền. */
  restart() {
    try { this.worker.terminate(); } catch (_) { /* đã dừng */ }
    for (const p of this.pending.values()) p.resolve({ timedOut: true });
    this.pending.clear();
    this.spawn();
  }

  destroy() {
    try { this.worker.terminate(); } catch (_) { /* đã dừng */ }
    this.pending.clear();
  }
}

/* ---------------------------------------------------------------- Bot */
class Bot {
  constructor() {
    this.rpc = new WorkerRpc(WEB_BASE + 'worker_bot.js?v=7');
    this.code = null;
    this.seed = 0;
    this.loaded = false;
    this.loadError = null;
    this.needsReload = false;
  }

  async check(code) {
    try { await this.rpc.ready; } catch (err) { return `Không tải được Python trong trình duyệt: ${err.message}`; }
    const out = await this.rpc.call({ type: 'check', code });
    return out.problem ?? null;
  }

  /* Nạp code vào worker. Không bao giờ ném lỗi; lỗi được ghi vào this.loadError. */
  async load(code, seed) {
    this.code = code;
    this.seed = seed;
    this.loaded = false;
    this.loadError = null;
    this.needsReload = false;
    try { await this.rpc.ready; } catch (err) {
      this.loadError = `Agent startup failed: ${err.message}`;
      return { ok: false, error: this.loadError };
    }
    const out = await this.rpc.call({ type: 'load', code, seed }, { timeoutMs: STARTUP_TIMEOUT_MS, startAt: 'exec' });
    if (out.timedOut) {
      this.rpc.restart();
      this.loadError = `Agent import exceeded ${STARTUP_TIMEOUT_MS / 1000}s startup limit`;
      return { ok: false, error: this.loadError };
    }
    if (out.result.status !== 'ready') {
      this.loadError = `Agent startup failed: ${out.result.error}`;
      return { ok: false, error: this.loadError };
    }
    this.loaded = true;
    return { ok: true };
  }

  /* Một lượt: trả về { status: ok|timeout|error, action, elapsed, error }. */
  async act(observation) {
    if (!this.loaded && this.needsReload && this.code) {
      // Worker bị dừng sau timeout: nạp lại. Phần import không tính vào giờ của lượt này.
      await this.load(this.code, this.seed);
    }
    if (!this.loaded) {
      return { status: 'error', action: null, elapsed: 0, error: this.loadError || 'Agent chưa được nạp' };
    }
    const started = performance.now();
    const out = await this.rpc.call({ type: 'act', obs: JSON.stringify(observation) }, { timeoutMs: ACTION_TIMEOUT_MS });
    const elapsed = (performance.now() - started) / 1000;
    if (out.timedOut) {
      this.rpc.restart();
      this.loaded = false;
      this.loadError = null;
      this.needsReload = true;
      return { status: 'timeout', action: null, elapsed, error: `choose_action exceeded ${ACTION_TIMEOUT_MS / 1000}s action limit` };
    }
    const r = out.result;
    if (r.status === 'ok') return { status: 'ok', action: r.action ?? null, elapsed, error: null };
    return { status: 'error', action: null, elapsed, error: r.error || 'Agent error' };
  }

  dispose() { this.rpc.destroy(); }
}

/* ------------------------------------------------------------- Engine */
class Engine {
  constructor() { this.rpc = new WorkerRpc(WEB_BASE + 'worker_engine.js?v=7'); }
  get ready() { return this.rpc.ready; }
  async call(fn, ...args) {
    await this.rpc.ready;
    const out = await this.rpc.call({ fn, args });
    if (out.error) throw new Error(out.error);
    return out.result;
  }
  dispose() { this.rpc.destroy(); }
}

window.WW = Object.assign(window.WW || {}, { Bot, Engine, ACTION_TIMEOUT_MS });
