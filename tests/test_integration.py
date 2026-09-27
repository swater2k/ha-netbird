"""Setup, Entitäten, Ereignisse, Schalter, Aktionen und Reparaturhinweise."""

from __future__ import annotations

from datetime import timedelta
import json

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.netbird.const import (
    DOMAIN,
    EVENT_AUDIT,
    EVENT_PEER_ADDED,
    EVENT_PEER_REMOVED,
    FEATURE_VERSION,
)

from .conftest import API, load, mock_api


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def _calls(aioclient_mock: AiohttpClientMocker, method: str) -> list[tuple[str, dict]]:
    return [
        (str(url), data if isinstance(data, dict) else json.loads(data or "null"))
        for m, url, data, _ in aioclient_mock.mock_calls
        if m.upper() == method
    ]


async def test_entities(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    # Peers
    assert hass.states.get("binary_sensor.netbird_router_connected").state == "on"
    assert hass.states.get("binary_sensor.s25_connected").state == "off"
    assert hass.states.get("binary_sensor.s25_login_expired").state == "off"
    assert hass.states.get("sensor.netbird_router_netbird_ip").state == "100.105.196.120"
    assert hass.states.get("sensor.s25_last_seen").state == "2026-09-26T07:30:00+00:00"
    # standardmäßig deaktiviert
    assert hass.states.get("sensor.s25_connection_ip") is None

    # Server
    online = hass.states.get("sensor.netbird_peers_online")
    assert online.state == "1"
    assert online.attributes["offline"] == ["s25+"]
    assert hass.states.get("sensor.netbird_peers").state == "2"
    assert hass.states.get("sensor.netbird_users").state == "2"
    pending = hass.states.get("sensor.netbird_users_awaiting_approval")
    assert pending.state == "1"
    assert pending.attributes["users"] == ["new@example.com"]
    assert hass.states.get("sensor.netbird_token_expiration").state == "2027-09-26T00:00:00+00:00"
    assert hass.states.get("sensor.netbird_last_audit_event").state == "Policy updated"
    assert hass.states.get("sensor.netbird_group_geraete").state == "1"
    key = hass.states.get("sensor.netbird_setup_key_lan_router")
    assert key.state == "overused"
    assert key.attributes["auto_groups"] == ["routing-peers"]

    update = hass.states.get("update.netbird_management_server")
    assert update.state == "on"
    assert update.attributes["installed_version"] == "0.79.0"
    assert update.attributes["latest_version"] == "0.80.1"

    # Netzwerk: Router läuft über die Gruppe routing-peers
    assert hass.states.get("binary_sensor.netbird_heimnetz_routing_peer_online").state == "on"
    assert hass.states.get("sensor.netbird_heimnetz_resources").state == "1"

    # ohne Optionen keine Schalter
    assert not hass.states.async_entity_ids("switch")


async def test_routing_peer_offline(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    peers = load("peers")
    peers[0]["connected"] = False
    mock_api(aioclient_mock, {"/peers": peers})
    await _setup(hass, config_entry)
    assert hass.states.get("binary_sensor.netbird_heimnetz_routing_peer_online").state == "off"


async def test_peer_added_and_removed(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    added, removed = [], []
    hass.bus.async_listen(EVENT_PEER_ADDED, added.append)
    hass.bus.async_listen(EVENT_PEER_REMOVED, removed.append)

    peers = load("peers")
    laptop = dict(peers[1], id="p-laptop", name="laptop", hostname="laptop", ip="100.105.1.1")
    mock_api(aioclient_mock, {"/peers": [peers[0], laptop]})
    await _refresh(hass, config_entry)

    assert [e.data["name"] for e in added] == ["laptop"]
    assert [e.data["name"] for e in removed] == ["s25+"]
    assert hass.states.get("binary_sensor.laptop_connected") is not None
    assert hass.states.get("binary_sensor.s25_connected") is None
    devices = dr.async_get(hass)
    assert (
        devices.async_get_device(identifiers={(DOMAIN, f"{config_entry.entry_id}_peer_p-phone")})
        is None
    )


async def test_audit_events(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    fired = []
    hass.bus.async_listen(EVENT_AUDIT, fired.append)

    # erneutes Abfragen ohne neue Einträge feuert nichts
    await _refresh(hass, config_entry)
    assert fired == []

    audit = load("audit")
    audit.append(
        {
            "id": "3",
            "timestamp": "2026-09-26T09:00:00Z",
            "activity": "User blocked",
            "activity_code": "user.block",
            "initiator_id": "u-michael",
            "initiator_name": "Michael",
            "initiator_email": "michael@example.com",
            "target_id": "u-pending",
            "meta": {},
        }
    )
    mock_api(aioclient_mock, {"/events/audit": audit})
    await _refresh(hass, config_entry)

    assert len(fired) == 1
    assert fired[0].data["activity_code"] == "user.block"
    assert hass.states.get("sensor.netbird_last_audit_event").state == "User blocked"


@pytest.mark.parametrize("options", [{"audit_events": False}])
async def test_audit_disabled(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.netbird_last_audit_event") is None
    assert not [c for c in _calls(aioclient_mock, "GET") if c[0].endswith("/events/audit")]


async def test_optional_endpoint_missing(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock, status={"/instance/version": 404})
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert FEATURE_VERSION in config_entry.runtime_data.coordinator.unsupported
    assert hass.states.get("update.netbird_management_server") is None


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    mock_api(aioclient_mock, status={"/peers": 401})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_token_expiring_issue(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    soon = (dt_util.utcnow() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tokens = [dict(load("tokens")[0], expiration_date=soon)]
    mock_api(aioclient_mock, {"/users/u-svc/tokens": tokens})
    await _setup(hass, config_entry)
    issue_id = f"token_expiring_{config_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    mock_api(aioclient_mock)
    await _refresh(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize("options", [{"control_policies": True}])
async def test_missing_permissions_issue(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    users = load("users")
    users[2]["role"] = "auditor"  # der Service User aus der Benutzerliste
    mock_api(aioclient_mock, {"/users": users})
    await _setup(hass, config_entry)
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_permissions_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders["modules"] == "policies"


@pytest.mark.parametrize("options", [{"control_policies": True}])
async def test_permissions_from_current_user(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    """Token eines normalen Benutzers: Rechte kommen direkt aus /users/current."""
    users = [u for u in load("users") if not u["is_service_user"]]
    user = load("current_user")
    user["permissions"]["modules"]["policies"]["update"] = False
    mock_api(aioclient_mock, {"/users": users, "/users/current": user})
    await _setup(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_permissions_{config_entry.entry_id}"
    )


# --------------------------------------------------------------------------- #
# Steuerung                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("options", [{"control_policies": True}])
async def test_policy_switch(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    entity_id = "switch.netbird_policy_heimnetz_adressblock_access"
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get("switch.netbird_policy_default").state == "off"

    policy = load("policies")[0]
    mock_api(aioclient_mock)
    aioclient_mock.get(f"{API}/policies/pol-home", json=policy)
    aioclient_mock.put(f"{API}/policies/pol-home", json=dict(policy, enabled=False))
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)

    ((url, payload),) = _calls(aioclient_mock, "PUT")
    assert url.endswith("/policies/pol-home")
    assert payload["enabled"] is False
    rule = payload["rules"][0]
    assert rule["sources"] == ["g-dev"]
    assert rule["destinationResource"] == {"id": "res-lan", "type": "subnet"}
    assert "destinations" not in rule


@pytest.mark.parametrize("options", [{"control_networks": True}])
async def test_network_switches(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    resource = "switch.netbird_heimnetz_resource_heimnetz_adressblock"
    router = "switch.netbird_heimnetz_routing_peer_routing_peers"
    assert hass.states.get(resource).state == "on"
    assert hass.states.get(router).state == "on"

    mock_api(aioclient_mock)
    aioclient_mock.put(f"{API}/networks/net-home/resources/res-lan", json={})
    aioclient_mock.put(f"{API}/networks/net-home/routers/rt-1", json={})
    await hass.services.async_call("switch", "turn_off", {"entity_id": resource}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": router}, blocking=True)

    calls = dict(_calls(aioclient_mock, "PUT"))
    assert calls[f"{API}/networks/net-home/resources/res-lan"] == {
        "name": "Heimnetz-AdressBlock",
        "description": "",
        "address": "192.168.178.0/24",
        "enabled": False,
        "groups": ["g-lan"],
    }
    assert calls[f"{API}/networks/net-home/routers/rt-1"] == {
        "metric": 9999,
        "masquerade": True,
        "enabled": False,
        "peer_groups": ["g-rp"],
    }


def _device_id(hass: HomeAssistant, entry, peer_id: str) -> str:
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_peer_{peer_id}")}
    )
    assert device is not None
    return device.id


async def test_action_needs_option(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_peer",
            {"device_id": _device_id(hass, config_entry, "p-phone")},
            blocking=True,
        )
    assert not _calls(aioclient_mock, "DELETE")


@pytest.mark.parametrize("options", [{"control_groups": True, "control_peers": True}])
async def test_peer_actions(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    device_id = _device_id(hass, config_entry, "p-phone")

    mock_api(aioclient_mock)
    aioclient_mock.get(f"{API}/groups/g-rp", json=load("groups")[1])
    aioclient_mock.put(f"{API}/groups/g-rp", json={})
    aioclient_mock.put(f"{API}/peers/p-phone", json={})
    await hass.services.async_call(
        DOMAIN,
        "add_peer_to_group",
        {"device_id": device_id, "group": "routing-peers"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "set_login_expiration", {"device_id": device_id, "enabled": False}, blocking=True
    )

    calls = dict(_calls(aioclient_mock, "PUT"))
    assert calls[f"{API}/groups/g-rp"] == {
        "name": "routing-peers",
        "peers": ["p-router", "p-phone"],
    }
    assert calls[f"{API}/peers/p-phone"]["login_expiration_enabled"] is False
    assert calls[f"{API}/peers/p-phone"]["name"] == "s25+"


@pytest.mark.parametrize("options", [{"control_setup_keys": True, "control_users": True}])
async def test_setup_key_and_user_actions(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)

    mock_api(aioclient_mock)
    aioclient_mock.post(
        f"{API}/setup-keys",
        json={
            "id": "sk-2",
            "name": "laptop",
            "key": "PLAIN-KEY",
            "expires": "2026-09-27T00:00:00Z",
        },
    )
    aioclient_mock.post(f"{API}/users/u-pending/approve", json={})
    response = await hass.services.async_call(
        DOMAIN,
        "create_setup_key",
        {"name": "laptop", "auto_groups": ["geraete"], "expires_days": 1},
        blocking=True,
        return_response=True,
    )
    assert response["key"] == "PLAIN-KEY"
    ((_, payload),) = _calls(aioclient_mock, "POST")[:1]
    assert payload == {
        "name": "laptop",
        "type": "one-off",
        "expires_in": 86400,
        "auto_groups": ["g-dev"],
        "usage_limit": 0,
        "ephemeral": False,
    }

    await hass.services.async_call(
        DOMAIN, "approve_user", {"user": "new@example.com"}, blocking=True
    )
    assert any(
        url.endswith("/users/u-pending/approve") for url, _ in _calls(aioclient_mock, "POST")
    )


async def test_unload(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_older_server(hass: HomeAssistant, config_entry, aioclient_mock) -> None:
    """Ohne /users/current und ohne Peer-Freigabe lädt die Integration trotzdem."""
    peers = load("peers")
    for peer in peers:
        peer.pop("approval_required")
    users = [dict(u, is_current=False) for u in load("users")]
    mock_api(aioclient_mock, {"/peers": peers, "/users": users}, status={"/users/current": 404})
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.s25_connected") is not None
    assert hass.states.get("binary_sensor.s25_approval_required") is None
    assert hass.states.get("sensor.netbird_token_expiration") is None
