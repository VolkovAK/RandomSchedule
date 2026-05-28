import os
from copy import deepcopy

import yaml

DAILY_STATE_PATH = "daily_state.yaml"

DEFAULT_DAILY_STATE = {
    "sent_date": "1970-01-01",
    "target_date": "1970-01-01",
    "mode": "normal",
    "exact_time_minutes": 0,
    "exact_time": "00:00",
    "display_time": "00:00",
    "bet_multiplier": 1,
    "chat_id": None,
    "fog_sigma": 0,
    "fog_center_minutes": 0,
    "fog_center": "00:00",
    "display_delta": 0,
    "revealed": False,
    "king_started": False,
    "king_deadline_iso": None,
    "king_winner": None,
    "pending_king_user_id": None,
    "pending_king_until_iso": None,
    "pending_king_password": None,
    "duel_date": "1970-01-01",
    "duel_player1": None,
    "duel_player2": None,
    "schedule_kind": "group",
    "solo_player": None,
    "solo_checkin": False,
    "insurance_holder": None,
    "expulsion_debuff": None,
}


def load_daily_state() -> dict:
    if not os.path.exists(DAILY_STATE_PATH):
        return deepcopy(DEFAULT_DAILY_STATE)
    with open(DAILY_STATE_PATH) as f:
        data = yaml.safe_load(f) or {}
    state = deepcopy(DEFAULT_DAILY_STATE)
    state.update(data)
    return state


def save_daily_state(state: dict) -> None:
    with open(DAILY_STATE_PATH, "w") as f:
        yaml.safe_dump(state, f, allow_unicode=True)


def sync_state_to_bot_data(bot_data: dict, state: dict) -> None:
    bot_data["daily_state"] = state
    bot_data["sent_date"] = state["sent_date"]
    bot_data["sent_time"] = state.get("display_time", state["exact_time"])


def get_daily_state(bot_data: dict) -> dict:
    if "daily_state" not in bot_data:
        bot_data["daily_state"] = load_daily_state()
        sync_state_to_bot_data(bot_data, bot_data["daily_state"])
    return bot_data["daily_state"]


def update_daily_state(bot_data: dict, **kwargs) -> dict:
    state = get_daily_state(bot_data)
    state.update(kwargs)
    save_daily_state(state)
    sync_state_to_bot_data(bot_data, state)
    return state


def reset_daily_state(bot_data: dict, keep_insurance: bool = True) -> None:
    holder = None
    debuff = None
    if keep_insurance:
        prev = get_daily_state(bot_data)
        holder = prev.get("insurance_holder")
        debuff = prev.get("expulsion_debuff")
    state = deepcopy(DEFAULT_DAILY_STATE)
    if keep_insurance and holder:
        state["insurance_holder"] = holder
    if keep_insurance and debuff:
        state["expulsion_debuff"] = debuff
    save_daily_state(state)
    sync_state_to_bot_data(bot_data, state)


def set_insurance_holder(bot_data: dict, nickname: str) -> None:
    update_daily_state(bot_data, insurance_holder=nickname)


def clear_insurance(bot_data: dict) -> None:
    update_daily_state(bot_data, insurance_holder=None)


def set_expulsion_debuff(bot_data: dict, nickname: str) -> None:
    update_daily_state(bot_data, expulsion_debuff=nickname)


def clear_expulsion_debuff(bot_data: dict) -> None:
    update_daily_state(bot_data, expulsion_debuff=None)


def cancel_named_jobs(job_queue, names: list[str]) -> None:
    if job_queue is None:
        return
    for job in job_queue.jobs():
        if job.name in names:
            job.schedule_removal()
