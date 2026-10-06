"""The confidentiality gate. Two layers, deterministic first, always.

Layer 1 is regex and fails closed: a hard hit blocks the substance outright,
even if it was marked shareable. The match is surfaced so it can be overridden
consciously, one item at a time. Layer 2 only ever sees material layer 1 cleared.

This is the one part not optimised for speed.
"""
import logging
import re
from dataclasses import dataclass

import yaml

import config
import llm

log = logging.getLogger(__name__)

BLOCKED = "blocked"
FLAGGED = "flagged"
CLEAR = "clear"
OVERRIDDEN = "overridden"


@dataclass(frozen=True)
class Hit:
    severity: str      # hard | soft
    category: str
    why: str
    excerpt: str


_rules: list[tuple[str, str, str, re.Pattern]] = []


def load() -> int:
    """(Re)compile blocklist.yml. Returns the number of compiled patterns."""
    global _rules
    spec = yaml.safe_load(config.BLOCKLIST_PATH.read_text(encoding="utf-8")) or {}
    compiled = []
    for severity in ("hard", "soft"):
        for group in spec.get(severity) or []:
            name = group.get("name", "unnamed")
            why = group.get("why", "")
            for pattern in group.get("patterns") or []:
                compiled.append((severity, name, why, re.compile(pattern, re.IGNORECASE)))
            for literal in group.get("literals") or []:
                if not str(literal).strip():
                    continue
                pattern = r"\b" + re.escape(str(literal).strip()) + r"\b"
                compiled.append((severity, name, why, re.compile(pattern, re.IGNORECASE)))
    _rules = compiled
    log.info("blocklist: %d patterns", len(_rules))
    return len(_rules)


def scan(text: str) -> list[Hit]:
    """Layer 1. Every match, in file order, deduplicated by (category, excerpt)."""
    if not _rules:
        load()
    hits: list[Hit] = []
    seen = set()
    for severity, category, why, pattern in _rules:
        for match in pattern.finditer(text or ""):
            excerpt = _excerpt(text, match)
            key = (category, excerpt.lower())
            if key in seen:
                continue
            seen.add(key)
            hits.append(Hit(severity, category, why, excerpt))
    return hits


def _excerpt(text: str, match: re.Match, pad: int = 30) -> str:
    start = max(0, match.start() - pad)
    end = min(len(text), match.end() + pad)
    snippet = text[start:end].replace("\n", " ").strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def run(text: str) -> tuple[str, str]:
    """Full gate. Returns (gate_status, reason).

    Layer 2 is skipped entirely when layer 1 blocks: blocked material does not
    get sent to a model.
    """
    hits = scan(text)
    hard = [h for h in hits if h.severity == "hard"]
    soft = [h for h in hits if h.severity == "soft"]

    if hard:
        return BLOCKED, _format_hits("Blocklist", hard)

    try:
        verdict = llm.classify_confidentiality(text)
    except Exception:
        log.exception("layer 2 classifier failed")
        # Fail closed. An error is not a pass.
        return FLAGGED, "Classifier errored, so this is held rather than cleared."

    if verdict.reveals_nonpublic:
        reason = f"Classifier: {verdict.reason}"
        if soft:
            reason += "\n" + _format_hits("Also matched", soft)
        return FLAGGED, reason

    if soft:
        return FLAGGED, _format_hits("Worth a look", soft)

    return CLEAR, ""


def _format_hits(label: str, hits: list[Hit], limit: int = 5) -> str:
    lines = [f"{label}:"]
    for hit in hits[:limit]:
        lines.append(f"- {hit.category} ({hit.why}): {hit.excerpt}")
    if len(hits) > limit:
        lines.append(f"- and {len(hits) - limit} more")
    return "\n".join(lines)
