"use strict";
// Renders /api/state. No framework: template literals, re-rendered when the data changes.

const POLL_MS = 5000;
const ICON_CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons";
const CAT_ORDER = ["Media", "Media management", "Downloads", "Games", "AI", "Network", "Monitoring",
                   "Infrastructure", "Apps", "Containers", "Links"];

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

let state = null;
let lastJson = "";
let category = store.get("burrow.cat") || "All";
let query = "";

// ---- formatting ------------------------------------------------------------

function bytes(n) {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
}
function uptime(s) {
  if (!s) return "";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
}
const pct = (a, b) => (b ? Math.min(100, (a / b) * 100) : 0);
const tone = (p) => (p >= 90 ? "bad" : p >= 75 ? "warn" : "");

function bar(p) { return `<div class="bar ${tone(p)}"><i style="width:${p.toFixed(1)}%"></i></div>`; }
function meter(label, used, total, text) {
  const p = pct(used, total);
  return `<div class="meter"><span class="k">${esc(label)}</span>${bar(p)}<span class="v">${esc(text ?? `${bytes(used)} / ${bytes(total)}`)}</span></div>`;
}

// ---- icons -----------------------------------------------------------------

function icon(name, label) {
  const letter = esc((label || name || "?").trim()[0]?.toUpperCase() || "?");
  if (!name) return `<div class="icon"><span class="mono-letter">${letter}</span></div>`;
  const n = encodeURIComponent(name.toLowerCase());
  return `<div class="icon"><img alt="" loading="lazy" src="${ICON_CDN}/svg/${n}.svg"
    data-png="${ICON_CDN}/png/${n}.png" data-letter="${letter}" onerror="iconFallback(this)"></div>`;
}
window.iconFallback = (img) => {
  if (img.dataset.png) { const p = img.dataset.png; img.dataset.png = ""; img.src = p; return; }
  img.outerHTML = `<span class="mono-letter">${img.dataset.letter}</span>`;
};

// ---- render ----------------------------------------------------------------

function matches(...fields) {
  if (!query) return true;
  return fields.some((f) => String(f ?? "").toLowerCase().includes(query));
}

function renderSummary(s) {
  const tiles = [];
  if (s.nodes) tiles.push(["Nodes online", s.nodes_online, s.nodes]);
  if (s.guests) tiles.push(["Guests running", s.guests_running, s.guests]);
  if (s.containers) tiles.push(["Containers running", s.containers_running, s.containers]);
  tiles.push(["Services up", s.services_up, s.services]);
  $("#summary").style.gridTemplateColumns = innerWidth > 760 ? `repeat(${tiles.length}, minmax(0,1fr))` : "";
  $("#summary").innerHTML = tiles.map(([label, a, b]) => {
    const alert = b && a < b && label.startsWith("Services");
    return `<div class="tile ${alert ? "alert" : ""}">
      <div class="label">${label}</div>
      <div class="value">${a}<small>/ ${b}</small></div>
      <div class="bar"><i style="width:${pct(a, b)}%"></i></div></div>`;
  }).join("");
}

function guestChip(g, svcCount) {
  const off = g.status !== "running";
  return `<span class="chip ${off ? "off" : ""}" title="${esc(g.kind.toUpperCase())} ${esc(g.vmid)} · ${esc(g.address || "no IP found")} · ${esc(g.status)}">
    <span class="dot ${esc(g.status)}"></span><span class="id">${esc(g.vmid)}</span>${esc(g.name)}
    ${svcCount ? `<span class="n">${svcCount}</span>` : ""}</span>`;
}

function hostCard(h, svcByHost) {
  const m = h.metrics || {};
  const guests = h.guests || [];
  const sub = [h.address, m.uptime ? `up ${uptime(m.uptime)}` : null].filter(Boolean).join(" · ");
  const meters = [];
  if (m.maxcpu != null && m.cpu != null) meters.push(meter("CPU", m.cpu, 1, `${(m.cpu * 100).toFixed(0)}% of ${m.maxcpu}`));
  if (m.maxmem && m.mem != null) meters.push(meter("RAM", m.mem, m.maxmem));
  if (!h.storage?.length && m.maxdisk && m.disk != null) meters.push(meter("Disk", m.disk, m.maxdisk));
  const storage = (h.storage || []).map((st) => meter(st.name, st.used, st.total)).join("");
  const containers = h.containers || [];
  const running = containers.filter((c) => c.state === "running").length;
  const shown = guests.filter((g) => matches(g.name, g.vmid, g.address));
  if (query && !shown.length && !matches(h.name, h.address)) return "";
  return `<article class="card node">
    <div class="node-head"><span class="dot ${esc(h.status)}"></span><h3>${esc(h.name)}</h3>
      <span class="sub mono">${esc(sub)}</span></div>
    ${meters.length ? `<div class="meters">${meters.join("")}</div>` : ""}
    ${storage ? `<div class="storage">${storage}</div>` : ""}
    ${containers.length ? `<div class="muted" style="font-size:12.5px">Docker · ${running} of ${containers.length} containers running</div>` : ""}
    ${guests.length ? `<div class="guests">${(query ? shown : guests).map((g) => guestChip(g, svcByHost[g.id] || 0)).join("")}</div>` : ""}
  </article>`;
}

function serviceCard(s) {
  const where = s.manual ? "" : [s.host, s.port ? `:${s.port}` : null].filter(Boolean).join(" ");
  const sub = s.description || where;
  const stats = (s.stats || []).slice(0, 4).map((st) => {
    const wide = String(st.value).length > 14;
    return `<div class="stat ${esc(st.tone)} ${wide ? "wide" : ""}"><div class="k">${esc(st.label)}</div><div class="v" title="${esc(st.value)}">${esc(st.value)}</div></div>`;
  }).join("");
  const latency = s.status === "unknown" ? "" : s.status === "up" && s.latency_ms != null ? `${s.latency_ms} ms` : s.status;
  const foot = [
    s.description && where ? `<span class="tag">${esc(where)}</span>` : "",
    s.version ? `<span class="tag">v${esc(String(s.version).replace(/^v/, ""))}</span>` : "",
    s.container && s.container.toLowerCase() !== s.name.toLowerCase() ? `<span class="tag">${esc(s.container)}</span>` : "",
    s.status === "down" && s.error ? `<span class="note">${esc(s.error)}</span>` : s.note ? `<span class="note">${esc(s.note)}</span>` : "",
  ].join("");
  const tag = s.url ? "a" : "div";
  const href = s.url ? ` href="${esc(s.url)}" target="_blank" rel="noopener"` : "";
  return `<${tag} class="card svc ${esc(s.status)}"${href}>
    <div class="svc-head">${icon(s.icon, s.name)}
      <div class="svc-title"><b>${esc(s.name)}</b><span class="${s.description ? "" : "mono"}" title="${esc(sub)}">${esc(sub)}</span></div>
      ${s.status === "unknown" ? `<div class="status link">↗</div>` : `<div class="status"><span class="dot ${esc(s.status)}"></span>${esc(latency)}</div>`}</div>
    ${stats ? `<div class="stats">${stats}</div>` : ""}
    ${foot ? `<div class="svc-foot">${foot}</div>` : ""}
  </${tag}>`;
}

function renderCats(services) {
  const order = [...(state.categories || []), ...CAT_ORDER];
  const cats = [...new Set(services.map((s) => s.category))].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  if (category !== "All" && !cats.includes(category)) category = "All";
  $("#cats").innerHTML = ["All", ...cats].map((c) =>
    `<button type="button" data-cat="${esc(c)}" aria-pressed="${c === category}">${esc(c)}</button>`).join("");
  return cats;
}

function render() {
  if (!state) return;
  const st = state;
  document.title = st.title;
  $("#title").textContent = st.title;
  const down = st.services.filter((s) => s.status === "down").length;
  $("#subtitle").textContent = st.identified_at
    ? `${st.summary.services} services discovered${down ? ` · ${down} down` : ""} · updated ${ago(st.generated_at)}`
    : "Discovering your lab…";

  renderSummary(st.summary);

  const svcByHost = {};
  st.services.forEach((s) => { if (s.host_id) svcByHost[s.host_id] = (svcByHost[s.host_id] || 0) + 1; });

  $("#cluster-block").hidden = !st.nodes.length;
  const guestTotal = st.nodes.reduce((n, x) => n + x.guests.length, 0);
  $("#cluster-count").textContent = `${st.nodes.length} nodes · ${guestTotal} guests`;
  $("#nodes").innerHTML = st.nodes.map((n) => hostCard(n, svcByHost)).join("");

  $("#hosts-block").hidden = !st.hosts.length;
  $("#hosts").innerHTML = st.hosts.map((h) => hostCard(h, svcByHost)).join("");

  const cats = renderCats(st.services);
  const visible = st.services.filter((s) =>
    (category === "All" || s.category === category) &&
    matches(s.name, s.app, s.host, s.address, s.container, s.category, s.description));
  $("#svc-count").textContent = `${st.summary.services_up} / ${st.summary.services} up`;
  if (!st.services.length) {
    $("#services").innerHTML = `<div class="empty">${st.identified_at ? "No services found yet. Mount the Docker socket, or add a Proxmox token, then give it a minute." : "Probing the lab…"}</div>`;
  } else if (!visible.length) {
    $("#services").innerHTML = `<div class="empty">Nothing matches “${esc(query)}”.</div>`;
  } else {
    // Down services first within each category, so problems are never below the fold.
    $("#services").innerHTML = cats.filter((c) => visible.some((s) => s.category === c)).map((c) => {
      const items = visible.filter((s) => s.category === c)
        .sort((a, b) => (b.status === "down") - (a.status === "down"));
      return `<section class="cat">${category === "All" ? `<h4>${esc(c)}</h4>` : ""}<div class="grid">${items.map(serviceCard).join("")}</div></section>`;
    }).join("");
  }

  const bm = st.bookmarks || [];
  $("#bookmarks-block").hidden = !bm.length;
  const groups = [...new Set(bm.map((b) => b.group || "Links"))];
  $("#bookmarks").innerHTML = groups.map((g) => `<div class="bm-group"><h4>${esc(g)}</h4><div class="bm-list">${
    bm.filter((b) => (b.group || "Links") === g && matches(b.name, b.url, g)).map((b) =>
      `<a class="bm" href="${esc(b.url)}" target="_blank" rel="noopener"><span class="abbr">${esc(b.abbr || b.name.slice(0, 2))}</span>${esc(b.name)}</a>`).join("")
  }</div></div>`).join("");

  const w = st.weather;
  $("#weather").hidden = !w;
  if (w) $("#weather").textContent = `${w.temp}${w.unit} ${w.text} · H ${w.high}° L ${w.low}°${w.label ? ` · ${w.label}` : ""}`;
  $("#q").placeholder = st.search ? "Filter, or Enter to search the web" : "Filter";

  $("#footer").innerHTML = st.providers.map((p) =>
    `<span class="prov ${p.ok === false ? "err" : ""}" title="${esc(p.error || "")}">
      <span class="dot ${p.ok ? "up" : p.ok === false ? "down" : ""}"></span>${esc(p.name)}${p.detail ? ` · ${esc(p.detail)}` : ""}${p.ok === false ? ` · ${esc(p.error)}` : ""}</span>`
  ).join("") + `<span class="spacer"></span><span>Burrow · read-only</span>`;

  // Extensions (custom.js) can listen for this and read the state.
  document.dispatchEvent(new CustomEvent("burrow:render", { detail: st }));
}

// ---- clock + web search -------------------------------------------------------

function tick() {
  const now = new Date();
  $("#time").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  $("#date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}
tick();
setInterval(tick, 10000);

const SEARCH = {
  google: "https://www.google.com/search?q=",
  duckduckgo: "https://duckduckgo.com/?q=",
  bing: "https://www.bing.com/search?q=",
};
$("#q").addEventListener("keydown", (e) => {
  const url = SEARCH[state?.search];
  if (e.key !== "Enter" || !url || !e.target.value.trim()) return;
  window.open(url + encodeURIComponent(e.target.value.trim()), "_blank", "noopener");
});

// ---- themes ----------------------------------------------------------------

function setupThemes(themes, fallback) {
  const sel = $("#theme");
  if (sel.options.length === themes.length) return;
  const current = store.get("burrow.theme") || fallback;
  sel.innerHTML = themes.map((t) => `<option value="${esc(t.id)}" ${t.id === current ? "selected" : ""}>${esc(t.name)}</option>`).join("");
  if (!store.get("burrow.theme")) $("#theme-css").href = `themes/${fallback}.css`;
}
$("#theme").addEventListener("change", (e) => {
  store.set("burrow.theme", e.target.value);
  $("#theme-css").href = `themes/${e.target.value}.css`;
});

// ---- events + polling --------------------------------------------------------

$("#cats").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-cat]");
  if (!b) return;
  category = b.dataset.cat;
  store.set("burrow.cat", category);
  render();
});
$("#q").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); render(); });
addEventListener("resize", () => state && renderSummary(state.summary));

async function poll() {
  try {
    const r = await fetch("api/state", { cache: "no-store" });
    const next = await r.json();
    setupThemes(next.themes || [], next.theme);
    const sig = JSON.stringify({ ...next, generated_at: 0 });
    state = next;
    if (sig !== lastJson) { lastJson = sig; render(); }
    else $("#subtitle").textContent = $("#subtitle").textContent.replace(/updated .*$/, `updated ${ago(next.generated_at)}`);
  } catch (e) {
    $("#subtitle").textContent = "Lost contact with the dashboard server — retrying";
  }
  setTimeout(poll, POLL_MS);
}
poll();
