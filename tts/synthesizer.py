"""
tts/synthesizer.py — Offline text-to-speech using pyttsx3.
No internet required. Uses the OS's built-in TTS engine:
    Windows  — SAPI5 (Microsoft voices, e.g. Zira, David, Hazel)
    macOS    — NSSpeechSynthesizer (Alex, Samantha, etc.)
    Linux    — espeak (install: sudo apt install espeak)

Voice can be configured via PYTTSX3_VOICE_NAME env var (partial name match).
Rate can be tuned via PYTTSX3_RATE env var (default: 165 wpm).
"""

import io
import os
import re
import logging
import tempfile
import threading

logger = logging.getLogger(__name__)

_RATE       = int(os.getenv("PYTTSX3_RATE", "165"))
_VOICE_NAME = os.getenv("PYTTSX3_VOICE_NAME", "").lower()

# pyttsx3 is NOT thread-safe — serialize all calls through a single lock
_tts_lock = threading.Lock()
_engine   = None


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    try:
        import pyttsx3
        _engine = pyttsx3.init()
        _engine.setProperty("rate", _RATE)

        # Prefer a female English voice if available
        voices = _engine.getProperty("voices") or []
        chosen = None
        if _VOICE_NAME:
            chosen = next((v for v in voices if _VOICE_NAME in v.name.lower()), None)
        if not chosen:
            # Default preference: English female
            for keyword in ("zira", "hazel", "samantha", "female", "en"):
                chosen = next((v for v in voices if keyword in v.name.lower()), None)
                if chosen:
                    break
        if chosen:
            _engine.setProperty("voice", chosen.id)
            logger.info(f"[TTS] Voice: {chosen.name}")
        else:
            logger.info("[TTS] Using default system voice")

        logger.info(f"[TTS] pyttsx3 ready (rate={_RATE})")
        return _engine

    except ImportError:
        logger.error("[TTS] pyttsx3 not installed — run: pip install pyttsx3")
        raise
    except Exception as e:
        logger.error(f"[TTS] pyttsx3 init failed: {e}")
        raise


# ── Text cleanup ───────────────────────────────────────────────────────────

_MARKDOWN_RE = re.compile(r'\*{1,3}(.*?)\*{1,3}', re.DOTALL)
_SYMBOL_RE   = re.compile(r'[₹$€£]')
_CLEAN_RE    = re.compile(r'[#`_]')


def _clean(text: str) -> str:
    text = _MARKDOWN_RE.sub(r'\1', text)
    text = _SYMBOL_RE.sub('', text)
    text = _CLEAN_RE.sub('', text)
    text = text.replace('\\n', ' ').replace('\n', ' ')
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


# ── Public API ─────────────────────────────────────────────────────────────

def speak_to_bytes(text: str) -> bytes:
    """
    Synthesize text to WAV bytes (for API responses / file playback).
    Thread-safe — serializes calls through a lock.
    """
    if not text or not text.strip():
        return b""

    cleaned = _clean(text)
    tmp_path = tempfile.mktemp(suffix=".wav")

    with _tts_lock:
        try:
            engine = _get_engine()
            engine.save_to_file(cleaned, tmp_path)
            engine.runAndWait()
        except Exception as e:
            logger.error(f"[TTS] speak_to_bytes failed: {e}")
            return b""

    try:
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        return data
    except Exception as e:
        logger.error(f"[TTS] file read failed: {e}")
        return b""
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def speak_direct(text: str) -> None:
    """
    Speak text directly through the system speaker (blocking).
    Use this for CLI / speech.py playback when you don't need the audio bytes.
    """
    if not text or not text.strip():
        return
    cleaned = _clean(text)
    with _tts_lock:
        try:
            engine = _get_engine()
            engine.say(cleaned)
            engine.runAndWait()
        except Exception as e:
            logger.error(f"[TTS] speak_direct failed: {e}")
