"use strict";
// Renders /api/state. No framework: template literals, re-rendered when the data changes.

const POLL_MS = 5000;
const HISTORY_MS = 60000;
const CAT_ORDER = ["Media", "Media management", "Downloads", "Games", "AI", "Home", "Network", "Monitoring",
                   "Infrastructure", "Files", "Apps", "Dev", "Dashboards", "Containers", "Links"];

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  list(k) { try { return JSON.parse(localStorage.getItem(k) || "[]"); } catch { return []; } },
  setList(k, v) { this.set(k, JSON.stringify([...v])); },
};
const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

let state = null;
let trends = {};          // series -> last hour of values, from /api/history
let lastSig = "";
let category = store.get("burrow.cat") || "All";
let query = "";
let viewId = new URLSearchParams(location.search).get("view") || store.get("burrow.view") || "";
const pinned = new Set(store.list("burrow.pinned"));
const hidden = new Set(store.list("burrow.hidden"));
let showHidden = false;

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

// A trend line from the last hour, drawn behind a number. Flat or short series draw nothing.
function spark(series, cls = "spark") {
  const v = trends[series];
  if (!v || v.length < 5) return "";
  const lo = Math.min(...v), hi = Math.max(...v);
  if (hi === lo) return "";
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * 100).toFixed(1)},${(28 - ((y - lo) / (hi - lo)) * 24).toFixed(1)}`).join(" ");
  return `<svg class="${cls}" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}"/></svg>`;
}

// ---- icons -----------------------------------------------------------------
// Served by Burrow itself (/icons/...), cached on disk, so they work offline.

function icon(name, label) {
  const letter = esc((label || name || "?").trim()[0]?.toUpperCase() || "?");
  if (!name) return `<div class="icon"><span class="mono-letter">${letter}</span></div>`;
  const src = /^https?:\/\//.test(name) ? `icons/url?u=${encodeURIComponent(name)}` : `icons/${encodeURIComponent(name.toLowerCase())}`;
  if (name.startsWith("mdi-")) return `<div class="icon"><span class="mdi" style="--src:url('${src}')"></span></div>`;
  return `<div class="icon"><img alt="" loading="lazy" src="${src}" data-letter="${letter}" onerror="iconFallback(this)"></div>`;
}
window.iconFallback = (img) => { img.outerHTML = `<span class="mono-letter">${img.dataset.letter}</span>`; };

// ---- views -----------------------------------------------------------------

function views() {
  const vs = (state.views || []).map((v) => ({ ...v, id: v.id || slug(v.name) }));
  if (vs.length && state.ui?.all_tab) vs.unshift({ id: "all", name: "All" });
  return vs;
}
function currentView() {
  const vs = views();
  return vs.find((v) => v.id === viewId) || vs[0] || { id: "all", name: "All" };
}
const inView = (v, s) => !v.categories || v.categories.includes(s.category) || pinned.has(s.id);
const showSection = (v, name) => !v.sections || v.sections.includes(name);

function renderTabs() {
  const vs = views();
  $("#tabs").hidden = vs.length < 2;
  const cur = currentView();
  $("#tabs").innerHTML = vs.map((v) =>
    `<a href="?view=${encodeURIComponent(v.id)}" data-view="${esc(v.id)}" aria-current="${v.id === cur.id ? "page" : "false"}">${esc(v.name)}</a>`).join("");
}

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
  const trend = spark(`${h.id}|cpu`, "spark node-spark");
  return `<article class="card node">
    <div class="node-head"><span class="dot ${esc(h.status)}"></span><h3>${esc(h.name)}</h3>
      <span class="sub mono">${esc(sub)}</span></div>
    ${meters.length ? `<div class="meters">${meters.join("")}</div>` : ""}
    ${trend ? `<div class="trend" title="CPU, last hour">${trend}</div>` : ""}
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
    return `<div class="stat ${esc(st.tone)} ${wide ? "wide" : ""}">${st.series ? spark(st.series) : ""}<div class="k">${esc(st.label)}</div><div class="v" title="${esc(st.value)}">${esc(st.value)}</div></div>`;
  }).join("");
  const latency = s.status === "unknown" ? "" : s.status === "up" && s.latency_ms != null ? `${s.latency_ms} ms` : s.status;
  const foot = [
    s.description && where ? `<span class="tag">${esc(where)}</span>` : "",
    s.version ? `<span class="tag">v${esc(String(s.version).replace(/^v/, ""))}</span>` : "",
    s.container && s.container.toLowerCase() !== s.name.toLowerCase() ? `<span class="tag">${esc(s.container)}</span>` : "",
    s.status === "down" && s.error ? `<span class="note">${esc(s.error)}</span>` : s.note ? `<span class="note">${esc(s.note)}</span>` : "",
  ].join("");
  const flags = `${pinned.has(s.id) ? " pinned" : ""}${hidden.has(s.id) ? " is-hidden" : ""}`;
  return `<article class="card svc ${esc(s.status)}${flags}" data-id="${esc(s.id)}">
    ${s.url ? `<a class="stretch" href="${esc(s.url)}" target="_blank" rel="noopener" aria-label="Open ${esc(s.name)}"></a>` : ""}
    <div class="svc-head">${icon(s.icon, s.name)}
      <div class="svc-title"><b>${esc(s.name)}</b><span class="${s.description ? "" : "mono"}" title="${esc(sub)}">${esc(sub)}</span></div>
      ${s.status === "unknown" ? `<div class="status link">↗</div>` : `<div class="status"><span class="dot ${esc(s.status)}"></span>${esc(latency)}</div>`}</div>
    ${stats ? `<div class="stats">${stats}</div>` : ""}
    <div class="svc-foot">${foot}<button type="button" class="menu-btn" data-menu="${esc(s.id)}" aria-label="Options for ${esc(s.name)}">⋯</button></div>
  </article>`;
}

function renderCats(services) {
  const order = [...(state.categories || []), ...CAT_ORDER];
  const cats = [...new Set(services.map((s) => s.category))].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  if (category !== "All" && !cats.includes(category)) category = "All";
  $("#cats").innerHTML = cats.length > 1 ? ["All", ...cats].map((c) =>
    `<button type="button" data-cat="${esc(c)}" aria-pressed="${c === category}">${esc(c)}</button>`).join("") : "";
  return cats;
}

function render() {
  if (!state) return;
  const st = state;
  const view = currentView();
  document.title = view.id !== "all" && views().length ? `${view.name} · ${st.title}` : st.title;
  $("#title").textContent = st.title;
  const down = st.services.filter((s) => s.status === "down").length;
  $("#subtitle").textContent = st.identified_at
    ? `${st.summary.services} services discovered${down ? ` · ${down} down` : ""} · updated ${ago(st.generated_at)}`
    : "Discovering your lab…";
  $("#config-error").hidden = !st.config_error;
  $("#config-error").textContent = st.config_error || "";
  renderTabs();

  $("#panels").hidden = !showSection(view, "panels");
  $("#summary").hidden = !showSection(view, "summary");
  if (showSection(view, "summary")) renderSummary(st.summary);

  const svcByHost = {};
  st.services.forEach((s) => { if (s.host_id) svcByHost[s.host_id] = (svcByHost[s.host_id] || 0) + 1; });

  $("#cluster-block").hidden = !st.nodes.length || !showSection(view, "cluster");
  const guestTotal = st.nodes.reduce((n, x) => n + x.guests.length, 0);
  $("#cluster-count").textContent = `${st.nodes.length} nodes · ${guestTotal} guests`;
  $("#nodes").innerHTML = st.nodes.map((n) => hostCard(n, svcByHost)).join("");

  $("#hosts-block").hidden = !st.hosts.length || !showSection(view, "hosts");
  $("#hosts").innerHTML = st.hosts.map((h) => hostCard(h, svcByHost)).join("");

  const inThisView = st.services.filter((s) => inView(view, s) && (showHidden || !hidden.has(s.id)));
  $("#services-block").hidden = !showSection(view, "services");
  const cats = renderCats(inThisView);
  const visible = inThisView.filter((s) =>
    (category === "All" || s.category === category) &&
    matches(s.name, s.app, s.host, s.address, s.container, s.category, s.description));
  const counted = inThisView.filter((s) => s.status !== "unknown");
  $("#svc-count").textContent = `${counted.filter((s) => s.status === "up").length} / ${counted.length} up`;
  if (!st.services.length) {
    $("#services").innerHTML = `<div class="empty">${st.identified_at ? "No services found yet. Mount the Docker socket, or add a Proxmox token, then give it a minute." : "Probing the lab…"}</div>`;
  } else if (!visible.length) {
    $("#services").innerHTML = `<div class="empty">${query ? `Nothing matches “${esc(query)}”.` : "Nothing in this view."}</div>`;
  } else {
    // Pinned first, then each category with down services at the top, so problems are never below the fold.
    const groups = [];
    const pins = visible.filter((s) => pinned.has(s.id));
    if (pins.length && category === "All") groups.push(["Pinned", pins]);
    for (const c of cats) {
      const items = visible.filter((s) => s.category === c && !(category === "All" && pinned.has(s.id)));
      if (items.length) groups.push([c, items.sort((a, b) => (b.status === "down") - (a.status === "down"))]);
    }
    $("#services").innerHTML = groups.map(([c, items]) =>
      `<section class="cat">${category === "All" ? `<h4>${esc(c)}</h4>` : ""}<div class="grid">${items.map(serviceCard).join("")}</div></section>`).join("");
  }

  const groupsWanted = view.links;
  const bm = (st.bookmarks || []).filter((b) => !groupsWanted || groupsWanted.includes(b.group || "Links"));
  $("#bookmarks-block").hidden = !bm.length || !showSection(view, "links");
  const bmGroups = [...new Set(bm.map((b) => b.group || "Links"))];
  $("#bookmarks").innerHTML = bmGroups.map((g) => `<div class="bm-group"><h4>${esc(g)}</h4><div class="bm-list">${
    bm.filter((b) => (b.group || "Links") === g && matches(b.name, b.url, g)).map((b) =>
      `<a class="bm" href="${esc(b.url)}" target="_blank" rel="noopener"><span class="abbr">${esc(b.abbr || b.name.slice(0, 2))}</span>${esc(b.name)}</a>`).join("")
  }</div></div>`).join("");

  const w = st.weather;
  $("#weather").hidden = !w;
  if (w) $("#weather").textContent = `${w.temp}${w.unit} ${w.text} · H ${w.high}° L ${w.low}°${w.label ? ` · ${w.label}` : ""}`;
  $("#q").placeholder = st.search ? "Filter, or Enter to search the web" : "Filter";

  const nHidden = st.services.filter((s) => hidden.has(s.id)).length;
  $("#footer").innerHTML = st.providers.map((p) =>
    `<span class="prov ${p.ok === false ? "err" : ""}" title="${esc(p.error || "")}">
      <span class="dot ${p.ok ? "up" : p.ok === false ? "down" : ""}"></span>${esc(p.name)}${p.detail ? ` · ${esc(p.detail)}` : ""}${p.ok === false ? ` · ${esc(p.error)}` : ""}</span>`
  ).join("") + `<span class="spacer"></span>` +
    (nHidden ? `<button type="button" class="linkish" id="toggle-hidden">${showHidden ? "Hide" : "Show"} ${nHidden} hidden</button>` : "") +
    `<span>Burrow · read-only</span>`;

  // Extensions (custom.js) can listen for this and read the state.
  document.dispatchEvent(new CustomEvent("burrow:render", { detail: st }));
}

// ---- card menu: pin / hide, per browser ------------------------------------------

const menu = $("#card-menu");
function openMenu(btn) {
  const id = btn.dataset.menu;
  menu.dataset.id = id;
  menu.innerHTML = `<button type="button" data-act="pin">${pinned.has(id) ? "Unpin" : "Pin to top"}</button>
    <button type="button" data-act="hide">${hidden.has(id) ? "Unhide" : "Hide"}</button>`;
  const r = btn.getBoundingClientRect();
  menu.hidden = false;
  menu.style.top = `${scrollY + r.bottom + 6}px`;
  menu.style.left = `${Math.min(scrollX + r.right - menu.offsetWidth, scrollX + innerWidth - menu.offsetWidth - 12)}px`;
  menu.querySelector("button").focus();
}
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-menu]");
  if (btn) { e.preventDefault(); return menu.hidden || menu.dataset.id !== btn.dataset.menu ? openMenu(btn) : (menu.hidden = true); }
  const act = e.target.closest("#card-menu [data-act]");
  if (act) {
    const id = menu.dataset.id, set = act.dataset.act === "pin" ? pinned : hidden;
    set.has(id) ? set.delete(id) : set.add(id);
    store.setList(act.dataset.act === "pin" ? "burrow.pinned" : "burrow.hidden", set);
    menu.hidden = true;
    return render();
  }
  if (e.target.closest("#toggle-hidden")) { showHidden = !showHidden; return render(); }
  if (!e.target.closest("#card-menu")) menu.hidden = true;
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.hidden = true; });

// ---- clock + web search -------------------------------------------------------

function tick() {
  const now = new Date();
  const h24 = state?.ui?.clock === "24h";
  $("#time").textContent = now.toLocaleTimeString([], { hour: h24 ? "2-digit" : "numeric", minute: "2-digit", hour12: !h24 });
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

// ---- themes + look ------------------------------------------------------------
// "auto" follows the device's light/dark setting. [ui] in burrow.toml can set the
// accent colours, a wallpaper and a compact density on top of any theme.

const dark = matchMedia("(prefers-color-scheme: dark)");
function chosenTheme() { return store.get("burrow.theme") || state?.theme || "midnight"; }
function applyTheme() {
  let t = chosenTheme();
  if (t === "auto") t = dark.matches ? (state?.ui?.auto_dark || "midnight") : (state?.ui?.auto_light || "daylight");
  const href = `themes/${t}.css`;
  if (!$("#theme-css").href.endsWith(href)) $("#theme-css").href = href;
}
dark.addEventListener?.("change", applyTheme);

function applyLook(ui = {}) {
  const root = document.documentElement.style;
  for (const [key, prop] of [["accent", "--accent"], ["accent_2", "--accent-2"]]) {
    ui[key] ? root.setProperty(prop, ui[key]) : root.removeProperty(prop);
  }
  ui.wallpaper ? root.setProperty("--wallpaper", `url("${String(ui.wallpaper).replace(/"/g, "%22")}")`) : root.removeProperty("--wallpaper");
  document.body.classList.toggle("has-wallpaper", !!ui.wallpaper);
  document.body.classList.toggle("compact", ui.density === "compact");
}

function setupThemes(themes) {
  const sel = $("#theme");
  const want = themes.length + 1;
  if (sel.options.length !== want) {
    sel.innerHTML = `<option value="auto">Auto</option>` +
      themes.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  }
  sel.value = chosenTheme();
  applyTheme();
}
$("#theme").addEventListener("change", (e) => { store.set("burrow.theme", e.target.value); applyTheme(); });

// ---- events + polling --------------------------------------------------------

$("#cats").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-cat]");
  if (!b) return;
  category = b.dataset.cat;
  store.set("burrow.cat", category);
  render();
});
$("#tabs").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-view]");
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
  e.preventDefault();
  viewId = a.dataset.view;
  store.set("burrow.view", viewId);
  window.history.replaceState(null, "", `?view=${encodeURIComponent(viewId)}`);
  category = "All";
  render();
});
$("#q").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); render(); });
addEventListener("resize", () => state && renderSummary(state.summary));

async function poll() {
  try {
    const r = await fetch("api/state", { cache: "no-store" });
    const next = await r.json();
    const firstLoad = !state;
    state = next;
    setupThemes(next.themes || []);
    applyLook(next.ui);
    if (firstLoad) tick();   // the clock format comes from [ui]
    const sig = JSON.stringify({ ...next, generated_at: 0 });
    if (sig !== lastSig) { lastSig = sig; render(); }
    else $("#subtitle").textContent = $("#subtitle").textContent.replace(/updated .*$/, `updated ${ago(next.generated_at)}`);
  } catch (e) {
    $("#subtitle").textContent = "Lost contact with the dashboard server — retrying";
  }
  setTimeout(poll, POLL_MS);
}

async function pollHistory() {
  try {
    const r = await fetch("api/history", { cache: "no-store" });
    trends = await r.json();
    if (state) render();
  } catch {}
  setTimeout(pollHistory, HISTORY_MS);
}
poll();
pollHistory();
