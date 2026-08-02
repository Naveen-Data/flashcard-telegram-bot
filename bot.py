import logging
import os
import re
import sys
from datetime import datetime, timedelta

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
            InlineKeyboardButton("✅ Got it right", callback_data=f"correct:{card_id}"),
            InlineKeyboardButton("❌ Got it wrong", callback_data=f"wrong:{card_id}"),
        ]
    ])


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
    logger.warning("Unknown snooze type: %s, defaulting to 1h", snooze_type)
    return timedelta(hours=1)


async def _send_next_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    queue: list[int] = context.chat_data.get("review_queue", [])
    if not queue:
        await context.bot.send_message(chat_id=chat_id, text="🎉 All done! Great work.")
        return
    card_id = queue[0]
    card = db.get_card(card_id)
    if card is None:
        context.chat_data["review_queue"] = queue[1:]
        await _send_next_card(context, chat_id)
        return
    tags_str = f"\n🏷 {card['tags']}" if card.get("tags") else ""
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📚 {card['question']}{tags_str}",
        reply_markup=_build_due_keyboard(card_id),
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db.set_registered_chat_id(chat_id)
    await update.message.reply_text(
        "✅ Registered! I'll notify you when cards are due.\n\n"
        "Add a card:\n`add: question / answer`\n"
        "With tags:\n`add: question / answer #python #math`\n\n"
        "/review — start reviewing due cards\n"
        "/review python — review only \\#python cards\n"
        "/tags — list all your tags",
        parse_mode="Markdown",
    )


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    tag = context.args[0].lstrip("#") if context.args else None
    due_cards = db.list_due_cards(chat_id, tag=tag)
    if not due_cards:
        suffix = f" for #{tag}" if tag else ""
        await update.message.reply_text(f"No cards due{suffix}! 🎉")
        return
    context.chat_data["review_queue"] = [c["id"] for c in due_cards]
    context.bot_data.pop("last_notified_ids", None)
    suffix = f" for #{tag}" if tag else ""
    await update.message.reply_text(
        f"Starting review{suffix}: {len(due_cards)} card(s) due."
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
    lines = ["📂 Your tags:\n"]
    for tag, total, due in tags:
        due_str = f" ({due} due)" if due > 0 else ""
        lines.append(f"  #{tag} — {total} card{'s' if total != 1 else ''}{due_str}")
    await update.message.reply_text("\n".join(lines))


async def handle_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if not text.lower().startswith("add:"):
        return
    body = text[4:].strip()
    if " / " not in body:
        await update.message.reply_text(
            "❌ Format: `add: question / answer`",
            parse_mode="Markdown",
        )
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
    """Periodic job: send one notification when new cards become due."""
    chat_id = db.get_registered_chat_id()
    if chat_id is None:
        return
    due_cards = db.list_due_cards(chat_id)
    if not due_cards:
        return
    due_ids = frozenset(c["id"] for c in due_cards)
    last_notified: frozenset = context.bot_data.get("last_notified_ids", frozenset())
    if not (due_ids - last_notified):
        return  # nothing new since last notification
    context.bot_data["last_notified_ids"] = due_ids
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📚 {len(due_cards)} card(s) due for review.\n/review to start.",
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
            await query.edit_message_text(
                f"📚 {card['question']}\n\n💡 {card['answer']}{tags_str}",
                reply_markup=_build_answer_keyboard(card_id),
            )

        elif data.startswith("correct:"):
            card_id = int(data.split(":")[1])
            db.record_answer(card_id, correct=True)
            card = db.get_card(card_id)
            next_date = card["due_at"][:10] if card else "N/A"
            await query.edit_message_text(f"✅ Got it! Next review: {next_date}")
            _pop_from_queue(context, card_id)
            await _send_next_card(context, chat_id)

        elif data.startswith("wrong:"):
            card_id = int(data.split(":")[1])
            db.record_answer(card_id, correct=False)
            await query.edit_message_text("❌ Resetting — due tomorrow")
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


def _pop_from_queue(context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    queue: list[int] = context.chat_data.get("review_queue", [])
    if queue and queue[0] == card_id:
        context.chat_data["review_queue"] = queue[1:]


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
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("tags", tags_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_card)
    )
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Check for due cards every 5 minutes — sends one notification per new batch
    application.job_queue.run_repeating(check_due_cards, interval=300, first=10)

    logger.info("Bot started. Polling for updates...")
    application.run_polling()


if __name__ == "__main__":
    main()
