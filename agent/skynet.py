"""
agent/skynet.py — AeroGuide: AI companion for Skylight International Airport (SLA).

Responsibilities per turn:
  1. Classify intent (NLU)
  2. Resolve entities → DB lookups
  3. Generate response (Ollama local LLM)
  4. Persist turn to session DB
  5. Return structured result for API layer
"""

import os
import re
import json
import logging
import sqlite3
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

from agent.prompt import SYSTEM_PROMPT, RESPONSE_TEMPLATE
from nlu.intent import IntentClassifier
from nlu import entities as ent

logger = logging.getLogger(__name__)

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
IST          = timezone(timedelta(hours=5, minutes=30))


# ── ConversationMemory ─────────────────────────────────────────────────────

class ConversationMemory:
    def __init__(self, session_id: str):
        self.session_id    = session_id
        self.turn          = 0
        self.history: list[dict] = []
        self.agent_turns: dict[int, str] = {}

        self.flight_number: Optional[str] = None
        self.terminal:      Optional[str] = None
        self.last_intent:   Optional[str] = None

    def update(self, transcript: str, intent: str, entities: dict) -> None:
        self.turn += 1
        self.last_intent = intent

        if entities.get("flight_number"):
            self.flight_number = entities["flight_number"]
        if entities.get("terminal"):
            self.terminal = ent._normalise_terminal(entities["terminal"])

        self.history.append({
            "turn":          self.turn,
            "speaker":       "traveler",
            "text":          transcript,
            "intent":        intent,
            "flight_number": self.flight_number,
            "terminal":      self.terminal,
        })

    def record_agent(self, text: str) -> None:
        self.agent_turns[self.turn] = text
        self.history.append({
            "turn":    self.turn,
            "speaker": "agent",
            "text":    text,
        })

    def conversation_str(self) -> str:
        if not self.history:
            return "No prior turns."
        lines = []
        for h in self.history[-12:]:
            tag = "Traveler" if h["speaker"] == "traveler" else "AeroGuide"
            lines.append(f"{tag}: {h['text']}")
        return "\n".join(lines)

    def context_str(self) -> str:
        parts = []
        if self.flight_number:
            parts.append(f"Known flight: {self.flight_number}")
        if self.terminal:
            parts.append(f"Known terminal: {self.terminal}")
        return "\n".join(parts) if parts else "No prior context."


# ── AeroGuide ──────────────────────────────────────────────────────────────

class AeroGuide:
    """Main airport AI companion agent."""

    def __init__(self):
        self.intent_classifier = IntentClassifier()
        DB_PATH = os.getenv("DB_PATH", "db/airport.db")
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        logger.info("[AeroGuide] Initialized.")

    # ── Session ──────────────────────────────────────────────────────────

    def new_session(self, session_id: str) -> dict:
        memory = ConversationMemory(session_id)
        self.intent_classifier.reset()
        logger.info(f"[AeroGuide] New session: {session_id}")
        return {
            "session_id": session_id,
            "memory":     memory,
            "turn":       0,
        }

    # ── Turn processing ───────────────────────────────────────────────────

    def run_turn_text(self, text: str, session: dict) -> dict:
        t0 = time.time()
        result = self._process_turn(text, session)
        result["latency_ms"] = int((time.time() - t0) * 1000)
        return result

    def _process_turn(self, text: str, session: dict) -> dict:
        memory: ConversationMemory = session["memory"]
        session["turn"] += 1

        intent_result = self.intent_classifier.classify(text)
        intent        = intent_result["intent"]
        entities      = intent_result.get("entities", {})
        language      = intent_result.get("language", "english")

        db_results = self._fetch_db_data(intent, entities, memory)

        memory.update(text, intent, entities)

        response = self._generate_response(
            transcript = text,
            intent     = intent,
            db_results = db_results,
            memory     = memory,
        )

        response_text = response.get("response", "")
        followup      = response.get("followup")
        map_hint      = response.get("map_hint", {})

        memory.record_agent(response_text)
        self.intent_classifier.add_agent_turn(response_text)

        return {
            "session_id":  session["session_id"],
            "turn":        session["turn"],
            "transcript":  text,
            "intent":      intent,
            "entities":    entities,
            "response":    response_text,
            "followup":    followup,
            "map_hint":    map_hint,
            "end_session": intent == "end_conversation",
            "language":    language,
        }

    # ── DB fetch routing ──────────────────────────────────────────────────

    def _fetch_db_data(self, intent: str, entities: dict,
                       memory: ConversationMemory) -> dict:
        results: dict = {}

        flight_number = entities.get("flight_number") or memory.flight_number
        brand_name    = entities.get("brand_name")
        food_item     = entities.get("food_item")
        facility_type = entities.get("facility_type")
        gate_code     = entities.get("gate_code")
        terminal      = entities.get("terminal") or memory.terminal
        destination   = entities.get("destination")

        if intent == "greeting":
            # Give an overview so the agent can mention what's available
            results["restaurants"] = self._all_restaurants(terminal)[:6]
            results["popular_items"] = ent.resolve_popular_items(8)

        elif intent == "flight_status":
            if flight_number:
                rows = ent.resolve_flight(flight_number)
                if rows:
                    results["flights"] = rows
            elif destination:
                rows = ent.resolve_flights_by_destination(destination)
                if rows:
                    results["flights_by_dest"] = rows

        elif intent in ("food_query", "recommend"):
            if brand_name:
                results["restaurants"] = ent.resolve_restaurant(brand_name, terminal)
                results["menu"]        = ent.resolve_menu(brand_name, food_item)
            elif food_item:
                results["menu_search"] = ent.resolve_food_item(food_item)
            else:
                # Generic food query — give restaurants + popular affordable items
                results["restaurants"]   = self._all_restaurants(terminal)[:10]
                results["popular_items"] = ent.resolve_popular_items(12)

        elif intent == "shop_query":
            if brand_name:
                results["shops"] = self._find_shop(brand_name, terminal)

        elif intent == "lounge_info":
            results["lounges"] = ent.resolve_lounge(terminal)

        elif intent == "checkin_help":
            results["checkin"] = ent.resolve_checkin(terminal)

        elif intent == "baggage_info":
            results["baggage_belts"] = ent.resolve_baggage_belt(flight_number, terminal)

        elif intent == "navigation":
            if gate_code:
                g = ent.resolve_gate(gate_code)
                if g:
                    results["gate"] = g
            if facility_type:
                results["facilities"] = ent.resolve_facility(facility_type, terminal)
            if brand_name:
                results["restaurants"] = ent.resolve_restaurant(brand_name, terminal)
            # Navigation with just a brand name (no gate/facility) — still useful
            if not gate_code and not facility_type and not brand_name:
                results["facilities"] = ent.resolve_facility("restroom", terminal)

        elif intent == "service_info":
            if facility_type:
                results["facilities"] = ent.resolve_facility(facility_type, terminal)
            else:
                # Return a mix of common services
                results["facilities"] = (
                    ent.resolve_facility("ATM", terminal) +
                    ent.resolve_facility("Currency Exchange", terminal)[:2]
                )

        elif intent == "transport_info":
            results["parking"] = ent.resolve_parking(terminal)

        return results

    def _all_restaurants(self, terminal: str = None) -> list[dict]:
        try:
            params: list = []
            q = """
                SELECT b.name AS brand_name, r.cuisine, r.avg_price_inr,
                       r.open_time, r.is_24_hours,
                       f.floor_code, t.code AS terminal_code
                FROM restaurants r
                JOIN brands b ON r.brand_id = b.brand_id
                JOIN floors f ON r.floor_id = f.floor_id
                JOIN terminals t ON f.terminal_id = t.terminal_id
            """
            if terminal:
                tc = ent._normalise_terminal(terminal)
                if tc:
                    q += " WHERE t.code = ?"
                    params.append(tc)
            q += " ORDER BY t.code, r.avg_price_inr"
            rows = self.conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[AeroGuide] all_restaurants failed: {e}")
            return []

    def _find_shop(self, brand_name: str, terminal: str = None) -> list[dict]:
        try:
            canonical = ent.BRAND_ALIASES.get(brand_name.lower(), brand_name)
            params    = [f"%{canonical}%"]
            q = """
                SELECT s.shop_code, s.open_time, s.close_time, s.is_24_hours,
                       b.name AS brand_name, b.category,
                       f.floor_code, f.floor_name,
                       t.code AS terminal_code,
                       z.zone_name
                FROM shops s
                JOIN brands b ON s.brand_id = b.brand_id
                JOIN floors f ON s.floor_id = f.floor_id
                JOIN terminals t ON f.terminal_id = t.terminal_id
                LEFT JOIN zones z ON s.zone_id = z.zone_id
                WHERE LOWER(b.name) LIKE LOWER(?)
            """
            if terminal:
                tc = ent._normalise_terminal(terminal)
                if tc:
                    q += " AND t.code = ?"
                    params.append(tc)
            rows = self.conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[AeroGuide] shop lookup failed: {e}")
            return []

    # ── LLM response generation ───────────────────────────────────────────

    def _generate_response(self, transcript: str, intent: str,
                           db_results: dict, memory: ConversationMemory) -> dict:
        now = datetime.now(IST)
        db_summary = self._summarise_db(db_results)

        prompt = RESPONSE_TEMPLATE.format(
            conversation_history = memory.conversation_str(),
            transcript           = transcript,
            intent               = intent,
            db_results           = db_summary,
            session_context      = memory.context_str(),
            current_date         = now.strftime("%A, %d %B %Y"),
            current_time         = now.strftime("%I:%M %p"),
        )

        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model":   OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.4, "num_predict": 300},
                },
                timeout=60,
            )
            resp.raise_for_status()
            raw   = resp.json()["message"]["content"].strip()
            start = raw.find("{")
            end   = raw.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(raw[start:end + 1])
                if "response" in parsed:
                    parsed["response"] = self._clean(parsed["response"])
                if parsed.get("followup"):
                    parsed["followup"] = self._clean(parsed["followup"])
                logger.info(f"[AeroGuide] Response: '{parsed.get('response','')[:80]}'")
                return parsed

        except (requests.ConnectionError, requests.Timeout):
            logger.error("[AeroGuide] Ollama not reachable — is 'ollama serve' running?")
        except Exception as e:
            logger.error(f"[AeroGuide] LLM failed: {e}")

        return self._fallback(intent)

    def _summarise_db(self, db_results: dict) -> str:
        if not db_results:
            return "No database results found."

        lines: list[str] = []

        if "flights" in db_results:
            for f in db_results["flights"]:
                direction = f.get("direction", "")
                city = f.get("dest_city") if direction == "DEPARTURE" else f.get("origin_city")
                lines.append(
                    f"Flight {f['flight_number']} ({f['airline_name']}) | "
                    f"{direction} to/from {city} | "
                    f"Scheduled: {f['scheduled_time']} | "
                    f"Gate: {f.get('gate_code','?')} | "
                    f"Terminal: {f.get('terminal_code','?')} | "
                    f"Status: {f.get('status_default','Unknown')}"
                    + (f" | Baggage Belt: {f['belt_code']}" if f.get("belt_code") else "")
                )

        if "flights_by_dest" in db_results:
            for f in db_results["flights_by_dest"][:5]:
                lines.append(
                    f"Flight {f['flight_number']} ({f['airline_name']}) | "
                    f"{f['direction']} | Scheduled: {f['scheduled_time']} | "
                    f"Gate: {f.get('gate_code','?')} | Status: {f.get('status_default','?')}"
                )

        if "restaurants" in db_results:
            for r in db_results["restaurants"]:
                hours = "Open 24h" if r.get("is_24_hours") else f"{r.get('open_time','?')}–{r.get('close_time','?')}"
                lines.append(
                    f"Restaurant: {r['brand_name']} | Cuisine: {r['cuisine']} | "
                    f"Avg price: {r.get('avg_price_inr','?')} rupees | "
                    f"{r.get('terminal_code','?')} {r.get('floor_code','?')} | {hours}"
                )

        if "menu" in db_results:
            for m in db_results["menu"][:10]:
                veg = "(veg)" if m.get("is_vegetarian") else "(non-veg)"
                lines.append(
                    f"Menu item: {m['item_name']} {veg} — {m['price_inr']} rupees | "
                    f"at {m['brand_name']} | Category: {m.get('category','?')}"
                )

        if "popular_items" in db_results:
            for m in db_results["popular_items"][:8]:
                veg = "(veg)" if m.get("is_vegetarian") else "(non-veg)"
                lines.append(
                    f"Popular: {m['item_name']} {veg} — {m['price_inr']} rupees "
                    f"at {m['brand_name']} ({m.get('terminal_code','?')} {m.get('floor_code','?')})"
                )

        if "menu_search" in db_results:
            seen_brands: set = set()
            for m in db_results["menu_search"][:12]:
                veg = "(veg)" if m.get("is_vegetarian") else "(non-veg)"
                brand = m["brand_name"]
                lines.append(
                    f"{m['item_name']} {veg} — {m['price_inr']} rupees at {brand} "
                    f"({m.get('terminal_code','?')} {m.get('floor_code','?')})"
                )
                seen_brands.add(brand)

        if "gate" in db_results:
            g = db_results["gate"]
            lines.append(
                f"Gate {g['gate_code']} | {g.get('terminal_code','?')} "
                f"{g.get('floor_code','?')} | Zone: {g.get('zone_name','?')} | "
                f"Jet bridge: {'Yes' if g.get('has_jet_bridge') else 'No'}"
            )

        if "facilities" in db_results:
            for f in db_results["facilities"][:6]:
                if f.get("kind") == "restroom":
                    lines.append(
                        f"Restroom ({f.get('type','?')}) | "
                        f"{f.get('terminal_code','?')} {f.get('floor_code','?')}"
                        + (" | Baby change available" if f.get("has_baby_change") else "")
                    )
                else:
                    open_str = "Open 24h" if f.get("open_24") else "Check timings"
                    lines.append(
                        f"Service: {f.get('name', f.get('type','?'))} | "
                        f"{f.get('terminal_code','?')} {f.get('floor_code','?')} | {open_str}"
                    )

        if "lounges" in db_results:
            for l in db_results["lounges"]:
                features = []
                if l.get("has_shower"):    features.append("shower")
                if l.get("has_bar"):       features.append("bar")
                if l.get("has_meal"):      features.append("meals")
                if l.get("has_workspace"): features.append("workspace")
                lines.append(
                    f"Lounge: {l['name']} ({l['tier']}) | "
                    f"Operator: {l['operator']} | "
                    f"{l.get('terminal_code','?')} {l.get('floor_code','?')} | "
                    f"Hours: {l.get('open_time','?')}–{l.get('close_time','?')} | "
                    f"Features: {', '.join(features) or 'standard'}"
                )

        if "checkin" in db_results:
            summary: dict = {}
            for c in db_results["checkin"]:
                tc = c.get("terminal_code", "?")
                summary.setdefault(tc, {"standard": 0, "bag_drop": 0, "premium": 0, "self_service": 0})
                t = c.get("type", "").lower()
                if "premium" in t:   summary[tc]["premium"]     += 1
                elif "bag"   in t:   summary[tc]["bag_drop"]    += 1
                elif "self"  in t:   summary[tc]["self_service"] += 1
                else:                summary[tc]["standard"]    += 1
            for tc, counts in summary.items():
                lines.append(
                    f"Check-in {tc}: {counts['standard']} standard, "
                    f"{counts['bag_drop']} bag-drop, {counts['premium']} premium, "
                    f"{counts['self_service']} self-service kiosks — Level 1 Check-in Hall"
                )

        if "baggage_belts" in db_results:
            for b in db_results["baggage_belts"]:
                fn = f" for flight {b['flight_number']}" if b.get("flight_number") else ""
                lines.append(
                    f"Baggage Belt {b['belt_code']}{fn} | "
                    f"{b.get('terminal_code','?')} {b.get('floor_code','?')}"
                )

        if "parking" in db_results:
            for p in db_results["parking"]:
                rate = f"{p['rate_inr_hr']} rupees/hr" if p["rate_inr_hr"] else "Free"
                lines.append(f"Parking {p['code']} ({p['type']}) — {rate} | {p['capacity']} spaces")

        if "shops" in db_results:
            for s in db_results["shops"]:
                hours = "Open 24h" if s.get("is_24_hours") else f"{s.get('open_time','?')}–{s.get('close_time','?')}"
                lines.append(
                    f"Shop: {s['brand_name']} ({s.get('category','?')}) | "
                    f"{s.get('terminal_code','?')} {s.get('floor_code','?')} | "
                    f"Zone: {s.get('zone_name','?')} | {hours}"
                )

        return "\n".join(lines) if lines else "No relevant records found in database."

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            return text
        text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
        text = text.replace('\\n', ' ')
        text = re.sub(r'\s*\n\s*', ' ', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

    def _fallback(self, intent: str) -> dict:
        if intent == "flight_status":
            msg = "I couldn't find that flight in our system. Please check the FIDS display boards or ask at the info desk."
        elif intent in ("food_query", "recommend"):
            msg = "Both T1 and T2 have great food options. T1's Food Court on Level 2 has Haldiram's, McDonald's, Starbucks and more. T2's Dining Boulevard has those plus Pizza Hut and Costa Coffee."
        elif intent == "navigation":
            msg = "Sorry, I couldn't pinpoint that location. The info desk on Ground Floor can help with directions."
        else:
            msg = "I'm here to help with flights, food, navigation, and airport services. What would you like to know?"
        return {"response": msg, "followup": None, "map_hint": {}}
