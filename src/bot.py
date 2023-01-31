from telegram import Update
import telegram
from telegram.ext import Application, CommandHandler, ContextTypes
import yaml
import os
import random
from datetime import datetime


CONFIG_PATH = "config.yaml"


YOU_STUPID_PHRASES = [
    "Время? Ты снова спрашиваешь время?! Запомни раз и на завтра: TIME!",
    "Ну и что было не ясно? TIME!",
    "Я два раза повторять не буду! Повторю три: TIME, TIME, TIME",
    "За расписанием обращайтесь в деканат! А, стоп... TIME",
    "TIME...",
    "TIME...",
    "TIME...",
    "TIME...",
]


TOMORROW_PHRASES = [
    "Время на завтра: TIME",
    "Приходим в TIME",
    "Работа начинается не позже TIME",
    "А давайте в TIME?",
    "Приход наступит в TIME...",
    "Джентельмены! Не задерживайтесь, ждём вас в TIME!",
    "Товарищ, помни! TIME - время для трудового подвига!",
    "Чтобы код был у меня на столе до TIME!",
    "TIME - и точка"
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
    "TIME",
]

async def generate_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_date = str(datetime.now().date())
    if context.bot_data["sent_date"] != current_date:
        rand_time = int(random.gauss(context.bot_data["mean"], context.bot_data["sigma"]))
        if rand_time < context.bot_data["from"]:
            rand_time = context.bot_data["from"]
        if rand_time > context.bot_data["to"]:
            rand_time = context.bot_data["to"]
        rand_time = parse_minutes_to_time(rand_time)

        phrase = random.choice(TOMORROW_PHRASES)
        text = phrase.replace("TIME", f"*{rand_time}*").replace("!", "\!").replace(".", "\.")
        context.bot_data["sent_date"] = current_date
        context.bot_data["sent_time"] = rand_time
    else:
        phrase = random.choice(YOU_STUPID_PHRASES)
        rand_time = context.bot_data["sent_time"]
        text = phrase.replace("TIME", f"*{rand_time}*").replace("!", "\!").replace(".", "\.")

    await update.effective_message.reply_text(text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)


async def print_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Команды:\n/time, /random - генерация времени на следующий день\n"
        "/help - печать этой памятки\n"
        "/get_config - получить настройки\n"
        "/set_config from 09:00 to 13:00 mean 240 sigma 45 - установить настройки"
    )
    await update.effective_message.reply_text(text=text)


async def get_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        f"Генерация производится от {parse_minutes_to_time(context.bot_data['from'])} "
        f"до {parse_minutes_to_time(context.bot_data['to'])}, среднее - "
        f"{parse_minutes_to_time(context.bot_data['mean'])}, "
        f"среднеквадратичное отклонение (в минутах) - {context.bot_data['sigma']}."
    )
    await update.effective_message.reply_text(text=text)

async def set_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _, _from, _, _to, _, _mean, _, _sigma = context.args
    except:
        await update.effective_message.reply_text(text="Используйте /help для указания правильных аргументов.")
        return
    context.bot_data["from"] = parse_time_to_minutes(_from)
    context.bot_data["to"] = parse_time_to_minutes(_to)
    context.bot_data["mean"] = parse_time_to_minutes(_mean)
    context.bot_data["sigma"] = int(_sigma)
    context.bot_data["sent_date"] = "1970-01-01"
    context.bot_data["sent_time"] = "00:00"

    cfg = dict()
    cfg["from"] = _from
    cfg["to"] = _to
    cfg["mean"] = _mean
    cfg["sigma"] = _sigma
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f)

    await update.effective_message.reply_text(text="Параметры установлены.")

def parse_minutes_to_time(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}:{mins:02d}"

def parse_time_to_minutes(time_: str) -> int:
    hours, minutes = list(map(int, time_.split(":")))
    return hours * 60 + minutes

class Bot:
    def __init__(self, token: str) -> None:

        _from = "9:00"
        _to = "13:00"
        _mean = "11:00"
        _sigma = "45"
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                cfg = yaml.safe_load(f)
            _from = cfg["from"]
            _to = cfg["to"]
            _mean = cfg["mean"]
            _sigma = cfg["sigma"]
            

        self.application = Application.builder().token(token).build()
        self.application.add_handler(CommandHandler(["time", "random"], generate_schedule))
        self.application.add_handler(CommandHandler("help", print_help))
        self.application.add_handler(CommandHandler("get_config", get_config))
        self.application.add_handler(CommandHandler("set_config", set_config))
        self.application.bot_data["from"] = parse_time_to_minutes(_from)
        self.application.bot_data["to"] = parse_time_to_minutes(_to)
        self.application.bot_data["mean"] = parse_time_to_minutes(_mean)
        self.application.bot_data["sigma"] = int(_sigma)
        self.application.bot_data["sent_date"] = "1970-01-01"
        self.application.bot_data["sent_time"] = "00:00"
        for k, v in self.application.bot_data.items():
            print(k, v )


    def run(self):
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

        
