"""Agent behavior in mock mode: the careful agent passes, the naive one traps."""

from __future__ import annotations

from agent_data_simulator.agent import run_agent
from agent_data_simulator.evaluate import extract_numbers, score_result
from agent_data_simulator.llm import MockLLM


def _question(benchmark, qid):
    return next(q for q in benchmark["questions"] if q["qid"] == qid)


def test_careful_agent_gets_downtime_right(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    q = _question(benchmark, "q-downtime-plant2-q3")
    result = run_agent(q["text"], tools, MockLLM("careful"))
    scored = score_result(q, result)
    assert scored["passed"], scored
    # It searched documents before running SQL.
    actions = [t["action"] for t in result["trace"]]
    assert actions.index("search_documents") < actions.index("run_sql")


def test_naive_agent_falls_for_downtime_trap(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    q = _question(benchmark, "q-downtime-plant2-q3")
    result = run_agent(q["text"], tools, MockLLM("naive"))
    scored = score_result(q, result)
    assert not scored["passed"]
    assert scored.get("fell_for_trap") is True
    assert any(
        abs(n - float(q["decoy_values"][0])) < 0.01 for n in extract_numbers(result["answer"])
    )


def test_careful_agent_abstains_on_sentinels(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    for qid in ("q-sentinel-basalt", "q-sentinel-quantum"):
        q = _question(benchmark, qid)
        result = run_agent(q["text"], tools, MockLLM("careful"))
        assert result["abstained"], qid
        assert score_result(q, result)["passed"]


def test_naive_agent_hallucinates_sentinels(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    q = _question(benchmark, "q-sentinel-basalt")
    result = run_agent(q["text"], tools, MockLLM("naive"))
    assert not result["abstained"]
    assert not score_result(q, result)["passed"]


def test_careful_agent_deduplicates_defects(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    q = _question(benchmark, "q-defects-plant1-q3")
    result = run_agent(q["text"], tools, MockLLM("careful"))
    assert score_result(q, result)["passed"]
    sqls = [t["sql"] for t in result["trace"] if t["action"] == "run_sql"]
    assert any("COUNT(DISTINCT event_key)" in s for s in sqls)


def test_careful_agent_normalizes_units(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    q = _question(benchmark, "q-units-plant2-q3")
    result = run_agent(q["text"], tools, MockLLM("careful"))
    assert score_result(q, result)["passed"]
    sqls = [t["sql"] for t in result["trace"] if t["action"] == "run_sql"]
    assert any("unit_of_measure='cases'" in s for s in sqls)
    assert any("'P2'" in s for s in sqls)  # legacy key included


def test_agent_never_sees_blueprint_or_answers(tools_and_benchmark):
    """The agent's only window is the read-only tools."""
    tools, benchmark = tools_and_benchmark
    assert set(tools.list_tables()) == {
        "plants", "employees", "production_runs",
        "downtime_events", "defect_records", "orders",
    }
