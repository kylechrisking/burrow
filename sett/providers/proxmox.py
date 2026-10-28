"""Proxmox VE: nodes, guests and storage through the API, with a read-only token.

Create one with the PVEAuditor role (no new user needed):

    pveum user token add root@pam sett --privsep 1
    pveum acl modify / --tokens 'root@pam!sett' --roles PVEAuditor

and save "root@pam!sett=<secret>" in a mode-600 file.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import quote

from .. import net
from ..config import Config
from ..model import Host, Inventory, Storage
from . import Provider

log = logging.getLogger(__name__)

IP_RE = re.compile(r"\bip=(\d+\.\d+\.\d+\.\d+)")
# Interfaces a guest has besides its LAN one: Docker/Podman/libvirt bridges, veth pairs,
# tunnels. Their addresses are never how anything else on the network reaches the guest.
VIRTUAL_IFACE = re.compile(r"^(lo|docker\d*|br-|veth|virbr|cni|flannel|cali|vxlan|tailscale|tun|tap|wg|zt|podman|kube)")


class ProxmoxProvider(Provider):
    name = "proxmox"
    interval = 20
    guest_ip_ttl = 300   # guest IPs rarely change; re-read them every 5 minutes
    health_ttl = 300     # ZFS, SMART and backup results: every 5 minutes is plenty

    def __init__(self, url: str, token: str, overrides: dict | None = None, backup_max_age_hours: float = 0):
        self.url = url.rstrip("/")
        self.overrides = {str(k): v for k, v in (overrides or {}).items()}   # vmid -> IP, for VMs without an agent
        self.headers = {"Authorization": f"PVEAPIToken={token}"}
        self._ips: dict[str, str | None] = {}
        self._ips_at = 0.0
        self._summary = ""
        self._health: dict[str, list[dict]] = {}
        self._health_at: dict[str, float] = {}
        self.backup_max_age = float(backup_max_age_hours) * 3600

    @classmethod
    def from_config(cls, cfg: Config):
        p = cfg.proxmox
        if not p.get("url"):
            return None
        token = p.get("token")
        tf = p.get("token_file")
        if not token and tf and Path(tf).is_file():
            token = Path(tf).read_text().strip()
        if not token:
            log.warning("proxmox: url set but no token (token_file=%s)", tf)
            return None
        return [cls(p["url"], token, p.get("guest_addresses"), p.get("backup_max_age_hours", 0))]

    def _get(self, path: str):
        return net.get_json(f"{self.url}/api2/json{path}", headers=dict(self.headers), timeout=6)["data"]

    def discover(self) -> Inventory:
        resources = self._get("/cluster/resources")
        node_ips: dict[str, str] = {}
        try:
            for item in self._get("/cluster/status"):
                if item.get("type") == "node" and item.get("ip"):
                    node_ips[item["name"]] = item["ip"]
        except (RuntimeError, OSError) as e:
            log.info("proxmox: /cluster/status unavailable (%s)", e)

        refresh_ips = time.monotonic() - self._ips_at > self.guest_ip_ttl
        hosts: dict[str, Host] = {}
        n_nodes = n_guests = 0
        for r in resources:
            t = r.get("type")
            if t == "node":
                n_nodes += 1
                node = r["node"]
                hosts[f"pve:node/{node}"] = Host(
                    id=f"pve:node/{node}", name=node, kind="node", source=self.name,
                    status=r.get("status", "unknown"), address=node_ips.get(node),
                    metrics=_metrics(r),
                )
            elif t in ("lxc", "qemu"):
                n_guests += 1
                vmid, node = r["vmid"], r["node"]
                hid = f"pve:{t}/{vmid}"
                if refresh_ips or hid not in self._ips:
                    self._ips[hid] = self._guest_ip(t, node, vmid, r.get("status") == "running")
                hosts[hid] = Host(
                    id=hid, name=r.get("name", str(vmid)), kind=t, vmid=vmid, source=self.name,
                    status=r.get("status", "unknown"), parent=f"pve:node/{node}",
                    address=self._ips.get(hid), metrics=_metrics(r),
                    tags=[x for x in re.split(r"[;,\s]+", r.get("tags", "")) if x],
                )
        if refresh_ips:
            self._ips_at = time.monotonic()

        # Storage: shared storage is reported once per node; keep the first.
        seen_shared: set[str] = set()
        for r in resources:
            if r.get("type") != "storage" or r.get("status") != "available" or not r.get("maxdisk"):
                continue
            if r.get("shared"):
                if r["storage"] in seen_shared:
                    continue
                seen_shared.add(r["storage"])
            node = hosts.get(f"pve:node/{r['node']}")
            if node:
                node.storage.append(Storage(r["storage"], int(r.get("disk", 0)), int(r["maxdisk"]),
                                            r.get("plugintype", "")))

        for h in hosts.values():
            h.storage.sort(key=lambda s: s.name)
            if h.kind == "node" and h.status == "online":
                if time.monotonic() - self._health_at.get(h.name, -1e9) > self.health_ttl:
                    self._health[h.name] = self.node_health(h.name)
                    self._health_at[h.name] = time.monotonic()
                h.health = self._health.get(h.name, [])
        self._summary = f"{n_nodes} nodes, {n_guests} guests"
        return Inventory(hosts=list(hosts.values()))

    def _guest_ip(self, kind: str, node: str, vmid: int, running: bool) -> str | None:
        """Configured address first: a static ip= is authoritative, while a live
        interface may list a floating VIP (keepalived) ahead of the real IP."""
        if str(vmid) in self.overrides:
            return self.overrides[str(vmid)]
        base = f"/nodes/{quote(node)}/{kind}/{vmid}"
        try:
            conf = self._get(f"{base}/config")
            for k in sorted(conf):
                if k.startswith(("net", "ipconfig")):
                    m = IP_RE.search(str(conf[k]))
                    if m:
                        return m.group(1)
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            log.debug("proxmox: %s config: %s", vmid, e)
        if running:
            try:
                if kind == "lxc":
                    for iface in self._get(f"{base}/interfaces") or []:
                        ip = (iface.get("inet") or "").split("/")[0]
                        if ip and not ip.startswith("127.") and not VIRTUAL_IFACE.match(iface.get("name", "")):
                            return ip
                else:
                    data = self._get(f"{base}/agent/network-get-interfaces")
                    for iface in data.get("result", []):
                        if VIRTUAL_IFACE.match(iface.get("name", "")):
                            continue
                        for a in iface.get("ip-addresses", []):
                            ip = a.get("ip-address", "")
                            if a.get("ip-address-type") == "ipv4" and not ip.startswith("127."):
                                return ip
            except (RuntimeError, OSError, ValueError, KeyError, AttributeError) as e:
                # No guest agent, no permission, or an agent that hangs until the request
                # times out: that guest has no IP this round, the rest of discovery goes on.
                log.debug("proxmox: %s interfaces: %s", vmid, e)
        return None

    def node_health(self, node: str) -> list[dict]:
        """ZFS pools, disk SMART results and the node's last backup, all readable
        with PVEAuditor (Sys.Audit). Each part is optional: one that errors is
        left out, so a node without ZFS or without backups simply shows less."""
        out: list[dict] = []
        base = f"/nodes/{quote(node)}"
        try:
            for pool in self._get(f"{base}/disks/zfs") or []:
                state = str(pool.get("health", "UNKNOWN")).upper()
                out.append({"label": f"ZFS {pool.get('name', '?')}", "value": state.title(),
                            "tone": ZFS_TONES.get(state, "warn"),
                            "detail": f"{pool.get('frag', 0)}% fragmented" if pool.get("frag") is not None else ""})
        except (RuntimeError, OSError, ValueError) as e:
            log.debug("proxmox: %s zfs: %s", node, e)
        try:
            disks = self._get(f"{base}/disks/list") or []
            bad, worn, known = [], [], 0
            for d in disks:
                health = str(d.get("health", "")).upper()
                name = d.get("devpath", "?").removeprefix("/dev/")
                if health in ("PASSED", "OK"):
                    known += 1
                elif health and health not in ("UNKNOWN", "N/A"):
                    bad.append(name)
                life = d.get("wearout")   # percent of rated endurance left (SSDs)
                if isinstance(life, (int, float)) and life < 20:
                    worn.append((name, life))
            if bad:
                out.append({"label": "SMART", "value": f"{len(bad)} failing", "tone": "bad", "detail": ", ".join(bad)})
            elif known:
                out.append({"label": "SMART", "value": f"{known} ok", "tone": "ok", "detail": ""})
            if worn:
                low = min(life for _, life in worn)
                out.append({"label": "Wear", "value": f"{low:.0f}% left", "tone": "bad" if low < 10 else "warn",
                            "detail": ", ".join(f"{n} {life:.0f}% life left" for n, life in worn)})
        except (RuntimeError, OSError, ValueError) as e:
            log.debug("proxmox: %s disks: %s", node, e)
        try:
            tasks = [t for t in self._get(f"{base}/tasks?typefilter=vzdump&limit=5") or [] if t.get("type") == "vzdump"]
            if tasks:
                out.append(backup_health(max(tasks, key=lambda t: t.get("starttime", 0)), self.backup_max_age))
        except (RuntimeError, OSError, ValueError) as e:
            log.debug("proxmox: %s backups: %s", node, e)
        return out

    def describe(self) -> str:
        return self._summary


ZFS_TONES = {"ONLINE": "ok", "DEGRADED": "warn", "FAULTED": "bad", "UNAVAIL": "bad",
             "OFFLINE": "bad", "REMOVED": "bad", "SUSPENDED": "bad"}


def backup_health(task: dict, max_age: float = 0, now: float | None = None) -> dict:
    """The newest vzdump task as a health entry. status "OK" passed; "WARNINGS: n"
    passed with warnings; no status yet means it is still running; anything else
    is the error. With max_age set, a backup older than that is a warning too."""
    now = now or time.time()
    status = task.get("status")
    started = task.get("starttime") or 0
    age = now - started
    when = _ago(age)
    if not status:
        return {"label": "Backup", "value": "running", "tone": "", "detail": f"started {when}"}
    if status == "OK":
        stale = max_age and age > max_age
        return {"label": "Backup", "value": when, "tone": "warn" if stale else "ok",
                "detail": "last backup is older than expected" if stale else "last backup OK"}
    if str(status).upper().startswith("WARNINGS"):
        return {"label": "Backup", "value": when, "tone": "warn", "detail": str(status)}
    return {"label": "Backup", "value": "failed", "tone": "bad", "detail": f"{status} ({when})"}


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _metrics(r: dict) -> dict:
    return {k: r[k] for k in ("cpu", "maxcpu", "mem", "maxmem", "disk", "maxdisk", "uptime") if k in r}
