"""Read-only SQL enforcement: writes are rejected at every layer."""

from __future__ import annotations

import pytest

from agent_data_simulator.tools import ReadOnlyViolation, validate_read_only


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO plants VALUES ('X', 'x', 'y', 'z')",
        "UPDATE plants SET name='x'",
        "DELETE FROM plants",
        "DROP TABLE plants",
        "ALTER TABLE plants ADD COLUMN x TEXT",
        "CREATE TABLE evil (x TEXT)",
        "REPLACE INTO plants VALUES ('X','x','y','z')",
        "PRAGMA journal_mode=WAL",
        "ATTACH DATABASE 'evil.db' AS evil",
        "SELECT 1; DELETE FROM plants",
        "WITH x AS (SELECT 1) DELETE FROM plants",
        "",
    ],
)
def test_writes_rejected(sql):
    with pytest.raises(ReadOnlyViolation):
        validate_read_only(sql)


def test_select_allowed():
    assert validate_read_only("SELECT COUNT(*) FROM plants").startswith("SELECT")
    assert validate_read_only("WITH t AS (SELECT 1) SELECT * FROM t").startswith("WITH")


def test_keyword_in_string_literal_not_false_positive():
    sql = "SELECT * FROM downtime_events WHERE reason_code = 'delete later'"
    assert validate_read_only(sql)


def test_run_sql_executes_selects(tools_and_benchmark):
    tools, _ = tools_and_benchmark
    result = tools.run_sql("SELECT COUNT(*) AS n FROM plants")
    assert result["rows"] == [[2]]


def test_run_sql_rejects_writes_end_to_end(tools_and_benchmark):
    tools, _ = tools_and_benchmark
    with pytest.raises(ReadOnlyViolation):
        tools.run_sql("DELETE FROM plants WHERE plant_id='PLANT-1'")
    # Table is untouched.
    assert tools.run_sql("SELECT COUNT(*) FROM plants")["rows"] == [[2]]


def test_describe_unknown_table_errors(tools_and_benchmark):
    tools, _ = tools_and_benchmark
    with pytest.raises(ValueError):
        tools.describe_table("no_such_table; DROP TABLE plants")


def test_document_search_finds_trap_and_correction(tools_and_benchmark):
    tools, _ = tools_and_benchmark
    results = tools.search_documents("downtime definition")
    doc_ids = {d["doc_id"] for d in results}
    assert "doc-dana-memo" in doc_ids
    assert "doc-correction-thread" in doc_ids
    # Results carry author + date so the agent can judge authority/recency.
    for d in results:
        assert d["author"] and d["date"]
