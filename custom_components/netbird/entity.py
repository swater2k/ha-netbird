"""Basis-Entitäten und Helfer für dynamisch auftauchende Objekte."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NetBirdCoordinator, NetBirdData, network_device_id, peer_device_id

if TYPE_CHECKING:
    from . import NetBirdConfigEntry


def server_device(entry: NetBirdConfigEntry, data: NetBirdData | None) -> DeviceInfo:
    version = None
    if data is not None and data.version:
        version = data.version.get("management_current_version")
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="NetBird",
        manufacturer="NetBird",
        model="Management server",
        sw_version=version,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.runtime_data.coordinator.client.url,
    )


class NetBirdEntity(CoordinatorEntity[NetBirdCoordinator]):
    """Entität am Server-Gerät."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = server_device(entry, coordinator.data)


class NetBirdPeerEntity(CoordinatorEntity[NetBirdCoordinator]):
    """Entität an einem Peer-Gerät."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, peer_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.peer_id = peer_id
        self._attr_unique_id = f"{entry.entry_id}_peer_{peer_id}_{key}"
        peer = coordinator.data.peers.get(peer_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, peer_device_id(entry.entry_id, peer_id))},
            name=peer.get("name") or peer.get("hostname") or peer_id,
            manufacturer="NetBird",
            model=peer.get("os") or "Peer",
            sw_version=peer.get("version"),
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def peer(self) -> dict[str, Any]:
        return self.coordinator.data.peers.get(self.peer_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.peer_id in self.coordinator.data.peers


class NetBirdNetworkEntity(CoordinatorEntity[NetBirdCoordinator]):
    """Entität an einem Netzwerk-Gerät."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NetBirdCoordinator,
        entry: NetBirdConfigEntry,
        network_id: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.network_id = network_id
        self._attr_unique_id = f"{entry.entry_id}_network_{network_id}_{key}"
        network = coordinator.data.networks[network_id].network
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, network_device_id(entry.entry_id, network_id))},
            name=f"NetBird {network.get('name') or network_id}",
            manufacturer="NetBird",
            model="Network",
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def network(self):
        return self.coordinator.data.networks.get(self.network_id)

    @property
    def available(self) -> bool:
        return super().available and self.network_id in self.coordinator.data.networks


@callback
def track_items(
    coordinator: NetBirdCoordinator,
    entry: NetBirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    get_ids: Callable[[NetBirdData], Iterable[str]],
    create: Callable[[str], list[Entity]],
) -> None:
    """Legt Entitäten für neue Objekte an und räumt verschwundene auf."""
    known: dict[str, list[Entity]] = {}

    @callback
    def _sync() -> None:
        if coordinator.data is None:
            return
        current = set(get_ids(coordinator.data))
        new: list[Entity] = []
        for item_id in current - known.keys():
            entities = create(item_id)
            known[item_id] = entities
            new.extend(entities)
        if new:
            async_add_entities(new)
        registry = er.async_get(coordinator.hass)
        for item_id in known.keys() - current:
            for entity in known.pop(item_id):
                if entity.entity_id and registry.async_get(entity.entity_id):
                    registry.async_remove(entity.entity_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
