"""Shared pytest fixtures.

Unit tests never need a database or a model. Integration tests are marked
``@integration`` and locate FalkorDB via the normal config.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Every test starts from fresh settings and no ambient overrides."""

    from noodle import config

    config.reset_cache()
    yield
    config.reset_cache()


@pytest.fixture
def repo_root() -> Path:
    return ROOT
