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


class ProxmoxProvider(Provider):
    name = "proxmox"
    interval = 20
    guest_ip_ttl = 300   # guest IPs rarely change; re-read them every 5 minutes

    def __init__(self, url: str, token: str, overrides: dict | None = None):
        self.url = url.rstrip("/")
        self.overrides = {str(k): v for k, v in (overrides or {}).items()}   # vmid -> IP, for VMs without an agent
        self.headers = {"Authorization": f"PVEAPIToken={token}"}
        self._ips: dict[str, str | None] = {}
        self._ips_at = 0.0
        self._summary = ""

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
        return [cls(p["url"], token, p.get("guest_addresses"))]

    def _get(self, path: str):
        return net.get_json(f"{self.url}/api2/json{path}", headers=dict(self.headers), timeout=6)["data"]

    def discover(self) -> Inventory:
        resources = self._get("/cluster/resources")
        node_ips: dict[str, str] = {}
        try:
            for item in self._get("/cluster/status"):
                if item.get("type") == "node" and item.get("ip"):
                    node_ips[item["name"]] = item["ip"]
        except RuntimeError as e:
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
        except RuntimeError:
            pass
        if running:
            try:
                if kind == "lxc":
                    for iface in self._get(f"{base}/interfaces") or []:
                        ip = (iface.get("inet") or "").split("/")[0]
                        if ip and not ip.startswith("127."):
                            return ip
                else:
                    data = self._get(f"{base}/agent/network-get-interfaces")
                    for iface in data.get("result", []):
                        for a in iface.get("ip-addresses", []):
                            ip = a.get("ip-address", "")
                            if a.get("ip-address-type") == "ipv4" and not ip.startswith("127."):
                                return ip
            except RuntimeError:
                pass  # no guest agent, or no permission
        return None

    def describe(self) -> str:
        return self._summary


def _metrics(r: dict) -> dict:
    return {k: r[k] for k in ("cpu", "maxcpu", "mem", "maxmem", "disk", "maxdisk", "uptime") if k in r}
