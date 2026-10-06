"""Build a voice corpus shortlist from your own local writing. Offline, no keys.

Reads your messages out of Claude Code transcripts (~/.claude/projects/*/*.jsonl)
and, optionally, plain-text files you point it at. Keeps prose of 80-500 words,
drops anything that reads like an instruction to an agent, ranks the rest by how
much it sounds like you arguing a position, and runs layer 1 of the gate over
each one so you can see which passages need scrubbing.

    python mine_corpus.py                          # Claude Code transcripts
    python mine_corpus.py --text notes.txt --text thread.md
    python mine_corpus.py --top 60 --out corpus_candidates.md

The output is a working file and is gitignored. It will contain whatever you
typed into those sessions, so read it before it goes anywhere. Nothing from it
reaches voice_corpus.md unless you paste it there by hand.
"""
import argparse
import json
import os
import pathlib
import re
import sys

# gate imports llm, which builds a client at import time. The miner never calls
# a model; it only needs layer 1, so a placeholder key is enough.
os.environ.setdefault("ANTHROPIC_API_KEY", "unused-by-the-miner")

import gate  # noqa: E402

MIN_WORDS = 80
MAX_WORDS = 500

# Words that show up when someone is defending a view rather than asking for a
# change. Crude on purpose: it orders the shortlist, it does not decide it.
POSITION_MARKERS = re.compile(
    r"\b(because|i think|imo|i believe|the truth is|truth is|the point is|the thing is|"
    r"meanwhile|whereas|actually|you said|i disagree|that's wrong|the problem is|"
    r"the real|in the end|at the end of the day|the move is|which means|that means)\b",
    re.IGNORECASE,
)

# Shapes of a message written to an agent, not to a person: imperatives at the
# start, file paths, code, stack traces, pasted blocks, numbered task lists.
INSTRUCTION_START = re.compile(
    r"^\s*(please\s+)?(fix|add|remove|create|implement|update|refactor|run|make|build|"
    r"write|change|rename|delete|move|install|deploy|check|test|debug|use|let's|lets)\b",
    re.IGNORECASE,
)
CODE_SIGNS = re.compile(
    r"```|Traceback|^\s*at \S+\(|\b\w+\.(py|ts|tsx|js|rs|go|json|yml|yaml|toml|md|sql)\b|"
    r"[A-Za-z]:\\|/[\w.-]+/[\w.-]+/|\[Pasted text|<[a-z-]+>|\bnpm |\bpip |\bgit ",
    re.IGNORECASE | re.MULTILINE,
)
LIST_ITEM = re.compile(r"^\s*(\d+[.)]|[-*•])\s", re.MULTILINE)


def claude_code_messages(root: pathlib.Path):
    """Yield (label, text) for every message you typed in a Claude Code session."""
    for path in sorted(root.glob("*/*.jsonl")):
        label = path.parent.name
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, list):
                # Lists carry tool results; only plain text blocks are you.
                if any(b.get("type") == "tool_result" for b in content if isinstance(b, dict)):
                    continue
                content = "\n".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            if isinstance(content, str) and content.strip():
                yield label, content.strip()


def text_file_messages(path: pathlib.Path):
    """Yield (label, text) per block of a plain-text file, split on --- or blank lines."""
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*---\s*\n", text) if "\n---" in text else re.split(r"\n\s*\n", text)
    for block in blocks:
        if block.strip():
            yield path.name, block.strip()


def reject_reason(text: str) -> str | None:
    words = len(text.split())
    if words < MIN_WORDS or words > MAX_WORDS:
        return "length"
    if text.startswith(("<", "/", "!")):
        return "command"  # slash commands, injected context, shell escapes
    if INSTRUCTION_START.match(text):
        return "instruction"
    if len(CODE_SIGNS.findall(text)) >= 2:
        return "code"
    if len(LIST_ITEM.findall(text)) >= 3:
        return "task list"
    return None


def score(text: str) -> int:
    return len(POSITION_MARKERS.findall(text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claude-dir", type=pathlib.Path,
                        default=pathlib.Path.home() / ".claude" / "projects",
                        help="Claude Code transcript root (default: ~/.claude/projects)")
    parser.add_argument("--no-claude", action="store_true", help="skip Claude Code transcripts")
    parser.add_argument("--text", type=pathlib.Path, action="append", default=[],
                        help="a plain-text file of your own writing; repeatable")
    parser.add_argument("--top", type=int, default=40, help="how many candidates to keep")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("corpus_candidates.md"))
    args = parser.parse_args()

    sources = []
    if not args.no_claude:
        if args.claude_dir.exists():
            sources.append(claude_code_messages(args.claude_dir))
        else:
            print(f"no Claude Code transcripts at {args.claude_dir}", file=sys.stderr)
    for path in args.text:
        sources.append(text_file_messages(path))

    seen, kept, rejected = set(), [], {}
    for source in sources:
        for label, text in source:
            key = re.sub(r"\s+", " ", text.lower())
            if key in seen:
                continue
            seen.add(key)
            reason = reject_reason(text)
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            kept.append((score(text), label, text))

    kept.sort(key=lambda k: (k[0], len(k[2].split())), reverse=True)
    kept = kept[: args.top]

    gate.load()
    out, dirty = [], 0
    for i, (points, label, text) in enumerate(kept, 1):
        hits = gate.scan(text)
        hard = [h for h in hits if h.severity == "hard"]
        dirty += bool(hard)
        out.append(f"## {i}. score {points} · {len(text.split())} words · {label}\n")
        for h in hard:
            out.append(f"GATE WARNING {h.category}: {h.excerpt}")
        for h in (h for h in hits if h.severity == "soft"):
            out.append(f"gate note {h.category}: {h.excerpt}")
        if hits:
            out.append("")
        out.append(text + "\n\n---\n")

    header = f"""# Voice corpus shortlist

NOT FOR COMMIT. Working file, gitignored. Mined from your own writing:
{MIN_WORDS}-{MAX_WORDS} words, prose, agent instructions filtered out, ranked by how
much each one reads like you defending a position.

How to use: read down, keep the ones that sound like you making a case, delete
the rest, strip names and numbers, paste what survives into voice_corpus.md
separated by `---`.

GATE WARNING lines mean the confidentiality gate would block that passage as
substance. The corpus is NOT gated. Scrub those before the passage goes anywhere
near voice_corpus.md.

{len(kept) - dirty} clean, {dirty} need scrubbing.

---
"""
    args.out.write_text(header + "\n" + "\n".join(out), encoding="utf-8")
    dropped = ", ".join(f"{n} {r}" for r, n in sorted(rejected.items())) or "none"
    print(f"{len(kept)} candidates -> {args.out} ({dirty} need scrubbing). Dropped: {dropped}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
