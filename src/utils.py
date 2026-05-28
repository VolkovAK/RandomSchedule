import re


def parse_minutes_to_time(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}:{mins:02d}"


def parse_time_to_minutes(time_: str) -> int:
    hours, minutes = list(map(int, time_.split(":")))
    return hours * 60 + minutes


def escape_md(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\-])", r"\\\1", text)


def bold_md(text: str) -> str:
    return f"*{escape_md(text)}*"


def get_rarity(mean: int, sigma: int, result: int) -> str:
    if mean - sigma < result < mean + sigma:
        return ""
    if mean - sigma * 1.6 < result < mean + sigma * 1.6:
        return "\n\n🟦_Редкое время_🟦"
    if mean - sigma * 1.9 < result < mean + sigma * 1.9:
        return "\n\n🟪*Эпичное время*🟪"
    return "\n\n🟨*_Легендарное время\\!_*🟨"


def apply_phrase(phrase: str, replacements: dict[str, str]) -> str:
    text = phrase
    for key, value in replacements.items():
        text = text.replace(key, value)
    return escape_md(text)
