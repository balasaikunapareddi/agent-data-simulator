"""Stage 1 — Compile blueprint.

The blueprint is the frozen spec of the simulated company: its systems,
people, metrics (with official definitions and owners), and individual
beliefs that can be deliberately wrong.

This is the ONLY stage where facts are invented. Once the blueprint is
frozen, every downstream stage (table simulation, document rendering,
benchmark generation) derives deterministically from it. The trap and
its solution are planted here, together: a supervisor's wrong-but-
confident definition of downtime sits next to the official metric and
the correction thread that supersedes it.

The agent under evaluation never sees the blueprint.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Person:
    employee_id: str
    name: str
    role: str
    plant_id: str
    shift: int | None  # None for staff who are not shift-bound
    writing_style: str = ""


@dataclass
class Metric:
    metric_id: str
    name: str
    owner: str  # employee_id of the metric owner
    official_definition: str
    unit: str


@dataclass
class Belief:
    """A belief held by one person. May contradict the official metric."""

    holder: str  # employee_id
    topic: str
    statement: str
    is_correct: bool
    motive: str = ""


@dataclass
class System:
    system_id: str
    name: str
    description: str


@dataclass
class Blueprint:
    company_name: str
    pattern_prompt: str
    quarter_label: str
    period_start: str  # ISO date, inclusive
    period_end: str  # ISO date, inclusive
    plants: list[dict]
    shifts: list[dict]
    systems: list[System]
    people: list[Person]
    metrics: list[Metric]
    beliefs: list[Belief]
    # Row-level flaw parameters consumed by the simulator.
    flaw_params: dict = field(default_factory=dict)
    # Facts the document renderer must state, keyed by document id.
    document_facts: dict = field(default_factory=dict)

    def person(self, employee_id: str) -> Person:
        for p in self.people:
            if p.employee_id == employee_id:
                return p
        raise KeyError(employee_id)

    def metric(self, metric_id: str) -> Metric:
        for m in self.metrics:
            if m.metric_id == metric_id:
                return m
        raise KeyError(metric_id)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "Blueprint":
        raw = json.loads(path.read_text())
        raw["systems"] = [System(**s) for s in raw["systems"]]
        raw["people"] = [Person(**p) for p in raw["people"]]
        raw["metrics"] = [Metric(**m) for m in raw["metrics"]]
        raw["beliefs"] = [Belief(**b) for b in raw["beliefs"]]
        return cls(**raw)


def default_blueprint() -> Blueprint:
    """The default simulated company: NorthPeak Components.

    A two-plant brake-component manufacturer with an ERP and a shop-floor
    execution system, running three shifts. Second-shift supervision at
    Plant 2 tracks downtime differently from operations and finance —
    exactly the pattern from the Google Cloud data-simulator article.
    """
    return Blueprint(
        company_name="NorthPeak Components",
        pattern_prompt=(
            "Simulate a two-plant brake manufacturer with an ERP, a shop-floor "
            "execution system, and a three-shift schedule where second-shift "
            "supervisors track downtime differently from operations and finance."
        ),
        quarter_label="Q3 2026",
        period_start="2026-07-01",
        period_end="2026-09-30",
        plants=[
            {"plant_id": "PLANT-1", "name": "Plant 1", "city": "Detroit", "legacy_code": "P1"},
            {"plant_id": "PLANT-2", "name": "Plant 2", "city": "Toledo", "legacy_code": "P2"},
        ],
        shifts=[
            {"shift": 1, "label": "Shift 1 (06:00-14:00)"},
            {"shift": 2, "label": "Shift 2 (14:00-22:00)"},
            {"shift": 3, "label": "Shift 3 (22:00-06:00)"},
        ],
        systems=[
            System(
                system_id="erp",
                name="NorthPeak ERP",
                description="Orders, inventory, and production-run records of record.",
            ),
            System(
                system_id="mes",
                name="ShopFloor MES",
                description="Shop-floor execution system: downtime events and defect scans.",
            ),
        ],
        people=[
            Person(
                employee_id="E-1001",
                name="Marcus Oyelaran",
                role="Operations Manager",
                plant_id="PLANT-2",
                shift=None,
                writing_style="terse, precise, signs off with initials",
            ),
            Person(
                employee_id="E-1002",
                name="Dana Whitfield",
                role="Shift 2 Supervisor, Plant 2",
                plant_id="PLANT-2",
                shift=2,
                writing_style="confident, memo-formal, fond of bullet lists",
            ),
            Person(
                employee_id="E-1003",
                name="Priya Natarajan",
                role="Quality Lead",
                plant_id="PLANT-1",
                shift=None,
                writing_style="methodical, references procedure numbers",
            ),
            Person(
                employee_id="E-1004",
                name="Tom Becker",
                role="ERP Administrator",
                plant_id="PLANT-1",
                shift=None,
                writing_style="dry, changelog-style notes",
            ),
            Person(
                employee_id="E-1005",
                name="Rosa Delgado",
                role="Shift 1 Supervisor, Plant 2",
                plant_id="PLANT-2",
                shift=1,
                writing_style="practical, short sentences",
            ),
            Person(
                employee_id="E-1006",
                name="Kenji Mori",
                role="Shift 3 Supervisor, Plant 2",
                plant_id="PLANT-2",
                shift=3,
                writing_style="quiet, factual handover notes",
            ),
            Person(
                employee_id="E-1007",
                name="Alisha Grant",
                role="Finance Analyst",
                plant_id="PLANT-1",
                shift=None,
                writing_style="numbers-first, asks clarifying questions",
            ),
        ],
        metrics=[
            Metric(
                metric_id="downtime_hours",
                name="Downtime",
                owner="E-1001",
                official_definition=(
                    "All time a production line is not producing, including "
                    "planned changeovers (counted as planned downtime) and "
                    "unplanned stoppages."
                ),
                unit="hours",
            ),
            Metric(
                metric_id="defect_count",
                name="Defect count",
                owner="E-1003",
                official_definition=(
                    "Number of distinct defect events. Each physical defect "
                    "has one event key; scanner retries may write the same "
                    "event more than once in the raw table."
                ),
                unit="defects",
            ),
            Metric(
                metric_id="production_units",
                name="Production units",
                owner="E-1004",
                official_definition=(
                    "Units produced, normalized to individual pieces. From "
                    "2026-08-15 the ERP records production in cases of 12 "
                    "instead of pieces."
                ),
                unit="pieces",
            ),
        ],
        beliefs=[
            Belief(
                holder="E-1002",
                topic="downtime definition",
                statement=(
                    "Downtime means unplanned stoppages only. Planned "
                    "changeovers are scheduled work and do not count."
                ),
                is_correct=False,
                motive="Excluding changeovers flatters Shift 2's numbers, "
                "since Shift 2 runs the most changeovers at Plant 2.",
            ),
            Belief(
                holder="E-1005",
                topic="downtime definition",
                statement="Downtime is downtime — if the line is stopped, it counts.",
                is_correct=True,
            ),
        ],
        flaw_params={
            # Fraction of defect events whose insert is retried 1-3 extra times.
            "defect_retry_rate": 0.22,
            # Date the ERP switches production units from pieces to cases.
            "unit_change_date": "2026-08-15",
            "pieces_per_case": 12,
            # Fraction of Plant 2 production runs written with the legacy
            # plant code instead of the canonical plant_id.
            "legacy_key_rate_plant2": 0.18,
            # Fraction of downtime events with a NULL reason_code.
            "null_reason_rate": 0.10,
        },
        document_facts={
            "doc-dana-memo": {
                "author": "E-1002",
                "date": "2026-07-08",
                "kind": "memo",
                "title": "How we track downtime on second shift",
                "facts": [
                    "Dana Whitfield states downtime means unplanned stoppages only",
                    "Planned changeovers do not count as downtime (Dana's position)",
                ],
                "required_phrases": ["unplanned stoppages only", "changeovers do not count"],
            },
            "doc-correction-thread": {
                "author": "E-1001",
                "date": "2026-08-27",
                "kind": "chat",
                "title": "#plant2-ops thread: downtime definition",
                "facts": [
                    "Marcus Oyelaran is the owner of the downtime metric",
                    "Official downtime includes planned changeovers as planned downtime",
                    "Dana's memo is superseded",
                ],
                "required_phrases": [
                    "official definition",
                    "planned changeovers count",
                    "supersedes",
                ],
            },
            "doc-erp-changelog": {
                "author": "E-1004",
                "date": "2026-08-14",
                "kind": "memo",
                "title": "ERP change notice: production unit of measure",
                "facts": [
                    "From 2026-08-15 production is recorded in cases of 12 pieces",
                    "Before that date production was recorded in individual pieces",
                ],
                "required_phrases": ["2026-08-15", "cases of 12"],
            },
            "doc-quality-handbook": {
                "author": "E-1003",
                "date": "2026-06-30",
                "kind": "handbook",
                "title": "Quality handbook excerpt: defect logging (QP-114)",
                "facts": [
                    "Each defect event has a unique event key",
                    "Scanner retries can write duplicate rows for the same event key",
                    "Count distinct event keys, not raw rows",
                ],
                "required_phrases": ["event key", "duplicate rows", "distinct"],
            },
            "doc-onboarding": {
                "author": "E-1004",
                "date": "2026-07-01",
                "kind": "onboarding",
                "title": "Analyst onboarding: data systems at NorthPeak",
                "facts": [
                    "Production runs live in the ERP; downtime and defects live in the MES",
                    "Some Plant 2 production rows still carry the legacy plant code P2",
                ],
                "required_phrases": ["legacy plant code", "P2"],
            },
            "doc-handover-notes": {
                "author": "E-1006",
                "date": "2026-09-12",
                "kind": "chat",
                "title": "Shift handover notes, Plant 2",
                "facts": [
                    "Shift 2 runs the most product changeovers at Plant 2",
                ],
                "required_phrases": ["most changeovers"],
            },
        },
    )
