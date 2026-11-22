"""Realism report — does the synthetic estate behave like a real one?

Measures the structural properties that affect SQL generation and
joins: duplicate and null rates, join fan-out, key skew, day-of-week
activity, and leading-digit frequencies (Benford's law) on numeric
columns. A wind tunnel does not need production's exact numbers — it
needs the same behavioral trends.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from pathlib import Path

BENFORD_EXPECTED = {d: math.log10(1 + 1 / d) for d in range(1, 10)}


def _benford(values: list[int]) -> dict:
    digits = [int(str(abs(v))[0]) for v in values if v]
    n = len(digits)
    if n == 0:
        return {"n": 0}
    counts = Counter(digits)
    observed = {d: round(counts.get(d, 0) / n, 4) for d in range(1, 10)}
    mad = sum(abs(observed[d] - BENFORD_EXPECTED[d]) for d in range(1, 10)) / 9
    return {
        "n": n,
        "observed": observed,
        "expected": {d: round(BENFORD_EXPECTED[d], 4) for d in range(1, 10)},
        "mean_absolute_deviation": round(mad, 4),
    }


def realism_report(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]

    report: dict = {"tables": {}}
    for table in tables:
        cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')]
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        col_list = ", ".join(f'"{c}"' for c in cols)
        distinct_rows = conn.execute(
            f'SELECT COUNT(*) FROM (SELECT DISTINCT {col_list} FROM "{table}")'
        ).fetchone()[0]
        null_rates = {}
        for col in cols:
            nulls = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NULL'
            ).fetchone()[0]
            null_rates[col] = round(nulls / total, 4) if total else 0.0
        report["tables"][table] = {
            "rows": total,
            "exact_duplicate_rate": round(1 - distinct_rows / total, 4) if total else 0.0,
            "null_rates": {c: r for c, r in null_rates.items() if r > 0},
        }

    # Business-key duplication on the defect table (retry storms).
    total_defects = conn.execute("SELECT COUNT(*) FROM defect_records").fetchone()[0]
    distinct_events = conn.execute(
        "SELECT COUNT(DISTINCT event_key) FROM defect_records"
    ).fetchone()[0]
    report["defect_event_key_duplicate_rate"] = (
        round(1 - distinct_events / total_defects, 4) if total_defects else 0.0
    )

    # Join fan-out: defects joined to their production runs.
    joined = conn.execute(
        "SELECT COUNT(*) FROM defect_records d JOIN production_runs r ON d.run_id = r.run_id"
    ).fetchone()[0]
    report["join_fanout"] = {
        "defect_records_x_production_runs": {
            "left_rows": total_defects,
            "joined_rows": joined,
            "fanout_factor": round(joined / total_defects, 3) if total_defects else 0.0,
        }
    }

    # Key skew: plant key variants actually present in production runs.
    report["plant_key_distribution"] = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT plant_id, COUNT(*) FROM production_runs GROUP BY plant_id ORDER BY plant_id"
        )
    }

    # Day-of-week activity (SQLite %w: 0=Sunday).
    report["runs_by_weekday"] = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT CAST(strftime('%w', run_date) AS INTEGER), COUNT(*) "
            "FROM production_runs GROUP BY 1 ORDER BY 1"
        )
    }

    # Benford checks on numeric columns.
    durations = [r[0] for r in conn.execute("SELECT duration_minutes FROM downtime_events")]
    units = [
        r[0]
        for r in conn.execute(
            "SELECT units_produced FROM production_runs WHERE unit_of_measure='pieces'"
        )
    ]
    quantities = [r[0] for r in conn.execute("SELECT quantity FROM orders")]
    report["benford"] = {
        "downtime_events.duration_minutes": _benford(durations),
        "production_runs.units_produced (pieces)": _benford(units),
        "orders.quantity": _benford(quantities),
    }

    conn.close()
    return report


def format_realism(report: dict) -> str:
    lines = ["Realism report", "=============="]
    for table, stats in report["tables"].items():
        lines.append(
            f"{table}: {stats['rows']} rows, "
            f"exact-duplicate rate {stats['exact_duplicate_rate']:.1%}"
        )
        for col, rate in stats["null_rates"].items():
            lines.append(f"  null rate {col}: {rate:.1%}")
    lines.append(
        f"defect event_key duplicate rate: {report['defect_event_key_duplicate_rate']:.1%}"
    )
    fan = report["join_fanout"]["defect_records_x_production_runs"]
    lines.append(
        f"join fan-out defects x runs: {fan['left_rows']} -> {fan['joined_rows']} "
        f"(factor {fan['fanout_factor']})"
    )
    lines.append(f"plant keys in production_runs: {report['plant_key_distribution']}")
    lines.append(f"runs by weekday (0=Sun): {report['runs_by_weekday']}")
    for col, check in report["benford"].items():
        if check.get("n"):
            lines.append(
                f"Benford {col}: n={check['n']}, "
                f"mean abs deviation {check['mean_absolute_deviation']:.3f}"
            )
    return "\n".join(lines)
