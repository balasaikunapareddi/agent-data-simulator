"""Shared fixtures: build one estate per test session (it's fast)."""

from __future__ import annotations

import pytest

from agent_data_simulator.estate import build_estate, load_estate


@pytest.fixture(scope="session")
def estate_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("estate")
    build_estate(out, seed=42)
    return out


@pytest.fixture(scope="session")
def tools_and_benchmark(estate_dir):
    return load_estate(estate_dir)
