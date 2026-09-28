"""Schlanker asynchroner Client für die NetBird-Management-API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class NetBirdError(Exception):
    """Basisfehler."""


class NetBirdConnectionError(NetBirdError):
    """Server nicht erreichbar oder Antwort unbrauchbar."""


class NetBirdAuthError(NetBirdError):
    """Token ungültig oder abgelaufen (HTTP 401)."""


class NetBirdPermissionError(NetBirdError):
    """Token gültig, aber die Rolle darf das nicht (HTTP 403)."""


class NetBirdNotFoundError(NetBirdError):
    """Endpunkt oder Objekt existiert nicht (HTTP 404)."""


class NetBirdRequestError(NetBirdError):
    """Server lehnt die Anfrage ab (HTTP 4xx/5xx)."""


def normalize_url(url: str) -> str:
    """``https://nb.example.com/api/`` → ``https://nb.example.com``."""
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path.endswith("/api"):
        path = path[: -len("/api")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def ids(items: list[Any] | None) -> list[str]:
    """Die API liefert Referenzen als Objekte, erwartet beim Schreiben aber IDs."""
    result: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            if item.get("id"):
                result.append(item["id"])
        elif item:
            result.append(str(item))
    return result


class NetBirdClient:
    """Dünne Hülle um die REST-Endpunkte, die die Integration braucht."""

    def __init__(
        self, session: aiohttp.ClientSession, url: str, token: str, verify_ssl: bool = True
    ) -> None:
        self._session = session
        self.url = normalize_url(url)
        self._headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        }
        self._ssl = None if verify_ssl else False

    async def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.url}/api{path}",
                headers=self._headers,
                json=payload,
                ssl=self._ssl,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise NetBirdAuthError("Token ungültig oder abgelaufen")
                if resp.status == 403:
                    raise NetBirdPermissionError(f"Keine Berechtigung für {method} {path}")
                if resp.status == 404:
                    raise NetBirdNotFoundError(f"{path} nicht gefunden")
                if resp.status >= 400:
                    raise NetBirdRequestError(
                        f"{method} {path}: HTTP {resp.status} {await _message(resp)}"
                    )
                body = await resp.text()
                if resp.status == 204 or not body.strip():
                    return None
                return json.loads(body)
        except NetBirdError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise NetBirdConnectionError(f"{method} {path}: {err}") from err

    # --- Lesen ------------------------------------------------------------ #

    async def current_user(self) -> dict[str, Any]:
        return await self._request("GET", "/users/current")

    async def instance_version(self) -> dict[str, Any]:
        return await self._request("GET", "/instance/version")

    async def peers(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/peers") or []

    async def users(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/users") or []

    async def groups(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/groups") or []

    async def group(self, group_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/groups/{group_id}")

    async def policies(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/policies") or []

    async def policy(self, policy_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/policies/{policy_id}")

    async def networks(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/networks") or []

    async def network_routers(self, network_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/networks/{network_id}/routers") or []

    async def network_resources(self, network_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/networks/{network_id}/resources") or []

    async def setup_keys(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/setup-keys") or []

    async def tokens(self, user_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/users/{user_id}/tokens") or []

    async def audit_events(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/events/audit") or []

    async def nameserver_groups(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/dns/nameservers") or []

    async def dns_zones(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/dns/zones") or []

    async def reverse_proxy_services(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/reverse-proxies/services") or []

    async def accessible_peers(self, peer_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/peers/{peer_id}/accessible-peers") or []

    # --- Schreiben ---------------------------------------------------------- #

    async def set_policy_enabled(self, policy_id: str, enabled: bool) -> dict[str, Any]:
        """Policy komplett neu schreiben – die API kennt kein Teil-Update."""
        policy = await self.policy(policy_id)
        rules = []
        for rule in policy.get("rules", []):
            new_rule = {
                key: rule[key]
                for key in (
                    "id",
                    "name",
                    "description",
                    "enabled",
                    "action",
                    "bidirectional",
                    "protocol",
                    "ports",
                    "port_ranges",
                    "authorized_groups",
                    "sourceResource",
                    "destinationResource",
                )
                if rule.get(key) is not None
            }
            if rule.get("sources") is not None:
                new_rule["sources"] = ids(rule["sources"])
            if rule.get("destinations") is not None:
                new_rule["destinations"] = ids(rule["destinations"])
            rules.append(new_rule)
        payload = {
            "name": policy["name"],
            "description": policy.get("description", ""),
            "enabled": enabled,
            "source_posture_checks": ids(policy.get("source_posture_checks")),
            "rules": rules,
        }
        return await self._request("PUT", f"/policies/{policy_id}", payload)

    async def set_router_enabled(
        self, network_id: str, router: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "metric": router.get("metric", 9999),
            "masquerade": router.get("masquerade", True),
            "enabled": enabled,
        }
        if router.get("peer"):
            payload["peer"] = router["peer"]
        if router.get("peer_groups"):
            payload["peer_groups"] = ids(router["peer_groups"])
        return await self._request("PUT", f"/networks/{network_id}/routers/{router['id']}", payload)

    async def set_resource_enabled(
        self, network_id: str, resource: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        payload = {
            "name": resource["name"],
            "description": resource.get("description", ""),
            "address": resource["address"],
            "enabled": enabled,
            "groups": ids(resource.get("groups")),
        }
        return await self._request(
            "PUT", f"/networks/{network_id}/resources/{resource['id']}", payload
        )

    async def update_peer(self, peer: dict[str, Any], **changes: Any) -> dict[str, Any]:
        payload = {
            "name": peer["name"],
            "ssh_enabled": peer.get("ssh_enabled", False),
            "login_expiration_enabled": peer.get("login_expiration_enabled", False),
            "inactivity_expiration_enabled": peer.get("inactivity_expiration_enabled", False),
        }
        if "approval_required" in peer:
            payload["approval_required"] = peer["approval_required"]
        payload.update(changes)
        return await self._request("PUT", f"/peers/{peer['id']}", payload)

    async def delete_peer(self, peer_id: str) -> None:
        await self._request("DELETE", f"/peers/{peer_id}")

    async def approve_user(self, user_id: str) -> None:
        await self._request("POST", f"/users/{user_id}/approve")

    async def reject_user(self, user_id: str) -> None:
        await self._request("DELETE", f"/users/{user_id}/reject")

    async def set_user_blocked(self, user: dict[str, Any], blocked: bool) -> dict[str, Any]:
        payload = {
            "role": user["role"],
            "auto_groups": ids(user.get("auto_groups")),
            "is_blocked": blocked,
        }
        return await self._request("PUT", f"/users/{user['id']}", payload)

    async def set_group_peers(self, group_id: str, peer_ids: list[str]) -> dict[str, Any]:
        group = await self.group(group_id)
        payload: dict[str, Any] = {"name": group["name"], "peers": peer_ids}
        if group.get("resources"):
            payload["resources"] = [
                {"id": res["id"], "type": res["type"]}
                for res in group["resources"]
                if isinstance(res, dict) and res.get("id")
            ]
        return await self._request("PUT", f"/groups/{group_id}", payload)

    async def set_nameserver_group_enabled(
        self, group: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        payload = {
            "name": group["name"],
            "description": group.get("description", ""),
            "nameservers": group.get("nameservers") or [],
            "enabled": enabled,
            "groups": ids(group.get("groups")),
            "primary": group.get("primary", False),
            "domains": group.get("domains") or [],
            "search_domains_enabled": group.get("search_domains_enabled", False),
        }
        return await self._request("PUT", f"/dns/nameservers/{group['id']}", payload)

    async def set_zone_enabled(self, zone: dict[str, Any], enabled: bool) -> dict[str, Any]:
        payload = {
            "name": zone["name"],
            "domain": zone["domain"],
            "enabled": enabled,
            "enable_search_domain": zone.get("enable_search_domain", False),
            "distribution_groups": ids(zone.get("distribution_groups")),
        }
        return await self._request("PUT", f"/dns/zones/{zone['id']}", payload)

    async def create_dns_record(self, zone_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/dns/zones/{zone_id}/records", record)

    async def update_dns_record(
        self, zone_id: str, record_id: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request("PUT", f"/dns/zones/{zone_id}/records/{record_id}", record)

    async def delete_dns_record(self, zone_id: str, record_id: str) -> None:
        await self._request("DELETE", f"/dns/zones/{zone_id}/records/{record_id}")

    async def create_setup_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/setup-keys", payload)

    async def revoke_setup_key(self, key: dict[str, Any]) -> dict[str, Any]:
        payload = {"revoked": True, "auto_groups": ids(key.get("auto_groups"))}
        return await self._request("PUT", f"/setup-keys/{key['id']}", payload)


async def _message(resp: aiohttp.ClientResponse) -> str:
    try:
        body = await resp.json(content_type=None)
    except (aiohttp.ClientError, ValueError):
        return ""
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or "")
    return ""
