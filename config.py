"""Environment and paths. Everything tunable lives here."""
import os
import pathlib
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parent

DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", ROOT / "data")).expanduser()
DB_PATH = DATA_DIR / "content.db"
AUDIO_DIR = DATA_DIR / "audio"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

_owner = os.getenv("OWNER_CHAT_ID", "").strip()
OWNER_CHAT_ID = int(_owner) if _owner else None

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
EFFORT = os.getenv("ANTHROPIC_EFFORT", "high")

TRANSCRIBE_PROVIDER = os.getenv("TRANSCRIBE_PROVIDER", "fal")
# Scribe costs more than fal-ai/whisper and is better on accented and technical
# speech. The transcript is the substance, so accuracy pays for itself here.
FAL_TRANSCRIBE_MODEL = os.getenv("FAL_TRANSCRIBE_MODEL", "fal-ai/elevenlabs/speech-to-text")
TRANSCRIBE_LANGUAGE = os.getenv("TRANSCRIBE_LANGUAGE", "")  # blank = auto-detect
WHISPER_CPP_BIN = os.getenv("WHISPER_CPP_BIN", "")
WHISPER_CPP_MODEL = os.getenv("WHISPER_CPP_MODEL", "")

TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
QUESTION_HOUR = int(os.getenv("QUESTION_HOUR", "9"))
REVIEW_HOUR = int(os.getenv("REVIEW_HOUR", "18"))
DRAFT_SWEEP_MINUTES = int(os.getenv("DRAFT_SWEEP_MINUTES", "10"))

DRAFTS_PER_SUBSTANCE = int(os.getenv("DRAFTS_PER_SUBSTANCE", "4"))
REVIEW_BATCH = int(os.getenv("REVIEW_BATCH", "3"))
CONTENT_LANGUAGE = os.getenv("CONTENT_LANGUAGE", "English")

# Source artifacts. The corpus is git-tracked (changing it is a deploy).
# context.md is seeded into DATA_DIR on first run so /context can edit it live.
VOICE_CORPUS_PATH = ROOT / "voice_corpus.md"
BLOCKLIST_PATH = ROOT / "blocklist.yml"
CONTEXT_TEMPLATE_PATH = ROOT / "context.md"
CONTEXT_PATH = pathlib.Path(os.getenv("CONTEXT_PATH", DATA_DIR / "context.md"))

PROMPTS_DIR = ROOT / "prompts"

# How far back the question generator looks for material.
QUESTION_LOOKBACK_DAYS = int(os.getenv("QUESTION_LOOKBACK_DAYS", "7"))


def storage_warning() -> str | None:
    """Non-fatal. In the container, a DATA_DIR off a volume is wiped every deploy."""
    if not os.getenv("CONTENT_ENGINE_CONTAINER") or os.path.ismount(DATA_DIR):
        return None
    return (f"DATA_DIR ({DATA_DIR}) is not a mounted volume. The database is wiped on "
            "every deploy. Run with -e DATA_DIR=/data -v content-engine-data:/data.")


def validate() -> list[str]:
    """Returns a list of fatal problems. Empty list means good to boot."""
    problems = []
    if not TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is not set (get one from @BotFather).")
    if not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY is not set.")
    if TRANSCRIBE_PROVIDER == "fal" and not FAL_KEY:
        problems.append(
            "TRANSCRIBE_PROVIDER=fal but FAL_KEY is not set. "
            "Set the key, or set TRANSCRIBE_PROVIDER=none to store voice notes untranscribed."
        )
    if TRANSCRIBE_PROVIDER == "whispercpp" and not (WHISPER_CPP_BIN and WHISPER_CPP_MODEL):
        problems.append("TRANSCRIBE_PROVIDER=whispercpp needs WHISPER_CPP_BIN and WHISPER_CPP_MODEL.")
    if not VOICE_CORPUS_PATH.exists():
        problems.append(f"Missing {VOICE_CORPUS_PATH.name}.")
    return problems
