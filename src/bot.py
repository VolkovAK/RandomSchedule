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
    format_expulsion_debuff_suffix,
    generate_password,
    get_solo_checkin_deadline,
    nickname_matches,
    resolve_user_nickname,
    solo_missed_checkin_for_debuff,
    roll_bet_multiplier,
    roll_exact_time_minutes,
    roll_fog_reveal_minutes,
    roll_fog_sigma,
    roll_mode,
    target_date_from,
)
from phrases import (
    CHECKIN_ALREADY_PHRASES,
    CHECKIN_KING_NOT_STARTED_PHRASES,
    CHECKIN_LATE_PHRASES,
    CHECKIN_NOT_OWNER_PHRASES,
    CHECKIN_NOT_SOLO_PHRASES,
    CHECKIN_SUCCESS_PHRASES,
    DUEL_PHRASES,
    DUEL_REPEAT_PHRASES,
    KING_ALREADY_PHRASES,
    KING_MEDIA_TIMEOUT_PHRASES,
    KING_PASSWORD_PHRASES,
    KING_START_PHRASES,
    MODE_LOCKED_DUEL_OTHER_PHRASES,
    MODE_LOCKED_PHRASES,
    MODE_LOCKED_SOLO_OTHER_PHRASES,
    SAVE_FAIL_PHRASES,
    SAVE_NO_INSURANCE_PHRASES,
    SAVE_NOT_OWNER_PHRASES,
    SAVE_SUCCESS_PHRASES,
)
from state import (
    cancel_named_jobs,
    clear_expulsion_debuff,
    clear_insurance,
    get_daily_state,
    get_today_activity,
    load_daily_state,
    reset_daily_state,
    set_expulsion_debuff,
    set_insurance_holder,
    update_daily_state,
)
from utils import (
    apply_phrase,
    bold_md,
    mode_chance_percents,
    parse_minutes_to_time,
    parse_time_to_minutes,
)

CONFIG_PATH = "config.yaml"

DEFAULT_MODE_CONFIG = {
    "mode_fog_chance": 0.30,
    "mode_king_chance": 0.20,
    "king_countdown_min": 5,
    "king_countdown_max": 30,
    "king_media_timeout_sec": 180,
    "fog_sigma_min": 10,
    "fog_sigma_max": 25,
}

ACTIVITY_LABELS = {
    "time": "/time",
    "solo": "/solo",
    "duel": "/duel",
}


async def send_md(update: Update, text: str) -> None:
    await update.effective_message.reply_text(
        text=text,
        parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
    )


async def send_mode_locked(update: Update, active: str, requested: str) -> None:
    phrase = random.choice(MODE_LOCKED_PHRASES)
    await send_md(
        update,
        apply_phrase(
            phrase,
            {
                "ACTIVE": bold_md(ACTIVITY_LABELS.get(active, active)),
                "REQUESTED": bold_md(ACTIVITY_LABELS.get(requested, requested)),
            },
        ),
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

    is_solo = solo_player is not None

    if not is_repeat:
        missed = solo_missed_checkin_for_debuff(state, current_date)
        if missed:
            set_expulsion_debuff(bot_data, missed)

        mode = "normal" if is_solo else roll_mode(bot_data)
        exact_minutes = roll_exact_time_minutes(bot_data)
        exact_time = parse_minutes_to_time(exact_minutes)
        bet_multiplier = 1 if is_solo else roll_bet_multiplier()
        target_date = target_date_from(current_date)

        fog_sigma = 0
        display_delta = 0
        display_time = exact_time

        fog_center_minutes = exact_minutes
        fog_center = exact_time

        if mode == "fog":
            fog_sigma = roll_fog_sigma(bot_data)
            display_delta = 2 * fog_sigma
            exact_minutes = roll_fog_reveal_minutes(fog_center_minutes, fog_sigma, bot_data)
            exact_time = parse_minutes_to_time(exact_minutes)
            display_time = build_fog_display(fog_center_minutes, display_delta)

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
            fog_center_minutes=fog_center_minutes,
            fog_center=fog_center,
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
            exact_time=exact_time,
            is_repeat=is_repeat,
            mean=bot_data["mean"],
            sigma=bot_data["sigma"],
            exact_minutes=exact_minutes,
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
            fog_center_time=state.get("fog_center"),
        )

    if not player:
        text += format_bet_suffix(bet_multiplier)
        debuff_player = get_daily_state(bot_data).get("expulsion_debuff")
        if mode == "normal" and debuff_player:
            text += format_expulsion_debuff_suffix(debuff_player)
    return state, text, bet_multiplier


async def generate_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_date = str(datetime.now().date())
    state = get_daily_state(context.bot_data)
    activity = get_today_activity(state, current_date)
    if activity in ("solo", "duel"):
        await send_mode_locked(update, activity, "time")
        return

    _, text, _ = _roll_daily_schedule(
        context.bot_data,
        update.effective_chat.id,
        context.application.job_queue,
        solo_player=None,
    )
    print(f"{datetime.now()} - {update.effective_user.full_name} [{update.effective_user.id}]: {text}")
    await send_md(update, text)


async def solo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    nickname = resolve_user_nickname(user)
    if not nickname:
        await update.effective_message.reply_text(
            text="Укажите @username в Telegram или задайте имя в профиле."
        )
        return

    current_date = str(datetime.now().date())
    state = get_daily_state(context.bot_data)
    activity = get_today_activity(state, current_date)

    if activity == "time":
        await send_mode_locked(update, activity, "solo")
        return
    if activity == "duel":
        await send_mode_locked(update, activity, "solo")
        return
    if activity == "solo" and not nickname_matches(user, state["solo_player"]):
        phrase = random.choice(MODE_LOCKED_SOLO_OTHER_PHRASES)
        await send_md(
            update,
            apply_phrase(phrase, {"PLAYER": bold_md(state["solo_player"])}),
        )
        return

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
            text="/checkin — в день, когда вызывал (/solo). Сегодня не тот день."
        )
        return

    player = state["solo_player"]
    player_bold = bold_md(player)

    if not nickname_matches(update.effective_user, player):
        phrase = random.choice(CHECKIN_NOT_OWNER_PHRASES)
        await send_md(update, apply_phrase(phrase, {"PLAYER": player_bold}))
        return

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
        await update.effective_message.reply_text(text="Дедлайн ещё не назначен.")
        return

    now = datetime.now()
    if now > deadline:
        set_expulsion_debuff(bot_data, player)
        phrase = random.choice(CHECKIN_LATE_PHRASES)
        await send_md(update, apply_phrase(phrase, {"PLAYER": player_bold}))
        print(f"{datetime.now()} - checkin late {player}, expulsion debuff")
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


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_daily_state(context.bot_data)
    if not state.get("expulsion_debuff"):
        await update.effective_message.reply_text(
            text="Дебаффа «под отчисление» нет."
        )
        return

    player = state["expulsion_debuff"]
    clear_expulsion_debuff(context.bot_data)
    await update.effective_message.reply_text(
        text=f"Дебафф «под отчисление» снят с {player}."
    )
    print(f"{datetime.now()} - restore expulsion debuff for {player}")


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
    activity = get_today_activity(state, current_date)
    is_repeat = (
        activity == "duel"
        and state.get("duel_player1") == player1
        and state.get("duel_player2") == player2
    )

    if activity in ("time", "solo"):
        await send_mode_locked(update, activity, "duel")
        return

    if activity == "duel" and not is_repeat:
        phrase = random.choice(MODE_LOCKED_DUEL_OTHER_PHRASES)
        await send_md(
            update,
            apply_phrase(
                phrase,
                {
                    "PLAYER1": bold_md(state["duel_player1"]),
                    "PLAYER2": bold_md(state["duel_player2"]),
                },
            ),
        )
        return

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
        await update.effective_message.reply_text(text="Сегодня не царь горы. Сначала /time.")
        return

    if state["target_date"] != today:
        await update.effective_message.reply_text(
            text="Царь горы — на другой день. Ждите вызова."
        )
        return

    if state.get("king_started"):
        phrase = random.choice(KING_ALREADY_PHRASES)
        await send_md(update, apply_phrase(phrase, {}))
        return

    if state.get("pending_king_user_id") is not None:
        until_iso = state.get("pending_king_until_iso")
        if until_iso and datetime.fromisoformat(until_iso) > datetime.now():
            await update.effective_message.reply_text(text="/king уже у другого. Ждите.")
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


HELP_TEXT = """ОБЪЯВЛЕНИЕ В ДЕКАНАТЕ

━━━ Один режим в день ━━━
За день можно выбрать только одно: /time, /solo или /duel.
Повтор той же команды в тот же день — Декан повторит уже сказанное.

━━━ /time и /random ━━━
Декан объявляет правила на ЗАВТРА.

При первом вызове крутится режим + ставка (см. ниже).

РЕЖИМЫ ДНЯ:

▸ Обычный (50% при дефолте в /get_config)
  Декан называет точное время прихода.
  Явиться к этому часу. Позже — опоздание.

▸ Туман войны (шанс в /get_config, по умолчанию 30%)
  Декан даёт ориентир: ~центр ± 2σ тумана (σ своя, из config).
  Фактическое время — gauss(центр, σ).
  Обрезка только по from/to. Декан сам назовёт точное время.
  Опоздание — с его сообщения.

▸ Царь горы (шанс в /get_config, по умолчанию 20%)
  Точного времени заранее нет.
  Первый в аудитории пишет /king — Декан выдаёт пароль.
  Фото с паролем или кружок (~3 мин).
  Отсчёт 5–30 мин, дедлайн не раньше 9:00 (from в конфиге).
  Декан объявит конец ожидания — опоздание после этого.

СТАВКА (только /time):
80% ×1 | 15% ×2 | 5% ×3

━━━ /solo ━━━
Декан вызывает вас (ник из профиля Telegram) — только точное время,
без тумана войны, царя горы и ставки.

━━━ /checkin ━━━
ТОЛЬКО после /solo, только автор соло. Без /solo Декан /checkin не принимает.
В день дедлайна, вовремя → страховка игроку из /solo.
Опоздал или не отметился → дебафф «под отчисление»: при следующем
обычном /time в конце: «@ник - на полчаса раньше!».

━━━ /restore ━━━
Единственный способ снять дебафф «под отчисление».

━━━ СТРАХОВКА ━━━
• Одна на всех — владелец в /get_config
• Новый соло-чемпион забирает у прежнего
• /save — только владелец, 50% отменить проигрыш; только в обычном режиме дня
• После /save страховка сгорает

━━━ /duel <имя1> <имя2> ━━━
Декан объявляет дуэль. Кто раньше на месте — победил.

━━━ /king ━━━
Только «царь горы», только в день дедлайна.
См. блок выше.

━━━ Служебное ━━━
/get_config — настройки + страховка
/set_config from 09:00 to 13:00 mean 11:00 sigma 45
/reset — сброс расписания (страховка остаётся)
/help — эта памятка"""


async def print_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(text=HELP_TEXT)


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
    debuff = state.get("expulsion_debuff") or "нет"
    fog_pct, king_pct, normal_pct = mode_chance_percents(bd)
    text = (
        f"Генерация от {parse_minutes_to_time(bd['from'])} "
        f"до {parse_minutes_to_time(bd['to'])}, среднее — "
        f"{parse_minutes_to_time(bd['mean'])}, σ — {bd['sigma']} мин.\n"
        f"Туман войны: {fog_pct}%, σ тумана {bd.get('fog_sigma_min')}-{bd.get('fog_sigma_max')} мин, "
        f"Царь горы: {king_pct}%, Обычный: {normal_pct}%, "
        f"отсчёт царя горы {bd.get('king_countdown_min')}-{bd.get('king_countdown_max')} мин.\n"
        f"Страховка: {holder}\n"
        f"Под отчисление: {debuff}\n"
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
        self.application.add_handler(CommandHandler("restore", restore_command))
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
