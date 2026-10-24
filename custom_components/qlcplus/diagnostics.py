"""Diagnostics for QLC+."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import QLCConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: QLCConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    loaded = data.loaded
    state = data.coordinator.data
    plan = loaded.plan
    return {
        "entry": {"data": async_redact_data(dict(entry.data), TO_REDACT),
                  "options": dict(entry.options)},
        "qlcplus": {"connected": state.connected, "version": state.version},
        "project": {
            "creator_version": loaded.project.creator_version,
            "universes": loaded.project.universes,
            "fixtures": {
                fid: {
                    "name": f.name, "manufacturer": f.manufacturer, "model": f.model,
                    "mode": f.mode, "universe": f.universe, "address": f.address,
                    "channels": f.channels,
                    "source": loaded.resolve.fixtures[fid].source
                    if fid in loaded.resolve.fixtures else None,
                    "type": loaded.fixtures[fid].type if fid in loaded.fixtures else None,
                }
                for fid, f in loaded.project.fixtures.items()
            },
            "functions": len(loaded.project.functions),
            "widgets": len(loaded.project.widgets),
            "resolve_errors": loaded.resolve.errors,
            "resolve_warnings": loaded.resolve.warnings,
        },
        "safety": {
            "laser_fixtures": sorted(loaded.safety.laser_fixtures),
            "unknown_fixtures": sorted(loaded.safety.unknown_fixtures),
            "guarded_functions": sorted(loaded.safety.functions),
            "guarded_widgets": sorted(loaded.safety.widgets),
            "released": state.laser_released,
        },
        "plan": {
            "devices": len(plan.devices),
            "lights": [s.key for s in plan.lights],
            "selects": [s.key for s in plan.selects],
            "numbers": [s.key for s in plan.numbers],
            "functions": {f.function_id: f.kind for f in plan.functions},
            "widgets": {w.widget_id: w.kind for w in plan.widgets},
            "blackout_widget": plan.blackout_widget,
        },
        "state": {
            "running_functions": sorted(k for k, v in state.functions.items() if v),
            "overridden_channels": sorted(state.overridden),
            "channels": dict(sorted(state.channels.items())),
            "blackout": state.blackout,
        },
    }
