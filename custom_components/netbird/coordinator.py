"""DataUpdateCoordinator für NetBird."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    NetBirdAuthError,
    NetBirdClient,
    NetBirdError,
    NetBirdNotFoundError,
    NetBirdPermissionError,
)
from .const import (
    CONF_AUDIT_EVENTS,
    CONF_TOKEN_WARNING_DAYS,
    CONTROL_MODULES,
    DEFAULT_TOKEN_WARNING_DAYS,
    DOMAIN,
    EVENT_AUDIT,
    EVENT_PEER_ADDED,
    EVENT_PEER_REMOVED,
    FEATURE_AUDIT,
    FEATURE_NETWORKS,
    FEATURE_SETUP_KEYS,
    FEATURE_TOKENS,
    FEATURE_VERSION,
    ISSUE_MISSING_PERMISSIONS,
    ISSUE_TOKEN_EXPIRING,
)

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def parse_time(value: Any) -> datetime | None:
    """NetBird liefert RFC3339; Nullwerte kommen als ``0001-01-01…``."""
    if not value or not isinstance(value, str) or value.startswith("0001-"):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def peer_device_id(entry_id: str, peer_id: str) -> str:
    return f"{entry_id}_peer_{peer_id}"


def network_device_id(entry_id: str, network_id: str) -> str:
    return f"{entry_id}_network_{network_id}"


@dataclass(slots=True)
class NetworkData:
    network: dict[str, Any]
    routers: list[dict[str, Any]]
    resources: list[dict[str, Any]]


@dataclass(slots=True)
class NetBirdData:
    current_user: dict[str, Any]
    peers: dict[str, dict[str, Any]]
    users: list[dict[str, Any]]
    groups: dict[str, dict[str, Any]]
    policies: dict[str, dict[str, Any]]
    networks: dict[str, NetworkData] = field(default_factory=dict)
    version: dict[str, Any] | None = None
    setup_keys: dict[str, dict[str, Any]] | None = None
    tokens: list[dict[str, Any]] | None = None
    last_audit_event: dict[str, Any] | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def permissions(self) -> dict[str, dict[str, bool]]:
        return (self.current_user.get("permissions") or {}).get("modules") or {}

    @property
    def token_expiration(self) -> datetime | None:
        dates = [parse_time(t.get("expiration_date")) for t in self.tokens or []]
        dates = [d for d in dates if d is not None]
        return min(dates) if dates else None

    def peer_connected(self, peer_id: str | None) -> bool:
        return bool(peer_id and (self.peers.get(peer_id) or {}).get("connected"))

    def router_online(self, router: dict[str, Any]) -> bool:
        """Ein Router ist online, wenn er aktiv ist und mind. ein Peer verbunden."""
        if not router.get("enabled", True):
            return False
        if self.peer_connected(router.get("peer")):
            return True
        for group in router.get("peer_groups") or []:
            group_id = group.get("id") if isinstance(group, dict) else group
            members = (self.groups.get(group_id) or {}).get("peers") or []
            for member in members:
                member_id = member.get("id") if isinstance(member, dict) else member
                if self.peer_connected(member_id):
                    return True
        return False


class NetBirdCoordinator(DataUpdateCoordinator[NetBirdData]):
    """Liest den Zustand des Management-Servers und leitet Ereignisse ab."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NetBirdClient,
        interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.unsupported: set[str] = set()
        self._known_peers: dict[str, dict[str, Any]] | None = None
        self._known_networks: set[str] | None = None
        self._seen_audit: set[str] | None = None
        self._last_audit: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #

    async def _optional(self, feature: str, call: Awaitable[T]) -> T | None:
        """Optionaler Bereich: 404 merkt ihn als nicht unterstützt, 403 überspringt."""
        if feature in self.unsupported:
            if asyncio.iscoroutine(call):
                call.close()
            return None
        try:
            return await call
        except NetBirdNotFoundError:
            _LOGGER.info("NetBird-Server kennt %s nicht, Bereich wird ausgelassen", feature)
            self.unsupported.add(feature)
        except NetBirdPermissionError:
            _LOGGER.debug("Keine Leserechte für %s", feature)
        return None

    async def _async_update_data(self) -> NetBirdData:
        try:
            peers, groups, policies = await asyncio.gather(
                self.client.peers(),
                self.client.groups(),
                self.client.policies(),
            )
            # Ohne eigenen Benutzer fehlen nur Rechteprüfung und Token-Ablauf.
            try:
                current_user = await self.client.current_user()
            except (NetBirdNotFoundError, NetBirdPermissionError):
                current_user = {}
            try:
                users = await self.client.users()
            except NetBirdPermissionError:
                users = []
            version, networks, setup_keys = await asyncio.gather(
                self._optional(FEATURE_VERSION, self.client.instance_version()),
                self._optional(FEATURE_NETWORKS, self._fetch_networks()),
                self._optional(FEATURE_SETUP_KEYS, self.client.setup_keys()),
            )
            tokens = None
            if current_user.get("id"):
                tokens = await self._optional(
                    FEATURE_TOKENS, self.client.tokens(current_user["id"])
                )
            audit = None
            if self.config_entry.options.get(CONF_AUDIT_EVENTS, True):
                audit = await self._optional(FEATURE_AUDIT, self.client.audit_events())
        except NetBirdAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NetBirdError as err:
            raise UpdateFailed(str(err)) from err

        data = NetBirdData(
            current_user=current_user,
            peers={p["id"]: p for p in peers if p.get("id")},
            users=users,
            groups={g["id"]: g for g in groups if g.get("id")},
            policies={p["id"]: p for p in policies if p.get("id")},
            networks=networks or {},
            version=version,
            setup_keys={k["id"]: k for k in setup_keys if k.get("id")}
            if setup_keys is not None
            else None,
            tokens=tokens,
        )
        self._process_peers(data)
        self._process_networks(data)
        if audit is not None:
            self._process_audit(audit)
        data.last_audit_event = self._last_audit
        self._update_issues(data)
        return data

    async def _fetch_networks(self) -> dict[str, NetworkData]:
        networks = await self.client.networks()

        async def _load(net: dict[str, Any]) -> NetworkData:
            routers, resources = await asyncio.gather(
                self.client.network_routers(net["id"]),
                self.client.network_resources(net["id"]),
            )
            return NetworkData(net, routers, resources)

        loaded = await asyncio.gather(*(_load(n) for n in networks if n.get("id")))
        return {item.network["id"]: item for item in loaded}

    # --- Ableitungen ----------------------------------------------------- #

    def _process_peers(self, data: NetBirdData) -> None:
        current = data.peers
        if self._known_peers is not None:
            for peer_id in current.keys() - self._known_peers.keys():
                self.hass.bus.async_fire(EVENT_PEER_ADDED, self._peer_event(current[peer_id]))
            for peer_id in self._known_peers.keys() - current.keys():
                self.hass.bus.async_fire(
                    EVENT_PEER_REMOVED, self._peer_event(self._known_peers[peer_id])
                )
                self._remove_device(peer_device_id(self.config_entry.entry_id, peer_id))
        self._known_peers = dict(current)

    def _process_networks(self, data: NetBirdData) -> None:
        if FEATURE_NETWORKS in self.unsupported:
            return
        current = set(data.networks)
        if self._known_networks is not None:
            for network_id in self._known_networks - current:
                self._remove_device(network_device_id(self.config_entry.entry_id, network_id))
        self._known_networks = current

    def _peer_event(self, peer: dict[str, Any]) -> dict[str, Any]:
        return {
            "config_entry_id": self.config_entry.entry_id,
            "peer_id": peer.get("id"),
            "name": peer.get("name"),
            "hostname": peer.get("hostname"),
            "ip": peer.get("ip"),
            "os": peer.get("os"),
            "user_id": peer.get("user_id"),
        }

    def _remove_device(self, identifier: str) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, identifier)})
        if device is not None:
            registry.async_update_device(
                device.id, remove_config_entry_id=self.config_entry.entry_id
            )

    def _process_audit(self, events: list[dict[str, Any]]) -> None:
        events = sorted(
            (e for e in events if e.get("id")),
            key=lambda e: parse_time(e.get("timestamp")) or datetime.min.replace(tzinfo=UTC),
        )
        ids = {e["id"] for e in events}
        if self._seen_audit is not None:
            # Erster Lauf ist nur die Grundlinie – alte Ereignisse nicht erneut feuern.
            for event in events:
                if event["id"] not in self._seen_audit:
                    self.hass.bus.async_fire(EVENT_AUDIT, self._audit_payload(event))
        self._seen_audit = ids
        if events:
            self._last_audit = events[-1]

    def _audit_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "config_entry_id": self.config_entry.entry_id,
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "activity": event.get("activity"),
            "activity_code": event.get("activity_code"),
            "initiator_name": event.get("initiator_name"),
            "initiator_email": event.get("initiator_email"),
            "target_id": event.get("target_id"),
            "meta": event.get("meta") or {},
        }

    # --- Reparaturhinweise ------------------------------------------------- #

    def _update_issues(self, data: NetBirdData) -> None:
        entry = self.config_entry
        token_issue = f"{ISSUE_TOKEN_EXPIRING}_{entry.entry_id}"
        expires = data.token_expiration
        warn_days = entry.options.get(CONF_TOKEN_WARNING_DAYS, DEFAULT_TOKEN_WARNING_DAYS)
        if expires is not None and expires - dt_util.utcnow() < timedelta(days=warn_days):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                token_issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_TOKEN_EXPIRING,
                translation_placeholders={
                    "title": entry.title,
                    "date": dt_util.as_local(expires).strftime("%d.%m.%Y"),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, token_issue)

        perm_issue = f"{ISSUE_MISSING_PERMISSIONS}_{entry.entry_id}"
        modules = data.permissions
        missing = sorted(
            module
            for option, module in CONTROL_MODULES.items()
            if entry.options.get(option, False)
            and modules
            and not (modules.get(module) or {}).get("update", False)
        )
        if missing:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                perm_issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_MISSING_PERMISSIONS,
                translation_placeholders={"title": entry.title, "modules": ", ".join(missing)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, perm_issue)
