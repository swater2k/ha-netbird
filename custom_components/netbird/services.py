"""Aktionen. Jede Gruppe ist nur nutzbar, wenn sie in den Optionen freigegeben ist."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .api import NetBirdError, NetBirdPermissionError
from .const import (
    CONF_CONTROL_GROUPS,
    CONF_CONTROL_PEERS,
    CONF_CONTROL_SETUP_KEYS,
    CONF_CONTROL_USERS,
    DOMAIN,
)

if TYPE_CHECKING:
    from . import NetBirdConfigEntry
    from .coordinator import NetBirdCoordinator

ATTR_DEVICE_ID = "device_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENABLED = "enabled"
ATTR_USER = "user"
ATTR_BLOCKED = "blocked"
ATTR_GROUP = "group"
ATTR_NAME = "name"
ATTR_REUSABLE = "reusable"
ATTR_EXPIRES_DAYS = "expires_days"
ATTR_AUTO_GROUPS = "auto_groups"
ATTR_USAGE_LIMIT = "usage_limit"
ATTR_EPHEMERAL = "ephemeral"

PEER_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
PEER_TOGGLE_SCHEMA = PEER_SCHEMA.extend({vol.Required(ATTR_ENABLED): cv.boolean})
PEER_GROUP_SCHEMA = PEER_SCHEMA.extend({vol.Required(ATTR_GROUP): cv.string})
ENTRY_FIELD = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
USER_SCHEMA = vol.Schema({**ENTRY_FIELD, vol.Required(ATTR_USER): cv.string})
USER_BLOCK_SCHEMA = USER_SCHEMA.extend({vol.Required(ATTR_BLOCKED): cv.boolean})
CREATE_KEY_SCHEMA = vol.Schema(
    {
        **ENTRY_FIELD,
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_REUSABLE, default=False): cv.boolean,
        vol.Optional(ATTR_EXPIRES_DAYS, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=365)
        ),
        vol.Optional(ATTR_AUTO_GROUPS, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_USAGE_LIMIT, default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_EPHEMERAL, default=False): cv.boolean,
    }
)
REVOKE_KEY_SCHEMA = vol.Schema({**ENTRY_FIELD, vol.Required(ATTR_NAME): cv.string})


# --------------------------------------------------------------------------- #
# Auflösung von Ziel-Einträgen                                                  #
# --------------------------------------------------------------------------- #


def _loaded_entries(hass: HomeAssistant) -> list[NetBirdConfigEntry]:
    return [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]


def _require(entry: NetBirdConfigEntry, option: str) -> NetBirdCoordinator:
    if not entry.options.get(option, False):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="control_disabled",
            translation_placeholders={"title": entry.title},
        )
    return entry.runtime_data.coordinator


def _entry_from_call(hass: HomeAssistant, call: ServiceCall) -> NetBirdConfigEntry:
    entries = _loaded_entries(hass)
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_found")
    if len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_ambiguous")
    return entries[0]


def _peer_from_call(
    hass: HomeAssistant, call: ServiceCall, option: str
) -> tuple[NetBirdCoordinator, dict[str, Any]]:
    device = dr.async_get(hass).async_get(call.data[ATTR_DEVICE_ID])
    if device is not None:
        for entry in _loaded_entries(hass):
            prefix = f"{entry.entry_id}_peer_"
            for domain, identifier in device.identifiers:
                if domain == DOMAIN and identifier.startswith(prefix):
                    coordinator = _require(entry, option)
                    peer = coordinator.data.peers.get(identifier[len(prefix) :])
                    if peer is not None:
                        return coordinator, peer
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="peer_not_found")


def _find_user(coordinator: NetBirdCoordinator, value: str) -> dict[str, Any]:
    needle = value.strip().lower()
    for user in coordinator.data.users:
        if needle in (
            (user.get("id") or "").lower(),
            (user.get("email") or "").lower(),
            (user.get("name") or "").lower(),
        ):
            return user
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="user_not_found",
        translation_placeholders={"user": value},
    )


def _find_group(coordinator: NetBirdCoordinator, value: str) -> dict[str, Any]:
    needle = value.strip().lower()
    for group in coordinator.data.groups.values():
        if needle in ((group.get("id") or "").lower(), (group.get("name") or "").lower()):
            return group
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="group_not_found",
        translation_placeholders={"group": value},
    )


def _peer_ids(group: dict[str, Any]) -> list[str]:
    return [p["id"] if isinstance(p, dict) else p for p in group.get("peers") or []]


async def _run(coordinator: NetBirdCoordinator, coro: Awaitable[Any]) -> Any:
    try:
        result = await coro
    except NetBirdPermissionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="permission_denied"
        ) from err
    except NetBirdError as err:
        raise HomeAssistantError(f"NetBird: {err}") from err
    await coordinator.async_request_refresh()
    return result


# --------------------------------------------------------------------------- #
# Handler                                                                      #
# --------------------------------------------------------------------------- #


def async_setup_services(hass: HomeAssistant) -> None:
    async def approve_peer(call: ServiceCall) -> None:
        coordinator, peer = _peer_from_call(hass, call, CONF_CONTROL_PEERS)
        await _run(coordinator, coordinator.client.update_peer(peer, approval_required=False))

    async def set_login_expiration(call: ServiceCall) -> None:
        coordinator, peer = _peer_from_call(hass, call, CONF_CONTROL_PEERS)
        await _run(
            coordinator,
            coordinator.client.update_peer(peer, login_expiration_enabled=call.data[ATTR_ENABLED]),
        )

    async def delete_peer(call: ServiceCall) -> None:
        coordinator, peer = _peer_from_call(hass, call, CONF_CONTROL_PEERS)
        await _run(coordinator, coordinator.client.delete_peer(peer["id"]))

    async def approve_user(call: ServiceCall) -> None:
        coordinator = _require(_entry_from_call(hass, call), CONF_CONTROL_USERS)
        user = _find_user(coordinator, call.data[ATTR_USER])
        await _run(coordinator, coordinator.client.approve_user(user["id"]))

    async def reject_user(call: ServiceCall) -> None:
        coordinator = _require(_entry_from_call(hass, call), CONF_CONTROL_USERS)
        user = _find_user(coordinator, call.data[ATTR_USER])
        await _run(coordinator, coordinator.client.reject_user(user["id"]))

    async def block_user(call: ServiceCall) -> None:
        coordinator = _require(_entry_from_call(hass, call), CONF_CONTROL_USERS)
        user = _find_user(coordinator, call.data[ATTR_USER])
        if user.get("is_current"):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="cannot_block_self"
            )
        await _run(coordinator, coordinator.client.set_user_blocked(user, call.data[ATTR_BLOCKED]))

    def _group_change(add: bool) -> Callable[[ServiceCall], Awaitable[None]]:
        async def handler(call: ServiceCall) -> None:
            coordinator, peer = _peer_from_call(hass, call, CONF_CONTROL_GROUPS)
            group = _find_group(coordinator, call.data[ATTR_GROUP])
            members = _peer_ids(group)
            if add and peer["id"] not in members:
                members.append(peer["id"])
            elif not add and peer["id"] in members:
                members.remove(peer["id"])
            else:
                return
            await _run(coordinator, coordinator.client.set_group_peers(group["id"], members))

        return handler

    async def create_setup_key(call: ServiceCall) -> ServiceResponse:
        coordinator = _require(_entry_from_call(hass, call), CONF_CONTROL_SETUP_KEYS)
        groups = [_find_group(coordinator, g)["id"] for g in call.data[ATTR_AUTO_GROUPS]]
        payload = {
            "name": call.data[ATTR_NAME],
            "type": "reusable" if call.data[ATTR_REUSABLE] else "one-off",
            "expires_in": call.data[ATTR_EXPIRES_DAYS] * 86400,
            "auto_groups": groups,
            "usage_limit": call.data[ATTR_USAGE_LIMIT],
            "ephemeral": call.data[ATTR_EPHEMERAL],
        }
        created = await _run(coordinator, coordinator.client.create_setup_key(payload))
        created = created or {}
        return {
            "id": created.get("id"),
            "name": created.get("name"),
            "key": created.get("key"),
            "expires": created.get("expires"),
        }

    async def revoke_setup_key(call: ServiceCall) -> None:
        coordinator = _require(_entry_from_call(hass, call), CONF_CONTROL_SETUP_KEYS)
        needle = call.data[ATTR_NAME].strip().lower()
        for key in (coordinator.data.setup_keys or {}).values():
            if needle in ((key.get("id") or "").lower(), (key.get("name") or "").lower()):
                await _run(coordinator, coordinator.client.revoke_setup_key(key))
                return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="setup_key_not_found",
            translation_placeholders={"name": call.data[ATTR_NAME]},
        )

    register = hass.services.async_register
    register(DOMAIN, "approve_peer", approve_peer, schema=PEER_SCHEMA)
    register(DOMAIN, "set_login_expiration", set_login_expiration, schema=PEER_TOGGLE_SCHEMA)
    register(DOMAIN, "delete_peer", delete_peer, schema=PEER_SCHEMA)
    register(DOMAIN, "approve_user", approve_user, schema=USER_SCHEMA)
    register(DOMAIN, "reject_user", reject_user, schema=USER_SCHEMA)
    register(DOMAIN, "block_user", block_user, schema=USER_BLOCK_SCHEMA)
    register(DOMAIN, "add_peer_to_group", _group_change(True), schema=PEER_GROUP_SCHEMA)
    register(DOMAIN, "remove_peer_from_group", _group_change(False), schema=PEER_GROUP_SCHEMA)
    register(
        DOMAIN,
        "create_setup_key",
        create_setup_key,
        schema=CREATE_KEY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    register(DOMAIN, "revoke_setup_key", revoke_setup_key, schema=REVOKE_KEY_SCHEMA)
