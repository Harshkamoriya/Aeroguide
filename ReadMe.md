# AeroGuide — Offline AI Airport Companion

An offline-first, AI-powered airport companion for **Skylight International Airport (SLA), Surat, India**. AeroGuide helps travelers navigate the airport, check flight status, find food and services, and get real-time assistance — entirely without an internet connection.

---

## What This Project Does

AeroGuide is a conversational AI assistant designed to be deployed on kiosks or accessed via a web browser inside the airport. Travelers can speak or type natural-language questions and receive helpful, context-aware answers about:

- Flight status, gates, and delays
- Restaurants, cafes, and menus
- Navigation to gates, terminals, and facilities
- Check-in counters, baggage, and boarding
- Lounges, ATMs, prayer rooms, medical services, and more

The system remembers context across a conversation (e.g., "your flight is at Gate B12, Terminal 2") and can proactively alert users about delayed flights.

---

## Architecture Overview

```
User (Voice / Text / Web)
        |
   [FastAPI Server]  <--- WebSocket / REST
        |
  +-----+------+
  |   NLU      |  Intent classification + entity extraction (Ollama LLM + keyword fallback)
  |   Agent    |  Conversation memory, session state, turn management
  |   DB       |  SQLite - airport data, sessions, turn history
  |   STT      |  OpenAI Whisper (offline)
  |   TTS      |  pyttsx3 OS voices (offline)
  +------------+
```

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI + Uvicorn |
| LLM (local) | Ollama — Llama 3.2 |
| Speech-to-Text | OpenAI Whisper (offline) |
| Text-to-Speech | pyttsx3 (OS native voices) |
| Database | SQLite3 |
| Frontend | Vanilla JS + WebSocket |
| Audio | sounddevice, soundfile, librosa, numpy |
| Config | python-dotenv |

---

## Project Structure

```
AI Companion/
├── agent/          # Main AI agent orchestrator + system prompts
├── api/            # FastAPI REST + WebSocket server
├── db/             # SQLite setup, session manager, airport data
├── nlu/            # Intent classifier + entity resolver
├── stt/            # Whisper speech-to-text
├── tts/            # pyttsx3 text-to-speech
├── web/            # Browser frontend (HTML, CSS, JS)
├── speech.py       # CLI voice/text client
├── requirements.txt
└── .env.example
```

---

## Key Features

- **Fully offline** — no cloud APIs, no API keys, no internet required
- **Multi-modal** — voice (microphone), text input, and web chat interface
- **Real-time WebSocket chat** with per-session conversation history
- **Context-aware** — remembers flight, terminal, and preferences across turns
- **Proactive alerts** — can push delayed flight notifications to connected users
- **Two-tier NLU** — Ollama LLM primary classifier with keyword fallback when LLM is unavailable
- **13 intent types** — from flight status to accessibility to escalation

---

## Prerequisites

1. **Python 3.10+**
2. **Ollama** installed and running locally
   ```bash
   ollama serve
   ollama pull llama3.2
   ```
3. Install Python dependencies
   ```bash
   pip install -r requirements.txt
   ```
4. Initialize the airport database
   ```bash
   python -m db.setup
   ```

---

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```env
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
DB_PATH=db/airport.db
PORT=8000
WHISPER_MODEL=base
PYTTSX3_RATE=165
PYTTSX3_VOICE_NAME=
AEROGUIDE_URL=http://localhost:8000
```

---

## Running the Server

```bash
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000` in a browser to use the web chat interface.

---

## CLI Usage

```bash
# Text mode
python speech.py --text --loop

# Voice mode (5-second recordings)
python speech.py --loop --duration 5

# Voice mode, no audio output
python speech.py --loop --no-audio-out
```

---

## Airport Model

Built for **Skylight International Airport (SLA), Surat, India**:

- **Terminal 1** — Domestic (IndiGo, Air India, Vistara, SpiceJet, Akasa Air)
- **Terminal 2** — International (Emirates, Qatar Airways, Lufthansa, Singapore Airlines, etc.)
- **Gates** — T1: A1–A22 | T2: B1–B28
- **Floors** — Ground (arrivals, ATMs) > L1 (check-in, departures) > L2 (gates, food) > L3 (lounges)
- **Restaurants** — Starbucks, McDonald's, KFC, Haldiram's, CCD, Costa Coffee, Pizza Hut, Subway, Domino's, and more
- **Services** — ATMs, currency exchange, prayer rooms, medical, parking, duty-free, lounges

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/session` | POST | Create a new chat session |
| `/chat/{session_id}` | POST | Send a text message |
| `/ws/{session_id}` | WS | Real-time WebSocket chat |
| `/flight/{number}` | GET | Flight status lookup |
| `/food` | GET | Restaurant / menu search |
| `/navigate` | GET | Gate / facility navigation |
| `/lounges` | GET | Lounge information |
| `/alerts/delayed-flights` | GET | List of delayed flights |

---

## License

This project is for educational and demonstration purposes.
