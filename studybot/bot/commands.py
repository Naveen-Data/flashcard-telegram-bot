import io
from datetime import datetime, time as dtime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from studybot import db
from studybot.bot import utils
from studybot.bot.callbacks import send_next_card
from studybot.bot.jobs import daily_digest, pomodoro_done
from studybot.sm2 import LEECH_THRESHOLD


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
        "`add: question / answer #tag` — add with tags\n"
        "`cloze: The {{c1::term}} is hidden` — cloze card\n"
        "Send a photo with caption `add: q / a` — image card\n"
        "/edit `id` question / answer — edit a card\n"
        "/delete `id` — delete a card\n"
        "/card `id` — view a card's details & history\n\n"
        "*Review*\n"
        "/review — review due cards\n"
        "/review `tag` — review due cards for a tag\n"
        "/review all — review all cards\n"
        "/review `tag` all — review all cards for a tag\n"
        "/undo — undo your last answer\n"
        "/tags — list all tags\n"
        "/search `keyword` — search cards\n"
        "/leeches — cards you keep getting wrong\n\n"
        "*Progress*\n"
        "/streak — view your streak\n"
        "/goal `N` — set daily card goal\n\n"
        "*Settings*\n"
        "/setdigest — show digest time\n"
        "/setdigest `HH:MM` — set daily digest time (IST)\n"
        "/dnd `HH:MM-HH:MM` — quiet hours (IST), no due-card pings\n"
        "/dnd off — clear quiet hours\n"
        "/backup — export all your cards as JSON\n\n"
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
    await send_next_card(context, chat_id)


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
        lines.append(f"• #{card['id']} {utils.clip(card['question'], 120)}{tags_str}\n  → {utils.clip(card['answer'], 200)}")
    if len(results) > 10:
        lines.append(f"\n…and {len(results) - 10} more.")
    await update.message.reply_text("\n".join(lines)[:utils.TG_MAX])


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    info = db.get_streak_info(chat_id)
    goal = int(db.get_setting(chat_id, "daily_goal") or "0")
    today_count_str = ""
    if goal > 0:
        weekly = db.get_weekly_stats(chat_id)
        today_ist = (datetime.utcnow() + utils.IST).date().isoformat()
        today_reviewed = weekly["by_day"].get(today_ist, 0)
        bar = utils.progress_bar(today_reviewed, goal)
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
    h_utc, m_utc = utils.ist_to_utc(h, m)
    for job in context.job_queue.get_jobs_by_name("daily_digest"):
        job.schedule_removal()
    context.job_queue.run_daily(
        daily_digest,
        time=dtime(hour=h_utc, minute=m_utc, tzinfo=timezone.utc),
        name="daily_digest",
    )
    await update.message.reply_text(f"✅ Daily digest set to {h:02d}:{m:02d} IST.")


async def dnd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        window = db.get_dnd_window(chat_id)
        if window and window[0]:
            await update.message.reply_text(
                f"🔕 DND: {window[0]}–{window[1]} IST\nChange: /dnd HH:MM-HH:MM or /dnd off"
            )
        else:
            await update.message.reply_text("🔔 DND is off.\nSet: /dnd HH:MM-HH:MM (e.g. /dnd 22:00-07:00)")
        return
    if args[0].lower() == "off":
        db.set_dnd_window(chat_id, None, None)
        await update.message.reply_text("🔔 DND turned off.")
        return
    if "-" not in args[0]:
        await update.message.reply_text("❌ Format: /dnd HH:MM-HH:MM or /dnd off")
        return
    start_str, end_str = args[0].split("-", 1)
    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        assert 0 <= sh < 24 and 0 <= sm < 60 and 0 <= eh < 24 and 0 <= em < 60
    except Exception:
        await update.message.reply_text("❌ Format: /dnd HH:MM-HH:MM (e.g. /dnd 22:00-07:00)")
        return
    db.set_dnd_window(chat_id, f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}")
    await update.message.reply_text(f"🔕 DND set: {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} IST")


async def pomodoro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    minutes = 25
    if context.args and context.args[0].isdigit():
        minutes = max(1, min(120, int(context.args[0])))
    context.job_queue.run_once(
        pomodoro_done,
        when=timedelta(minutes=minutes),
        chat_id=chat_id,
        name=f"pomodoro:{chat_id}",
    )
    await update.message.reply_text(f"⏱ Pomodoro started: {minutes} min. I'll ping you when done!")


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /edit <id> question / answer")
        return
    card_id = int(args[0])
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    body = " ".join(args[1:]).strip()
    if " / " not in body:
        await update.message.reply_text("Usage: /edit <id> question / answer")
        return
    question, _, rest = body.partition(" / ")
    question = question.strip()
    tags_found = utils.TAG_RE.findall(rest)
    answer = utils.TAG_RE.sub("", rest).strip()
    tags = ",".join(tags_found) if tags_found else None
    if not question or not answer:
        await update.message.reply_text("❌ Both question and answer must be non-empty.")
        return
    db.edit_card(card_id, question=question, answer=answer, tags=tags)
    await update.message.reply_text(f"✅ Card #{card_id} updated.")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /delete <id>")
        return
    card_id = int(args[0])
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    db.delete_card(card_id)
    await update.message.reply_text(f"🗑 Card #{card_id} deleted.")


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if db.apply_undo(chat_id):
        await update.message.reply_text("↩️ Last answer undone.")
    else:
        await update.message.reply_text("Nothing to undo.")


async def leeches_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    leeches = db.list_leeches(chat_id)
    if not leeches:
        await update.message.reply_text("No leeches — nothing you're stuck on right now. 🎉")
        return
    lines = ["🩸 Leeches (4+ wrong in a row):\n"]
    for card in leeches:
        lines.append(f"#{card['id']} — {utils.clip(card['question'], 80)} ({card['consecutive_again']}x)")
    await update.message.reply_text("\n".join(lines)[:utils.TG_MAX])


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    data = db.export_backup(chat_id)
    buf = io.BytesIO(data.encode("utf-8"))
    filename = f"studybot_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    await update.message.reply_document(document=buf, filename=filename, caption="📦 Your backup.")


async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /card <id>")
        return
    card_id = int(args[0])
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    history = db.get_card_history(card_id, limit=5)
    labels = {1: "🔴", 3: "🟠", 4: "🟢", 5: "🔵"}
    hist_lines = [f"  {labels.get(h['quality'], '?')} {h['reviewed_at'][:16]}" for h in history]
    hist_str = "\n".join(hist_lines) if hist_lines else "  No reviews yet."
    tags_str = card["tags"] or "none"
    leech_str = " ⚠️ LEECH" if card["consecutive_again"] >= LEECH_THRESHOLD else ""
    await update.message.reply_text(
        f"📇 Card #{card['id']}{leech_str}\n"
        f"Q: {utils.clip(card['question'], 300)}\n"
        f"A: {utils.clip(card['answer'], 300)}\n"
        f"Tags: {tags_str}\n"
        f"Stage: {card['stage']} | Ease: {card['ease_factor']:.2f} | Interval: {card['interval_days']}d\n"
        f"Due: {card['due_at'][:16]}\n\n"
        f"Recent reviews:\n{hist_str}"
    )


async def handle_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    lower = text.lower()

    if lower.startswith("cloze:"):
        await _add_cloze_card(update, text[6:].strip())
        return

    if not lower.startswith("add:"):
        return
    body = text[4:].strip()
    if " / " not in body:
        await update.message.reply_text("❌ Format: add: question / answer")
        return
    question, _, rest = body.partition(" / ")
    question = question.strip()
    tags_found = utils.TAG_RE.findall(rest)
    answer = utils.TAG_RE.sub("", rest).strip()
    tags = ",".join(tags_found) if tags_found else None
    if not question or not answer:
        await update.message.reply_text("❌ Both question and answer must be non-empty.")
        return
    chat_id = update.effective_chat.id
    card_id = db.add_card(question, answer, chat_id, tags=tags)
    tag_str = f" | 🏷 {tags}" if tags else ""
    await update.message.reply_text(f"✅ Card #{card_id} added.{tag_str}")


async def _add_cloze_card(update: Update, body: str) -> None:
    if not utils.is_cloze_text(body):
        await update.message.reply_text(
            "❌ No {{c1::...}} cloze found. Format: cloze: The {{c1::answer}} is hidden."
        )
        return
    tags_found = utils.TAG_RE.findall(body)
    clean_text = utils.TAG_RE.sub("", body).strip()
    tags = ",".join(tags_found) if tags_found else None
    chat_id = update.effective_chat.id
    card_id = db.add_card(clean_text, "", chat_id, tags=tags, card_type="cloze")
    tag_str = f" | 🏷 {tags}" if tags else ""
    await update.message.reply_text(f"✅ Cloze card #{card_id} added.{tag_str}")


async def handle_add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.caption:
        return
    text = update.message.caption.strip()
    if not text.lower().startswith("add:"):
        return
    body = text[4:].strip()
    if " / " not in body:
        await update.message.reply_text("❌ Format (as photo caption): add: question / answer")
        return
    question, _, rest = body.partition(" / ")
    question = question.strip()
    tags_found = utils.TAG_RE.findall(rest)
    answer = utils.TAG_RE.sub("", rest).strip()
    tags = ",".join(tags_found) if tags_found else None
    if not question or not answer:
        await update.message.reply_text("❌ Both question and answer must be non-empty.")
        return
    chat_id = update.effective_chat.id
    image_file_id = update.message.photo[-1].file_id
    card_id = db.add_card(question, answer, chat_id, tags=tags, image_file_id=image_file_id)
    tag_str = f" | 🏷 {tags}" if tags else ""
    await update.message.reply_text(f"✅ Card #{card_id} added with image.{tag_str}")
