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

    application.add_handler(CommandHandler("start", commands.start_command))
    application.add_handler(CommandHandler("help", commands.help_command))
    application.add_handler(CommandHandler("review", commands.review_command))
    application.add_handler(CommandHandler("tags", commands.tags_command))
    application.add_handler(CommandHandler("search", commands.search_command))
    application.add_handler(CommandHandler("streak", commands.streak_command))
    application.add_handler(CommandHandler("goal", commands.goal_command))
    application.add_handler(CommandHandler("setdigest", commands.setdigest_command))
    application.add_handler(CommandHandler("dnd", commands.dnd_command))
    application.add_handler(CommandHandler("pomodoro", commands.pomodoro_command))
    application.add_handler(CommandHandler("edit", commands.edit_command))
    application.add_handler(CommandHandler("delete", commands.delete_command))
    application.add_handler(CommandHandler("undo", commands.undo_command))
    application.add_handler(CommandHandler("leeches", commands.leeches_command))
    application.add_handler(CommandHandler("backup", commands.backup_command))
    application.add_handler(CommandHandler("card", commands.card_command))
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
