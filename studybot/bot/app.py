import logging
import os
import sys
from datetime import time as dtime, timezone

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from studybot import db
from studybot.bot import commands, utils
from studybot.bot.callbacks import handle_callback
from studybot.bot.jobs import check_due_cards, daily_digest, weekly_report

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Error: TELEGRAM_BOT_TOKEN environment variable not set.")

    db.init_db()

    application = ApplicationBuilder().token(token).build()

    for name, handler in [
        ("start", commands.start_command),
        ("help", commands.help_command),
        ("review", commands.review_command),
        ("tags", commands.tags_command),
        ("search", commands.search_command),
        ("streak", commands.streak_command),
        ("forecast", commands.forecast_command),
        ("stats", commands.stats_command),
        ("goal", commands.goal_command),
        ("cap", commands.cap_command),
        ("window", commands.window_command),
        ("retention", commands.retention_command),
        ("exam", commands.exam_command),
        ("setdigest", commands.setdigest_command),
        ("dnd", commands.dnd_command),
        ("pomodoro", commands.pomodoro_command),
        ("edit", commands.edit_command),
        ("note", commands.note_command),
        ("delete", commands.delete_command),
        ("suspend", commands.suspend_command),
        ("unsuspend", commands.unsuspend_command),
        ("suspended", commands.suspended_command),
        ("bury", commands.bury_command),
        ("undo", commands.undo_command),
        ("leeches", commands.leeches_command),
        ("backup", commands.backup_command),
        ("card", commands.card_command),
    ]:
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, commands.handle_add_card))
    application.add_handler(MessageHandler(filters.PHOTO, commands.handle_add_photo))
    application.add_handler(CallbackQueryHandler(handle_callback))

    application.job_queue.run_repeating(check_due_cards, interval=300, first=10)

    saved_digest = db.get_global_setting("digest_time_ist") or "13:00"
    dh, dm = map(int, saved_digest.split(":"))
    h_utc, m_utc = utils.ist_to_utc(dh, dm)
    application.job_queue.run_daily(
        daily_digest,
        time=dtime(hour=h_utc, minute=m_utc, tzinfo=timezone.utc),
        name="daily_digest",
    )

    wr_h, wr_m = utils.ist_to_utc(19, 0)
    application.job_queue.run_daily(
        weekly_report,
        time=dtime(hour=wr_h, minute=wr_m, tzinfo=timezone.utc),
        name="weekly_report",
    )

    logger.info("Bot started. Polling for updates...")
    application.run_polling()


if __name__ == "__main__":
    main()
