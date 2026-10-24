"""Make the HA-free library importable as top-level package ``qlc``."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "custom_components" / "qlcplus"))
sys.path.insert(0, str(ROOT / "tests"))

DATA = ROOT / "tests" / "data"


@pytest.fixture
def data_dir() -> Path:
    return DATA
