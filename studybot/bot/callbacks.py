import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from studybot import db
from studybot.bot import keyboards, utils
from studybot.fsrs import LEECH_THRESHOLD

logger = logging.getLogger(__name__)


def _pop_from_queue(context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    """Remove first occurrence of card_id from queue regardless of position."""
    queue: list[int] = context.chat_data.get("review_queue", [])
    try:
        idx = queue.index(card_id)
        context.chat_data["review_queue"] = queue[:idx] + queue[idx + 1:]
    except ValueError:
        pass


async def send_next_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
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
        await send_next_card(context, chat_id)
        return
    session = context.chat_data.get("session", {})
    reviewed = session.get("reviewed", 0)
    total = session.get("total", len(queue))
    bar = f"\n{utils.progress_bar(reviewed, total)} {reviewed}/{total}" if total else ""
    tags_str = f"\n🏷 {card['tags']}" if card.get("tags") else ""

    front = card["question"]
    if card.get("card_type") == "cloze":
        front = utils.cloze_front(front)

    # Plain text for card content — avoids Markdown parse errors on special chars
    text = f"📚 #{card_id} — {utils.clip(front, 500)}{tags_str}{bar}"
    keyboard = keyboards.due_keyboard(card_id)

    if card.get("image_file_id"):
        await context.bot.send_photo(
            chat_id=chat_id, photo=card["image_file_id"], caption=text, reply_markup=keyboard,
        )
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data: str = query.data
    chat_id = query.message.chat_id
    is_photo = bool(query.message.photo)

    async def edit(text: str, reply_markup=None):
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text, reply_markup=reply_markup)

    try:
        if data.startswith("reveal:"):
            card_id = int(data.split(":")[1])
            card = db.get_card(card_id)
            if card is None:
                await edit("Card not found.")
                return
            tags_str = f"\n🏷 {card['tags']}" if card.get("tags") else ""
            notes_str = f"\n\n📝 {utils.clip(card['notes'], 500)}" if card.get("notes") else ""
            if card.get("card_type") == "cloze":
                filled = utils.cloze_back(card["question"])
                text = f"📚 #{card_id} — {utils.clip(filled, 800)}{tags_str}{notes_str}"
            else:
                text = (
                    f"📚 #{card_id} — {utils.clip(card['question'], 500)}\n\n"
                    f"💡 {utils.clip(card['answer'], 800)}{tags_str}{notes_str}"
                )
            await edit(text, reply_markup=keyboards.answer_keyboard(card_id))

        elif data.startswith("ans:"):
            _, quality_str, card_id_str = data.split(":")
            quality = int(quality_str)
            card_id = int(card_id_str)

            pre_card = db.get_card(card_id)
            if pre_card is None:
                await edit("Card not found.")
                return

            db.record_answer(
                card_id, quality,
                desired_retention=db.get_desired_retention(chat_id),
                study_window=db.get_study_window(chat_id),
                exam_date=db.get_exam_date_for_card(chat_id, pre_card.get("tags")),
            )
            log_id = db.log_review(chat_id, card_id, quality)
            db.save_undo_snapshot(chat_id, pre_card, log_id)
            db.update_streak(chat_id)

            card = db.get_card(card_id)
            labels = {1: "🔴 Again", 3: "🟠 Hard", 4: "🟢 Good", 5: "🔵 Easy"}
            if card:
                next_date = card["due_at"][:10]
                interval = card["interval_days"]
                detail = f"next review: {next_date} ({interval}d)"
            else:
                detail = "next review: N/A"
            leech_note = ""
            if card and card.get("consecutive_again", 0) == LEECH_THRESHOLD:
                leech_note = f"\n⚠️ Leech — {LEECH_THRESHOLD} wrong in a row. /leeches to manage."
            await edit(f"{labels.get(quality, '✅')} — {detail}{leech_note}")

            session = context.chat_data.get("session", {})
            session["reviewed"] = session.get("reviewed", 0) + 1
            if quality >= 3:
                session["correct"] = session.get("correct", 0) + 1
            context.chat_data["session"] = session
            _pop_from_queue(context, card_id)
            if quality == 1:  # Again — re-queue at end, bump total so the bar stays honest
                queue = context.chat_data.get("review_queue", [])
                if card_id not in queue:
                    context.chat_data["review_queue"] = queue + [card_id]
                    session["total"] = session.get("total", 0) + 1
                    context.chat_data["session"] = session
            await send_next_card(context, chat_id)

        elif data.startswith("snooze:"):
            parts = data.split(":")
            snooze_type = parts[1]
            card_id = int(parts[2])
            db.snooze_card(card_id, utils.snooze_delta(snooze_type))
            label_map = {"1h": "1 hour", "tonight": "tonight", "tomorrow": "tomorrow"}
            await edit(f"⏰ Snoozed until {label_map.get(snooze_type, snooze_type)}")
            _pop_from_queue(context, card_id)
            await send_next_card(context, chat_id)

        elif data.startswith("bury:"):
            card_id = int(data.split(":")[1])
            db.bury_card(card_id)
            await edit(f"🫥 Card #{card_id} buried until tomorrow (schedule untouched).")
            _pop_from_queue(context, card_id)
            await send_next_card(context, chat_id)

        elif data.startswith("suspend:"):
            card_id = int(data.split(":")[1])
            db.set_suspended(card_id, True)
            await edit(f"⏸ Card #{card_id} suspended. /unsuspend {card_id} to bring it back.")
            _pop_from_queue(context, card_id)
            await send_next_card(context, chat_id)

        else:
            logger.warning("Unknown callback data: %s", data)

    except Exception as e:
        logger.error("Error handling callback %s: %s", data, e)
        await query.answer("Something went wrong, please try again.")
