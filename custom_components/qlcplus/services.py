"""Services of the QLC+ integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

if TYPE_CHECKING:
    from . import QLCConfigEntry

ATTR_CONFIG_ENTRY = "config_entry_id"
ATTR_FUNCTION = "function"
ATTR_ADDRESS = "address"
ATTR_UNIVERSE = "universe"
ATTR_VALUE = "value"
ATTR_STATE = "state"

_ENTRY = {vol.Optional(ATTR_CONFIG_ENTRY): cv.string}

FUNCTION_SCHEMA = vol.Schema({**_ENTRY, vol.Required(ATTR_FUNCTION): vol.Any(
    vol.Coerce(int), cv.string)})
SET_CHANNEL_SCHEMA = vol.Schema({
    **_ENTRY,
    vol.Required(ATTR_ADDRESS): vol.All(vol.Coerce(int), vol.Range(min=1, max=512 * 64)),
    vol.Optional(ATTR_UNIVERSE): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
    vol.Required(ATTR_VALUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
})
RELEASE_SCHEMA = vol.Schema({
    **_ENTRY,
    vol.Optional(ATTR_ADDRESS): vol.All(cv.ensure_list, [vol.All(
        vol.Coerce(int), vol.Range(min=1, max=512 * 64))]),
    vol.Optional(ATTR_UNIVERSE): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
})
BLACKOUT_SCHEMA = vol.Schema({**_ENTRY, vol.Required(ATTR_STATE): cv.boolean})
ENTRY_SCHEMA = vol.Schema(_ENTRY)


def _entry(hass: HomeAssistant, call: ServiceCall) -> QLCConfigEntry:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN)
               if e.state is ConfigEntryState.LOADED]
    wanted = call.data.get(ATTR_CONFIG_ENTRY)
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN,
                                     translation_key="entry_required")
    return entries[0]


def _function_id(entry: QLCConfigEntry, ref: int | str) -> int:
    functions = entry.runtime_data.loaded.project.functions
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        fid = int(ref)
        if fid in functions:
            return fid
    else:
        matches = [f.id for f in functions.values() if f.name == ref]
        if len(matches) == 1:
            return matches[0]
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_function",
                                 translation_placeholders={"function": str(ref)})


def _address(call: ServiceCall, address: int) -> int:
    universe = call.data.get(ATTR_UNIVERSE)
    if universe is None:
        return address
    if address > 512:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="bad_address")
    return (universe - 1) * 512 + address


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def run_function(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await entry.runtime_data.coordinator.async_set_function(
            _function_id(entry, call.data[ATTR_FUNCTION]), True)

    async def stop_function(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await entry.runtime_data.coordinator.async_set_function(
            _function_id(entry, call.data[ATTR_FUNCTION]), False)

    async def set_channel(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await entry.runtime_data.coordinator.async_set_channels(
            {_address(call, call.data[ATTR_ADDRESS]): call.data[ATTR_VALUE]})

    async def release_channel(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        coordinator = entry.runtime_data.coordinator
        addresses = call.data.get(ATTR_ADDRESS)
        if addresses:
            await coordinator.async_release_channels([_address(call, a) for a in addresses])
            return
        universes = ([call.data[ATTR_UNIVERSE]] if ATTR_UNIVERSE in call.data
                     else [u + 1 for u in entry.runtime_data.loaded.project.universes])
        await coordinator.async_release_universes(universes)

    async def blackout(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await entry.runtime_data.coordinator.async_set_blackout(call.data[ATTR_STATE])

    async def reload_project(call: ServiceCall) -> None:
        entry = _entry(hass, call)
        await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, "run_function", run_function, FUNCTION_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_function", stop_function, FUNCTION_SCHEMA)
    hass.services.async_register(DOMAIN, "set_channel", set_channel, SET_CHANNEL_SCHEMA)
    hass.services.async_register(DOMAIN, "release_channel", release_channel, RELEASE_SCHEMA)
    hass.services.async_register(DOMAIN, "blackout", blackout, BLACKOUT_SCHEMA)
    hass.services.async_register(DOMAIN, "reload_project", reload_project, ENTRY_SCHEMA)
