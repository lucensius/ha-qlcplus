"""The QLC+ integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    ALL_GROUPS,
    CONF_GROUPS,
    CONF_POLL_INTERVAL,
    CONF_STORAGE_ID,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    GROUP_FIXTURES,
    GROUP_FUNCTIONS,
    GROUP_WIDGETS,
)
from .coordinator import QLCCoordinator
from .entity import fixture_device_info
from .qlc.client import QLCClient
from .qlc.pipeline import LoadedProject
from .qlc.xmlutil import QLCParseError
from .services import async_setup_services
from .storage import async_load_entry, entry_dir, remove_entry_files

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class QLCRuntimeData:
    coordinator: QLCCoordinator
    loaded: LoadedProject
    groups: list[str]


type QLCConfigEntry = ConfigEntry[QLCRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry) -> bool:
    try:
        loaded = await async_load_entry(hass, entry.data[CONF_STORAGE_ID])
    except FileNotFoundError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="project_missing",
            translation_placeholders={
                "path": str(entry_dir(hass, entry.data[CONF_STORAGE_ID]))},
        ) from err
    except QLCParseError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="project_invalid",
            translation_placeholders={"error": str(err)},
        ) from err
    for fid, error in loaded.errors.items():
        _LOGGER.warning("Fixture %s skipped: %s", fid, error)
    for warning in loaded.resolve.warnings:
        _LOGGER.warning("%s", warning)

    groups = entry.options.get(CONF_GROUPS, ALL_GROUPS)
    client = QLCClient(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        async_get_clientsession(hass),
        username=entry.data.get(CONF_USERNAME) or None,
        password=entry.data.get(CONF_PASSWORD) or None,
    )
    coordinator = QLCCoordinator(
        hass,
        client,
        loaded,
        poll_interval=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        poll_channels=GROUP_FIXTURES in groups,
        name=f"{DOMAIN} {entry.title}",
    )
    await coordinator.async_start()
    if not await client.wait_connected(10):
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect",
            translation_placeholders={"host": entry.data[CONF_HOST]},
        )

    await coordinator.async_wait_synced(10)
    entry.runtime_data = QLCRuntimeData(coordinator, loaded, groups)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Q Light Controller Plus",
        model="QLC+ Web Access",
        name=entry.title,
        sw_version=coordinator.data.version,
        configuration_url=f"http://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}",
    )
    if GROUP_FIXTURES in groups:
        # parents first, so via_device always points to an existing device
        for device in sorted(loaded.plan.devices, key=lambda d: d.via is not None):
            dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id, **fixture_device_info(entry.entry_id, device))
    _remove_stale_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _expected_unique_ids(entry: QLCConfigEntry) -> set[str]:
    data = entry.runtime_data
    plan = data.loaded.plan
    keys = {"connection", "blackout"}
    if plan.has_laser:
        keys.add("laser_release")
    if GROUP_FIXTURES in data.groups:
        keys |= {s.key for s in (*plan.lights, *plan.selects, *plan.numbers)}
    if GROUP_FUNCTIONS in data.groups:
        keys |= {f"fn{f.function_id}" for f in plan.functions}
    if GROUP_WIDGETS in data.groups:
        keys |= {f"vc{w.widget_id}" for w in plan.widgets}
    return {f"{entry.entry_id}_{k}" for k in keys}


def _remove_stale_entities(hass: HomeAssistant, entry: QLCConfigEntry) -> None:
    """Drop entities and devices that are no longer part of the project or groups."""
    expected = _expected_unique_ids(entry)
    ent_reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id not in expected:
            ent_reg.async_remove(ent.entity_id)
    dev_reg = dr.async_get(hass)
    valid = {(DOMAIN, entry.entry_id)}
    if GROUP_FIXTURES in entry.runtime_data.groups:
        valid |= {(DOMAIN, f"{entry.entry_id}_{d.key}")
                  for d in entry.runtime_data.loaded.plan.devices}
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not device.identifiers & valid:
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: QLCConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: QLCConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: QLCConfigEntry) -> None:
    await hass.async_add_executor_job(
        remove_entry_files, entry_dir(hass, entry.data[CONF_STORAGE_ID]))
