import random
from datetime import datetime, timedelta

from phrases import FOG_REVEAL_PHRASES, KING_DEADLINE_PHRASES
from state import save_daily_state, update_daily_state
from timeutil import at_time, now, parse_iso
from utils import apply_phrase, bold_md


async def reveal_fog_time(context) -> None:
    data = context.job.data
    chat_id = data["chat_id"]
    exact_time = data["exact_time"]

    phrase = random.choice(FOG_REVEAL_PHRASES)
    text = apply_phrase(phrase, {"TIME": bold_md(exact_time)})

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="MarkdownV2",
    )

    bot_data = context.application.bot_data
    state = bot_data.get("daily_state", {})
    if state.get("target_date") == data.get("target_date"):
        update_daily_state(bot_data, revealed=True, display_time=exact_time)
    print(f"{now()} - fog reveal: {exact_time} -> chat {chat_id}")


async def king_deadline(context) -> None:
    data = context.job.data
    chat_id = data["chat_id"]

    phrase = random.choice(KING_DEADLINE_PHRASES)
    text = apply_phrase(phrase, {})

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="MarkdownV2",
    )
    print(f"{now()} - king deadline -> chat {chat_id}")


def schedule_fog_reveal(job_queue, state: dict) -> None:
    if job_queue is None or state.get("mode") != "fog" or state.get("revealed"):
        return

    run_at = at_time(state["target_date"], state["exact_time"])
    when = run_at if run_at > now() else now() + timedelta(seconds=1)

    job_queue.run_once(
        reveal_fog_time,
        when,
        data={
            "chat_id": state["chat_id"],
            "exact_time": state["exact_time"],
            "target_date": state["target_date"],
        },
        name="fog_reveal",
    )


def schedule_king_deadline(job_queue, chat_id: int, deadline: datetime) -> None:
    if job_queue is None:
        return
    if deadline <= now():
        return

    job_queue.run_once(
        king_deadline,
        deadline,
        data={"chat_id": chat_id},
        name="king_deadline",
    )


def restore_jobs(application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        return

    from state import cancel_named_jobs, load_daily_state

    cancel_named_jobs(job_queue, ["fog_reveal", "king_deadline"])

    state = load_daily_state()
    if state.get("chat_id") is None:
        return

    if state.get("mode") == "fog" and not state.get("revealed"):
        schedule_fog_reveal(job_queue, state)

    if state.get("mode") == "king" and state.get("king_started") and state.get("king_deadline_iso"):
        deadline = parse_iso(state["king_deadline_iso"])
        if deadline > now():
            schedule_king_deadline(job_queue, state["chat_id"], deadline)

    pending_until = state.get("pending_king_until_iso")
    if pending_until:
        until = parse_iso(pending_until)
        if until <= now():
            state["pending_king_user_id"] = None
            state["pending_king_until_iso"] = None
            save_daily_state(state)
