"""
api/server.py — AeroGuide FastAPI server.

Endpoints:
  POST   /session              — create a new conversation session
  POST   /chat/{session_id}    — send a text message, get a response
  GET    /session/{session_id} — fetch session history
  DELETE /session/{session_id} — end session

  GET    /flight/{flight_number}       — quick flight status lookup
  GET    /food                         — food search (query params: brand, item, terminal)
  GET    /navigate                     — navigation lookup (gate, facility, terminal)
  GET    /lounges                      — lounge info (query param: terminal)

  GET    /alerts/delayed-flights       — proactive: all non-On-Time flights
  GET    /health                       — health check

  POST   /test/transcribe     — upload WAV bytes → {"text", "confidence"}
  POST   /test/synthesize     — {"text"} → WAV audio bytes
  POST   /test/full           — {"text"} → full pipeline response (for speech.py)

WebSocket:
  WS     /ws/{session_id}     — real-time bidirectional chat
"""

import os
import time
import uuid
import logging
import threading
import requests as _requests

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from typing import Optional
from pydantic import BaseModel

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

ADMIN_KEY = os.getenv("ADMIN_KEY", "aeroguide2024")

from agent.skynet import AeroGuide
from db.session_manager import SessionManager
from nlu import entities as ent

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app   = FastAPI(title="AeroGuide — Airport AI Companion", version="1.0.0")
agent = AeroGuide()
sdb   = SessionManager()

def _warmup_ollama():
    """Load llama3.2 into RAM in the background so the first user message is fast."""
    ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    model      = os.getenv("OLLAMA_MODEL", "llama3.2")
    try:
        logger.info("[Warmup] Loading Ollama model into RAM...")
        _requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model":   model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream":  False,
                "options": {"num_predict": 1},
            },
            timeout=(5, 120),
        )
        logger.info("[Warmup] Ollama model ready.")
    except Exception as e:
        logger.warning(f"[Warmup] Could not pre-load model: {e}")

threading.Thread(target=_warmup_ollama, daemon=True).start()

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

_ws_clients: dict[str, WebSocket] = {}

# Lazy-loaded STT/TTS (avoid loading Whisper at startup unless needed)
_transcriber = None

def _get_transcriber():
    global _transcriber
    if _transcriber is None:
        from stt.transcriber import Transcriber
        _transcriber = Transcriber()
    return _transcriber


# ── Request / Response models ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    language: str = "english"

class SessionResponse(BaseModel):
    session_id: str
    message: str

class SynthRequest(BaseModel):
    text: str

class FullRequest(BaseModel):
    text: str
    session_id: str = "__cli__"
    user_lat: Optional[float] = None
    user_lng: Optional[float] = None

class RegisterRequest(BaseModel):
    name: str
    mobile_number: str   # E.164 format, e.g. +919876543210
    flight_number: str
    session_id: Optional[str] = None

class TerminalChangeRequest(BaseModel):
    flight_number: str
    new_terminal:  str
    new_gate:      Optional[str] = None
    admin_key:     str = "aeroguide2024"


# ── Session endpoints ──────────────────────────────────────────────────────

@app.post("/session", response_model=SessionResponse)
def create_session():
    session_id = str(uuid.uuid4())
    sdb.create(session_id)
    agent.new_session(session_id)
    logger.info(f"[API] New session: {session_id}")
    return {
        "session_id": session_id,
        "message":    "Session created. How can I help you today?",
    }


@app.get("/session/{session_id}")
def get_session(session_id: str):
    meta = sdb.get(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found.")
    turns = sdb.get_turns(session_id)
    return {"session": meta, "turns": turns}


@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    sdb.delete(session_id)
    return {"status": "deleted", "session_id": session_id}


# ── Chat endpoint ──────────────────────────────────────────────────────────

@app.post("/chat/{session_id}")
def chat(session_id: str, body: ChatRequest):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    sdb.create(session_id)

    if not hasattr(agent, "_sessions"):
        agent._sessions = {}
    if session_id not in agent._sessions:
        agent._sessions[session_id] = agent.new_session(session_id)
    session = agent._sessions[session_id]

    result = agent.run_turn_text(body.message, session)

    turn = result["turn"]
    sdb.save_turn(session_id, turn * 2 - 1, "traveler", body.message, result["intent"])
    sdb.save_turn(session_id, turn * 2,     "agent",    result["response"])
    sdb.touch(
        session_id,
        flight_number = result["entities"].get("flight_number"),
        terminal      = result["entities"].get("terminal"),
    )

    return {
        "session_id":  session_id,
        "turn":        result["turn"],
        "intent":      result["intent"],
        "response":    result["response"],
        "followup":    result.get("followup"),
        "map_hint":    result.get("map_hint", {}),
        "end_session": result.get("end_session", False),
    }


# ── WebSocket real-time chat ───────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()
    _ws_clients[session_id] = websocket

    sdb.create(session_id)
    if not hasattr(agent, "_sessions"):
        agent._sessions = {}
    if session_id not in agent._sessions:
        agent._sessions[session_id] = agent.new_session(session_id)
    session = agent._sessions[session_id]

    await websocket.send_json({
        "type":     "greeting",
        "response": "Welcome to Skylight International Airport! I'm AeroGuide. "
                    "How can I help you today?",
    })
    logger.info(f"[WS] Connected: {session_id}")

    try:
        while True:
            data = await websocket.receive_json()
            msg  = (data.get("message") or "").strip()
            if not msg:
                continue

            result = agent.run_turn_text(msg, session)

            turn = result["turn"]
            sdb.save_turn(session_id, turn * 2 - 1, "traveler", msg, result["intent"])
            sdb.save_turn(session_id, turn * 2,     "agent",    result["response"])
            sdb.touch(
                session_id,
                flight_number = result["entities"].get("flight_number"),
                terminal      = result["entities"].get("terminal"),
            )

            await websocket.send_json({
                "type":        "response",
                "session_id":  session_id,
                "turn":        result["turn"],
                "intent":      result["intent"],
                "response":    result["response"],
                "followup":    result.get("followup"),
                "map_hint":    result.get("map_hint", {}),
                "end_session": result.get("end_session", False),
            })

            if result.get("end_session"):
                await websocket.send_json({"type": "goodbye"})
                break

    except WebSocketDisconnect:
        logger.info(f"[WS] Disconnected: {session_id}")
    finally:
        _ws_clients.pop(session_id, None)


# ── Proactive alert push ───────────────────────────────────────────────────

async def push_flight_alert(session_id: str, flight_number: str,
                            message: str) -> bool:
    ws = _ws_clients.get(session_id)
    if not ws:
        return False
    try:
        await ws.send_json({
            "type":          "proactive_alert",
            "flight_number": flight_number,
            "message":       message,
        })
        return True
    except Exception as e:
        logger.warning(f"[WS] Alert push failed for {session_id}: {e}")
        return False


# ── /test/* endpoints (for speech.py CLI client) ───────────────────────────

@app.post("/test/transcribe")
def test_transcribe(audio: UploadFile = File(...)):
    """
    Accept a WAV file upload and return the transcribed text.
    Usage: POST multipart/form-data with field 'audio' containing WAV bytes.
    """
    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    try:
        transcriber = _get_transcriber()
        result      = transcriber.transcribe(audio_bytes)
        return {"text": result["text"], "confidence": result["confidence"]}
    except Exception as e:
        logger.error(f"[/test/transcribe] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/test/synthesize")
def test_synthesize(body: SynthRequest):
    """
    Accept JSON {"text": "..."} and return WAV audio bytes.
    """
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    try:
        from tts.synthesizer import speak_to_bytes
        wav_bytes = speak_to_bytes(body.text)
        return Response(content=wav_bytes, media_type="audio/wav")
    except Exception as e:
        logger.error(f"[/test/synthesize] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/test/full")
def test_full(body: FullRequest):
    """
    Accept JSON {"text": "...", "session_id": "..."} and run the full pipeline:
    NLU → DB → LLM → response.
    Returns intent, entities, response, followup, map_hint.
    """
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    session_id = body.session_id or "__cli__"

    if not hasattr(agent, "_sessions"):
        agent._sessions = {}
    if session_id not in agent._sessions:
        sdb.create(session_id)
        agent._sessions[session_id] = agent.new_session(session_id)
    session = agent._sessions[session_id]
    print("Hello")
    result = agent.run_turn_text(
        body.text, session,
        user_lat=body.user_lat,
        user_lng=body.user_lng,
    )
    print("Hello")

    turn = result["turn"]
    sdb.save_turn(session_id, turn * 2 - 1, "traveler", body.text, result["intent"])
    sdb.save_turn(session_id, turn * 2,     "agent",    result["response"])

    return {
        "session_id":  session_id,
        "turn":        result["turn"],
        "intent":      result["intent"],
        "entities":    result["entities"],
        "response":    result["response"],
        "followup":    result.get("followup"),
        "map_hint":    result.get("map_hint", {}),
        "directions":  result.get("directions"),
        "end_session": result.get("end_session", False),
        "latency_ms":  result.get("latency_ms"),
    }


# ── User registration & Twilio calling ───────────────────────────────────

@app.post("/register")
def register_user(body: RegisterRequest):
    """Save traveler name + mobile + flight so they get an automated departure call."""
    number = body.mobile_number.strip()
    if not number.startswith("+"):
        raise HTTPException(
            status_code=400,
            detail="mobile_number must be in E.164 format, e.g. +919876543210",
        )
    flight = body.flight_number.strip().upper()
    if not flight:
        raise HTTPException(status_code=400, detail="flight_number cannot be empty.")

    import sqlite3 as _sqlite3
    db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "airport.db"))
    conn = _sqlite3.connect(db_path)
    try:
        # Ensure users table exists (handles DBs created before this feature)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    NOT NULL,
                mobile_number  TEXT    NOT NULL,
                flight_number  TEXT    NOT NULL,
                session_id     TEXT,
                call_sent      INTEGER DEFAULT 0,
                registered_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO users (name, mobile_number, flight_number, session_id) VALUES (?, ?, ?, ?)",
            (body.name.strip(), number, flight, body.session_id),
        )
        conn.commit()
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()

    logger.info(f"[Register] user_id={user_id} name={body.name!r} flight={flight} mobile={number}")
    return {
        "status":        "registered",
        "user_id":       user_id,
        "name":          body.name.strip(),
        "flight_number": flight,
        "message":       f"You will receive a call when your flight {flight} departs in ~30 minutes.",
    }


def _flight_alert_loop() -> None:
    """Background daemon: every 5 minutes, call users whose flights depart in ~30 min."""
    import sqlite3 as _sqlite3
    from calls.twilio_caller import make_departure_call

    db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "airport.db"))

    logger.info("[FlightAlert] Background scheduler started.")
    while True:
        try:
            conn = _sqlite3.connect(db_path)
            conn.row_factory = _sqlite3.Row
            # Flights departing in 25–35 minutes from now (local time)
            rows = conn.execute("""
                SELECT
                    u.user_id,
                    u.name,
                    u.mobile_number,
                    u.flight_number,
                    g.gate_code,
                    t.code     AS terminal_code,
                    r.dest_city
                FROM users u
                JOIN flight_schedule fs ON UPPER(fs.flight_number) = UPPER(u.flight_number)
                JOIN gates     g  ON g.gate_id     = fs.gate_id
                JOIN terminals t  ON t.terminal_id = fs.terminal_id
                JOIN routes    r  ON r.route_id    = fs.route_id
                WHERE u.call_sent = 0
                  AND fs.direction = 'departure'
                  AND time(fs.scheduled_time) BETWEEN
                        time('now', '+25 minutes', 'localtime') AND
                        time('now', '+35 minutes', 'localtime')
            """).fetchall()
            conn.close()

            for row in rows:
                sid = make_departure_call(
                    to_number     = row["mobile_number"],
                    name          = row["name"],
                    flight_number = row["flight_number"],
                    destination   = row["dest_city"],
                    gate_code     = row["gate_code"],
                    terminal      = row["terminal_code"],
                )
                # Mark as called (1) or failed (2)
                status = 1 if sid else 2
                upd = _sqlite3.connect(db_path)
                upd.execute("UPDATE users SET call_sent = ? WHERE user_id = ?",
                            (status, row["user_id"]))
                upd.commit()
                upd.close()
                logger.info(
                    f"[FlightAlert] user_id={row['user_id']} flight={row['flight_number']} "
                    f"→ call_sent={status}"
                )

        except Exception as e:
            logger.error(f"[FlightAlert] Scheduler error: {e}")

        time.sleep(300)   # check every 5 minutes


threading.Thread(target=_flight_alert_loop, daemon=True).start()


# ── Test call endpoint ─────────────────────────────────────────────────────

class TestCallRequest(BaseModel):
    to_number:     str
    name:          str   = "Traveler"
    flight_number: str   = "6E211"
    destination:   str   = "Mumbai"
    gate_code:     str   = "A5"
    terminal:      str   = "T1"

@app.post("/test/call")
def test_call(body: TestCallRequest):
    """Fire an immediate Twilio test call to a verified number."""
    from calls.twilio_caller import make_departure_call
    sid = make_departure_call(
        to_number     = body.to_number,
        name          = body.name,
        flight_number = body.flight_number,
        destination   = body.destination,
        gate_code     = body.gate_code,
        terminal      = body.terminal,
    )
    if not sid:
        raise HTTPException(status_code=500,
                            detail="Call failed. Check Twilio credentials and verified numbers.")
    return {"status": "calling", "call_sid": sid, "to": body.to_number}


# ── Quick-lookup endpoints ─────────────────────────────────────────────────

@app.get("/flight/{flight_number}")
def get_flight(flight_number: str):
    rows = ent.resolve_flight(flight_number)
    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"Flight {flight_number.upper()} not found.")
    return {"flight_number": flight_number.upper(), "schedules": rows}


@app.get("/food")
def get_food(
    brand:    str = Query(None, description="Brand name, e.g. Starbucks"),
    item:     str = Query(None, description="Food item, e.g. cappuccino"),
    terminal: str = Query(None, description="T1 or T2"),
):
    if brand:
        restaurants = ent.resolve_restaurant(brand, terminal)
        menu        = ent.resolve_menu(brand, item)
        return {"restaurants": restaurants, "menu": menu}
    if item:
        return {"menu_search": ent.resolve_food_item(item)}
    raise HTTPException(status_code=400, detail="Provide 'brand' or 'item' query param.")


@app.get("/navigate")
def navigate(
    gate:     str = Query(None, description="Gate code, e.g. B7"),
    facility: str = Query(None, description="Facility type, e.g. ATM"),
    terminal: str = Query(None, description="T1 or T2"),
):
    result: dict = {}
    if gate:
        g = ent.resolve_gate(gate)
        if g:
            result["gate"] = g
        else:
            raise HTTPException(status_code=404, detail=f"Gate {gate} not found.")
    if facility:
        result["facilities"] = ent.resolve_facility(facility, terminal)
    if not result:
        raise HTTPException(status_code=400,
                            detail="Provide 'gate' or 'facility' query param.")
    return result


@app.get("/lounges")
def get_lounges(terminal: str = Query(None, description="T1 or T2")):
    return {"lounges": ent.resolve_lounge(terminal)}


@app.get("/alerts/delayed-flights")
def delayed_flights():
    delays = sdb.get_delayed_flights()
    return {"count": len(delays), "flights": delays}


@app.get("/health")
def health():
    return {"status": "ok", "service": "AeroGuide Airport AI Companion"}


# ── Admin endpoints ────────────────────────────────────────────────────────

def _admin_db_path():
    return os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "airport.db"))


@app.get("/admin/users")
def admin_users(admin_key: str = Query(...)):
    if admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key.")

    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(_admin_db_path())
    conn.row_factory = _sqlite3.Row

    users_rows = conn.execute("""
        SELECT user_id, name, mobile_number, flight_number, call_sent, registered_at
        FROM users ORDER BY flight_number, registered_at
    """).fetchall()

    flight_infos = {}
    for row in users_rows:
        fn = row["flight_number"].upper()
        if fn not in flight_infos:
            info = conn.execute("""
                SELECT g.gate_code, t.code AS terminal_code, r.dest_city, fs.scheduled_time
                FROM flight_schedule fs
                JOIN gates     g ON g.gate_id     = fs.gate_id
                JOIN terminals t ON t.terminal_id = fs.terminal_id
                JOIN routes    r ON r.route_id    = fs.route_id
                WHERE UPPER(fs.flight_number) = ? AND fs.direction = 'departure'
                LIMIT 1
            """, (fn,)).fetchone()
            flight_infos[fn] = dict(info) if info else {}
    conn.close()

    flights: dict = {}
    for row in users_rows:
        fn  = row["flight_number"].upper()
        inf = flight_infos.get(fn, {})
        if fn not in flights:
            flights[fn] = {
                "flight_number":  fn,
                "destination":    inf.get("dest_city")     or "Unknown",
                "terminal":       inf.get("terminal_code") or "Unknown",
                "gate_code":      inf.get("gate_code")     or "Unknown",
                "departure_time": inf.get("scheduled_time") or "Unknown",
                "users":          [],
            }
        flights[fn]["users"].append({
            "user_id":       row["user_id"],
            "name":          row["name"],
            "mobile_number": row["mobile_number"],
            "call_sent":     row["call_sent"],
            "registered_at": row["registered_at"],
        })

    flights_list = list(flights.values())
    for f in flights_list:
        f["user_count"] = len(f["users"])

    all_users = [u for f in flights_list for u in f["users"]]
    stats = {
        "total_users":   len(all_users),
        "calls_made":    sum(1 for u in all_users if u["call_sent"] == 1),
        "calls_failed":  sum(1 for u in all_users if u["call_sent"] == 2),
        "calls_pending": sum(1 for u in all_users if u["call_sent"] == 0),
    }
    return {"flights": flights_list, "stats": stats}


@app.post("/admin/terminal-change")
def admin_terminal_change(body: TerminalChangeRequest):
    if body.admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key.")

    from calls.twilio_caller import make_terminal_change_call
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(_admin_db_path())
    conn.row_factory = _sqlite3.Row
    flight = body.flight_number.strip().upper()

    schedule = conn.execute("""
        SELECT r.dest_city, fs.scheduled_time
        FROM flight_schedule fs
        LEFT JOIN routes r ON r.route_id = fs.route_id
        WHERE UPPER(fs.flight_number) = ? AND fs.direction = 'departure'
        LIMIT 1
    """, (flight,)).fetchone()
    destination = schedule["dest_city"] if schedule else "your destination"

    users = conn.execute(
        "SELECT user_id, name, mobile_number FROM users WHERE UPPER(flight_number) = ?",
        (flight,)
    ).fetchall()
    conn.close()

    results = []
    for user in users:
        sid = make_terminal_change_call(
            to_number     = user["mobile_number"],
            name          = user["name"],
            flight_number = flight,
            destination   = destination,
            new_terminal  = body.new_terminal,
            new_gate      = body.new_gate,
        )
        results.append({
            "user_id":  user["user_id"],
            "name":     user["name"],
            "status":   "called" if sid else "failed",
            "call_sid": sid,
        })

    called = sum(1 for r in results if r["status"] == "called")
    logger.info(f"[Admin] Terminal change {flight} → {body.new_terminal}: {called}/{len(results)} called")
    return {
        "flight_number":   flight,
        "new_terminal":    body.new_terminal,
        "new_gate":        body.new_gate,
        "calls_triggered": called,
        "results":         results,
    }


# ── Entry point ────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=True)
