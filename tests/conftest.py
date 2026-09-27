"""Pytest-Setup."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.netbird.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://netbird.example.com"
API = f"{URL}/api"
ENTRY_DATA = {"url": URL, "token": "nbp_secret", "verify_ssl": True}


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


ENDPOINTS = {
    "/users/current": "current_user",
    "/instance/version": "instance_version",
    "/peers": "peers",
    "/users": "users",
    "/groups": "groups",
    "/policies": "policies",
    "/networks": "networks",
    "/networks/net-home/routers": "network_routers",
    "/networks/net-home/resources": "network_resources",
    "/setup-keys": "setup_keys",
    "/users/u-svc/tokens": "tokens",
    "/events/audit": "audit",
}


def mock_api(
    aioclient_mock: AiohttpClientMocker,
    overrides: dict[str, Any] | None = None,
    status: dict[str, int] | None = None,
) -> None:
    """Registriert alle Lese-Endpunkte; ``overrides`` ersetzt Antworten."""
    aioclient_mock.clear_requests()
    overrides = overrides or {}
    status = status or {}
    for path, fixture in ENDPOINTS.items():
        if path in status:
            aioclient_mock.get(f"{API}{path}", status=status[path])
        else:
            aioclient_mock.get(f"{API}{path}", json=overrides.get(path, load(fixture)))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def options() -> dict[str, Any]:
    return {"scan_interval": 60, "audit_events": True}


@pytest.fixture
def config_entry(options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="NetBird (netbird.example.com)",
        data=ENTRY_DATA,
        unique_id=URL,
        options=options,
    )
