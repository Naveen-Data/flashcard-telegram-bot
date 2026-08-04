from datetime import datetime

from telegram.ext import ContextTypes

from studybot import db
from studybot.bot import utils


async def check_due_cards(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return
    if db.is_within_dnd(chat_id):
        return
    due_cards = db.list_due_cards(chat_id)
    if not due_cards:
        return
    due_ids = frozenset(c["id"] for c in due_cards)
    last_notified: frozenset = context.bot_data.get("last_notified_ids", frozenset())
    if not (due_ids - last_notified):
        return
    context.bot_data["last_notified_ids"] = due_ids
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📚 {len(due_cards)} card(s) due for review.\n/review to start.",
    )


async def daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return
    due_cards = db.list_due_cards(chat_id)
    stats = db.get_stats(chat_id)
    streak = db.get_streak_info(chat_id)
    if not due_cards:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🌅 Good morning! No cards due today.\n"
                 f"🔥 Streak: {streak['current']} day(s) | 📦 Total: {stats['total']} cards",
        )
        return
    tags = db.list_tags(chat_id)
    tag_lines = [f"  #{tag}: {due} due" for tag, _, due in tags if due > 0]
    msg = f"🌅 Good morning! {len(due_cards)} card(s) due today.\n🔥 Streak: {streak['current']} day(s)\n"
    if tag_lines:
        msg += "\n" + "\n".join(tag_lines) + "\n"
    msg += "\n/review to start."
    await context.bot.send_message(chat_id=chat_id, text=msg)


async def weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    if (datetime.utcnow() + utils.IST).weekday() != 6:
        return
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return
    stats = db.get_weekly_stats(chat_id)
    streak = db.get_streak_info(chat_id)
    bar = utils.progress_bar(stats["active_days"], 7)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📊 *Weekly Report*\n\n"
             f"📚 Cards reviewed: {stats['total']}\n"
             f"✅ Accuracy: {stats['accuracy']}%\n"
             f"📅 Active days: {bar} {stats['active_days']}/7\n"
             f"🔥 Current streak: {streak['current']} day(s)\n"
             f"🏆 Longest streak: {streak['longest']} day(s)",
        parse_mode="Markdown",
    )


async def pomodoro_done(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="⏰ Pomodoro done! Take a 5 minute break. 🧘",
    )
