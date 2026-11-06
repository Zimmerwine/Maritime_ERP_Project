"""
init_db.py — Maritime Liner Logistics Management System
SQLite schema initializer & seeder.
Run once automatically from app.py on first startup.
"""

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = "maritime_liner.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ─────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vessels (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT    NOT NULL,
    imo_number              TEXT    NOT NULL UNIQUE,
    capacity_teu            INTEGER NOT NULL,
    dwt_tonnage             REAL    NOT NULL,
    fuel_consumption_sea    REAL    NOT NULL   -- MT/day at design speed (knots)
);

CREATE TABLE IF NOT EXISTS ports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,   -- UN/LOCODE
    name        TEXT    NOT NULL,
    max_draft   REAL    NOT NULL           -- metres
);

CREATE TABLE IF NOT EXISTS voyages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    voyage_number   TEXT    NOT NULL UNIQUE,
    vessel_id       INTEGER NOT NULL REFERENCES vessels(id),
    status          TEXT    NOT NULL DEFAULT 'SCHEDULED'
                    CHECK(status IN ('SCHEDULED','EN_ROUTE','BERTHED','COMPLETED'))
);

CREATE TABLE IF NOT EXISTS voyage_legs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    voyage_id   INTEGER NOT NULL REFERENCES voyages(id),
    port_id     INTEGER NOT NULL REFERENCES ports(id),
    stop_order  INTEGER NOT NULL,
    eta         TEXT,      -- ISO-8601
    etd         TEXT,
    ata         TEXT,      -- actual
    atd         TEXT,
    UNIQUE(voyage_id, stop_order)
);

CREATE TABLE IF NOT EXISTS container_inventory (
    port_id         INTEGER PRIMARY KEY REFERENCES ports(id),
    empty_teu_stock INTEGER NOT NULL DEFAULT 0,
    demand_teu      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS port_costs (
    origin_port_id  INTEGER NOT NULL REFERENCES ports(id),
    dest_port_id    INTEGER NOT NULL REFERENCES ports(id),
    cost_per_teu    REAL    NOT NULL,
    PRIMARY KEY (origin_port_id, dest_port_id)
);
"""


# ─────────────────────────────────────────────────────────────
# SEED DATA
# ─────────────────────────────────────────────────────────────
def seed(conn: sqlite3.Connection):
    cur = conn.cursor()

    # ── Vessels ────────────────────────────────────────────
    vessels = [
        ("MV Nusantara Jaya",  "IMO9234567", 1800, 25000, 42.0),
        ("MV Bahari Perkasa",  "IMO9345678", 2400, 33000, 55.0),
        ("MV Samudera Abadi",  "IMO9456789", 1200, 18000, 30.0),
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO vessels (name,imo_number,capacity_teu,dwt_tonnage,fuel_consumption_sea)"
        " VALUES (?,?,?,?,?)",
        vessels,
    )

    # ── Ports ──────────────────────────────────────────────
    # Fixed liner ring: Jakarta → Surabaya → Makassar → Balikpapan → Jakarta
    ports = [
        ("IDTPP", "Tanjung Priok (Jakarta)",  14.5),
        ("IDSUB", "Tanjung Perak (Surabaya)", 12.0),
        ("IDMAK", "Makassar (Soekarno-Hatta)", 10.5),
        ("IDBPN", "Balikpapan (Kariangau)",    11.0),
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO ports (code,name,max_draft) VALUES (?,?,?)",
        ports,
    )

    # ── Resolve port IDs ───────────────────────────────────
    port_ids = {
        row["code"]: row["id"]
        for row in cur.execute("SELECT id, code FROM ports").fetchall()
    }
    vessel_ids = [
        row["id"]
        for row in cur.execute("SELECT id FROM vessels ORDER BY id").fetchall()
    ]

    # ── Voyages & Legs ────────────────────────────────────
    # Ring rotation: IDTPP → IDSUB → IDMAK → IDBPN → IDTPP
    ring = ["IDTPP", "IDSUB", "IDMAK", "IDBPN"]
    # Leg durations (days) between consecutive stops
    leg_days = [2, 3, 2, 3]   # JKT→SBY=2, SBY→MAK=3, MAK→BPN=2, BPN→JKT=3

    statuses = ["EN_ROUTE", "SCHEDULED", "COMPLETED"]
    base_time = datetime(2026, 11, 1, 8, 0, 0)

    for idx, vessel_id in enumerate(vessel_ids):
        voy_num = f"NJ{2026}{idx+1:02d}001"
        status = statuses[idx % len(statuses)]
        cur.execute(
            "INSERT OR IGNORE INTO voyages (voyage_number, vessel_id, status)"
            " VALUES (?,?,?)",
            (voy_num, vessel_id, status),
        )
        voyage_id = cur.execute(
            "SELECT id FROM voyages WHERE voyage_number=?", (voy_num,)
        ).fetchone()["id"]

        # Offset each vessel voyage start by a few days so schedules spread out
        start = base_time + timedelta(days=idx * 4)
        current_eta = start
        for stop_order, code in enumerate(ring):
            eta = current_eta
            etd = eta + timedelta(hours=12)
            leg = leg_days[stop_order]
            # Add delay for "EN_ROUTE" voyage to create late-arrival scenario
            delay_h = 6 if (status == "EN_ROUTE" and stop_order == 1) else 0
            ata = (eta + timedelta(hours=delay_h)) if status in ("EN_ROUTE", "COMPLETED") else None
            atd = (etd + timedelta(hours=delay_h)) if status == "COMPLETED" else None

            cur.execute(
                "INSERT OR IGNORE INTO voyage_legs"
                " (voyage_id, port_id, stop_order, eta, etd, ata, atd)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    voyage_id,
                    port_ids[code],
                    stop_order,
                    eta.isoformat(),
                    etd.isoformat(),
                    ata.isoformat() if ata else None,
                    atd.isoformat() if atd else None,
                ),
            )
            current_eta = etd + timedelta(days=leg)

    # ── Container Inventory ───────────────────────────────
    # Supply (surplus) at Jakarta & Surabaya, demand at Makassar & Balikpapan
    inventory = [
        (port_ids["IDTPP"], 850, 200),   # Jakarta  surplus
        (port_ids["IDSUB"], 620, 150),   # Surabaya surplus
        (port_ids["IDMAK"], 100, 580),   # Makassar deficit
        (port_ids["IDBPN"], 90,  440),   # Balikpapan deficit
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO container_inventory (port_id, empty_teu_stock, demand_teu)"
        " VALUES (?,?,?)",
        inventory,
    )

    # ── Port Costs (USD / TEU)  ───────────────────────────
    # Full cost matrix between all 4 ports (symmetric but different)
    costs = [
        # origin         dest           USD/TEU
        (port_ids["IDTPP"], port_ids["IDSUB"], 65.0),
        (port_ids["IDTPP"], port_ids["IDMAK"], 120.0),
        (port_ids["IDTPP"], port_ids["IDBPN"], 145.0),
        (port_ids["IDSUB"], port_ids["IDTPP"], 65.0),
        (port_ids["IDSUB"], port_ids["IDMAK"], 90.0),
        (port_ids["IDSUB"], port_ids["IDBPN"], 110.0),
        (port_ids["IDMAK"], port_ids["IDTPP"], 120.0),
        (port_ids["IDMAK"], port_ids["IDSUB"], 90.0),
        (port_ids["IDMAK"], port_ids["IDBPN"], 55.0),
        (port_ids["IDBPN"], port_ids["IDTPP"], 145.0),
        (port_ids["IDBPN"], port_ids["IDSUB"], 110.0),
        (port_ids["IDBPN"], port_ids["IDMAK"], 55.0),
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO port_costs (origin_port_id, dest_port_id, cost_per_teu)"
        " VALUES (?,?,?)",
        costs,
    )

    conn.commit()
    print("[init_db] Database seeded successfully.")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def init_database():
    """Create schema and seed data if the DB does not yet exist."""
    first_run = not os.path.exists(DB_PATH)
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    if first_run:
        seed(conn)
    else:
        # Re-seed only if tables are empty (idempotent)
        count = conn.execute("SELECT COUNT(*) FROM vessels").fetchone()[0]
        if count == 0:
            seed(conn)
    conn.close()
    print(f"[init_db] Database ready: {DB_PATH}")


if __name__ == "__main__":
    init_database()
