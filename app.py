#!/usr/bin/env python3
"""Wrongway Online: máy chủ thi đấu bot Wrongway theo thời gian thực.

Kiến trúc
---------
* FastAPI phục vụ giao diện tĩnh (thư mục ``static``) và một endpoint
  WebSocket duy nhất ``/ws``. Mỗi kết nối WebSocket là một người chơi.
* Toàn bộ trạng thái phòng nằm trong RAM (dict ``ROOMS``) và chỉ được sửa trên
  luồng event loop, nên không cần khóa.
* Trận đấu dùng nguyên engine của thư mục ``wrongway``: ``arena.run_match``
  chạy trong một thread riêng (``asyncio.to_thread``). Callback ``on_turn``
  của arena đẩy từng lượt về event loop bằng ``call_soon_threadsafe``, rồi
  event loop phát cho cả phòng. Khung bàn cờ được vẽ bằng
  ``visualizer.render_board`` y như khi chạy ``arena.py --render``.

Chạy:  python app.py      (mặc định http://0.0.0.0:8000)
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import random
import re
import secrets
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from wrongway.arena import run_match
from wrongway.environment import Environment
from wrongway.visualizer import render_board


# --------------------------------------------------------------------------
# Cấu hình (đổi bằng biến môi trường nếu cần)
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WORK_DIR = BASE_DIR / "runtime" / "rooms"          # nơi lưu tạm code bot
TEMPLATE_PATH = BASE_DIR / "wrongway" / "submissions" / "TEMPLATE.py"

ACTION_TIMEOUT = float(os.environ.get("WW_TIMEOUT", "1.0"))        # giây / nước
STARTUP_TIMEOUT = float(os.environ.get("WW_STARTUP_TIMEOUT", "5.0"))
MAX_TURNS = int(os.environ.get("WW_MAX_TURNS", "500"))
TURN_DELAY = float(os.environ.get("WW_TURN_DELAY", "0.25"))        # giây nghỉ giữa 2 lượt để người xem kịp nhìn
COUNTDOWN_SECONDS = 3
PAUSE_BETWEEN_ROUNDS = 2.0
MAX_CODE_BYTES = 200_000

NAME_MAX = 32
MSSV_RE = re.compile(r"^[A-Za-z0-9]{3,20}$")
ROOM_RE = re.compile(r"^\d{4}$")

_rng = random.SystemRandom()


class MatchAborted(Exception):
    """Ném ra trong on_turn để dừng run_match khi có người rời phòng."""


# --------------------------------------------------------------------------
# Mô hình dữ liệu
# --------------------------------------------------------------------------
class Conn:
    """Một kết nối WebSocket. Mọi tin gửi đi đều qua hàng đợi riêng để không
    bao giờ có hai lệnh send chạy chồng lên nhau trên cùng một socket."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.player: Optional[Player] = None

    def send(self, msg: dict) -> None:
        self.outbox.put_nowait(msg)


class Player:
    def __init__(self, conn: Conn, name: str, mssv: str):
        self.id = secrets.token_hex(4)
        self.conn = conn
        self.name = name
        self.mssv = mssv
        self.room: Optional[Room] = None
        self.ready = False
        self.code_path: Optional[Path] = None

    def send(self, msg: dict) -> None:
        self.conn.send(msg)

    def public(self, room: "Room") -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mssv": self.mssv,
            "ready": self.ready,
            "has_bot": self.code_path is not None,
            "is_host": room.host_id == self.id,
            "role": room.role_of(self.id),
        }


class Room:
    """Trạng thái: lobby -> countdown -> running -> (countdown -> running) -> finished."""

    def __init__(self, room_id: str):
        self.id = room_id
        self.players: List[Player] = []
        self.host_id: Optional[str] = None
        self.status = "lobby"
        self.round = 0
        self.roles: List[Optional[str]] = [None, None]   # [id người làm P0, id người làm P1]
        self.results: List[dict] = []
        self.task: Optional[asyncio.Task] = None
        self.cancel: Optional[threading.Event] = None
        self.token = 0          # tăng mỗi lần bắt đầu/hủy trận; tin cũ từ thread sẽ bị bỏ qua
        # Hậu tố ngẫu nhiên để thư mục cũ (đang chờ xóa) không đụng phòng mới cùng ID.
        self.dir = WORK_DIR / "{}-{}".format(room_id, secrets.token_hex(3))

    # ---- tiện ích ----
    @property
    def busy(self) -> bool:
        return self.status in ("countdown", "running")

    def get(self, player_id: Optional[str]) -> Optional[Player]:
        return next((p for p in self.players if p.id == player_id), None)

    def default_roles(self) -> List[Optional[str]]:
        """Hiệp 1: chủ phòng là P0, người còn lại là P1."""
        others = [p.id for p in self.players if p.id != self.host_id]
        return [self.host_id, others[0] if others else None]

    def role_of(self, player_id: str) -> Optional[str]:
        if self.roles[0] == player_id:
            return "P0"
        if self.roles[1] == player_id:
            return "P1"
        return None

    def reset_lobby(self) -> None:
        self.status = "lobby"
        self.round = 0
        self.roles = self.default_roles()

    def broadcast(self, msg: dict) -> None:
        for player in self.players:
            player.send(msg)

    def emit(self, token: int, msg: dict) -> None:
        """Được gọi từ thread trận đấu (qua call_soon_threadsafe)."""
        if token == self.token:
            self.broadcast(msg)

    def sync(self) -> None:
        self.broadcast({"type": "room", "room": self.public()})

    def public(self) -> dict:
        data = {
            "id": self.id,
            "status": self.status,
            "round": self.round,
            "host": self.host_id,
            "roles": list(self.roles),
            "players": [p.public(self) for p in self.players],
            "results": self.results,
            "config": {"timeout": ACTION_TIMEOUT, "max_turns": MAX_TURNS},
        }
        if self.status == "lobby":
            names = [self.get(pid).name if self.get(pid) else "(trống)" for pid in self.roles]
            data["preview"] = render_board(Environment(MAX_TURNS, 0).snapshot(), names)
        return data


ROOMS: Dict[str, Room] = {}


# --------------------------------------------------------------------------
# Quản lý phòng
# --------------------------------------------------------------------------
def allocate_room_id() -> Optional[str]:
    """ID 4 chữ số, không trùng với phòng đang hoạt động."""
    if len(ROOMS) >= 10_000:
        return None
    for _ in range(64):
        candidate = "{:04d}".format(_rng.randrange(10_000))
        if candidate not in ROOMS:
            return candidate
    free = [f"{i:04d}" for i in range(10_000) if f"{i:04d}" not in ROOMS]
    return _rng.choice(free) if free else None


def remove_later(path: Path, delay: float = 3.0) -> None:
    """Xóa thư mục sau vài giây: tiến trình bot có thể vẫn đang đứng trong đó
    (Windows không cho xóa thư mục là cwd của tiến trình khác)."""
    try:
        asyncio.get_running_loop().call_later(delay, shutil.rmtree, path, True)
    except RuntimeError:
        shutil.rmtree(path, ignore_errors=True)


def clean_name(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > NAME_MAX or any(ord(ch) < 32 for ch in name):
        return None
    return name


def check_code(code) -> Optional[str]:
    """Kiểm tra nhanh trước khi lưu, để báo lỗi ngay thay vì để bot mất lượt."""
    if not isinstance(code, str) or not code.strip():
        return "Bạn chưa dán mã nguồn bot."
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        return "Mã nguồn quá lớn (tối đa {} KB).".format(MAX_CODE_BYTES // 1000)
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


def abort_match(room: Room, reason: str) -> None:
    room.token += 1
    if room.cancel is not None:
        room.cancel.set()
    if room.task is not None:
        room.task.cancel()
    room.task = None
    room.cancel = None
    room.results = []
    for player in room.players:
        player.ready = False
    room.reset_lobby()
    room.broadcast({"type": "aborted", "reason": reason})


def leave_room(player: Player) -> None:
    room = player.room
    if room is None:
        return
    was_host = room.host_id == player.id
    room.players.remove(player)
    player.room = None
    player.ready = False
    remove_later(room.dir / player.id)
    player.code_path = None

    if room.busy:
        abort_match(room, "{} đã rời phòng, trận đấu bị hủy.".format(player.name))

    if not room.players:
        # Phòng trống: xóa khỏi bộ nhớ, ID được giải phóng ngay để cấp lại.
        ROOMS.pop(room.id, None)
        remove_later(room.dir)
        return

    if was_host:
        room.host_id = room.players[0].id
        room.broadcast({"type": "notice", "message": "{} đã rời phòng. {} trở thành chủ phòng.".format(
            player.name, room.players[0].name)})
    else:
        room.broadcast({"type": "notice", "message": "{} đã rời phòng.".format(player.name)})
    if room.status == "finished":
        room.results = []
    room.reset_lobby()
    room.sync()


def start_match(room: Room) -> None:
    room.token += 1
    room.results = []
    room.task = asyncio.create_task(run_series(room, room.token))


# --------------------------------------------------------------------------
# Chạy trận: 2 hiệp, đổi bên
# --------------------------------------------------------------------------
def play_round(paths, names, round_no, seed, cancel: threading.Event, emit):
    """Chạy trong thread. Gọi đúng arena.run_match của bộ wrongway."""

    def on_turn(record):
        if cancel.is_set():
            raise MatchAborted()
        header = "Turn {} | P{} {} | {} | {}".format(
            record["turn"], record["player"], names[record["player"]],
            record["status"], json.dumps(record["action"], ensure_ascii=False))
        emit({
            "type": "turn",
            "round": round_no,
            "turn": record["turn"],
            "player": record["player"],
            "status": record["status"],
            "action": record["action"],
            "reason": record["reason"],
            "error": record["error"],
            "elapsed": record["elapsed"],
            "header": header,
            "frame": render_board(record["state"], names),
            "walls_remaining": record["state"]["walls_remaining"],
            "positions": record["state"]["positions"],
        })
        # Nghỉ ngoài đồng hồ của bot (arena chỉ bấm giờ lúc gọi choose_action).
        if TURN_DELAY and cancel.wait(TURN_DELAY):
            raise MatchAborted()

    return run_match(
        paths[0], paths[1],
        timeout=ACTION_TIMEOUT, max_turns=MAX_TURNS, seed=seed,
        starting_player=0, startup_timeout=STARTUP_TIMEOUT, on_turn=on_turn,
    )


async def run_series(room: Room, token: int) -> None:
    loop = asyncio.get_running_loop()
    cancel = threading.Event()
    room.cancel = cancel
    order = room.default_roles()            # [chủ phòng, người thứ hai]
    info = {}
    for pid in order:
        p = room.get(pid)
        info[pid] = {"id": p.id, "name": p.name, "mssv": p.mssv, "path": str(p.code_path)}
    seed = _rng.randrange(1_000_000)        # hai hiệp dùng chung seed cho công bằng

    def emit(msg):
        try:
            loop.call_soon_threadsafe(room.emit, token, msg)
        except RuntimeError:
            pass                            # event loop đã đóng (tắt server)

    try:
        for round_no in (1, 2):
            roles = order if round_no == 1 else order[::-1]
            names = [info[pid]["name"] for pid in roles]
            room.round = round_no
            room.roles = list(roles)
            room.status = "countdown"
            room.sync()
            room.broadcast({"type": "countdown", "round": round_no, "seconds": COUNTDOWN_SECONDS,
                            "swapped": round_no == 2})
            await asyncio.sleep(COUNTDOWN_SECONDS + 0.5)

            room.status = "running"
            room.sync()
            room.broadcast({
                "type": "round_start",
                "round": round_no,
                "roles": roles,
                "names": names,
                "files": [Path(info[pid]["path"]).name for pid in roles],
                "seed": seed,
                "frame": render_board(Environment(MAX_TURNS, 0).snapshot(), names),
            })
            replay = await asyncio.to_thread(
                play_round, [info[pid]["path"] for pid in roles], names, round_no, seed, cancel, emit)

            winner = replay["winner"]
            winner_id = roles[winner] if winner is not None else None
            result = {
                "round": round_no,
                "p0": {k: info[roles[0]][k] for k in ("id", "name", "mssv")},
                "p1": {k: info[roles[1]][k] for k in ("id", "name", "mssv")},
                "winner": winner,                       # 0, 1 hoặc None (hòa)
                "winner_id": winner_id,
                "reason": replay["reason"],             # "goal" hoặc "max_turns"
                "total_turns": replay["total_turns"],
                "stats": replay["stats"],
            }
            room.results.append(result)
            room.broadcast({"type": "round_end", "round": round_no, "result": result})
            room.sync()
            if round_no == 1:
                await asyncio.sleep(PAUSE_BETWEEN_ROUNDS)

        # Tổng kết theo cách tính điểm của mùa giải: thắng 3, hòa 1, thua 0.
        table = {pid: {**{k: info[pid][k] for k in ("id", "name", "mssv")},
                       "points": 0, "wins": 0, "draws": 0, "losses": 0} for pid in order}
        for result in room.results:
            if result["winner_id"] is None:
                for pid in order:
                    table[pid]["points"] += 1
                    table[pid]["draws"] += 1
            else:
                loser = next(pid for pid in order if pid != result["winner_id"])
                table[result["winner_id"]]["points"] += 3
                table[result["winner_id"]]["wins"] += 1
                table[loser]["losses"] += 1
        first, second = (table[pid] for pid in order)
        if first["points"] != second["points"]:
            overall = first if first["points"] > second["points"] else second
        else:
            overall = None
        summary = {"rounds": room.results, "table": [first, second], "winner": overall, "seed": seed}

        for player in room.players:
            player.ready = False
        room.status = "finished"
        room.sync()
        room.broadcast({"type": "match_end", "summary": summary})
    except asyncio.CancelledError:
        pass                                    # abort_match đã xử lý trạng thái phòng
    except MatchAborted:
        pass
    except Exception as exc:                    # lỗi ngoài dự kiến: báo cho phòng và quay về sảnh
        if room.token == token:
            room.token += 1
            for player in room.players:
                player.ready = False
            room.results = []
            room.reset_lobby()
            room.broadcast({"type": "aborted", "reason": "Lỗi máy chủ khi chạy trận: {}".format(exc)})
            room.sync()
    finally:
        cancel.set()
        if room.cancel is cancel:
            room.cancel = None
        if room.token == token or room.task is asyncio.current_task():
            room.task = None


# --------------------------------------------------------------------------
# Xử lý tin nhắn từ client
# --------------------------------------------------------------------------
def handle_message(conn: Conn, msg: dict) -> None:
    kind = msg.get("type")
    player = conn.player

    if kind in ("create", "join"):
        if player is not None and player.room is not None:
            conn.send({"type": "error", "message": "Bạn đang ở trong một phòng khác."})
            return
        name = clean_name(msg.get("name"))
        mssv = str(msg.get("mssv", "")).strip()
        if not name:
            conn.send({"type": "error", "message": "Tên không hợp lệ (1–{} ký tự).".format(NAME_MAX)})
            return
        if not MSSV_RE.match(mssv):
            conn.send({"type": "error", "message": "MSSV chỉ gồm chữ/số, dài 3–20 ký tự."})
            return

        if kind == "create":
            room_id = allocate_room_id()
            if room_id is None:
                conn.send({"type": "error", "message": "Máy chủ đã hết ID phòng trống."})
                return
            room = Room(room_id)
            ROOMS[room_id] = room
        else:
            room_id = str(msg.get("room", "")).strip()
            if not ROOM_RE.match(room_id):
                conn.send({"type": "error", "message": "ID phòng phải gồm đúng 4 chữ số."})
                return
            room = ROOMS.get(room_id)
            if room is None:
                conn.send({"type": "error", "message": "Không tìm thấy phòng {}.".format(room_id)})
                return
            if len(room.players) >= 2:
                conn.send({"type": "error", "message": "Phòng {} đã đủ 2 người.".format(room_id)})
                return
            if any(p.mssv == mssv for p in room.players):
                conn.send({"type": "error", "message": "MSSV này đã có trong phòng."})
                return

        player = Player(conn, name, mssv)
        conn.player = player
        player.room = room
        room.players.append(player)
        if room.host_id is None:
            room.host_id = player.id
        room.results = [] if room.status == "finished" else room.results
        room.reset_lobby()
        player.send({"type": "joined", "you": player.id, "room": room.public()})
        if kind == "join":
            for other in room.players:
                if other is not player:
                    other.send({"type": "notice", "message": "{} đã vào phòng.".format(name)})
        room.sync()
        return

    if player is None or player.room is None:
        conn.send({"type": "error", "message": "Bạn chưa ở trong phòng nào."})
        return
    room = player.room

    if kind == "leave":
        leave_room(player)
        conn.player = None
        conn.send({"type": "left"})
        return

    if kind == "ready":
        if room.busy:
            player.send({"type": "error", "message": "Trận đấu đang diễn ra."})
            return
        code = msg.get("code")
        problem = check_code(code)
        if problem:
            player.send({"type": "code_error", "message": problem})
            return
        folder = room.dir / player.id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "{}.py".format(player.mssv)
        path.write_text(code, encoding="utf-8")
        player.code_path = path
        player.ready = True
        if room.status == "finished":
            room.results = []
            room.reset_lobby()
        room.sync()
        if len(room.players) == 2 and all(p.ready for p in room.players):
            start_match(room)
        return

    if kind == "unready":
        if room.busy:
            player.send({"type": "error", "message": "Trận đấu đang diễn ra, không thể hủy."})
            return
        player.ready = False
        if room.status == "finished":
            room.results = []
            room.reset_lobby()
        room.sync()
        return

    if kind == "ping":
        player.send({"type": "pong"})
        return

    conn.send({"type": "error", "message": "Lệnh không hợp lệ."})


# --------------------------------------------------------------------------
# FastAPI
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app):
    # Dọn code bot còn sót từ lần chạy trước.
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Wrongway Online", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/template", response_class=PlainTextResponse)
async def template():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/api/health")
async def health():
    return {"ok": True, "rooms": len(ROOMS)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    conn = Conn(ws)

    async def writer():
        while True:
            msg = await conn.outbox.get()
            await ws.send_text(json.dumps(msg, ensure_ascii=False))

    writer_task = asyncio.create_task(writer())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                handle_message(conn, msg)
    except (WebSocketDisconnect, RuntimeError, KeyError):
        pass
    finally:
        # Đóng tab, mất mạng hay bấm Rời phòng đều đi chung một đường.
        if conn.player is not None:
            leave_room(conn.player)
            conn.player = None
        writer_task.cancel()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))
