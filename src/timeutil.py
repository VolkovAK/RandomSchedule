from datetime import date, datetime, time
from zoneinfo import ZoneInfo

BOT_TZ = ZoneInfo("Europe/Moscow")


def now() -> datetime:
    return datetime.now(BOT_TZ)


def today_str() -> str:
    return str(now().date())


def at_time(date_str: str, time_str: str) -> datetime:
    day = date.fromisoformat(date_str)
    hours, minutes = map(int, time_str.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=BOT_TZ)


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BOT_TZ)
    return dt
