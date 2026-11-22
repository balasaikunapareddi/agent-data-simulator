"""Stage 4 — Evaluate agent (the agent under test).

A LangGraph state machine: plan -> act (typed read-only tools) ->
observe -> plan ... -> answer or abstain. The agent only ever sees the
estate through EstateTools (schema browsing, document search, SQL
execution). It never sees the blueprint or the answer key.

The system prompt encodes the judgment the benchmark tests: check who
owns a metric and look for later corrections before writing SQL, and
abstain on metrics that do not exist.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from .llm import AgentContext
from .tools import EstateTools

SYSTEM_PROMPT = """You are a data agent answering questions about a company's data estate.
You can browse the database schema, search company documents, and run read-only SQL.

Rules you must follow:
1. Before writing SQL for any metric, search the documents for its definition.
   Check WHO OWNS the metric — a confident memo from someone who does not own
   the metric is not authority. Look for LATER CORRECTIONS: definitions get
   superseded in follow-up threads, and the newest statement from the metric
   owner wins over an older memo.
2. Enterprise data is messy. Before trusting a table, check for duplicate
   rows (retries), unit-of-measure changes over time, and inconsistent keys
   for the same entity. Write SQL that accounts for what you find.
3. If a question asks about a metric or entity that does not appear in the
   documents or the schema, abstain and say so. Never invent a number.
4. Answer with the value you computed and a one-line explanation of how.
   If you cannot verify an answer, abstain instead of guessing.
"""

TOOL_ACTIONS = {"list_tables", "describe_table", "search_documents", "run_sql"}


class AgentState(TypedDict):
    question: str
    observations: list[dict]
    pending: dict | None
    steps: int
    final: dict | None
    trace: list[dict]


def _execute(tools: EstateTools, action: dict) -> dict:
    name = action.get("action")
    try:
        if name == "list_tables":
            return tools.list_tables()
        if name == "describe_table":
            return tools.describe_table(action["table"])
        if name == "search_documents":
            return tools.search_documents(action["query"])
        if name == "run_sql":
            return tools.run_sql(action["sql"])
        return {"error": f"unknown action: {name}"}
    except Exception as exc:  # tool errors become observations, not crashes
        return {"error": f"{type(exc).__name__}: {exc}"}


def build_agent_graph(tools: EstateTools, llm, max_steps: int = 12):
    tool_specs = tools.tool_specs()

    def plan(state: AgentState) -> AgentState:
        ctx = AgentContext(
            system_prompt=SYSTEM_PROMPT,
            question=state["question"],
            tool_specs=tool_specs,
            observations=state["observations"],
        )
        action = llm.decide(ctx)
        return {
            **state,
            "pending": action,
            "steps": state["steps"] + 1,
            "trace": state["trace"] + [action],
        }

    def act(state: AgentState) -> AgentState:
        action = state["pending"] or {}
        result = _execute(tools, action)
        args = {k: v for k, v in action.items() if k != "action"}
        observation = {"tool": action.get("action"), "args": args, "result": result}
        return {**state, "observations": state["observations"] + [observation], "pending": None}

    def finalize(state: AgentState) -> AgentState:
        action = state["pending"] or {}
        if state["steps"] > max_steps and action.get("action") in TOOL_ACTIONS:
            final = {"kind": "abstain", "reason": "step limit reached without an answer"}
        elif action.get("action") == "answer":
            final = {"kind": "answer", "text": action.get("text", "")}
        elif action.get("action") == "abstain":
            final = {"kind": "abstain", "reason": action.get("reason", "")}
        else:
            final = {"kind": "abstain", "reason": f"unexpected final action: {action}"}
        return {**state, "final": final}

    def route(state: AgentState) -> str:
        action = (state["pending"] or {}).get("action")
        if action in TOOL_ACTIONS and state["steps"] <= max_steps:
            return "act"
        return "finalize"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan)
    graph.add_node("act", act)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", route, {"act": "act", "finalize": "finalize"})
    graph.add_edge("act", "plan")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_agent(question: str, tools: EstateTools, llm, max_steps: int = 12) -> dict:
    """Run one question through the agent. Returns answer + full trace."""
    app = build_agent_graph(tools, llm, max_steps=max_steps)
    final_state = app.invoke(
        {
            "question": question,
            "observations": [],
            "pending": None,
            "steps": 0,
            "final": None,
            "trace": [],
        }
    )
    final = final_state["final"] or {"kind": "abstain", "reason": "no final state"}
    return {
        "question": question,
        "abstained": final["kind"] == "abstain",
        "answer": final.get("text"),
        "reason": final.get("reason"),
        "trace": final_state["trace"],
        "observations": final_state["observations"],
    }
