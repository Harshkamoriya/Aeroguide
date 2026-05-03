"""
speech.py — AeroGuide interactive voice/text client.

Talks to the AeroGuide server (api/server.py) over HTTP.
No direct LLM or TTS calls — all processing happens server-side.

Usage:
  python speech.py                    # voice mode, single turn
  python speech.py --loop             # voice mode, continuous until 'goodbye'
  python speech.py --text             # type input instead of speaking
  python speech.py --text --loop      # continuous text chat
  python speech.py --duration 8       # record for 8 seconds (default 5)
  python speech.py --base-url http://localhost:8000

Requirements (client-side only):
  pip install sounddevice soundfile numpy requests
"""

import argparse
import io
import os
import sys
import tempfile
import time

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf

# ── Config ─────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL   = os.getenv("AEROGUIDE_URL", "http://localhost:8000")
DEFAULT_DURATION   = 5       # seconds per recording
SAMPLE_RATE        = 16000
CHANNELS           = 1
SESSION_ID         = "__cli__"   # shared session for CLI (matches server default)


# ── Audio helpers ───────────────────────────────────────────────────────────

def record_audio(duration: float) -> bytes:
    """Record from the default microphone and return WAV bytes."""
    print(f"  [mic] Recording for {duration:.0f}s... speak now", flush=True)
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate = SAMPLE_RATE,
        channels   = CHANNELS,
        dtype      = "float32",
    )
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def play_audio(wav_bytes: bytes) -> None:
    """Play WAV bytes through the default speaker."""
    if not wav_bytes:
        return
    try:
        buf        = io.BytesIO(wav_bytes)
        data, sr   = sf.read(buf, dtype="float32")
        sd.play(data, sr, blocking=True)
    except Exception as e:
        print(f"  [playback] Could not play audio: {e}", file=sys.stderr)


# ── Server calls ────────────────────────────────────────────────────────────

def transcribe(wav_bytes: bytes, base_url: str) -> str:
    """Upload WAV bytes to /test/transcribe and return the transcript."""
    try:
        resp = requests.post(
            f"{base_url}/test/transcribe",
            files   = {"audio": ("audio.wav", wav_bytes, "audio/wav")},
            timeout = 60,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text", "").strip()
        conf = data.get("confidence", 0.0)
        if text:
            print(f"  [stt] '{text}' (conf={conf:.2f})")
        else:
            print("  [stt] (no speech detected)")
        return text
    except requests.ConnectionError:
        print(f"  [error] Cannot reach server at {base_url}. Is it running?", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"  [transcribe error] {e}", file=sys.stderr)
        return ""


def run_full(text: str, base_url: str) -> dict:
    """Send text to /test/full and return the full pipeline result."""
    try:
        resp = requests.post(
            f"{base_url}/test/full",
            json    = {"text": text, "session_id": SESSION_ID},
            timeout = 90,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"  [error] Cannot reach server at {base_url}.", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  [full-pipeline error] {e}", file=sys.stderr)
        return {}


def synthesize(text: str, base_url: str) -> bytes:
    """Send text to /test/synthesize and return WAV bytes."""
    try:
        resp = requests.post(
            f"{base_url}/test/synthesize",
            json    = {"text": text},
            timeout = 30,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  [synthesize error] {e}", file=sys.stderr)
        return b""


# ── Turn logic ──────────────────────────────────────────────────────────────

def run_turn(text: str, base_url: str, speak_out: bool) -> bool:
    """
    Run a single pipeline turn.
    Returns True if the conversation should end.
    """
    if not text:
        return False

    # Full pipeline (NLU + DB + LLM)
    result = run_full(text, base_url)
    if not result:
        return False

    response  = result.get("response", "")
    followup  = result.get("followup")
    intent    = result.get("intent", "unknown")
    map_hint  = result.get("map_hint") or {}
    latency   = result.get("latency_ms")

    ms_str = f" ({latency}ms)" if latency else ""
    print(f"\n  AeroGuide [{intent}]{ms_str}:")
    print(f"  {response}")

    if followup:
        print(f"  Follow-up: {followup}")

    if map_hint and any(map_hint.values()):
        t = map_hint.get("terminal", "")
        f = map_hint.get("floor", "")
        l = map_hint.get("landmark", "")
        parts = [x for x in [t, f, l] if x]
        print(f"  [map] {' › '.join(parts)}")

    # Speak the response aloud
    if speak_out and response:
        wav = synthesize(response, base_url)
        play_audio(wav)

    end = result.get("end_session", False) or intent == "end_conversation"
    return end


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AeroGuide voice/text CLI client")
    parser.add_argument("--text",     action="store_true",
                        help="Type input instead of recording from mic")
    parser.add_argument("--loop",     action="store_true",
                        help="Keep conversing until user says goodbye")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                        help=f"Recording duration in seconds (default {DEFAULT_DURATION})")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"Server base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--no-audio-out", action="store_true",
                        help="Print responses only, do not play audio")
    args = parser.parse_args()

    base_url  = args.base_url.rstrip("/")
    speak_out = not args.no_audio_out and not args.text  # voice output only in voice mode
    # Allow --text with audio output if user explicitly wants it
    if args.text and not args.no_audio_out:
        speak_out = True  # text-in, voice-out is valid too

    # Check server health
    try:
        h = requests.get(f"{base_url}/health", timeout=5)
        h.raise_for_status()
        print(f"Connected to AeroGuide server at {base_url}")
    except Exception:
        print(f"[warn] Server not reachable at {base_url}. Continuing anyway...")

    print("=" * 60)
    print("  Welcome to AeroGuide — Skylight International Airport")
    print("  Type 'exit' or say 'goodbye' to end the session.")
    if not args.text:
        print(f"  Voice mode: {args.duration:.0f}s recording per turn.")
    print("=" * 60)

    while True:
        print()

        if args.text:
            try:
                user_input = input("  You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  [Goodbye]")
                break
            if user_input.lower() in ("exit", "quit", "bye", "goodbye"):
                print("  AeroGuide: Safe travels! Goodbye.")
                break
            if not user_input:
                continue
            ended = run_turn(user_input, base_url, speak_out)

        else:
            # Voice mode
            try:
                input("  [Press Enter to speak, Ctrl+C to exit]")
            except KeyboardInterrupt:
                print("\n  [Goodbye]")
                break

            wav_bytes  = record_audio(args.duration)
            user_input = transcribe(wav_bytes, base_url)

            if not user_input:
                print("  (nothing heard, try again)")
                if not args.loop:
                    break
                continue

            ended = run_turn(user_input, base_url, speak_out)

        if ended:
            print("\n  [Session ended by agent]")
            break

        if not args.loop:
            break

    print("\n  Session complete. Thank you for using AeroGuide.")


if __name__ == "__main__":
    main()
