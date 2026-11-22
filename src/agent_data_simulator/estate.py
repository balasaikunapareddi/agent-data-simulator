"""Estate pipeline — runs the four stages end to end and loads estates back.

An estate directory contains:
    estate.db          the messy SQLite tables (agent-visible)
    documents.json     rendered memos/chat threads (agent-visible)
    benchmark.json     questions + verified answer key (NOT agent-visible)
    blueprint.json     the frozen spec (NOT agent-visible)
    messiness_log.json every injected flaw (NOT agent-visible)
"""

from __future__ import annotations

from pathlib import Path

from .benchmark import build_benchmark, load_benchmark, save_benchmark
from .blueprint import Blueprint, default_blueprint
from .documents import load_documents, render_documents, save_documents
from .simulate import build_tables, save_messiness_log
from .tools import EstateTools


def build_estate(
    out_dir: Path,
    seed: int = 42,
    blueprint: Blueprint | None = None,
    llm_client=None,
) -> dict:
    """Compile blueprint -> simulate tables -> render docs -> build benchmark."""
    bp = blueprint or default_blueprint()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bp.save(out_dir / "blueprint.json")
    summary = build_tables(bp, out_dir / "estate.db", seed=seed)
    save_messiness_log(summary, out_dir / "messiness_log.json")
    documents = render_documents(bp, llm_client=llm_client)
    save_documents(documents, out_dir / "documents.json")
    benchmark = build_benchmark(bp, out_dir / "estate.db", documents)
    save_benchmark(benchmark, out_dir / "benchmark.json")

    return {
        "dir": out_dir,
        "blueprint": bp,
        "summary": summary,
        "documents": documents,
        "benchmark": benchmark,
    }


def load_estate(estate_dir: Path) -> tuple[EstateTools, dict]:
    """Load a built estate: agent tools + benchmark."""
    estate_dir = Path(estate_dir)
    documents = load_documents(estate_dir / "documents.json")
    tools = EstateTools(estate_dir / "estate.db", documents)
    benchmark = load_benchmark(estate_dir / "benchmark.json")
    return tools, benchmark
