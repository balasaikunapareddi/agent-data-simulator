"""Stage 3 — Render documents.

Turns the facts fixed in the blueprint into memos and chat threads in
each person's writing style: the trap memo, the correction thread, the
ERP change notice, and the supporting docs an agent needs to find.

Template-based by default (deterministic, offline). In live mode an LLM
can re-phrase each document from the same fact spec. Either way, every
rendered document is verified against the blueprint: it must state its
assigned facts and must not introduce unrecorded numbers or authors.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .blueprint import Blueprint
from .prompts import DOCUMENT_RENDER_TEMPLATE


def _templates(bp: Blueprint) -> dict[str, str]:
    dana = bp.person("E-1002").name
    marcus = bp.person("E-1001").name
    priya = bp.person("E-1003").name
    tom = bp.person("E-1004").name
    kenji = bp.person("E-1006").name
    alisha = bp.person("E-1007").name
    change_date = bp.flaw_params["unit_change_date"]
    per_case = bp.flaw_params["pieces_per_case"]

    return {
        "doc-dana-memo": f"""MEMO — Plant 2, Second Shift
From: {dana}, Shift 2 Supervisor
Date: 2026-07-08
Subject: How we track downtime on second shift

Team,

To keep our shift reports consistent, here is how second shift logs downtime:

- Downtime means unplanned stoppages only — breakdowns, material shortages, sensor faults.
- Planned changeovers are scheduled work. Changeovers do not count as downtime.
- If the line stops for a product switch, log it under changeover time, not downtime.

This keeps the numbers honest: we should only be measured on stops we did not plan for. Please apply this on all shift reports from this week forward.

{dana}""",
        "doc-correction-thread": f"""#plant2-ops — 2026-08-27

{alisha}: Quick question — finance is seeing different downtime totals for Plant 2 depending on who runs the report. Are changeovers in or out?

{marcus}: In. Let me settle this since I own the downtime metric. The official definition of downtime is all time a line is not producing. Planned changeovers count as planned downtime. Unplanned stoppages count too. If the line is stopped, it is downtime.

{marcus}: This supersedes the July memo from second shift. {dana.split()[0]}, I know the intent was to keep shift reports clean, but operations and finance report on the official definition, so shift reports need to match it.

{dana}: Noted — we will align our reporting. For the record, second shift does run the most changeovers, so our numbers will look worse than the other shifts under this definition.

{marcus}: Understood, and that is exactly why the definition has to be the same for everyone. Changeover load is a scheduling conversation, not a reporting one. — M.O.""",
        "doc-erp-changelog": f"""ERP CHANGE NOTICE — {tom}, ERP Administrator
Date: 2026-08-14

Effective {change_date}, the ERP records production in cases of {per_case} pieces instead of individual pieces.

- production_runs.units_produced: in pieces for run_date before {change_date}; in cases on or after {change_date}.
- production_runs.unit_of_measure tells you which: 'pieces' or 'cases'.
- Multiply by {per_case} to normalize case rows back to pieces.

Historical rows are not being rewritten. Any quarter-spanning total has to normalize, or it will be wrong.""",
        "doc-quality-handbook": f"""QUALITY HANDBOOK EXCERPT — QP-114: Defect logging
Owner: {priya}, Quality Lead
Date: 2026-06-30

Every defect scan generates a unique event key (event_key) in defect_records. One physical defect = one event key.

Known issue: the handheld scanners retry on weak Wi-Fi. A retry writes duplicate rows for the same event key — same defect, extra rows, slightly later logged_at timestamps.

Rule: defect counts must count distinct event keys, never raw rows. COUNT(*) on defect_records overstates defects wherever retries happened.""",
        "doc-onboarding": f"""ANALYST ONBOARDING — Data systems at NorthPeak
From: {tom}
Date: 2026-07-01

Where things live:
- Production runs and orders: the ERP (production_runs, orders).
- Downtime events and defect scans: the shop-floor MES (downtime_events, defect_records).
- Metric definitions: owned by operations/quality leads — ask before you assume. Definitions have changed before; check for corrections, not just the first memo you find.

Known data quirks:
- Some Plant 2 production rows still carry the legacy plant code P2 instead of the canonical PLANT-2 (a leftover from the old ERP plant table). Include both codes when you query Plant 2 production, or you will undercount.
- The defect table can contain duplicate rows from scanner retries (see QP-114).""",
        "doc-handover-notes": f"""Shift handover notes — Plant 2
{kenji}, Shift 3 Supervisor
2026-09-12

Quiet night. Line L1 ran clean after the sensor swap.

One recurring note for whoever reads the downtime reports: Shift 2 runs the most changeovers at Plant 2 — product switches on most nights, sometimes two per shift. Their stopped-line time is mostly planned. Shifts 1 and 3 stop less often but for longer when something breaks.""",
    }


def _allowed_numbers(bp: Blueprint) -> set[str]:
    """Numbers a document may legitimately contain: anything in the blueprint."""
    blob = json.dumps(bp.to_dict())
    nums = set(re.findall(r"\d+", blob))
    # Structural numbers used in prose (shifts, plants, procedure refs).
    nums |= {"1", "2", "3", "114"}
    return nums


def verify_document(doc: dict, bp: Blueprint) -> list[str]:
    """Return a list of verification problems (empty = verified)."""
    problems: list[str] = []
    spec = bp.document_facts.get(doc["doc_id"], {})
    body_lower = doc["body"].lower()

    for phrase in spec.get("required_phrases", []):
        if phrase.lower() not in body_lower:
            problems.append(f"missing required phrase: {phrase!r}")

    allowed = _allowed_numbers(bp)
    for num in re.findall(r"\d+", doc["body"]):
        if num not in allowed:
            problems.append(f"unrecorded number in document: {num}")

    author_ids = {p.employee_id for p in bp.people}
    if doc["author_id"] not in author_ids:
        problems.append(f"author {doc['author_id']} is not a blueprint person")

    return problems


def render_documents(bp: Blueprint, llm_client=None) -> list[dict]:
    """Render all blueprint documents. Verifies each one; raises on failure.

    If llm_client is given (live mode), each document is re-phrased by the
    model from its fact spec and verified; on any verification failure the
    deterministic template is used instead.
    """
    templates = _templates(bp)
    docs: list[dict] = []

    for doc_id, spec in bp.document_facts.items():
        author = bp.person(spec["author"])
        body = templates[doc_id]
        rendered_by = "template"

        if llm_client is not None:
            try:
                candidate = _render_with_llm(bp, doc_id, spec, author, llm_client)
                probe = {
                    "doc_id": doc_id,
                    "author_id": author.employee_id,
                    "body": candidate,
                }
                if not verify_document(probe, bp):
                    body = candidate
                    rendered_by = "llm"
            except Exception:
                pass  # fall back to the template

        doc = {
            "doc_id": doc_id,
            "title": spec["title"],
            "kind": spec["kind"],
            "author": author.name,
            "author_id": author.employee_id,
            "date": spec["date"],
            "body": body,
            "rendered_by": rendered_by,
        }
        problems = verify_document(doc, bp)
        if problems:
            raise ValueError(f"Document {doc_id} failed verification: {problems}")
        docs.append(doc)

    docs.sort(key=lambda d: (d["date"], d["doc_id"]))
    return docs


def _render_with_llm(bp: Blueprint, doc_id: str, spec: dict, author, llm_client) -> str:
    facts = "\n".join(f"- {f}" for f in spec["facts"])
    phrases = ", ".join(repr(p) for p in spec["required_phrases"])
    prompt = DOCUMENT_RENDER_TEMPLATE.format(
        kind=spec["kind"],
        author_name=author.name,
        author_role=author.role,
        author_style=author.writing_style,
        title=spec["title"],
        date=spec["date"],
        facts=facts,
        phrases=phrases,
    )
    return llm_client.complete(prompt).strip()


def save_documents(docs: list[dict], path: Path) -> None:
    path.write_text(json.dumps(docs, indent=2) + "\n")


def load_documents(path: Path) -> list[dict]:
    return json.loads(path.read_text())
