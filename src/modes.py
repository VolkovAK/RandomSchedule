import random
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from phrases import (
    FOG_ANNOUNCE_PHRASES,
    FOG_REPEAT_PHRASES,
    KING_ANNOUNCE_PHRASES,
    KING_REPEAT_PHRASES,
    SOLO_ANNOUNCE_PHRASES,
    SOLO_REPEAT_PHRASES,
    TOMORROW_PHRASES,
    YOU_STUPID_PHRASES,
)
from utils import apply_phrase, bold_md, escape_md, get_rarity, parse_minutes_to_time


def roll_bet_multiplier() -> int:
    r = random.random()
    if r < 0.80:
        return 1
    if r < 0.95:
        return 2
    return 3


def format_bet_suffix(multiplier: int) -> str:
    if multiplier == 1:
        return ""
    return f"\n\nСтавка: x{multiplier}"


def roll_mode(bot_data: dict) -> str:
    king_chance = bot_data.get("mode_king_chance", 0.20)
    fog_chance = bot_data.get("mode_fog_chance", 0.30)
    r = random.random()
    if r < king_chance:
        return "king"
    if r < king_chance + fog_chance:
        return "fog"
    return "normal"


def roll_exact_time_minutes(bot_data: dict) -> int:
    sigma = bot_data["sigma"]
    mean = bot_data["mean"]
    rand_time = int(random.gauss(mean, sigma))
    if rand_time < bot_data["from"]:
        rand_time = bot_data["from"]
    if rand_time > bot_data["to"]:
        rand_time = bot_data["to"]
    return rand_time


def roll_fog_sigma(bot_data: dict) -> int:
    fog_lo = bot_data.get("fog_sigma_min", 10)
    fog_hi = bot_data.get("fog_sigma_max", 25)
    return random.randint(fog_lo, max(fog_lo, fog_hi))


def roll_fog_reveal_minutes(center_minutes: int, fog_sigma: int, bot_data: dict) -> int:
    """Фактическое время: gauss(центр, fog_sigma), без обрезки по ±2σ."""
    t = int(random.gauss(center_minutes, fog_sigma))
    if t < bot_data["from"]:
        t = bot_data["from"]
    if t > bot_data["to"]:
        t = bot_data["to"]
    return t


def target_date_from(sent_date: str) -> str:
    d = datetime.strptime(sent_date, "%Y-%m-%d").date()
    return str(d + timedelta(days=1))


def build_fog_display(center_minutes: int, display_delta: int) -> str:
    center_str = parse_minutes_to_time(center_minutes)
    return f"~{center_str} ± {display_delta} мин"


def build_announcement_text(
    mode: str,
    exact_time: str,
    display_time: str,
    is_repeat: bool,
    mean: int,
    sigma: int,
    exact_minutes: int,
    display_delta: int = 0,
    fog_center_time: Optional[str] = None,
) -> str:
    time_bold = bold_md(display_time if mode != "normal" else exact_time)

    if is_repeat:
        if mode == "fog":
            phrase = random.choice(FOG_REPEAT_PHRASES)
        elif mode == "king":
            phrase = random.choice(KING_REPEAT_PHRASES)
        else:
            phrase = random.choice(YOU_STUPID_PHRASES)
        return apply_phrase(phrase, {"TIME": time_bold})

    if mode == "fog":
        phrase = random.choice(FOG_ANNOUNCE_PHRASES)
        approx_str = fog_center_time or exact_time
        return apply_phrase(
            phrase,
            {"APPROX": bold_md(approx_str), "DELTA": bold_md(str(display_delta))},
        )

    if mode == "king":
        phrase = random.choice(KING_ANNOUNCE_PHRASES)
        return apply_phrase(phrase, {})

    phrase = random.choice(TOMORROW_PHRASES)
    text = apply_phrase(phrase, {"TIME": time_bold})
    text += get_rarity(mean, sigma, exact_minutes)
    return text


def build_solo_announcement_text(
    solo_player: str,
    exact_time: str,
    is_repeat: bool,
    mean: int,
    sigma: int,
    exact_minutes: int,
) -> str:
    time_bold = bold_md(exact_time)

    if is_repeat:
        phrase = random.choice(SOLO_REPEAT_PHRASES)
        return apply_phrase(phrase, {"PLAYER": bold_md(solo_player), "TIME": time_bold})

    phrase = random.choice(SOLO_ANNOUNCE_PHRASES)
    text = apply_phrase(
        phrase,
        {"PLAYER": bold_md(solo_player), "TIME": time_bold},
    )
    text += get_rarity(mean, sigma, exact_minutes)
    return text


def generate_password(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def compute_king_deadline(now: datetime, countdown_min: int, from_minutes: int) -> datetime:
    raw = now + timedelta(minutes=countdown_min)
    from_h, from_m = divmod(from_minutes, 60)
    earliest = now.replace(hour=from_h, minute=from_m, second=0, microsecond=0)
    return max(raw, earliest)


def solo_missed_checkin_for_debuff(state: dict, current_date: str) -> Optional[str]:
    if state.get("schedule_kind") != "solo" or not state.get("solo_player"):
        return None
    if state.get("solo_checkin"):
        return None
    target = state.get("target_date")
    if not target or target >= current_date:
        return None
    return state["solo_player"]


def format_expulsion_debuff_suffix(nickname: str) -> str:
    nick = nickname if nickname.startswith("@") else f"@{nickname}"
    return f"\n\n{escape_md(nick + ' - на полчаса раньше!')}"


def get_solo_checkin_deadline(state: dict) -> Optional[datetime]:
    target_date = state.get("target_date")
    if not target_date:
        return None

    day = datetime.strptime(target_date, "%Y-%m-%d").date()
    mode = state.get("mode", "normal")

    if mode == "king":
        if not state.get("king_started") or not state.get("king_deadline_iso"):
            return None
        return datetime.fromisoformat(state["king_deadline_iso"])

    exact_time = state.get("exact_time", "00:00")
    hours, minutes = map(int, exact_time.split(":"))
    return datetime.combine(day, datetime.min.time()).replace(hour=hours, minute=minutes)


def nickname_matches(user, nickname: str, extra_arg: Optional[str] = None) -> bool:
    nick = nickname.lower().lstrip("@")
    if extra_arg and extra_arg.lower().lstrip("@") == nick:
        return True
    if user.username and user.username.lower() == nick:
        return True
    if user.first_name and user.first_name.lower() == nick:
        return True
    if user.full_name and nick in user.full_name.lower():
        return True
    return False
