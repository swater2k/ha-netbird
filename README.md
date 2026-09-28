<p align="center">
  <img src="custom_components/netbird/brand/icon@2x.png" alt="NetBird integration icon" width="128">
</p>

<h1 align="center">NetBird for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/swater2k/ha-netbird/releases"><img src="https://img.shields.io/github/v/release/swater2k/ha-netbird" alt="Release"></a>
</p>

A custom integration for a self-hosted [NetBird](https://netbird.io) management server: peer status, routing peers, users, groups, setup keys, token expiry and the audit log — plus optional control of policies, network resources, peers, users, groups and setup keys.

It talks to the documented NetBird REST API with the personal access token of a service user. Nothing is installed on the NetBird host.

> [!NOTE]
> Community project, not affiliated with NetBird GmbH.

## Features

- **Peers**: one device per peer with connection status, last seen, login expiry, NetBird IP, client version and operating system
- **Networks**: one device per network with a routing peer online sensor that also resolves routers defined by peer groups
- **Server**: peers online, users awaiting approval, blocked users, valid setup keys, peers per group, management server update
- **Security**: every new audit log entry becomes a Home Assistant event, peers joining or leaving fire their own events
- **Token expiry**: a repair issue appears before the integration's own access token runs out
- **DNS**: nameserver groups and custom DNS zones with their records
- **Reverse proxy**: status of services published through NetBird's reverse proxy (read-only)
- **Optional control**: switches for policies, network resources, routing peers, nameserver groups and DNS zones, actions for peers, users, groups, setup keys and DNS records — each one turned on separately
- **Stable entity IDs**: new entities are named after the device and entity only, never after the area the device is assigned to
- Endpoints missing on older servers are skipped instead of breaking the integration

## Requirements

- Home Assistant **2026.2** or newer
- A self-hosted NetBird management server (tested against **0.79**)
- A **service user** with a personal access token

## Service user

1. NetBird dashboard → **Team → Service Users → Create Service User**
2. Role **auditor** for monitoring only, **admin** if you want to use control functions
3. Open the service user → **Create Token**, choose the expiry and copy the token

> [!IMPORTANT]
> With the admin role the token can change access policies and remove peers. Store it like a password and turn on only the control functions you need.

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/swater2k/ha-netbird`, type **Integration**
2. Download **NetBird** and restart Home Assistant
3. **Settings → Devices & services → Add integration → NetBird**

Manual alternative: copy `custom_components/netbird` into your `custom_components` folder and restart.

## Setup

| Field | Default | Description |
|---|---|---|
| Management URL | – | Base URL of the dashboard, for example `https://netbird.example.com`. A trailing `/api` is removed. |
| Access token | – | Personal access token of the service user |
| Verify SSL certificate | on | Turn off only for self-signed certificates |

The connection and the token are checked before saving. An invalid token later on starts the re-authentication flow; a new token can also be entered via **Reconfigure**.

### Options

| Option | Default | Description |
|---|---|---|
| Polling interval | `60 s` | How often the server is queried (15–900 s) |
| Token expiry warning | `14 d` | Days before expiry the repair issue appears |
| Audit events | on | Fires `netbird_audit_event` and adds the last audit event sensor |
| Accessible peers per peer | off | Adds a sensor per peer with the number of peers it may reach; one extra request per peer and poll |
| Control: policies | off | One switch per access policy |
| Control: networks | off | Switches for network resources and routing peers |
| Control: peer management | off | Actions approve peer, set login expiration, delete peer |
| Control: user management | off | Actions approve, reject and block user |
| Control: group membership | off | Actions add and remove a peer from a group |
| Control: setup keys | off | Actions create and revoke setup key |
| Control: DNS | off | Switches for nameserver groups and DNS zones, actions set and delete DNS record |

If a control function is on but the service user may not change that area, a repair issue names the missing permissions. Turning an option off removes the entities it added; binary sensors replaced by switches come back.

## Entities

Entities marked ✗ are disabled by default and can be enabled in the entity settings.

### NetBird server

| Entity | Type | Default |
|---|---|---|
| Peers, peers online (offline peers as attribute) | sensor | ✓ |
| Users, users awaiting approval, blocked users (users as attribute) | sensor | ✓ |
| Group *name* — peers per group | sensor | ✓ |
| Valid setup keys | sensor | ✓ |
| Setup key *name* — valid, expired, revoked, used up | sensor (diagnostic) | ✓ |
| Last audit event | sensor | ✓ |
| Token expiration | sensor (timestamp, diagnostic) | ✓ |
| Management server | update | ✓ |
| Nameserver *name* — nameservers, domains and groups as attributes | binary sensor (diagnostic), switch with control option | ✓ |
| DNS zone *name* | binary sensor (diagnostic), switch with control option | ✓ |
| DNS zone *name* records — records as attribute | sensor | ✓ |
| Reverse proxy *name*, reverse proxy *name* status | binary sensor, sensor | ✓ |

### Per peer

| Entity | Type | Default |
|---|---|---|
| Connected | binary sensor (connectivity) | ✓ |
| Login expired | binary sensor (problem) | ✓ |
| Approval required — only if the server supports peer approval | binary sensor (problem) | ✓ |
| Last seen | sensor (timestamp) | ✓ |
| NetBird IP, client version, operating system | sensor (diagnostic) | ✓ |
| Connection IP, location, last login | sensor (diagnostic) | ✗ |
| Accessible peers | sensor (diagnostic) | only with option |

### Per network

| Entity | Type | Default |
|---|---|---|
| Routing peer online (routers as attribute) | binary sensor (connectivity) | ✓ |
| Resources | sensor | ✓ |
| Resource *name*, routing peer *name* | switch | only with control option |

### Policies

| Entity | Type | Default |
|---|---|---|
| Policy *name* | switch | only with control option |

## Events

| Event | Fired when | Data |
|---|---|---|
| `netbird_peer_added` | a peer appears | `peer_id`, `name`, `hostname`, `ip`, `os`, `user_id` |
| `netbird_peer_removed` | a peer disappears | same as above |
| `netbird_audit_event` | a new audit log entry appears | `activity`, `activity_code`, `initiator_name`, `initiator_email`, `target_id`, `meta`, `timestamp` |

Entries that already exist when Home Assistant starts are not fired again.

```yaml
automation:
  - alias: NetBird new peer
    triggers:
      - trigger: event
        event_type: netbird_peer_added
    actions:
      - action: notify.mobile_app_phone
        data:
          title: New NetBird peer
          message: "{{ trigger.event.data.name }} ({{ trigger.event.data.os }})"
```

## Actions

Each group of actions only works if its control option is turned on. The NetBird server field is only needed if several servers are configured.

| Action | Option | Description |
|---|---|---|
| `netbird.approve_peer` | peer management | Approve a peer waiting for approval |
| `netbird.set_login_expiration` | peer management | Turn login expiration of a peer on or off |
| `netbird.delete_peer` | peer management | Remove a peer; it has to register again |
| `netbird.approve_user` / `netbird.reject_user` | user management | Handle users waiting for approval (e-mail, name or ID) |
| `netbird.block_user` | user management | Block or unblock a user |
| `netbird.add_peer_to_group` / `netbird.remove_peer_from_group` | group membership | Change group membership, for example to grant access through a policy |
| `netbird.create_setup_key` | setup keys | Create a setup key; the key is returned in the response |
| `netbird.revoke_setup_key` | setup keys | Revoke a setup key by name or ID |
| `netbird.set_dns_record` | DNS | Create or update an A, AAAA or CNAME record in a DNS zone; short names get the zone domain appended |
| `netbird.delete_dns_record` | DNS | Delete records with a name, optionally only of one type |

```yaml
action: netbird.create_setup_key
data:
  name: laptop-onboarding
  expires_days: 1
  auto_groups:
    - devices
response_variable: setup_key
```

Reverse proxy services are read-only on purpose: the API does not return their authentication secrets, so writing a service back could remove them.

## Removal

1. **Settings → Devices & services → NetBird → ⋮ → Delete**
2. Remove the repository in HACS and restart Home Assistant
3. Delete the service user or its token in the NetBird dashboard

## Troubleshooting

- **"Server not reachable"**: check the URL. Home Assistant has to reach the dashboard address, including the internal DNS name if you use split DNS.
- **"The token was rejected"**: the token is wrong, expired or was deleted.
- **"The token is valid, but may not read peers"**: give the service user at least the auditor role.
- **A control switch fails with a permission error**: the service user needs the admin role.
- **Diagnostics**: Settings → Devices & services → NetBird → ⋮ → Download diagnostics. URL, token, e-mail addresses and public IPs are redacted.

```yaml
logger:
  logs:
    custom_components.netbird: debug
```

## License

[MIT](LICENSE)
