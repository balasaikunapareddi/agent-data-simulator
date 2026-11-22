"""Evaluation scoring: numbers, tolerance, decoys, rubrics, abstention."""

from __future__ import annotations

from agent_data_simulator.evaluate import (
    extract_numbers,
    run_evaluation,
    score_qualitative,
    score_quantitative,
    score_sentinel,
)
from agent_data_simulator.llm import MockLLM


def test_extract_numbers_handles_commas_and_decimals():
    assert extract_numbers("Plant 2 logged 1,234.5 hours in Q3") == [2.0, 1234.5, 3.0]


def _quant_question():
    return {
        "qid": "q-test",
        "kind": "quantitative",
        "text": "How many hours?",
        "answer_type": "hours",
        "trap": "test trap",
        "gold_value": 100.0,
        "decoy_values": [60.0],
    }


def test_quantitative_gold_within_tolerance_passes():
    result = {"abstained": False, "answer": "It was 100.4 hours."}
    assert score_quantitative(_quant_question(), result)["passed"]


def test_quantitative_decoy_flagged_as_trap():
    result = {"abstained": False, "answer": "It was 60.0 hours."}
    scored = score_quantitative(_quant_question(), result)
    assert not scored["passed"] and scored["fell_for_trap"]


def test_quantitative_abstention_fails():
    result = {"abstained": True, "answer": None}
    assert not score_quantitative(_quant_question(), result)["passed"]


def test_qualitative_rubric_partial_credit():
    question = {
        "rubric": [
            {"check": "a", "any_of": ["alpha"]},
            {"check": "b", "any_of": ["beta"]},
            {"check": "c", "any_of": ["gamma"]},
            {"check": "d", "any_of": ["delta"]},
        ]
    }
    result = {"abstained": False, "answer": "alpha beta gamma"}
    scored = score_qualitative(question, result)
    assert scored["score"] == 0.75 and scored["passed"]
    result = {"abstained": False, "answer": "alpha only"}
    assert not score_qualitative(question, result)["passed"]


def test_sentinel_scoring():
    q = {"kind": "sentinel"}
    assert score_sentinel(q, {"abstained": True, "answer": None})["passed"]
    assert score_sentinel(
        q, {"abstained": False, "answer": "No such metric exists in this estate."}
    )["passed"]
    assert not score_sentinel(
        q, {"abstained": False, "answer": "The ratio was 4.27."}
    )["passed"]


def test_full_evaluation_careful_beats_naive(tools_and_benchmark):
    tools, benchmark = tools_and_benchmark
    careful = run_evaluation(benchmark, tools, MockLLM("careful"), label="careful")
    naive = run_evaluation(benchmark, tools, MockLLM("naive"), label="naive")
    assert careful["pass_rate"] == 1.0
    assert careful["traps_fallen"] == 0
    assert naive["pass_rate"] < careful["pass_rate"]
    assert naive["traps_fallen"] >= 5
    assert set(careful["by_kind"]) == {"quantitative", "qualitative", "sentinel"}
