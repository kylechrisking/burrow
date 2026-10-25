# lab-dashboard

A homelab dashboard that **finds what's running on its own**. Point it at your
Proxmox cluster and your Docker hosts. It lists the nodes, guests and containers,
recognises the apps behind their ports (Sonarr, Plex, Pi-hole and friends), and
shows each one with its live status and the numbers that matter.

There is no service list to write. One container, no dependencies, themeable.

> Status: early MVP. See [docs/design.md](docs/design.md) for how it works and where it's going.

![Midnight theme](docs/screenshots/desktop-midnight.jpg)

## Quickstart

```bash
git clone https://github.com/<you>/lab-dashboard && cd lab-dashboard
docker compose -f compose.example.yaml up -d --build
```

Open `http://<host>:8099`. With only the Docker socket mounted you'll see that
host's containers straight away. For the rest of the lab, add Proxmox.

### Add Proxmox (recommended)

Create a **read-only** API token on any node. PVEAuditor can see everything
and change nothing, and no new user is needed:

```bash
pveum user token add root@pam labdash --privsep 1
pveum acl modify / --tokens 'root@pam!labdash' --roles PVEAuditor
```

Save `root@pam!labdash=<secret>` as `secrets/proxmox.token` (`chmod 600`), then
create `config/labdash.toml`:

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

Labels on a container override discovery: `labdash.name`, `labdash.app`,
`labdash.url` and `labdash.hide=true`.

## What it recognises

Sonarr, Radarr, Lidarr, Readarr, Prowlarr, Bazarr, Jackett, qBittorrent,
Transmission, SABnzbd, FlareSolverr, Plex, Jellyfin, Navidrome,
Seerr/Overseerr/Jellyseerr, Tautulli, Immich, RomM, Pi-hole, AdGuard Home,
Caddy, Nginx Proxy Manager, Proxmox VE, Synology DSM, Portainer, Uptime Kuma,
Beszel, Grafana, Open WebUI, Ollama, Home Assistant, Nextcloud, Vaultwarden,
Gitea/Forgejo, Paperless-ngx, Tandoor, DokuWiki, Crafty, and Minecraft servers
(player counts via server-list ping).

A container it can't identify still gets a card with its name and status, so
nothing you've published is invisible. Adding an app is usually 2–3 lines in
`labdash/apps/`; see the design doc.

## Themes

Midnight (default), Daylight and Ember ship in `labdash/web/themes/`. A theme
is a single CSS file of variables. Drop in a new one and it appears in the
picker.

## Run without Docker

```bash
LABDASH_CONFIG=./config/labdash.toml LABDASH_SECRETS_FILE=./secrets/apps.toml python3 -m labdash
```

Python 3.11 or newer. Standard library only.

## Security

Everything is read-only. It sends only GET requests to Proxmox and Docker,
plus a session login to qBittorrent and Pi-hole when you give it credentials.
It has no authentication of its own, so keep it on your LAN or behind your
reverse proxy.

## License

MIT
