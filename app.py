"""
app.py — Maritime Liner Logistics Management System
FastAPI backend: SQLite integration, KPI aggregation, schedule data,
and the Empty Container Repositioning (ECR) Transportation Problem solver.

Run:
    uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

import math
import copy
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, model_validator
import sqlite3
import os

from init_db import init_database, get_connection, DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# LIFESPAN — initialise DB on startup
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield

app = FastAPI(title="Maritime Liner Logistics", version="1.0.0", lifespan=lifespan)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def db_rows(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return a list of plain dicts."""
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def db_one(sql: str, params: tuple = ()) -> dict | None:
    """Execute a SELECT and return a single plain dict or None."""
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# STATIC & INDEX
# ─────────────────────────────────────────────────────────────────────────────
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "index.html")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ─────────────────────────────────────────────────────────────────────────────
# API — KPI DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/kpi")
async def get_kpi():
    """
    Returns the four dashboard KPI cards:
      1. active_voyages       – count of voyages with status EN_ROUTE or BERTHED
      2. schedule_reliability – % legs where ATA <= ETA (on-time)
      3. empty_imbalance      – total absolute TEU surplus/deficit across all ports
      4. bunker_consumption   – estimated MT VLSFO for all active voyage legs
    """
    # 1. Active Voyages
    active = db_one(
        "SELECT COUNT(*) AS c FROM voyages WHERE status IN ('EN_ROUTE','BERTHED')"
    )["c"]

    # 2. Schedule Reliability — compare ATA vs ETA on legs that have both values
    legs = db_rows(
        "SELECT eta, ata FROM voyage_legs WHERE eta IS NOT NULL AND ata IS NOT NULL"
    )
    total_legs = len(legs)
    on_time = sum(
        1 for l in legs
        if datetime.fromisoformat(l["ata"]) <= datetime.fromisoformat(l["eta"])
    )
    reliability = round((on_time / total_legs * 100) if total_legs else 0, 1)

    # 3. Empty Container Imbalance Index
    #    surplus = max(0, stock - demand)   per port
    #    deficit = max(0, demand - stock)   per port
    #    index   = total_surplus + total_deficit  (lower is better)
    inv = db_rows("SELECT empty_teu_stock, demand_teu FROM container_inventory")
    total_surplus = sum(max(0, r["empty_teu_stock"] - r["demand_teu"]) for r in inv)
    total_deficit = sum(max(0, r["demand_teu"] - r["empty_teu_stock"]) for r in inv)
    imbalance_index = total_surplus + total_deficit

    # 4. Bunker Consumption (cubic-speed model: F = k·v³)
    #    k is derived from design fuel / design_speed³ per vessel
    #    We use a fixed design speed of 14 knots for all vessels.
    DESIGN_SPEED = 14.0  # knots
    vessels = db_rows("SELECT fuel_consumption_sea FROM vessels")
    active_vessel_ids = db_rows(
        "SELECT DISTINCT vessel_id FROM voyages WHERE status='EN_ROUTE'"
    )
    active_ids = {r["vessel_id"] for r in active_vessel_ids}
    vessel_rows = db_rows(
        "SELECT id, fuel_consumption_sea FROM vessels"
    )
    total_bunker = 0.0
    for v in vessel_rows:
        if v["id"] in active_ids:
            k = v["fuel_consumption_sea"] / (DESIGN_SPEED ** 3)
            # Assume current speed ≈ design speed for KPI estimate
            total_bunker += k * (DESIGN_SPEED ** 3)
    total_bunker = round(total_bunker, 1)

    return {
        "active_voyages": active,
        "schedule_reliability": reliability,
        "empty_imbalance_teu": imbalance_index,
        "bunker_consumption_mt_day": total_bunker,
        "surplus_teu": total_surplus,
        "deficit_teu": total_deficit,
    }


# ─────────────────────────────────────────────────────────────────────────────
# API — SCHEDULE & ROTATION BOARD
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/schedules")
async def get_schedules():
    """
    Returns all voyages with their ordered legs, vessel info, and port info.
    Also appends a draft_safe flag (vessel dwt used as proxy for draft check).
    """
    voyages = db_rows(
        """
        SELECT v.id, v.voyage_number, v.status,
               vs.name AS vessel_name, vs.imo_number,
               vs.capacity_teu, vs.dwt_tonnage, vs.fuel_consumption_sea
        FROM voyages v
        JOIN vessels vs ON vs.id = v.vessel_id
        ORDER BY v.id
        """
    )

    for voyage in voyages:
        legs = db_rows(
            """
            SELECT vl.stop_order, vl.eta, vl.etd, vl.ata, vl.atd,
                   p.code AS port_code, p.name AS port_name, p.max_draft
            FROM voyage_legs vl
            JOIN ports p ON p.id = vl.port_id
            WHERE vl.voyage_id = ?
            ORDER BY vl.stop_order
            """,
            (voyage["id"],),
        )
        # Draft safety: estimate vessel draft from DWT (simplified: draft ≈ DWT/2500 m)
        estimated_draft = round(voyage["dwt_tonnage"] / 2500, 1)
        for leg in legs:
            leg["estimated_vessel_draft"] = estimated_draft
            leg["draft_safe"] = estimated_draft <= leg["max_draft"]
            # Delay flag: ATA later than ETA by > 30 min
            if leg["ata"] and leg["eta"]:
                delta = (
                    datetime.fromisoformat(leg["ata"])
                    - datetime.fromisoformat(leg["eta"])
                ).total_seconds() / 3600
                leg["delay_hours"] = round(delta, 1)
            else:
                leg["delay_hours"] = None
        voyage["legs"] = legs

    return {"voyages": voyages}


# ─────────────────────────────────────────────────────────────────────────────
# API — CONTAINER INVENTORY MATRIX
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/ecr/matrix")
async def get_ecr_matrix():
    """
    Returns the supply/demand per port and the full cost matrix for the
    ECR Transport Optimization UI.
    """
    inventory = db_rows(
        """
        SELECT p.id, p.code, p.name,
               ci.empty_teu_stock, ci.demand_teu,
               (ci.empty_teu_stock - ci.demand_teu) AS net
        FROM container_inventory ci
        JOIN ports p ON p.id = ci.port_id
        ORDER BY p.id
        """
    )
    costs = db_rows(
        """
        SELECT pc.origin_port_id, pc.dest_port_id,
               p1.code AS origin_code, p2.code AS dest_code,
               p1.name AS origin_name, p2.name AS dest_name,
               pc.cost_per_teu
        FROM port_costs pc
        JOIN ports p1 ON p1.id = pc.origin_port_id
        JOIN ports p2 ON p2.id = pc.dest_port_id
        ORDER BY pc.origin_port_id, pc.dest_port_id
        """
    )
    return {"inventory": inventory, "costs": costs}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSPORTATION PROBLEM SOLVER (pure Python, no scipy)
# ─────────────────────────────────────────────────────────────────────────────
def _vam_initial_solution(
    supply: list[float],
    demand: list[float],
    cost: list[list[float]],
) -> list[list[float]]:
    """
    Vogel's Approximation Method (VAM) — produces an initial Basic Feasible Solution
    closer to optimal than North-West Corner. Minimises transportation cost.

    Args:
        supply  – list of supply quantities at each source [m]
        demand  – list of demand quantities at each destination [n]
        cost    – m×n cost matrix (cost[i][j] per unit shipped from i to j)

    Returns:
        allocation – m×n matrix of allocated quantities
    """
    m, n = len(supply), len(demand)
    s = supply[:]
    d = demand[:]
    alloc = [[0.0] * n for _ in range(m)]

    row_done = [False] * m
    col_done = [False] * n

    def row_penalty(i):
        vals = sorted(cost[i][j] for j in range(n) if not col_done[j])
        return vals[1] - vals[0] if len(vals) >= 2 else vals[0]

    def col_penalty(j):
        vals = sorted(cost[i][j] for i in range(m) if not row_done[i])
        return vals[1] - vals[0] if len(vals) >= 2 else vals[0]

    remaining = m * n - sum(row_done) - sum(col_done)

    while any(s[i] > 1e-9 for i in range(m)) and any(d[j] > 1e-9 for j in range(n)):
        # Compute penalties
        r_pen = [row_penalty(i) if not row_done[i] else -1 for i in range(m)]
        c_pen = [col_penalty(j) if not col_done[j] else -1 for j in range(n)]

        max_r = max(r_pen)
        max_c = max(c_pen)

        if max_r >= max_c:
            i = r_pen.index(max_r)
            # Assign to minimum cost column not yet done
            j = min(
                (j for j in range(n) if not col_done[j]),
                key=lambda j: cost[i][j],
            )
        else:
            j = c_pen.index(max_c)
            # Assign to minimum cost row not yet done
            i = min(
                (i for i in range(m) if not row_done[i]),
                key=lambda i: cost[i][j],
            )

        qty = min(s[i], d[j])
        alloc[i][j] += qty
        s[i] -= qty
        d[j] -= qty

        if s[i] < 1e-9:
            row_done[i] = True
        if d[j] < 1e-9:
            col_done[j] = True

    return alloc


def _total_cost(alloc: list[list[float]], cost: list[list[float]]) -> float:
    return sum(
        alloc[i][j] * cost[i][j]
        for i in range(len(alloc))
        for j in range(len(alloc[0]))
    )


def solve_transportation(
    supply: list[float],
    demand: list[float],
    cost: list[list[float]],
    supply_labels: list[str],
    demand_labels: list[str],
) -> dict[str, Any]:
    """
    Solves the Transportation Problem (minimise total cost) using VAM.

    Handles supply ≠ demand imbalance automatically by inserting a dummy
    supply node (if total_demand > total_supply) or dummy demand node
    (if total_supply > total_demand) with zero cost.

    Returns:
        {
          "allocations": [ {origin, destination, teu, cost_per_teu, total_cost}, … ],
          "total_cost":  float,
          "balanced":    bool,
          "dummy_used":  bool,
          "savings_vs_naive": float   (vs uniform allocation baseline)
        }
    """
    total_supply = sum(supply)
    total_demand = sum(demand)
    dummy_used = False
    s = supply[:]
    d = demand[:]
    c = [row[:] for row in cost]
    s_labels = supply_labels[:]
    d_labels = demand_labels[:]

    # ── Balance the problem ───────────────────────────────
    if abs(total_supply - total_demand) > 1e-6:
        dummy_used = True
        if total_supply > total_demand:
            # Add dummy demand column with 0 cost
            diff = total_supply - total_demand
            d.append(diff)
            d_labels.append("DUMMY_DEMAND")
            for row in c:
                row.append(0.0)
        else:
            # Add dummy supply row with 0 cost
            diff = total_demand - total_supply
            s.append(diff)
            s_labels.append("DUMMY_SUPPLY")
            c.append([0.0] * len(d))

    # ── Solve via VAM ─────────────────────────────────────
    alloc = _vam_initial_solution(s, d, c)
    opt_cost = _total_cost(alloc, c)

    # ── Build result rows (skip dummy / zero allocations) ─
    results = []
    for i, orig in enumerate(s_labels):
        for j, dest in enumerate(d_labels):
            qty = alloc[i][j]
            if qty < 0.5:
                continue
            if "DUMMY" in orig or "DUMMY" in dest:
                continue
            results.append(
                {
                    "origin": orig,
                    "destination": dest,
                    "teu": round(qty),
                    "cost_per_teu": c[i][j],
                    "total_cost": round(qty * c[i][j], 2),
                }
            )

    # ── Savings vs naïve equal-distribution baseline ──────
    # Naïve: each surplus port ships evenly to all deficit ports at avg cost
    avg_cost = sum(
        c[i][j]
        for i in range(len(supply))
        for j in range(len(demand))
    ) / max(1, len(supply) * len(demand))
    naive_cost = avg_cost * min(total_supply, total_demand)
    savings = round(max(0.0, naive_cost - opt_cost), 2)

    return {
        "allocations": results,
        "total_cost": round(opt_cost, 2),
        "balanced": not dummy_used,
        "dummy_used": dummy_used,
        "savings_vs_naive": savings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# API — RUN ECR OPTIMIZER
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/ecr/optimize")
async def run_ecr_optimizer():
    """
    Reads current container_inventory and port_costs from DB,
    identifies surplus (supply) and deficit (demand) ports,
    then solves the Transportation Problem via VAM to minimise relocation cost.
    """
    inventory = db_rows(
        """
        SELECT p.id, p.code, p.name,
               ci.empty_teu_stock, ci.demand_teu
        FROM container_inventory ci
        JOIN ports p ON p.id = ci.port_id
        ORDER BY p.id
        """
    )

    # Separate surplus vs deficit ports
    surplus_ports = [r for r in inventory if r["empty_teu_stock"] > r["demand_teu"]]
    deficit_ports = [r for r in inventory if r["demand_teu"] > r["empty_teu_stock"]]

    if not surplus_ports or not deficit_ports:
        return {
            "allocations": [],
            "total_cost": 0,
            "balanced": True,
            "dummy_used": False,
            "savings_vs_naive": 0,
            "message": "No imbalance detected — no repositioning required.",
        }

    supply = [r["empty_teu_stock"] - r["demand_teu"] for r in surplus_ports]
    demand = [r["demand_teu"] - r["empty_teu_stock"] for r in deficit_ports]
    supply_labels = [r["name"] for r in surplus_ports]
    demand_labels = [r["name"] for r in deficit_ports]

    # Build cost sub-matrix (surplus → deficit only)
    cost_matrix: list[list[float]] = []
    cost_lookup = {
        (r["origin_port_id"], r["dest_port_id"]): r["cost_per_teu"]
        for r in db_rows("SELECT * FROM port_costs")
    }
    for sp in surplus_ports:
        row = []
        for dp in deficit_ports:
            row.append(cost_lookup.get((sp["id"], dp["id"]), 9999.0))
        cost_matrix.append(row)

    result = solve_transportation(supply, demand, cost_matrix, supply_labels, demand_labels)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# API — BUNKER SPEED SIMULATOR
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/bunker/simulate")
async def simulate_bunker(
    speed_knots: float = 14.0,
    vessel_id: int = 1,
    distance_nm: float = 900.0,
):
    """
    Cubic-speed fuel model: F(v) = k · v³
      k is derived from the vessel's design fuel rate at design speed (14 knots).

    Returns:
      - fuel_mt_day    : MT/day at the requested speed
      - voyage_days    : voyage duration in days
      - total_fuel_mt  : total MT consumed for the voyage
      - eta_delta_h    : ETA delta vs design speed (negative = earlier)
    """
    DESIGN_SPEED = 14.0  # knots

    if speed_knots < 5 or speed_knots > 25:
        raise HTTPException(422, "speed_knots must be between 5 and 25")

    vessel = db_one("SELECT * FROM vessels WHERE id=?", (vessel_id,))
    if not vessel:
        raise HTTPException(404, "Vessel not found")

    design_fuel = vessel["fuel_consumption_sea"]  # MT/day at design speed
    k = design_fuel / (DESIGN_SPEED ** 3)

    fuel_mt_day = round(k * (speed_knots ** 3), 2)
    voyage_days_design = distance_nm / (DESIGN_SPEED * 24)
    voyage_days = round(distance_nm / (speed_knots * 24), 2)
    total_fuel = round(fuel_mt_day * voyage_days, 2)
    eta_delta_h = round((voyage_days_design - voyage_days) * 24, 1)

    return {
        "vessel_name": vessel["name"],
        "speed_knots": speed_knots,
        "design_speed_knots": DESIGN_SPEED,
        "fuel_mt_day": fuel_mt_day,
        "design_fuel_mt_day": design_fuel,
        "voyage_days": voyage_days,
        "total_fuel_mt": total_fuel,
        "eta_delta_h": eta_delta_h,
        "distance_nm": distance_nm,
    }


# ─────────────────────────────────────────────────────────────────────────────
# API — FLEET LIST
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/fleet")
async def get_fleet():
    vessels = db_rows(
        """
        SELECT vs.*,
               v.voyage_number, v.status AS voyage_status
        FROM vessels vs
        LEFT JOIN voyages v ON v.vessel_id = vs.id
                            AND v.status IN ('EN_ROUTE','BERTHED','SCHEDULED')
        ORDER BY vs.id
        """
    )
    return {"vessels": vessels}


# ─────────────────────────────────────────────────────────────────────────────
# API — DB HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    try:
        db_one("SELECT 1 AS ok")
        db_size = round(os.path.getsize(DB_PATH) / 1024, 1)
        return {"status": "ok", "db": DB_PATH, "size_kb": db_size}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS — request body validation
# ─────────────────────────────────────────────────────────────────────────────

class VesselCreate(BaseModel):
    name: str
    imo_number: str
    capacity_teu: int
    dwt_tonnage: float
    fuel_consumption_sea: float

    @field_validator("capacity_teu")
    @classmethod
    def cap_positive(cls, v):
        if v <= 0:
            raise ValueError("capacity_teu must be positive")
        return v

    @field_validator("dwt_tonnage", "fuel_consumption_sea")
    @classmethod
    def floats_positive(cls, v):
        if v <= 0:
            raise ValueError("Value must be positive")
        return v

    @field_validator("imo_number")
    @classmethod
    def imo_format(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("IMO number cannot be empty")
        return v


class VesselUpdate(BaseModel):
    name: Optional[str] = None
    imo_number: Optional[str] = None
    capacity_teu: Optional[int] = None
    dwt_tonnage: Optional[float] = None
    fuel_consumption_sea: Optional[float] = None


class VoyageCreate(BaseModel):
    voyage_number: str
    vessel_id: int
    status: str = "SCHEDULED"

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        allowed = {"SCHEDULED", "EN_ROUTE", "BERTHED", "COMPLETED"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v

    @field_validator("voyage_number")
    @classmethod
    def voy_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("voyage_number cannot be empty")
        return v


class VoyageStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        allowed = {"SCHEDULED", "EN_ROUTE", "BERTHED", "COMPLETED"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class LegCreate(BaseModel):
    port_id: int
    stop_order: int
    eta: Optional[str] = None
    etd: Optional[str] = None
    ata: Optional[str] = None
    atd: Optional[str] = None


class InventoryUpdate(BaseModel):
    empty_teu_stock: int
    demand_teu: int

    @field_validator("empty_teu_stock", "demand_teu")
    @classmethod
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("TEU values must be >= 0")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# CRUD — VESSELS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/vessels")
async def list_vessels():
    """List all vessels with their current active voyage (if any)."""
    vessels = db_rows(
        """
        SELECT vs.*,
               v.id AS voyage_id, v.voyage_number, v.status AS voyage_status
        FROM vessels vs
        LEFT JOIN voyages v ON v.vessel_id = vs.id
                            AND v.status IN ('SCHEDULED','EN_ROUTE','BERTHED')
        ORDER BY vs.id
        """
    )
    return {"vessels": vessels}


@app.post("/api/vessels", status_code=201)
async def create_vessel(body: VesselCreate):
    """Create a new vessel. IMO number must be unique."""
    # Check IMO uniqueness
    existing = db_one(
        "SELECT id FROM vessels WHERE imo_number = ?", (body.imo_number,)
    )
    if existing:
        raise HTTPException(409, f"IMO number '{body.imo_number}' already exists")

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO vessels (name, imo_number, capacity_teu, dwt_tonnage, fuel_consumption_sea)"
            " VALUES (?,?,?,?,?)",
            (body.name, body.imo_number, body.capacity_teu, body.dwt_tonnage, body.fuel_consumption_sea),
        )
        conn.commit()
        new_id = cur.lastrowid

    return db_one("SELECT * FROM vessels WHERE id=?", (new_id,))


@app.put("/api/vessels/{vessel_id}")
async def update_vessel(vessel_id: int, body: VesselUpdate):
    """Update vessel fields (partial update — only provided fields are changed)."""
    vessel = db_one("SELECT * FROM vessels WHERE id=?", (vessel_id,))
    if not vessel:
        raise HTTPException(404, "Vessel not found")

    # Check IMO uniqueness if changing
    if body.imo_number and body.imo_number != vessel["imo_number"]:
        existing = db_one(
            "SELECT id FROM vessels WHERE imo_number=? AND id != ?",
            (body.imo_number, vessel_id),
        )
        if existing:
            raise HTTPException(409, f"IMO number '{body.imo_number}' already used by another vessel")

    # Build SET clause dynamically from provided fields
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return vessel  # Nothing to update

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [vessel_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE vessels SET {set_clause} WHERE id=?", values)
        conn.commit()

    return db_one("SELECT * FROM vessels WHERE id=?", (vessel_id,))


@app.delete("/api/vessels/{vessel_id}", status_code=200)
async def delete_vessel(vessel_id: int):
    """
    Delete a vessel. Protected: cannot delete if there is an active voyage
    (status SCHEDULED, EN_ROUTE, or BERTHED) assigned to this vessel.
    """
    vessel = db_one("SELECT * FROM vessels WHERE id=?", (vessel_id,))
    if not vessel:
        raise HTTPException(404, "Vessel not found")

    active_voy = db_one(
        "SELECT voyage_number FROM voyages WHERE vessel_id=? AND status IN ('SCHEDULED','EN_ROUTE','BERTHED')",
        (vessel_id,),
    )
    if active_voy:
        raise HTTPException(
            409,
            f"Cannot delete vessel: active voyage '{active_voy['voyage_number']}' is assigned. "
            "Complete or reassign the voyage first.",
        )

    with get_connection() as conn:
        conn.execute("DELETE FROM vessels WHERE id=?", (vessel_id,))
        conn.commit()

    return {"deleted": vessel_id}


# ─────────────────────────────────────────────────────────────────────────────
# CRUD — VOYAGES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/voyages")
async def list_voyages():
    """List all voyages with legs and vessel name."""
    voyages = db_rows(
        """
        SELECT v.id, v.voyage_number, v.status,
               vs.id AS vessel_id, vs.name AS vessel_name, vs.imo_number,
               vs.capacity_teu, vs.dwt_tonnage
        FROM voyages v
        JOIN vessels vs ON vs.id = v.vessel_id
        ORDER BY v.id
        """
    )
    for voyage in voyages:
        legs = db_rows(
            """
            SELECT vl.id, vl.stop_order, vl.eta, vl.etd, vl.ata, vl.atd,
                   p.id AS port_id, p.code AS port_code, p.name AS port_name, p.max_draft
            FROM voyage_legs vl
            JOIN ports p ON p.id = vl.port_id
            WHERE vl.voyage_id = ?
            ORDER BY vl.stop_order
            """,
            (voyage["id"],),
        )
        estimated_draft = round(voyage["dwt_tonnage"] / 2500, 1)
        for leg in legs:
            leg["estimated_vessel_draft"] = estimated_draft
            leg["draft_safe"] = estimated_draft <= leg["max_draft"]
            if leg["ata"] and leg["eta"]:
                delta = (
                    datetime.fromisoformat(leg["ata"])
                    - datetime.fromisoformat(leg["eta"])
                ).total_seconds() / 3600
                leg["delay_hours"] = round(delta, 1)
            else:
                leg["delay_hours"] = None
        voyage["legs"] = legs
    return {"voyages": voyages}


@app.post("/api/voyages", status_code=201)
async def create_voyage(body: VoyageCreate):
    """Create a new voyage. voyage_number must be unique. vessel_id must exist."""
    if not db_one("SELECT id FROM vessels WHERE id=?", (body.vessel_id,)):
        raise HTTPException(404, "Vessel not found")

    if db_one("SELECT id FROM voyages WHERE voyage_number=?", (body.voyage_number,)):
        raise HTTPException(409, f"Voyage number '{body.voyage_number}' already exists")

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO voyages (voyage_number, vessel_id, status) VALUES (?,?,?)",
            (body.voyage_number, body.vessel_id, body.status),
        )
        conn.commit()
        new_id = cur.lastrowid

    return db_one(
        """
        SELECT v.*, vs.name AS vessel_name
        FROM voyages v JOIN vessels vs ON vs.id=v.vessel_id
        WHERE v.id=?
        """,
        (new_id,),
    )


@app.put("/api/voyages/{voyage_id}/status")
async def update_voyage_status(voyage_id: int, body: VoyageStatusUpdate):
    """Update the status of a voyage."""
    voyage = db_one("SELECT * FROM voyages WHERE id=?", (voyage_id,))
    if not voyage:
        raise HTTPException(404, "Voyage not found")

    with get_connection() as conn:
        conn.execute("UPDATE voyages SET status=? WHERE id=?", (body.status, voyage_id))
        conn.commit()

    return db_one("SELECT * FROM voyages WHERE id=?", (voyage_id,))


@app.post("/api/voyages/{voyage_id}/legs", status_code=201)
async def add_voyage_leg(voyage_id: int, body: LegCreate):
    """
    Add a port stop (leg) to a voyage.
    stop_order uniqueness is enforced per voyage by the DB UNIQUE constraint.
    """
    if not db_one("SELECT id FROM voyages WHERE id=?", (voyage_id,)):
        raise HTTPException(404, "Voyage not found")
    if not db_one("SELECT id FROM ports WHERE id=?", (body.port_id,)):
        raise HTTPException(404, "Port not found")

    # Check uniqueness of stop_order within this voyage
    if db_one(
        "SELECT id FROM voyage_legs WHERE voyage_id=? AND stop_order=?",
        (voyage_id, body.stop_order),
    ):
        raise HTTPException(409, f"stop_order {body.stop_order} already exists in this voyage")

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO voyage_legs (voyage_id, port_id, stop_order, eta, etd, ata, atd)"
            " VALUES (?,?,?,?,?,?,?)",
            (voyage_id, body.port_id, body.stop_order, body.eta, body.etd, body.ata, body.atd),
        )
        conn.commit()
        new_id = cur.lastrowid

    return db_one(
        """
        SELECT vl.*, p.code AS port_code, p.name AS port_name
        FROM voyage_legs vl JOIN ports p ON p.id=vl.port_id
        WHERE vl.id=?
        """,
        (new_id,),
    )


@app.delete("/api/voyages/{voyage_id}", status_code=200)
async def delete_voyage(voyage_id: int):
    """
    Delete a voyage and all its legs.
    Protected: EN_ROUTE and BERTHED voyages cannot be deleted directly.
    """
    voyage = db_one("SELECT * FROM voyages WHERE id=?", (voyage_id,))
    if not voyage:
        raise HTTPException(404, "Voyage not found")

    if voyage["status"] in ("EN_ROUTE", "BERTHED"):
        raise HTTPException(
            409,
            f"Cannot delete voyage with status '{voyage['status']}'. "
            "Change status to SCHEDULED or COMPLETED first.",
        )

    with get_connection() as conn:
        conn.execute("DELETE FROM voyage_legs WHERE voyage_id=?", (voyage_id,))
        conn.execute("DELETE FROM voyages WHERE id=?", (voyage_id,))
        conn.commit()

    return {"deleted": voyage_id}


# ─────────────────────────────────────────────────────────────────────────────
# CRUD — CONTAINER INVENTORY (quick update per port)
# ─────────────────────────────────────────────────────────────────────────────

@app.put("/api/ports/{port_id}/inventory")
async def update_inventory(port_id: int, body: InventoryUpdate):
    """Update empty TEU stock and demand for a specific port."""
    if not db_one("SELECT id FROM ports WHERE id=?", (port_id,)):
        raise HTTPException(404, "Port not found")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO container_inventory (port_id, empty_teu_stock, demand_teu)"
            " VALUES (?,?,?)"
            " ON CONFLICT(port_id) DO UPDATE SET"
            "   empty_teu_stock=excluded.empty_teu_stock,"
            "   demand_teu=excluded.demand_teu",
            (port_id, body.empty_teu_stock, body.demand_teu),
        )
        conn.commit()

    return db_one(
        """
        SELECT p.id, p.code, p.name,
               ci.empty_teu_stock, ci.demand_teu,
               (ci.empty_teu_stock - ci.demand_teu) AS net
        FROM container_inventory ci
        JOIN ports p ON p.id = ci.port_id
        WHERE p.id=?
        """,
        (port_id,),
    )


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY — list all ports (for dropdowns in voyage leg form)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/ports")
async def list_ports():
    """Return all ports (used for vessel/voyage form dropdowns)."""
    return {"ports": db_rows("SELECT * FROM ports ORDER BY id")}
