from bot import Bot

def main():
    with open("/run/secrets/tg_key") as f:
        token = f.read().strip()
    if token == "":
        print("tg_key is not set!")
        return

    print("Nu che narod, pognali?")
    bot = Bot(token)
    bot.run()

if __name__ == "__main__":
    main()
