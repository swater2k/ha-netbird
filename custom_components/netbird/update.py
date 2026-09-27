"""Update-Hinweis für den Management-Server (nur Anzeige, keine Installation)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NetBirdConfigEntry
from .coordinator import NetBirdCoordinator
from .entity import NetBirdEntity

PARALLEL_UPDATES = 0

RELEASE_URL = "https://github.com/netbirdio/netbird/releases"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetBirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    if coordinator.data.version is not None:
        async_add_entities([ManagementUpdate(coordinator, entry)])


class ManagementUpdate(NetBirdEntity, UpdateEntity):
    _attr_translation_key = "management"
    _attr_release_url = RELEASE_URL

    def __init__(self, coordinator: NetBirdCoordinator, entry: NetBirdConfigEntry) -> None:
        super().__init__(coordinator, entry, "management_update")

    @property
    def _version(self) -> dict[str, Any]:
        return self.coordinator.data.version or {}

    @property
    def installed_version(self) -> str | None:
        return _clean(self._version.get("management_current_version"))

    @property
    def latest_version(self) -> str | None:
        latest = _clean(self._version.get("management_available_version"))
        if latest and self._version.get("management_update_available"):
            return latest
        return self.installed_version

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"dashboard_available_version": self._version.get("dashboard_available_version")}


def _clean(version: str | None) -> str | None:
    if not version:
        return None
    return version[1:] if version.startswith("v") else version
