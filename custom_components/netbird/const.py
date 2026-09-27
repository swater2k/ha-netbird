"""Konstanten der NetBird-Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "netbird"

CONF_TOKEN: Final = "token"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_AUDIT_EVENTS: Final = "audit_events"
CONF_TOKEN_WARNING_DAYS: Final = "token_warning_days"

# Steuerfunktionen – jede einzeln über die Optionen freischaltbar.
CONF_CONTROL_POLICIES: Final = "control_policies"
CONF_CONTROL_NETWORKS: Final = "control_networks"
CONF_CONTROL_PEERS: Final = "control_peers"
CONF_CONTROL_USERS: Final = "control_users"
CONF_CONTROL_GROUPS: Final = "control_groups"
CONF_CONTROL_SETUP_KEYS: Final = "control_setup_keys"

CONTROL_OPTIONS: Final = (
    CONF_CONTROL_POLICIES,
    CONF_CONTROL_NETWORKS,
    CONF_CONTROL_PEERS,
    CONF_CONTROL_USERS,
    CONF_CONTROL_GROUPS,
    CONF_CONTROL_SETUP_KEYS,
)

# Welches NetBird-Rechtemodul eine Steuerfunktion braucht.
CONTROL_MODULES: Final = {
    CONF_CONTROL_POLICIES: "policies",
    CONF_CONTROL_NETWORKS: "networks",
    CONF_CONTROL_PEERS: "peers",
    CONF_CONTROL_USERS: "users",
    CONF_CONTROL_GROUPS: "groups",
    CONF_CONTROL_SETUP_KEYS: "setup_keys",
}

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 900
DEFAULT_TOKEN_WARNING_DAYS: Final = 14

EVENT_PEER_ADDED: Final = "netbird_peer_added"
EVENT_PEER_REMOVED: Final = "netbird_peer_removed"
EVENT_AUDIT: Final = "netbird_audit_event"

ISSUE_TOKEN_EXPIRING: Final = "token_expiring"
ISSUE_MISSING_PERMISSIONS: Final = "missing_permissions"

# Optionale Endpunkte: Fehlen sie auf einem älteren Server, wird der Bereich
# einfach ausgelassen statt die ganze Integration scheitern zu lassen.
FEATURE_VERSION: Final = "version"
FEATURE_AUDIT: Final = "audit"
FEATURE_SETUP_KEYS: Final = "setup_keys"
FEATURE_TOKENS: Final = "tokens"
FEATURE_NETWORKS: Final = "networks"
