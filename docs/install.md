# Installing Burrow

Burrow runs as one container (or as plain Python, see near the end). It only
reads from your lab. The steps below go from "it's running" to "it sees
everything", and you can stop after any of them.

- [1. Run it](#1-run-it)
- [2. Let it see Proxmox](#2-let-it-see-proxmox)
- [3. More Docker hosts](#3-more-docker-hosts)
- [4. API keys for stats](#4-api-keys-for-stats)
- [5. Put it behind your reverse proxy](#5-put-it-behind-your-reverse-proxy)
- [Running without Docker](#running-without-docker)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

## 1. Run it

Pick a machine that can reach your other hosts. A small LXC or VM is plenty;
Burrow uses about 30 MB of RAM.

```bash
mkdir -p burrow/config burrow/secrets && cd burrow
curl -fsSLO https://raw.githubusercontent.com/kylechrisking/burrow/main/compose.example.yaml
docker compose -f compose.example.yaml up -d
```

Open `http://<that-machine>:8099`. The compose file mounts the local Docker
socket read-only, so that host's containers show up straight away.

Images are built for amd64 and arm64 (a Raspberry Pi works). If you'd rather
build it yourself:

```bash
git clone https://github.com/kylechrisking/burrow && cd burrow
docker compose -f compose.example.yaml up -d --build
```

## 2. Let it see Proxmox

This is what turns Burrow from "one Docker host" into "the whole lab". It reads
your nodes, guests and storage, finds each guest's IP, and checks the usual
ports on every guest for apps.

Create a read-only API token. In a shell on any node:

```bash
pveum user token add root@pam burrow --privsep 1
pveum acl modify / --tokens 'root@pam!burrow' --roles PVEAuditor
```

The first command prints a `value`. That's the secret, and it's only shown
once.

If you prefer the web UI: go to Datacenter → Permissions → API Tokens → Add,
pick user `root@pam`, set the token ID to `burrow` and leave "Privilege
Separation" ticked. Then go to Datacenter → Permissions → Add → API Token
Permission, with path `/`, token `root@pam!burrow` and role `PVEAuditor`.

PVEAuditor can look at everything and change nothing. With privilege
separation on, the token can never do more than that role, even though it
belongs to root.

Save it as one line in `secrets/proxmox.token`:

```bash
echo 'root@pam!burrow=<the secret>' > secrets/proxmox.token
chmod 600 secrets/proxmox.token
```

and create `config/burrow.toml`:

```toml
[proxmox]
url = "https://192.168.1.10:8006"     # any node; the whole cluster comes along
token_file = "/secrets/proxmox.token"
```

Give it a minute. Proxmox shows up in the footer with a green dot, and your
guests appear under Cluster.

Guest IPs come from a static `ip=` in the guest's network config, from LXC
interfaces, or from the QEMU guest agent. A VM with DHCP and no guest agent has
no IP Burrow can find, so tell it:

```toml
[proxmox]
guest_addresses = { 105 = "192.168.1.105" }
```

Tag a guest `burrow-hide` in Proxmox to keep it off the dashboard.

## 3. More Docker hosts

Proxmox finds apps by their ports, but Docker can say exactly which container
is which, and lists containers that don't publish a port at all. For any other
Docker host, run a read-only socket proxy on it:

```yaml
services:
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: 1
      INFO: 1
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    ports:
      - "192.168.1.20:2375:2375"     # that host's LAN IP; don't expose it further
    labels:
      burrow.hide: "true"
```

With `CONTAINERS=1 INFO=1` it answers the two read calls Burrow makes and
refuses everything else. Then list it, keeping the local socket too:

```toml
[docker]
endpoints = ["unix:///var/run/docker.sock", "tcp://192.168.1.20:2375"]
```

Podman works the same way through its Docker-compatible socket.

Burrow reads labels you may already have. Homepage's `homepage.name`,
`homepage.description`, `homepage.icon` and `homepage.group` carry over. A
Traefik `Host()` rule or a caddy-docker-proxy `caddy=` label becomes the
card's link. Its own labels win over all of those: `burrow.name`,
`burrow.url`, `burrow.icon`, `burrow.description`, `burrow.category`,
`burrow.app` and `burrow.hide=true`.

## 4. API keys for stats

Without credentials, every card shows up/down, response time and, when the app
gives it away freely, its version. For the interesting numbers (queue sizes,
streams, blocked queries, transfer speeds) Burrow needs the app's API key.

```bash
curl -fsSL https://raw.githubusercontent.com/kylechrisking/burrow/main/secrets.example.toml -o secrets/apps.toml
chmod 600 secrets/apps.toml
```

Fill in the apps you have and delete the rest. Each section is named after the
app's id from [apps.md](apps.md), which also says what each app needs. The file
is read on the server only and is never sent to the browser. Saving it applies
the change within a few seconds.

## 5. Put it behind your reverse proxy

Burrow has no login of its own, so either keep it on your LAN or put it behind
whatever already guards your other apps.

Caddy:

```
http://dash.home.lan {
	reverse_proxy 192.168.1.30:8099
}
```

Traefik labels on the Burrow container:

```yaml
labels:
  traefik.enable: "true"
  traefik.http.routers.burrow.rule: "Host(`dash.home.lan`)"
  traefik.http.services.burrow.loadbalancer.server.port: "8099"
```

In Nginx Proxy Manager, add a proxy host pointing at port 8099. Nothing special
is needed: no websockets, and it works under any hostname.

## Running without Docker

Burrow needs Python 3.11 or newer and nothing else.

```bash
git clone https://github.com/kylechrisking/burrow /opt/burrow
cd /opt/burrow
BURROW_CONFIG=./config/burrow.toml BURROW_SECRETS_FILE=./secrets/apps.toml python3 -m burrow
```

As a systemd service, in `/etc/systemd/system/burrow.service`:

```ini
[Unit]
Description=Burrow homelab dashboard
After=network-online.target

[Service]
WorkingDirectory=/opt/burrow
Environment=BURROW_CONFIG=/opt/burrow/config/burrow.toml
Environment=BURROW_SECRETS_FILE=/opt/burrow/secrets/apps.toml
ExecStart=/usr/bin/python3 -m burrow
Restart=on-failure
DynamicUser=yes
StateDirectory=burrow
Environment=BURROW_CACHE=/var/lib/burrow

[Install]
WantedBy=multi-user.target
```

Then run `systemctl enable --now burrow`. To read a local Docker socket, the
user needs to be in the `docker` group, so swap `DynamicUser` for a real user.

Settings you can pass as environment variables instead of in the file:
`BURROW_PORT`, `BURROW_TITLE`, `BURROW_THEME`, `BURROW_PROXMOX_URL`,
`BURROW_PROXMOX_TOKEN_FILE`, `BURROW_DOCKER` (comma-separated; empty turns
Docker off) and `BURROW_SCAN`.

## Updating

```bash
docker compose -f compose.example.yaml pull
docker compose -f compose.example.yaml up -d
```

Your config and secrets live in the mounted folders, so nothing is lost.

## Troubleshooting

**Nothing shows up.** Check the footer. Each source (Proxmox, every Docker
endpoint, scanned hosts) has a dot and, when it fails, the reason.

**Proxmox says 401.** The token file must be one line,
`user@realm!tokenid=secret`, with no quotes. Also check that the role was
given to the token (`--tokens`), not just to the user.

**A guest has no IP.** See the `guest_addresses` note in step 2.

**An app shows as a plain container card.** Burrow didn't recognise it.
`docker exec burrow python -m burrow identify http://ip:port --all` shows what
it sees. Please open an issue with that output.

**A card says "add api_key to secrets for stats".** That's step 4.

**Icons are letters.** Icons are baked into the image and cached after that,
but an icon that isn't in the catalog is fetched once from a CDN. On a lab with
no internet, put your own file in `config/icons/<name>.svg` and set
`icon = "<name>"` on the card.

**I changed the config and nothing happened.** If the TOML doesn't parse, a red
banner at the top says why, and the old config keeps running until it's fixed.
