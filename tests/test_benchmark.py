"""Benchmark integrity: gold vs decoy divergence, drop logic, sentinels."""

from __future__ import annotations

from agent_data_simulator.benchmark import Question, build_benchmark
from agent_data_simulator.blueprint import default_blueprint
from agent_data_simulator.documents import load_documents


def test_every_quantitative_question_discriminates(tools_and_benchmark):
    _, benchmark = tools_and_benchmark
    quant = [q for q in benchmark["questions"] if q["kind"] == "quantitative"]
    assert len(quant) >= 5
    for q in quant:
        assert q["gold_value"] is not None
        assert q["decoy_values"], f"{q['qid']} has no decoys"
        for decoy in q["decoy_values"]:
            if q["answer_type"] == "hours":
                assert abs(float(q["gold_value"]) - float(decoy)) > 0.5, q["qid"]
            else:
                assert str(q["gold_value"]) != str(decoy), q["qid"]


def test_unverifiable_question_is_dropped(estate_dir):
    """A question whose decoy equals gold must be dropped, not shipped."""
    bp = default_blueprint()
    docs = load_documents(estate_dir / "documents.json")
    rigged = Question(
        qid="q-rigged",
        kind="quantitative",
        text="How many plants are there?",
        answer_type="count",
        gold_sql="SELECT COUNT(*) FROM plants",
        decoy_sql=["SELECT COUNT(*) FROM plants"],  # identical -> cannot discriminate
    )
    benchmark = build_benchmark(bp, estate_dir / "estate.db", docs, candidates=[rigged])
    assert benchmark["questions"] == []
    assert benchmark["dropped"][0]["qid"] == "q-rigged"


def test_sentinel_metrics_absent_from_estate(estate_dir, tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    sentinels = [q for q in benchmark["questions"] if q["kind"] == "sentinel"]
    assert len(sentinels) == 2
    for term in ("basalt", "quantum"):
        assert tools.search_documents(term) == []
    schema_text = " ".join(tools.list_tables())
    assert "basalt" not in schema_text and "quantum" not in schema_text


def test_qualitative_rubrics_grounded_in_blueprint(tools_and_benchmark):
    _, benchmark = tools_and_benchmark
    bp = default_blueprint()
    owner = bp.person(bp.metric("downtime_hours").owner)
    qual = [q for q in benchmark["questions"] if q["kind"] == "qualitative"]
    assert len(qual) == 2
    for q in qual:
        checks = " ".join(item["check"] for item in q["rubric"])
        assert owner.name in checks
        assert len(q["rubric"]) >= 2


def test_benchmark_has_all_three_kinds(tools_and_benchmark):
    _, benchmark = tools_and_benchmark
    kinds = {q["kind"] for q in benchmark["questions"]}
    assert kinds == {"quantitative", "qualitative", "sentinel"}
