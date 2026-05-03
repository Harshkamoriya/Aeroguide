"""
nlu/intent.py — Intent classifier for AeroGuide airport companion.
Uses Ollama (local LLM) with a rule-based keyword fallback for robustness.
"""

import os
import re
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

# Inline aliases (mirrors nlu/entities.py — kept here to avoid circular import)
_BRAND_MAP = {
    "starbuck": "Starbucks", "sbux": "Starbucks",
    "mcdonald": "McDonald's", "mcdonalds": "McDonald's", "mcds": "McDonald's", "mcd": "McDonald's", "mac": "McDonald's",
    "burger king": "Burger King", "burgerking": "Burger King", "bk": "Burger King",
    "kfc": "KFC",
    "dominos": "Domino's", "dominoes": "Domino's",
    "pizza hut": "Pizza Hut", "pizzahut": "Pizza Hut",
    "haldiram": "Haldiram's", "haldirams": "Haldiram's",
    "saravana": "Saravana Bhavan", "saravanaa": "Saravana Bhavan",
    "wow momo": "Wow! Momo", "wowmomo": "Wow! Momo", "momos": "Wow! Momo",
    "baskin": "Baskin-Robbins",
    "ccd": "Café Coffee Day", "cafe coffee day": "Café Coffee Day", "coffee day": "Café Coffee Day",
    "costa": "Costa Coffee",
    "dunkin": "Dunkin'", "dunkins": "Dunkin'",
    "haagen": "Häagen-Dazs", "haagendazs": "Häagen-Dazs",
    "subway": "Subway",
    "starbucks": "Starbucks",
}

_FACILITY_MAP = {
    "toilet": "restroom", "toilets": "restroom", "loo": "restroom",
    "washroom": "restroom", "bathroom": "restroom",
    "cash": "ATM", "cash machine": "ATM", "money": "ATM", "bank": "ATM",
    "doctor": "Medical", "doc": "Medical", "first aid": "Medical", "medical": "Medical",
    "prayer": "Prayer Room", "namaz": "Prayer Room", "masjid": "Prayer Room",
    "smoke": "Smoking Area", "cigarette": "Smoking Area", "smoking": "Smoking Area",
    "baby": "Nursery", "nursing": "Nursery",
    "wifi": "Wi-Fi", "wi-fi": "Wi-Fi", "internet": "Wi-Fi",
    "forex": "Currency Exchange", "currency": "Currency Exchange", "exchange": "Currency Exchange",
    "atm": "ATM",
    "pharmacy": "Pharmacy", "chemist": "Pharmacy",
}


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
        raise ValueError(f"No JSON object found in: {text[:120]!r}")
    return json.loads(clean[start:end + 1])


class IntentClassifier:
    """Classifies traveler utterances. Uses Ollama LLM with keyword fallback."""

    def __init__(self, model: str = None):
        self.model    = model or OLLAMA_MODEL
        self._history: list[dict] = []

    def classify(self, transcript: str, retries: int = 2, backoff: float = 1.5) -> dict:
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
                        "options": {"temperature": 0.1, "num_predict": 250},
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
                logger.debug(f"[NLU] Ollama raw: {content[:200]}")
                result = _extract_json(content)

                intent = result.get("intent", "unknown")

                # If model still returns unknown for a non-trivial input, use keyword fallback
                if intent == "unknown" and len(transcript.split()) >= 2:
                    logger.info(f"[NLU] Ollama→unknown, trying keyword fallback for: {transcript!r}")
                    fallback = self._keyword_fallback(transcript)
                    if fallback["intent"] != "unknown":
                        return fallback

                ents = result.get("entities", {})
                logger.info(
                    f"[NLU] intent={intent} conf={result.get('confidence', 0):.2f} "
                    f"flight={ents.get('flight_number')} brand={ents.get('brand_name')} "
                    f"facility={ents.get('facility_type')} gate={ents.get('gate_code')}"
                )
                return result

            except (requests.ConnectionError, requests.Timeout):
                if attempt < retries:
                    logger.warning(f"[NLU] Ollama unreachable, retry {attempt}/{retries}")
                    time.sleep(backoff * attempt)
                else:
                    logger.warning("[NLU] Ollama unavailable — using keyword fallback")
                    return self._keyword_fallback(transcript)

            except Exception as e:
                logger.error(f"[NLU] {type(e).__name__}: {e}")
                return self._keyword_fallback(transcript)

        return self._keyword_fallback(transcript)

    def add_agent_turn(self, agent_text: str) -> None:
        if agent_text.strip():
            self._history.append({"role": "agent", "text": agent_text.strip()})

    def reset(self) -> None:
        self._history.clear()

    # ── Internal helpers ──────────────────────────────────────────────────

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

    def _keyword_fallback(self, transcript: str) -> dict:
        """Rule-based classifier used when Ollama returns unknown or is unavailable."""
        t        = transcript.lower().strip()
        entities = self._base_entities()

        # ── Extract entities inline ────────────────────────────────────
        # Brand
        for kw in sorted(_BRAND_MAP, key=len, reverse=True):
            if kw in t:
                entities["brand_name"] = _BRAND_MAP[kw]
                break

        # Facility
        for kw in sorted(_FACILITY_MAP, key=len, reverse=True):
            if kw in t:
                entities["facility_type"] = _FACILITY_MAP[kw]
                break

        # Flight number  e.g. 6E211, AI511, EK 539
        fn_match = re.search(r'\b([A-Z0-9]{2})\s*(\d{2,4})\b', transcript.upper())
        if fn_match:
            entities["flight_number"] = fn_match.group(1) + fn_match.group(2)

        # Gate code  e.g. B7, A22, gate B12
        gate_match = re.search(r'\b([AB]\d{1,2})\b', transcript.upper())
        if gate_match:
            entities["gate_code"] = gate_match.group(1)

        # Terminal
        if re.search(r'\bT1\b|terminal\s*1|domestic', t):
            entities["terminal"] = "T1"
        elif re.search(r'\bT2\b|terminal\s*2|international', t):
            entities["terminal"] = "T2"

        # Food item
        for food_kw in ["coffee", "cappuccino", "latte", "espresso", "burger", "pizza",
                        "sandwich", "idli", "dosa", "samosa", "chai", "tea", "juice",
                        "momo", "wrap", "pasta", "fries", "ice cream", "cake", "donut"]:
            if food_kw in t:
                entities["food_item"] = food_kw
                break

        # ── Detect intent ──────────────────────────────────────────────
        greet_words = ["hello", "hi ", " hi", "namaste", "hey ", "good morning",
                       "good evening", "good afternoon", "good night"]
        if any(w in t for w in greet_words) or t in ("hi", "hey", "hello"):
            return self._make("greeting", 0.9, entities)

        end_words = ["bye", "goodbye", "good bye", "take care", "see you",
                     "thanks bye", "thank you bye", "ok bye"]
        if any(w in t for w in end_words):
            return self._make("end_conversation", 0.9, entities)

        if entities["flight_number"] or any(w in t for w in [
            "flight", "delay", "delayed", "gate", "boarding", "board",
            "depart", "arriv", "status", "on time", "scheduled"
        ]):
            return self._make("flight_status", 0.85, entities)

        if any(w in t for w in ["lounge", "plaza premium", "encalm", "skylight lounge",
                                  "business class lounge", "first class lounge"]):
            return self._make("lounge_info", 0.85, entities)

        if any(w in t for w in ["check-in", "checkin", "check in", "bag drop",
                                  "baggage drop", "self check", "kiosk"]):
            return self._make("checkin_help", 0.85, entities)

        if any(w in t for w in ["baggage", "luggage", "belt", "lost bag",
                                  "lost luggage", "mera bag"]):
            return self._make("baggage_info", 0.85, entities)

        if any(w in t for w in ["taxi", "metro", "bus", "parking", "cab",
                                  "uber", "ola", "car rental", "transport",
                                  "pick up", "drop off", "pickup"]):
            return self._make("transport_info", 0.85, entities)

        if entities["facility_type"] or any(w in t for w in [
            "atm", "cash", "prayer", "toilet", "restroom", "loo",
            "smoking", "medical", "doctor", "wifi", "wi-fi",
            "currency", "exchange", "pharmacy", "baby", "nursing"
        ]):
            return self._make("service_info", 0.85, entities)

        if entities["brand_name"] or entities["food_item"] or any(w in t for w in [
            "food", "eat", "drink", "menu", "price", "hungry",
            "snack", "restaurant", "cafe", "khaana", "khana",
            "breakfast", "lunch", "dinner", "meal", "coffee shop"
        ]):
            return self._make("food_query", 0.82, entities)

        if any(w in t for w in ["shop", "store", "buy", "duty free", "dutyfree",
                                  "luxury", "retail", "gucci", "fashion", "brand"]):
            return self._make("shop_query", 0.82, entities)

        nav_words = ["where", "how to get", "direction", "navigate", "find",
                     "locate", "kahan", "show me", "which way", "how do i go"]
        if entities["gate_code"] or any(w in t for w in nav_words):
            return self._make("navigation", 0.8, entities)

        if any(w in t for w in ["suggest", "recommend", "best", "which is good",
                                  "what should", "help me choose", "what do you",
                                  "popular", "favourite", "what can"]):
            return self._make("recommend", 0.78, entities)

        if any(w in t for w in ["wheelchair", "special assist", "accessibility",
                                  "accessible", "disabled"]):
            return self._make("accessibility", 0.85, entities)

        if any(w in t for w in ["staff", "manager", "human", "real person",
                                  "help desk", "helpdesk", "someone"]):
            return self._make("escalate", 0.8, entities)

        if any(w in t for w in ["thanks", "thank you", "shukriya", "dhanyawad", "ok done"]):
            return self._make("end_conversation", 0.8, entities)

        return self._empty()

    def _make(self, intent: str, confidence: float, entities: dict) -> dict:
        logger.info(f"[NLU] keyword→{intent} ({confidence:.2f})")
        return {
            "intent":     intent,
            "confidence": confidence,
            "entities":   entities,
            "language":   "english",
        }

    def _base_entities(self) -> dict:
        return {
            "flight_number": None, "airline":       None, "destination": None,
            "terminal":      None, "gate_code":     None, "food_item":   None,
            "brand_name":    None, "facility_type": None, "floor_ref":   None,
        }

    def _empty(self) -> dict:
        return self._make("unknown", 0.0, self._base_entities())
