# How Burrow works

This is the tour for anyone who wants to change or extend Burrow. It's short,
because the code is too.

## The big picture

```
            ┌──────────────── one container ─────────────────┐
 Proxmox ◄──┤ providers/proxmox.py ─┐                        │
            │                       │  hosts + endpoints     │
 Docker  ◄──┤ providers/docker.py ──┼─────────────┐          │
            │                       │             ▼          │
 subnets ◄──┤ providers/static.py ──┘     engine: identify   │
            │                                     │          │
   apps  ◄──┤ apps/*.py  ◄── check loop (15 s) ── services   │
            │                                     │          │
            │ server.py: /api/state, /api/history, /icons    │
            │ web/: one HTML page, app.js, themes            │
            └────────────────────────────────────────────────┘
```

Four background threads do all the work:

1. **Providers** each poll their source on their own schedule and return
   hosts (nodes, guests, Docker engines) and endpoints (a port on a host,
   plus a hint such as the image name).
2. **Identify** runs whenever that picture changes, and every five minutes
   anyway. It merges hosts first. A Docker engine whose hostname is `media` is
   the Proxmox guest called `media`, so its containers move onto that guest.
   Then it works out which app is behind each port.
3. **Check** visits every identified service every 15 seconds to see if it's
   up and to collect its stats.
4. **Watch** reloads `burrow.toml` and the secrets file when they change.

There's no database. Everything lives in memory and comes back within a minute
of a restart. The browser polls `/api/state` every five seconds and redraws.

I picked plain Python with no dependencies on purpose. `http.server`, `urllib`,
`socket`, `ssl`, `tomllib` and a thread pool cover everything, and the whole
thing is a few thousand lines that one person can read in an evening. The
front end is one JS file with no build step.

## Providers

A provider is one file in `burrow/providers/`:

```python
class Provider:
    name = "example"     # shown in the footer
    interval = 30        # seconds between discover() calls

    @classmethod
    def from_config(cls, cfg) -> list["Provider"] | None:
        """Zero or more instances. Docker returns one per endpoint."""

    def discover(self) -> Inventory:
        """What exists right now. Raise on failure: the engine keeps the
        last good answer and shows the error in the footer."""
```

The three that ship:

- **proxmox** reads `/cluster/resources` and `/cluster/status`. A guest's IP
  comes from a static `ip=` in its config first, then LXC interfaces or the
  QEMU guest agent. It only ever needs the PVEAuditor role.
- **docker** reads `/info` and `/containers/json`. Published ports become
  endpoints, and the image name becomes the hint that names the app. It also
  borrows Homepage, Traefik and caddy-docker-proxy labels.
- **static** takes IPs, hostnames or subnets. A subnet gets swept for any port
  an app is known to use.

Something like Kubernetes, TrueNAS, libvirt or mDNS would fit the same
interface without touching the engine.

## Recognising apps

Each app is a declaration in `burrow/apps/`. Most are one line:

```python
App("prowlarr", "Prowlarr", "Media management", [9696], match=title("Prowlarr"),
    images=["prowlarr"], stats=prowlarr_stats, needs="api_key")
```

For each endpoint, the engine builds a list of candidates: apps whose image
keyword appears in the hint first, then apps that normally use that port. It
fetches each candidate's probe path and asks its matcher. Responses are cached
for the pass, because a busy port like 8080 can have a dozen candidates that
all want `/`.

A Docker image name is trusted. If the image says `linuxserver/sonarr`, the
card is Sonarr even if Sonarr happens to be slow to answer at that moment.
Anything published that matches nothing still gets a card with the container
name, so it doesn't vanish.

Matchers available: `title()` and `title_is()` (the stricter one, for short
names), `body()`, `header()`, `json_has()`, `json_at()` for nested keys, and
`any_of()`. Non-HTTP apps set `kind`. Minecraft uses the server-list ping, so
player counts need no plugin. MQTT sends a CONNECT and reads the CONNACK.

A stats function gets a small context with HTTP helpers and that app's secrets,
and returns a list of `Stat(label, value, tone)`. Without secrets, a card still
shows up/down, latency, and the version if the app gives it out.

### Tests that use real apps

I don't trust a fingerprint I haven't seen match. `testlab/compose.yaml` runs
about 55 apps on a spare Docker host. Most are on default ports, and a few are
remapped on purpose, because real people remap.
`tools/record_fixtures.py` saves what each one answers, and
`tests/test_fixtures.py` replays those answers offline. Every recorded app has
to be identified from its image, identified from its port alone when it's on a
default port, and not claimed by any other fingerprint. That last check is the
one that catches mistakes, like a short title such as "Actual" matching some
unrelated page.

## The page

`server.py` serves the page, `/api/state` (everything the page needs, with no
secrets in it), `/api/history`, and `/icons/<name>`.

- Views, thresholds, renamed or chosen stats, and links all happen in
  `state()`, after the checks. They work the same on discovered apps and on
  JSON cards.
- History is 60 samples per numeric stat at one-minute resolution. It's served
  on its own endpoint, so the five-second poll stays small.
- Icons come from your own `icons/` folder, then from icons baked into the
  image, then from a disk cache, and only then from the CDN, once. `mdi-*`
  icons are single-colour, so the page tints them with the theme using a CSS
  mask.
- Pins and hidden cards are kept in each browser's localStorage. They're meant
  to be personal; the config is for things everyone should see.

## Themes

A theme is one CSS file of custom properties in `burrow/web/themes/`. The
layout CSS only reads variables, so a theme can't break the layout. The first
line, `/* name: Midnight */`, is what the picker shows. Drop a new file in and
it appears.

## What's next

- Server-sent events instead of polling.
- Proxmox backup jobs, ZFS pool health and SMART data, all of which are
  readable with PVEAuditor.
- More fingerprints, from whatever people run.
