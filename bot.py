"""Telegram handlers. The only interface this thing has.

Nothing here talks to Claude directly except through llm.py, and nothing reaches
the draft generator without the gate clearing it and you tapping Keep.
"""
import asyncio
import html
import logging
import pathlib

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import config
import db
import gate
import llm
import transcribe

log = logging.getLogger(__name__)

MAX_MSG = 3500  # Telegram caps at 4096; leave room for the wrapper


# --- helpers ---------------------------------------------------------------

def esc(text: str) -> str:
    return html.escape(text or "")


def clip(text: str, limit: int = MAX_MSG) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[...truncated, full text is in the database]"


def is_owner(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if config.OWNER_CHAT_ID is None:
        log.warning("OWNER_CHAT_ID not set - your chat id is %s. Set it and restart.", chat.id)
        return False
    return chat.id == config.OWNER_CHAT_ID


async def guard(update: Update) -> bool:
    if is_owner(update):
        return True
    if update.effective_chat and config.OWNER_CHAT_ID is None:
        await update.effective_chat.send_message(
            f"OWNER_CHAT_ID is not set. Your chat id is {update.effective_chat.id}. "
            "Put it in the environment and restart me."
        )
    return False


def gate_keyboard(sid: int, status: str) -> InlineKeyboardMarkup:
    if status == gate.BLOCKED:
        share = InlineKeyboardButton("Override and keep", callback_data=f"o:{sid}")
    elif status == gate.FLAGGED:
        share = InlineKeyboardButton("Keep anyway", callback_data=f"o:{sid}")
    else:
        share = InlineKeyboardButton("Keep", callback_data=f"s:{sid}")
    return InlineKeyboardMarkup([[share, InlineKeyboardButton("Don't keep", callback_data=f"p:{sid}")]])


def draft_keyboard(did: int, status: str) -> InlineKeyboardMarkup:
    if status == "proposed":
        rows = [
            [InlineKeyboardButton("Approve", callback_data=f"a:{did}"),
             InlineKeyboardButton("Edit", callback_data=f"e:{did}"),
             InlineKeyboardButton("Kill", callback_data=f"k:{did}")],
            [InlineKeyboardButton("Video script", callback_data=f"v:{did}")],
        ]
    else:
        rows = [[InlineKeyboardButton("Mark shipped", callback_data=f"h:{did}"),
                 InlineKeyboardButton("Video script", callback_data=f"v:{did}")]]
    return InlineKeyboardMarkup(rows)


def render_draft(row) -> str:
    header = f"<b>#{row['id']}</b> · {esc(row['platform'])} · {esc(row['angle'] or '')}"
    body = clip(db.final_text(row))
    status = "" if row["status"] == "proposed" else f"\nstatus: {esc(row['status'])}"
    return f"{header}{status}\n\n<pre>{esc(body)}</pre>"


async def send_draft(bot, chat_id: int, row) -> None:
    await bot.send_message(
        chat_id, render_draft(row), parse_mode=ParseMode.HTML,
        reply_markup=draft_keyboard(row["id"], row["status"]),
    )


# --- ingest ----------------------------------------------------------------

async def ingest(update: Update, source: str, raw_text: str | None = None,
                 audio_path: pathlib.Path | None = None) -> None:
    """One path in for everything: text, voice, document, forward."""
    message = update.effective_message
    stored_path = str(audio_path) if audio_path else None
    sid = await asyncio.to_thread(db.add_substance, source, raw_text, None, stored_path)

    # A reply to the daily question is an interview answer.
    replied = message.reply_to_message
    question = None
    if replied:
        question = await asyncio.to_thread(db.question_by_message, replied.message_id)
    if question is None and source == "voice":
        question = await asyncio.to_thread(db.open_question, 48)
    if question is not None:
        await asyncio.to_thread(db.answer_question, question["id"], sid)

    note = await message.reply_text(f"Captured #{sid}. Running the gate.")

    if audio_path:
        transcript = await asyncio.to_thread(transcribe.transcribe, audio_path)
        if transcript:
            await asyncio.to_thread(db.set_transcript, sid, transcript)
        else:
            await note.edit_text(
                f"Captured #{sid}, but transcription came back empty. "
                "The audio is stored. Send the gist as text and I will gate that."
            )
            return

    row = await asyncio.to_thread(db.get_substance, sid)
    text = db.substance_text(row)
    if not text.strip():
        await note.edit_text(f"Captured #{sid}, but there is no text to work with.")
        return

    status, reason = await asyncio.to_thread(gate.run, text)
    await asyncio.to_thread(db.set_gate, sid, status, reason)

    # Every piece is clipped before it is wrapped in a tag. Never clip the
    # assembled message: a cut inside <pre> loses the closing tag and Telegram
    # rejects the whole thing.
    lines = [f"<b>#{sid}</b> captured ({esc(source)})."]
    if question is not None:
        lines.append(f"Logged as the answer to question #{question['id']}.")
    if status == gate.CLEAR:
        lines.append("Gate: clear.")
    else:
        headline = ("Gate: <b>flagged</b>." if status == gate.FLAGGED else
                    "Gate: <b>BLOCKED</b>. This cannot reach a draft without an override.")
        lines.append(headline)
        lines.append(f"<pre>{esc(clip(reason, 1500))}</pre>")
    if row["transcript"]:
        lines.append(f"\nTranscript:\n<pre>{esc(clip(row['transcript'], 1200))}</pre>")

    await note.edit_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=gate_keyboard(sid, status),
    )


# --- commands --------------------------------------------------------------

HELP = """<b>Content engine</b>

/drop &lt;text&gt; - capture. Plain messages, voice notes and forwards work too.
/review - push the proposed drafts now
/drafts - same thing, shorter
/ask - generate an interview question now
/pending - substance waiting on a share decision
/ship &lt;id&gt; - mark a draft shipped
/signal &lt;id&gt; &lt;note&gt; - log who reacted and what came in
/cancel - drop a pending edit
/context [text] - show the standing context, or append a line to it
/corpus - voice corpus stats
/metrics - the four numbers
/reloadgate - recompile blocklist.yml

Nothing is drafted until you tap Keep. A blocklist hit needs a conscious override."""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def cmd_drop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    if not text.strip():
        await update.message.reply_text("Give me something: /drop <text>, or just send the message.")
        return
    await ingest(update, "text", raw_text=text)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    editing = context.user_data.pop("editing", None)
    if editing is not None:
        await apply_edit(update, editing)
        return
    source = "forward" if update.message.forward_origin else "text"
    await ingest(update, source, raw_text=update.message.text)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    media = update.message.voice or update.message.audio
    tg_file = await context.bot.get_file(media.file_id)
    suffix = ".ogg" if update.message.voice else ".m4a"
    path = config.AUDIO_DIR / f"{media.file_unique_id}{suffix}"
    await tg_file.download_to_drive(custom_path=path)
    await ingest(update, "voice", raw_text=update.message.caption, audio_path=path)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    doc = update.message.document
    name = doc.file_name or "file"
    text = update.message.caption or ""
    readable = (doc.mime_type or "").startswith("text/") or name.endswith((".md", ".txt"))
    if readable and doc.file_size and doc.file_size < 2_000_000:
        tg_file = await context.bot.get_file(doc.file_id)
        raw = bytes(await tg_file.download_as_bytearray())
        text = (text + "\n\n" + raw.decode("utf-8", errors="replace")).strip()
    else:
        text = f"[attached file: {name}]\n{text}".strip()
    await ingest(update, "document", raw_text=text)


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    rows = await asyncio.to_thread(db.proposed_drafts, config.REVIEW_BATCH)
    if not rows:
        await update.message.reply_text("Nothing proposed. Drop something and tap Keep.")
        return
    for row in rows:
        await send_draft(context.bot, update.effective_chat.id, row)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text("Thinking of a question.")
    try:
        qid, text = await asyncio.to_thread(build_question)
    except Exception:
        log.exception("question generation failed")
        await update.message.reply_text("Question generation failed. Check the logs.")
        return
    sent = await update.message.reply_text(
        f"<b>Question #{qid}</b>\n\n{esc(text)}\n\nReply with a voice note.",
        parse_mode=ParseMode.HTML, reply_markup=ForceReply(selective=True),
    )
    await asyncio.to_thread(db.set_question_message, qid, sent.message_id)


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    rows = await asyncio.to_thread(
        db.q, "SELECT * FROM substance WHERE shareable=0 AND drafted_at IS NULL ORDER BY id DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("Nothing pending.")
        return
    for row in rows:
        preview = clip(db.substance_text(row), 400)
        await update.message.reply_text(
            f"<b>#{row['id']}</b> · {esc(row['source'])} · gate: {esc(row['gate_status'])}\n"
            f"<pre>{esc(preview)}</pre>",
            parse_mode=ParseMode.HTML, reply_markup=gate_keyboard(row["id"], row["gate_status"]),
        )


async def cmd_ship(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /ship <draft id>")
        return
    did = int(context.args[0])
    if not await asyncio.to_thread(db.get_draft, did):
        await update.message.reply_text(f"No draft #{did}.")
        return
    await asyncio.to_thread(db.mark_shipped, did)
    await update.message.reply_text(f"#{did} shipped. Log what comes back with /signal {did} <note>.")


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    args = list(context.args or [])
    did = None
    if args and args[0].isdigit():
        did = int(args.pop(0))
    note = " ".join(args).strip()
    if not note:
        await update.message.reply_text("Usage: /signal <draft id> <who reacted, what came in>")
        return
    sig = await asyncio.to_thread(db.add_signal, did, note)
    await update.message.reply_text(f"Signal #{sig} logged.")


async def cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    line = " ".join(context.args or []).strip()
    if line:
        with config.CONTEXT_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n- {line}\n")
        await update.message.reply_text("Added to context.")
        return
    text = llm.read_context() or "(empty)"
    await update.message.reply_text(f"<pre>{esc(clip(text))}</pre>", parse_mode=ParseMode.HTML)


async def cmd_corpus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    text = llm.read_corpus()
    passages = [p for p in text.split("\n---") if p.strip()]
    words = len(text.split())
    verdict = "Good." if 15 <= len(passages) <= 30 and words > 2000 else \
        "Thin. Aim for 15-25 passages of 100-400 words. Drafts are only as good as this."
    await update.message.reply_text(
        f"Voice corpus: {len(passages)} passages, {words} words.\n{verdict}"
    )


async def cmd_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    m = await asyncio.to_thread(db.metrics)
    sigs = await asyncio.to_thread(db.signals, 10)

    def pct(v):
        return "-" if v is None else f"{v * 100:.0f}%"

    lines = [
        "<b>Four numbers</b>",
        f"1. Voice-pass-rate: {pct(m['voice_pass_rate'])} "
        f"({m['voice_pass']}/{m['approved_or_shipped']} approved with under 10% edit)",
        f"2. Interview answer rate: {pct(m['answer_rate'])} "
        f"({m['questions_answered']}/{m['questions_asked']})",
        f"3. Leverage signals: {m['signals']}",
        f"4. Refusal rate: {pct(m['refusal_rate'])} "
        f"({m['refusals']}/{m['substance_drafted']} pieces of substance refused)",
        "",
        f"proposed {m['proposed']} · shipped {m['shipped']} · killed {m['killed']}",
    ]
    if m["refusal_rate"] == 0 and m["substance_drafted"] > 3:
        lines.append("\nRefusal rate is zero. The anti-slop instruction is not working.")
    if sigs:
        lines.append("\n<b>Signals</b>")
        for s in sigs:
            tag = f"#{s['draft_id']} " if s["draft_id"] else ""
            lines.append(f"- {tag}{esc(s['note'])}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    did = context.user_data.pop("editing", None)
    await update.message.reply_text(
        f"Edit of #{did} cancelled." if did else "Nothing to cancel."
    )


async def cmd_reloadgate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    n = await asyncio.to_thread(gate.load)
    await update.message.reply_text(f"Blocklist reloaded: {n} patterns.")


# --- callbacks -------------------------------------------------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Not your bot.")
        return
    await query.answer()
    verb, _, raw_id = query.data.partition(":")
    item_id = int(raw_id)

    if verb in ("s", "o", "p"):
        await handle_gate_action(query, verb, item_id)
    elif verb in ("a", "k"):
        await handle_draft_status(query, verb, item_id)
    elif verb == "e":
        context.user_data["editing"] = item_id
        await query.message.reply_text(
            f"Send the edited version of #{item_id}.", reply_markup=ForceReply(selective=True)
        )
    elif verb == "v":
        await handle_video(query, item_id)
    elif verb == "h":
        await asyncio.to_thread(db.mark_shipped, item_id)
        await query.message.reply_text(
            f"#{item_id} shipped. Log what comes back with /signal {item_id} <note>."
        )


async def handle_gate_action(query, verb: str, sid: int) -> None:
    row = await asyncio.to_thread(db.get_substance, sid)
    if row is None:
        await query.message.reply_text(f"No substance #{sid}.")
        return
    if verb == "p":
        await asyncio.to_thread(db.set_shareable, sid, 0)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"#{sid} not kept. It stays stored, but it will never be drafted.")
        return
    if verb == "o":
        await asyncio.to_thread(db.set_gate, sid, gate.OVERRIDDEN, row["gate_reason"])
    elif row["gate_status"] not in (gate.CLEAR, gate.OVERRIDDEN):
        # Never ran, usually because transcription failed and we bailed early.
        # Sharing it now without gating would leave it stuck out of the queue.
        status, reason = await asyncio.to_thread(gate.run, db.substance_text(row))
        await asyncio.to_thread(db.set_gate, sid, status, reason)
        if status != gate.CLEAR:
            await query.edit_message_reply_markup(reply_markup=gate_keyboard(sid, status))
            await query.message.reply_text(
                f"Gate ran on #{sid} and did not clear it.\n<pre>{esc(clip(reason, 1500))}</pre>",
                parse_mode=ParseMode.HTML,
            )
            return
    await asyncio.to_thread(db.set_shareable, sid, 1)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"#{sid} cleared for drafting. Drafts land within {config.DRAFT_SWEEP_MINUTES} minutes."
    )


async def handle_draft_status(query, verb: str, did: int) -> None:
    status = "approved" if verb == "a" else "killed"
    await asyncio.to_thread(db.set_draft_status, did, status)
    row = await asyncio.to_thread(db.get_draft, did)
    if status == "killed":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"#{did} killed.")
        return
    await query.edit_message_reply_markup(reply_markup=draft_keyboard(did, "approved"))
    await query.message.reply_text(
        f"#{did} approved. Copy it above, paste it, then tap Mark shipped.\n\n"
        f"<pre>{esc(clip(db.final_text(row)))}</pre>",
        parse_mode=ParseMode.HTML,
    )


async def handle_video(query, did: int) -> None:
    draft = await asyncio.to_thread(db.get_draft, did)
    if draft is None:
        await query.message.reply_text(f"No draft #{did}.")
        return
    if draft["video_script"]:
        await query.message.reply_text(
            f"<b>Video #{did}</b>\n\n<pre>{esc(clip(draft['video_script']))}</pre>",
            parse_mode=ParseMode.HTML,
        )
        return

    substance = await asyncio.to_thread(db.get_substance, draft["substance_id"])
    source = substance["transcript"] or substance["raw_text"] or ""
    if not source.strip():
        await query.message.reply_text("No source text for a script.")
        return
    warning = ""
    if not substance["transcript"]:
        warning = ("\n\nNote: this came from typed text, not a voice note. "
                   "Scripts trimmed from you actually saying it sound better.")

    await query.message.reply_text("Trimming the transcript.")
    try:
        script = await asyncio.to_thread(llm.generate_video_script, source)
    except Exception:
        log.exception("video script failed")
        await query.message.reply_text("Video script generation failed. Check the logs.")
        return
    await asyncio.to_thread(db.set_video_script, did, script)
    await query.message.reply_text(
        f"<b>Video #{did}</b>\n\n<pre>{esc(clip(script))}</pre>{esc(warning)}",
        parse_mode=ParseMode.HTML,
    )


async def apply_edit(update: Update, did: int) -> None:
    body = update.message.text
    row = await asyncio.to_thread(db.get_draft, did)
    if row is None:
        await update.message.reply_text(f"No draft #{did}.")
        return
    await asyncio.to_thread(db.set_final_body, did, body)
    row = await asyncio.to_thread(db.get_draft, did)
    ratio = db.edit_ratio(row)
    await update.message.reply_text(
        f"#{did} updated and approved. Edit distance: {ratio * 100:.0f}%.\n"
        + ("That counts as a voice pass." if ratio < 0.10 else
           "Over 10%, so it does not count as a voice pass."),
    )


# --- question generation (shared with the scheduler) -----------------------

def build_question() -> tuple[int, str]:
    """Synchronous. Returns (question_id, text)."""
    rows = db.recent_substance(config.QUESTION_LOOKBACK_DAYS)
    if rows:
        material = "\n\n---\n\n".join(
            f"[#{r['id']} {r['created_at']} {r['source']}]\n{db.substance_text(r)}" for r in rows
        )
        ids = ",".join(str(r["id"]) for r in rows)
    else:
        material = "(nothing captured in the lookback window)"
        ids = ""
    asked = "\n".join(f"- {q['text']}" for q in db.recent_questions(10)) or "(none yet)"
    text = llm.generate_question(material, asked)
    qid = db.add_question(text, ids)
    return qid, text


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A handler that dies silently looks like a bot that ignored you."""
    log.exception("handler failed", exc_info=context.error)
    if config.OWNER_CHAT_ID is None:
        return
    try:
        await context.bot.send_message(
            config.OWNER_CHAT_ID,
            f"Something broke: {type(context.error).__name__}: {context.error}",
        )
    except Exception:
        log.exception("could not report the error either")


# --- registration ----------------------------------------------------------

def register(app: Application) -> None:
    # UpdateType.MESSAGE everywhere: without it an edit to an old message is
    # delivered again and gets re-ingested as new substance.
    fresh = filters.UpdateType.MESSAGE
    commands = (
        (["start", "help"], cmd_start),
        ("drop", cmd_drop),
        (["review", "drafts"], cmd_review),
        ("ask", cmd_ask),
        ("pending", cmd_pending),
        ("ship", cmd_ship),
        ("signal", cmd_signal),
        ("context", cmd_context),
        ("corpus", cmd_corpus),
        ("metrics", cmd_metrics),
        ("cancel", cmd_cancel),
        ("reloadgate", cmd_reloadgate),
    )
    for name, callback in commands:
        app.add_handler(CommandHandler(name, callback, filters=fresh))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(fresh & (filters.VOICE | filters.AUDIO), on_voice))
    app.add_handler(MessageHandler(fresh & filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(fresh & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
