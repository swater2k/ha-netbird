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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NetBirdConfigEntry
from .coordinator import NetBirdCoordinator
from .entity import NetBirdNetworkEntity, NetBirdPeerEntity, track_items

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
                    "peer": (data.peers.get(r.get("peer") or "") or {}).get("name"),
                    "enabled": r.get("enabled"),
                    "online": data.router_online(r),
                    "metric": r.get("metric"),
                }
                for r in network.routers
            ]
        }
