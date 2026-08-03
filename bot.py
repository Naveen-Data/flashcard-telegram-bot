import logging
import os
import re
import sys
from datetime import datetime, timedelta, time as dtime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"#(\w+)")
_IST = timedelta(hours=5, minutes=30)
_TG_MAX = 4096


def _clip(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _ist_to_utc(hour: int, minute: int) -> tuple[int, int]:
    total = (hour * 60 + minute - 330) % (24 * 60)
    return total // 60, total % 60


def _progress_bar(current: int, total: int, width: int = 10) -> str:
    if total == 0:
        return ""
    filled = round(current / total * width)
    return "█" * filled + "░" * (width - filled)


def _build_due_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Reveal", callback_data=f"reveal:{card_id}")],
        [
            InlineKeyboardButton("⏰ 1h", callback_data=f"snooze:1h:{card_id}"),
            InlineKeyboardButton("🌙 Tonight", callback_data=f"snooze:tonight:{card_id}"),
            InlineKeyboardButton("📅 Tomorrow", callback_data=f"snooze:tomorrow:{card_id}"),
        ],
    ])


def _build_answer_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 Again", callback_data=f"ans:1:{card_id}"),
        InlineKeyboardButton("🟠 Hard", callback_data=f"ans:3:{card_id}"),
        InlineKeyboardButton("🟢 Good", callback_data=f"ans:4:{card_id}"),
        InlineKeyboardButton("🔵 Easy", callback_data=f"ans:5:{card_id}"),
    ]])


def _snooze_delta(snooze_type: str) -> timedelta:
    now_ist = datetime.utcnow() + _IST
    if snooze_type == "1h":
        return timedelta(hours=1)
    if snooze_type == "tonight":
        target = now_ist.replace(hour=20, minute=0, second=0, microsecond=0)
        if now_ist.hour >= 19:
            target += timedelta(days=1)
        delta = target - now_ist
        return delta if delta.total_seconds() > 0 else timedelta(hours=1)
    if snooze_type == "tomorrow":
        target = (now_ist + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return target - now_ist
    return timedelta(hours=1)


def _pop_from_queue(context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    """Remove first occurrence of card_id from queue regardless of position."""
    queue: list[int] = context.chat_data.get("review_queue", [])
    try:
        idx = queue.index(card_id)
        context.chat_data["review_queue"] = queue[:idx] + queue[idx + 1:]
    except ValueError:
        pass


async def _send_next_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    queue: list[int] = context.chat_data.get("review_queue", [])
    if not queue:
        session = context.chat_data.pop("session", None)
        if session and session.get("reviewed", 0) > 0:
            reviewed = session["reviewed"]
            correct = session.get("correct", 0)
            accuracy = round(correct / reviewed * 100)
            elapsed = int((datetime.utcnow() - session["start"]).total_seconds() / 60)
            elapsed_str = f" • {elapsed}m" if elapsed > 0 else ""
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉 Session complete!\n📊 {reviewed} cards • {accuracy}% accuracy{elapsed_str}",
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text="🎉 All done! Great work.")
        return
    card_id = queue[0]
    card = db.get_card(card_id)
    if card is None:
        context.chat_data["review_queue"] = queue[1:]
        await _send_next_card(context, chat_id)
        return
    session = context.chat_data.get("session", {})
    reviewed = session.get("reviewed", 0)
    total = session.get("total", len(queue))
    bar = f"\n{_progress_bar(reviewed, total)} {reviewed}/{total}" if total else ""
    tags_str = f"\n🏷 {card['tags']}" if card.get("tags") else ""
    # Plain text for card content — avoids Markdown parse errors on special chars
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📚 {_clip(card['question'], 500)}{tags_str}{bar}",
        reply_markup=_build_due_keyboard(card_id),
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db.set_registered_chat_id(chat_id)
    await update.message.reply_text(
        "✅ Registered! I'll notify you when cards are due.\n\n"
        "Add a card:\n`add: question / answer`\n"
        "With tags:\n`add: question / answer #python #math`\n\n"
        "/help — show all commands",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Study Buddy Commands*\n\n"
        "*Cards*\n"
        "`add: question / answer` — add a card\n"
        "`add: question / answer #tag` — add with tags\n\n"
        "*Review*\n"
        "/review — review due cards\n"
        "/review `tag` — review due cards for a tag\n"
        "/review all — review all cards\n"
        "/review `tag` all — review all cards for a tag\n"
        "/tags — list all tags\n"
        "/search `keyword` — search cards\n\n"
        "*Progress*\n"
        "/streak — view your streak\n"
        "/goal `N` — set daily card goal\n\n"
        "*Settings*\n"
        "/setdigest — show digest time\n"
        "/setdigest `HH:MM` — set daily digest time (IST)\n\n"
        "*Focus*\n"
        "/pomodoro — 25 min focus timer\n"
        "/pomodoro `N` — custom timer in minutes",
        parse_mode="Markdown",
    )


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = [a.lstrip("#") for a in (context.args or [])]
    review_all = "all" in args
    tag = next((a for a in args if a != "all"), None)
    due_cards = db.list_all_cards(chat_id, tag=tag) if review_all else db.list_due_cards(chat_id, tag=tag)
    if not due_cards:
        suffix = f" for #{tag}" if tag else ""
        await update.message.reply_text(f"No cards due{suffix}! 🎉")
        return
    context.chat_data["review_queue"] = [c["id"] for c in due_cards]
    context.chat_data["session"] = {
        "start": datetime.utcnow(),
        "total": len(due_cards),
        "reviewed": 0,
        "correct": 0,
    }
    context.bot_data.pop("last_notified_ids", None)
    suffix = f" for #{tag}" if tag else ""
    await update.message.reply_text(f"Starting review{suffix}: {len(due_cards)} card(s).")
    await _send_next_card(context, chat_id)


async def tags_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    tags = db.list_tags(chat_id)
    if not tags:
        await update.message.reply_text(
            "No tags yet. Add cards with #tags:\n`add: question / answer #topic`",
            parse_mode="Markdown",
        )
        return
    lines = ["📂 Your tags:\n"]
    for tag, total, due in tags:
        due_str = f" ({due} due)" if due > 0 else ""
        lines.append(f"  #{tag} — {total} card{'s' if total != 1 else ''}{due_str}")
    await update.message.reply_text("\n".join(lines))


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /search keyword")
        return
    keyword = " ".join(context.args)
    results = db.search_cards(chat_id, keyword)
    if not results:
        await update.message.reply_text(f'No cards found for "{keyword}".')
        return
    lines = [f'🔍 {len(results)} result(s) for "{keyword}":\n']
    for card in results[:10]:
        tags_str = f" 🏷 {card['tags']}" if card.get("tags") else ""
        lines.append(f"• {_clip(card['question'], 120)}{tags_str}\n  → {_clip(card['answer'], 200)}")
    if len(results) > 10:
        lines.append(f"\n…and {len(results) - 10} more.")
    await update.message.reply_text("\n".join(lines)[:_TG_MAX])


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    info = db.get_streak_info(chat_id)
    goal = int(db.get_setting(chat_id, "daily_goal") or "0")
    today_count_str = ""
    if goal > 0:
        weekly = db.get_weekly_stats(chat_id)
        today_ist = (datetime.utcnow() + _IST).date().isoformat()
        today_reviewed = weekly["by_day"].get(today_ist, 0)
        bar = _progress_bar(today_reviewed, goal)
        today_count_str = f"\n\n🎯 Today: {bar} {today_reviewed}/{goal}"
    current = info["current"]
    longest = info["longest"]
    flame = "🔥" if current > 0 else "💤"
    await update.message.reply_text(
        f"{flame} *Streak: {current} day{'s' if current != 1 else ''}*\n"
        f"🏆 Longest: {longest} day{'s' if longest != 1 else ''}"
        f"{today_count_str}",
        parse_mode="Markdown",
    )


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        current = db.get_setting(chat_id, "daily_goal") or "not set"
        await update.message.reply_text(f"Current goal: {current} cards/day\nUsage: /goal 10")
        return
    goal = int(context.args[0])
    db.set_daily_goal(chat_id, goal)
    await update.message.reply_text(f"✅ Daily goal set to {goal} cards.")


async def setdigest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args:
        saved = db.get_global_setting("digest_time_ist") or "13:00"
        await update.message.reply_text(f"Daily digest is at {saved} IST.\nChange: /setdigest 14:00")
        return
    time_str = context.args[0]
    try:
        h, m = map(int, time_str.split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        await update.message.reply_text("❌ Format: /setdigest HH:MM (e.g. 14:00)")
        return
    db.set_global_setting("digest_time_ist", f"{h:02d}:{m:02d}")
    h_utc, m_utc = _ist_to_utc(h, m)
    for job in context.job_queue.get_jobs_by_name("daily_digest"):
        job.schedule_removal()
    context.job_queue.run_daily(
        daily_digest,
        time=dtime(hour=h_utc, minute=m_utc, tzinfo=timezone.utc),
        name="daily_digest",
    )
    await update.message.reply_text(f"✅ Daily digest set to {h:02d}:{m:02d} IST.")


async def pomodoro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    minutes = 25
    if context.args and context.args[0].isdigit():
        minutes = max(1, min(120, int(context.args[0])))
    context.job_queue.run_once(
        _pomodoro_done,
        when=timedelta(minutes=minutes),
        chat_id=chat_id,
        name=f"pomodoro:{chat_id}",
    )
    await update.message.reply_text(f"⏱ Pomodoro started: {minutes} min. I'll ping you when done!")


async def _pomodoro_done(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="⏰ Pomodoro done! Take a 5 minute break. 🧘",
    )


async def handle_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if not text.lower().startswith("add:"):
        return
    body = text[4:].strip()
    if " / " not in body:
        await update.message.reply_text("❌ Format: add: question / answer")
        return
    question, _, rest = body.partition(" / ")
    question = question.strip()
    tags_found = _TAG_RE.findall(rest)
    answer = _TAG_RE.sub("", rest).strip()
    tags = ",".join(tags_found) if tags_found else None
    if not question or not answer:
        await update.message.reply_text("❌ Both question and answer must be non-empty.")
        return
    chat_id = update.effective_chat.id
    card_id = db.add_card(question, answer, chat_id, tags=tags)
    tag_str = f" | 🏷 {tags}" if tags else ""
    await update.message.reply_text(f"✅ Card #{card_id} added.{tag_str}")


async def check_due_cards(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
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
    if (datetime.utcnow() + _IST).weekday() != 6:
        return
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return
    stats = db.get_weekly_stats(chat_id)
    streak = db.get_streak_info(chat_id)
    bar = _progress_bar(stats["active_days"], 7)
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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data: str = query.data
    chat_id = query.message.chat_id

    try:
        if data.startswith("reveal:"):
            card_id = int(data.split(":")[1])
            card = db.get_card(card_id)
            if card is None:
                await query.edit_message_text("Card not found.")
                return
            tags_str = f"\n🏷 {card['tags']}" if card.get("tags") else ""
            # Plain text for card content — avoids Markdown errors on special chars
            await query.edit_message_text(
                f"📚 {_clip(card['question'], 500)}\n\n💡 {_clip(card['answer'], 800)}{tags_str}",
                reply_markup=_build_answer_keyboard(card_id),
            )

        elif data.startswith("ans:"):
            _, quality_str, card_id_str = data.split(":")
            quality = int(quality_str)
            card_id = int(card_id_str)
            db.record_answer(card_id, quality)
            db.log_review(chat_id, card_id, quality)
            db.update_streak(chat_id)
            card = db.get_card(card_id)
            next_date = card["due_at"][:10] if card else "N/A"
            labels = {1: "🔴 Again", 3: "🟠 Hard", 4: "🟢 Good", 5: "🔵 Easy"}
            await query.edit_message_text(f"{labels.get(quality, '✅')} — next review: {next_date}")
            session = context.chat_data.get("session", {})
            session["reviewed"] = session.get("reviewed", 0) + 1
            if quality >= 3:
                session["correct"] = session.get("correct", 0) + 1
            context.chat_data["session"] = session
            _pop_from_queue(context, card_id)
            if quality == 1:  # Again — re-queue at end, increment total for accurate progress bar
                queue = context.chat_data.get("review_queue", [])
                if card_id not in queue:
                    context.chat_data["review_queue"] = queue + [card_id]
                    session["total"] = session.get("total", 0) + 1
                    context.chat_data["session"] = session
            await _send_next_card(context, chat_id)

        elif data.startswith("snooze:"):
            parts = data.split(":")
            snooze_type = parts[1]
            card_id = int(parts[2])
            db.snooze_card(card_id, _snooze_delta(snooze_type))
            label_map = {"1h": "1 hour", "tonight": "tonight", "tomorrow": "tomorrow"}
            await query.edit_message_text(f"⏰ Snoozed until {label_map.get(snooze_type, snooze_type)}")
            _pop_from_queue(context, card_id)
            await _send_next_card(context, chat_id)

        else:
            logger.warning("Unknown callback data: %s", data)

    except Exception as e:
        logger.error("Error handling callback %s: %s", data, e)
        await query.answer("Something went wrong, please try again.")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Error: TELEGRAM_BOT_TOKEN environment variable not set.")

    db.init_db()

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("tags", tags_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("streak", streak_command))
    application.add_handler(CommandHandler("goal", goal_command))
    application.add_handler(CommandHandler("setdigest", setdigest_command))
    application.add_handler(CommandHandler("pomodoro", pomodoro_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_card))
    application.add_handler(CallbackQueryHandler(handle_callback))

    application.job_queue.run_repeating(check_due_cards, interval=300, first=10)

    saved_digest = db.get_global_setting("digest_time_ist") or "13:00"
    dh, dm = map(int, saved_digest.split(":"))
    h_utc, m_utc = _ist_to_utc(dh, dm)
    application.job_queue.run_daily(
        daily_digest,
        time=dtime(hour=h_utc, minute=m_utc, tzinfo=timezone.utc),
        name="daily_digest",
    )

    wr_h, wr_m = _ist_to_utc(19, 0)
    application.job_queue.run_daily(
        weekly_report,
        time=dtime(hour=wr_h, minute=wr_m, tzinfo=timezone.utc),
        name="weekly_report",
    )

    logger.info("Bot started. Polling for updates...")
    application.run_polling()


if __name__ == "__main__":
    main()
