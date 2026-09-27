"""Config-, Reauth- und Options-Flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.netbird.api import ids, normalize_url
from custom_components.netbird.const import DOMAIN

from .conftest import ENTRY_DATA, URL, mock_api


async def test_user_flow(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"url": "netbird.example.com/api/", "token": " nbp_secret ", "verify_ssl": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NetBird (netbird.example.com)"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == URL
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == "Token nbp_secret"


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, "invalid_auth"), (403, "insufficient_permissions"), (500, "invalid_response")],
)
async def test_user_flow_errors(hass: HomeAssistant, aioclient_mock, status, error) -> None:
    mock_api(aioclient_mock, status={"/peers": status})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_duplicate(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": "nbp_new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["token"] == "nbp_new"


async def test_options(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "scan_interval": 30,
            "token_warning_days": 7,
            "audit_events": True,
            "control_policies": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options["control_policies"] is True
    assert config_entry.options["control_users"] is False
    await hass.async_block_till_done()
    assert hass.states.get("switch.netbird_policy_default") is not None


def test_helpers() -> None:
    assert normalize_url("https://nb.example.com/api/") == "https://nb.example.com"
    assert normalize_url("nb.example.com") == "https://nb.example.com"
    assert normalize_url("http://10.0.0.1:8080/netbird") == "http://10.0.0.1:8080/netbird"
    assert ids([{"id": "a"}, "b", None, {"name": "x"}]) == ["a", "b"]
