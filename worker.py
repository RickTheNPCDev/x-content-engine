"""Scheduled work. Runs on the bot's own job queue, no broker, no second process.

Three jobs: sweep cleared substance into drafts, ask one question a day, push
the review once a day. A restart catches up on anything today's schedule missed.
"""
import asyncio
import datetime as dt
import logging

from telegram import ForceReply
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

import bot
import config
import db
import llm

log = logging.getLogger(__name__)


def _today() -> str:
    return dt.datetime.now(config.TZ).date().isoformat()


def _owner_chat() -> int | None:
    if config.OWNER_CHAT_ID is None:
        log.warning("OWNER_CHAT_ID unset, skipping scheduled push")
    return config.OWNER_CHAT_ID


# --- drafting --------------------------------------------------------------

def _draft_one(row) -> tuple[str, str]:
    """Synchronous. Returns (result, human summary)."""
    sid = row["id"]
    text = db.substance_text(row)
    try:
        result = llm.generate_drafts(text)
    except Exception as exc:
        log.exception("draft generation failed for #%s", sid)
        db.mark_drafted(sid, "error")
        return "error", f"#{sid}: draft generation failed ({exc})."

    if not result.enough_substance:
        db.mark_drafted(sid, "not_enough_substance")
        reason = result.refusal_reason or "no detail only someone who did the work would know"
        return "not_enough_substance", f"#{sid}: NOT_ENOUGH_SUBSTANCE. {reason}"

    for post in result.posts:
        db.add_draft(sid, post.angle, post.body)
    db.mark_drafted(sid, "ok")
    return "ok", f"#{sid}: {len(result.posts)} drafts ready. /review"


async def sweep_drafts(context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await asyncio.to_thread(db.awaiting_drafts, 3)
    if not rows:
        return
    chat = _owner_chat()
    for row in rows:
        _, summary = await asyncio.to_thread(_draft_one, row)
        log.info("sweep: %s", summary)
        if chat is not None:
            await context.bot.send_message(chat, summary)


# --- daily question --------------------------------------------------------

async def daily_question(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = _owner_chat()
    if chat is None:
        return
    try:
        qid, text = await asyncio.to_thread(bot.build_question)
    except Exception:
        log.exception("daily question failed")
        return
    sent = await context.bot.send_message(
        chat,
        f"<b>Question #{qid}</b>\n\n{bot.esc(text)}\n\nReply with a voice note.",
        parse_mode=ParseMode.HTML, reply_markup=ForceReply(selective=True),
    )
    await asyncio.to_thread(db.set_question_message, qid, sent.message_id)
    await asyncio.to_thread(db.kv_set, "last_question_date", _today())


# --- daily review ----------------------------------------------------------

async def daily_review(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = _owner_chat()
    if chat is None:
        return
    rows = await asyncio.to_thread(db.proposed_drafts, config.REVIEW_BATCH)
    await asyncio.to_thread(db.kv_set, "last_review_date", _today())
    if not rows:
        return  # silence beats a daily nag
    await context.bot.send_message(chat, f"{len(rows)} drafts for review.")
    for row in rows:
        await bot.send_draft(context.bot, chat, row)


# --- catch-up --------------------------------------------------------------

async def catch_up(context: ContextTypes.DEFAULT_TYPE) -> None:
    """After a restart, run any daily job whose slot passed today without firing."""
    now = dt.datetime.now(config.TZ)
    today = _today()
    for key, hour, job in (
        ("last_question_date", config.QUESTION_HOUR, daily_question),
        ("last_review_date", config.REVIEW_HOUR, daily_review),
    ):
        last = await asyncio.to_thread(db.kv_get, key)
        if last != today and now.hour >= hour:
            log.info("catch-up: running %s (last run %s)", job.__name__, last)
            await job(context)


# --- registration ----------------------------------------------------------

def register(app: Application) -> None:
    jq = app.job_queue
    jq.run_repeating(
        sweep_drafts,
        interval=dt.timedelta(minutes=config.DRAFT_SWEEP_MINUTES),
        first=dt.timedelta(seconds=30),
        name="sweep_drafts",
    )
    jq.run_daily(
        daily_question, time=dt.time(hour=config.QUESTION_HOUR, tzinfo=config.TZ),
        name="daily_question",
    )
    jq.run_daily(
        daily_review, time=dt.time(hour=config.REVIEW_HOUR, tzinfo=config.TZ),
        name="daily_review",
    )
    jq.run_once(catch_up, when=dt.timedelta(seconds=10), name="catch_up")
