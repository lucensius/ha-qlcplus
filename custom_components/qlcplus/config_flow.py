"""Config and options flow for QLC+."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ALL_GROUPS,
    CONF_FLASH_DURATION,
    CONF_GROUPS,
    CONF_POLL_INTERVAL,
    CONF_REVISION,
    CONF_STORAGE_ID,
    DEFAULT_FLASH_DURATION,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)
from .qlc.client import QLCAuthError, QLCClient, QLCConnectionError
from .qlc.pipeline import LoadedProject, compare_with_live
from .qlc.xmlutil import QLCParseError
from .storage import async_load, entry_dir, read_files, write_files

_LOGGER = logging.getLogger(__name__)

CONF_PROJECT_FILE = "project_file"
CONF_FIXTURE_FILE = "fixture_file"
CONF_REPLACE_FIXTURES = "replace_fixtures"

PROJECT_SELECTOR = selector.FileSelector(selector.FileSelectorConfig(accept=".qxw"))
FIXTURE_SELECTOR = selector.FileSelector(selector.FileSelectorConfig(accept=".qxf"))


def _read_upload(hass: HomeAssistant, file_id: str) -> tuple[str, bytes]:
    with process_uploaded_file(hass, file_id) as path:
        return Path(path).name, Path(path).read_bytes()


async def _validate_connection(hass: HomeAssistant, data: dict[str, Any]) -> str | None:
    """Return the QLC+ version; raise QLCConnectionError / QLCAuthError."""
    client = QLCClient(
        data[CONF_HOST], data[CONF_PORT], async_get_clientsession(hass),
        username=data.get(CONF_USERNAME) or None, password=data.get(CONF_PASSWORD) or None,
    )
    try:
        version = await client.get_version()
        await client.connect()
        await client.get_functions()
    finally:
        await client.close()
    return version


async def _live_lists(hass: HomeAssistant, data: dict[str, Any]) -> tuple[dict, dict]:
    client = QLCClient(
        data[CONF_HOST], data[CONF_PORT], async_get_clientsession(hass),
        username=data.get(CONF_USERNAME) or None, password=data.get(CONF_PASSWORD) or None,
    )
    try:
        await client.connect()
        return await client.get_functions(), await client.get_widgets()
    finally:
        await client.close()


class _ProjectSteps:
    """Shared upload / fixture / mismatch handling for config and options flow."""

    hass: HomeAssistant
    _project: bytes | None
    _fixtures: dict[str, bytes]
    _loaded: LoadedProject | None
    _conn: dict[str, Any]
    _project_name: str = ""

    async def _add_uploads(self, user_input: dict[str, Any], errors: dict[str, str]) -> None:
        try:
            if file_id := user_input.get(CONF_PROJECT_FILE):
                name, self._project = await self.hass.async_add_executor_job(
                    _read_upload, self.hass, file_id)
                self._project_name = Path(name).stem
            if file_id := user_input.get(CONF_FIXTURE_FILE):
                name, data = await self.hass.async_add_executor_job(
                    _read_upload, self.hass, file_id)
                self._fixtures[name] = data
        except ValueError:
            errors["base"] = "upload_failed"

    async def _process(self, errors: dict[str, str]) -> None:
        self._loaded = None
        if self._project is None:
            errors["base"] = "project_required"
            return
        try:
            self._loaded = await async_load(self.hass, self._project, self._fixtures)
        except QLCParseError as err:
            _LOGGER.debug("Invalid upload: %s", err)
            self._parse_error = str(err)
            errors["base"] = "invalid_file"

    def _missing_text(self) -> str:
        assert self._loaded is not None
        return "\n".join(f"- {msg}" for msg in self._loaded.errors.values())

    async def _mismatch_text(self) -> str | None:
        assert self._loaded is not None
        try:
            functions, widgets = await _live_lists(self.hass, self._conn)
        except QLCConnectionError:
            return None  # cannot compare, setup will retry the connection later
        mismatch = compare_with_live(self._loaded.project, functions, widgets)
        return None if mismatch.is_match else mismatch.summary()


class QLCConfigFlow(_ProjectSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._conn: dict[str, Any] = {}
        self._project = None
        self._fixtures = {}
        self._loaded = None
        self._parse_error = ""
        self._version: str | None = None
        self._mismatch = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> QLCOptionsFlow:
        return QLCOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            try:
                self._version = await _validate_connection(self.hass, user_input)
            except QLCAuthError:
                errors["base"] = "invalid_auth"
            except QLCConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self._conn = user_input
                return await self.async_step_project()
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(CONF_USERNAME): str,
            vol.Optional(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_project(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self._add_uploads(user_input, errors)
            if not errors:
                await self._process(errors)
            if not errors:
                return await self._after_load()
        schema = vol.Schema({
            vol.Required(CONF_PROJECT_FILE): PROJECT_SELECTOR,
            vol.Optional(CONF_FIXTURE_FILE): FIXTURE_SELECTOR,
        })
        return self.async_show_form(
            step_id="project", data_schema=schema, errors=errors,
            description_placeholders={"version": self._version or "?",
                                      "error": self._parse_error})

    async def _after_load(self) -> ConfigFlowResult:
        assert self._loaded is not None
        if self._loaded.errors:
            return await self.async_step_fixtures()
        return await self.async_step_check()

    async def async_step_fixtures(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_FIXTURE_FILE):
                await self._add_uploads(user_input, errors)
                if not errors:
                    await self._process(errors)
                if not errors and not self._loaded.errors:
                    return await self.async_step_check()
            else:
                return await self.async_step_check()
        return self.async_show_form(
            step_id="fixtures",
            data_schema=vol.Schema({vol.Optional(CONF_FIXTURE_FILE): FIXTURE_SELECTOR}),
            errors=errors,
            description_placeholders={"missing": self._missing_text() if self._loaded else "",
                                      "error": self._parse_error},
        )

    async def async_step_check(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        mismatch = await self._mismatch_text()
        if mismatch:
            self._mismatch = mismatch
            return await self.async_step_mismatch()
        return await self._create()

    async def async_step_mismatch(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create()
        return self.async_show_form(
            step_id="mismatch", data_schema=vol.Schema({}),
            description_placeholders={"details": self._mismatch})

    async def _create(self) -> ConfigFlowResult:
        storage_id = uuid.uuid4().hex
        await self.hass.async_add_executor_job(
            write_files, entry_dir(self.hass, storage_id), self._project, self._fixtures, True)
        title = f"QLC+ {self._project_name or self._conn[CONF_HOST]}"
        return self.async_create_entry(
            title=title,
            data={**self._conn, CONF_STORAGE_ID: storage_id},
            options={CONF_GROUPS: ALL_GROUPS, CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                     CONF_FLASH_DURATION: DEFAULT_FLASH_DURATION, CONF_REVISION: 1},
        )


class QLCOptionsFlow(_ProjectSteps, OptionsFlow):
    def __init__(self) -> None:
        self._project = None
        self._fixtures = {}
        self._loaded = None
        self._parse_error = ""
        self._options: dict[str, Any] = {}
        self._files_changed = False
        self._replace = False
        self._mismatch = ""

    @property
    def _conn(self) -> dict[str, Any]:
        return dict(self.config_entry.data)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        opts = self.config_entry.options
        if user_input is not None:
            self._options = {
                CONF_GROUPS: user_input[CONF_GROUPS],
                CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                CONF_FLASH_DURATION: user_input[CONF_FLASH_DURATION],
                CONF_REVISION: opts.get(CONF_REVISION, 1),
            }
            if user_input.get(CONF_PROJECT_FILE) or user_input.get(CONF_FIXTURE_FILE):
                self._replace = user_input.get(CONF_REPLACE_FIXTURES, False)
                project, fixtures = await self.hass.async_add_executor_job(
                    read_files, entry_dir(self.hass, self._conn[CONF_STORAGE_ID]))
                self._project = project
                self._fixtures = {} if self._replace else fixtures
                await self._add_uploads(user_input, errors)
                if not errors:
                    await self._process(errors)
                if not errors:
                    self._files_changed = True
                    if self._loaded.errors:
                        return await self.async_step_fixtures()
                    return await self.async_step_check()
            else:
                return self._save()

        schema = vol.Schema({
            vol.Required(CONF_GROUPS, default=opts.get(CONF_GROUPS, ALL_GROUPS)):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=ALL_GROUPS, multiple=True, translation_key=CONF_GROUPS)),
            vol.Required(CONF_POLL_INTERVAL,
                         default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=1, max=300, step=1, unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_FLASH_DURATION,
                         default=opts.get(CONF_FLASH_DURATION, DEFAULT_FLASH_DURATION)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0.1, max=60, step=0.1, unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_PROJECT_FILE): PROJECT_SELECTOR,
            vol.Optional(CONF_FIXTURE_FILE): FIXTURE_SELECTOR,
            vol.Optional(CONF_REPLACE_FIXTURES, default=False): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors,
                                    description_placeholders={"error": self._parse_error})

    async def async_step_fixtures(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_FIXTURE_FILE):
                await self._add_uploads(user_input, errors)
                if not errors:
                    await self._process(errors)
                if not errors and not self._loaded.errors:
                    return await self.async_step_check()
            else:
                return await self.async_step_check()
        return self.async_show_form(
            step_id="fixtures",
            data_schema=vol.Schema({vol.Optional(CONF_FIXTURE_FILE): FIXTURE_SELECTOR}),
            errors=errors,
            description_placeholders={"missing": self._missing_text() if self._loaded else "",
                                      "error": self._parse_error},
        )

    async def async_step_check(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        mismatch = await self._mismatch_text()
        if mismatch:
            self._mismatch = mismatch
            return await self.async_step_mismatch()
        return await self._save_files()

    async def async_step_mismatch(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._save_files()
        return self.async_show_form(
            step_id="mismatch", data_schema=vol.Schema({}),
            description_placeholders={"details": self._mismatch})

    async def _save_files(self) -> ConfigFlowResult:
        await self.hass.async_add_executor_job(
            write_files, entry_dir(self.hass, self._conn[CONF_STORAGE_ID]),
            self._project, self._fixtures, True)
        # bump the revision so the entry reloads even if no option changed
        self._options[CONF_REVISION] = self._options.get(CONF_REVISION, 1) + 1
        return self._save()

    def _save(self) -> ConfigFlowResult:
        return self.async_create_entry(data=self._options)
