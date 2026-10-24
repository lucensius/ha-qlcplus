"""Config and options flow tests."""

from __future__ import annotations

from pathlib import Path

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.qlcplus.const import CONF_GROUPS, CONF_STORAGE_ID, DOMAIN

from .conftest import PROJECT, TMH, mock_uploads

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
TMH_ID = "22222222-2222-4222-8222-222222222222"
BROKEN_ID = "33333333-3333-4333-8333-333333333333"


async def _start(hass: HomeAssistant, server) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: server.host, CONF_PORT: server.port})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "project"
    assert result["description_placeholders"]["version"] == "4.14.4"
    return result


async def test_full_flow(hass: HomeAssistant, qlc_server, library_cache) -> None:
    result = await _start(hass, qlc_server)
    with mock_uploads({PROJECT_ID: PROJECT, TMH_ID: TMH}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project_file": PROJECT_ID, "fixture_file": TMH_ID})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "QLC+ Lichtsteuerung"
    storage = Path(hass.config.path(DOMAIN, result["data"][CONF_STORAGE_ID]))
    assert (storage / "project.qxw").read_bytes() == PROJECT.read_bytes()
    assert (storage / "fixtures" / TMH.name).exists()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_GROUPS] == ["functions", "fixtures", "widgets"]


async def test_missing_fixture_then_upload(hass: HomeAssistant, qlc_server, library_cache) -> None:
    result = await _start(hass, qlc_server)
    with mock_uploads({PROJECT_ID: PROJECT, TMH_ID: TMH}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project_file": PROJECT_ID})
        assert result["step_id"] == "fixtures"
        assert "Eurolite LED TMH Bar S120 MH Spots" in result["description_placeholders"]["missing"]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"fixture_file": TMH_ID})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_missing_fixture_skip(hass: HomeAssistant, qlc_server, library_cache) -> None:
    result = await _start(hass, qlc_server)
    with mock_uploads({PROJECT_ID: PROJECT}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project_file": PROJECT_ID})
        assert result["step_id"] == "fixtures"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_mismatch_warning(hass: HomeAssistant, qlc_server, library_cache) -> None:
    qlc_server.functions[3] = ("Something else", "Scene")
    del qlc_server.widgets[9]
    result = await _start(hass, qlc_server)
    with mock_uploads({PROJECT_ID: PROJECT, TMH_ID: TMH}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project_file": PROJECT_ID, "fixture_file": TMH_ID})
    assert result["step_id"] == "mismatch"
    details = result["description_placeholders"]["details"]
    assert "1 functions with other names (IDs 3)" in details
    assert "1 widgets missing in QLC+ (IDs 9)" in details
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_invalid_project(hass: HomeAssistant, qlc_server, library_cache, tmp_path) -> None:
    broken = tmp_path / "broken.qxw"
    broken.write_text("<Workspace><Engine>")
    result = await _start(hass, qlc_server)
    with mock_uploads({BROKEN_ID: broken}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project_file": BROKEN_ID})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_file"}
    assert "invalid XML" in result["description_placeholders"]["error"]


async def test_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, mock_entry, qlc_server) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: qlc_server.host, CONF_PORT: qlc_server.port})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_options_groups_and_reupload(hass: HomeAssistant, loaded_entry) -> None:
    entity_count = len(hass.states.async_entity_ids())
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_GROUPS: ["fixtures"], "poll_interval": 10, "flash_duration": 2})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["poll_interval"] == 10
    assert len(hass.states.async_entity_ids()) < entity_count
    assert hass.states.get("switch.qlc_lichtsteuerung_bar_rot") is None

    # re-upload the project: entry reloads because the revision changes
    revision = loaded_entry.options.get("project_revision", 1)
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    with mock_uploads({PROJECT_ID: PROJECT}):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_GROUPS: ["fixtures", "functions"], "poll_interval": 10,
             "flash_duration": 2, "project_file": PROJECT_ID})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["project_revision"] == revision + 1
    assert hass.states.get("switch.qlc_lichtsteuerung_bar_rot") is not None
