"""Chạy trong Pyodide trên máy chủ phòng: bọc wrongway/environment.py (giữ nguyên luật)."""

import json

from environment import Environment

_env = None


def new_game(max_turns, starting_player):
    global _env
    _env = Environment(max_turns=max_turns, starting_player=starting_player)
    return json.dumps(_env.snapshot())


def observation(player):
    return json.dumps(_env.get_observation(player))


def apply(status, action_json, error, names):
    """Giống vòng lặp trong arena.run_match: ok -> step, còn lại -> skip_turn."""
    env = _env
    player = env.current_player
    if status == "ok":
        action = json.loads(action_json) if action_json is not None else None
        update = env.step(action)
        result_status = update["status"]
        err = update.get("reason") if result_status == "invalid" else None
    else:
        update = env.skip_turn(status)
        result_status = status
        err = error
    return json.dumps({
        "turn": env.turn_count,
        "player": player,
        "action": update["action"],
        "status": result_status,
        "reason": update.get("reason"),
        "error": err,
        "header": "Turn {} | P{} {} | {} | {}".format(
            env.turn_count, player, names[player], result_status,
            json.dumps(update["action"], ensure_ascii=False)),
        "state": env.snapshot(),
        "done": env.done,
        "winner": env.winner,
        "termination_reason": env.termination_reason,
    })
