"""Speech to text. One interface, three providers, switched by TRANSCRIBE_PROVIDER.

Claude has no audio input, so voice notes go through fal. Everything downstream
reads substance.transcript and does not care which provider filled it.

The default is ElevenLabs Scribe. Set FAL_TRANSCRIBE_MODEL to fal-ai/whisper for
a cheaper Whisper large-v3 run; both return `text`, so nothing else changes.
"""
import logging
import pathlib
import subprocess

import config

log = logging.getLogger(__name__)

# Scribe names the language field differently from Whisper. Everything else in
# the payload is shared, so this is the only per-model branch we need.
_LANGUAGE_FIELD = {
    "fal-ai/elevenlabs/speech-to-text": "language_code",
    "fal-ai/whisper": "language",
}


def transcribe(path: pathlib.Path) -> str | None:
    """Returns the transcript, or None when transcription is off or failed."""
    provider = config.TRANSCRIBE_PROVIDER
    if provider == "none":
        return None
    try:
        if provider == "fal":
            return _fal_transcribe(path)
        if provider == "whispercpp":
            return _whispercpp_transcribe(path)
        log.error("unknown TRANSCRIBE_PROVIDER %r", provider)
        return None
    except Exception:
        log.exception("transcription failed for %s", path)
        return None


def _fal_transcribe(path: pathlib.Path) -> str:
    import fal_client  # imported lazily so `none` and whispercpp need no fal install

    model = config.FAL_TRANSCRIBE_MODEL
    audio_url = fal_client.upload_file(path)
    arguments = {"audio_url": audio_url}
    if config.TRANSCRIBE_LANGUAGE:
        arguments[_LANGUAGE_FIELD.get(model, "language")] = config.TRANSCRIBE_LANGUAGE

    result = fal_client.subscribe(model, arguments=arguments)
    text = (result or {}).get("text") or ""
    if not text.strip():
        log.error("%s returned no text: keys=%s", model, list((result or {}).keys()))
    return text.strip()


def _whispercpp_transcribe(path: pathlib.Path) -> str:
    # whisper.cpp wants 16kHz mono wav; ffmpeg must be on PATH.
    wav = path.with_suffix(".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ar", "16000", "-ac", "1", str(wav)],
        check=True,
    )
    out = subprocess.run(
        [config.WHISPER_CPP_BIN, "-m", config.WHISPER_CPP_MODEL, "-f", str(wav), "-nt", "-otxt"],
        check=True, capture_output=True, text=True,
    )
    txt = wav.with_suffix(".wav.txt")
    if txt.exists():
        return txt.read_text(encoding="utf-8").strip()
    return out.stdout.strip()
