"""LLM layer for the data agent.

Two implementations behind one interface:

* ``MockLLM`` — a deterministic, rule-based stand-in so the full
  pipeline and evaluation run offline with no API key. It is a *fake
  model*, not a fake agent: the LangGraph loop, tool calls, SQL
  execution, and scoring around it are all real. Two variants:
  ``careful`` (checks metric ownership and later corrections before
  writing SQL, abstains on invented metrics) and ``naive`` (trusts the
  most formal-looking document, writes the obvious SQL, hallucinates
  sentinel answers) — the pair exists to prove the benchmark can tell
  them apart.

* ``GeminiLLM`` — live mode via the google-genai SDK. The API key is
  read only from the GEMINI_API_KEY environment variable; it is never
  hardcoded, printed, or logged.

An LLM decision is a dict like::

    {"action": "search_documents", "query": "downtime definition"}
    {"action": "run_sql", "sql": "SELECT ..."}
    {"action": "answer", "text": "..."}
    {"action": "abstain", "reason": "..."}
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field


@dataclass
class AgentContext:
    system_prompt: str
    question: str
    tool_specs: list[dict]
    observations: list[dict] = field(default_factory=list)  # {tool, args, result}


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------

_KNOWN_INTENTS = [
    ("downtime_hours", ["downtime", "hours"]),
    ("unplanned_hours", ["unplanned", "stoppage"]),
    ("which_shift", ["which shift", "downtime"]),
    ("defect_count", ["defect"]),
    ("units_produced", ["units", "produce"]),
    ("runs_count", ["production runs"]),
    ("onboarding", ["new analyst", "should they know"]),
    ("owner", ["who owns"]),
]


def _classify(question: str) -> str:
    q = question.lower()
    if "unplanned" in q and "stoppage" in q:
        return "unplanned_hours"
    if "which shift" in q:
        return "which_shift"
    if "new analyst" in q or "should they know" in q:
        return "onboarding"
    if "who owns" in q:
        return "owner"
    if "defect" in q:
        return "defect_count"
    if "production runs" in q:
        return "runs_count"
    if "units" in q and "produc" in q:
        return "units_produced"
    if "downtime" in q:
        return "downtime_hours"
    return "unknown_metric"


def _plant_of(question: str) -> str:
    m = re.search(r"plant\s*([12])", question.lower())
    return f"PLANT-{m.group(1)}" if m else "PLANT-2"


def _period_of(question: str) -> tuple[str, str]:
    q = question.lower()
    if "august" in q:
        return ("2026-08-01", "2026-08-31")
    return ("2026-07-01", "2026-09-30")  # Q3 2026


class MockLLM:
    """Deterministic fake model. `variant` is 'careful' or 'naive'."""

    def __init__(self, variant: str = "careful"):
        if variant not in {"careful", "naive"}:
            raise ValueError(variant)
        self.variant = variant
        self.mode = "mock"

    # -- helpers ----------------------------------------------------------
    def _obs(self, ctx: AgentContext, tool: str) -> list[dict]:
        return [o for o in ctx.observations if o["tool"] == tool]

    def _docs(self, ctx: AgentContext) -> list[dict]:
        docs: list[dict] = []
        for o in self._obs(ctx, "search_documents"):
            if isinstance(o["result"], list):
                docs.extend(o["result"])
        return docs

    def _docs_text(self, ctx: AgentContext) -> str:
        return "\n".join(d["body"] for d in self._docs(ctx)).lower()

    def _definition_says_exclude_changeovers(self, ctx: AgentContext) -> bool:
        """Doc-driven definition check — the heart of the downtime trap."""
        docs = self._docs(ctx)
        if self.variant == "naive":
            # Trusts formal documents over chat threads, stops at the
            # first definition it finds.
            formal = [d for d in docs if d["kind"] in {"memo", "handbook", "onboarding"}]
            for d in formal or docs:
                body = d["body"].lower()
                if "unplanned stoppages only" in body:
                    return True
                if "planned changeovers count" in body:
                    return False
            return False
        # Careful: reads everything, looks for the official definition
        # and later corrections before trusting any single document.
        text = self._docs_text(ctx)
        if "official definition" in text and "planned changeovers count" in text:
            return False
        return "unplanned stoppages only" in text

    def _legacy_code(self, ctx: AgentContext, plant: str) -> str | None:
        m = re.search(r"legacy plant code (\w+)", self._docs_text(ctx))
        if m and plant == "PLANT-2":
            return m.group(1).upper()
        return None

    def _plant_filter(self, ctx: AgentContext, plant: str) -> str:
        if self.variant == "careful":
            legacy = self._legacy_code(ctx, plant)
            if legacy:
                return f"plant_id IN ('{plant}','{legacy}')"
        return f"plant_id='{plant}'"

    def _sql_for(self, ctx: AgentContext, intent: str) -> str:
        q = ctx.question
        plant = _plant_of(q)
        start, end = _period_of(q)
        text = self._docs_text(ctx)
        exclude = self._definition_says_exclude_changeovers(ctx)
        co_filter = " AND event_type != 'planned_changeover'" if exclude else ""

        if intent == "downtime_hours":
            return (
                "SELECT ROUND(SUM(duration_minutes)/60.0, 2) FROM downtime_events "
                f"WHERE plant_id='{plant}' AND event_date BETWEEN '{start}' AND '{end}'{co_filter}"
            )
        if intent == "unplanned_hours":
            return (
                "SELECT ROUND(SUM(duration_minutes)/60.0, 2) FROM downtime_events "
                f"WHERE plant_id='{plant}' AND event_type='unplanned_stoppage' "
                f"AND event_date BETWEEN '{start}' AND '{end}'"
            )
        if intent == "which_shift":
            return (
                "SELECT shift FROM downtime_events "
                f"WHERE plant_id='{plant}' AND event_date BETWEEN '{start}' AND '{end}'{co_filter} "
                "GROUP BY shift ORDER BY SUM(duration_minutes) DESC LIMIT 1"
            )
        if intent == "defect_count":
            agg = "COUNT(*)"
            if self.variant == "careful" and "distinct" in text and "event key" in text:
                agg = "COUNT(DISTINCT event_key)"
            return (
                f"SELECT {agg} FROM defect_records WHERE plant_id='{plant}' "
                f"AND logged_at >= '{start}' AND logged_at < '2026-10-01'"
            )
        if intent == "units_produced":
            expr = "SUM(units_produced)"
            if self.variant == "careful" and "cases of 12" in text:
                expr = (
                    "SUM(CASE WHEN unit_of_measure='cases' THEN units_produced*12 "
                    "ELSE units_produced END)"
                )
            return (
                f"SELECT {expr} FROM production_runs WHERE {self._plant_filter(ctx, plant)} "
                f"AND run_date BETWEEN '{start}' AND '{end}'"
            )
        if intent == "runs_count":
            return (
                "SELECT COUNT(*) FROM production_runs "
                f"WHERE {self._plant_filter(ctx, plant)} AND run_date BETWEEN '{start}' AND '{end}'"
            )
        raise ValueError(intent)

    def _table_for(self, intent: str) -> str:
        return {
            "downtime_hours": "downtime_events",
            "unplanned_hours": "downtime_events",
            "which_shift": "downtime_events",
            "defect_count": "defect_records",
            "units_produced": "production_runs",
            "runs_count": "production_runs",
        }[intent]

    def _search_query(self, intent: str, question: str) -> str:
        return {
            "downtime_hours": "downtime definition owner correction",
            "unplanned_hours": "downtime definition",
            "which_shift": "downtime definition changeovers",
            "defect_count": "defect event key duplicate rows",
            "units_produced": "production units cases plant code",
            "runs_count": "production plant code legacy",
            "onboarding": "downtime definition owner correction",
            "owner": "downtime metric owner official definition",
        }.get(intent, question)

    # -- main decision ------------------------------------------------------
    def decide(self, ctx: AgentContext) -> dict:
        intent = _classify(ctx.question)
        searched = bool(self._obs(ctx, "search_documents"))

        if intent == "unknown_metric":
            if not searched:
                return {"action": "search_documents", "query": ctx.question}
            if not self._obs(ctx, "list_tables"):
                return {"action": "list_tables"}
            if self.variant == "naive":
                # Hallucinates a plausible-sounding number.
                return {
                    "action": "answer",
                    "text": "Based on the available data, the value for Q3 2026 was 4.27.",
                }
            return {
                "action": "abstain",
                "reason": (
                    "No such metric appears in the company documents or the "
                    "database schema, so I cannot answer this."
                ),
            }

        if intent in {"onboarding", "owner"}:
            if not searched:
                return {"action": "search_documents", "query": self._search_query(intent, ctx.question)}
            return {"action": "answer", "text": self._qualitative_answer(ctx, intent)}

        # Quantitative path: docs -> schema -> SQL -> answer.
        if not searched:
            return {"action": "search_documents", "query": self._search_query(intent, ctx.question)}
        table = self._table_for(intent)
        described = [o for o in self._obs(ctx, "describe_table") if o["args"].get("table") == table]
        if not described:
            return {"action": "describe_table", "table": table}
        sql_obs = self._obs(ctx, "run_sql")
        if not sql_obs:
            return {"action": "run_sql", "sql": self._sql_for(ctx, intent)}
        last = sql_obs[-1]["result"]
        if isinstance(last, dict) and "error" in last:
            if len(sql_obs) >= 2:
                return {"action": "abstain", "reason": f"SQL failed: {last['error']}"}
            return {"action": "run_sql", "sql": self._sql_for(ctx, intent)}
        return {"action": "answer", "text": self._quantitative_answer(ctx, intent, last)}

    def _quantitative_answer(self, ctx: AgentContext, intent: str, result: dict) -> str:
        rows = result.get("rows") or []
        value = rows[0][0] if rows and rows[0] else None
        plant = _plant_of(ctx.question).replace("PLANT-", "Plant ")
        if intent == "which_shift":
            return f"Shift {value} logged the most downtime at {plant} in Q3 2026."
        if intent == "defect_count":
            return f"{plant} logged {value} defects in Q3 2026."
        if intent == "units_produced":
            return f"{plant} produced {value} units in Q3 2026."
        if intent == "runs_count":
            return f"{plant} completed {value} production runs in Q3 2026."
        if intent == "unplanned_hours":
            return f"{plant} logged {value} hours of unplanned stoppage time in August 2026."
        return f"{plant} logged {value} hours of downtime in Q3 2026."

    def _qualitative_answer(self, ctx: AgentContext, intent: str) -> str:
        if self.variant == "naive":
            if intent == "owner":
                return (
                    "Downtime at NorthPeak is defined by Dana Whitfield, the Plant 2 "
                    "second-shift supervisor: it means unplanned stoppages only, and "
                    "planned changeovers do not count."
                )
            return (
                "Downtime here means unplanned stoppages only — breakdowns and "
                "shortages. Planned changeovers are scheduled work and do not count "
                "as downtime, per the second-shift supervisor's memo. Log changeovers "
                "separately from downtime."
            )
        if intent == "owner":
            return (
                "The downtime metric is owned by Marcus Oyelaran (Operations Manager). "
                "The official definition: all time a line is not producing — planned "
                "changeovers count as planned downtime, and unplanned stoppages count too."
            )
        return (
            "Three things matter. First, the official definition of downtime covers "
            "all time a line is not producing: planned changeovers count as planned "
            "downtime, not just unplanned stoppages. Second, the metric owner is "
            "Marcus Oyelaran, who corrected the record in an August thread, "
            "superseding the July memo from Dana Whitfield that counted unplanned "
            "stoppages only. Third, second shift at Plant 2 historically tracked it "
            "Dana's way and runs the most changeovers, so expect their numbers to "
            "move the most under the official definition."
        )

    def complete(self, prompt: str) -> str:
        # Mock mode never renders documents with the LLM; templates are used.
        raise RuntimeError("MockLLM.complete is not used; templates render documents")


# ---------------------------------------------------------------------------
# Live LLM (Gemini)
# ---------------------------------------------------------------------------


class GeminiLLM:
    """Live model via google-genai. Key read only from GEMINI_API_KEY env."""

    def __init__(self, model: str | None = None):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY environment variable is not set. "
                "Set it to run in live mode, or use mock mode (default)."
            )
        from google import genai  # imported lazily so mock mode needs no SDK

        self._client = genai.Client(api_key=api_key)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.mode = "live"

    def complete(self, prompt: str) -> str:
        response = self._client.models.generate_content(model=self.model, contents=prompt)
        return response.text or ""

    def decide(self, ctx: AgentContext) -> dict:
        prompt = self._render_prompt(ctx)
        for _ in range(2):
            raw = self.complete(prompt)
            parsed = _parse_action_json(raw)
            if parsed is not None:
                return parsed
            prompt += "\n\nYour previous reply was not valid JSON. Reply with ONE JSON object only."
        return {"action": "abstain", "reason": "model did not return a valid action"}

    def _render_prompt(self, ctx: AgentContext) -> str:
        tools = "\n".join(
            f"- {t['name']}({', '.join(t['parameters']) or 'no args'}): {t['description']}"
            for t in ctx.tool_specs
        )
        obs_lines = []
        for o in ctx.observations:
            result = json.dumps(o["result"], default=str)
            if len(result) > 4000:
                result = result[:4000] + "…[truncated]"
            obs_lines.append(f"Tool {o['tool']} args={o['args']} -> {result}")
        observations = "\n".join(obs_lines) if obs_lines else "(none yet)"
        return (
            f"{ctx.system_prompt}\n\n"
            f"Available tools:\n{tools}\n\n"
            f"Question: {ctx.question}\n\n"
            f"Observations so far:\n{observations}\n\n"
            "Reply with exactly ONE JSON object for your next step, one of:\n"
            '{"action": "list_tables"}\n'
            '{"action": "describe_table", "table": "<name>"}\n'
            '{"action": "search_documents", "query": "<keywords>"}\n'
            '{"action": "run_sql", "sql": "<single SELECT>"}\n'
            '{"action": "answer", "text": "<final answer>"}\n'
            '{"action": "abstain", "reason": "<why you cannot answer>"}\n'
            "No other text."
        )


def _parse_action_json(raw: str) -> dict | None:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "action" not in obj:
        return None
    return obj


def make_llm(live: bool = False, variant: str = "careful"):
    """Factory: mock by default, Gemini when live=True."""
    if live:
        return GeminiLLM()
    return MockLLM(variant=variant)
