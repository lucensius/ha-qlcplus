"""Constants for the QLC+ integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "qlcplus"

CONF_STORAGE_ID: Final = "storage_id"
CONF_REVISION: Final = "project_revision"
CONF_GROUPS: Final = "entity_groups"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_FLASH_DURATION: Final = "flash_duration"

GROUP_FUNCTIONS: Final = "functions"
GROUP_FIXTURES: Final = "fixtures"
GROUP_WIDGETS: Final = "widgets"
ALL_GROUPS: Final = [GROUP_FUNCTIONS, GROUP_FIXTURES, GROUP_WIDGETS]

DEFAULT_PORT: Final = 9999
DEFAULT_POLL_INTERVAL: Final = 5
DEFAULT_FLASH_DURATION: Final = 1.0

PROJECT_FILE: Final = "project.qxw"
FIXTURES_DIR: Final = "fixtures"
LIBRARY_CACHE_DIR: Final = "library_cache"

ATTR_LASER: Final = "laser"
ATTR_LASER_LOCKED: Final = "laser_locked"
ATTR_DMX_ADDRESS: Final = "dmx_address"
ATTR_FUNCTION_ID: Final = "function_id"
ATTR_WIDGET_ID: Final = "widget_id"
