import logging
import os
import re
import sys
from datetime import datetime, time, timedelta

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


def _ist_to_utc(hour: int, minute: int) -> time:
    total = (hour * 60 + minute - 330) % (24 * 60)
    return time(total // 60, total % 60, 0)


def _utc_to_ist_str() -> str:
    return (datetime.utcnow() + _IST).strftime("%H:%M IST")


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

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
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 Again", callback_data=f"ans:1:{card_id}"),
            InlineKeyboardButton("🟠 Hard", callback_data=f"ans:3:{card_id}"),
            InlineKeyboardButton("🟢 Good", callback_data=f"ans:4:{card_id}"),
            InlineKeyboardButton("🔵 Easy", callback_data=f"ans:5:{card_id}"),
        ]
    ])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snooze_delta(snooze_type: str) -> timedelta:
    now_local = datetime.now()
    if snooze_type == "1h":
        return timedelta(hours=1)
    if snooze_type == "tonight":
        target = now_local.replace(hour=20, minute=0, second=0, microsecond=0)
        if now_local.hour >= 19:
            target += timedelta(days=1)
        delta = target - now_local
        return delta if delta.total_seconds() > 0 else timedelta(hours=1)
    if snooze_type == "tomorrow":
        target = (now_local + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return target - now_local
    return timedelta(hours=1)


def _pop_from_queue(context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    queue: list[int] = context.chat_data.get("review_queue", [])
    if queue and queue[0] == card_id:
        context.chat_data["review_queue"] = queue[1:]


def _progress_bar(current: int, total: int, width: int = 10) -> str:
    filled = round(width * current / total) if total else 0
    return "▓" * filled + "░" * (width - filled)


async def _send_next_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    queue: list[int] = context.chat_data.get("review_queue", [])
    total: int = context.chat_data.get("session_total", 0)
    reviewed = total - len(queue)

    if not queue:
        await _send_session_summary(context, chat_id)
        return

    card_id = queue[0]
    card = db.get_card(card_id)
    if card is None:
        context.chat_data["review_queue"] = queue[1:]
        await _send_next_card(context, chat_id)
        return

    tags_str = f"\n🏷 {card['tags']}" if card.get("tags") else ""
    progress = f"[{reviewed}/{total}] {_progress_bar(reviewed, total)}\n\n" if total else ""
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{progress}📚 {card['question']}{tags_str}",
        reply_markup=_build_due_keyboard(card_id),
    )


async def _send_session_summary(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    session = context.chat_data.get("session", {})
    if not session:
        await context.bot.send_message(chat_id=chat_id, text="🎉 All done!")
        return

    total = session.get("total", 0)
    correct = session.get("correct", 0)
    hard = session.get("hard", 0)
    again = session.get("again", 0)
    elapsed = int((datetime.utcnow() - session["start"]).total_seconds() / 60)
    accuracy = round(correct / total * 100) if total else 0

    streak_info = db.get_streak_info(chat_id)
    streak_line = f"🔥 {streak_info['streak']}-day streak"
    goal_line = f" | 🎯 {streak_info['cards_today']}/{streak_info['goal']} today"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ Session complete!\n\n"
            f"📊 {total} cards in {elapsed} min\n"
            f"🟢 Good/Easy: {correct}  🟠 Hard: {hard}  🔴 Again: {again}\n"
            f"🎯 Accuracy: {accuracy}%\n\n"
            f"{streak_line}{goal_line}"
        ),
    )
    context.chat_data.pop("session", None)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db.set_registered_chat_id(chat_id)
    await update.message.reply_text(
        "✅ Registered! I'm your study buddy.\n\n"
        "*Add cards:*\n"
        "`add: question / answer #tag`\n\n"
        "*Commands:*\n"
        "/review — review due cards\n"
        "/review rag — review #rag cards\n"
        "/review all — review everything\n"
        "/review rag all — all #rag cards\n"
        "/tags — list all tags\n"
        "/search keyword — search your cards\n"
        "/streak — your study streak\n"
        "/goal 10 — set daily card goal\n"
        "/pomodoro — 25 min focus timer\n"
        "/pomodoro 45 — custom timer",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Study Buddy — Commands*\n\n"
        "*Adding Cards*\n"
        "`add: question / answer` — add a card\n"
        "`add: question / answer #tag` — add with tag\n\n"
        "*Reviewing*\n"
        "/review — review due cards\n"
        "/review rag — only \\#rag cards\n"
        "/review all — all cards (ignore due date)\n"
        "/review rag all — all \\#rag cards\n\n"
        "*Organising*\n"
        "/tags — list all tags with due counts\n"
        "/search keyword — search your cards\n\n"
        "*Progress*\n"
        "/streak — streak, longest, today's goal\n"
        "/goal 10 — set daily card target\n\n"
        "*Focus*\n"
        "/pomodoro — 25 min focus timer\n"
        "/pomodoro 45 — custom timer\n\n"
        "*Settings*\n"
        "/setdigest — show current digest time\n"
        "/setdigest 14:00 — set daily digest to 2pm IST\n\n"
        "*Review buttons*\n"
        "🔴 Again — forgot, resets to tomorrow\n"
        "🟠 Hard — remembered with difficulty\n"
        "🟢 Good — normal recall\n"
        "🔵 Easy — too easy, longer gap",
        parse_mode="Markdown",
    )


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = [a.lstrip("#") for a in (context.args or [])]
    force = "all" in args
    tag = next((a for a in args if a != "all"), None)

    cards = db.list_all_cards(chat_id, tag=tag) if force else db.list_due_cards(chat_id, tag=tag)

    if not cards:
        suffix = f" for #{tag}" if tag else ""
        await update.message.reply_text(
            f"No cards{'  ' if not force else ''} due{suffix}! 🎉"
        )
        return

    context.chat_data["review_queue"] = [c["id"] for c in cards]
    context.chat_data["session"] = {
        "start": datetime.utcnow(),
        "total": len(cards),
        "correct": 0,
        "hard": 0,
        "again": 0,
    }
    context.chat_data["session_total"] = len(cards)
    context.bot_data.pop("last_notified_ids", None)

    suffix = f" for #{tag}" if tag else ""
    mode = "all" if force else "due"
    await update.message.reply_text(
        f"Starting review{suffix}: {len(cards)} {mode} card(s). Let's go! 💪"
    )
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
    lines = ["📂 *Your tags:*\n"]
    for tag, total, due in tags:
        due_str = f" ({due} due)" if due > 0 else ""
        lines.append(f"  #{tag} — {total} card{'s' if total != 1 else ''}{due_str}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text("Usage: `/search keyword`", parse_mode="Markdown")
        return
    results = db.search_cards(chat_id, query)
    if not results:
        await update.message.reply_text(f"No cards found for '{query}'.")
        return
    lines = [f"🔍 *{len(results)} result(s) for '{query}':*\n"]
    for card in results[:10]:
        tags_str = f" 🏷{card['tags']}" if card.get("tags") else ""
        lines.append(f"• {card['question']}{tags_str}")
    if len(results) > 10:
        lines.append(f"\n_...and {len(results) - 10} more_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    info = db.get_streak_info(chat_id)
    goal_bar = _progress_bar(info["cards_today"], info["goal"])
    await update.message.reply_text(
        f"🔥 *Streak:* {info['streak']} day(s)\n"
        f"🏆 *Longest:* {info['longest']} day(s)\n\n"
        f"🎯 *Today:* {info['cards_today']}/{info['goal']} cards\n"
        f"{goal_bar}",
        parse_mode="Markdown",
    )


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: `/goal 10`", parse_mode="Markdown")
        return
    goal = int(context.args[0])
    db.set_daily_goal(chat_id, goal)
    await update.message.reply_text(f"🎯 Daily goal set to {goal} cards.")


async def setdigest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args or ":" not in context.args[0]:
        saved = db.get_setting(f"digest_time:{chat_id}", "13:00")
        await update.message.reply_text(
            f"📅 Current digest time: *{saved} IST*\n"
            f"Change with: `/setdigest 14:30`",
            parse_mode="Markdown",
        )
        return
    try:
        hour, minute = map(int, context.args[0].split(":"))
        assert 0 <= hour <= 23 and 0 <= minute <= 59
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Invalid time. Use HH:MM format e.g. `/setdigest 14:30`", parse_mode="Markdown")
        return

    db.set_setting(f"digest_time:{chat_id}", f"{hour:02d}:{minute:02d}")

    existing = context.bot_data.get("digest_job")
    if existing:
        existing.schedule_removal()
    utc_time = _ist_to_utc(hour, minute)
    job = context.application.job_queue.run_daily(daily_digest, time=utc_time)
    context.bot_data["digest_job"] = job

    await update.message.reply_text(
        f"📅 Daily digest set to *{hour:02d}:{minute:02d} IST* ✅", parse_mode="Markdown"
    )


async def pomodoro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    minutes = 25
    if context.args and context.args[0].isdigit():
        minutes = int(context.args[0])
    if minutes < 1 or minutes > 120:
        await update.message.reply_text("Timer must be between 1 and 120 minutes.")
        return

    # Cancel any existing pomodoro
    existing = context.chat_data.get("pomodoro_job")
    if existing:
        existing.schedule_removal()

    await update.message.reply_text(
        f"⏱ Pomodoro started: {minutes} min. Focus! 🎯\n"
        f"I'll ping you when time's up."
    )

    async def pomodoro_done(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        context.chat_data.pop("pomodoro_job", None)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="⏱ Time's up! Great focus session 🎉\nTake a 5 min break, then /review or /pomodoro again.",
        )

    job = context.application.job_queue.run_once(
        pomodoro_done, when=timedelta(minutes=minutes)
    )
    context.chat_data["pomodoro_job"] = job


async def handle_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if not text.lower().startswith("add:"):
        return
    body = text[4:].strip()
    if " / " not in body:
        await update.message.reply_text("❌ Format: `add: question / answer`", parse_mode="Markdown")
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


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------

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
    due = db.list_due_cards(chat_id)
    stats = db.get_stats(chat_id)
    streak = db.get_streak_info(chat_id)
    if not due and streak["streak"] == 0:
        return
    due_by_tag: dict[str, int] = {}
    for card in due:
        for tag in (card.get("tags") or "untagged").split(","):
            due_by_tag[tag.strip()] = due_by_tag.get(tag.strip(), 0) + 1
    tag_lines = "\n".join(f"  #{t}: {n}" for t, n in sorted(due_by_tag.items())[:5])
    streak_line = f"🔥 {streak['streak']}-day streak — keep it up!" if streak["streak"] > 1 else ""
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"☀️ Good morning! Here's your study brief:\n\n"
            f"📚 {len(due)} card(s) due today\n"
            f"{tag_lines}\n\n"
            f"📦 Total deck: {stats['total']} cards\n"
            f"{streak_line}\n\n"
            f"/review to start your session"
        ),
    )


async def weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    if datetime.utcnow().weekday() != 6:  # Sunday only
        return
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return
    w = db.get_weekly_stats(chat_id)
    streak = db.get_streak_info(chat_id)
    if w["total"] == 0:
        await context.bot.send_message(
            chat_id=chat_id,
            text="📈 Weekly report: no cards reviewed this week. Start with /review!",
        )
        return
    bar = _progress_bar(w["accuracy"], 100)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📈 *Weekly Report*\n\n"
            f"📚 Cards reviewed: {w['total']}\n"
            f"✅ Accuracy: {w['accuracy']}% {bar}\n"
            f"📅 Active days: {w['days_active']}/7\n"
            f"🔥 Current streak: {streak['streak']} day(s)\n"
            f"🏆 Longest streak: {streak['longest']} day(s)\n\n"
            f"Keep it up! /review to start this week strong 💪"
        ),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

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
            await query.edit_message_text(
                f"📚 {card['question']}\n\n💡 {card['answer']}{tags_str}",
                reply_markup=_build_answer_keyboard(card_id),
            )

        elif data.startswith("ans:"):
            _, quality_str, card_id_str = data.split(":")
            card_id = int(card_id_str)
            quality = int(quality_str)
            db.record_answer(card_id, quality)
            db.log_review(chat_id, card_id, quality)
            streak = db.update_streak(chat_id)

            card = db.get_card(card_id)
            next_date = card["due_at"][:10] if card else "N/A"
            interval = card["interval_days"] if card else "?"
            labels = {1: "🔴 Again", 3: "🟠 Hard", 4: "🟢 Good", 5: "🔵 Easy"}
            await query.edit_message_text(
                f"{labels.get(quality, '')} — next in {interval} day(s) ({next_date})"
            )

            # Update session stats
            session = context.chat_data.get("session", {})
            if session:
                if quality >= 4:
                    session["correct"] = session.get("correct", 0) + 1
                elif quality == 3:
                    session["hard"] = session.get("hard", 0) + 1
                else:
                    session["again"] = session.get("again", 0) + 1

            # Celebrate goal hit
            if streak.get("goal_hit"):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎯 Daily goal hit! {streak['goal']} cards reviewed today 🎉\n🔥 {streak['streak']}-day streak",
                )

            _pop_from_queue(context, card_id)
            await _send_next_card(context, chat_id)

        elif data.startswith("snooze:"):
            parts = data.split(":")
            snooze_type = parts[1]
            card_id = int(parts[2])
            db.snooze_card(card_id, _snooze_delta(snooze_type))
            label_map = {"1h": "1 hour", "tonight": "tonight", "tomorrow": "tomorrow"}
            await query.edit_message_text(
                f"⏰ Snoozed until {label_map.get(snooze_type, snooze_type)}"
            )
            _pop_from_queue(context, card_id)
            await _send_next_card(context, chat_id)

        else:
            logger.warning("Unknown callback data: %s", data)

    except Exception as e:
        logger.error("Error handling callback %s: %s", data, e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit(
            "Error: TELEGRAM_BOT_TOKEN environment variable not set.\n"
            "Export it before running: export TELEGRAM_BOT_TOKEN='your-token-here'"
        )

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
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_card)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))

    jq = application.job_queue
    jq.run_repeating(check_due_cards, interval=300, first=10)

    # Load saved digest time or default to 1PM IST (07:30 UTC)
    chat_id = db.get_registered_chat_id()
    saved_digest = db.get_setting(f"digest_time:{chat_id}", "13:00") if chat_id else "13:00"
    d_hour, d_min = map(int, saved_digest.split(":"))
    digest_job = jq.run_daily(daily_digest, time=_ist_to_utc(d_hour, d_min))
    application.bot_data["digest_job"] = digest_job

    # Weekly report at 7PM IST (13:30 UTC) on Sundays
    jq.run_daily(weekly_report, time=_ist_to_utc(19, 0))

    logger.info("Bot started. Polling for updates...")
    application.run_polling()


if __name__ == "__main__":
    main()
