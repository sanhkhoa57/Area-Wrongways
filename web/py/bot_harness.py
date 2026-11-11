"""Chạy trong Pyodide, bên trong Web Worker của người chơi (chỉ bot của chính họ)."""

import ast
import json
import random
import sys
import types

_choose = None


def _error_text(exc):
    try:
        return "{}: {}".format(type(exc).__name__, exc)[:2000]
    except BaseException:
        return type(exc).__name__


def check_code(code, max_bytes=3000000):
    """Kiểm tra nhanh trước khi Sẵn sàng. Trả về chuỗi lỗi hoặc None."""
    if not isinstance(code, str) or not code.strip():
        return "Bạn chưa dán mã nguồn bot."
    if len(code.encode("utf-8")) > max_bytes:
        return "Mã nguồn quá lớn (tối đa {} MB).".format(max_bytes // 1000000)
    try:
        tree = ast.parse(code, filename="bot.py")
    except SyntaxError as exc:
        return "Lỗi cú pháp ở dòng {}: {}".format(exc.lineno, exc.msg)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "choose_action":
            return None
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "choose_action" for t in node.targets):
            return None
    return "Không thấy hàm choose_action(observation) ở cấp module (xem TEMPLATE.py)."


def load(code, seed):
    """Nạp bot như một module mới. Trả về JSON {"status": "ready"|"error"}."""
    global _choose
    _choose = None
    try:
        random.seed(seed)
        module = types.ModuleType("wrongway_submission")
        sys.modules["wrongway_submission"] = module
        exec(compile(code, "bot.py", "exec"), module.__dict__)
        fn = getattr(module, "choose_action", None)
        if not callable(fn):
            raise TypeError("Submission must define choose_action(observation)")
        _choose = fn
    except BaseException as exc:
        return json.dumps({"status": "error", "error": _error_text(exc)})
    return json.dumps({"status": "ready"})


def act(observation_json):
    """Gọi choose_action một lần. Trả về JSON {"status": "ok"|"error", ...}."""
    try:
        observation = json.loads(observation_json)
        action = _choose(observation)
        return json.dumps({"status": "ok", "action": action}, allow_nan=False)
    except BaseException as exc:
        return json.dumps({"status": "error", "error": _error_text(exc)})
