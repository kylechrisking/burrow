# Burrow

A homelab dashboard that **finds what's running on its own**. Point it at your
Proxmox cluster and your Docker hosts. It lists the nodes, guests and containers,
recognises the apps behind their ports (Sonarr, Plex, Pi-hole and friends), and
shows each one with its live status and the numbers that matter.

There is no service list to write. One container, no dependencies, themeable.

> Status: early MVP. See [docs/design.md](docs/design.md) for how it works and where it's going.

![Midnight theme](docs/screenshots/desktop-midnight.jpg)

## Quickstart

```bash
git clone https://github.com/kylechrisking/burrow && cd burrow
docker compose -f compose.example.yaml up -d --build
```

Open `http://<host>:8099`. With only the Docker socket mounted you'll see that
host's containers straight away. For the rest of the lab, add Proxmox.

### Add Proxmox (recommended)

Create a **read-only** API token on any node. PVEAuditor can see everything
and change nothing, and no new user is needed:

```bash
pveum user token add root@pam burrow --privsep 1
pveum acl modify / --tokens 'root@pam!burrow' --roles PVEAuditor
```

Save `root@pam!burrow=<secret>` as `secrets/proxmox.token` (`chmod 600`), then
create `config/burrow.toml`:

```toml
[proxmox]
url = "https://192.168.1.10:8006"
token_file = "/secrets/proxmox.token"
```

The dashboard now reads every guest's IP. LXC IPs come from the API; VMs need
the QEMU guest agent or a static `ipconfig`. It then fingerprints well-known
ports on each guest.

### Stats that need a key

Without credentials every card shows up/down, latency and, where the app
exposes it, its version. For queue sizes, streams, blocked queries and so on,
copy [`secrets.example.toml`](secrets.example.toml) to `secrets/apps.toml`, fill
in what you have, and `chmod 600` it. Secrets never leave the server.

### Other Docker hosts

Run [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
with `CONTAINERS=1 INFO=1` on each one, then:

```toml
[docker]
endpoints = ["unix:///var/run/docker.sock", "tcp://192.168.1.20:2375"]
```

Labels on a container override discovery: `burrow.name`, `burrow.app`,
`burrow.url` and `burrow.hide=true`.

## What it recognises

Over 120 apps: the *arr stack, every common download client, Plex, Jellyfin
and Emby, Immich, Home Assistant and friends, Pi-hole, AdGuard, Traefik,
Proxmox VE and Backup Server, TrueNAS, Unraid, UniFi, Grafana, Prometheus,
Netdata, Uptime Kuma, Vaultwarden, Paperless-ngx, Nextcloud, Gitea, the other
dashboards, and more. It also speaks Minecraft's server-list ping and MQTT. The
full list is in **[docs/apps.md](docs/apps.md)**, including which cards show
stats and what they need.

Fingerprints are checked against real installs. `testlab/compose.yaml` runs
about 55 apps on a spare Docker host, `tools/record_fixtures.py` records their
answers, and the test suite replays them, so a change can't quietly break
identification.

A container it can't identify still gets a card with its name and status, so
nothing you've published is invisible. Adding an app is usually one line in
`burrow/apps/`. `python -m burrow identify http://host:port` shows what Burrow
sees at an address, which is most of the work of writing a new fingerprint.

## Already using Homepage, Traefik or Caddy labels?

Burrow reads them. A container with `homepage.name`, `homepage.description`,
`homepage.icon` and `homepage.group` labels keeps its name, description, icon
and group. A Traefik `Host()` rule or a caddy-docker-proxy `caddy=` label
becomes the card's link, so links come out as `https://sonarr.example.com`
instead of `IP:port`, with no config at all.

## No Proxmox, no Docker API?

Give it your subnet and it finds things on its own:

```toml
[scan]
hosts = ["192.168.1.0/24"]
```

## Hide things

Tag a Proxmox guest `burrow-hide`, list hosts under `[hide] hosts = [...]`, or
put `burrow.hide=true` on a container.

## Coming from Homepage?

```bash
python -m burrow import-homepage /path/to/homepage/config -o burrow.toml --secrets apps.toml
```

The importer reads your `services.yaml`, `bookmarks.yaml`, `settings.yaml` and
`widgets.yaml`:

- Widgets for apps Burrow discovers become names, descriptions and friendly
  links, and their keys go to the secrets file (mode 600, never printed).
- `customapi` widgets become JSON cards with the same field mappings.
- Everything else becomes a link card.
- Tabs become views, and weather, search, theme colour and clock format carry
  over.

## Make it yours

Discovery gets you most of the way. The rest is optional config in
`burrow.toml` (see [`burrow.example.toml`](burrow.example.toml)). Edits apply
within a few seconds, without a restart.

- **Views.** Named pages shown as tabs, each with its own groups, sections and
  bookmarks. Link straight to one with `?view=family`.
- **Thresholds.** Colour any number or text, on discovered apps and custom
  cards alike, e.g. `warn_below = 1500` or `bad_if = ["off", "error"]`.
- **Stats.** Choose which stats a card shows, in what order and under what name.
- **History.** Numeric stats and node CPU get a one-hour sparkline behind them.
- **Look.** Four themes plus `auto`, which follows the device, and `[ui]` for
  accent colours, a wallpaper, compact density and a 24-hour clock.
- **Pin and hide.** The ⋯ on any card pins it to the top or hides it, per
  browser, so each person in the house can set up their own.
- **Icons.** Dashboard-icons names, `mdi-*` names, URLs, or your own files in
  `icons/`. They are served and cached by Burrow, so they work offline.
- **Cards.** `[[card]]` turns any JSON URL into a card, and `[[service]]` adds a
  link card for anything discovery can't see.
- **Overrides.** `[override."app"]` and `[links]` rename, describe, re-icon,
  re-link or hide anything discovered.
- **Header and links.** `[[bookmark]]`, `[weather]` and `search` add a links
  section, the weather, and web search from the filter box.
- **Your own panels.** `custom.css` and `custom.js` next to the config get a
  `#panels` slot that Burrow never re-renders, plus a `burrow:render` event.

## Themes

Midnight (default), Daylight, Ember and Forest ship in `burrow/web/themes/`. A theme
is a single CSS file of variables. Drop in a new one and it appears in the
picker.

## Run without Docker

```bash
BURROW_CONFIG=./config/burrow.toml BURROW_SECRETS_FILE=./secrets/apps.toml python3 -m burrow
```

Python 3.11 or newer. Standard library only.

## Security

Everything is read-only. It sends only GET requests to Proxmox and Docker,
plus a session login to qBittorrent and Pi-hole when you give it credentials.
It has no authentication of its own, so keep it on your LAN or behind your
reverse proxy.

## License

MIT
