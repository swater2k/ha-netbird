"""NetBird-Integration für Home Assistant (selbst gehosteter Management-Server)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import NetBirdClient
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_MISSING_PERMISSIONS,
    ISSUE_TOKEN_EXPIRING,
)
from .coordinator import NetBirdCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class NetBirdRuntimeData:
    coordinator: NetBirdCoordinator
    server_device_id: str


type NetBirdConfigEntry = ConfigEntry[NetBirdRuntimeData]


def build_client(hass: HomeAssistant, data: dict) -> NetBirdClient:
    verify_ssl = data.get(CONF_VERIFY_SSL, True)
    return NetBirdClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        data[CONF_URL],
        data[CONF_TOKEN],
        verify_ssl,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NetBirdConfigEntry) -> bool:
    client = build_client(hass, dict(entry.data))
    coordinator = NetBirdCoordinator(
        hass, entry, client, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    await coordinator.async_config_entry_first_refresh()

    # Das Server-Gerät muss existieren, bevor Peers und Netzwerke darauf verweisen.
    server = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="NetBird",
        manufacturer="NetBird",
        model="Management server",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    entry.runtime_data = NetBirdRuntimeData(coordinator, server.id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NetBirdConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NetBirdConfigEntry) -> None:
    for issue in (ISSUE_TOKEN_EXPIRING, ISSUE_MISSING_PERMISSIONS):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NetBirdConfigEntry, device
) -> bool:
    """Geräte dürfen manuell entfernt werden, sobald NetBird sie nicht mehr kennt."""
    data = entry.runtime_data.coordinator.data
    for _, identifier in device.identifiers:
        if identifier == entry.entry_id:
            return False
        for peer_id in data.peers:
            if identifier.endswith(f"_peer_{peer_id}"):
                return False
        for network_id in data.networks:
            if identifier.endswith(f"_network_{network_id}"):
                return False
    return True


def option_enabled(entry: NetBirdConfigEntry, option: str) -> bool:
    return bool(entry.options.get(option, False))
