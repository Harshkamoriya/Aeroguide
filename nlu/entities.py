"""
nlu/entities.py — Entity resolver for AeroGuide.
Maps raw spoken entities (flight numbers, brand names, facilities) to DB records.
"""

import os
import re
import sqlite3
import logging
from typing import Optional

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

logger  = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "db/airport.db")

# ── Alias maps for common spoken variations ────────────────────────────────

FACILITY_ALIASES = {
    "toilet":        "restroom",
    "toilets":       "restroom",
    "loo":           "restroom",
    "washroom":      "restroom",
    "bathroom":      "restroom",
    "wc":            "restroom",
    "ladies room":   "restroom",
    "gents":         "restroom",
    "cash":          "ATM",
    "cash machine":  "ATM",
    "money":         "ATM",
    "bank":          "ATM",
    "doc":           "Medical",
    "doctor":        "Medical",
    "first aid":     "Medical",
    "ambulance":     "Medical",
    "hospital":      "Medical",
    "nurse":         "Medical",
    "masjid":        "Prayer",
    "namaz":         "Prayer",
    "chapel":        "Prayer",
    "mandir":        "Prayer",
    "prayer":        "Prayer",
    "smoke":         "Smoking",
    "cigarette":     "Smoking",
    "smoking":       "Smoking",
    "baby":          "Nursery",
    "infant":        "Nursery",
    "nursing":       "Nursery",
    "nappy":         "Nursery",
    "spa":           "Spa",
    "massage":       "Spa",
    "wifi":          "Wi-Fi",
    "wi-fi":         "Wi-Fi",
    "internet":      "Wi-Fi",
    "password":      "Wi-Fi",
    "forex":         "Currency Exchange",
    "currency":      "Currency Exchange",
    "exchange":      "Currency Exchange",
    "dollar":        "Currency Exchange",
    "pound":         "Currency Exchange",
}

BRAND_ALIASES = {
    "mcdonalds":   "McDonald's",
    "mcdonald":    "McDonald's",
    "mcds":        "McDonald's",
    "mac":         "McDonald's",
    "mcd":         "McDonald's",
    "bk":          "Burger King",
    "burgerking":  "Burger King",
    "kfc":         "KFC",
    "sbux":        "Starbucks",
    "starbs":      "Starbucks",
    "starbuck":    "Starbucks",
    "ccd":         "Café Coffee Day",
    "cafe coffee": "Café Coffee Day",
    "coffee day":  "Café Coffee Day",
    "costa":       "Costa Coffee",
    "dunkin":      "Dunkin'",
    "dunkins":     "Dunkin'",
    "dominos":     "Domino's",
    "dominoes":    "Domino's",
    "pizzahut":    "Pizza Hut",
    "haldirams":   "Haldiram's",
    "haldiram":    "Haldiram's",
    "saravana":    "Saravana Bhavan",
    "saravanaa":   "Saravana Bhavan",
    "south indian":"Saravana Bhavan",
    "wow momo":    "Wow! Momo",
    "wowmomo":     "Wow! Momo",
    "momos":       "Wow! Momo",
    "baskin":      "Baskin-Robbins",
    "br":          "Baskin-Robbins",
    "haagendazs":  "Häagen-Dazs",
    "haagen":      "Häagen-Dazs",
    "hd":          "Häagen-Dazs",
    "subway":      "Subway",
    "dfs":         "DFS Duty Free",
    "duty free":   "DFS Duty Free",
    "dutyfree":    "DFS Duty Free",
    "travelex":    "Travelex",
    "thomas cook": "Thomas Cook",
    "apple":       "Apple",
    "bose":        "Bose",
    "samsung":     "Samsung",
    "gucci":       "Gucci",
    "lv":          "Louis Vuitton",
    "vuitton":     "Louis Vuitton",
    "hermes":      "Hermès",
    "swarovski":   "Swarovski",
    "tanishq":     "Tanishq",
    "mac cosmetics":"MAC Cosmetics",
    "sephora":     "Sephora",
}

AIRLINE_ALIASES = {
    "6e":       "IndiGo",
    "indigo":   "IndiGo",
    "ai":       "Air India",
    "air india":"Air India",
    "uk":       "Vistara",
    "vistara":  "Vistara",
    "sg":       "SpiceJet",
    "spicejet": "SpiceJet",
    "qp":       "Akasa Air",
    "akasa":    "Akasa Air",
    "ek":       "Emirates",
    "emirates": "Emirates",
    "ey":       "Etihad Airways",
    "etihad":   "Etihad Airways",
    "qr":       "Qatar Airways",
    "qatar":    "Qatar Airways",
    "lh":       "Lufthansa",
    "lufthansa":"Lufthansa",
    "ba":       "British Airways",
    "british":  "British Airways",
    "sq":       "Singapore Airlines",
    "singapore":"Singapore Airlines",
    "af":       "Air France",
    "kl":       "KLM",
    "tk":       "Turkish Airlines",
    "turkish":  "Turkish Airlines",
}


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Public resolution functions ────────────────────────────────────────────

def resolve_flight(raw: str) -> Optional[dict]:
    """Return flight schedule row(s) for a given flight number."""
    if not raw:
        return None
    normalised = re.sub(r"\s+", "", raw).upper()
    try:
        conn = _db_conn()
        rows = conn.execute(
            """
            SELECT fs.flight_number, fs.direction, fs.scheduled_time,
                   fs.status_default, fs.days_of_week,
                   a.name  AS airline_name, a.iata_code,
                   g.gate_code,
                   r.dest_city, r.dest_iata, r.origin_city, r.origin_iata,
                   t.code  AS terminal_code, t.name AS terminal_name,
                   bb.belt_code
            FROM flight_schedule fs
            JOIN airlines  a  ON fs.airline_id  = a.airline_id
            JOIN gates     g  ON fs.gate_id     = g.gate_id
            JOIN routes    r  ON fs.route_id    = r.route_id
            JOIN terminals t  ON fs.terminal_id = t.terminal_id
            LEFT JOIN baggage_belts bb ON fs.belt_id = bb.belt_id
            WHERE UPPER(REPLACE(fs.flight_number,' ','')) = ?
            ORDER BY fs.direction
            """,
            (normalised,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows] if rows else None
    except Exception as e:
        logger.error(f"[Entities] Flight lookup failed: {e}")
        return None


def resolve_restaurant(brand_raw: str, terminal: str = None) -> list[dict]:
    """Return restaurant(s) matching a brand name, optionally filtered by terminal."""
    if not brand_raw:
        return []
    # Normalise alias
    key = brand_raw.lower().strip()
    canonical = BRAND_ALIASES.get(key) or BRAND_ALIASES.get(re.sub(r"['\s!]+", "", key))
    search = canonical or brand_raw

    try:
        conn   = _db_conn()
        params = [f"%{search}%"]
        q = """
            SELECT r.restaurant_id, r.code, r.cuisine, r.seats,
                   r.open_time, r.close_time, r.is_24_hours, r.avg_price_inr,
                   b.name  AS brand_name, b.category AS brand_category,
                   f.floor_code, f.floor_name,
                   t.code  AS terminal_code, t.name AS terminal_name,
                   z.zone_name
            FROM restaurants r
            JOIN brands    b ON r.brand_id   = b.brand_id
            JOIN floors    f ON r.floor_id   = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON r.zone_id   = z.zone_id
            WHERE LOWER(b.name) LIKE LOWER(?)
        """
        if terminal:
            t_code = _normalise_terminal(terminal)
            if t_code:
                q += " AND t.code = ?"
                params.append(t_code)
        q += " ORDER BY t.code, r.restaurant_id"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Restaurant lookup failed: {e}")
        return []


def resolve_menu(brand_raw: str, food_item: str = None) -> list[dict]:
    """Return menu items for a brand, optionally filtered by item name."""
    if not brand_raw and not food_item:
        return []
    key = (brand_raw or "").lower().strip()
    canonical = BRAND_ALIASES.get(key) or BRAND_ALIASES.get(re.sub(r"['\s!]+", "", key))
    search_brand = canonical or brand_raw or ""

    try:
        conn = _db_conn()
        params: list = []
        conditions: list[str] = []

        if search_brand:
            conditions.append("LOWER(b.name) LIKE LOWER(?)")
            params.append(f"%{search_brand}%")
        if food_item:
            conditions.append("LOWER(mi.item_name) LIKE LOWER(?)")
            params.append(f"%{food_item}%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT mi.item_id, mi.item_name, mi.category, mi.price_inr,
                   mi.is_vegetarian, mi.description,
                   b.name AS brand_name
            FROM menu_items mi
            JOIN brands b ON mi.brand_id = b.brand_id
            {where}
            ORDER BY mi.price_inr
            """,
            params
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Menu lookup failed: {e}")
        return []


def resolve_food_item(food_item: str) -> list[dict]:
    """Search menu items by food keyword (coffee, burger, idli, etc.)."""
    if not food_item:
        return []
    try:
        conn = _db_conn()
        rows = conn.execute(
            """
            SELECT mi.item_name, mi.category, mi.price_inr, mi.is_vegetarian,
                   b.name  AS brand_name,
                   r.cuisine,
                   f.floor_code, f.floor_name,
                   t.code  AS terminal_code
            FROM menu_items mi
            JOIN brands      b ON mi.brand_id   = b.brand_id
            JOIN restaurants r ON r.brand_id    = mi.brand_id
            JOIN floors      f ON r.floor_id    = f.floor_id
            JOIN terminals   t ON f.terminal_id = t.terminal_id
            WHERE LOWER(mi.item_name) LIKE LOWER(?)
               OR LOWER(mi.category)  LIKE LOWER(?)
            ORDER BY mi.price_inr
            LIMIT 20
            """,
            (f"%{food_item}%", f"%{food_item}%")
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Food item lookup failed: {e}")
        return []


def resolve_gate(gate_code: str) -> Optional[dict]:
    """Return gate details including terminal, floor, and zone."""
    if not gate_code:
        return None
    code = gate_code.upper().replace("GATE", "").strip()
    try:
        conn = _db_conn()
        row  = conn.execute(
            """
            SELECT g.gate_code, g.has_jet_bridge, g.max_aircraft_size, g.seating_capacity,
                   f.floor_code, f.floor_name,
                   t.code  AS terminal_code, t.name AS terminal_name,
                   z.zone_name
            FROM gates     g
            JOIN floors    f ON g.floor_id    = f.floor_id
            JOIN terminals t ON g.terminal_id = t.terminal_id
            LEFT JOIN zones z ON g.zone_id   = z.zone_id
            WHERE UPPER(g.gate_code) = ?
            """,
            (code,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[Entities] Gate lookup failed: {e}")
        return None


def resolve_facility(facility_type: str, terminal: str = None) -> list[dict]:
    """Return services, restrooms, or lounges matching a facility type."""
    if not facility_type:
        return []
    norm = FACILITY_ALIASES.get(facility_type.lower(), facility_type)
    results: list[dict] = []

    try:
        conn = _db_conn()

        # Check services table
        params = [f"%{norm}%"]
        q = """
            SELECT s.type, s.name, s.open_24,
                   f.floor_code, f.floor_name,
                   t.code AS terminal_code
            FROM services  s
            JOIN floors    f ON s.floor_id    = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            WHERE LOWER(s.type) LIKE LOWER(?)
        """
        if terminal:
            tc = _normalise_terminal(terminal)
            if tc:
                q += " AND t.code = ?"
                params.append(tc)
        q += " ORDER BY t.code, f.floor_level"
        svc_rows = conn.execute(q, params).fetchall()
        results.extend({"kind": "service", **dict(r)} for r in svc_rows)

        # Check restrooms if toilet-related
        if norm.lower() in ("restroom", "toilet", "wc"):
            rr_params = []
            rr_q = """
                SELECT r.type, r.has_baby_change, r.has_shower,
                       f.floor_code, f.floor_name,
                       t.code AS terminal_code
                FROM restrooms r
                JOIN floors    f ON r.floor_id    = f.floor_id
                JOIN terminals t ON f.terminal_id = t.terminal_id
            """
            if terminal:
                tc = _normalise_terminal(terminal)
                if tc:
                    rr_q += " WHERE t.code = ?"
                    rr_params.append(tc)
            rr_q += " ORDER BY t.code, f.floor_level LIMIT 6"
            rr_rows = conn.execute(rr_q, rr_params).fetchall()
            results.extend({"kind": "restroom", **dict(r)} for r in rr_rows)

        conn.close()
    except Exception as e:
        logger.error(f"[Entities] Facility lookup failed: {e}")

    return results


def resolve_lounge(terminal: str = None, tier: str = None) -> list[dict]:
    """Return lounges, optionally filtered by terminal or tier."""
    try:
        conn = _db_conn()
        params: list = []
        conditions: list[str] = []
        if terminal:
            tc = _normalise_terminal(terminal)
            if tc:
                conditions.append("t.code = ?")
                params.append(tc)
        if tier:
            conditions.append("LOWER(l.tier) LIKE LOWER(?)")
            params.append(f"%{tier}%")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT l.name, l.operator, l.tier, l.capacity,
                   l.has_shower, l.has_bar, l.has_meal, l.has_workspace,
                   l.open_time, l.close_time,
                   f.floor_code, f.floor_name,
                   t.code AS terminal_code
            FROM lounges   l
            JOIN floors    f ON l.floor_id    = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            {where}
            ORDER BY t.code, l.tier
            """,
            params
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Lounge lookup failed: {e}")
        return []


def resolve_checkin(terminal: str = None) -> list[dict]:
    """Return check-in counter info for a terminal."""
    try:
        conn = _db_conn()
        params: list = []
        conditions: list[str] = []
        if terminal:
            tc = _normalise_terminal(terminal)
            if tc:
                conditions.append("t.code = ?")
                params.append(tc)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT cc.counter_code, cc.type,
                   f.floor_code, f.floor_name,
                   t.code AS terminal_code, t.name AS terminal_name,
                   z.zone_name
            FROM checkin_counters cc
            JOIN floors    f ON cc.floor_id   = f.floor_id
            JOIN terminals t ON cc.terminal_id = t.terminal_id
            LEFT JOIN zones z ON cc.zone_id   = z.zone_id
            {where}
            ORDER BY t.code, cc.counter_id
            """,
            params
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Check-in lookup failed: {e}")
        return []


def resolve_baggage_belt(flight_number: str = None, terminal: str = None) -> list[dict]:
    """Return baggage belt info, optionally linked to a flight."""
    try:
        conn = _db_conn()
        results = []

        if flight_number:
            fn = re.sub(r"\s+", "", flight_number).upper()
            rows = conn.execute(
                """
                SELECT bb.belt_code, bb.type AS belt_type,
                       fs.flight_number, fs.scheduled_time,
                       f.floor_code, t.code AS terminal_code
                FROM flight_schedule fs
                JOIN baggage_belts bb ON fs.belt_id   = bb.belt_id
                JOIN floors        f  ON bb.floor_id  = f.floor_id
                JOIN terminals     t  ON bb.terminal_id = t.terminal_id
                WHERE UPPER(REPLACE(fs.flight_number,' ','')) = ?
                  AND fs.direction = 'ARRIVAL'
                """,
                (fn,)
            ).fetchall()
            results.extend(dict(r) for r in rows)

        if not results:
            params = []
            q = """
                SELECT bb.belt_code, bb.type AS belt_type,
                       f.floor_code, t.code AS terminal_code
                FROM baggage_belts bb
                JOIN floors    f ON bb.floor_id   = f.floor_id
                JOIN terminals t ON bb.terminal_id = t.terminal_id
            """
            if terminal:
                tc = _normalise_terminal(terminal)
                if tc:
                    q += " WHERE t.code = ?"
                    params.append(tc)
            rows = conn.execute(q, params).fetchall()
            results.extend(dict(r) for r in rows)

        conn.close()
        return results
    except Exception as e:
        logger.error(f"[Entities] Baggage belt lookup failed: {e}")
        return []


def resolve_parking(terminal: str = None) -> list[dict]:
    """Return parking lot rates."""
    try:
        conn = _db_conn()
        params: list = []
        q = "SELECT p.code, p.type, p.capacity, p.rate_inr_hr FROM parking_lots p"
        if terminal:
            tc = _normalise_terminal(terminal)
            if tc:
                q += " JOIN terminals t ON p.terminal_id = t.terminal_id WHERE t.code = ?"
                params.append(tc)
        q += " ORDER BY p.rate_inr_hr"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Parking lookup failed: {e}")
        return []


def resolve_flights_by_destination(destination: str, direction: str = None) -> list[dict]:
    """Return flights to/from a destination city or IATA code."""
    if not destination:
        return []
    try:
        conn = _db_conn()
        params = [f"%{destination}%", destination.upper()]
        q = """
            SELECT fs.flight_number, fs.direction, fs.scheduled_time,
                   fs.status_default, a.name AS airline_name,
                   g.gate_code, r.dest_city, r.dest_iata,
                   t.code AS terminal_code
            FROM flight_schedule fs
            JOIN airlines  a ON fs.airline_id = a.airline_id
            JOIN gates     g ON fs.gate_id    = g.gate_id
            JOIN routes    r ON fs.route_id   = r.route_id
            JOIN terminals t ON fs.terminal_id= t.terminal_id
            WHERE (LOWER(r.dest_city) LIKE LOWER(?) OR UPPER(r.dest_iata) = ?)
        """
        if direction:
            q += " AND fs.direction = ?"
            params.append(direction.upper())
        q += " ORDER BY fs.scheduled_time LIMIT 10"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Entities] Flight-by-dest lookup failed: {e}")
        return []


# ── Helpers ────────────────────────────────────────────────────────────────

def _normalise_terminal(raw: str) -> Optional[str]:
    if not raw:
        return None
    r = raw.lower().strip()
    if "1" in r or "domestic" in r or "t1" in r:
        return "T1"
    if "2" in r or "international" in r or "t2" in r:
        return "T2"
    return None
