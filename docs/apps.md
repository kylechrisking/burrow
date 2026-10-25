# Supported apps

Burrow recognises **126 apps**. Generated from `burrow/apps/` by `tools/gen_apps_doc.py`.

- **Ports** are where it looks when it has no Docker metadata. With Docker, the image name finds the app on any port.
- **Stats** shows what the card adds beyond up/down, latency and version, and what (if anything) it needs in `secrets.toml`.
- ✓ means the fingerprint is checked against a real install in the test lab (56 apps).

## AI

| App | Ports | Stats | Tested |
|---|---|---|---|
| Ollama | 11434 | yes |  |
| Open WebUI | 8080, 3000 | - |  |

## Apps

| App | Ports | Stats | Tested |
|---|---|---|---|
| Actual Budget | 5006 | - | ✓ |
| BookStack | 80, 6875 | - |  |
| DokuWiki | 80 | - |  |
| Firefly III | 8080 | - |  |
| FreshRSS | 80 | - | ✓ |
| Grocy | 80, 9283 | - | ✓ |
| Homebox | 7745 | - |  |
| IT Tools | 80 | - | ✓ |
| linkding | 9090 | - | ✓ |
| Mealie | 9000, 9925 | - | ✓ |
| Miniflux | 8080 | - |  |
| Outline | 3000 | - |  |
| Paperless-ngx | 8000 | yes, needs `token` |  |
| SearXNG | 8080 | - | ✓ |
| Stirling PDF | 8080 | - |  |
| Tandoor Recipes | 8080 | - |  |
| Vaultwarden | 80, 8000 | - | ✓ |
| Vikunja | 3456 | - |  |
| wallabag | 80 | - |  |
| Wiki.js | 3000 | - | ✓ |

## Dashboards

| App | Ports | Stats | Tested |
|---|---|---|---|
| Dashy | 4000, 8080 | - | ✓ |
| Flame | 5005 | - |  |
| Glance | 8080 | - |  |
| Heimdall | 80 | - | ✓ |
| Homarr | 7575 | - | ✓ |
| Homepage | 3000 | - | ✓ |
| Organizr | 80 | - |  |

## Dev

| App | Ports | Stats | Tested |
|---|---|---|---|
| code-server | 8443, 8080 | - | ✓ |
| Forgejo | 3000 | - | ✓ |
| Gitea | 3000 | - | ✓ |
| GitLab | 80, 443 | - |  |
| Jenkins | 8080 | - |  |
| n8n | 5678 | - | ✓ |

## Downloads

| App | Ports | Stats | Tested |
|---|---|---|---|
| Deluge | 8112 | - | ✓ |
| FlareSolverr | 8191 | - |  |
| Nicotine+ | 6080 | - |  |
| NZBGet | 6789 | yes, needs `username + password` | ✓ |
| qBittorrent | 8080, 8090 | yes | ✓ |
| SABnzbd | 8080, 8085 | yes, needs `api_key` | ✓ |
| Transmission | 9091 | yes | ✓ |

## Files

| App | Ports | Stats | Tested |
|---|---|---|---|
| Duplicati | 8200 | - | ✓ |
| File Browser | 80, 8080 | - | ✓ |
| MinIO | 9000, 9001 | - |  |
| Nextcloud | 80, 443 | - | ✓ |
| Seafile | 80, 8000 | - |  |
| Syncthing | 8384 | - | ✓ |

## Games

| App | Ports | Stats | Tested |
|---|---|---|---|
| Crafty Controller | 8443 | - |  |
| Minecraft | 25565, 25566 | yes (minecraft handshake) |  |
| RomM | 8080, 80 | - |  |

## Home

| App | Ports | Stats | Tested |
|---|---|---|---|
| ESPHome | 6052 | - | ✓ |
| Frigate | 5000, 8971 | yes |  |
| Home Assistant | 8123 | yes, needs `token` | ✓ |
| Mosquitto (MQTT) | 1883 | yes (mqtt handshake) | ✓ |
| Node-RED | 1880 | - | ✓ |
| Scrypted | 10443 | - |  |
| Zigbee2MQTT | 8080 | - |  |

## Infrastructure

| App | Ports | Stats | Tested |
|---|---|---|---|
| Dockge | 5001 | - | ✓ |
| OpenMediaVault | 80 | - |  |
| Portainer | 9000, 9443 | - |  |
| Proxmox Backup Server | 8007 | - |  |
| Proxmox Mail Gateway | 8006 | - |  |
| Proxmox VE | 8006 | - |  |
| Synology DSM | 5001, 5000 | - |  |
| TrueNAS | 443, 80 | - |  |
| Unraid | 80, 443 | - |  |
| Webmin | 10000 | - |  |
| Yacht | 8000 | - |  |

## Media

| App | Ports | Stats | Tested |
|---|---|---|---|
| Audiobookshelf | 13378, 80 | - | ✓ |
| Calibre-Web | 8083 | - | ✓ |
| Emby | 8096, 8920 | - | ✓ |
| Immich | 2283 | yes, needs `api_key` |  |
| Jellyfin | 8096 | yes, needs `api_key` | ✓ |
| Kavita | 5000 | - | ✓ |
| Komga | 25600 | - | ✓ |
| Navidrome | 4533 | yes, needs `username + password` |  |
| Ombi | 3579 | - |  |
| PhotoPrism | 2342 | - |  |
| Plex | 32400 | yes, needs `token` |  |
| Seerr | 5055 | yes, needs `api_key` | ✓ |
| Tautulli | 8181 | yes, needs `api_key` | ✓ |

## Media management

| App | Ports | Stats | Tested |
|---|---|---|---|
| Bazarr | 6767 | yes, needs `api_key` |  |
| Jackett | 9117 | - | ✓ |
| LazyLibrarian | 5299 | - |  |
| Lidarr | 8686 | yes, needs `api_key` | ✓ |
| Mylar3 | 8090 | - |  |
| Notifiarr | 5454 | - |  |
| Prowlarr | 9696 | yes, needs `api_key` |  |
| Radarr | 7878 | yes, needs `api_key` |  |
| Readarr | 8787 | yes, needs `api_key` |  |
| Sonarr | 8989 | yes, needs `api_key` |  |
| Tdarr | 8265 | - |  |
| Whisparr | 6969 | yes, needs `api_key` |  |

## Monitoring

| App | Ports | Stats | Tested |
|---|---|---|---|
| Beszel | 8090 | - |  |
| changedetection.io | 5000 | - | ✓ |
| Dozzle | 8080 | - | ✓ |
| Glances | 61208 | yes | ✓ |
| Gotify | 80 | - | ✓ |
| Grafana | 3000 | - | ✓ |
| Healthchecks | 8000 | - |  |
| InfluxDB | 8086 | - |  |
| NetAlertX | 20211 | - |  |
| Netdata | 19999 | yes | ✓ |
| ntfy | 80 | - | ✓ |
| Prometheus | 9090 | yes | ✓ |
| Scrutiny | 8080 | yes | ✓ |
| Speedtest Tracker | 80, 8765 | yes | ✓ |
| Uptime Kuma | 3001 | yes, needs `status_page` | ✓ |
| Zabbix | 80, 8080 | - |  |

## Network

| App | Ports | Stats | Tested |
|---|---|---|---|
| AdGuard Home | 3000, 80 | yes, needs `username + password` | ✓ |
| Authelia | 9091 | - |  |
| authentik | 9000, 9443 | - |  |
| Caddy | 80, 443 | - |  |
| Guacamole | 8080 | - |  |
| Keycloak | 8080 | - |  |
| MeshCentral | 443, 80 | - |  |
| NetBox | 8080, 80 | - |  |
| Nginx Proxy Manager | 81 | - |  |
| Omada Controller | 8043 | - |  |
| Pi-hole | 80 | yes | ✓ |
| Technitium DNS | 5380 | yes, needs `token` | ✓ |
| Traefik | 8080 | yes | ✓ |
| UniFi Network | 8443 | - |  |
| UniFi OS | 443 | - |  |
| WireGuard Easy | 51821 | - |  |
