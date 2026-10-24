"""Make the HA-free library importable as top-level package ``qlc``."""

import contextlib
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


@pytest.fixture(autouse=True)
def _allow_local_sockets(request):
    """The HA test plugin blocks sockets; the fake QLC+ server needs localhost."""
    if "socket_enabled" in request.fixturenames:
        return
    with contextlib.suppress(pytest.FixtureLookupError):
        request.getfixturevalue("socket_enabled")
