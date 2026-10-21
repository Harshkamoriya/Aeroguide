"""
agent/navigation.py — Graph-based indoor navigation for Skylight International Airport.

Uses nav_nodes + nav_edges tables (built by db/build_nav_graph.py) to find the
shortest walking path via Dijkstra's algorithm, then converts the node sequence
into human-readable step-by-step directions.
"""

import math
import heapq
from typing import Optional


# ── Geometry helpers ──────────────────────────────────────────────────────────

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlng  = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def euclidean_m(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def _compass(x1: float, y1: float, x2: float, y2: float) -> str:
    dx, dy = x2 - x1, y2 - y1
    angle  = math.degrees(math.atan2(dy, dx))
    if   -22.5  <= angle <  22.5:  return "east"
    if    22.5  <= angle <  67.5:  return "north-east"
    if    67.5  <= angle < 112.5:  return "north"
    if   112.5  <= angle < 157.5:  return "north-west"
    if angle >= 157.5 or angle < -157.5: return "west"
    if  -157.5  <= angle < -112.5: return "south-west"
    if  -112.5  <= angle <  -67.5: return "south"
    return "south-east"


# ── Graph loading ─────────────────────────────────────────────────────────────

def _load_graph(conn) -> dict[int, list[tuple[int, float, str]]]:
    """Return adjacency list  node_id -> [(neighbour_id, distance_m, edge_type)]."""
    rows = conn.execute(
        "SELECT from_node_id, to_node_id, distance_m, edge_type FROM nav_edges"
    ).fetchall()
    graph: dict[int, list] = {}
    for r in rows:
        graph.setdefault(r["from_node_id"], []).append(
            (r["to_node_id"], r["distance_m"], r["edge_type"])
        )
    return graph


# ── Dijkstra ──────────────────────────────────────────────────────────────────

def _dijkstra(graph: dict, source: int, target: int) -> tuple[list[int], float]:
    """
    Return (path_node_ids, total_distance_m).
    path is empty and distance is inf when no path exists.
    """
    dist: dict[int, float] = {source: 0.0}
    prev: dict[int, int]   = {}
    heap = [(0.0, source)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        if u == target:
            path = []
            while u in prev:
                path.append(u)
                u = prev[u]
            path.append(source)
            return list(reversed(path)), d
        for v, w, _ in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    return [], math.inf


# ── Node lookup helpers ───────────────────────────────────────────────────────

def _nearest_node(conn, x_m: float, y_m: float,
                  floor_id: int = None,
                  terminal_id: int = None) -> Optional[dict]:
    """Find the nav_node closest to (x_m, y_m), optionally filtered by floor/terminal."""
    q      = "SELECT * FROM nav_nodes WHERE 1=1"
    params = []
    if floor_id is not None:
        q += " AND floor_id = ?"
        params.append(floor_id)
    if terminal_id is not None:
        q += " AND terminal_id = ?"
        params.append(terminal_id)
    rows = conn.execute(q, params).fetchall()
    if not rows:
        return None
    return min(rows, key=lambda r: euclidean_m(x_m, y_m, r["x_m"], r["y_m"]))


# ── User location ─────────────────────────────────────────────────────────────

def find_user_location(conn, user_lat: float, user_lng: float) -> dict:
    """Map live GPS coordinates to the nearest airport zone."""
    rows = conn.execute("""
        SELECT z.zone_id, z.zone_name,
               z.center_latitude, z.center_longitude,
               z.center_x_m, z.center_y_m,
               f.floor_id, f.floor_code, f.floor_name, f.floor_level,
               t.terminal_id, t.code AS terminal_code, t.name AS terminal_name
        FROM zones z
        JOIN floors    f ON z.floor_id    = f.floor_id
        JOIN terminals t ON f.terminal_id = t.terminal_id
        WHERE z.center_latitude IS NOT NULL AND z.center_longitude IS NOT NULL
    """).fetchall()
    if not rows:
        return {}
    nearest = min(rows, key=lambda r: haversine_m(
        user_lat, user_lng, r["center_latitude"], r["center_longitude"]
    ))
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


def find_location_from_context(conn, terminal_code: str,
                                floor_code: str = "L2") -> Optional[dict]:
    """Approximate location when GPS is unavailable but terminal/floor is known."""
    row = conn.execute("""
        SELECT z.zone_id, z.zone_name,
               z.center_x_m AS x_m, z.center_y_m AS y_m,
               f.floor_id, f.floor_code, f.floor_name, f.floor_level,
               t.terminal_id, t.code AS terminal_code, t.name AS terminal_name
        FROM zones z
        JOIN floors    f ON z.floor_id    = f.floor_id
        JOIN terminals t ON f.terminal_id = t.terminal_id
        WHERE UPPER(t.code) = UPPER(?) AND UPPER(f.floor_code) = UPPER(?)
        LIMIT 1
    """, [terminal_code, floor_code]).fetchone()
    if row:
        return dict(row)
    row = conn.execute("""
        SELECT z.zone_id, z.zone_name,
               z.center_x_m AS x_m, z.center_y_m AS y_m,
               f.floor_id, f.floor_code, f.floor_name, f.floor_level,
               t.terminal_id, t.code AS terminal_code, t.name AS terminal_name
        FROM zones z
        JOIN floors    f ON z.floor_id    = f.floor_id
        JOIN terminals t ON f.terminal_id = t.terminal_id
        WHERE UPPER(t.code) = UPPER(?)
        LIMIT 1
    """, [terminal_code]).fetchone()
    return dict(row) if row else None


# ── Destination lookup ────────────────────────────────────────────────────────

def find_destination(conn, brand_name: str = None, gate_code: str = None,
                     facility_type: str = None, terminal: str = None) -> Optional[dict]:
    """Return physical coordinates of a restaurant, shop, gate, or facility."""
    if brand_name:
        row = conn.execute("""
            SELECT b.name AS name, r.x_m, r.y_m,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, z.zone_name
            FROM restaurants r
            JOIN brands    b ON r.brand_id  = b.brand_id
            JOIN floors    f ON r.floor_id  = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON r.zone_id  = z.zone_id
            WHERE LOWER(b.name) LIKE LOWER(?)
        """ + (" AND t.code = ?" if terminal else "") + " LIMIT 1",
            [f"%{brand_name}%"] + ([terminal] if terminal else [])
        ).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)
        row = conn.execute("""
            SELECT b.name AS name, s.x_m, s.y_m,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, z.zone_name
            FROM shops s
            JOIN brands    b ON s.brand_id  = b.brand_id
            JOIN floors    f ON s.floor_id  = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON s.zone_id  = z.zone_id
            WHERE LOWER(b.name) LIKE LOWER(?)
        """ + (" AND t.code = ?" if terminal else "") + " LIMIT 1",
            [f"%{brand_name}%"] + ([terminal] if terminal else [])
        ).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

    if gate_code:
        row = conn.execute("""
            SELECT g.gate_code AS name, g.x_m, g.y_m,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, z.zone_name
            FROM gates g
            JOIN floors    f ON g.floor_id  = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            LEFT JOIN zones z ON g.zone_id  = z.zone_id
            WHERE UPPER(g.gate_code) = UPPER(?) LIMIT 1
        """, [gate_code]).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

    if facility_type:
        row = conn.execute("""
            SELECT r.code AS name, r.x_m, r.y_m,
                   f.floor_id, f.floor_code, f.floor_level,
                   t.terminal_id, t.code AS terminal_code, NULL AS zone_name
            FROM restrooms r
            JOIN floors    f ON r.floor_id  = f.floor_id
            JOIN terminals t ON f.terminal_id = t.terminal_id
            WHERE 1=1
        """ + (" AND t.code = ?" if terminal else "") + " LIMIT 1",
            ([terminal] if terminal else [])
        ).fetchone()
        if row and row["x_m"] is not None:
            return dict(row)

    return None


# ── Direction builder ─────────────────────────────────────────────────────────

def build_directions(conn, user_location: dict, destination: dict) -> dict:
    """
    Shortest-path directions from user_location to destination using Dijkstra.

    Falls back to straight-line guidance if the nav graph is unavailable.
    """
    if not user_location or not destination:
        return {"steps": [], "distance_m": 0, "error": "Location data unavailable."}

    ul        = user_location
    dest      = destination
    dest_name = dest.get("name", "your destination")

    # ── Try graph-based pathfinding first ─────────────────────────────────────
    try:
        graph = _load_graph(conn)
        if graph:
            src_node = _nearest_node(conn, ul["x_m"],   ul["y_m"],
                                     ul["floor_id"],   ul["terminal_id"])
            dst_node = _nearest_node(conn, dest["x_m"], dest["y_m"],
                                     dest["floor_id"], dest["terminal_id"])

            if src_node and dst_node:
                path_ids, total_dist = _dijkstra(
                    graph, src_node["node_id"], dst_node["node_id"]
                )
                if path_ids:
                    node_rows = {
                        r["node_id"]: r
                        for r in conn.execute("SELECT * FROM nav_nodes").fetchall()
                    }
                    edge_types = _path_edge_types(conn, path_ids)
                    steps = _path_to_steps(path_ids, node_rows, edge_types,
                                           ul, dest, dest_name,
                                           dst_node=dst_node)
                    final_approach = euclidean_m(
                        dst_node["x_m"], dst_node["y_m"],
                        dest["x_m"],     dest["y_m"],
                    )
                    return {
                        "steps":                steps,
                        "distance_m":           round(total_dist + final_approach),
                        "destination_name":     dest_name,
                        "destination_terminal": dest.get("terminal_code"),
                        "destination_floor":    dest.get("floor_code"),
                        "destination_zone":     dest.get("zone_name"),
                    }
    except Exception:
        pass   # nav graph missing — fall through to geometry fallback

    # ── Geometry fallback (no graph) ──────────────────────────────────────────
    return _geometry_directions(conn, ul, dest, dest_name)


def _path_edge_types(conn, path_ids: list[int]) -> list[str]:
    """Return the edge_type for each consecutive pair in path_ids."""
    types = []
    for i in range(len(path_ids) - 1):
        row = conn.execute(
            "SELECT edge_type FROM nav_edges "
            "WHERE from_node_id=? AND to_node_id=? LIMIT 1",
            [path_ids[i], path_ids[i + 1]]
        ).fetchone()
        types.append(row["edge_type"] if row else "walk")
    return types


def _path_to_steps(path_ids: list[int], nodes: dict, edge_types: list[str],
                   ul: dict, dest: dict, dest_name: str,
                   dst_node: dict = None) -> list[str]:
    """Convert a node-id path into human-readable step strings."""
    steps = [f"You are at {ul['zone_name']}, {ul['terminal_code']} {ul['floor_code']}."]

    prev = None
    for i, nid in enumerate(path_ids):
        node = nodes.get(nid)
        if node is None:
            continue
        if prev is None:
            prev = node
            continue

        etype = edge_types[i - 1] if i - 1 < len(edge_types) else "walk"
        d     = round(euclidean_m(prev["x_m"], prev["y_m"], node["x_m"], node["y_m"]))

        if etype == "corridor":
            steps.append(
                f"Walk through the inter-terminal corridor for about {d}m "
                f"until you enter {node['terminal_code']}."
            )
        elif etype == "escalator":
            going_up = node["floor_level"] > prev["floor_level"]
            steps.append(
                f"Take the escalator {'up' if going_up else 'down'} "
                f"to {node['floor_code']}."
            )
        elif etype == "lift":
            going_up = node["floor_level"] > prev["floor_level"]
            steps.append(
                f"Take the lift {'up' if going_up else 'down'} "
                f"to {node['floor_code']}."
            )
        else:  # walk
            direction = _compass(prev["x_m"], prev["y_m"], node["x_m"], node["y_m"])
            label = node["label"] if node["node_type"] != "zone" else node["label"]
            steps.append(f"Head {direction} for about {d}m towards {label}.")

        prev = node

    # Precise final-approach step: nav node → exact restaurant/facility coords
    zone_note = f" ({dest['zone_name']})" if dest.get("zone_name") else ""
    if dst_node and dest.get("x_m") is not None and dest.get("y_m") is not None:
        d_final = euclidean_m(dst_node["x_m"], dst_node["y_m"], dest["x_m"], dest["y_m"])
        if d_final >= 2:
            dir_final = _compass(dst_node["x_m"], dst_node["y_m"], dest["x_m"], dest["y_m"])
            steps.append(
                f"Head {dir_final} for about {round(d_final)}m — "
                f"{dest_name}{zone_note} is on your {dest.get('floor_code', '')}."
            )
            return steps

    steps.append(f"You will reach {dest_name}{zone_note} on {dest.get('floor_code', '')}.")
    return steps


# ── Geometry fallback (used when nav graph tables are absent) ─────────────────

def _geometry_directions(conn, ul: dict, dest: dict, dest_name: str) -> dict:
    steps: list[str] = []
    total = 0

    steps.append(f"You are at {ul['zone_name']}, {ul['terminal_code']} {ul['floor_code']}.")

    same_terminal = ul["terminal_id"] == dest["terminal_id"]
    same_floor    = ul["floor_id"]    == dest["floor_id"]

    if same_terminal and same_floor:
        d   = round(euclidean_m(ul["x_m"], ul["y_m"], dest["x_m"], dest["y_m"]))
        dir = _compass(ul["x_m"], ul["y_m"], dest["x_m"], dest["y_m"])
        steps.append(f"Head {dir} for about {d}m.")
        zone_note = f" ({dest['zone_name']})" if dest.get("zone_name") else ""
        steps.append(f"You will reach {dest_name}{zone_note}.")
        total = d

    elif same_terminal:
        # Find nearest escalator between the two floors
        esc = conn.execute("""
            SELECT e.x_m, e.y_m, e.code,
                   f2.floor_code AS to_fc, f2.floor_level AS to_lvl
            FROM escalators e
            JOIN floors f1 ON e.from_floor_id = f1.floor_id
            JOIN floors f2 ON e.to_floor_id   = f2.floor_id
            WHERE e.terminal_id = ? AND e.x_m IS NOT NULL
              AND ((f1.floor_level = ? AND f2.floor_level = ?)
                OR (f1.floor_level = ? AND f2.floor_level = ?))
            LIMIT 1
        """, [ul["terminal_id"],
              ul["floor_level"], dest["floor_level"],
              dest["floor_level"], ul["floor_level"]]).fetchone()
        if esc:
            d1  = round(euclidean_m(ul["x_m"], ul["y_m"], esc["x_m"], esc["y_m"]))
            dir1 = _compass(ul["x_m"], ul["y_m"], esc["x_m"], esc["y_m"])
            up   = dest["floor_level"] > ul["floor_level"]
            steps.append(f"Head {dir1} for about {d1}m to escalator {esc['code']}.")
            steps.append(f"Take escalator {'up' if up else 'down'} to {esc['to_fc']}.")
            d2   = round(euclidean_m(esc["x_m"], esc["y_m"], dest["x_m"], dest["y_m"]))
            dir2 = _compass(esc["x_m"], esc["y_m"], dest["x_m"], dest["y_m"])
            steps.append(f"Head {dir2} for about {d2}m.")
            total = d1 + d2
        else:
            steps.append(f"Take an escalator or lift to {dest['floor_code']}.")
            total = 150
        zone_note = f" ({dest['zone_name']})" if dest.get("zone_name") else ""
        steps.append(f"You will reach {dest_name}{zone_note}.")

    else:
        t1r = conn.execute(
            "SELECT sw_x_m,sw_y_m,ne_x_m,ne_y_m FROM terminals WHERE terminal_id=?",
            [ul["terminal_id"]]
        ).fetchone()
        t2r = conn.execute(
            "SELECT sw_x_m,sw_y_m,ne_x_m,ne_y_m FROM terminals WHERE terminal_id=?",
            [dest["terminal_id"]]
        ).fetchone()
        if t1r and t2r:
            cx = (max(t1r["sw_x_m"], t2r["sw_x_m"]) +
                  min(t1r["ne_x_m"], t2r["ne_x_m"])) / 2
            if ul["y_m"] < dest["y_m"]:
                exit_y, entry_y, cdir = t1r["ne_y_m"], t2r["sw_y_m"], "north"
            else:
                exit_y, entry_y, cdir = t1r["sw_y_m"], t2r["ne_y_m"], "south"
            d1  = round(euclidean_m(ul["x_m"], ul["y_m"], cx, exit_y))
            d2  = round(abs(entry_y - exit_y))
            d3  = round(euclidean_m(cx, entry_y, dest["x_m"], dest["y_m"]))
            dir1 = _compass(ul["x_m"], ul["y_m"], cx, exit_y)
            dir3 = _compass(cx, entry_y, dest["x_m"], dest["y_m"])
            steps.append(f"Head {dir1} for about {d1}m to the inter-terminal walkway.")
            steps.append(f"Walk {cdir} through the corridor for about {d2}m to {dest['terminal_code']}.")
            steps.append(f"Head {dir3} for about {d3}m inside {dest['terminal_code']}.")
            zone_note = f" ({dest['zone_name']})" if dest.get("zone_name") else ""
            steps.append(f"You will reach {dest_name}{zone_note} on {dest.get('floor_code', '')}.")
            total = d1 + d2 + d3
        else:
            steps.append("Head to the inter-terminal connector and follow signs.")
            total = 600

    return {
        "steps":                steps,
        "distance_m":           total,
        "destination_name":     dest_name,
        "destination_terminal": dest.get("terminal_code"),
        "destination_floor":    dest.get("floor_code"),
        "destination_zone":     dest.get("zone_name"),
    }
