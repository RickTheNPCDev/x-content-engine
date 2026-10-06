# Content engine

A Telegram bot that interviews you, turns your answers and notes into LinkedIn and X
drafts written in your own voice, and blocks anything confidential before a
model sees it. It exists because generic AI posts read like everyone else's,
and the cure is your own words, your real details, and a draft generator
allowed to say "not enough substance".

One Python process, one SQLite file, one job queue. Telegram is the whole
interface. The reasoning behind every choice is in `METHODOLOGY.md`.

Nothing auto-publishes. You copy approved posts into LinkedIn by hand and log
signals by hand. That is deliberate for v1.

## The loop

```
you --drop--> substance --gate--> [you tap Keep]  --> drafts --review--> approve/edit/kill
                  ^                                                          |
                  |                                                          v
            voice note reply <--- daily question              copy-paste to LinkedIn
```

## Before it does anything useful

**1. Build your voice corpus: get interviewed by your agent.** This one hour
determines everything downstream. Open Claude Code (or any agent you chat with
locally), paste [`prompts/corpus_interview.md`](prompts/corpus_interview.md),
and answer its questions fast and without rereading. It asks one pointed
question at a time, pushes back so you have to defend your answer, and on
"done" writes `voice_corpus.md` for you: your words verbatim, typos kept, names
and figures scrubbed, every change listed. Target 15-25 passages of 100-400
words. `/corpus` tells you when it is still too thin.

Already have a lot of your own writing in Claude Code sessions? Run
`python mine_corpus.py` for a ranked, gate-checked shortlist
(`corpus_candidates.md`, gitignored, offline) and fill the gaps with the
interview. Full method in [`METHODOLOGY.md`](METHODOLOGY.md#building-the-voice-corpus).

> The corpus is not gated and is committed when you deploy from git. Once it
> holds your real text, deploy from a **private fork**. Never push it back here.

**2. Fill in `context.md`.** What you are building this month, what you have a real
position on, what you refuse to say publicly. The question generator reads it.
You can also append to it live with `/context <line>`.

**3. Add names to `blocklist.yml`.** v1 ships with structural patterns
only (money, cap table, deal terms, credentials). The `literals:` lists for
client, investor, and cofounder names are empty and waiting. Adding a name is
one line, no code change.

## Run it locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                            # then fill in the three keys
.venv/Scripts/python smoke_test.py              # offline, no keys needed
.venv/Scripts/python main.py
```

First run: leave `OWNER_CHAT_ID` blank, message the bot, and it replies with your
chat id. Put it in `.env` and restart. Every other chat is ignored.

## Deploy to Railway

1. Push your private fork, then **New Project → Deploy from GitHub**.
2. **Add a volume** and mount it at `/data`. You do not need a database
   service: the database is a SQLite file on that volume. Railway's filesystem
   is wiped on every deploy; without the volume you lose everything each push.
3. Variables: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `FAL_KEY`,
   `OWNER_CHAT_ID`, and `DATA_DIR=/data`. Set `TIMEZONE` (defaults to UTC).
4. There is no HTTP server here. It is a long-polling worker, so do not generate
   a domain and do not expect a health check to pass. `railway.json` sets the
   start command; the `Procfile` says the same thing.

Run exactly one instance. Two processes long-polling the same bot token will
fight over updates.

## Commands

| | |
|---|---|
| `/drop <text>` | capture. Plain messages, voice notes, forwards and text files work too |
| `/review`, `/drafts` | push proposed drafts now |
| `/ask` | generate an interview question now |
| `/pending` | substance still waiting on a share decision |
| `/ship <id>` | mark a draft shipped |
| `/signal <id> <note>` | log who reacted and what came in |
| `/context [text]` | show the standing context, or append a line |
| `/corpus` | voice corpus stats |
| `/metrics` | the four numbers |
| `/cancel` | drop a pending edit |
| `/reloadgate` | recompile `blocklist.yml` without a restart |

## The gate

Two layers, deterministic first, always.

**Layer 1** is regex from `blocklist.yml` and fails closed. A `hard` hit blocks
the substance outright, even if you already marked it shareable, and it never
reaches a model. The match is shown to you with 30 characters of context either
side so you can override it consciously, one item at a time. A `soft` hit flags
instead of blocking.

**Layer 2** is a Claude classifier that only ever sees material layer 1 cleared:
would publishing this reveal non-public commercial or technical information? If
the classifier errors or returns something unparsable, the material is held, not
passed. An error is not a pass.

`shareable` defaults to 0 in the schema. Nothing reaches the draft generator
without you tapping a button.

## The four numbers

`/metrics` computes them. Three come out of the database on their own:

1. **Voice-pass-rate.** Measured, not estimated: when you use the Edit button the
   engine stores the original alongside your version and computes the edit
   distance. Under 10% counts as a pass.
2. **Interview answer rate.** Voice notes replying to a question are linked back
   to it automatically.
3. **Leverage signals.** Named, not counted, via `/signal`. This is the one you
   log by hand, because the whole point is that it is a person, not a number.
4. **Refusal rate.** How often the draft generator returned
   `NOT_ENOUGH_SUBSTANCE`. If this stays at zero, `/metrics` says so out loud.
   A zero refusal rate means the anti-slop instruction is not working.

Follower count is not in here on purpose.

## Files

| | |
|---|---|
| `main.py` | entry point: validate config, open the db, register handlers and jobs, poll |
| `bot.py` | every Telegram handler |
| `worker.py` | three scheduled jobs plus restart catch-up |
| `gate.py` | the two-layer confidentiality gate |
| `llm.py` | every call to Claude, and nothing else |
| `transcribe.py` | fal (ElevenLabs Scribe), swappable for whisper.cpp or off |
| `db.py` | SQLite, no ORM |
| `schema.sql` | four tables (substance, question, draft, signal) plus a kv table for the scheduler |
| `prompts/` | question, draft, video and gate-classifier prompts, plus `corpus_interview.md` for building the corpus |
| `smoke_test.py` | offline checks; `--live` makes real API calls |
| `mine_corpus.py` | offline corpus shortlist from your Claude Code transcripts |

## Notes on the model calls

`claude-opus-5` with adaptive thinking at `high` effort, everywhere, including
the gate. The gate is the one part not to optimise for speed.

The voice corpus is the first system block on every draft call, so it sits at the
front of the cached prefix. Watch `cache_read=` in the logs. If it is always
zero, something volatile is getting in front of the corpus and you are paying
full price on every draft.

The draft generator returns structured output with an `enough_substance` boolean
rather than a magic string. Same anti-slop mechanism, one less thing to parse.

## What this deliberately does not do

Auto-publishing. Repurposing. X. Analytics. Multi-user. Notion sync. A web UI.
Each of those is a way to spend a week without learning whether
the drafts sound like you. Run it for two weeks first.

The video layer is wired but is a flag and a prompt, not a pipeline: tap **Video
script** on any draft and the script comes back trimmed from the original voice
note, not rewritten from the finished post. Record it on a phone or don't.

## License

MIT. Use it, fork it, sell it. See `LICENSE`.
