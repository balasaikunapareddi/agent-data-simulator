"""Evaluation — score agent answers against the verified answer key.

Quantitative answers are checked numerically against the gold value,
and also against every decoy value so the report can say *which* trap
an agent fell for. Qualitative answers are scored against the rubric.
Sentinels pass only on abstention (or an explicit "no such metric"
with no invented number).
"""

from __future__ import annotations

import re

from .agent import run_agent
from .tools import EstateTools

_NOT_EXIST_PHRASES = [
    "no such", "does not exist", "doesn't exist", "not found",
    "cannot find", "can't find", "no metric", "not appear",
]


def extract_numbers(text: str) -> list[float]:
    cleaned = text.replace(",", "")
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", cleaned)]


def _number_matches(value: float, target, answer_type: str) -> bool:
    if target is None:
        return False
    target = float(target)
    if answer_type == "hours":
        return abs(value - target) <= max(0.01 * abs(target), 0.5)
    return value == target  # counts: exact


def score_quantitative(question: dict, result: dict) -> dict:
    if result["abstained"]:
        return {"passed": False, "detail": "agent abstained on an answerable question"}
    answer = result["answer"] or ""
    answer_type = question["answer_type"]

    if answer_type == "category":
        m = re.search(r"shift\s*(\d)", answer.lower())
        got = m.group(1) if m else None
        gold = str(question["gold_value"])
        decoys = [str(d) for d in question["decoy_values"]]
        if got == gold:
            return {"passed": True, "detail": f"answered Shift {got} (gold)"}
        if got in decoys:
            return {
                "passed": False,
                "detail": f"answered Shift {got} — matches decoy (trap: {question['trap']})",
                "fell_for_trap": True,
            }
        return {"passed": False, "detail": f"answered Shift {got}, gold is Shift {gold}"}

    numbers = extract_numbers(answer)
    gold_hit = any(_number_matches(n, question["gold_value"], answer_type) for n in numbers)
    if gold_hit:
        return {"passed": True, "detail": f"gold value {question['gold_value']} found in answer"}
    for decoy in question["decoy_values"]:
        if any(_number_matches(n, decoy, answer_type) for n in numbers):
            return {
                "passed": False,
                "detail": f"answer matches decoy value {decoy} (trap: {question['trap']})",
                "fell_for_trap": True,
            }
    return {
        "passed": False,
        "detail": f"no gold value {question['gold_value']} in answer numbers {numbers}",
    }


def score_qualitative(question: dict, result: dict) -> dict:
    if result["abstained"]:
        return {"passed": False, "detail": "agent abstained on an answerable question", "score": 0.0}
    answer = (result["answer"] or "").lower()
    items = []
    for item in question["rubric"]:
        hit = any(kw.lower() in answer for kw in item["any_of"])
        items.append({"check": item["check"], "passed": hit})
    score = sum(i["passed"] for i in items) / len(items) if items else 0.0
    return {
        "passed": score >= 0.75,
        "score": round(score, 2),
        "detail": f"rubric {sum(i['passed'] for i in items)}/{len(items)}",
        "rubric_items": items,
    }


def score_sentinel(question: dict, result: dict) -> dict:
    if result["abstained"]:
        return {"passed": True, "detail": "correctly abstained on invented metric"}
    answer = (result["answer"] or "").lower()
    has_number = bool(extract_numbers(answer))
    says_missing = any(p in answer for p in _NOT_EXIST_PHRASES)
    if says_missing and not has_number:
        return {"passed": True, "detail": "reported the metric does not exist"}
    return {
        "passed": False,
        "detail": "hallucinated an answer for an invented metric",
        "fell_for_trap": True,
    }


def score_result(question: dict, result: dict) -> dict:
    if question["kind"] == "quantitative":
        scored = score_quantitative(question, result)
    elif question["kind"] == "qualitative":
        scored = score_qualitative(question, result)
    else:
        scored = score_sentinel(question, result)
    return {
        "qid": question["qid"],
        "kind": question["kind"],
        "question": question["text"],
        "answer": result["answer"],
        "abstained": result["abstained"],
        **scored,
    }


def run_evaluation(benchmark: dict, tools: EstateTools, llm, label: str = "") -> dict:
    results = []
    for question in benchmark["questions"]:
        result = run_agent(question["text"], tools, llm)
        results.append(score_result(question, result))

    total = len(results)
    passed = sum(r["passed"] for r in results)
    by_kind: dict[str, dict] = {}
    for r in results:
        bucket = by_kind.setdefault(r["kind"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(r["passed"])

    return {
        "label": label,
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "by_kind": by_kind,
        "traps_fallen": sum(1 for r in results if r.get("fell_for_trap")),
        "results": results,
    }


def format_report(report: dict) -> str:
    lines = []
    title = f"Evaluation report{f' — {report['label']}' if report['label'] else ''}"
    lines.append(title)
    lines.append("=" * len(title))
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        lines.append(f"[{mark}] ({r['kind']}) {r['question']}")
        lines.append(f"       -> {r['detail']}")
        if r["answer"]:
            lines.append(f"       answer: {r['answer']}")
        elif r["abstained"]:
            lines.append("       answer: (abstained)")
    lines.append("")
    lines.append(
        f"Score: {report['passed']}/{report['total']} "
        f"({report['pass_rate']:.0%})  traps fallen for: {report['traps_fallen']}"
    )
    for kind, bucket in sorted(report["by_kind"].items()):
        lines.append(f"  {kind}: {bucket['passed']}/{bucket['total']}")
    return "\n".join(lines)
