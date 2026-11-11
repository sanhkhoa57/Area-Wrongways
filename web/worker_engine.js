'use strict';
/* Web Worker của chủ phòng: chạy luật chơi (wrongway/environment.py) bằng Pyodide. */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';
importScripts(PYODIDE + 'pyodide.js');

let py = null;
const booted = (async () => {
  py = await loadPyodide({ indexURL: PYODIDE });
  const get = async rel => (await fetch(new URL(rel, self.location.href))).text();
  const [env, glue] = await Promise.all([get('../wrongway/environment.py'), get('py/engine_glue.py')]);
  py.FS.writeFile('/home/pyodide/environment.py', env);
  py.runPython('import sys; sys.path.insert(0, "/home/pyodide")');
  py.runPython(glue);
  self.postMessage({ type: 'booted' });
})().catch(err => self.postMessage({ type: 'boot_error', error: String(err) }));

self.onmessage = async e => {
  const m = e.data;
  await booted;
  if (!py) return;
  try {
    const fn = py.globals.get(m.fn);
    const out = fn(...m.args);
    fn.destroy();
    self.postMessage({ type: 'done', id: m.id, result: JSON.parse(out) });
  } catch (err) {
    self.postMessage({ type: 'done', id: m.id, error: String(err).slice(0, 2000) });
  }
};
