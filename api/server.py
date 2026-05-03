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
import uuid
import logging

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

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

    result = agent.run_turn_text(body.text, session)

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
        "end_session": result.get("end_session", False),
        "latency_ms":  result.get("latency_ms"),
    }


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


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=True)
