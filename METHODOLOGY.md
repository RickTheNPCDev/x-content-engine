# Methodology

Why the engine is shaped the way it is, and how to build the one input that
decides whether it works: the voice corpus.

## The objective

Change how your public profile reads, using things you actually did and
actually think. Not follower count.

**Success bar (2 weeks):** drafts ship with minimal edit, and at least one
person who matters to you engages or reaches out because of a post.

**Failure signal:** you edit every draft to death, or reviewing feels like a
chore. Both are cheap to learn.

## Building the voice corpus

Drafts are only as good as the few-shot examples. This is one hour of work and
it determines everything downstream.

### What to collect

Your own messages where you are **explaining or defending a position**: long
answers in AI conversations, threads with a cofounder or teammate, a reply
where someone pushed back and you held your ground. That is you thinking out
loud, which is the register you want in public.

Not your specs or technical docs: they read nothing like how you talk. Not your
existing posts either, since those are usually the voice you are trying to
escape.

**Target:** 15-25 passages, 100-400 words each. `/corpus` tells you when it is
still too thin.

### Recommended: get interviewed by your agent

The fastest way to good material is to produce it fresh. Open Claude Code (or
any agent you chat with locally) and paste `prompts/corpus_interview.md`. The
agent then:

1. asks one pointed question at a time, pulling for a position rather than a
   summary,
2. **pushes back** once you answer, so you have to defend it,
3. moves on to a new topic, 15-20 times,
4. on "done", writes `voice_corpus.md` from your answers verbatim, anonymised
   and with figures removed, and lists every change for you to check.

Answer fast, in flow, without rereading. Typos are fine; they are the point.
The pushback is what makes it work: the passages where you argue with the
interviewer are usually the most alive ones in the corpus.

Run it locally. Your answers are raw and unscrubbed until step 4, so they should
not be in a shared chat or a shared repo.

### Alternative: mine what you already wrote

If you use Claude Code, your past messages are already on disk, and some of them
will be you making a case. You can also combine both: mine first, then use the
interview to fill the topics the shortlist missed.

```bash
python mine_corpus.py                       # reads ~/.claude/projects
python mine_corpus.py --text slack.txt      # add any plain-text export of your own writing
```

It keeps 80-500 word prose messages, throws out anything that reads like an
instruction to an agent (imperatives, file paths, code, task lists, pasted
blocks), ranks the rest by position markers ("because", "the truth is", "you
said", "meanwhile"...), and runs layer 1 of the gate over each candidate. The
result is `corpus_candidates.md`, which is gitignored and never leaves your
machine.

The ranking is crude on purpose. It orders the pile so the good ones are near
the top; you still make every call.

### Curating it

Read down the shortlist and keep a passage only if:

- **It makes a case.** A position, then the defence of it. Summaries of what
  happened are weak; arguments are strong.
- **It sounds like you on a good day.** Fast, specific, a bit blunt. If you
  wince reading it, cut it.
- **It covers a different topic.** Twenty passages on one subject teach the
  model one subject.

Passages where you are **arguing with someone** are often the best ones. That
is where people sound most alive.

### Leave it raw

Do not fix typos, casing, or run-on sentences. The corpus is a sample of how
you talk, not how you spell, and cleaning it up is how it stops sounding like
you. The draft prompt keeps the informality and is told not to invent typos of
its own.

### Scrub it

The corpus is **not gated**. It is a plain file sent to the model on every
draft call, and if you deploy from git it is committed. Before a passage goes
in:

- Fix anything with a `GATE WARNING` line: figures, deal terms, credentials.
- Anonymise people and companies, especially anything recounting a private
  conversation ("a client's CTO", not their name).
- Remove internal project codenames, or add them to `blocklist.yml` so the
  gate catches them in substance too.

Once `voice_corpus.md` holds real text, the repo you deploy from is private.
Fork this one, never push your corpus back upstream.

## Standing context

`context.md` is the second input. The question generator reads it so it can
ask something specific instead of "what did you learn this week?". Keep four
short lists: what you are building this month, positions you actually hold,
what you refuse to say publicly, and who you are trying to reach. Edit it live
with `/context <line>`.

## The prompts

All in `prompts/`, plain markdown, editable without code changes.

- **Question generator** (daily, sends one). Must reference something specific
  from your material and pull for a position, a disagreement, or a
  counterintuitive detail. Answerable in a two-minute voice note.
- **Draft generator.** N posts per piece of substance, each on a different
  angle: contrarian take, concrete build detail, mistake and correction,
  why-this-matters. Every post must rest on one detail only someone who did the
  work would know. If there is none, it refuses. That refusal is the anti-slop
  mechanism; let it refuse.
- **Video script.** Trimmed from the raw voice-note transcript, never rewritten
  from the finished post. A video read off a written post sounds read.
- **Gate classifier.** Layer 2, below.

## The confidentiality gate

Two layers, deterministic first, always.

1. **Blocklist** (`blocklist.yml`, regex, fails closed). Money, cap table, deal
   terms, credentials, confidentiality markers, plus the literal names you add.
   A hard hit blocks the substance even if you marked it shareable, and it never
   reaches a model. You can override it consciously, one item at a time.
2. **LLM classifier.** "Would publishing this reveal non-public commercial or
   technical information?" Runs only on what layer 1 cleared. An error is a
   hold, not a pass.

The gate is the one part not to optimise for speed. The highest-value
five-minute edit in the repo is filling in the `literals:` lists with your
client, investor and team names.

## Build order and scope

Run the text loop for two weeks before adding anything. Copy approved posts to
LinkedIn by hand. Log signals by hand.

Not in v1, on purpose: auto-publishing, repurposing, X, analytics dashboards,
multi-user, Notion sync, a web UI. Each is a way to spend a week without
learning whether the drafts sound like you.

## The four numbers

1. **Voice-pass-rate.** Approved drafts shipped with under 10% edit.
2. **Interview answer rate.** Did you answer the questions or ignore them.
3. **Leverage signals.** Inbound from people who matter. Named, not counted.
4. **Refusal rate.** How often the generator said NOT_ENOUGH_SUBSTANCE. Zero
   means the anti-slop instruction is not working.

Follower count is not on this list on purpose.
