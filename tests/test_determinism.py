"""Determinism: same blueprint + seed => identical tables and answer key."""

from __future__ import annotations

import hashlib
import sqlite3

from agent_data_simulator.estate import build_estate


def _table_dump_hash(db_path) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    h = hashlib.sha256()
    for table in tables:
        rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY 1, 2, 3').fetchall()
        h.update(table.encode())
        for row in rows:
            h.update(repr(row).encode())
    conn.close()
    return h.hexdigest()


def test_same_seed_identical_tables(tmp_path):
    a = build_estate(tmp_path / "a", seed=42)
    b = build_estate(tmp_path / "b", seed=42)
    assert _table_dump_hash(a["dir"] / "estate.db") == _table_dump_hash(b["dir"] / "estate.db")


def test_same_seed_identical_answer_key(tmp_path):
    a = build_estate(tmp_path / "a", seed=42)
    b = build_estate(tmp_path / "b", seed=42)
    gold_a = {q["qid"]: q["gold_value"] for q in a["benchmark"]["questions"]}
    gold_b = {q["qid"]: q["gold_value"] for q in b["benchmark"]["questions"]}
    assert gold_a == gold_b
    assert a["benchmark"]["dropped"] == b["benchmark"]["dropped"]


def test_different_seed_different_tables(tmp_path):
    a = build_estate(tmp_path / "a", seed=42)
    b = build_estate(tmp_path / "b", seed=7)
    assert _table_dump_hash(a["dir"] / "estate.db") != _table_dump_hash(b["dir"] / "estate.db")


def test_same_seed_identical_messiness_log(tmp_path):
    a = build_estate(tmp_path / "a", seed=42)
    b = build_estate(tmp_path / "b", seed=42)
    assert a["summary"]["messiness_log"] == b["summary"]["messiness_log"]
