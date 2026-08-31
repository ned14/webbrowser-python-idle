#!/usr/bin/env python3
"""Shared fixtures for the unit suite."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def render_webvm_config():
    """server/render-webvm-config.py (hyphenated filename — import via spec).
    The single loader for the renderer module, shared by every test file that
    exercises the production functions directly (a second inline loader would
    drift)."""
    spec = importlib.util.spec_from_file_location(
        "render_webvm_config", ROOT / "server" / "render-webvm-config.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
