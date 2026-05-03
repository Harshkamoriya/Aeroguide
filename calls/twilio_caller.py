"""
calls/twilio_caller.py — Twilio outbound call engine for AeroGuide.

Requires env vars:
  TWILIO_ACCOUNT_SID   — Twilio account SID
  TWILIO_AUTH_TOKEN    — Twilio auth token
  TWILIO_FROM_NUMBER   — Twilio phone number (E.164, e.g. +12015551234)
"""

import os
import logging

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER  = os.getenv("TWILIO_FROM_NUMBER")


def _spaced(flight: str) -> str:
    """'6E211' → '6 E 2 1 1' so TTS reads it letter-by-letter."""
    return " ".join(flight)


def _build_twiml(name: str, flight_number: str, destination: str,
                  gate_code: str, terminal: str) -> str:
    message = (
        f"Hello {name}. This is an automated message from Skylight International Airport. "
        f"Your flight {_spaced(flight_number)} to {destination} departs in approximately "
        f"30 minutes. Please proceed immediately to Gate {gate_code} in Terminal {terminal}. "
        f"Please have your boarding pass ready. We wish you a safe and pleasant flight. "
        f"Goodbye."
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say voice="Polly.Raveena" language="en-IN">{message}</Say>'
        '<Pause length="1"/>'
        f'<Say voice="Polly.Raveena" language="en-IN">{message}</Say>'
        "</Response>"
    )


def _call(to_number: str, twiml: str) -> str | None:
    """Internal: place a Twilio call with given TwiML. Returns SID or None."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
        logger.warning("[Twilio] Credentials not configured — skipping call.")
        return None
    try:
        from twilio.rest import Client
        call = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).calls.create(
            twiml=twiml, to=to_number, from_=TWILIO_FROM_NUMBER,
        )
        return call.sid
    except ImportError:
        logger.error("[Twilio] 'twilio' package not installed.")
        return None
    except Exception as e:
        logger.error(f"[Twilio] Call failed for {to_number}: {e}")
        return None


def make_departure_call(to_number: str, name: str, flight_number: str,
                         destination: str, gate_code: str, terminal: str) -> str | None:
    """Initiate departure reminder call. Returns Twilio call SID or None."""
    twiml = _build_twiml(name, flight_number, destination, gate_code, terminal)
    sid   = _call(to_number, twiml)
    if sid:
        logger.info(f"[Twilio] Departure call → {to_number} flight={flight_number} SID={sid}")
    return sid


def make_terminal_change_call(to_number: str, name: str, flight_number: str,
                               destination: str, new_terminal: str,
                               new_gate: str | None = None) -> str | None:
    """Initiate urgent terminal-change alert call. Returns Twilio call SID or None."""
    gate_part = f", Gate {new_gate}" if new_gate else ""
    message = (
        f"Hello {name}. This is an urgent announcement from Skylight International Airport. "
        f"Your flight {_spaced(flight_number)} to {destination} has had a terminal change. "
        f"Please proceed to Terminal {new_terminal}{gate_part} immediately. "
        f"We apologize for any inconvenience. Thank you."
    )
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say voice="Polly.Raveena" language="en-IN">{message}</Say>'
        '<Pause length="1"/>'
        f'<Say voice="Polly.Raveena" language="en-IN">{message}</Say>'
        "</Response>"
    )
    sid = _call(to_number, twiml)
    if sid:
        logger.info(f"[Twilio] Terminal-change call → {to_number} flight={flight_number} new={new_terminal} SID={sid}")
    return sid
