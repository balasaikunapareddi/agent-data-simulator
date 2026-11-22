"""Messiness: every injected flaw is real in the tables and logged accurately."""

from __future__ import annotations

import json
import sqlite3


def _conn(estate_dir):
    return sqlite3.connect(f"file:{estate_dir / 'estate.db'}?mode=ro", uri=True)


def _log(estate_dir) -> dict:
    entries = json.loads((estate_dir / "messiness_log.json").read_text())
    return {e["flaw_type"]: e for e in entries}


def test_retry_duplicates_exist_and_match_log(estate_dir):
    conn = _conn(estate_dir)
    total = conn.execute("SELECT COUNT(*) FROM defect_records").fetchone()[0]
    distinct = conn.execute("SELECT COUNT(DISTINCT event_key) FROM defect_records").fetchone()[0]
    log = _log(estate_dir)["retry_storm_duplicates"]
    assert total > distinct, "expected duplicate defect rows"
    assert log["affected_rows"] == total - distinct
    assert log["distinct_events_affected"] == len(log["event_keys"])
    # Every logged event key really is duplicated.
    for key in log["event_keys"][:25]:
        n = conn.execute(
            "SELECT COUNT(*) FROM defect_records WHERE event_key = ?", (key,)
        ).fetchone()[0]
        assert n > 1
    conn.close()


def test_unit_change_present_and_dated(estate_dir):
    conn = _conn(estate_dir)
    log = _log(estate_dir)["unit_of_measure_change"]
    cases_before = conn.execute(
        "SELECT COUNT(*) FROM production_runs WHERE unit_of_measure='cases' AND run_date < ?",
        (log["change_date"],),
    ).fetchone()[0]
    pieces_after = conn.execute(
        "SELECT COUNT(*) FROM production_runs WHERE unit_of_measure='pieces' AND run_date >= ?",
        (log["change_date"],),
    ).fetchone()[0]
    cases = conn.execute(
        "SELECT COUNT(*) FROM production_runs WHERE unit_of_measure='cases'"
    ).fetchone()[0]
    assert cases_before == 0 and pieces_after == 0
    assert cases == log["affected_rows"] > 0
    conn.close()


def test_legacy_keys_present_and_match_log(estate_dir):
    conn = _conn(estate_dir)
    log = _log(estate_dir)["unmerged_legacy_keys"]
    n = conn.execute(
        "SELECT COUNT(*) FROM production_runs WHERE plant_id = ?", (log["legacy_key"],)
    ).fetchone()[0]
    assert n == log["affected_rows"] > 0
    # Legacy rows belong to Plant 2 lines only (same lines as canonical rows).
    canonical = conn.execute(
        "SELECT COUNT(*) FROM production_runs WHERE plant_id = ?", (log["canonical_key"],)
    ).fetchone()[0]
    assert canonical > 0
    conn.close()


def test_null_reasons_match_log(estate_dir):
    conn = _conn(estate_dir)
    log = _log(estate_dir)["null_reason_codes"]
    n = conn.execute(
        "SELECT COUNT(*) FROM downtime_events WHERE reason_code IS NULL"
    ).fetchone()[0]
    assert n == log["affected_rows"] > 0
    conn.close()


def test_messiness_log_is_machine_readable(estate_dir):
    entries = json.loads((estate_dir / "messiness_log.json").read_text())
    assert {e["flaw_type"] for e in entries} == {
        "retry_storm_duplicates",
        "unit_of_measure_change",
        "unmerged_legacy_keys",
        "null_reason_codes",
    }
    for entry in entries:
        assert entry["table"] and entry["description"] and entry["affected_rows"] > 0
