"""Every call to Claude lives here. Synchronous; callers use asyncio.to_thread.

The voice corpus is the first system block on draft generation so it sits at the
front of the cached prefix. Check cache_read_input_tokens in the logs: if it is
always zero, something volatile crept in ahead of it.
"""
import logging

import anthropic
from pydantic import BaseModel, Field

import config

log = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)

MAX_TOKENS = 16000


# --- structured shapes -----------------------------------------------------

class Post(BaseModel):
    angle: str = Field(description="A few words naming the angle, e.g. 'mistake and correction'.")
    specific_detail: str = Field(
        description="The detail from the substance this post rests on, quoted or closely paraphrased."
    )
    body: str = Field(description="The post itself, ready to paste into LinkedIn.")


class DraftSet(BaseModel):
    enough_substance: bool = Field(
        description="False when the substance contains no detail only someone who did the work would know."
    )
    posts: list[Post] = Field(default_factory=list, description="Empty when enough_substance is false.")
    refusal_reason: str = Field(
        default="", description="When refusing, one sentence on what the substance is missing."
    )


class GateVerdict(BaseModel):
    reveals_nonpublic: bool
    reason: str


# --- plumbing --------------------------------------------------------------

def _prompt(name: str, **fields: str) -> str:
    text = (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in fields.items():
        text = text.replace("{{" + key + "}}", value or "")
    return text


def _log_usage(label: str, response) -> None:
    u = response.usage
    log.info(
        "%s: in=%s out=%s cache_read=%s cache_write=%s",
        label, u.input_tokens, u.output_tokens,
        getattr(u, "cache_read_input_tokens", 0), getattr(u, "cache_creation_input_tokens", 0),
    )


def _text_of(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def _guard(response, label: str) -> None:
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise RuntimeError(f"{label}: model declined ({getattr(detail, 'category', None)})")


def read_corpus() -> str:
    return config.VOICE_CORPUS_PATH.read_text(encoding="utf-8").strip()


def read_context() -> str:
    if config.CONTEXT_PATH.exists():
        return config.CONTEXT_PATH.read_text(encoding="utf-8").strip()
    return ""


# --- the three prompts, plus the gate classifier ---------------------------

def generate_question(material: str, asked: str) -> str:
    response = _client.messages.create(
        model=config.MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": config.EFFORT},
        messages=[{
            "role": "user",
            "content": _prompt("question", material=material, context=read_context(), asked=asked),
        }],
    )
    _guard(response, "question")
    _log_usage("question", response)
    return _text_of(response)


def generate_drafts(substance: str, n: int | None = None) -> DraftSet:
    n = n or config.DRAFTS_PER_SUBSTANCE
    instructions = _prompt("draft", n=str(n), language=config.CONTENT_LANGUAGE)
    context = read_context()
    user = "## Standing context\n\n" + (context or "(none)") + "\n\n## Cleared substance\n\n" + substance

    response = _client.messages.parse(
        model=config.MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": config.EFFORT},
        system=[
            {
                "type": "text",
                "text": "# Voice corpus\n\n" + read_corpus(),
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": instructions},
        ],
        messages=[{"role": "user", "content": user}],
        output_format=DraftSet,
    )
    _guard(response, "drafts")
    _log_usage("drafts", response)
    result = response.parsed_output
    if result is None:
        raise RuntimeError("drafts: model returned no parsable output")
    if not result.enough_substance:
        result.posts = []
    return result


def generate_video_script(transcript: str) -> str:
    response = _client.messages.create(
        model=config.MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": config.EFFORT},
        messages=[{
            "role": "user",
            "content": _prompt("video", transcript=transcript, language=config.CONTENT_LANGUAGE),
        }],
    )
    _guard(response, "video")
    _log_usage("video", response)
    return _text_of(response)


def classify_confidentiality(material: str) -> GateVerdict:
    """Layer 2 of the gate. Never runs on material layer 1 already blocked."""
    response = _client.messages.parse(
        model=config.MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": config.EFFORT},
        messages=[{"role": "user", "content": _prompt("gate_classifier", material=material)}],
        output_format=GateVerdict,
    )
    _guard(response, "gate")
    _log_usage("gate", response)
    verdict = response.parsed_output
    if verdict is None:
        # Fail closed. An unreadable verdict is a flag, not a pass.
        return GateVerdict(reveals_nonpublic=True, reason="Classifier returned no usable verdict.")
    return verdict
