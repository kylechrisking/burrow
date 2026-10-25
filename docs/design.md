# Burrow — design

## What it is

A homelab dashboard you point at your Proxmox cluster and your Docker hosts.
It finds the nodes, the guests, the containers and the apps behind their ports
on its own, and shows each one with its live status and a few numbers that matter.
You do not write a service list.

## Why another dashboard

| | Homepage | Homarr | Dashy | **burrow** |
|---|---|---|---|---|
| How services get on the page | YAML per service, or Docker labels on each container | Drag-and-drop UI, stored in its DB | YAML/UI per item | **Discovered**: Proxmox guests + Docker ports + fingerprinting |
| Proxmox | One widget showing totals | Integration widget | Status-check only | Cluster is the backbone: nodes, guests, storage, and the guest IPs that drive probing |
| App stats | ~150 widgets, each configured by hand | Integrations, configured in UI | Few | Turn on automatically for apps it recognises; API keys are optional extras |
| Config | Many YAML files | Database + UI | One big YAML | Optional: one small TOML file plus a secrets file |
| Runtime | Next.js | Next.js + DB | Vue + Node | Python stdlib, no dependencies, one container |

Homepage and Homarr are excellent once configured. The gap is the first ten
minutes and every change after that. In a lab built from Proxmox and Docker,
most of what a dashboard needs is already known by the Proxmox API, the Docker
API, and the apps themselves. Kyle's current Homepage has 40 hand-written service
entries and about 1,100 lines of custom CSS and JS. Every one of those service
entries could have been discovered.

**Non-goals**: bookmarks, search bars, weather, iframes, user accounts,
editing in the UI, and controlling anything. The dashboard only reads.

## Architecture

```
            ┌──────────────── one container ─────────────────┐
            │                                                │
 Proxmox ◄──┤ providers/proxmox.py ─┐                        │
 API token  │                       │  Inventory             │
            │                       ├─► hosts + endpoints ─┐ │
 Docker  ◄──┤ providers/docker.py ──┤                      │ │
 socket(s)  │                       │                      ▼ │
            │ providers/static.py ──┘          engine: identify (fingerprint)
 extra IPs  │                                        │       │
            │                                        ▼       │
   apps  ◄──┤ apps/*.py  ◄──── check loop (15 s) ── services │
 (HTTP/TCP) │                                        │       │
            │ server.py  GET /api/state ◄────────────┘       │
            │            static web/ (vanilla JS, themes)    │
            └────────────────────────────────────────────────┘
```

Three loops run in background threads:

1. **Providers** each run on their own interval and return an *Inventory*.
   That is a list of **hosts** (nodes, VMs, containers, Docker engines) and
   **endpoints** (an address and port, plus a hint such as the image name).
2. **Identify** runs when the set of hosts or endpoints changes, and every few
   minutes anyway. It merges hosts from different providers into one: a Docker
   engine called `ai` *is* the Proxmox guest `ai`. It then gives every guest
   with an IP the app's well-known ports, does a TCP connect, and asks each
   app's fingerprint whether it recognises the answer.
3. **Check** re-probes each identified service every 15 seconds and collects
   its stats.

The browser polls `GET /api/state` every few seconds and renders it. There is
no database. State lives in memory and is rebuilt within a minute of a restart.

### Why this stack

- **Python standard library only.** `http.server`, `urllib`, `ssl`, `socket`,
  `tomllib` and `concurrent.futures` cover everything. There is no `pip install`.
  The image is `python:3.13-alpine` plus about 100 KB of code. Contributors can
  read the whole thing in an afternoon.
- **Vanilla JS, no build step.** One `app.js` file renders the state with
  template literals. Themes are plain CSS. Anyone can fork it and edit it in place.
- **Threads, not asyncio.** Probing is I/O-bound and bounded. A pool of 64
  threads probes a small lab in a second or two, and the code stays sequential
  and easy to follow.

Each of these can be revisited if it stops earning its place. A Go rewrite
would give a single static binary, and that is the obvious next step if the
project grows.

## Discovery providers

A provider is one self-contained module in `burrow/providers/`. It never
writes to the thing it reads.

```python
class Provider:
    name: str = "example"          # shown in the footer, used as id prefix
    interval: float = 30           # seconds between discover() calls

    @classmethod
    def from_config(cls, cfg: Config) -> "list[Provider] | None":
        """Return zero or more instances (Docker returns one per engine).
        Auto-detection lives here (e.g. Docker: is /var/run/docker.sock mounted?)."""

    def discover(self) -> Inventory:
        """Read the source and return what exists right now.
        Raise on failure; the engine keeps the last good inventory and
        shows the error in the provider status strip."""
```

```python
@dataclass
class Inventory:
    hosts: list[Host]          # id, name, kind, status, address, parent, metrics, storage, containers
    endpoints: list[Endpoint]  # host_id, port, hint (image/container name), container, labels
```

Shipped providers:

| Provider | Turns on when | Reads |
|---|---|---|
| `proxmox` | `[proxmox] url` and a token file exist | `/cluster/resources` (nodes, guests, storage), `/cluster/status` (node IPs), guest IPs from LXC `interfaces`, the QEMU guest agent, or `net0 ip=` in the config. Needs only the **PVEAuditor** role. |
| `docker` | the socket is mounted, or `[docker] endpoints` is set | `/info`, `/containers/json`. Published ports become endpoints, the image name becomes the hint, and `burrow.*` labels act as overrides. Read-only; use a socket proxy for remote engines. |
| `static` | `[scan] hosts` is set | Plain IPs or hostnames to fingerprint, for things outside Proxmox and Docker (a NAS, a Pi). |

Adding a provider means adding a file and one line in `providers/__init__.py`.
Candidates that fit the interface without engine changes: Kubernetes, Unraid,
TrueNAS, libvirt, and mDNS.

## App fingerprints

An app is a small declaration in `burrow/apps/`. Most need no code:

```python
App(
    id="prowlarr", name="Prowlarr", category="Downloads", icon="prowlarr",
    ports=[9696], images=["prowlarr"],
    probe="/", match=title("Prowlarr"),
    stats=arr_stats("v1"),        # optional; may use secrets["prowlarr"]
)
```

- `probe` + `match` decide identity. Several apps can share a port (8080 is
  qBittorrent on one host and Open WebUI on another), so responses are cached
  per pass and each candidate checks the same answer.
- `images` match Docker image names. A hint makes the app the first candidate
  and also probes containers that publish odd ports.
- `stats(ctx)` returns `[Stat(label, value)]`. The context gives `get`/`json`
  helpers and that app's secrets. Without secrets an app still shows up/down,
  latency and, where the app exposes it unauthenticated, its version.
- Non-HTTP apps set `kind`. `minecraft` speaks the server-list ping, so player
  counts need no plugin or RCON.

Secrets are per app id, in a separate TOML file that should be mode 600:

```toml
[sonarr]
api_key = "..."
[qbittorrent]
username = "..."
password = "..."
[uptime-kuma]
status_page = "homelab"
```

## Themes

A theme is **one CSS file of custom properties** in `web/themes/`. Layout lives
in `app.css`, which only consumes variables, so a theme cannot break the layout.

```css
/* name: Midnight */
:root {
  --bg: #0b0f1a;            /* page */
  --bg-glow-1: #3b4cca33;   /* two soft radial glows behind the page */
  --bg-glow-2: #06b6d422;
  --surface: #121829cc;     /* cards; alpha shows the glow through */
  --surface-2: #1a2236;     /* bars, chips */
  --border: #ffffff12;
  --text: #e6e9f2;
  --text-muted: #8a93a8;
  --accent: #7c8cff;
  --accent-2: #22d3ee;      /* gradients pair accent → accent-2 */
  --ok: #34d399; --warn: #fbbf24; --bad: #f87171;
  --radius: 14px;
  --shadow: 0 1px 0 #ffffff08 inset, 0 8px 24px #0006;
  --blur: 14px;
  --font: "Inter", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
}
```

The first line `/* name: ... */` is the display name. The server lists
`web/themes/*.css`, so dropping a file in makes it selectable. You can mount a
folder at `/app/burrow/web/themes/custom/` for your own themes. Shipped themes:
**Midnight** (dark, the default), **Daylight** (light) and **Ember** (warm dark).
The choice is kept per browser, and the default comes from the config.

## Config

Everything is optional. The whole file:

```toml
title = "HomeLab"
theme = "midnight"

[proxmox]
url = "https://192.168.1.124:8006"
token_file = "/secrets/proxmox.token"   # one line: user@realm!tokenid=uuid

[docker]
endpoints = ["unix:///var/run/docker.sock", "tcp://192.168.1.130:2375"]

[scan]
hosts = ["192.168.1.123"]

[secrets]
file = "/secrets/apps.toml"

[[service]]                 # manual extras, for what cannot be discovered
name = "Router"
url = "https://192.168.1.1"
category = "Network"

[hide]
apps = ["flaresolverr"]
```

Docker labels on a container override what discovery guessed:
`burrow.name`, `burrow.app`, `burrow.url`, `burrow.hide=true`.

## Security posture

- Read-only everywhere. The Proxmox token needs PVEAuditor and nothing more.
  The provider only ever sends `GET`, and so does the Docker provider.
  Recommend `tecnativa/docker-socket-proxy` with `CONTAINERS=1 INFO=1` for
  remote engines.
- Secrets are read from files and are never included in `/api/state`.
- No auth of its own in v0. Put it behind your reverse proxy or LAN-only rules,
  as you would Homepage.

## Not in the MVP (next steps)

- Server-sent events instead of polling.
- Self-hosted icons for offline labs. Icons come from the dashboard-icons CDN
  for now, with a monogram fallback.
- History and sparklines (a small ring buffer per stat).
- More fingerprints, plus a `burrow identify <url>` CLI to help contributors
  write them.
- Proxmox backup, ZFS pool health and SMART, all of which still fit under PVEAuditor.
- A Go port, if a single binary becomes worth more than hackability.

## Name

**Burrow**, chosen 2026-10-24. It digs through your lab and finds what's living
in there. The working title was `lab-dashboard`. Identifiers follow the name:
the `burrow` package, `BURROW_*` environment variables, `burrow.*` Docker labels
and `burrow.toml`.
