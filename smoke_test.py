"""Offline smoke test. No API keys, no network, no Telegram.

Exercises the database, the metrics maths, and layer 1 of the gate. Layer 2 is
stubbed, because the point of layer 1 is that it works without a model.

    python smoke_test.py
"""
import logging
import os
import sys
import tempfile

# Load .env before stubbing, or --live can never see the real key: setdefault
# would win, and python-dotenv does not overwrite what is already set.
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")  # --live needs a real one
os.environ["TRANSCRIBE_PROVIDER"] = "none"  # tests never call Whisper
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="content-engine-test-")

logging.disable(logging.CRITICAL)  # the fail-closed test deliberately raises

import config  # noqa: E402
import db  # noqa: E402
import gate  # noqa: E402
import llm  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def test_gate_layer1():
    print("layer 1 (deterministic, no model)")
    blocked = [
        "We closed the deal at 40k EUR for the year.",
        "Our MRR crossed a threshold I am not going to name.",
        "The term sheet came back with a 20 percent option pool.",
        "This is confidential, do not share.",
        "The key is sk-abcdefghijklmnop1234 and it is in the env.",
    ]
    for text in blocked:
        hits = gate.scan(text)
        hard = [h for h in hits if h.severity == "hard"]
        check(f"blocks: {text[:40]}...", bool(hard),
              f"-> {[h.category for h in hits]}")

    clean = [
        "The gate has to be deterministic before the model sees anything. "
        "If you let the classifier run first you have already leaked the text "
        "to a third party, and you cannot un-send that.",
        "I spent two days on a scheduler I did not need. One cron and a sqlite "
        "file would have done it. The lesson is that queue brokers are a way to "
        "feel productive without shipping.",
    ]
    for text in clean:
        hits = [h for h in gate.scan(text) if h.severity == "hard"]
        check(f"passes: {text[:40]}...", not hits, f"-> {[h.category for h in hits]}")


def test_gate_fails_closed():
    print("layer 2 fails closed")
    original = llm.classify_confidentiality
    try:
        llm.classify_confidentiality = lambda text: (_ for _ in ()).throw(RuntimeError("boom"))
        status, reason = gate.run("A perfectly ordinary sentence about building things.")
        check("classifier error holds the material", status == gate.FLAGGED, f"-> {status}")

        llm.classify_confidentiality = lambda text: llm.GateVerdict(
            reveals_nonpublic=False, reason="nothing here"
        )
        status, _ = gate.run("A perfectly ordinary sentence about building things.")
        check("clean text clears", status == gate.CLEAR, f"-> {status}")

        # Hard hit must never reach layer 2 at all.
        called = []
        llm.classify_confidentiality = lambda text: called.append(text)
        status, _ = gate.run("Our ARR is not something I discuss.")
        check("hard hit blocks", status == gate.BLOCKED, f"-> {status}")
        check("hard hit never reaches the model", not called)
    finally:
        llm.classify_confidentiality = original


def test_db_and_metrics():
    print("database and metrics")
    db.init()
    sid = db.add_substance("text", raw_text="I rewrote the gate three times.")
    db.set_gate(sid, gate.CLEAR, "")
    check("substance stored", db.get_substance(sid)["raw_text"].startswith("I rewrote"))
    check("not draftable until shared", db.awaiting_drafts() == [])
    db.set_shareable(sid, 1)
    check("draftable after share", len(db.awaiting_drafts()) == 1)

    d1 = db.add_draft(sid, "build detail", "The gate is regex first. " * 10)
    d2 = db.add_draft(sid, "contrarian", "Everyone runs the classifier first. " * 10)
    db.mark_drafted(sid, "ok")

    db.set_draft_status(d1, "approved")
    db.mark_shipped(d1)
    check("clean approval is a voice pass", db.edit_ratio(db.get_draft(d1)) == 0.0)

    db.set_final_body(d2, "Completely different text with nothing in common at all.")
    check("heavy edit is not a voice pass", db.edit_ratio(db.get_draft(d2)) > 0.10)

    refused = db.add_substance("text", raw_text="had a good day")
    db.mark_drafted(refused, "not_enough_substance")

    qid = db.add_question("Why deterministic first?", str(sid))
    answer = db.add_substance("voice", transcript="Because you cannot un-send text.")
    db.answer_question(qid, answer)
    check("answer links to question",
          db.get_substance(answer)["question_id"] == qid)
    check("answer is tagged as interview",
          db.get_substance(answer)["source"] == "interview")

    db.add_signal(d1, "An investor replied to this one.")

    m = db.metrics()
    check("voice-pass-rate", abs(m["voice_pass_rate"] - 0.5) < 1e-9, f"-> {m['voice_pass_rate']}")
    check("answer rate", m["answer_rate"] == 1.0, f"-> {m['answer_rate']}")
    check("signals", m["signals"] == 1)
    check("refusal rate", abs(m["refusal_rate"] - 0.5) < 1e-9, f"-> {m['refusal_rate']}")


def test_prompts_render():
    print("prompts")
    for name in ("question", "draft", "video", "gate_classifier"):
        check(f"{name}.md exists", (config.PROMPTS_DIR / f"{name}.md").exists())
    rendered = llm._prompt("draft", n="4", language="English")
    check("placeholders substituted", "{{" not in rendered)
    check("refusal instruction survives", "enough_substance to false" in rendered)


def test_wiring():
    """Build the real Application offline: handlers and jobs must register."""
    print("wiring")
    import bot
    import worker
    from telegram.ext import Application

    app = Application.builder().token("123456:AAquiteFakeTokenButWellFormed_00000000000").build()
    bot.register(app)
    worker.register(app)

    commands = set()
    for group in app.handlers.values():
        for handler in group:
            commands |= set(getattr(handler, "commands", None) or [])
    for expected in ("drop", "review", "ask", "metrics", "ship", "signal", "cancel"):
        check(f"/{expected} registered", expected in commands)

    jobs = {j.name for j in app.job_queue.jobs()}
    for expected in ("sweep_drafts", "daily_question", "daily_review", "catch_up"):
        check(f"job {expected}", expected in jobs, f"-> {jobs}")

    row = db.get_draft(1)
    rendered = bot.render_draft(row)
    check("draft renders with a copy block", "<pre>" in rendered and "#1" in rendered)
    check("gate keyboard offers override on a block",
          "Override" in str(bot.gate_keyboard(1, gate.BLOCKED).inline_keyboard))
    check("gate keyboard is plain Keep when clear",
          "Override" not in str(bot.gate_keyboard(1, gate.CLEAR).inline_keyboard))


def test_pipeline():
    """Ingest through to drafts, with Claude and Telegram stubbed out."""
    print("pipeline")
    import asyncio
    import types

    import bot
    import worker

    sent = []

    class FakeMessage:
        def __init__(self, text=None, reply_to=None):
            self.text = text
            self.caption = None
            self.message_id = 999
            self.reply_to_message = reply_to
            self.forward_origin = None

        async def reply_text(self, text, **kw):
            sent.append(text)
            return self

        async def edit_text(self, text, **kw):
            sent.append(text)
            return self

    def fake_update(message):
        return types.SimpleNamespace(effective_message=message, message=message)

    original_classify = llm.classify_confidentiality
    original_drafts = llm.generate_drafts
    try:
        llm.classify_confidentiality = lambda t: llm.GateVerdict(
            reveals_nonpublic=False, reason="fine"
        )
        before = db.one("SELECT MAX(id) m FROM substance")["m"]
        asyncio.run(bot.ingest(fake_update(FakeMessage("I rewrote the gate three times.")), "text",
                               raw_text="I rewrote the gate three times."))
        row = db.one("SELECT * FROM substance WHERE id > ? ORDER BY id DESC LIMIT 1", (before,))
        check("ingest stored substance", row is not None)
        check("ingest ran the gate", row["gate_status"] == gate.CLEAR, f"-> {row['gate_status']}")
        check("ingest leaves it unshareable", row["shareable"] == 0)
        check("user was told", any("captured" in s.lower() for s in sent))

        # A blocked drop must say so and must not become shareable.
        sent.clear()
        asyncio.run(bot.ingest(fake_update(FakeMessage("We closed at 40k EUR.")), "text",
                               raw_text="We closed at 40k EUR."))
        blocked = db.one("SELECT * FROM substance ORDER BY id DESC LIMIT 1")
        check("money is blocked on ingest", blocked["gate_status"] == gate.BLOCKED)
        check("block is surfaced", any("BLOCKED" in s for s in sent))

        # Sweep: refusal, then a real generation.
        db.set_shareable(row["id"], 1)
        llm.generate_drafts = lambda text, n=None: llm.DraftSet(
            enough_substance=False, posts=[], refusal_reason="no detail in there"
        )
        result, summary = worker._draft_one(db.get_substance(row["id"]))
        check("refusal is recorded", result == "not_enough_substance", f"-> {summary}")
        check("refused substance is not retried", db.awaiting_drafts() == [])

        sid2 = db.add_substance("text", raw_text="The regex runs before the classifier.")
        db.set_gate(sid2, gate.CLEAR, "")
        db.set_shareable(sid2, 1)
        llm.generate_drafts = lambda text, n=None: llm.DraftSet(
            enough_substance=True,
            posts=[llm.Post(angle="build detail", specific_detail="regex first", body="A post.")],
        )
        result, _ = worker._draft_one(db.get_substance(sid2))
        check("drafts are created", result == "ok" and len(db.drafts_for_substance(sid2)) == 1)

        # Generator errors must not wedge the queue.
        sid3 = db.add_substance("text", raw_text="Something else entirely.")
        db.set_gate(sid3, gate.CLEAR, "")
        db.set_shareable(sid3, 1)
        llm.generate_drafts = lambda text, n=None: (_ for _ in ()).throw(RuntimeError("api down"))
        result, _ = worker._draft_one(db.get_substance(sid3))
        check("generator error is contained", result == "error")
        check("errored substance leaves the queue", db.awaiting_drafts() == [])
    finally:
        llm.classify_confidentiality = original_classify
        llm.generate_drafts = original_drafts


def test_live():
    """Real API calls. Costs a few cents. Run with: python smoke_test.py --live"""
    print("live (real Claude calls)")
    logging.disable(logging.NOTSET)
    # force=True: building the Application in test_wiring already put a handler
    # on the root logger, which makes a plain basicConfig a silent no-op.
    logging.basicConfig(level=logging.INFO, format="  %(message)s", force=True)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    verdict = llm.classify_confidentiality(
        "I rewrote the confidentiality gate three times before I got the order right. "
        "The regex has to run before the classifier, because the moment you send text "
        "to a model to ask whether it is safe to send, you have already sent it."
    )
    check("classifier returns a verdict", isinstance(verdict.reveals_nonpublic, bool),
          f"-> {verdict}")
    print(f"       reason: {verdict.reason}")

    thin = llm.generate_drafts("Good day. Made progress on the thing. Feeling positive.")
    check("thin substance is refused", not thin.enough_substance,
          f"-> {len(thin.posts)} posts generated from nothing")
    print(f"       refusal: {thin.refusal_reason}")

    real = llm.generate_drafts(
        "I rewrote the confidentiality gate three times. The version that works runs a "
        "regex blocklist before the LLM classifier ever sees the text. Everyone builds it "
        "the other way round because the classifier is smarter. But the moment you send "
        "text to a model to ask whether it is safe to send, you have already sent it to a "
        "third party, and you cannot un-send it. The regex is dumber and it is the only "
        "part that can fail closed. It blocked 'we closed at 40k EUR' on the first run, "
        "which is exactly the sentence I would have posted at 11pm without thinking."
    )
    check("real substance produces posts", real.enough_substance and real.posts,
          f"-> {real.refusal_reason}")
    for post in real.posts[:2]:
        print(f"\n  --- {post.angle} ---\n  detail: {post.specific_detail}\n")
        print("\n".join("  " + line for line in post.body.splitlines()))

    if real.posts:
        joined = " ".join(p.body for p in real.posts)
        check("no em dashes", "—" not in joined)
        check("no announcement opener", "excited to announce" not in joined.lower())


def main():
    print(f"data dir: {config.DATA_DIR}\n")
    test_gate_layer1()
    test_gate_fails_closed()
    test_db_and_metrics()
    test_prompts_render()
    test_wiring()
    test_pipeline()
    if "--live" in sys.argv:
        if os.getenv("ANTHROPIC_API_KEY", "test") == "test":
            print("\n--live needs a real ANTHROPIC_API_KEY in the environment or .env")
            raise SystemExit(1)
        test_live()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {FAILURES}")
        raise SystemExit(1)
    print("all green")


if __name__ == "__main__":
    main()
