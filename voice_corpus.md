# Voice corpus

Replace everything below the first `---` with your own passages. See
METHODOLOGY.md for how to pick them; `python mine_corpus.py` builds a
shortlist from your local Claude Code transcripts.

Keep it raw. Verbatim. Typos left in on purpose: this is a sample of how you
talk, not of how you spell, and cleaning it up is how it stops sounding like
you. The draft prompt keeps the informality and is told not to invent typos of
its own.

This file is sent to the model on every draft call and, if you deploy from git,
it is committed. It is NOT run through the confidentiality gate. Scrub names
and figures before a passage goes in, and keep the repo you deploy from private
once this file holds real text.

Passages are separated by a line containing only ---

---

(passage 1: 100-400 words of you explaining a position, in your own words)

---

(passage 2)
