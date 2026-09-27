"""Diagnose-Download."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from . import NetBirdConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {
    CONF_TOKEN,
    CONF_URL,
    "key",
    "email",
    "initiator_email",
    "connection_ip",
    "serial_number",
    "city_name",
    "geoname_id",
    "last_login",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetBirdConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    result: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "unsupported_features": sorted(coordinator.unsupported),
    }
    if data is None:
        return result
    result.update(
        {
            "fetched_at": data.fetched_at.isoformat(),
            "version": data.version,
            "permissions": data.permissions,
            "counts": {
                "peers": len(data.peers),
                "users": len(data.users),
                "groups": len(data.groups),
                "policies": len(data.policies),
                "networks": len(data.networks),
                "setup_keys": None if data.setup_keys is None else len(data.setup_keys),
                "tokens": None if data.tokens is None else len(data.tokens),
            },
            "peers": async_redact_data(list(data.peers.values()), TO_REDACT),
            "networks": {
                nid: {
                    "network": n.network,
                    "routers": n.routers,
                    "resources": n.resources,
                }
                for nid, n in data.networks.items()
            },
            "last_audit_event": async_redact_data(data.last_audit_event or {}, TO_REDACT),
        }
    )
    return result
