"""
nlu/intent.py — Intent classifier for AeroGuide airport companion.
Uses Ollama (local LLM) for fully offline intent detection.
"""

import os
import json
import logging
import time
import requests

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

from agent.prompt import INTENT_SYSTEM_PROMPT, INTENT_USER_PROMPT

logger = logging.getLogger(__name__)

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

HISTORY_WINDOW = 10


def _extract_json(text: str) -> dict:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1]
    if clean.endswith("```"):
        clean = clean.rsplit("```", 1)[0]
    clean = clean.strip()
    start = clean.find("{")
    end   = clean.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON in response: {text[:120]!r}")
    return json.loads(clean[start:end + 1])


class IntentClassifier:
    """Classifies traveler utterances into airport intents using local Ollama LLM."""

    def __init__(self, model: str = None):
        self.model    = model or OLLAMA_MODEL
        self._history: list[dict] = []

    def classify(self, transcript: str, retries: int = 3, backoff: float = 2.0) -> dict:
        if not transcript.strip():
            return self._empty()

        self._history.append({"role": "traveler", "text": transcript.strip()})
        prompt = self._build_prompt(transcript)

        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model":   self.model,
                        "messages": [
                            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                            {"role": "user",   "content": prompt},
                        ],
                        "format": "json",
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 200},
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
                result  = _extract_json(content)

                ents = result.get("entities", {})
                logger.info(
                    f"[NLU] intent={result.get('intent')} conf={result.get('confidence', 0):.2f} "
                    f"flight={ents.get('flight_number')} brand={ents.get('brand_name')} "
                    f"facility={ents.get('facility_type')} gate={ents.get('gate_code')}"
                )
                return result

            except (requests.ConnectionError, requests.Timeout):
                if attempt < retries:
                    logger.warning(f"[NLU] Ollama unreachable, retry {attempt}/{retries}")
                    time.sleep(backoff * attempt)
                else:
                    logger.error("[NLU] Ollama not available — is it running? (ollama serve)")
                    return self._empty()

            except Exception as e:
                logger.error(f"[NLU] {type(e).__name__}: {e}")
                return self._empty()

        return self._empty()

    def add_agent_turn(self, agent_text: str) -> None:
        if agent_text.strip():
            self._history.append({"role": "agent", "text": agent_text.strip()})

    def reset(self) -> None:
        self._history.clear()

    def _build_prompt(self, transcript: str) -> str:
        recent = self._history[-HISTORY_WINDOW:]
        lines  = []
        for turn in recent[:-1]:
            tag = "Traveler" if turn["role"] == "traveler" else "AeroGuide"
            lines.append(f"{tag}: {turn['text']}")
        history_text = "\n".join(lines) if lines else "None"
        return INTENT_USER_PROMPT.format(
            history    = history_text,
            transcript = transcript,
        )

    def _empty(self) -> dict:
        return {
            "intent":     "unknown",
            "confidence": 0.0,
            "entities": {
                "flight_number": None,
                "airline":       None,
                "destination":   None,
                "terminal":      None,
                "gate_code":     None,
                "food_item":     None,
                "brand_name":    None,
                "facility_type": None,
                "floor_ref":     None,
            },
            "language": "english",
        }
