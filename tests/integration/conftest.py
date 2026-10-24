"""Fixtures for Home Assistant integration tests."""

from __future__ import annotations

import contextlib
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "data"
sys.path.insert(0, str(ROOT / "tests"))

from fake_qlc import FakeQLC  # noqa: E402

from custom_components.qlcplus.const import (  # noqa: E402
    ALL_GROUPS,
    CONF_GROUPS,
    CONF_POLL_INTERVAL,
    CONF_STORAGE_ID,
    DOMAIN,
)
from custom_components.qlcplus.qlc.project import parse_project  # noqa: E402

PROJECT = DATA / "Lichtsteuerung.qxw"
TMH = DATA / "Eurolite-LED-TMH-Bar-S120-MH-Spots.qxf"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, socket_enabled):
    yield


@pytest.fixture
def library_cache(hass: HomeAssistant) -> Path:
    """Pre-fill the fixture library cache so no network access is needed."""
    target = Path(hass.config.path(DOMAIN, "library_cache"))
    shutil.copytree(DATA / "library", target, dirs_exist_ok=True)
    return target


@pytest.fixture
async def qlc_server():
    srv = FakeQLC(ping_interval=1.0)
    project = parse_project(PROJECT.read_bytes())
    srv.functions = {f.id: (f.name, f.type.value) for f in project.functions.values()}
    srv.widgets = {w.id: (w.caption, "0") for w in project.widgets.values()}
    for f in project.functions.values():
        values = {}
        for v in f.values:
            fx = project.fixtures[v.fixture]
            values[fx.absolute_address + v.channel + 1] = v.value
        srv.function_values[f.id] = values
    await srv.start()
    yield srv
    await srv.stop()


def write_entry_files(hass: HomeAssistant, storage_id: str, with_tmh: bool = True) -> None:
    target = Path(hass.config.path(DOMAIN, storage_id))
    (target / "fixtures").mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECT, target / "project.qxw")
    if with_tmh:
        shutil.copy(TMH, target / "fixtures" / TMH.name)


@pytest.fixture
def mock_entry(hass: HomeAssistant, qlc_server, library_cache) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="QLC+ Lichtsteuerung",
        unique_id=f"{qlc_server.host}:{qlc_server.port}",
        data={CONF_HOST: qlc_server.host, CONF_PORT: qlc_server.port,
              CONF_STORAGE_ID: "abc123"},
        options={CONF_GROUPS: ALL_GROUPS, CONF_POLL_INTERVAL: 1},
    )
    write_entry_files(hass, "abc123")
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, mock_entry: MockConfigEntry) -> MockConfigEntry:
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_entry
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


@contextlib.contextmanager
def mock_uploads(files: dict[str, Path]):
    """Make process_uploaded_file() return local test files."""

    @contextlib.contextmanager
    def _process(hass, file_id):
        yield files[file_id]

    with patch("custom_components.qlcplus.config_flow.process_uploaded_file", _process):
        yield
