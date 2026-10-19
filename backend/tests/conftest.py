from __future__ import annotations

from pathlib import Path

import pytest

from faultline.ingest.pipeline import Analysis, analyse_path

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture(scope="session")
def fixture_analysis() -> Analysis:
    """The whole sample project, parsed and resolved once per test session."""
    return analyse_path(FIXTURE_ROOT, progress_every=0)
