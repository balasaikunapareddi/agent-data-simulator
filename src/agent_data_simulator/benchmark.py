"""Benchmark generation — the verified answer key.

Every quantitative question ships with gold SQL computed against the
messy tables, plus decoy SQL representing the common mistakes (an
un-deduplicated COUNT(*), the wrong-belief changeover filter, a naive
unit sum). At build time both are executed: if a decoy produces the
same answer as gold, the question cannot discriminate and is dropped
rather than shipped unverified.

Qualitative questions ship with a rubric grounded in the blueprint's
metric owner, official definition, and correction trail. Hallucination
sentinels ask about invented metrics that appear nowhere in the estate;
the correct behavior is abstention.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .blueprint import Blueprint

Q3_START = "2026-07-01"
Q3_END = "2026-09-30"


@dataclass
class Question:
    qid: str
    kind: str  # quantitative | qualitative | sentinel
    text: str
    answer_type: str  # hours | count | category | text | abstain
    trap: str = ""
    gold_sql: str | None = None
    decoy_sql: list[str] = field(default_factory=list)
    gold_value: float | int | str | None = None
    decoy_values: list = field(default_factory=list)
    rubric: list[dict] = field(default_factory=list)


def _run_scalar(conn: sqlite3.Connection, sql: str):
    row = conn.execute(sql).fetchone()
    return None if row is None else row[0]


def _values_equal(a, b, answer_type: str) -> bool:
    if a is None or b is None:
        return a is b
    if answer_type == "hours":
        return abs(float(a) - float(b)) <= max(0.01 * abs(float(a)), 0.5)
    if answer_type == "count":
        return int(a) == int(b)
    return str(a) == str(b)


def candidate_questions(bp: Blueprint) -> list[Question]:
    owner = bp.person(bp.metric("downtime_hours").owner)
    q3 = f"BETWEEN '{Q3_START}' AND '{Q3_END}'"

    return [
        Question(
            qid="q-downtime-plant2-q3",
            kind="quantitative",
            text="How many hours of downtime did Plant 2 log in Q3 2026?",
            answer_type="hours",
            trap="Dana's wrong-belief filter: excluding planned changeovers",
            gold_sql=(
                "SELECT ROUND(SUM(duration_minutes)/60.0, 2) FROM downtime_events "
                f"WHERE plant_id='PLANT-2' AND event_date {q3}"
            ),
            decoy_sql=[
                "SELECT ROUND(SUM(duration_minutes)/60.0, 2) FROM downtime_events "
                f"WHERE plant_id='PLANT-2' AND event_date {q3} "
                "AND event_type != 'planned_changeover'"
            ],
        ),
        Question(
            qid="q-defects-plant1-q3",
            kind="quantitative",
            text="How many defects were logged at Plant 1 in Q3 2026?",
            answer_type="count",
            trap="Retry-storm duplicates: COUNT(*) instead of COUNT(DISTINCT event_key)",
            gold_sql=(
                "SELECT COUNT(DISTINCT event_key) FROM defect_records "
                "WHERE plant_id='PLANT-1' AND logged_at >= '2026-07-01' AND logged_at < '2026-10-01'"
            ),
            decoy_sql=[
                "SELECT COUNT(*) FROM defect_records "
                "WHERE plant_id='PLANT-1' AND logged_at >= '2026-07-01' AND logged_at < '2026-10-01'"
            ],
        ),
        Question(
            qid="q-units-plant2-q3",
            kind="quantitative",
            text="How many units did Plant 2 produce in Q3 2026?",
            answer_type="count",
            trap="Mid-quarter unit change (pieces -> cases of 12) and unmerged legacy plant keys",
            gold_sql=(
                "SELECT SUM(CASE WHEN unit_of_measure='cases' THEN units_produced*12 "
                "ELSE units_produced END) FROM production_runs "
                f"WHERE plant_id IN ('PLANT-2','P2') AND run_date {q3}"
            ),
            decoy_sql=[
                # Naive: no unit normalization, canonical key only.
                "SELECT SUM(units_produced) FROM production_runs "
                f"WHERE plant_id='PLANT-2' AND run_date {q3}",
                # Half-fixed: normalized units but misses legacy-key rows.
                "SELECT SUM(CASE WHEN unit_of_measure='cases' THEN units_produced*12 "
                "ELSE units_produced END) FROM production_runs "
                f"WHERE plant_id='PLANT-2' AND run_date {q3}",
            ],
        ),
        Question(
            qid="q-shift-most-downtime",
            kind="quantitative",
            text="Which shift logged the most downtime at Plant 2 in Q3 2026?",
            answer_type="category",
            trap="Excluding changeovers flips the answer from Shift 2 to another shift",
            gold_sql=(
                "SELECT shift FROM downtime_events "
                f"WHERE plant_id='PLANT-2' AND event_date {q3} "
                "GROUP BY shift ORDER BY SUM(duration_minutes) DESC LIMIT 1"
            ),
            decoy_sql=[
                "SELECT shift FROM downtime_events "
                f"WHERE plant_id='PLANT-2' AND event_date {q3} "
                "AND event_type != 'planned_changeover' "
                "GROUP BY shift ORDER BY SUM(duration_minutes) DESC LIMIT 1"
            ],
        ),
        Question(
            qid="q-runs-plant2-q3",
            kind="quantitative",
            text="How many production runs did Plant 2 complete in Q3 2026?",
            answer_type="count",
            trap="Unmerged legacy plant keys: rows stored under 'P2' instead of 'PLANT-2'",
            gold_sql=(
                "SELECT COUNT(*) FROM production_runs "
                f"WHERE plant_id IN ('PLANT-2','P2') AND run_date {q3}"
            ),
            decoy_sql=[
                "SELECT COUNT(*) FROM production_runs "
                f"WHERE plant_id='PLANT-2' AND run_date {q3}"
            ],
        ),
        Question(
            qid="q-unplanned-august",
            kind="quantitative",
            text="How many hours of unplanned stoppage time did Plant 2 log in August 2026?",
            answer_type="hours",
            trap="Over-inclusion: counting all downtime when only unplanned stoppages were asked for",
            gold_sql=(
                "SELECT ROUND(SUM(duration_minutes)/60.0, 2) FROM downtime_events "
                "WHERE plant_id='PLANT-2' AND event_type='unplanned_stoppage' "
                "AND event_date BETWEEN '2026-08-01' AND '2026-08-31'"
            ),
            decoy_sql=[
                "SELECT ROUND(SUM(duration_minutes)/60.0, 2) FROM downtime_events "
                "WHERE plant_id='PLANT-2' "
                "AND event_date BETWEEN '2026-08-01' AND '2026-08-31'"
            ],
        ),
        Question(
            qid="q-onboarding-downtime",
            kind="qualitative",
            text=(
                "A new analyst is joining Plant 2. What should they know about how "
                "downtime is handled here before they start producing reports?"
            ),
            answer_type="text",
            trap="Summarizing Dana's confident memo instead of the official definition",
            rubric=[
                {
                    "check": "States the official definition includes planned changeovers",
                    "any_of": [
                        "planned changeovers count",
                        "changeovers count as",
                        "changeovers are counted",
                        "includes planned changeovers",
                        "changeovers included",
                        "changeovers do count",
                    ],
                },
                {
                    "check": f"Names the metric owner ({owner.name})",
                    "any_of": [owner.name.lower(), owner.name.split()[0].lower()],
                },
                {
                    "check": "Notes the correction / superseded memo",
                    "any_of": ["supersed", "correct", "later thread", "updated definition"],
                },
                {
                    "check": "Notes second shift tracked it differently",
                    "any_of": ["second shift", "shift 2", "dana"],
                },
            ],
        ),
        Question(
            qid="q-downtime-owner",
            kind="qualitative",
            text="Who owns the downtime metric at NorthPeak Components, and what is the official definition?",
            answer_type="text",
            trap="Citing the memo author (Dana) as the authority instead of the metric owner",
            rubric=[
                {
                    "check": f"Names the metric owner ({owner.name})",
                    "any_of": [owner.name.lower(), owner.name.split()[-1].lower()],
                },
                {
                    "check": "Official definition includes planned changeovers",
                    "any_of": [
                        "planned changeovers count",
                        "changeovers count as",
                        "includes planned changeovers",
                        "changeovers are counted",
                        "all time",
                        "not producing",
                    ],
                },
            ],
        ),
        Question(
            qid="q-sentinel-basalt",
            kind="sentinel",
            text="What was Plant 2's Basalt Velocity Ratio in Q3 2026?",
            answer_type="abstain",
            trap="Invented metric — correct behavior is to abstain",
        ),
        Question(
            qid="q-sentinel-quantum",
            kind="sentinel",
            text="How many Quantum Yield Points did Plant 1 earn in August 2026?",
            answer_type="abstain",
            trap="Invented metric — correct behavior is to abstain",
        ),
    ]


def build_benchmark(
    bp: Blueprint,
    db_path: Path,
    documents: list[dict],
    candidates: list[Question] | None = None,
) -> dict:
    """Compute gold answers, verify decoys diverge, drop unverifiable items."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    questions: list[Question] = []
    dropped: list[dict] = []

    # Text corpus for sentinel verification: docs + schema identifiers.
    schema_text = " ".join(
        row[0]
        for row in conn.execute(
            "SELECT name || ' ' || COALESCE(sql,'') FROM sqlite_master WHERE type='table'"
        )
    ).lower()
    doc_text = " ".join(d["title"] + " " + d["body"] for d in documents).lower()
    corpus = schema_text + " " + doc_text

    for q in candidates or candidate_questions(bp):
        if q.kind == "quantitative":
            gold = _run_scalar(conn, q.gold_sql)
            decoys = [_run_scalar(conn, sql) for sql in q.decoy_sql]
            if gold is None:
                dropped.append({"qid": q.qid, "reason": "gold SQL returned no value"})
                continue
            bad = [d for d in decoys if _values_equal(gold, d, q.answer_type)]
            if bad:
                dropped.append(
                    {
                        "qid": q.qid,
                        "reason": "a decoy answer equals gold; question cannot discriminate",
                        "gold_value": gold,
                        "decoy_values": decoys,
                    }
                )
                continue
            q.gold_value = gold
            q.decoy_values = decoys
            questions.append(q)
        elif q.kind == "sentinel":
            # The invented metric must appear nowhere in the estate.
            metric_words = [
                w for w in q.text.lower().replace("?", "").split() if len(w) > 4
            ]
            invented = [w for w in ("basalt", "quantum", "velocity ratio", "yield points") if w in q.text.lower()]
            leaked = [w for w in invented if w in corpus]
            if leaked:
                dropped.append(
                    {"qid": q.qid, "reason": f"sentinel metric appears in estate: {leaked}"}
                )
                continue
            questions.append(q)
        else:  # qualitative
            questions.append(q)

    conn.close()
    return {
        "questions": [asdict(q) for q in questions],
        "dropped": dropped,
    }


def save_benchmark(benchmark: dict, path: Path) -> None:
    path.write_text(json.dumps(benchmark, indent=2) + "\n")


def load_benchmark(path: Path) -> dict:
    return json.loads(path.read_text())
