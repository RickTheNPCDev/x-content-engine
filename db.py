"""SQLite access. Thin, synchronous, no ORM.

Callers on the asyncio event loop should wrap multi-step work in
asyncio.to_thread; the connection is shared under a lock.
"""
import difflib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import config

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    global _conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript((config.ROOT / "schema.sql").read_text(encoding="utf-8"))
    _conn.commit()


def _c() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db.init() has not run")
    return _conn


def q(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _c().execute(sql, params).fetchall()


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def write(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = _c().execute(sql, params)
        _c().commit()
        return cur.lastrowid


# --- substance -------------------------------------------------------------

def add_substance(source: str, raw_text: str | None = None, transcript: str | None = None,
                  audio_path: str | None = None, question_id: int | None = None) -> int:
    return write(
        "INSERT INTO substance (created_at, source, raw_text, transcript, audio_path, question_id)"
        " VALUES (?,?,?,?,?,?)",
        (now(), source, raw_text, transcript, audio_path, question_id),
    )


def get_substance(sid: int):
    return one("SELECT * FROM substance WHERE id=?", (sid,))


def set_transcript(sid: int, transcript: str) -> None:
    write("UPDATE substance SET transcript=? WHERE id=?", (transcript, sid))


def set_gate(sid: int, status: str, reason: str | None) -> None:
    write("UPDATE substance SET gate_status=?, gate_reason=? WHERE id=?", (status, reason, sid))


def set_shareable(sid: int, value: int) -> None:
    write("UPDATE substance SET shareable=? WHERE id=?", (value, sid))


def mark_drafted(sid: int, result: str) -> None:
    write("UPDATE substance SET drafted_at=?, draft_result=? WHERE id=?", (now(), result, sid))


def substance_text(row) -> str:
    """What the model reads. Transcript first, then any typed text."""
    parts = [p for p in (row["transcript"], row["raw_text"]) if p and p.strip()]
    return "\n\n".join(parts)


def awaiting_drafts(limit: int = 5) -> list:
    return q(
        "SELECT * FROM substance"
        " WHERE shareable=1 AND gate_status IN ('clear','overridden') AND drafted_at IS NULL"
        " ORDER BY id ASC LIMIT ?",
        (limit,),
    )


def recent_substance(days: int, limit: int = 40) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return q(
        "SELECT * FROM substance WHERE created_at >= ? AND gate_status != 'blocked'"
        " ORDER BY id DESC LIMIT ?",
        (since, limit),
    )


# --- question --------------------------------------------------------------

def add_question(text: str, context_ids: str) -> int:
    return write("INSERT INTO question (asked_at, text, context_ids) VALUES (?,?,?)",
                 (now(), text, context_ids))


def set_question_message(qid: int, message_id: int) -> None:
    write("UPDATE question SET message_id=? WHERE id=?", (message_id, qid))


def question_by_message(message_id: int):
    return one("SELECT * FROM question WHERE message_id=?", (message_id,))


def open_question(hours: int = 48):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return one(
        "SELECT * FROM question WHERE answered_substance_id IS NULL AND asked_at >= ?"
        " ORDER BY id DESC LIMIT 1",
        (since,),
    )


def answer_question(qid: int, sid: int) -> None:
    write("UPDATE question SET answered_substance_id=? WHERE id=?", (sid, qid))
    write("UPDATE substance SET question_id=?, source='interview' WHERE id=?", (qid, sid))


def recent_questions(limit: int = 10) -> list:
    return q("SELECT * FROM question ORDER BY id DESC LIMIT ?", (limit,))


# --- draft -----------------------------------------------------------------

def add_draft(substance_id: int, angle: str, body: str, platform: str = "linkedin") -> int:
    return write(
        "INSERT INTO draft (created_at, substance_id, platform, angle, body) VALUES (?,?,?,?,?)",
        (now(), substance_id, platform, angle, body),
    )


def get_draft(did: int):
    return one("SELECT * FROM draft WHERE id=?", (did,))


def set_draft_status(did: int, status: str) -> None:
    write("UPDATE draft SET status=?, reviewed_at=? WHERE id=?", (status, now(), did))


def set_final_body(did: int, body: str) -> None:
    write("UPDATE draft SET final_body=?, status='approved', reviewed_at=? WHERE id=?",
          (body, now(), did))


def set_video_script(did: int, script: str) -> None:
    write("UPDATE draft SET video_flag=1, video_script=? WHERE id=?", (script, did))


def mark_shipped(did: int) -> None:
    write("UPDATE draft SET status='shipped', shipped_at=? WHERE id=?", (now(), did))


def proposed_drafts(limit: int) -> list:
    return q("SELECT * FROM draft WHERE status='proposed' ORDER BY id ASC LIMIT ?", (limit,))


def drafts_for_substance(sid: int) -> list:
    return q("SELECT * FROM draft WHERE substance_id=? ORDER BY id", (sid,))


def final_text(row) -> str:
    return row["final_body"] or row["body"]


def edit_ratio(row) -> float:
    """0.0 means shipped exactly as generated. 1.0 means fully rewritten."""
    if not row["final_body"]:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, row["body"], row["final_body"]).ratio()


# --- signal ----------------------------------------------------------------

def add_signal(draft_id: int | None, note: str) -> int:
    return write("INSERT INTO signal (created_at, draft_id, note) VALUES (?,?,?)",
                 (now(), draft_id, note))


def signals(limit: int = 20) -> list:
    return q("SELECT * FROM signal ORDER BY id DESC LIMIT ?", (limit,))


# --- kv --------------------------------------------------------------------

def kv_get(key: str) -> str | None:
    row = one("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    write(
        "INSERT INTO kv (key, value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --- metrics ---------------------------------------------------------------

def metrics() -> dict:
    reviewed = q("SELECT * FROM draft WHERE status IN ('approved','shipped')")
    clean = [d for d in reviewed if edit_ratio(d) < 0.10]
    asked = one("SELECT COUNT(*) n FROM question")["n"]
    answered = one("SELECT COUNT(*) n FROM question WHERE answered_substance_id IS NOT NULL")["n"]
    drafted = one("SELECT COUNT(*) n FROM substance WHERE drafted_at IS NOT NULL")["n"]
    refused = one("SELECT COUNT(*) n FROM substance WHERE draft_result='not_enough_substance'")["n"]
    return {
        "approved_or_shipped": len(reviewed),
        "voice_pass": len(clean),
        "voice_pass_rate": (len(clean) / len(reviewed)) if reviewed else None,
        "questions_asked": asked,
        "questions_answered": answered,
        "answer_rate": (answered / asked) if asked else None,
        "signals": one("SELECT COUNT(*) n FROM signal")["n"],
        "substance_drafted": drafted,
        "refusals": refused,
        "refusal_rate": (refused / drafted) if drafted else None,
        "proposed": one("SELECT COUNT(*) n FROM draft WHERE status='proposed'")["n"],
        "killed": one("SELECT COUNT(*) n FROM draft WHERE status='killed'")["n"],
        "shipped": one("SELECT COUNT(*) n FROM draft WHERE status='shipped'")["n"],
    }
