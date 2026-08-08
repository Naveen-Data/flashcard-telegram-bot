import io
from datetime import datetime, time as dtime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from studybot import db, scheduling
from studybot.bot import utils
from studybot.bot.callbacks import send_next_card
from studybot.bot.jobs import daily_digest, pomodoro_done
from studybot.fsrs import LEECH_THRESHOLD


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
        "*Adding cards*\n"
        "`add: question / answer` — add a card\n"
        "`add: question / answer #tag` — add with tags\n"
        "`add: term / definition --both` — also add the reverse\n"
        "`cloze: The {{c1::term}} is hidden` — cloze card\n"
        "Photo + caption `add: q / a` — image card\n\n"
        "*Managing cards*\n"
        "/edit `id` question / answer — edit a card\n"
        "/note `id` text — attach a note\n"
        "/delete `id` — delete a card\n"
        "/card `id` — details, memory state & history\n"
        "/suspend `id` · /unsuspend `id` · /suspended\n"
        "/bury `id` — hide until tomorrow\n\n"
        "*Review*\n"
        "/review — review due cards\n"
        "/review `tag` — only that tag\n"
        "/review all — ignore due dates\n"
        "/review force — ignore your daily cap\n"
        "/undo — undo your last answer\n"
        "/tags · /search `keyword` · /leeches\n\n"
        "*Insight*\n"
        "/forecast — due-card load for the next 7 days\n"
        "/stats — true retention, overall and per tag\n"
        "/streak — streak and today's goal\n\n"
        "*Settings*\n"
        "/goal `N` — daily card target\n"
        "/cap `N` — max cards/day (`/cap off`)\n"
        "/window `HH:MM-HH:MM` — when cards come due (IST)\n"
        "/retention `0.9` — target recall probability\n"
        "/exam `tag` `YYYY-MM-DD` — cram before a deadline\n"
        "/setdigest `HH:MM` — daily digest time (IST)\n"
        "/dnd `HH:MM-HH:MM` — quiet hours (no pings)\n"
        "/backup — export everything as JSON\n\n"
        "*Focus*\n"
        "/pomodoro `N` — focus timer (default 25 min)",
        parse_mode="Markdown",
    )


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = [a.lstrip("#").lower() for a in (context.args or [])]
    review_all = "all" in args
    force = "force" in args
    tag = next((a for a in args if a not in ("all", "force")), None)

    cards = db.list_all_cards(chat_id, tag=tag) if review_all else db.list_due_cards(chat_id, tag=tag)
    if not cards:
        suffix = f" for #{tag}" if tag else ""
        await update.message.reply_text(f"No cards due{suffix}! 🎉")
        return

    capped_note = ""
    cap = db.get_daily_cap(chat_id)
    if cap and not force:
        done_today = db.count_reviews_today(chat_id)
        remaining = cap - done_today
        if remaining <= 0:
            await update.message.reply_text(
                f"🛑 Daily cap reached ({done_today}/{cap} reviews today).\n"
                f"Rest is good for retention — or /review force to keep going."
            )
            return
        if len(cards) > remaining:
            cards = cards[:remaining]
            capped_note = f" (capped at {remaining} of your {cap}/day)"

    context.chat_data["review_queue"] = [c["id"] for c in cards]
    context.chat_data["session"] = {
        "start": datetime.utcnow(),
        "total": len(cards),
        "reviewed": 0,
        "correct": 0,
    }
    context.bot_data.pop("last_notified_ids", None)
    suffix = f" for #{tag}" if tag else ""
    await update.message.reply_text(
        f"Starting review{suffix}: {len(cards)} card(s).{capped_note}"
    )
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
        lines.append(
            f"• #{card['id']} {utils.clip(card['question'], 120)}{tags_str}\n"
            f"  → {utils.clip(card['answer'], 200)}"
        )
    if len(results) > 10:
        lines.append(f"\n…and {len(results) - 10} more.")
    await update.message.reply_text("\n".join(lines)[:utils.TG_MAX])


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    info = db.get_streak_info(chat_id)
    goal = int(db.get_setting(chat_id, "daily_goal") or "0")
    today_count_str = ""
    if goal > 0:
        done_today = db.count_reviews_today(chat_id)
        bar = utils.progress_bar(done_today, goal)
        today_count_str = f"\n\n🎯 Today: {bar} {done_today}/{goal}"
    current = info["current"]
    longest = info["longest"]
    flame = "🔥" if current > 0 else "💤"
    await update.message.reply_text(
        f"{flame} *Streak: {current} day{'s' if current != 1 else ''}*\n"
        f"🏆 Longest: {longest} day{'s' if longest != 1 else ''}"
        f"{today_count_str}",
        parse_mode="Markdown",
    )


async def forecast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    forecast = db.get_forecast(chat_id, days=7)
    counts = [c for _, c in forecast]
    peak = max(counts) if counts else 0
    cap = db.get_daily_cap(chat_id)

    lines = ["📅 *Upcoming reviews*\n"]
    for label, count in forecast:
        if count == 0 and label == "Overdue":
            continue
        width = round(count / peak * 12) if peak else 0
        bar = "█" * width if count else "·"
        flag = " ⚠️" if cap and count > cap else ""
        lines.append(f"`{label:<12}` {bar} {count}{flag}")

    total = sum(counts)
    lines.append(f"\nTotal next 7 days: {total}")
    if cap:
        lines.append(f"Daily cap: {cap} (⚠️ = over cap)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    stats = db.get_retention_stats(chat_id, days=30)
    deck = db.get_stats(chat_id)

    if stats["reviews"] == 0:
        await update.message.reply_text(
            "📊 Not enough review history yet — retention needs cards you've seen more than once."
        )
        return

    lines = [
        "📊 *Last 30 days*\n",
        f"True retention: *{stats['retention']}%* over {stats['reviews']} reviews",
        f"_(how often you recalled a card you'd already learned)_\n",
        f"📦 Deck: {deck['total']} cards · {deck['due']} due · {deck['suspended']} suspended",
    ]
    if stats["by_tag"]:
        lines.append("\n*Weakest tags:*")
        for tag, data in list(stats["by_tag"].items())[:5]:
            lines.append(f"  #{tag} — {data['retention']}% ({data['reviews']} reviews)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        current = db.get_setting(chat_id, "daily_goal") or "not set"
        await update.message.reply_text(f"Current goal: {current} cards/day\nUsage: /goal 10")
        return
    goal = int(context.args[0])
    db.set_daily_goal(chat_id, goal)
    await update.message.reply_text(f"✅ Daily goal set to {goal} cards.")


async def cap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        cap = db.get_daily_cap(chat_id)
        current = f"{cap} cards/day" if cap else "off"
        await update.message.reply_text(
            f"Daily review cap: {current}\n"
            "Set with /cap 20, disable with /cap off.\n"
            "A cap stops a backlog day from turning into a 60-card slog."
        )
        return
    if args[0].lower() == "off":
        db.set_daily_cap(chat_id, None)
        await update.message.reply_text("✅ Daily cap removed.")
        return
    if not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text("❌ Usage: /cap 20  (or /cap off)")
        return
    cap = int(args[0])
    db.set_daily_cap(chat_id, cap)
    await update.message.reply_text(f"✅ Daily cap set to {cap} cards.")


async def window_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        window = db.get_study_window(chat_id)
        current = window if window else "off (cards come due at whatever time they were reviewed)"
        await update.message.reply_text(
            f"📖 Study window: {current}\n\n"
            "Set with /window 21:00-23:00 — new due dates land inside that window\n"
            "instead of the exact hour you happened to review. /window off to disable."
        )
        return
    if args[0].lower() == "off":
        db.set_study_window(chat_id, None)
        await update.message.reply_text("✅ Study window disabled.")
        return
    if scheduling.parse_window(args[0]) is None:
        await update.message.reply_text("❌ Usage: /window 21:00-23:00  (or /window off)")
        return
    db.set_study_window(chat_id, args[0])
    await update.message.reply_text(
        f"✅ Study window set to {args[0]} IST.\n"
        "Cards reviewed from now on will come due inside it."
    )


async def retention_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        current = db.get_desired_retention(chat_id)
        await update.message.reply_text(
            f"🎯 Target retention: {current:.0%}\n\n"
            "Higher = see cards more often, remember more, more work.\n"
            "Lower = fewer reviews, more forgetting.\n"
            "Set with /retention 0.9 (allowed 0.70–0.99)."
        )
        return
    try:
        value = float(args[0])
        if not 0.7 <= value <= 0.99:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Usage: /retention 0.9  (between 0.70 and 0.99)")
        return
    db.set_desired_retention(chat_id, value)
    await update.message.reply_text(
        f"✅ Target retention set to {value:.0%}. Applies from your next review."
    )


async def exam_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []

    if not args or args[0].lower() == "list":
        exams = db.list_exams(chat_id)
        if not exams:
            await update.message.reply_text(
                "No exams set.\n"
                "Set one with /exam rag 2026-08-20 — reviews for #rag will bunch up\n"
                "so nothing is scheduled past the exam date."
            )
            return
        today = scheduling.now_ist().date()
        lines = ["🎓 Exams:\n"]
        for tag, date_str in exams:
            try:
                days = (datetime.strptime(date_str, "%Y-%m-%d").date() - today).days
                when = f"{days}d away" if days > 0 else "passed"
            except ValueError:
                when = "?"
            lines.append(f"  #{tag} — {date_str} ({when})")
        lines.append("\nClear with /exam clear <tag>")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0].lower() == "clear":
        if len(args) < 2:
            await update.message.reply_text("Usage: /exam clear <tag>")
            return
        tag = args[1].lstrip("#")
        db.clear_exam(chat_id, tag)
        await update.message.reply_text(f"✅ Exam cleared for #{tag}.")
        return

    if len(args) < 2:
        await update.message.reply_text("Usage: /exam <tag> <YYYY-MM-DD>")
        return
    tag = args[0].lstrip("#")
    date_str = args[1]
    try:
        exam_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text("❌ Date format: YYYY-MM-DD (e.g. 2026-08-20)")
        return
    days = (exam_date - scheduling.now_ist().date()).days
    if days < 0:
        await update.message.reply_text("❌ That date is in the past.")
        return
    db.set_exam(chat_id, tag, date_str)
    await update.message.reply_text(
        f"🎓 Exam set: #{tag} on {date_str} ({days}d away).\n"
        f"Intervals for #{tag} will now be capped so you always get another look before then."
    )


async def setdigest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        saved = db.get_global_setting("digest_time_ist") or "13:00"
        await update.message.reply_text(f"Daily digest is at {saved} IST.\nChange: /setdigest 14:00")
        return
    parsed = scheduling.parse_hhmm(context.args[0])
    if parsed is None:
        await update.message.reply_text("❌ Format: /setdigest HH:MM (e.g. 14:00)")
        return
    db.set_global_setting("digest_time_ist", f"{parsed.hour:02d}:{parsed.minute:02d}")
    h_utc, m_utc = utils.ist_to_utc(parsed.hour, parsed.minute)
    for job in context.job_queue.get_jobs_by_name("daily_digest"):
        job.schedule_removal()
    context.job_queue.run_daily(
        daily_digest,
        time=dtime(hour=h_utc, minute=m_utc, tzinfo=timezone.utc),
        name="daily_digest",
    )
    await update.message.reply_text(
        f"✅ Daily digest set to {parsed.hour:02d}:{parsed.minute:02d} IST."
    )


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
            await update.message.reply_text(
                "🔔 DND is off.\nSet: /dnd HH:MM-HH:MM (e.g. /dnd 22:00-07:00)"
            )
        return
    if args[0].lower() == "off":
        db.set_dnd_window(chat_id, None, None)
        await update.message.reply_text("🔔 DND turned off.")
        return
    window = scheduling.parse_window(args[0])
    if window is None:
        await update.message.reply_text("❌ Format: /dnd HH:MM-HH:MM (e.g. /dnd 22:00-07:00)")
        return
    start, end = window
    db.set_dnd_window(
        chat_id, f"{start.hour:02d}:{start.minute:02d}", f"{end.hour:02d}:{end.minute:02d}"
    )
    await update.message.reply_text(
        f"🔕 DND set: {start.hour:02d}:{start.minute:02d}–{end.hour:02d}:{end.minute:02d} IST"
    )


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


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /note <id> your note here")
        return
    card_id = int(args[0])
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    note = " ".join(args[1:]).strip()
    if not note:
        await update.message.reply_text("Usage: /note <id> your note here")
        return
    db.edit_card(card_id, notes=note)
    await update.message.reply_text(f"📝 Note saved on card #{card_id} — shown when you reveal it.")


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


async def suspend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_suspended(update, context, suspended=True)


async def unsuspend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_suspended(update, context, suspended=False)


async def _set_suspended(
    update: Update, context: ContextTypes.DEFAULT_TYPE, suspended: bool
) -> None:
    chat_id = update.effective_chat.id
    verb = "suspend" if suspended else "unsuspend"
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text(f"Usage: /{verb} <id>")
        return
    card_id = int(args[0])
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    db.set_suspended(card_id, suspended)
    if suspended:
        await update.message.reply_text(
            f"⏸ Card #{card_id} suspended — out of rotation until /unsuspend {card_id}."
        )
    else:
        await update.message.reply_text(f"▶️ Card #{card_id} back in rotation.")


async def suspended_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    cards = db.list_suspended(chat_id)
    if not cards:
        await update.message.reply_text("No suspended cards.")
        return
    lines = [f"⏸ {len(cards)} suspended card(s):\n"]
    for card in cards:
        lines.append(f"#{card['id']} — {utils.clip(card['question'], 80)}")
    lines.append("\n/unsuspend <id> to restore.")
    await update.message.reply_text("\n".join(lines)[:utils.TG_MAX])


async def bury_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /bury <id>")
        return
    card_id = int(args[0])
    card = db.get_card(card_id)
    if card is None or card["chat_id"] != chat_id:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    db.bury_card(card_id)
    await update.message.reply_text(
        f"🫥 Card #{card_id} buried until tomorrow — its schedule is untouched."
    )


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
    lines = [f"🩸 Leeches ({LEECH_THRESHOLD}+ wrong in a row):\n"]
    for card in leeches:
        lines.append(
            f"#{card['id']} — {utils.clip(card['question'], 80)} ({card['consecutive_again']}x)"
        )
    lines.append("\nConsider /suspend <id>, or rewrite it with /edit — a leech usually means")
    lines.append("the card is asking too much at once.")
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

    if card["stability"] is not None:
        memory = (
            f"Stability: {card['stability']:.1f}d · Difficulty: {card['difficulty']:.1f}/10\n"
            f"Interval: {card['interval_days']}d"
        )
    else:
        memory = "Not yet reviewed — memory state starts after your first answer."

    flags = []
    if card["suspended"]:
        flags.append("⏸ SUSPENDED")
    if card["consecutive_again"] >= LEECH_THRESHOLD:
        flags.append("⚠️ LEECH")
    if card["reverse_of"]:
        flags.append(f"↔️ reverse of #{card['reverse_of']}")
    flag_str = ("\n" + " · ".join(flags)) if flags else ""
    notes_str = f"\n📝 {utils.clip(card['notes'], 300)}" if card["notes"] else ""

    await update.message.reply_text(
        f"📇 Card #{card['id']}{flag_str}\n"
        f"Q: {utils.clip(card['question'], 300)}\n"
        f"A: {utils.clip(card['answer'], 300)}\n"
        f"Tags: {card['tags'] or 'none'}{notes_str}\n\n"
        f"{memory}\n"
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

    make_reverse = False
    for flag in ("--both", "--reverse"):
        if body.lower().endswith(flag):
            body = body[: -len(flag)].strip()
            make_reverse = True
            break

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
    tag_str = f" | 🏷 {tags}" if tags else ""
    if make_reverse:
        forward_id, reverse_id = db.add_card_with_reverse(question, answer, chat_id, tags=tags)
        await update.message.reply_text(
            f"✅ Cards #{forward_id} and #{reverse_id} added (both directions).{tag_str}"
        )
    else:
        card_id = db.add_card(question, answer, chat_id, tags=tags)
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
