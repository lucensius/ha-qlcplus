"""Files of a config entry: project, uploaded fixtures, library cache."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, FIXTURES_DIR, LIBRARY_CACHE_DIR, PROJECT_FILE
from .qlc.pipeline import LoadedProject, load_project

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ +-]")


def base_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(DOMAIN))


def entry_dir(hass: HomeAssistant, storage_id: str) -> Path:
    return base_dir(hass) / storage_id


def cache_dir(hass: HomeAssistant) -> Path:
    return base_dir(hass) / LIBRARY_CACHE_DIR


def safe_filename(name: str, default: str) -> str:
    name = _SAFE_NAME.sub("_", Path(name).name).strip(". ")
    return name or default


def write_files(target: Path, project: bytes | None, fixtures: dict[str, bytes],
                replace_fixtures: bool) -> None:
    """Blocking: store project and fixture files of an entry."""
    target.mkdir(parents=True, exist_ok=True)
    fx_dir = target / FIXTURES_DIR
    if replace_fixtures and fx_dir.exists():
        shutil.rmtree(fx_dir)
    fx_dir.mkdir(exist_ok=True)
    if project is not None:
        tmp = target / (PROJECT_FILE + ".tmp")
        tmp.write_bytes(project)
        tmp.replace(target / PROJECT_FILE)
    for name, data in fixtures.items():
        (fx_dir / safe_filename(name, "fixture.qxf")).write_bytes(data)


def read_files(target: Path) -> tuple[bytes, dict[str, bytes]]:
    """Blocking: return project bytes and {filename: bytes} of fixtures."""
    project = (target / PROJECT_FILE).read_bytes()
    fx_dir = target / FIXTURES_DIR
    fixtures = {}
    if fx_dir.is_dir():
        for path in sorted(fx_dir.glob("*.qxf")):
            fixtures[path.name] = path.read_bytes()
    return project, fixtures


def remove_entry_files(target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)


def make_fetcher(hass: HomeAssistant):
    session = async_get_clientsession(hass)

    async def fetch(url: str) -> bytes:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            return await resp.read()

    return fetch


async def async_load(hass: HomeAssistant, project: bytes,
                     fixtures: dict[str, bytes]) -> LoadedProject:
    return await load_project(
        project, fixtures.items(), cache_dir(hass), make_fetcher(hass), PROJECT_FILE)


async def async_load_entry(hass: HomeAssistant, storage_id: str) -> LoadedProject:
    project, fixtures = await hass.async_add_executor_job(
        read_files, entry_dir(hass, storage_id))
    return await async_load(hass, project, fixtures)
