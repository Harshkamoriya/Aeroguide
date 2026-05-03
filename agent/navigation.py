"""
agent/navigation.py — GPS-based indoor navigation for Skylight International Airport.

Given user's live lat/lng, finds their current zone and generates
step-by-step directions to any restaurant, shop, gate, or facility.
"""

import math
from typing import Optional


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance in meters between two GPS coordinates."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def euclidean_m(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def _compass(x1: float, y1: float, x2: float, y2: float) -> str:
    """Cardinal direction label from (x1,y1) toward (x2,y2). x=East, y=North."""
    dx, dy = x2 - x1, y2 - y1
    angle = math.degrees(math.atan2(dy, dx))
    if -22.5 <= angle < 22.5:            return "east"
    if 22.5 <= angle < 67.5:             return "north-east"
    if 67.5 <= angle < 112.5:            return "north"
    if 112.5 <= angle < 157.5:           return "north-west"
    if angle >= 157.5 or angle < -157.5: return "west"
    if -157.5 <= angle < -112.5:         return "south-west"
    if -112.5 <= angle < -67.5:          return "south"
    return "south-east"


# ── Locating the user ──────────────────────────────────────────────────────────

def find_user_location(conn, user_lat: float, user_lng: float) -> dict:
    """
    Find the nearest airport zone to the user's GPS coordinates.
    Returns zone/floor/terminal info plus internal x_m, y_m position.
    """
    rows = conn.execute("""
        SELECT z.zone_id, z.zone_name,
               z.center_latitude, z.center_longitude,
               z.center_x_m, z.center_y_m,
               f.floor_id, f.floor_code, f.floor_name, f.floor_level,
               t.terminal_id, t.code AS terminal_code, t.name AS terminal_name
        FROM zones z
        JOIN floors f ON z.floor_id = f.floor_id
        JOIN terminals t ON f.terminal_id = t.terminal_id
        WHERE z.center_latitude IS NOT NULL AND z.center_longitude IS NOT NULL
    """).fetchall()

    if not rows:
        return {}

    nearest = min(
        rows,
        key=lambda r: haversine_m(user_lat, user_lng,
                                  r["center_latitude"], r["center_longitude"])
    )
    dist = haversine_m(user_lat, user_lng,
                       nearest["center_latitude"], nearest["center_longitude"])

    return {
        "zone_id":       nearest["zone_id"],
        "zone_name":     nearest["zone_name"],
        "floor_id":      nearest["floor_id"],
        "floor_code":    nearest["floor_code"],
        "floor_name":    nearest["floor_name"],
        "floor_level":   nearest["floor_level"],
        "terminal_id":   nearest["terminal_id"],
        "terminal_code": nearest["terminal_code"],
        "terminal_name": nearest["terminal_name"],
        "x_m":           nearest["center_x_m"],
        "y_m":           nearest["center_y_m"],
        "gps_dist_m":    round(dist),
    }


# ── Finding the destination ────────────────────────────────────────────────────

def find_destination(conn, brand_name: str = None, gate_code: str = None,
                     facility_type: str = None, terminal: str = None) -> Optional[dict]:
    """
    Find the physical location of a destination (restaurant, shop, gate, or service).
    Returns coordinates and floor/terminal info, or None if not found.
    """
    if brand_name:
        row = conn.execute("""
            SELECT b.name AS name, r.x_m, r.y_m, r.latitude, r.longitude,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, z.zone_name
            FROM restaurants r
            JOIN brands b ON r.brand_id = b.brand_id
            JOIN floors f  ON r.floor_id = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON r.zone_id = z.zone_id
            WHERE LOWER(b.name) LIKE LOWER(?)
        """ + (" AND t.code = ?" if terminal else "") + " LIMIT 1",
            [f"%{brand_name}%"] + ([terminal] if terminal else [])
        ).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

        # Fall back to shops
        row = conn.execute("""
            SELECT b.name AS name, s.x_m, s.y_m, s.latitude, s.longitude,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, z.zone_name
            FROM shops s
            JOIN brands b ON s.brand_id = b.brand_id
            JOIN floors f  ON s.floor_id = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON s.zone_id = z.zone_id
            WHERE LOWER(b.name) LIKE LOWER(?)
        """ + (" AND t.code = ?" if terminal else "") + " LIMIT 1",
            [f"%{brand_name}%"] + ([terminal] if terminal else [])
        ).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

    if gate_code:
        row = conn.execute("""
            SELECT g.gate_code AS name, g.x_m, g.y_m, g.latitude, g.longitude,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, z.zone_name
            FROM gates g
            JOIN floors f  ON g.floor_id = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON g.zone_id = z.zone_id
            WHERE UPPER(g.gate_code) = UPPER(?) LIMIT 1
        """, [gate_code]).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

    if facility_type:
        # Try restrooms, charging stations, services table etc.
        row = conn.execute("""
            SELECT r.code AS name, r.x_m, r.y_m, r.latitude, r.longitude,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, NULL AS zone_name
            FROM restrooms r
            JOIN floors f  ON r.floor_id = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            WHERE 1=1
        """ + (" AND t.code = ?" if terminal else "") + " LIMIT 1",
            ([terminal] if terminal else [])
        ).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

    return None


# ── Floor connectors (escalators / lifts) ─────────────────────────────────────

def _nearest_floor_connector(conn, terminal_id: int, from_level: int,
                              to_level: int, user_x: float, user_y: float) -> Optional[dict]:
    """Find the closest escalator or lift that connects two floor levels."""
    escalators = conn.execute("""
        SELECT e.code, e.x_m, e.y_m, 'escalator' AS type
        FROM escalators e
        JOIN floors f1 ON e.from_floor_id = f1.floor_id
        JOIN floors f2 ON e.to_floor_id   = f2.floor_id
        WHERE e.terminal_id = ?
          AND ((f1.floor_level = ? AND f2.floor_level = ?)
            OR (f1.floor_level = ? AND f2.floor_level = ?))
          AND e.x_m IS NOT NULL
    """, [terminal_id, from_level, to_level, to_level, from_level]).fetchall()

    lifts = conn.execute("""
        SELECT l.lift_code AS code, l.x_m, l.y_m, 'lift' AS type
        FROM lifts l
        WHERE l.terminal_id = ? AND l.x_m IS NOT NULL
    """, [terminal_id]).fetchall()

    candidates = list(escalators) + list(lifts)
    if not candidates:
        return None

    nearest = min(candidates,
                  key=lambda r: euclidean_m(user_x, user_y, r["x_m"], r["y_m"]))
    return {
        "code":   nearest["code"],
        "type":   nearest["type"],
        "x_m":    nearest["x_m"],
        "y_m":    nearest["y_m"],
        "dist_m": round(euclidean_m(user_x, user_y, nearest["x_m"], nearest["y_m"])),
    }


# ── Direction builder ──────────────────────────────────────────────────────────

def build_directions(conn, user_location: dict, destination: dict) -> dict:
    """
    Generate step-by-step walking directions from user's current position to destination.

    Returns:
        steps       — list of human-readable instruction strings
        distance_m  — total walking distance estimate in meters
        destination_name / terminal / floor / zone
    """
    if not user_location or not destination:
        return {"steps": [], "distance_m": 0, "error": "Location data unavailable."}

    ul   = user_location
    dest = destination
    dest_name = dest.get("name", "your destination")
    steps: list[str] = []

    same_terminal = ul["terminal_id"] == dest["terminal_id"]
    same_floor    = ul["floor_id"]    == dest["floor_id"]

    steps.append(
        f"You are currently in {ul['zone_name']}, "
        f"{ul['terminal_code']} {ul['floor_code']}."
    )

    if same_terminal and same_floor:
        dist = euclidean_m(ul["x_m"], ul["y_m"], dest["x_m"], dest["y_m"])
        direction = _compass(ul["x_m"], ul["y_m"], dest["x_m"], dest["y_m"])
        steps.append(f"Walk {direction} for approximately {round(dist)} meters.")
        zone_note = f" in the {dest['zone_name']} area" if dest.get("zone_name") else ""
        steps.append(f"You will reach {dest_name}{zone_note}.")
        total_dist = round(dist)

    elif same_terminal and not same_floor:
        connector = _nearest_floor_connector(
            conn, ul["terminal_id"],
            ul["floor_level"], dest["floor_level"],
            ul["x_m"], ul["y_m"]
        )
        if connector:
            d1 = connector["dist_m"]
            dir1 = _compass(ul["x_m"], ul["y_m"], connector["x_m"], connector["y_m"])
            steps.append(
                f"Walk {dir1} approximately {d1} meters to the "
                f"{connector['type']} ({connector['code']})."
            )
            steps.append(f"Take the {connector['type']} to {dest['floor_code']}.")
            d2    = round(euclidean_m(connector["x_m"], connector["y_m"], dest["x_m"], dest["y_m"]))
            dir2  = _compass(connector["x_m"], connector["y_m"], dest["x_m"], dest["y_m"])
            steps.append(f"Continue {dir2} for approximately {d2} meters.")
            total_dist = d1 + d2
        else:
            steps.append(f"Take an escalator or lift to {dest['floor_code']}.")
            total_dist = 150

        zone_note = f" in the {dest['zone_name']} area" if dest.get("zone_name") else ""
        steps.append(f"You will reach {dest_name}{zone_note}.")

    else:
        steps.append(
            f"{dest_name} is located in {dest['terminal_code']} "
            f"{dest.get('floor_code', '')}."
        )
        steps.append(
            f"Follow the inter-terminal connector signs from {ul['terminal_code']}."
        )
        steps.append(
            f"Once inside {dest['terminal_code']}, follow signs to "
            f"{dest.get('floor_code', 'the correct floor')}."
        )
        zone_note = f" in the {dest['zone_name']} area" if dest.get("zone_name") else ""
        steps.append(f"You will find {dest_name}{zone_note}.")
        total_dist = 600

    return {
        "steps":                steps,
        "distance_m":           total_dist,
        "destination_name":     dest_name,
        "destination_terminal": dest.get("terminal_code"),
        "destination_floor":    dest.get("floor_code"),
        "destination_zone":     dest.get("zone_name"),
    }
