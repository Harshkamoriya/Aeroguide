"""
db/build_nav_graph.py — Build nav_nodes + nav_edges from existing airport data.

Derives a walkable graph from:
  zones       → main waypoint nodes (one per zone centre)
  escalators  → floor-change edges via nearest zone on each floor
  lifts       → floor-change edges (bidirectional, all adjacent served floors)
  terminals   → bounding-box used to compute inter-terminal corridor length

Run:  python -m db.build_nav_graph

Node types : zone | connector
Edge types : walk | escalator | lift | corridor
"""

import os
import math
import sqlite3
import logging

load_dotenv = __import__('dotenv').load_dotenv
load_dotenv(override=False)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "airport.db"))


def _euclid(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def build(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    (Re)build nav_nodes and nav_edges from the live airport DB.
    Returns (node_count, edge_count).
    """
    cur = conn.cursor()

    # ── Schema ────────────────────────────────────────────────────────────────
    cur.executescript("""
        DROP TABLE IF EXISTS nav_edges;
        DROP TABLE IF EXISTS nav_nodes;

        CREATE TABLE nav_nodes (
            node_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type     TEXT    NOT NULL,   -- zone | connector
            ref_id        INTEGER,            -- zone_id for zone nodes, NULL otherwise
            label         TEXT    NOT NULL,
            floor_id      INTEGER,
            floor_code    TEXT,
            floor_level   INTEGER,
            terminal_id   INTEGER,
            terminal_code TEXT,
            x_m           REAL    NOT NULL,
            y_m           REAL    NOT NULL
        );

        CREATE TABLE nav_edges (
            edge_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node_id INTEGER NOT NULL REFERENCES nav_nodes(node_id),
            to_node_id   INTEGER NOT NULL REFERENCES nav_nodes(node_id),
            distance_m   REAL    NOT NULL,
            edge_type    TEXT    NOT NULL    -- walk | escalator | lift | corridor
        );
    """)

    # ── 1. Zone nodes ─────────────────────────────────────────────────────────
    zones = cur.execute("""
        SELECT z.zone_id, z.zone_name,
               z.center_x_m, z.center_y_m,
               f.floor_id, f.floor_code, f.floor_level,
               t.terminal_id, t.code AS terminal_code
        FROM zones z
        JOIN floors    f ON z.floor_id    = f.floor_id
        JOIN terminals t ON f.terminal_id = t.terminal_id
        WHERE z.center_x_m IS NOT NULL AND z.center_y_m IS NOT NULL
    """).fetchall()

    zone_node: dict[int, int] = {}   # zone_id → node_id
    for z in zones:
        cur.execute("""
            INSERT INTO nav_nodes
                (node_type, ref_id, label, floor_id, floor_code, floor_level,
                 terminal_id, terminal_code, x_m, y_m)
            VALUES ('zone', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (z["zone_id"], z["zone_name"], z["floor_id"], z["floor_code"],
              z["floor_level"], z["terminal_id"], z["terminal_code"],
              z["center_x_m"], z["center_y_m"]))
        zone_node[z["zone_id"]] = cur.lastrowid

    logger.info(f"Inserted {len(zone_node)} zone nodes.")

    # ── 2. Inter-terminal connector virtual nodes (one per terminal side) ─────
    t1 = cur.execute("SELECT * FROM terminals WHERE terminal_id=1").fetchone()
    t2 = cur.execute("SELECT * FROM terminals WHERE terminal_id=2").fetchone()
    t1_l2 = cur.execute(
        "SELECT * FROM floors WHERE terminal_id=1 AND floor_code='L2'"
    ).fetchone()
    t2_l2 = cur.execute(
        "SELECT * FROM floors WHERE terminal_id=2 AND floor_code='L2'"
    ).fetchone()

    t1_conn_id = t2_conn_id = None
    if t1 and t2 and t1_l2 and t2_l2:
        # Corridor runs along the x-overlap between the two terminal bounding boxes
        cx = (max(t1["sw_x_m"], t2["sw_x_m"]) +
              min(t1["ne_x_m"], t2["ne_x_m"])) / 2

        cur.execute("""
            INSERT INTO nav_nodes
                (node_type, ref_id, label, floor_id, floor_code, floor_level,
                 terminal_id, terminal_code, x_m, y_m)
            VALUES ('connector', NULL, 'Inter-Terminal Walkway (T1 exit)',
                    ?, 'L2', 2, 1, 'T1', ?, ?)
        """, (t1_l2["floor_id"], cx, t1["ne_y_m"]))
        t1_conn_id = cur.lastrowid

        cur.execute("""
            INSERT INTO nav_nodes
                (node_type, ref_id, label, floor_id, floor_code, floor_level,
                 terminal_id, terminal_code, x_m, y_m)
            VALUES ('connector', NULL, 'Inter-Terminal Walkway (T2 exit)',
                    ?, 'L2', 2, 2, 'T2', ?, ?)
        """, (t2_l2["floor_id"], cx, t2["sw_y_m"]))
        t2_conn_id = cur.lastrowid

        corridor_dist = round(abs(t2["sw_y_m"] - t1["ne_y_m"]))
        cur.execute(
            "INSERT INTO nav_edges VALUES (NULL,?,?,?,'corridor')",
            (t1_conn_id, t2_conn_id, corridor_dist)
        )
        cur.execute(
            "INSERT INTO nav_edges VALUES (NULL,?,?,?,'corridor')",
            (t2_conn_id, t1_conn_id, corridor_dist)
        )
        logger.info(f"Corridor nodes added, length={corridor_dist}m.")

    # Reload all nodes for edge building
    all_nodes = cur.execute("SELECT * FROM nav_nodes").fetchall()

    # ── 3. Walk edges — zone ↔ zone on same floor, within 300 m ──────────────
    walk_added = 0
    for i, a in enumerate(all_nodes):
        for b in all_nodes[i + 1:]:
            if a["floor_id"] != b["floor_id"]:
                continue
            d = round(_euclid(a["x_m"], a["y_m"], b["x_m"], b["y_m"]))
            if d > 300:
                continue
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'walk')",
                (a["node_id"], b["node_id"], d)
            )
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'walk')",
                (b["node_id"], a["node_id"], d)
            )
            walk_added += 2
    logger.info(f"Walk edges added: {walk_added}.")

    # Helper: find the node on a given floor+terminal closest to (x,y)
    def nearest_node_on_floor(tid, fid, x, y):
        candidates = [n for n in all_nodes
                      if n["terminal_id"] == tid and n["floor_id"] == fid]
        if not candidates:
            return None
        return min(candidates, key=lambda n: _euclid(x, y, n["x_m"], n["y_m"]))

    # ── 4. Escalator edges ────────────────────────────────────────────────────
    escalators = cur.execute("""
        SELECT e.code, e.x_m, e.y_m, e.direction,
               f1.floor_id AS from_fid, f1.floor_level AS from_lvl,
               f2.floor_id AS to_fid,   f2.floor_level AS to_lvl,
               e.terminal_id
        FROM escalators e
        JOIN floors f1 ON e.from_floor_id = f1.floor_id
        JOIN floors f2 ON e.to_floor_id   = f2.floor_id
        WHERE e.x_m IS NOT NULL AND e.y_m IS NOT NULL
    """).fetchall()

    esc_added = 0
    for e in escalators:
        na = nearest_node_on_floor(e["terminal_id"], e["from_fid"], e["x_m"], e["y_m"])
        nb = nearest_node_on_floor(e["terminal_id"], e["to_fid"],   e["x_m"], e["y_m"])
        if not na or not nb:
            continue
        # Total cost = walk to escalator + small floor-change penalty + walk from
        da = round(_euclid(e["x_m"], e["y_m"], na["x_m"], na["y_m"]))
        db_ = round(_euclid(e["x_m"], e["y_m"], nb["x_m"], nb["y_m"]))
        cost = da + db_ + 10   # 10m equivalent for stepping on/off escalator

        if e["direction"] in ("UP", "DUAL"):
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'escalator')",
                (na["node_id"], nb["node_id"], cost)
            )
            esc_added += 1
        if e["direction"] in ("DOWN", "DUAL"):
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'escalator')",
                (nb["node_id"], na["node_id"], cost)
            )
            esc_added += 1

    logger.info(f"Escalator edges added: {esc_added}.")

    # ── 5. Lift edges (bidirectional, all adjacent served floors) ─────────────
    lifts = cur.execute("""
        SELECT l.lift_code, l.x_m, l.y_m, l.serves_floors, l.terminal_id
        FROM lifts l
        WHERE l.x_m IS NOT NULL AND l.y_m IS NOT NULL
    """).fetchall()

    lift_added = 0
    for lft in lifts:
        served_codes = [s.strip() for s in lft["serves_floors"].split(",")]
        served_floors = cur.execute(
            "SELECT floor_id, floor_code, floor_level FROM floors "
            "WHERE terminal_id=? AND floor_code IN ({}) ORDER BY floor_level".format(
                ",".join("?" * len(served_codes))
            ),
            [lft["terminal_id"]] + served_codes,
        ).fetchall()

        for i in range(len(served_floors) - 1):
            fa, fb = served_floors[i], served_floors[i + 1]
            na = nearest_node_on_floor(lft["terminal_id"], fa["floor_id"],
                                       lft["x_m"], lft["y_m"])
            nb = nearest_node_on_floor(lft["terminal_id"], fb["floor_id"],
                                       lft["x_m"], lft["y_m"])
            if not na or not nb:
                continue
            da  = round(_euclid(lft["x_m"], lft["y_m"], na["x_m"], na["y_m"]))
            db_ = round(_euclid(lft["x_m"], lft["y_m"], nb["x_m"], nb["y_m"]))
            cost = da + db_ + 15   # 15m lift penalty
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'lift')",
                (na["node_id"], nb["node_id"], cost)
            )
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'lift')",
                (nb["node_id"], na["node_id"], cost)
            )
            lift_added += 2

    logger.info(f"Lift edges added: {lift_added}.")

    # ── 6. Connect connector nodes to nearest zone on same floor ──────────────
    if t1_conn_id and t2_conn_id:
        conn_nodes = cur.execute(
            "SELECT * FROM nav_nodes WHERE node_type='connector'"
        ).fetchall()
        for cn in conn_nodes:
            nearby = [n for n in all_nodes
                      if n["floor_id"] == cn["floor_id"]
                      and n["terminal_id"] == cn["terminal_id"]
                      and n["node_type"] == "zone"]
            if not nearby:
                continue
            nearest = min(nearby,
                          key=lambda n: _euclid(cn["x_m"], cn["y_m"],
                                                n["x_m"], n["y_m"]))
            d = round(_euclid(cn["x_m"], cn["y_m"],
                              nearest["x_m"], nearest["y_m"]))
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'walk')",
                (cn["node_id"], nearest["node_id"], d)
            )
            cur.execute(
                "INSERT INTO nav_edges VALUES (NULL,?,?,?,'walk')",
                (nearest["node_id"], cn["node_id"], d)
            )

    conn.commit()

    nc = cur.execute("SELECT COUNT(*) FROM nav_nodes").fetchone()[0]
    ec = cur.execute("SELECT COUNT(*) FROM nav_edges").fetchone()[0]
    logger.info(f"Nav graph complete: {nc} nodes, {ec} edges.")
    return nc, ec


if __name__ == "__main__":
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    n, e = build(db)
    db.close()
    print(f"[OK] nav graph built: {n} nodes, {e} edges -> {DB_PATH}")
