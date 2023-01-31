import os
from bot import Bot

def main():
    token = os.getenv("RANDOMSCHEDULE_TG_KEY")
    if token is None:
        print("RANDOMSCHEDULE_TG_KEY is not set!")
        return

    print("Nu che narod, pognali?")
    bot = Bot(token)
    bot.run()

if __name__ == "__main__":
    main()
