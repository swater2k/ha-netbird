"""Binärsensoren: Peer-Verbindung, Login-Ablauf, Freigabe, Routing-Peers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NetBirdConfigEntry, option_enabled
from .const import CONF_CONTROL_DNS
from .coordinator import NetBirdCoordinator
from .entity import NetBirdEntity, NetBirdNetworkEntity, NetBirdPeerEntity, track_items

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PeerBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True


PEER_BINARY_SENSORS: tuple[PeerBinaryDescription, ...] = (
    PeerBinaryDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda p: bool(p.get("connected")),
    ),
    PeerBinaryDescription(
        key="login_expired",
        translation_key="login_expired",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda p: bool(p.get("login_expired")),
    ),
    PeerBinaryDescription(
        key="approval_required",
        translation_key="approval_required",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda p: p.get("approval_required"),
        # Peer-Freigabe gibt es nicht auf jedem Server – dann kein Sensor.
        exists_fn=lambda p: "approval_required" in p,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetBirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator

    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda data: data.peers.keys(),
        lambda peer_id: [
            PeerBinarySensor(coordinator, entry, peer_id, desc)
            for desc in PEER_BINARY_SENSORS
            if desc.exists_fn(coordinator.data.peers.get(peer_id, {}))
        ],
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda data: data.networks.keys(),
        lambda network_id: [RoutingPeerOnline(coordinator, entry, network_id)],
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda data: data.services.keys(),
        lambda service_id: [ServiceEnabled(coordinator, entry, service_id)],
    )
    # Mit freigegebener DNS-Steuerung übernehmen Schalter diese Rolle.
    if not option_enabled(entry, CONF_CONTROL_DNS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda data: data.nameservers.keys(),
            lambda group_id: [DnsItemEnabled(coordinator, entry, "nameserver", group_id)],
        )
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda data: data.zones.keys(),
            lambda zone_id: [DnsItemEnabled(coordinator, entry, "zone", zone_id)],
        )


class PeerBinarySensor(NetBirdPeerEntity, BinarySensorEntity):
    entity_description: PeerBinaryDescription

    def __init__(
        self,
        coordinator: NetBirdCoordinator,
        entry: NetBirdConfigEntry,
        peer_id: str,
        description: PeerBinaryDescription,
    ) -> None:
        super().__init__(coordinator, entry, peer_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.peer)


class RoutingPeerOnline(NetBirdNetworkEntity, BinarySensorEntity):
    """An, sobald mindestens ein aktiver Routing-Peer des Netzwerks verbunden ist."""

    _attr_translation_key = "routing_peer_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, network_id: str
    ) -> None:
        super().__init__(coordinator, entry, network_id, "routing_peer_online")

    @property
    def is_on(self) -> bool | None:
        network = self.network
        if network is None:
            return None
        return any(self.coordinator.data.router_online(r) for r in network.routers)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        network = self.network
        if network is None:
            return {}
        data = self.coordinator.data
        return {
            "routers": [
                {
                    "peers": data.router_peer_names(r),
                    "groups": data.router_group_names(r),
                    "enabled": r.get("enabled"),
                    "online": data.router_online(r),
                    "metric": r.get("metric"),
                }
                for r in network.routers
            ]
        }


def dns_items(coordinator: NetBirdCoordinator, kind: str) -> dict[str, dict[str, Any]]:
    data = coordinator.data
    return data.nameservers if kind == "nameserver" else data.zones


def dns_attributes(item: dict[str, Any], kind: str, groups: dict[str, Any]) -> dict[str, Any]:
    group_ids = item.get("groups") if kind == "nameserver" else item.get("distribution_groups")
    attrs: dict[str, Any] = {
        "groups": [(groups.get(g) or {}).get("name", g) for g in group_ids or []],
    }
    if kind == "nameserver":
        attrs["nameservers"] = [
            f"{ns.get('ip')}:{ns.get('port')}" for ns in item.get("nameservers") or []
        ]
        attrs["domains"] = item.get("domains") or []
        attrs["primary"] = item.get("primary")
    else:
        attrs["domain"] = item.get("domain")
    return attrs


class DnsItemEnabled(NetBirdEntity, BinarySensorEntity):
    """Nameserver-Gruppe oder DNS-Zone aktiv (nur lesend)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, kind: str, item_id: str
    ) -> None:
        super().__init__(coordinator, entry, f"{kind}_{item_id}")
        self.kind = kind
        self.item_id = item_id
        self._attr_translation_key = kind
        self._attr_translation_placeholders = {
            "name": dns_items(coordinator, kind).get(item_id, {}).get("name", item_id)
        }

    @property
    def available(self) -> bool:
        return super().available and self.item_id in dns_items(self.coordinator, self.kind)

    @property
    def is_on(self) -> bool:
        return bool(dns_items(self.coordinator, self.kind).get(self.item_id, {}).get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = dns_items(self.coordinator, self.kind).get(self.item_id, {})
        return dns_attributes(item, self.kind, self.coordinator.data.groups)


class ServiceEnabled(NetBirdEntity, BinarySensorEntity):
    """Dienst des NetBird-Reverse-Proxys aktiv (nur lesend)."""

    _attr_translation_key = "service"

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, service_id: str
    ) -> None:
        super().__init__(coordinator, entry, f"service_{service_id}")
        self.service_id = service_id
        service = coordinator.data.services.get(service_id, {})
        self._attr_translation_placeholders = {"name": service.get("name") or service_id}

    @property
    def available(self) -> bool:
        return super().available and self.service_id in self.coordinator.data.services

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.services.get(self.service_id, {}).get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        service = self.coordinator.data.services.get(self.service_id, {})
        return {"domain": service.get("domain"), "mode": service.get("mode")}
