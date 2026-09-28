"""Schalter (nur wenn in den Optionen freigegeben)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NetBirdConfigEntry, option_enabled
from .api import NetBirdError, NetBirdPermissionError
from .binary_sensor import dns_attributes, dns_items
from .const import CONF_CONTROL_DNS, CONF_CONTROL_NETWORKS, CONF_CONTROL_POLICIES
from .coordinator import NetBirdCoordinator
from .entity import NetBirdEntity, NetBirdNetworkEntity, track_items

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetBirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator

    if option_enabled(entry, CONF_CONTROL_POLICIES):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.policies.keys(),
            lambda policy_id: [PolicySwitch(coordinator, entry, policy_id)],
        )

    if option_enabled(entry, CONF_CONTROL_NETWORKS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: (f"{nid}/r/{r['id']}" for nid, n in d.networks.items() for r in n.resources),
            lambda item: [ResourceSwitch(coordinator, entry, *_split(item))],
        )
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: (f"{nid}/p/{r['id']}" for nid, n in d.networks.items() for r in n.routers),
            lambda item: [RouterSwitch(coordinator, entry, *_split(item))],
        )

    if option_enabled(entry, CONF_CONTROL_DNS):
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.nameservers.keys(),
            lambda group_id: [DnsItemSwitch(coordinator, entry, "nameserver", group_id)],
        )
        track_items(
            coordinator,
            entry,
            async_add_entities,
            lambda d: d.zones.keys(),
            lambda zone_id: [DnsItemSwitch(coordinator, entry, "zone", zone_id)],
        )


def _split(item: str) -> tuple[str, str]:
    network_id, _, item_id = item.split("/", 2)
    return network_id, item_id


async def _call(coordinator: NetBirdCoordinator, coro) -> None:
    try:
        await coro
    except NetBirdPermissionError as err:
        raise HomeAssistantError(
            "NetBird: the service user lacks permission for this action (role admin required)"
        ) from err
    except NetBirdError as err:
        raise HomeAssistantError(f"NetBird: {err}") from err
    await coordinator.async_request_refresh()


class PolicySwitch(NetBirdEntity, SwitchEntity):
    _attr_translation_key = "policy"

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, policy_id: str
    ) -> None:
        super().__init__(coordinator, entry, f"policy_{policy_id}")
        self.policy_id = policy_id
        self._attr_translation_placeholders = {"name": self._policy.get("name", policy_id)}

    @property
    def _policy(self) -> dict[str, Any]:
        return self.coordinator.data.policies.get(self.policy_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.policy_id in self.coordinator.data.policies

    @property
    def is_on(self) -> bool:
        return bool(self._policy.get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await _call(
            self.coordinator, self.coordinator.client.set_policy_enabled(self.policy_id, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await _call(
            self.coordinator, self.coordinator.client.set_policy_enabled(self.policy_id, False)
        )


class _NetworkItemSwitch(NetBirdNetworkEntity, SwitchEntity):
    _kind: str

    def __init__(
        self,
        coordinator: NetBirdCoordinator,
        entry: NetBirdConfigEntry,
        network_id: str,
        item_id: str,
    ) -> None:
        super().__init__(coordinator, entry, network_id, f"{self._kind}_{item_id}")
        self.item_id = item_id

    def _items(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @property
    def _item(self) -> dict[str, Any] | None:
        if self.network is None:
            return None
        return next((i for i in self._items() if i.get("id") == self.item_id), None)

    @property
    def available(self) -> bool:
        return super().available and self._item is not None

    @property
    def is_on(self) -> bool:
        return bool((self._item or {}).get("enabled"))


class ResourceSwitch(_NetworkItemSwitch):
    _kind = "resource"
    _attr_translation_key = "resource"

    def __init__(self, coordinator, entry, network_id, item_id) -> None:
        super().__init__(coordinator, entry, network_id, item_id)
        item = self._item or {}
        self._attr_translation_placeholders = {"name": item.get("name") or item_id}

    def _items(self) -> list[dict[str, Any]]:
        return self.network.resources

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"address": (self._item or {}).get("address")}

    async def _set(self, enabled: bool) -> None:
        item = self._item
        if item is None:
            raise HomeAssistantError("NetBird: resource no longer exists")
        await _call(
            self.coordinator,
            self.coordinator.client.set_resource_enabled(self.network_id, item, enabled),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class RouterSwitch(_NetworkItemSwitch):
    _kind = "router"
    _attr_translation_key = "router"

    def __init__(self, coordinator, entry, network_id, item_id) -> None:
        super().__init__(coordinator, entry, network_id, item_id)
        item = self._item or {}
        data = coordinator.data
        if item.get("peer"):
            name = (data.peers.get(item["peer"]) or {}).get("name") or item["peer"]
        else:
            names = []
            for group in item.get("peer_groups") or []:
                group_id = group.get("id") if isinstance(group, dict) else group
                names.append((data.groups.get(group_id) or {}).get("name", group_id))
            name = ", ".join(names) or item_id
        self._attr_translation_placeholders = {"name": name}

    def _items(self) -> list[dict[str, Any]]:
        return self.network.routers

    async def _set(self, enabled: bool) -> None:
        item = self._item
        if item is None:
            raise HomeAssistantError("NetBird: router no longer exists")
        await _call(
            self.coordinator,
            self.coordinator.client.set_router_enabled(self.network_id, item, enabled),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class DnsItemSwitch(NetBirdEntity, SwitchEntity):
    """Nameserver-Gruppe oder DNS-Zone ein- und ausschalten."""

    def __init__(
        self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry, kind: str, item_id: str
    ) -> None:
        super().__init__(coordinator, entry, f"{kind}_{item_id}_switch")
        self.kind = kind
        self.item_id = item_id
        self._attr_translation_key = kind
        self._attr_translation_placeholders = {
            "name": dns_items(coordinator, kind).get(item_id, {}).get("name", item_id)
        }

    @property
    def _item(self) -> dict[str, Any] | None:
        return dns_items(self.coordinator, self.kind).get(self.item_id)

    @property
    def available(self) -> bool:
        return super().available and self._item is not None

    @property
    def is_on(self) -> bool:
        return bool((self._item or {}).get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dns_attributes(self._item or {}, self.kind, self.coordinator.data.groups)

    async def _set(self, enabled: bool) -> None:
        item = self._item
        if item is None:
            raise HomeAssistantError("NetBird: DNS entry no longer exists")
        client = self.coordinator.client
        call = (
            client.set_nameserver_group_enabled(item, enabled)
            if self.kind == "nameserver"
            else client.set_zone_enabled(item, enabled)
        )
        await _call(self.coordinator, call)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
