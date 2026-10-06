"""One process: Telegram long polling plus the job queue. Start it and leave it."""
import logging
import shutil
import sys

from telegram import Update
from telegram.ext import Application

import bot
import config
import db
import gate
import worker

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger("content-engine")


def seed_context() -> None:
    """context.md is editable at runtime, so it lives in DATA_DIR, not the repo."""
    if config.CONTEXT_PATH.exists():
        return
    config.CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if config.CONTEXT_TEMPLATE_PATH.exists():
        shutil.copy(config.CONTEXT_TEMPLATE_PATH, config.CONTEXT_PATH)
    else:
        config.CONTEXT_PATH.write_text("", encoding="utf-8")


def main() -> None:
    problems = config.validate()
    if problems:
        for p in problems:
            log.error(p)
        sys.exit(1)

    db.init()
    seed_context()
    gate.load()

    if config.OWNER_CHAT_ID is None:
        log.warning("OWNER_CHAT_ID is not set. Message the bot and it will tell you your chat id.")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    bot.register(app)
    worker.register(app)

    storage = config.storage_warning()
    if storage:
        log.error(storage)
        if config.OWNER_CHAT_ID is not None:
            async def warn_owner(context) -> None:
                await context.bot.send_message(config.OWNER_CHAT_ID, f"Storage warning: {storage}")
            app.job_queue.run_once(warn_owner, when=5, name="storage_warning")

    log.info(
        "up. model=%s effort=%s transcribe=%s db=%s question=%02d:00 review=%02d:00 tz=%s",
        config.MODEL, config.EFFORT, config.TRANSCRIBE_PROVIDER, config.DB_PATH,
        config.QUESTION_HOUR, config.REVIEW_HOUR, config.TZ,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
