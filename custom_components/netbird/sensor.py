"""Sensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NetBirdConfigEntry
from .const import CONF_AUDIT_EVENTS, FEATURE_AUDIT
from .coordinator import NetBirdCoordinator, NetBirdData, parse_time
from .entity import NetBirdEntity, NetBirdNetworkEntity, NetBirdPeerEntity, track_items

PARALLEL_UPDATES = 0

SETUP_KEY_STATES = ["valid", "expired", "revoked", "overused"]


def _human_users(data: NetBirdData) -> list[dict[str, Any]]:
    return [u for u in data.users if not u.get("is_service_user")]


def _user_list(users: list[dict[str, Any]]) -> list[str]:
    return [u.get("email") or u.get("name") or u.get("id", "") for u in users]


# --------------------------------------------------------------------------- #
# Server                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class ServerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[NetBirdData], Any]
    attrs_fn: Callable[[NetBirdData], dict[str, Any]] | None = None
    exists_fn: Callable[[NetBirdData], bool] = lambda _: True


SERVER_SENSORS: tuple[ServerSensorDescription, ...] = (
    ServerSensorDescription(
        key="peers_total",
        translation_key="peers_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.peers),
    ),
    ServerSensorDescription(
        key="peers_online",
        translation_key="peers_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for p in d.peers.values() if p.get("connected")),
        attrs_fn=lambda d: {
            "offline": sorted(p.get("name", "") for p in d.peers.values() if not p.get("connected"))
        },
    ),
    ServerSensorDescription(
        key="users_total",
        translation_key="users_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(_human_users(d)),
    ),
    ServerSensorDescription(
        key="users_pending",
        translation_key="users_pending",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for u in d.users if u.get("pending_approval")),
        attrs_fn=lambda d: {"users": _user_list([u for u in d.users if u.get("pending_approval")])},
    ),
    ServerSensorDescription(
        key="users_blocked",
        translation_key="users_blocked",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for u in d.users if u.get("is_blocked")),
        attrs_fn=lambda d: {"users": _user_list([u for u in d.users if u.get("is_blocked")])},
    ),
    ServerSensorDescription(
        key="token_expiration",
        translation_key="token_expiration",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.token_expiration,
        attrs_fn=lambda d: {
            "tokens": [
                {"name": t.get("name"), "expires": t.get("expiration_date")} for t in d.tokens or []
            ]
        },
        exists_fn=lambda d: d.tokens is not None,
    ),
    ServerSensorDescription(
        key="setup_keys_valid",
        translation_key="setup_keys_valid",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for k in (d.setup_keys or {}).values() if k.get("valid")),
        exists_fn=lambda d: d.setup_keys is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetBirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data

    entities: list[SensorEntity] = [
        ServerSensor(coordinator, entry, desc) for desc in SERVER_SENSORS if desc.exists_fn(data)
    ]
    if entry.options.get(CONF_AUDIT_EVENTS, True) and FEATURE_AUDIT not in coordinator.unsupported:
        entities.append(LastAuditEventSensor(coordinator, entry))
    async_add_entities(entities)

    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda d: d.peers.keys(),
        lambda peer_id: [PeerSensor(coordinator, entry, peer_id, desc) for desc in PEER_SENSORS],
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda d: d.groups.keys(),
        lambda group_id: [GroupSensor(coordinator, entry, group_id)],
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda d: (d.setup_keys or {}).keys(),
        lambda key_id: [SetupKeySensor(coordinator, entry, key_id)],
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda d: d.networks.keys(),
        lambda network_id: [NetworkResourcesSensor(coordinator, entry, network_id)],
    )


class ServerSensor(NetBirdEntity, SensorEntity):
    entity_description: ServerSensorDescription

    def __init__(
        self,
        coordinator: NetBirdCoordinator,
        entry: NetBirdConfigEntry,
        description: ServerSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class LastAuditEventSensor(NetBirdEntity, SensorEntity):
    """Zuletzt protokolliertes Audit-Ereignis; jedes neue feuert zusätzlich ein HA-Event."""

    _attr_translation_key = "last_audit_event"

    def __init__(self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_audit_event")

    @property
    def native_value(self) -> str | None:
        event = self.coordinator.data.last_audit_event
        return (event or {}).get("activity")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        event = self.coordinator.data.last_audit_event
        if not event:
            return None
        return {
            "activity_code": event.get("activity_code"),
            "timestamp": event.get("timestamp"),
            "initiator": event.get("initiator_email") or event.get("initiator_name"),
            "target_id": event.get("target_id"),
            "meta": event.get("meta") or {},
        }


# --------------------------------------------------------------------------- #
# Peers                                                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class PeerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


def _location(peer: dict[str, Any]) -> str | None:
    parts = [peer.get("city_name"), peer.get("country_code")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


PEER_SENSORS: tuple[PeerSensorDescription, ...] = (
    PeerSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: parse_time(p.get("last_seen")),
    ),
    PeerSensorDescription(
        key="ip",
        translation_key="ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.get("ip"),
    ),
    PeerSensorDescription(
        key="version",
        translation_key="client_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.get("version"),
    ),
    PeerSensorDescription(
        key="os",
        translation_key="os",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.get("os"),
    ),
    PeerSensorDescription(
        key="connection_ip",
        translation_key="connection_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda p: p.get("connection_ip"),
    ),
    PeerSensorDescription(
        key="location",
        translation_key="location",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_location,
    ),
    PeerSensorDescription(
        key="last_login",
        translation_key="last_login",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda p: parse_time(p.get("last_login")),
    ),
)


class PeerSensor(NetBirdPeerEntity, SensorEntity):
    entity_description: PeerSensorDescription

    def __init__(
        self,
        coordinator: NetBirdCoordinator,
        entry: NetBirdConfigEntry,
        peer_id: str,
        description: PeerSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, peer_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.peer)


# --------------------------------------------------------------------------- #
# Gruppen, Setup-Keys, Netzwerke                                               #
# --------------------------------------------------------------------------- #


class GroupSensor(NetBirdEntity, SensorEntity):
    """Anzahl Peers einer Gruppe."""

    _attr_translation_key = "group_peers"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, group_id: str
    ) -> None:
        super().__init__(coordinator, entry, f"group_{group_id}")
        self.group_id = group_id
        self._attr_translation_placeholders = {"name": self._group.get("name", group_id)}

    @property
    def _group(self) -> dict[str, Any]:
        return self.coordinator.data.groups.get(self.group_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.group_id in self.coordinator.data.groups

    @property
    def native_value(self) -> int:
        group = self._group
        return group.get("peers_count", len(group.get("peers") or []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        group = self._group
        return {
            "group_id": self.group_id,
            "peers": [p.get("name") for p in group.get("peers") or [] if isinstance(p, dict)],
            "resources_count": group.get("resources_count", 0),
        }


class SetupKeySensor(NetBirdEntity, SensorEntity):
    """Zustand eines Setup-Keys (gültig, abgelaufen, widerrufen, aufgebraucht)."""

    _attr_translation_key = "setup_key"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SETUP_KEY_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, key_id: str
    ) -> None:
        super().__init__(coordinator, entry, f"setup_key_{key_id}")
        self.key_id = key_id
        self._attr_translation_placeholders = {"name": self._key.get("name", key_id)}

    @property
    def _key(self) -> dict[str, Any]:
        return (self.coordinator.data.setup_keys or {}).get(self.key_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.key_id in (self.coordinator.data.setup_keys or {})

    @property
    def native_value(self) -> str | None:
        state = self._key.get("state")
        return state if state in SETUP_KEY_STATES else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        key = self._key
        groups = self.coordinator.data.groups
        expires: datetime | None = parse_time(key.get("expires"))
        return {
            "type": key.get("type"),
            "expires": expires.isoformat() if expires else None,
            "used_times": key.get("used_times"),
            "usage_limit": key.get("usage_limit"),
            "last_used": key.get("last_used"),
            "ephemeral": key.get("ephemeral"),
            "auto_groups": [
                (groups.get(g) or {}).get("name", g) if isinstance(g, str) else g.get("name")
                for g in key.get("auto_groups") or []
            ],
        }


class NetworkResourcesSensor(NetBirdNetworkEntity, SensorEntity):
    """Anzahl der Resources eines Netzwerks."""

    _attr_translation_key = "network_resources"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, network_id: str
    ) -> None:
        super().__init__(coordinator, entry, network_id, "resources")

    @property
    def native_value(self) -> int | None:
        network = self.network
        return len(network.resources) if network else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        network = self.network
        if network is None:
            return {}
        return {
            "resources": [
                {"name": r.get("name"), "address": r.get("address"), "enabled": r.get("enabled")}
                for r in network.resources
            ]
        }
