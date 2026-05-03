"""
stt/transcriber.py — Offline speech-to-text using OpenAI Whisper (local model).
No internet connection required. Model files are downloaded once on first run.

Model sizes (downloaded to stt/models/):
    tiny   ~39 MB  — fastest, lowest accuracy
    base   ~74 MB  — good balance  (default)
    small  ~244 MB — better Hindi/Hinglish accuracy
    medium ~769 MB — high accuracy, ~2s latency on CPU
"""

import io
import os
import logging
import numpy as np

logger     = logging.getLogger(__name__)
MODEL_DIR  = os.path.join(os.path.dirname(__file__), "models")
MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")

# Confidence floor — below this treat as silence / noise
CONF_FLOOR = 0.15

# Prompt seeds Whisper toward airport vocabulary
AIRPORT_PROMPT = (
    "Airport conversation. English and Hindi. "
    "flight number, gate, terminal, check-in, boarding pass, baggage, lounge, "
    "departure, arrival, IndiGo, Air India, Emirates, Starbucks, McDonald's, "
    "gate A1 through A22, gate B1 through B28, Terminal 1, Terminal 2, "
    "Skylight International Airport, Surat."
)

_DECODE_KWARGS = dict(
    task                        = "transcribe",
    initial_prompt              = AIRPORT_PROMPT,
    fp16                        = False,
    verbose                     = False,
    condition_on_previous_text  = True,
    no_speech_threshold         = 0.5,
    logprob_threshold           = -1.2,
    compression_ratio_threshold = 2.4,
)


def _confidence(segments: list) -> float:
    if not segments:
        return 0.5
    try:
        avg = sum(float(s.get("avg_logprob", -1.0)) for s in segments) / len(segments)
        return round(max(0.0, min(1.0, 1.0 + avg / 2.0)), 2)
    except Exception:
        return 0.5


class Transcriber:
    """Offline Whisper transcriber. Thread-safe for concurrent use."""

    def __init__(self, model_size: str = MODEL_SIZE):
        import whisper
        os.makedirs(MODEL_DIR, exist_ok=True)
        logger.info(f"[STT] Loading Whisper '{model_size}' (offline)...")
        self.model      = whisper.load_model(model_size, download_root=MODEL_DIR)
        self._prev_text = ""
        logger.info("[STT] Whisper ready.")

    def transcribe(self, audio_bytes: bytes) -> dict:
        """
        Transcribe raw WAV bytes to text.
        Returns: {"text": str, "confidence": float, "language": str}
        """
        if not audio_bytes:
            return {"text": "", "confidence": 0.0, "language": "english"}

        audio  = self._to_array(audio_bytes)
        prompt = AIRPORT_PROMPT
        if self._prev_text:
            prompt += " " + self._prev_text[-120:]

        result = self.model.transcribe(audio, **{**_DECODE_KWARGS, "initial_prompt": prompt})
        text   = (result.get("text") or "").strip()
        conf   = _confidence(result.get("segments", []))

        if conf < CONF_FLOOR:
            logger.info(f"[STT] conf={conf:.2f} below floor — discarding noise")
            return {"text": "", "confidence": conf, "language": "english"}

        self._prev_text = text[-120:] if text else ""
        logger.info(f"[STT] '{text}' conf={conf:.2f}")
        return {"text": text, "confidence": conf, "language": "english"}

    def set_context(self, agent_reply: str) -> None:
        """Feed last agent reply back as context to bias next transcription."""
        self._prev_text = (agent_reply or "")[:200]

    def _to_array(self, audio_bytes: bytes) -> np.ndarray:
        import soundfile as sf
        import librosa
        buf = io.BytesIO(audio_bytes)
        arr, sr = sf.read(buf, dtype="float32")
        if arr.ndim == 2:
            arr = arr.mean(axis=1)
        if sr != 16000:
            arr = librosa.resample(arr, orig_sr=sr, target_sr=16000)
        return arr
