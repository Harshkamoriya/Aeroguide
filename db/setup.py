"""
db/setup.py — Initialise airport.db from blueprint.sql.
Run once:  python -m db.setup
"""

import os
import sqlite3
import logging

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BLUEPRINT = os.path.join(os.path.dirname(__file__), "blueprint.sql")
DB_PATH   = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "airport.db"))


def init_db(blueprint_path: str = BLUEPRINT, db_path: str = DB_PATH) -> None:
    """Create (or recreate) airport.db from blueprint.sql, then add sessions tables."""
    if not os.path.exists(blueprint_path):
        raise FileNotFoundError(f"blueprint.sql not found at {blueprint_path}")

    logger.info(f"Reading blueprint: {blueprint_path}")
    with open(blueprint_path, "r", encoding="utf-8") as fh:
        sql = fh.read()

    logger.info(f"Creating database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.executescript(sql)
    conn.commit()
    logger.info("Blueprint loaded.")

    # ── Session tables (not in blueprint.sql) ─────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL,
            last_active  TEXT NOT NULL,
            flight_number TEXT,
            terminal      TEXT,
            language      TEXT DEFAULT 'english'
        );

        CREATE TABLE IF NOT EXISTS turns (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            turn_number  INTEGER NOT NULL,
            speaker      TEXT NOT NULL,   -- 'traveler' | 'agent'
            text         TEXT,
            intent       TEXT,
            timestamp    TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
        CREATE INDEX IF NOT EXISTS idx_fs_flight ON flight_schedule(flight_number);
        CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(name);
        CREATE INDEX IF NOT EXISTS idx_menu_brand ON menu_items(brand_id);
    """)
    conn.commit()

    _add_users_table(conn)

    from db.build_nav_graph import build as _build_nav
    _build_nav(conn)

    conn.close()
    logger.info("Session tables created. Database ready.")


def _add_users_table(conn: sqlite3.Connection) -> None:
    """Add users table for Twilio flight-departure calling."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            mobile_number  TEXT    NOT NULL,
            flight_number  TEXT    NOT NULL,
            session_id     TEXT,
            call_sent      INTEGER DEFAULT 0,
            registered_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_users_flight ON users(flight_number);
    """)
    conn.commit()
    logger.info("Users table ready.")


def migrate(db_path: str = DB_PATH) -> None:
    """Add users table to an existing airport.db without recreating it."""
    conn = sqlite3.connect(db_path)
    _add_users_table(conn)
    conn.close()
    logger.info(f"[Migrate] users table ensured in {db_path}")


if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        migrate()
        print(f"[OK] Migration done: {DB_PATH}")
    else:
        init_db()
        print(f"[OK] airport.db initialised at {DB_PATH}")