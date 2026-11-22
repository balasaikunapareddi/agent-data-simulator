"""Stage 2 — Simulate & build tables.

Deterministic: no LLM anywhere in this stage. Employees act on their
schedules, events create rows, and row-level flaws that model real
ingestion glitches are injected on top:

* retry-storm duplicate rows in the defect table,
* a mid-quarter unit-of-measure change in production runs,
* unmerged legacy plant keys,
* NULL reason codes on some downtime events.

Every flaw is recorded in a machine-readable messiness log so the
benchmark generator can compute ground-truth SQL against the messy
tables. Same blueprint + same seed => identical tables, every run.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .blueprint import Blueprint

SCHEMA = """
CREATE TABLE plants (
    plant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    legacy_code TEXT
);
CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    plant_id TEXT NOT NULL,
    shift INTEGER
);
CREATE TABLE production_runs (
    run_id TEXT PRIMARY KEY,
    plant_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    shift INTEGER NOT NULL,
    run_date TEXT NOT NULL,
    units_produced INTEGER NOT NULL,
    unit_of_measure TEXT NOT NULL
);
CREATE TABLE downtime_events (
    event_id TEXT PRIMARY KEY,
    plant_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    shift INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    reason_code TEXT
);
CREATE TABLE defect_records (
    record_id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    plant_id TEXT NOT NULL,
    defect_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    logged_at TEXT NOT NULL
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer TEXT NOT NULL,
    plant_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL
);
"""

LINES = ["L1", "L2"]
DEFECT_TYPES = ["crack", "porosity", "dimension_out_of_spec", "surface_scratch", "coating_void"]
SEVERITIES = ["minor", "major", "critical"]
CUSTOMERS = [
    "Apex Auto Group",
    "BlueRail Transit",
    "Cobalt Heavy Trucks",
    "Driftline EV",
    "Everline Motors",
    "Ferrostat Rail",
]


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def build_tables(
    blueprint: Blueprint,
    db_path: Path,
    seed: int = 42,
) -> dict:
    """Build the estate database deterministically. Returns a summary."""
    rng = random.Random(seed)
    flaws = blueprint.flaw_params
    unit_change = date.fromisoformat(flaws["unit_change_date"])
    pieces_per_case = int(flaws["pieces_per_case"])
    start = date.fromisoformat(blueprint.period_start)
    end = date.fromisoformat(blueprint.period_end)

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # --- reference data -------------------------------------------------
    for plant in blueprint.plants:
        conn.execute(
            "INSERT INTO plants VALUES (?, ?, ?, ?)",
            (plant["plant_id"], plant["name"], plant["city"], plant["legacy_code"]),
        )
    for person in blueprint.people:
        conn.execute(
            "INSERT INTO employees VALUES (?, ?, ?, ?, ?)",
            (person.employee_id, person.name, person.role, person.plant_id, person.shift),
        )

    legacy_code = {p["plant_id"]: p["legacy_code"] for p in blueprint.plants}

    messiness: list[dict] = []
    dup_event_keys: list[str] = []
    dup_extra_rows = 0
    legacy_key_rows = 0
    null_reason_rows = 0
    case_rows = 0
    piece_rows = 0

    run_seq = 0
    event_seq = 0
    defect_seq = 0
    record_seq = 0
    order_seq = 0

    # Per-shift downtime behaviour. Shift 2 at Plant 2 runs far more
    # planned changeovers than anyone else; unplanned stoppages are
    # roughly even (Shift 1 slightly worse). This is what makes the
    # "which shift has the most downtime" answer flip when changeovers
    # are wrongly excluded.
    def changeover_profile(plant_id: str, shift: int) -> tuple[float, int, int]:
        if plant_id == "PLANT-2" and shift == 2:
            return (0.90, 45, 95)
        if shift == 2:
            return (0.50, 30, 60)
        return (0.30, 25, 55)

    def unplanned_profile(shift: int) -> tuple[float, int, int]:
        means = {1: (0.45, 15, 110), 2: (0.45, 10, 80), 3: (0.45, 12, 100)}
        return means[shift]

    for day in _daterange(start, end):
        iso = day.isoformat()
        for plant in blueprint.plants:
            plant_id = plant["plant_id"]
            for line in LINES:
                for shift in (1, 2, 3):
                    run_seq += 1
                    run_id = f"RUN-{run_seq:06d}"

                    # Production units: draw in pieces (multiples of the
                    # case size so conversion is exact), then record in
                    # cases after the unit-change date.
                    pieces = rng.randrange(60, 111) * pieces_per_case
                    if day >= unit_change:
                        units, uom = pieces // pieces_per_case, "cases"
                        case_rows += 1
                    else:
                        units, uom = pieces, "pieces"
                        piece_rows += 1

                    stored_plant = plant_id
                    if plant_id == "PLANT-2" and rng.random() < flaws["legacy_key_rate_plant2"]:
                        stored_plant = legacy_code[plant_id]
                        legacy_key_rows += 1

                    conn.execute(
                        "INSERT INTO production_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (run_id, stored_plant, line, shift, iso, units, uom),
                    )

                    # Downtime events for this run.
                    events: list[tuple[str, int, str | None]] = []
                    p_co, lo, hi = changeover_profile(plant_id, shift)
                    if rng.random() < p_co:
                        events.append(("planned_changeover", rng.randint(lo, hi), "product_switch"))
                    p_un, lo, hi = unplanned_profile(shift)
                    if rng.random() < p_un:
                        events.append(
                            (
                                "unplanned_stoppage",
                                rng.randint(lo, hi),
                                rng.choice(
                                    ["tool_break", "material_shortage", "sensor_fault", "power_dip"]
                                ),
                            )
                        )
                    if rng.random() < 0.08:
                        events.append(("planned_maintenance", rng.randint(60, 180), "scheduled_service"))

                    for event_type, minutes, reason in events:
                        event_seq += 1
                        if rng.random() < flaws["null_reason_rate"]:
                            reason = None
                            null_reason_rows += 1
                        conn.execute(
                            "INSERT INTO downtime_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                f"DT-{event_seq:06d}",
                                plant_id,
                                line,
                                shift,
                                iso,
                                event_type,
                                minutes,
                                reason,
                            ),
                        )

                    # Defect records for this run.
                    n_defects = rng.choices([0, 1, 2, 3, 4, 5], weights=[30, 30, 20, 10, 6, 4])[0]
                    for _ in range(n_defects):
                        defect_seq += 1
                        event_key = f"DEF-{defect_seq:06d}"
                        dtype = rng.choice(DEFECT_TYPES)
                        severity = rng.choices(SEVERITIES, weights=[70, 22, 8])[0]
                        base_minute = rng.randint(0, 400)
                        copies = 1
                        if rng.random() < flaws["defect_retry_rate"]:
                            copies += rng.randint(1, 3)
                            dup_event_keys.append(event_key)
                            dup_extra_rows += copies - 1
                        for copy_idx in range(copies):
                            record_seq += 1
                            logged = f"{iso}T{6 + (base_minute + copy_idx * 2) // 60:02d}:{(base_minute + copy_idx * 2) % 60:02d}:00"
                            conn.execute(
                                "INSERT INTO defect_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (
                                    f"REC-{record_seq:06d}",
                                    event_key,
                                    run_id,
                                    plant_id,
                                    dtype,
                                    severity,
                                    logged,
                                ),
                            )

            # Orders for the day.
            for _ in range(rng.randint(2, 5)):
                order_seq += 1
                conn.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"ORD-{order_seq:06d}",
                        rng.choice(CUSTOMERS),
                        plant_id,
                        iso,
                        rng.randrange(50, 2000),
                        rng.choices(["shipped", "in_production", "open"], weights=[60, 25, 15])[0],
                    ),
                )

    conn.commit()

    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ["plants", "employees", "production_runs", "downtime_events", "defect_records", "orders"]
    }
    conn.close()

    # --- messiness log --------------------------------------------------
    messiness.append(
        {
            "flaw_type": "retry_storm_duplicates",
            "table": "defect_records",
            "description": (
                "Scanner retries re-inserted the same defect event. Duplicate "
                "rows share event_key; count distinct event_key for true counts."
            ),
            "affected_rows": dup_extra_rows,
            "distinct_events_affected": len(dup_event_keys),
            "event_keys": dup_event_keys,
        }
    )
    messiness.append(
        {
            "flaw_type": "unit_of_measure_change",
            "table": "production_runs",
            "description": (
                f"From {flaws['unit_change_date']} the ERP records production in "
                f"cases of {pieces_per_case} pieces instead of individual pieces "
                "(unit_of_measure column). Normalize before summing."
            ),
            "affected_rows": case_rows,
            "rows_before_change": piece_rows,
            "change_date": flaws["unit_change_date"],
            "pieces_per_case": pieces_per_case,
        }
    )
    messiness.append(
        {
            "flaw_type": "unmerged_legacy_keys",
            "table": "production_runs",
            "description": (
                "Some Plant 2 production rows carry the legacy plant code 'P2' "
                "instead of the canonical 'PLANT-2'."
            ),
            "affected_rows": legacy_key_rows,
            "canonical_key": "PLANT-2",
            "legacy_key": "P2",
        }
    )
    messiness.append(
        {
            "flaw_type": "null_reason_codes",
            "table": "downtime_events",
            "description": "Some downtime events were logged without a reason_code.",
            "affected_rows": null_reason_rows,
        }
    )

    return {
        "seed": seed,
        "row_counts": counts,
        "messiness_log": messiness,
    }


def save_messiness_log(summary: dict, path: Path) -> None:
    path.write_text(json.dumps(summary["messiness_log"], indent=2) + "\n")
