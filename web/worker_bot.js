'use strict';
/* Web Worker chạy bot của người chơi bằng Pyodide.
   Bị terminate khi quá giờ; trang chính sẽ tạo worker mới. */
// Pyodide được đặt sẵn trong repo (web/vendor/pyodide) để không phụ thuộc CDN, vì một số nhà mạng chặn jsdelivr.
const PYODIDE = new URL('vendor/pyodide/', self.location.href).href;
const PYODIDE_CDN = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';   // chỉ dùng để tải thêm gói như numpy
importScripts(PYODIDE + 'pyodide.js');

let py = null;
const booted = (async () => {
  py = await loadPyodide({ indexURL: PYODIDE });
  // Lõi Python lấy từ repo; các gói thêm (numpy, ...) không có sẵn thì tải từ CDN.
  try { py._api.config.indexURL = PYODIDE_CDN; } catch (_) { /* bản Pyodide khác: bỏ qua */ }
  py.setStdout({ batched() {} });
  py.setStderr({ batched() {} });
  const src = await (await fetch(new URL('py/bot_harness.py', self.location.href))).text();
  py.runPython(src);
  self.postMessage({ type: 'booted' });
})().catch(err => self.postMessage({ type: 'boot_error', error: String(err) }));

self.onmessage = async e => {
  const m = e.data;
  await booted;
  if (!py) return;
  try {
    if (m.type === 'check') {
      const fn = py.globals.get('check_code');
      const problem = fn(m.code);
      fn.destroy();
      self.postMessage({ type: 'done', id: m.id, problem: problem ?? null });
    } else if (m.type === 'load') {
      try { await py.loadPackagesFromImports(m.code); } catch (_) { /* gói không có: để import báo lỗi */ }
      self.postMessage({ type: 'stage', id: m.id, stage: 'exec' });
      const fn = py.globals.get('load');
      const out = fn(m.code, m.seed);
      fn.destroy();
      self.postMessage({ type: 'done', id: m.id, result: JSON.parse(out) });
    } else if (m.type === 'act') {
      const fn = py.globals.get('act');
      const out = fn(m.obs);
      fn.destroy();
      self.postMessage({ type: 'done', id: m.id, result: JSON.parse(out) });
    }
  } catch (err) {
    self.postMessage({ type: 'done', id: m.id, result: { status: 'error', error: String(err).slice(0, 2000) } });
  }
};
