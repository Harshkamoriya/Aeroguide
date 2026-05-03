"""
db/session_manager.py — Session and turn persistence for AeroGuide.
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

logger  = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "db/airport.db")
IST     = timezone(timedelta(hours=5, minutes=30))


def _now() -> str:
    return datetime.now(IST).isoformat()


class SessionManager:
    """Manages conversation sessions in airport.db."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Session lifecycle ───────────────────────────────────────────────

    def create(self, session_id: str, language: str = "english") -> dict:
        now = _now()
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at, last_active, language)"
                " VALUES (?, ?, ?, ?)",
                (session_id, now, now, language)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[SessionDB] create failed: {e}")
        return {"session_id": session_id, "created_at": now}

    def touch(self, session_id: str, flight_number: str = None,
              terminal: str = None) -> None:
        try:
            conn   = self._conn()
            fields = ["last_active = ?"]
            params = [_now()]
            if flight_number is not None:
                fields.append("flight_number = ?")
                params.append(flight_number)
            if terminal is not None:
                fields.append("terminal = ?")
                params.append(terminal)
            params.append(session_id)
            conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE session_id = ?",
                params
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[SessionDB] touch failed: {e}")

    def get(self, session_id: str) -> Optional[dict]:
        try:
            conn = self._conn()
            row  = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            logger.warning(f"[SessionDB] get failed: {e}")
            return None

    def delete(self, session_id: str) -> None:
        try:
            conn = self._conn()
            conn.execute("DELETE FROM turns    WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[SessionDB] delete failed: {e}")

    # ── Turn persistence ────────────────────────────────────────────────

    def save_turn(self, session_id: str, turn_number: int,
                  speaker: str, text: str, intent: str = None) -> None:
        try:
            conn = self._conn()
            conn.execute(
                "INSERT INTO turns (session_id, turn_number, speaker, text, intent, timestamp)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, turn_number, speaker, text, intent, _now())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[SessionDB] save_turn failed: {e}")

    def get_turns(self, session_id: str) -> list[dict]:
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT turn_number, speaker, text, intent, timestamp"
                " FROM turns WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[SessionDB] get_turns failed: {e}")
            return []

    # ── Flight delay simulation (proactive alert hook) ──────────────────

    def get_delayed_flights(self) -> list[dict]:
        """
        Returns flights whose status_default != 'On-Time'.
        In production, this would poll a live feed; here it reads the DB.
        Agents can call this on a schedule to push proactive alerts.
        """
        try:
            conn = self._conn()
            rows = conn.execute(
                """
                SELECT fs.flight_number, fs.direction, fs.scheduled_time,
                       fs.status_default, a.name AS airline_name,
                       g.gate_code, t.code AS terminal_code,
                       r.dest_city
                FROM flight_schedule fs
                JOIN airlines  a ON fs.airline_id  = a.airline_id
                JOIN gates     g ON fs.gate_id     = g.gate_id
                JOIN routes    r ON fs.route_id    = r.route_id
                JOIN terminals t ON fs.terminal_id = t.terminal_id
                WHERE fs.status_default != 'On-Time'
                ORDER BY fs.scheduled_time
                """
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[SessionDB] delayed flights lookup failed: {e}")
            return []
