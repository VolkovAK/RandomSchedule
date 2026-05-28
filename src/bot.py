import os
import random
from datetime import datetime, timedelta
from typing import Optional, Tuple

import telegram
import yaml
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from jobs import restore_jobs, schedule_fog_reveal, schedule_king_deadline
from modes import (
    build_announcement_text,
    build_fog_display,
    build_solo_announcement_text,
    compute_king_deadline,
    format_bet_suffix,
    generate_password,
    get_solo_checkin_deadline,
    nickname_matches,
    roll_bet_multiplier,
    roll_exact_time_minutes,
    roll_mode,
    target_date_from,
)
from phrases import (
    CHECKIN_ALREADY_PHRASES,
    CHECKIN_KING_NOT_STARTED_PHRASES,
    CHECKIN_LATE_PHRASES,
    CHECKIN_NOT_SOLO_PHRASES,
    CHECKIN_SUCCESS_PHRASES,
    DUEL_PHRASES,
    DUEL_REPEAT_PHRASES,
    KING_ALREADY_PHRASES,
    KING_MEDIA_TIMEOUT_PHRASES,
    KING_PASSWORD_PHRASES,
    KING_START_PHRASES,
    SAVE_FAIL_PHRASES,
    SAVE_NO_INSURANCE_PHRASES,
    SAVE_NOT_OWNER_PHRASES,
    SAVE_SUCCESS_PHRASES,
)
from state import (
    cancel_named_jobs,
    clear_insurance,
    get_daily_state,
    load_daily_state,
    reset_daily_state,
    set_insurance_holder,
    update_daily_state,
)
from utils import apply_phrase, bold_md, parse_minutes_to_time, parse_time_to_minutes

CONFIG_PATH = "config.yaml"

DEFAULT_MODE_CONFIG = {
    "mode_fog_chance": 0.10,
    "mode_king_chance": 0.05,
    "king_countdown_min": 5,
    "king_countdown_max": 30,
    "king_media_timeout_sec": 180,
    "fog_sigma_min": 10,
}


async def send_md(update: Update, text: str) -> None:
    await update.effective_message.reply_text(
        text=text,
        parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
    )


def _roll_daily_schedule(
    bot_data: dict,
    chat_id: int,
    job_queue,
    solo_player: Optional[str] = None,
) -> Tuple[dict, str, int]:
    current_date = str(datetime.now().date())
    state = get_daily_state(bot_data)
    is_repeat = state["sent_date"] == current_date

    if not is_repeat:
        mode = roll_mode(bot_data)
        exact_minutes = roll_exact_time_minutes(bot_data)
        exact_time = parse_minutes_to_time(exact_minutes)
        bet_multiplier = roll_bet_multiplier()
        target_date = target_date_from(current_date)

        fog_sigma = 0
        display_delta = 0
        display_time = exact_time

        if mode == "fog":
            fog_sigma = random.randint(
                bot_data.get("fog_sigma_min", 10),
                bot_data["sigma"],
            )
            display_delta = 2 * fog_sigma
            display_time = build_fog_display(exact_minutes, display_delta)

        cancel_named_jobs(job_queue, ["fog_reveal", "king_deadline"])

        state = update_daily_state(
            bot_data,
            sent_date=current_date,
            target_date=target_date,
            mode=mode,
            exact_time_minutes=exact_minutes,
            exact_time=exact_time,
            display_time=display_time,
            bet_multiplier=bet_multiplier,
            chat_id=chat_id,
            fog_sigma=fog_sigma,
            display_delta=display_delta,
            revealed=False,
            king_started=False,
            king_deadline_iso=None,
            king_winner=None,
            pending_king_user_id=None,
            pending_king_until_iso=None,
            pending_king_password=None,
            schedule_kind="solo" if solo_player else "group",
            solo_player=solo_player,
            solo_checkin=False,
        )

        if mode == "fog":
            schedule_fog_reveal(job_queue, state)
    else:
        if solo_player and state.get("schedule_kind") != "solo":
            update_daily_state(
                bot_data,
                schedule_kind="solo",
                solo_player=solo_player,
                solo_checkin=False,
            )
            state = get_daily_state(bot_data)

    mode = state["mode"]
    exact_time = state["exact_time"]
    display_time = state["display_time"]
    exact_minutes = state["exact_time_minutes"]
    bet_multiplier = state["bet_multiplier"]
    use_solo = state.get("schedule_kind") == "solo" and state.get("solo_player")
    player = solo_player or (state.get("solo_player") if use_solo else None)

    if player:
        text = build_solo_announcement_text(
            solo_player=player,
            mode=mode,
            exact_time=exact_time,
            display_time=display_time,
            is_repeat=is_repeat,
            mean=bot_data["mean"],
            sigma=bot_data["sigma"],
            exact_minutes=exact_minutes,
            display_delta=state.get("display_delta", 0),
        )
    else:
        text = build_announcement_text(
            mode=mode,
            exact_time=exact_time,
            display_time=display_time,
            is_repeat=is_repeat,
            mean=bot_data["mean"],
            sigma=bot_data["sigma"],
            exact_minutes=exact_minutes,
            display_delta=state.get("display_delta", 0),
        )

    text += format_bet_suffix(bet_multiplier)
    return state, text, bet_multiplier


async def generate_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, text, _ = _roll_daily_schedule(
        context.bot_data,
        update.effective_chat.id,
        context.application.job_queue,
        solo_player=None,
    )
    print(f"{datetime.now()} - {update.effective_user.full_name} [{update.effective_user.id}]: {text}")
    await send_md(update, text)


async def solo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(text="Использование: /solo <ник>")
        return

    nickname = context.args[0]
    _, text, _ = _roll_daily_schedule(
        context.bot_data,
        update.effective_chat.id,
        context.application.job_queue,
        solo_player=nickname,
    )
    print(f"{datetime.now()} - solo {nickname} by {update.effective_user.full_name}")
    await send_md(update, text)


async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.bot_data
    state = get_daily_state(bot_data)
    today = str(datetime.now().date())

    if state.get("schedule_kind") != "solo" or not state.get("solo_player"):
        phrase = random.choice(CHECKIN_NOT_SOLO_PHRASES)
        await send_md(update, apply_phrase(phrase, {}))
        return

    if state["target_date"] != today:
        await update.effective_message.reply_text(
            text="Сегодня не день соло. /checkin в день дедлайна."
        )
        return

    player = state["solo_player"]
    player_bold = bold_md(player)

    if state.get("solo_checkin"):
        phrase = random.choice(CHECKIN_ALREADY_PHRASES)
        await send_md(update, apply_phrase(phrase, {"PLAYER": player_bold}))
        return

    if state["mode"] == "king" and not state.get("king_started"):
        phrase = random.choice(CHECKIN_KING_NOT_STARTED_PHRASES)
        await send_md(update, apply_phrase(phrase, {"PLAYER": player_bold}))
        return

    deadline = get_solo_checkin_deadline(state)
    if deadline is None:
        await update.effective_message.reply_text(text="Дедлайн ещё не определён.")
        return

    now = datetime.now()
    if now > deadline:
        phrase = random.choice(CHECKIN_LATE_PHRASES)
        await send_md(update, apply_phrase(phrase, {"PLAYER": player_bold}))
        return

    set_insurance_holder(bot_data, player)
    update_daily_state(bot_data, solo_checkin=True)

    phrase = random.choice(CHECKIN_SUCCESS_PHRASES)
    await send_md(update, apply_phrase(phrase, {"PLAYER": player_bold}))
    print(f"{datetime.now()} - checkin {player}, insurance granted")


async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.bot_data
    state = get_daily_state(bot_data)
    holder = state.get("insurance_holder")

    if not holder:
        phrase = random.choice(SAVE_NO_INSURANCE_PHRASES)
        await send_md(update, apply_phrase(phrase, {}))
        return

    user = update.effective_user
    extra = context.args[0] if context.args else None
    if not nickname_matches(user, holder, extra):
        phrase = random.choice(SAVE_NOT_OWNER_PHRASES)
        await send_md(update, apply_phrase(phrase, {"HOLDER": bold_md(holder)}))
        return

    clear_insurance(bot_data)
    holder_bold = bold_md(holder)

    success = random.random() < 0.5
    if success:
        phrase = random.choice(SAVE_SUCCESS_PHRASES)
    else:
        phrase = random.choice(SAVE_FAIL_PHRASES)

    await send_md(update, apply_phrase(phrase, {"HOLDER": holder_bold}))
    print(f"{datetime.now()} - save by {holder}, success={success}")


async def duel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            text="Использование: /duel <имя1> <имя2>"
        )
        return

    player1 = context.args[0]
    player2 = context.args[1]
    current_date = str(datetime.now().date())
    bot_data = context.bot_data

    state = get_daily_state(bot_data)
    is_repeat = (
        state.get("duel_date") == current_date
        and state.get("duel_player1") == player1
        and state.get("duel_player2") == player2
    )

    if is_repeat:
        phrase = random.choice(DUEL_REPEAT_PHRASES)
    else:
        phrase = random.choice(DUEL_PHRASES)
        update_daily_state(
            bot_data,
            duel_date=current_date,
            duel_player1=player1,
            duel_player2=player2,
        )
        bot_data["duel_date"] = current_date
        bot_data["duel_player1"] = player1
        bot_data["duel_player2"] = player2

    text = apply_phrase(
        phrase,
        {"PLAYER1": bold_md(player1), "PLAYER2": bold_md(player2)},
    )

    print(f"{datetime.now()} - duel: {player1} vs {player2} by {update.effective_user.full_name}")
    await send_md(update, text)


async def king_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_data = context.bot_data
    state = get_daily_state(bot_data)
    today = str(datetime.now().date())

    if state["mode"] != "king":
        await update.effective_message.reply_text(text="Сегодня не режим царя горы.")
        return

    if state["target_date"] != today:
        await update.effective_message.reply_text(
            text="Царь горы объявлен на другой день. Сначала /time."
        )
        return

    if state.get("king_started"):
        phrase = random.choice(KING_ALREADY_PHRASES)
        await send_md(update, apply_phrase(phrase, {}))
        return

    if state.get("pending_king_user_id") is not None:
        until_iso = state.get("pending_king_until_iso")
        if until_iso and datetime.fromisoformat(until_iso) > datetime.now():
            await update.effective_message.reply_text(text="Кто-то уже вызвал /king. Ждите.")
            return

    password = generate_password()
    timeout_sec = bot_data.get("king_media_timeout_sec", 180)
    timeout_min = max(1, timeout_sec // 60)
    until = datetime.now() + timedelta(seconds=timeout_sec)

    update_daily_state(
        bot_data,
        pending_king_user_id=update.effective_user.id,
        pending_king_until_iso=until.isoformat(),
        pending_king_password=password,
        chat_id=update.effective_chat.id,
    )

    phrase = random.choice(KING_PASSWORD_PHRASES)
    text = apply_phrase(
        phrase,
        {
            "PASSWORD": bold_md(password),
            "TIMEOUT": bold_md(str(timeout_min)),
        },
    )
    await send_md(update, text)


async def king_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or update.effective_user is None:
        return

    bot_data = context.bot_data
    state = get_daily_state(bot_data)
    today = str(datetime.now().date())

    if state["mode"] != "king" or state["target_date"] != today:
        return

    if state.get("king_started"):
        return

    pending_user = state.get("pending_king_user_id")
    if pending_user is None:
        return

    if update.effective_user.id != pending_user:
        return

    until_iso = state.get("pending_king_until_iso")
    if until_iso and datetime.fromisoformat(until_iso) < datetime.now():
        phrase = random.choice(KING_MEDIA_TIMEOUT_PHRASES)
        update_daily_state(
            bot_data,
            pending_king_user_id=None,
            pending_king_until_iso=None,
            pending_king_password=None,
        )
        await send_md(update, apply_phrase(phrase, {}))
        return

    if not state.get("pending_king_password"):
        return

    countdown = random.randint(
        bot_data.get("king_countdown_min", 5),
        bot_data.get("king_countdown_max", 30),
    )
    now = datetime.now()
    deadline = compute_king_deadline(now, countdown, bot_data["from"])
    deadline_str = deadline.strftime("%H:%M")
    winner = update.effective_user.full_name

    cancel_named_jobs(context.application.job_queue, ["king_deadline"])
    schedule_king_deadline(context.application.job_queue, update.effective_chat.id, deadline)

    update_daily_state(
        bot_data,
        king_started=True,
        king_deadline_iso=deadline.isoformat(),
        king_winner=winner,
        pending_king_user_id=None,
        pending_king_until_iso=None,
        pending_king_password=None,
        chat_id=update.effective_chat.id,
    )

    phrase = random.choice(KING_START_PHRASES)
    text = apply_phrase(
        phrase,
        {
            "WINNER": bold_md(winner),
            "COUNTDOWN": bold_md(str(countdown)),
            "DEADLINE": bold_md(deadline_str),
        },
    )
    print(f"{datetime.now()} - king started by {winner}, deadline {deadline_str}")
    await send_md(update, text)


async def print_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Команды:\n"
        "/time, /random — время на завтра (normal / туман / царь горы)\n"
        "/solo <ник> — как /time, но для одного; вовремя + /checkin = страховка\n"
        "/checkin — отметка в соло-режиме до дедлайна (страховка одному)\n"
        "/save — 50% отменить проигрыш (только владелец страховки, сгорает)\n"
        "/duel <имя1> <имя2> — дуэль: кто раньше — победил\n"
        "/king — царь горы: /king + фото или кружок\n"
        "/help — эта памятка\n"
        "/get_config — настройки и владелец страховки\n"
        "/set_config from 09:00 to 13:00 mean 11:00 sigma 45\n"
        "/reset — сброс расписания (страховка сохраняется)"
    )
    await update.effective_message.reply_text(text=text)


async def reset_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cancel_named_jobs(context.application.job_queue, ["fog_reveal", "king_deadline"])
    reset_daily_state(context.bot_data)
    context.bot_data["duel_date"] = "1970-01-01"
    context.bot_data["duel_player1"] = None
    context.bot_data["duel_player2"] = None
    text = "Время сброшено."
    print(f"{datetime.now()} - {update.effective_user.full_name} [{update.effective_user.id}]: {text}")
    await update.effective_message.reply_text(text=text)


async def get_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bd = context.bot_data
    state = get_daily_state(bd)
    holder = state.get("insurance_holder") or "нет"
    text = (
        f"Генерация от {parse_minutes_to_time(bd['from'])} "
        f"до {parse_minutes_to_time(bd['to'])}, среднее — "
        f"{parse_minutes_to_time(bd['mean'])}, σ — {bd['sigma']} мин.\n"
        f"Туман: {bd.get('mode_fog_chance', 0.1)}, "
        f"Царь: {bd.get('mode_king_chance', 0.05)}, "
        f"king countdown {bd.get('king_countdown_min')}-{bd.get('king_countdown_max')} мин.\n"
        f"Страховка: {holder}\n"
        f"Установил {bd['author']} в {bd['config_set_time']}"
    )
    print(f"{datetime.now()} - {update.effective_user.full_name} [{update.effective_user.id}]: {text}")
    await update.effective_message.reply_text(text=text)


async def set_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _, _from, _, _to, _, _mean, _, _sigma = context.args
    except ValueError:
        await update.effective_message.reply_text(
            text="Используйте /help для указания правильных аргументов."
        )
        return

    bot_data = context.bot_data
    bot_data["from"] = parse_time_to_minutes(_from)
    bot_data["to"] = parse_time_to_minutes(_to)
    bot_data["mean"] = parse_time_to_minutes(_mean)
    bot_data["sigma"] = int(_sigma)
    bot_data["author"] = update.effective_user.full_name
    bot_data["config_set_time"] = str(datetime.now())

    cancel_named_jobs(context.application.job_queue, ["fog_reveal", "king_deadline"])
    reset_daily_state(bot_data)

    cfg = {
        "from": _from,
        "to": _to,
        "mean": _mean,
        "sigma": _sigma,
        "author": update.effective_user.full_name,
        "config_set_time": str(datetime.now()),
    }
    for key, default in DEFAULT_MODE_CONFIG.items():
        cfg[key] = bot_data.get(key, default)

    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f)

    print(f"{datetime.now()} - {update.effective_user.full_name} [{update.effective_user.id}]: {cfg}")
    await update.effective_message.reply_text(text="Параметры установлены.")


def load_config_into_bot_data(bot_data: dict) -> None:
    _from = "9:00"
    _to = "13:00"
    _mean = "11:00"
    _sigma = "45"
    _author = "-"
    _config_set_time = "-"

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        _from = str(cfg.get("from", _from))
        _to = str(cfg.get("to", _to))
        _mean = str(cfg.get("mean", _mean))
        _sigma = str(cfg.get("sigma", _sigma))
        _author = cfg.get("author", _author)
        _config_set_time = cfg.get("config_set_time", _config_set_time)
        for key, default in DEFAULT_MODE_CONFIG.items():
            bot_data[key] = cfg.get(key, default)
    else:
        for key, default in DEFAULT_MODE_CONFIG.items():
            bot_data[key] = default

    bot_data["from"] = parse_time_to_minutes(_from)
    bot_data["to"] = parse_time_to_minutes(_to)
    bot_data["mean"] = parse_time_to_minutes(_mean)
    bot_data["sigma"] = int(_sigma)
    bot_data["author"] = _author
    bot_data["config_set_time"] = _config_set_time


class Bot:
    def __init__(self, token: str) -> None:
        self.application = Application.builder().token(token).build()
        load_config_into_bot_data(self.application.bot_data)

        state = load_daily_state()
        self.application.bot_data["daily_state"] = state
        self.application.bot_data["sent_date"] = state["sent_date"]
        self.application.bot_data["sent_time"] = state.get("display_time", state["exact_time"])
        self.application.bot_data["duel_date"] = state.get("duel_date", "1970-01-01")
        self.application.bot_data["duel_player1"] = state.get("duel_player1")
        self.application.bot_data["duel_player2"] = state.get("duel_player2")

        self.application.add_handler(CommandHandler(["time", "random"], generate_schedule))
        self.application.add_handler(CommandHandler("solo", solo_command))
        self.application.add_handler(CommandHandler("checkin", checkin_command))
        self.application.add_handler(CommandHandler("save", save_command))
        self.application.add_handler(CommandHandler("duel", duel_command))
        self.application.add_handler(CommandHandler("king", king_command))
        self.application.add_handler(
            MessageHandler(filters.PHOTO | filters.VIDEO_NOTE, king_media_handler)
        )
        self.application.add_handler(CommandHandler("help", print_help))
        self.application.add_handler(CommandHandler("get_config", get_config))
        self.application.add_handler(CommandHandler("set_config", set_config))
        self.application.add_handler(CommandHandler("reset", reset_time))

        for k, v in self.application.bot_data.items():
            if k != "daily_state":
                print(k, v)

    def run(self) -> None:
        restore_jobs(self.application)
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)
