/* AutoSim Studio web mode. Talks to autosim.web.server: REST for actions, one WebSocket for live data. */
"use strict";

// ---------- helpers ----------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hexId = (id, ext) => ext ? id.toString(16).toUpperCase().padStart(8, "0") : id.toString(16).toUpperCase().padStart(3, "0");
const idText = (id, ext) => "0x" + id.toString(16).toUpperCase() + (ext ? " x" : "");
const rate = (v) => v >= 1e6 ? `${v / 1e6} Mbit/s` : `${v / 1e3} kbit/s`;
const fmt = (v) => (v === null || v === undefined) ? "" : (Number.isInteger(v) ? String(v) : String(+(+v).toPrecision(10)));
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

// Inside the AutoSim Studio desktop app (Tauri) the page gets the native Open / Save dialogs and file access;
// in a browser it uses the browser's upload / download.
const TAURI = window.__TAURI__ && window.__TAURI__.dialog && window.__TAURI__.fs ? window.__TAURI__ : null;
const extsOf = (accept) => accept.split(",").map((s) => s.trim()).filter((s) => s.startsWith(".")).map((s) => s.slice(1));
const baseName = (p) => String(p).split(/[\\/]/).pop();
async function nativeOpen(accept, multiple) {
  const exts = extsOf(accept);
  const sel = await TAURI.dialog.open({ multiple: !!multiple, directory: false,
    filters: exts.length ? [{ name: exts.map((e) => "." + e).join(" "), extensions: exts }] : [] });
  return sel ? (Array.isArray(sel) ? sel : [sel]).map((p) => (typeof p === "string" ? p : p.path)) : [];
}

// Open the browser's file dialog. The <input> must belong to this page: one made by el() lives in the template's
// inert document, where click() does nothing (no dialog at all), so it is created here and kept in the page.
function pickFiles(accept, multiple, onFiles) {
  if (TAURI) {                                                  // native dialog; File objects as in a browser
    nativeOpen(accept, multiple).then(async (paths) => {
      if (!paths.length) return;
      onFiles(await Promise.all(paths.map(async (p) => new File([await TAURI.fs.readFile(p)], baseName(p)))));
    }).catch((e) => toast("Open failed: " + e));
    return;
  }
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = accept;
  inp.multiple = !!multiple;
  inp.style.display = "none";
  inp.onchange = () => { const files = [...inp.files]; inp.remove(); if (files.length) onFiles(files); };
  document.body.appendChild(inp);
  inp.click();
}
// Save a file: a link that belongs to this page (see pickFiles), clicked, then removed.
function download(href, name = "") {
  if (TAURI) {                                                  // native Save dialog, then write the file
    (async () => {
      const res = await fetch(href);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const disp = res.headers.get("Content-Disposition") || "";
      const suggested = name || (disp.match(/filename="?([^";]+)"?/) || [])[1] || "download";
      const path = await TAURI.dialog.save({ defaultPath: suggested });
      if (!path) return;
      await TAURI.fs.writeFile(path, new Uint8Array(await res.arrayBuffer()));
      toast(`Saved ${path}`, true);
    })().catch((e) => toast("Save failed: " + e));
    return;
  }
  const a = document.createElement("a");
  a.href = href;
  if (name) a.download = name;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function toast(text, ok = false) {
  const t = el(`<div class="toast${ok ? " ok" : ""}">${esc(text)}</div>`);
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), ok ? 2500 : 6000);
}

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(path, opt); } catch (e) { toast("Server not reachable"); throw e; }
  const data = res.headers.get("Content-Type")?.includes("json") ? await res.json() : null;
  if (!res.ok) { toast(data?.error || `${res.status} ${res.statusText}`); throw new Error(data?.error || res.statusText); }
  return data;
}
const post = (p, b = {}) => api("POST", p, b);

function menu(x, y, items) {
  closeMenu();
  const m = el(`<div class="menu"></div>`);
  for (const it of items) {
    if (it === "-") { m.appendChild(el("<hr>")); continue; }
    const d = el(`<div>${esc(it.label)}</div>`);
    d.onclick = () => { closeMenu(); it.action(); };
    m.appendChild(d);
  }
  document.body.appendChild(m);
  m.style.left = Math.min(x, innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = Math.min(y, innerHeight - m.offsetHeight - 8) + "px";
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
}
function closeMenu() { $$(".menu").forEach((m) => m.remove()); }

function modal(title, bodyHtml, buttons) {
  const o = el(`<div class="overlay"><div class="modal"><header>${esc(title)}</header><div class="mb">${bodyHtml}</div><footer></footer></div></div>`);
  const close = () => o.remove();
  for (const b of buttons) {
    const btn = el(`<button class="btn ${b.kind || ""}">${esc(b.label)}</button>`);
    if (b.left) btn.style.marginRight = "auto";
    btn.onclick = async () => { if ((await b.action?.(o)) !== false) close(); };
    $("footer", o).appendChild(btn);
  }
  o.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  document.body.appendChild(o);
  return o;
}

// ---------- global state ----------
let S = null;                 // /api/state
let DB = [];                  // /api/databases
let dbVersion = -1;
let stateVersion = -1;
const unseen = { log: 0 };

// ---------- desktops / rail / theme / dividers ----------
function showDesk(name) {
  $$(".desktop").forEach((d) => d.classList.toggle("active", d.id === "desk-" + name));
  $$(".rail-btn[data-desk]").forEach((b) => b.classList.toggle("active", b.dataset.desk === name));
  try { localStorage.setItem("autosim.desk", name); } catch (e) {}
  Trace.renderAll(true);
}
$$(".rail-btn[data-desk]").forEach((b) => b.onclick = () => showDesk(b.dataset.desk));
$("#theme-btn").onclick = () => {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("autosim.theme", t); } catch (e) {}
};
function toggleLog(on) {
  const p = $("#logpane");
  const show = on ?? p.classList.contains("hidden");
  p.classList.toggle("hidden", !show);
  $("#log-btn").classList.toggle("active", show);
  if (show) { unseen.log = 0; $("#log-label").textContent = "Log"; $("#log").scrollTop = 1e9; }
}
$("#log-btn").onclick = () => toggleLog();
$("#log-close").onclick = () => toggleLog(false);
$("#log-clear").onclick = () => { $("#log").innerHTML = ""; };

$$(".divider").forEach((d) => {
  d.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const desk = document.getElementById(d.dataset.desk), r = desk.getBoundingClientRect(), vertical = d.classList.contains("h");
    d.classList.add("drag");
    const move = (ev) => {
      const f = vertical ? (ev.clientY - r.top) / r.height : (ev.clientX - r.left) / r.width;
      desk.style.setProperty(d.dataset.var, Math.max(15, Math.min(85, f * 100)) + "%");
    };
    const up = () => { d.classList.remove("drag"); removeEventListener("mousemove", move); Trace.renderAll(true); };
    addEventListener("mousemove", move);
    addEventListener("mouseup", up, { once: true });
  });
});

function addLog(t, text) {
  const d = new Date(t * 1000);
  const line = el(`<div><span class="t">${d.toLocaleTimeString()}</span>${esc(text)}</div>`);
  const box = $("#log");
  const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.appendChild(line);
  while (box.childElementCount > 1000) box.firstElementChild.remove();
  if (atEnd) box.scrollTop = 1e9;
  if ($("#logpane").classList.contains("hidden")) { unseen.log++; $("#log-label").textContent = `Log · ${unseen.log}`; }
}

// ---------- state ----------
let refreshing = null;
async function refreshState() {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      S = await api("GET", "/api/state");
      stateVersion = S.version;
      if (S.db_version !== dbVersion) { DB = (await api("GET", "/api/databases")).databases; dbVersion = S.db_version; }
      Channels.render(); Database.render(); Generator.render(); Rules.render(); Trace.channelsChanged();
      TestApi.load().catch(() => {});
    } finally { refreshing = null; }
  })();
  return refreshing;
}

// ---------- Channels ----------
const Channels = {
  kind(port) {
    const p = S?.ports.find((x) => x.port.toUpperCase() === port.toUpperCase());
    if (p) return p.kind;
    if (/^bridge:\/\//i.test(port) || port.toUpperCase() === "VIRTUAL CAN") return "bridge";
    if (/^PCAN_/i.test(port)) return "pcan";
    return /^SIM/i.test(port) ? "sim" : "autosim";
  },
  portInfo(port) { return S?.ports.find((x) => x.port.toUpperCase() === port.toUpperCase()); },
  open(ch) { return S?.channels.find((c) => c.channel === ch); },
  owner(port) { return /^SIM/i.test(port) ? null : S?.channels.find((c) => c.port.toUpperCase() === port.toUpperCase()); },
  nextName() { let n = 1; while (S.channels.some((c) => c.channel === `CAN${n}`)) n++; return `CAN${n}`; },
  init() {
    $("#scan-btn").onclick = async () => { await post("/api/scan"); await refreshState(); toast("Scan done", true); };
    $("#f-port").addEventListener("change", () => this.deviceChanged());
    $("#f-port").addEventListener("input", () => this.deviceChanged(false));
    $("#f-nominal").onchange = () => this.fillRates();
    $("#f-fd").onchange = () => this.sync();
    $("#f-channel").oninput = () => this.syncButton();
    $("#connect-btn").onclick = () => this.toggle();
    $("#connect-all-btn").onclick = () => this.connectAll();
    $("#gen-connect-all").onclick = () => this.connectAll();
    $("#gen-disconnect-all").onclick = () => this.disconnectAll();
  },
  formLoaded: false,
  render() {
    const dl = $("#ports");
    dl.innerHTML = S.ports.map((p) => `<option value="${esc(p.port)}">${esc(p.label)}</option>`).join("");
    const nom = $("#f-nominal");
    if (!nom.options.length) nom.innerHTML = S.nominal_rates.map((v) => `<option value="${v}">${rate(v)}</option>`).join("");
    if (!this.formLoaded) {
      this.formLoaded = true;
      const f = S.form;
      $("#f-port").value = f.port; nom.value = f.nominal;
      $("#f-fd").checked = f.fd; $("#f-brs").checked = f.brs; $("#f-iso").checked = f.iso; $("#f-loop").checked = f.loop;
      $("#f-channel").value = this.nextName();
      this.deviceChanged(true, f.data);
    }
    const tb = $("#chan-table tbody");
    tb.innerHTML = S.channels.map((c) => `<tr data-ch="${esc(c.channel)}"><td>${esc(c.channel)}</td>
      <td title="Double-click to rename the board">${esc(c.node)}</td><td>${esc(c.port)}</td>
      <td class="state-${esc(c.state)}">${esc(c.state)}</td><td class="r">${c.rx}</td><td class="r">${c.tx}</td>
      <td class="r">${c.errors}</td><td><button class="btn sm icon" title="Disconnect ${esc(c.channel)}" data-x>✕</button></td></tr>`).join("");
    $$("tr", tb).forEach((tr) => {
      const ch = tr.dataset.ch;
      tr.onclick = (e) => { if (e.target.closest("[data-x]")) return; this.load(ch); };
      tr.ondblclick = () => this.rename(ch);
      $("[data-x]", tr).onclick = () => this.disconnect(ch);
    });
    this.badge(); this.syncButton();
  },
  updateNodes(nodes) {
    if (!S) return;
    for (const n of nodes) {
      const c = this.open(n.channel);
      if (c) Object.assign(c, { state: n.state, rx: n.rx, tx: n.tx, errors: n.errors, node: n.node });
      const tr = $(`#chan-table tr[data-ch="${CSS.escape(n.channel)}"]`);
      if (tr) {
        const td = tr.children;
        td[1].textContent = n.node; td[3].textContent = n.state; td[3].className = "state-" + n.state;
        td[4].textContent = n.rx; td[5].textContent = n.tx; td[6].textContent = n.errors;
      }
    }
    this.badge();
  },
  badge() {
    const chans = S.channels, b = $("#chan-badge"), sim = $("#sim-status");
    const bad = chans.filter((c) => c.state === "error").length, running = chans.filter((c) => c.state === "running").length;
    let text = "Offline", cls = "";
    if (bad) { text = "Error"; cls = "err"; }
    else if (chans.length && running < chans.length) { text = "Connecting"; cls = "warn"; }
    else if (running) { text = `${running} connected`; cls = "online"; }
    b.textContent = text; b.className = "pill " + cls;
    sim.textContent = bad ? "Error" : (chans.length && running < chans.length) ? "Connecting" :
      running ? `Active ${running} channel${running > 1 ? "s" : ""}` : "Inactive";
    sim.className = "pill status-pill " + cls;
    sim.title = chans.map((c) => `${c.channel}: ${c.port} · ${c.state}`).join("\n") || "No channel open";
  },
  deviceChanged(keep = true, preferData) {
    const port = $("#f-port").value.trim(), kind = this.kind(port), info = this.portInfo(port);
    const owner = this.owner(port);
    if (owner) $("#f-channel").value = owner.channel;
    else if (this.open($("#f-channel").value.trim())) $("#f-channel").value = this.nextName();
    $("#port-hint").textContent = { slcan: "CANable 2.0 · slcan", sim: "simulated node", bridge: "Android head unit · vcan over TCP",
      pcan: `${info?.label || "PCAN"} · PEAK`, autosim: "serial · ESP32 + MCP2518FD firmware?" }[kind] || "";
    const fdOk = kind !== "pcan" || (info?.fd ?? true);
    if (!fdOk) $("#f-fd").checked = false;
    $("#f-fd").disabled = !fdOk;
    const fixedIso = ["slcan", "pcan", "bridge"].includes(kind);
    if (kind === "slcan" || kind === "pcan") $("#f-iso").checked = true;
    $("#f-iso").disabled = fixedIso;
    $("#f-nominal").disabled = kind === "bridge";
    $("#f-loop").disabled = kind === "bridge";
    if (kind === "bridge") $("#f-loop").checked = false;
    this.fillRates(preferData);
  },
  async fillRates(prefer) {
    const port = $("#f-port").value.trim(), nominal = +$("#f-nominal").value || 500000;
    const sel = $("#f-data"), cur = prefer ?? +sel.value ?? 2000000;
    let rates = [];
    try { rates = (await api("GET", `/api/rates?port=${encodeURIComponent(port)}&nominal=${nominal}`)).rates; } catch (e) { return; }
    sel.innerHTML = rates.map((v) => `<option value="${v}">${rate(v)}${v === nominal ? "  (= nominal)" : ""}</option>`).join("");
    const best = rates.reduce((a, v) => Math.abs(v - (cur || 2e6)) < Math.abs(a - (cur || 2e6)) ? v : a, rates[0]);
    sel.value = best;
    this.sync();
  },
  sync() {
    const kind = this.kind($("#f-port").value.trim());
    $("#f-data").disabled = !$("#f-fd").checked || kind === "bridge";
    $("#f-brs").disabled = !$("#f-fd").checked;
    $$(".chk").forEach((l) => l.classList.toggle("dis", !!$("input", l)?.disabled));
  },
  syncButton() {
    const b = $("#connect-btn"), open = S && this.open($("#f-channel").value.trim());
    b.textContent = open ? "Disconnect" : "Connect";
    b.className = "btn " + (open ? "danger" : "primary");
  },
  settings() {
    return { nominal: +$("#f-nominal").value, data: +$("#f-data").value, fd: $("#f-fd").checked, brs: $("#f-brs").checked,
      iso: $("#f-iso").checked, loop: $("#f-loop").checked };
  },
  load(ch) {
    const c = this.open(ch); if (!c) return;
    $("#f-port").value = c.port; $("#f-channel").value = ch; $("#f-nominal").value = c.nominal;
    $("#f-fd").checked = c.fd; $("#f-brs").checked = c.brs; $("#f-iso").checked = c.iso; $("#f-loop").checked = c.loop;
    this.deviceChanged(true, c.data); this.syncButton();
    $$("#chan-table tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.ch === ch));
  },
  async toggle() {
    const ch = $("#f-channel").value.trim();
    if (this.open(ch)) return this.disconnect(ch);
    const b = $("#connect-btn"); b.disabled = true; b.textContent = "Connecting…";
    try {
      const r = await post("/api/connect", { port: $("#f-port").value.trim(), channel: ch, ...this.settings() });
      await refreshState();
      toast(`${r.channel} connected`, true);
      const free = S.ports.find((p) => ["slcan", "pcan"].includes(p.kind) && !this.owner(p.port));
      if (free) { $("#f-port").value = free.port; this.deviceChanged(); }
      $("#f-channel").value = this.nextName();
    } catch (e) { /* toast shown */ } finally { b.disabled = false; this.syncButton(); }
  },
  async connectAll() {
    try {
      const r = await post("/api/connect_all", this.settings());
      if (r.failed.length) toast("Could not open: " + r.failed.join("; "));
      if (r.opened.length) toast(`Connected ${r.opened.join(", ")}`, true);
    } catch (e) { /* toast */ }
    await refreshState();
  },
  async disconnect(ch) {
    await post("/api/disconnect", { channel: ch });
    await refreshState();
  },
  async disconnectAll() {
    if (S.cards.some((c) => c.enabled) && !confirm("Cyclic transmission is running. Disconnect all channels?")) return;
    await post("/api/disconnect_all");
    await refreshState();
  },
  async rename(ch) {
    const name = prompt(`New name stored in the board of ${ch}:`, this.open(ch)?.node || "");
    if (name) { await post("/api/rename", { channel: ch, name }); await refreshState(); }
  },
};

// ---------- Database ----------
const SEARCH_MAX = 300;                 // messages listed for a search (more: type more)
const Database = {
  expanded: new Set(), selected: null, search: "",
  render() {
    const body = $("#db-body");
    if (!DB.length) {
      body.innerHTML = `<div class="empty"><div class="ico">🗄</div><h3>No database in the configuration</h3>
        <p>Add a DBC or ARXML file, or drop it here, to browse its nodes, messages and signals, and to choose on which channel it is used.</p>
        <button class="btn primary" data-add>Add database…</button></div>`;
      $("[data-add]", body).onclick = () => this.pick();
      return;
    }
    if (!$("#db-tree", body)) {
      body.innerHTML = `<div class="toolbar"><button class="btn" data-add>Add database…</button>
        ${TAURI ? "" : `<button class="btn" data-path title="Add a file that is on the server PC">Add by path…</button>`}
        <button class="btn icon" data-reload title="Reload the selected database">⟳</button>
        <button class="btn icon" data-remove title="Remove the selected database">🗑</button>
        <input id="db-search" placeholder="Search message, signal or ID" style="flex:1"></div>
        <div id="db-split"><div class="tree" id="db-tree"></div><div class="props"><h3 id="props-title" class="subhead">Properties</h3>
        <div class="tablebox"><table class="grid"><thead><tr><th style="width:110px">Property</th><th>Value</th></tr></thead><tbody id="props"></tbody></table></div></div></div>`;
      $("[data-add]", body).onclick = () => this.pick();
      if (!TAURI) $("[data-path]", body).onclick = () => this.addPath();
      $("[data-reload]", body).onclick = () => { const p = this.dbOf(this.selected); if (p) post("/api/databases/reload", { path: p }).then(refreshState); };
      $("[data-remove]", body).onclick = () => { const p = this.dbOf(this.selected); if (p) this.remove(p); };
      $("#db-search").oninput = (e) => {                    // a large database: wait for a pause in the typing
        clearTimeout(this._typing);
        this._typing = setTimeout(() => { this.search = e.target.value.trim().toLowerCase(); this.renderTree(); }, 150);
      };
      this._db = null;
    }
    for (const d of DB) if (!this.expanded.has("db:" + d.path + ":init")) {
      this.expanded.add("db:" + d.path + ":init"); this.expanded.add("db:" + d.path); this.expanded.add("msgs:" + d.path);
    }
    if (this._db !== DB) { this._db = DB; this.renderTree(); }  // the tree only changes with the databases
    this.renderProps();
  },
  dbOf(key) { return key ? (DB.find((d) => key.includes(":" + d.path)) || DB.find((d) => key.startsWith("db:" + d.path)))?.path : null; },
  pick() {
    if (TAURI) {                         // desktop app: the files are used where they are (Reload reads them again)
      nativeOpen(".dbc,.arxml,.kcd,.sym", true).then((paths) => this.addPaths(paths)).catch((e) => toast("Open failed: " + e));
      return;
    }
    pickFiles(".dbc,.arxml,.kcd,.sym", true, (files) => this.upload(files));
  },
  async addPaths(paths) {
    const added = [];
    for (const p of paths) {
      try { added.push(...(await post("/api/databases/add", { path: p })).added); } catch (e) { /* toast shown */ }
    }
    if (added.length) toast(`Added ${added.join(", ")}`, true);
    await refreshState();
  },
  async upload(files) {
    if (!files.length) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("file", f, f.name));
    try { const r = await api("POST", "/api/databases/upload", fd); toast(`Added ${r.added.join(", ")}`, true); } catch (e) { /* toast */ }
    await refreshState();
  },
  async addPath() {
    const p = prompt("Path of the database file on the server PC:");
    if (p) { await post("/api/databases/add", { path: p }); await refreshState(); }
  },
  async remove(path) {
    const name = DB.find((d) => d.path === path)?.name;
    if (S.cards.length && !confirm(`Remove ${name}? Messages of it in the generator are removed too.`)) return;
    await post("/api/databases/remove", { path }); this.selected = null; await refreshState();
  },
  async use(path, channel) { await post("/api/databases/use", { path, channel }); await refreshState(); },
  matches(m) {
    const q = this.search;
    if (!q) return true;
    const hex = q.replace(/^0x/, "");
    return m.name.toLowerCase().includes(q) || m.signals.some((s) => s.name.toLowerCase().includes(q)) ||
      (/^[0-9a-f]+$/.test(hex) && m.id.toString(16) === hex.replace(/^0+(?=.)/, ""));
  },
  renderTree() {
    const out = [], ex = this.expanded, q = !!this.search;
    const line = (depth, key, label, aux = "", kids = false, cls = "", dlc = null) => {
      const open = q || ex.has(key);
      out.push(`<div class="node${this.selected === key ? " sel" : ""} ${cls}" data-key="${esc(key)}" style="padding-left:${4 + depth * 16}px">
        <span class="tw" data-tw>${kids ? (open ? "▾" : "▸") : ""}</span><span class="nm">${label}</span><span class="aux">${esc(aux)}</span>${dlc ?? ""}</div>`);
      return open;
    };
    for (const d of DB) {
      const msgs = d.messages.filter((m) => this.matches(m));
      if (q && !msgs.length) continue;
      const state = d.active ? "in use · " + (d.channel === "*" ? "all channels" : d.channel) : "not used";
      if (!line(0, "db:" + d.path, `<b>${esc(d.name)}</b>`, d.readable ? state : "cannot be read", true)) continue;
      if (!q && line(1, "nodes:" + d.path, "Nodes", "", d.nodes.length > 0)) {
        for (const n of d.nodes) {
          if (line(2, "node:" + d.path + ":" + n.name, esc(n.name), "", n.tx.length > 0) && n.tx.length &&
              line(3, "tx:" + d.path + ":" + n.name, "Tx messages", "", true)) {
            for (const mn of n.tx) {
              const m = d.messages.find((x) => x.name === mn);
              if (m) this.msgLines(line, 4, `nmsg:${d.path}:${n.name}:`, m);
            }
          }
        }
      }
      if (line(1, "msgs:" + d.path, `Messages (${d.messages.length})`, "", true)) {
        const shown = q ? msgs.slice(0, SEARCH_MAX) : msgs;          // a search hit in every message: keep it fast
        for (const m of shown) this.msgLines(line, 2, `msg:${d.path}:`, m);
        if (shown.length < msgs.length) out.push(`<div class="tree-more muted">… ${msgs.length - shown.length} more messages match - type more to narrow the search</div>`);
      }
    }
    const tree = $("#db-tree");
    tree.innerHTML = out.join("") || `<div class="muted" style="padding:8px">No match</div>`;
    $$(".node", tree).forEach((n) => {
      const key = n.dataset.key;
      $("[data-tw]", n).onclick = (e) => { e.stopPropagation(); ex.has(key) ? ex.delete(key) : ex.add(key); this.renderTree(); };
      n.onclick = () => { this.selected = key; $$(".node.sel", tree).forEach((x) => x.classList.remove("sel")); n.classList.add("sel"); this.renderProps(); };
      n.ondblclick = () => { const m = this.message(key); if (m) Generator.add([m.name]); else { ex.has(key) ? ex.delete(key) : ex.add(key); this.renderTree(); } };
      n.oncontextmenu = (e) => { e.preventDefault(); n.click(); this.context(e, key); };
    });
  },
  msgLines(line, depth, prefix, m) {
    const fd = m.fd || m.length > 8;
    const dlc = `<span class="dlc${fd ? " fd" : ""}" title="${fd ? "CAN FD" : "Classic CAN"} frame, ${m.length} bytes">${m.length}${fd ? " FD" : ""}</span>`;
    const q = this.search;
    const sigs = q && !m.name.toLowerCase().includes(q) && m.signals.some((s) => s.name.toLowerCase().includes(q))
      ? m.signals.filter((s) => s.name.toLowerCase().includes(q)) : m.signals;   // a signal hit: only those signals
    if (line(depth, prefix + m.name, esc(m.name), idText(m.id, m.extended), m.signals.length > 0, "", dlc)) {
      for (const s of sigs) line(depth + 1, "sig:" + prefix + m.name + ":" + s.name, esc(s.name), "", false);
    }
  },
  findDb(key) { return DB.find((d) => key.split(":").slice(1).join(":").startsWith(d.path)); },
  message(key) {
    if (!key || !/^(msg|nmsg):/.test(key)) return null;
    const d = this.findDb(key); if (!d) return null;
    const name = key.split(":").pop();
    return d.messages.find((m) => m.name === name);
  },
  signal(key) {
    if (!key?.startsWith("sig:")) return null;
    const parts = key.split(":"), sname = parts.pop(), mname = parts.pop();
    const d = this.findDb(key.slice(4)); const m = d?.messages.find((x) => x.name === mname);
    return m ? { m, s: m.signals.find((x) => x.name === sname) } : null;
  },
  renderProps() {
    const key = this.selected, tb = $("#props"); if (!tb) return;
    let title = "Properties", rows = [];
    if (key?.startsWith("db:")) {
      const d = DB.find((x) => "db:" + x.path === key);
      if (d) {
        title = `Database ${d.name}`;
        const chans = [...new Set([...S.channels.map((c) => c.channel), ...(d.channel !== "*" ? [d.channel] : [])])].sort();
        const cur = d.active ? d.channel : "";
        const sel = `<select id="db-channel" title="Where the database is used: all channels, one channel, or not at all">
          <option value="*">All channels</option>${chans.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}<option value="">Not used</option></select>`;
        rows = [["File", esc(d.path)], ["Channel", sel], ["Nodes", d.nodes.length], ["Messages", d.messages.length],
          ["Signals", d.messages.reduce((a, m) => a + m.signals.length, 0)]];
        title = `Database ${esc(d.name)}`;
        tb.innerHTML = rows.map(([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join("");
        const s = $("#db-channel"); s.value = cur; s.onchange = () => this.use(d.path, s.value || null);
        $("#props-title").innerHTML = title;
        return;
      }
    } else if (this.message(key)) {
      const m = this.message(key);
      title = `Message ${m.name}`;
      rows = [["Name", m.name], ["ID", idText(m.id, m.extended)], ["Frame", (m.fd || m.length > 8 ? "CAN FD" : "CAN") + (m.extended ? ", extended" : "")],
        ["DLC", `${m.length} bytes`], ["Cycle time", m.cycle ? `${m.cycle} ms` : "—"], ["Senders", m.senders.join(", ") || "—"],
        ["Signals", m.signals.length]];
      if (m.comment) rows.push(["Comment", m.comment]);
    } else if (this.signal(key)) {
      const { m, s } = this.signal(key);
      title = `Signal ${s.name}`;
      rows = [["Name", s.name], ["Message", m.name], ["Start bit", s.start], ["Length", `${s.length} bit`], ["Byte order", s.order],
        ["Signed", s.signed ? "yes" : "no"], ["Factor", fmt(s.factor)], ["Offset", fmt(s.offset)], ["Minimum", fmt(s.min)],
        ["Maximum", fmt(s.max)], ["Unit", s.unit], ["Initial", fmt(s.initial)]];
      const ch = Object.entries(s.choices);
      if (ch.length) rows.push(["Values", ch.map(([k, v]) => `${k} = ${v}`).join("\n")]);
      if (s.comment) rows.push(["Comment", s.comment]);
    } else if (key?.startsWith("node:")) { title = `Node ${key.split(":").pop()}`; rows = [["Name", key.split(":").pop()]]; }
    $("#props-title").textContent = title;
    tb.innerHTML = rows.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td style="white-space:pre-wrap">${esc(v)}</td></tr>`).join("");
  },
  context(e, key) {
    const items = [];
    const m = this.message(key);
    if (m) items.push({ label: "Add to generator", action: () => Generator.add([m.name]) });
    if (key.startsWith("db:")) {
      const d = DB.find((x) => "db:" + x.path === key);
      items.push({ label: "Use on all channels", action: () => this.use(d.path, "*") });
      for (const c of S.channels) items.push({ label: `Use on ${c.channel}`, action: () => this.use(d.path, c.channel) });
      if (d.active) items.push({ label: "Do not use", action: () => this.use(d.path, null) });
      items.push("-", { label: "Reload", action: () => post("/api/databases/reload", { path: d.path }).then(refreshState) },
        { label: "Copy path", action: () => navigator.clipboard?.writeText(d.path) }, "-",
        { label: `Remove ${d.name}`, action: () => this.remove(d.path) });
    }
    if (items.length) menu(e.clientX, e.clientY, items);
  },
};
const dbCard = $("#db-card");
dbCard.addEventListener("dragover", (e) => { e.preventDefault(); dbCard.classList.add("drop"); });
dbCard.addEventListener("dragleave", () => dbCard.classList.remove("drop"));
dbCard.addEventListener("drop", (e) => { e.preventDefault(); dbCard.classList.remove("drop"); Database.upload([...e.dataTransfer.files]); });

// ---------- Interactive generator ----------
const Generator = {
  folded: new Set(),
  async add(names) {
    if (!names.length) return;
    await post("/api/tx/add", { messages: names });
    await refreshState();
    toast(`Added ${names.join(", ")} to the generator`, true);
  },
  render() {
    const body = $("#gen-body");
    if (!S.cards.length) {
      const hasDb = DB.some((d) => d.active);
      body.innerHTML = `<div class="empty"><div class="ico">〜</div><h3>${hasDb ? "No message yet" : "No database in use"}</h3>
        <p>${hasDb ? "Add the messages and signals to send." : "Add a .dbc / .arxml file in Setup > Database to start."}</p>
        <button class="btn primary" data-go>${hasDb ? "Add signals…" : "Open Setup"}</button></div>`;
      $("[data-go]", body).onclick = () => hasDb ? this.dialog() : showDesk("setup");
      return;
    }
    // Card by card: a card is rebuilt only when its layout changed (channel, cycle, Cyclic, generators, …);
    // the others keep their DOM and only get the new values and counters. With 200 messages a click on Cyclic
    // rebuilt 3200 signal rows; now it rebuilds one card.
    const chans = S.channels.map((c) => c.channel);
    if (!$(".msgcard", body)) { body.innerHTML = ""; this._keys = new Map(); }
    const keep = new Set();
    S.cards.forEach((c, i) => {
      const key = JSON.stringify([this.folded.has(c.id), chans, { ...c, count: 0, signals: c.signals.map((x) => ({ ...x, value: 0 })) }]);
      let card = $(`.msgcard[data-id="${c.id}"]`, body);
      if (!card || this._keys.get(c.id) !== key) {
        const fresh = el(this.cardHtml(c, chans));
        if (card) card.replaceWith(fresh); else body.appendChild(fresh);
        card = fresh; this._keys.set(c.id, key); this.wire(card, c.id);
      } else {
        for (const x of c.signals) this.showValue(card, c.id, x.name, x.value);
      }
      $("[data-count]", card).textContent = c.count ? `${c.count} sent` : "";
      if (body.children[i] !== card) body.insertBefore(card, body.children[i] || null);
      keep.add(card);
    });
    for (const x of $$(".msgcard", body)) if (!keep.has(x)) { this._keys.delete(+x.dataset.id); body.removeChild(x); }
  },
  cardHtml(c, chans) {
    const opts = [...new Set([...chans, c.channel].filter(Boolean))];
    return `<div class="msgcard${this.folded.has(c.id) ? " folded" : ""}" data-id="${c.id}">
      <div class="mh"><span class="tw-t" data-fold>${this.folded.has(c.id) ? "▸" : "▾"}</span>
        <span class="name">${esc(c.message)}</span><span class="mono muted">${idText(c.can_id, c.extended)}</span>
        <select class="sm" data-k="channel" title="Channel that sends this message">${c.channel ? "" : `<option value="">(no channel)</option>`}
          ${opts.map((o) => `<option${o === c.channel ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>
        <input class="sm cyc" type="number" min="1" max="60000" value="${c.cycle}" data-k="cycle" title="Cycle time in ms"><span class="muted">ms</span>
        <label class="chk"><input type="checkbox" data-k="fd"${c.fd ? " checked" : ""}${c.length > 8 ? " disabled" : ""}>FD</label>
        <label class="chk"><input type="checkbox" data-k="brs"${c.brs ? " checked" : ""}${c.fd ? "" : " disabled"}>BRS</label>
        <span class="grow" style="flex:1"></span><span class="count" data-count></span>
        <button class="btn sm${c.enabled ? " run" : ""}" data-cyclic title="Start / stop periodic transmission">Cyclic</button>
        <button class="btn sm" data-send title="Send once">Send</button>
        <button class="btn sm icon" data-more title="More">⋯</button></div>
      <table class="grid"><thead><tr><th>Signal</th><th style="width:90px" class="r">Raw</th><th style="width:110px" class="r">Physical</th>
        <th style="width:52px">Unit</th><th style="width:230px">Generator</th><th style="width:130px">Symbolic</th></tr></thead><tbody>
        ${c.signals.map((s) => this.signalRow(c, s)).join("")}</tbody></table></div>`;
  },
  raw(s, v) { return Math.round((v - (s.offset || 0)) / (s.factor || 1)); },
  signalRow(c, s) {
    const g = s.gen, labels = g ? S.generators[g.kind] : [];
    const choices = Object.entries(s.choices);
    return `<tr data-sig="${esc(s.name)}" title="${esc(s.comment)}"><td>${esc(s.name)}</td>
      <td><input class="r mono" data-raw value="${this.raw(s, s.value)}"${g ? " disabled" : ""}></td>
      <td><input class="r mono" data-val value="${fmt(s.value)}"${g ? " disabled" : ""} title="${s.min !== null ? `${fmt(s.min)} … ${fmt(s.max)}` : ""}"></td>
      <td>${esc(s.unit)}</td>
      <td><div class="genparams"><select data-gen style="width:76px">${["none", "sine", "ramp", "square", "toggle"].map((k) =>
        `<option value="${k}"${(g?.kind || "none") === k ? " selected" : ""}>${k === "none" ? "Off" : k[0].toUpperCase() + k.slice(1)}</option>`).join("")}</select>
        ${g ? g.params.map((p, i) => `<input type="number" step="any" data-p="${i}" value="${fmt(p)}" title="${esc(labels[i])}" placeholder="${esc(labels[i])}">`).join("") : ""}</div></td>
      <td>${choices.length ? `<select data-sym${g ? " disabled" : ""}><option value=""></option>${choices.map(([k, v]) =>
        `<option value="${k}"${+k === s.value ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>` : ""}</td></tr>`;
  },
  wire(card, id) {
    const upd = (body) => post("/api/tx/update", { id, ...body }).then(refreshState).catch(refreshState);
    $("[data-fold]", card).onclick = () => { this.folded.has(id) ? this.folded.delete(id) : this.folded.add(id); this.render(); };
    $("[data-k=channel]", card).onchange = (e) => upd({ channel: e.target.value });
    $("[data-k=cycle]", card).onchange = (e) => upd({ cycle: +e.target.value });
    $("[data-k=fd]", card).onchange = (e) => upd({ fd: e.target.checked });
    $("[data-k=brs]", card).onchange = (e) => upd({ brs: e.target.checked });
    $("[data-cyclic]", card).onclick = (e) => upd({ enabled: !e.target.classList.contains("run") });
    $("[data-send]", card).onclick = () => post("/api/tx/send", { id }).then(() => toast("Sent", true)).catch(() => {});
    $("[data-more]", card).onclick = (e) => menu(e.clientX, e.clientY, [
      { label: "Remove message", action: () => post("/api/tx/remove", { id }).then(refreshState) }]);
    $$("tr[data-sig]", card).forEach((tr) => {
      const name = tr.dataset.sig;
      const setv = (body) => post("/api/tx/signal", { id, name, ...body }).then((r) => this.showValue(card, id, name, r.value)).catch(refreshState);
      const commit = (inp, key) => {
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); if (e.key === "Escape") { inp.dataset.cancel = 1; inp.blur(); } });
        inp.addEventListener("change", () => { if (inp.dataset.cancel) { delete inp.dataset.cancel; return refreshState(); } setv({ [key]: inp.value }); });
      };
      commit($("[data-val]", tr), "value");
      commit($("[data-raw]", tr), "raw");
      $("[data-gen]", tr).onchange = (e) => post("/api/tx/generator", { id, name, kind: e.target.value === "none" ? null : e.target.value }).then(refreshState);
      $$("[data-p]", tr).forEach((p) => p.onchange = () => {
        const params = $$("[data-p]", tr).map((x) => +x.value);
        post("/api/tx/generator", { id, name, kind: $("[data-gen]", tr).value, params }).then(refreshState);
      });
      const sym = $("[data-sym]", tr);
      if (sym) sym.onchange = () => sym.value !== "" && setv({ value: +sym.value });
    });
  },
  showValue(card, id, name, v) {
    const c = S.cards.find((x) => x.id === id), s = c?.signals.find((x) => x.name === name);
    if (!s) return;
    s.value = v;
    const tr = $(`tr[data-sig="${CSS.escape(name)}"]`, card);
    if (!tr) return;
    const val = $("[data-val]", tr), raw = $("[data-raw]", tr), sym = $("[data-sym]", tr);
    const set = (inp, text) => { if (document.activeElement !== inp && inp.value !== text) inp.value = text; };  // no needless repaint
    set(val, fmt(+(+v).toPrecision(8)));
    set(raw, String(this.raw(s, v)));
    if (sym) set(sym, Object.hasOwn(s.choices, String(v)) ? String(v) : "");
  },
  live(cards) {
    if (!S) return;
    for (const [id, info] of Object.entries(cards || {})) {
      const card = $(`.msgcard[data-id="${id}"]`);
      if (!card) continue;
      const cnt = $("[data-count]", card);
      if (cnt) cnt.textContent = info.count ? `${info.count} sent` : "";
      const c = S.cards.find((x) => x.id === +id);
      if (!c) continue;
      for (const s of c.signals) if (s.gen && info.values[s.name] !== undefined) this.showValue(card, +id, s.name, info.values[s.name]);
    }
  },
  dialog() {
    const active = DB.filter((d) => d.active);
    if (!active.length) { toast("No database in use: add one in Setup > Database"); return showDesk("setup"); }
    const have = new Set(S.cards.map((c) => c.message));
    const rows = active.map((d) => `<div class="node" style="padding-left:4px"><b>${esc(d.name)}</b><span class="aux">${esc(d.channel === "*" ? "all channels" : d.channel)}</span></div>` +
      d.messages.map((m) => `<div class="node" data-m="${esc(m.name)}" data-text="${esc((m.name + " " + m.id.toString(16) + " " + m.signals.map((s) => s.name).join(" ")).toLowerCase())}" style="padding-left:20px">
        <span class="tw" data-tw>▸</span><label class="chk nm"><input type="checkbox" data-msg="${esc(m.name)}"${have.has(m.name) ? " checked disabled" : ""}>${esc(m.name)}</label>
        <span class="aux">${idText(m.id, m.extended)}</span></div>
        <div class="sigs hidden" data-for="${esc(m.name)}">${m.signals.map((s) => `<div class="node" style="padding-left:56px"><label class="chk nm"><input type="checkbox" data-sigof="${esc(m.name)}"${have.has(m.name) ? " checked disabled" : ""}>${esc(s.name)}</label><span class="aux">${esc(s.unit)}</span></div>`).join("")}</div>`).join("")).join("");
    const o = modal("Add signals", `<input id="sel-search" placeholder="Search message, signal or ID" style="width:100%;margin-bottom:8px">
      <div class="tree" id="sel-tree" style="height:52vh">${rows}</div>`, [
      { label: "Cancel" },
      { label: "Add", kind: "primary", action: async (ov) => {
        const names = $$("[data-msg]", ov).filter((c) => c.checked && !c.disabled).map((c) => c.dataset.msg);
        if (!names.length) { toast("Tick the messages or signals to add"); return false; }
        await this.add(names);
      } }]);
    $$("[data-tw]", o).forEach((t) => t.onclick = () => {
      const m = t.closest("[data-m]").dataset.m, box = $(`[data-for="${CSS.escape(m)}"]`, o);
      box.classList.toggle("hidden"); t.textContent = box.classList.contains("hidden") ? "▸" : "▾";
    });
    $$("[data-sigof]", o).forEach((c) => c.onchange = () => { if (c.checked) $(`[data-msg="${CSS.escape(c.dataset.sigof)}"]`, o).checked = true; });
    $("#sel-search", o).oninput = (e) => {
      const q = e.target.value.trim().toLowerCase().replace(/^0x/, "");
      $$("[data-m]", o).forEach((n) => { const hit = !q || n.dataset.text.includes(q); n.classList.toggle("hidden", !hit);
        if (!hit) $(`[data-for="${CSS.escape(n.dataset.m)}"]`, o).classList.add("hidden"); });
    };
    $("#sel-search", o).focus();
  },
};
$("#add-signals").onclick = () => Generator.dialog();
$("#fold-all").onclick = () => {
  const all = S.cards.every((c) => Generator.folded.has(c.id));
  S.cards.forEach((c) => all ? Generator.folded.delete(c.id) : Generator.folded.add(c.id));
  $("#fold-all").textContent = all ? "Collapse messages" : "Expand messages";
  Generator.render();
};

// ---------- Auto response ----------
const OPS = ["==", "!=", "<", "<=", ">", ">="];
const Rules = {
  list() { return S.auto_response.rules.map((r) => { const o = { ...r }; for (const k of ["when", "condition", "response", "hits", "errors", "last_error", "disabled_reason"]) delete o[k]; return o; }); },
  async save(list, enabled) {
    const r = await api("PUT", "/api/rules", { rules: list, enabled });
    r.problems.forEach((p) => toast(p));
    await refreshState();
  },
  render() {
    const ar = S.auto_response;
    $("#ar-enabled").checked = ar.enabled;
    const tb = $("#ar-table tbody");
    tb.innerHTML = ar.rules.length ? ar.rules.map((r, i) => `<tr data-i="${i}" title="${esc(r.disabled_reason || r.last_error || "Double-click to edit")}"
      ${r.disabled_reason || r.last_error ? ' style="color:var(--error)"' : ""}><td><input type="checkbox" data-on${r.enabled ? " checked" : ""}></td>
      <td>${esc(r.name)}</td><td class="mono">${esc(r.when)}${r.rx_channel ? " · " + esc(r.rx_channel) : ""}</td><td>${esc(r.condition)}</td>
      <td>${esc(r.response)}</td><td class="r">${r.delay_ms} ms</td><td class="r">${r.hits}</td></tr>`).join("")
      : `<tr><td colspan="7" class="muted" style="text-align:center;height:60px">No scenario yet. Scenarios answer received frames automatically.</td></tr>`;
    $$("tr[data-i]", tb).forEach((tr) => {
      const i = +tr.dataset.i;
      $("[data-on]", tr).onchange = (e) => { const l = this.list(); l[i].enabled = e.target.checked; this.save(l); };
      tr.ondblclick = () => this.edit(i);
      tr.oncontextmenu = (e) => { e.preventDefault(); menu(e.clientX, e.clientY, [
        { label: "Edit…", action: () => this.edit(i) },
        { label: "Duplicate", action: () => { const l = this.list(); l.splice(i + 1, 0, { ...l[i], name: l[i].name + " copy" }); this.save(l); } },
        { label: "Delete", action: () => { const l = this.list(); l.splice(i, 1); this.save(l); } }]); };
    });
  },
  messages() { return DB.filter((d) => d.active).flatMap((d) => d.messages); },
  edit(i) {
    const l = this.list();
    const r = i === undefined ? { name: `Scenario ${l.length + 1}`, enabled: true, rx_channel: "", rx_id: 0, rx_extended: false, match: "any",
      signal: "", op: "==", value: "0", raw_offset: 0, raw_size: 1, raw_order: "little", tx_channel: "", tx_id: 0, tx_extended: false,
      tx_fd: false, tx_brs: false, tx_mode: "raw", tx_dlc: 8, tx_data: "", tx_value: "", tx_offset: 0, tx_size: 1, tx_order: "little",
      tx_signals: {}, delay_ms: 0 } : { ...l[i] };
    const msgs = this.messages();
    const chanOpts = (cur, any) => `<option value="">${any}</option>` + S.channels.map((c) => `<option${c.channel === cur ? " selected" : ""}>${esc(c.channel)}</option>`).join("");
    const msgOpts = `<option value="">(pick from the databases)</option>` + msgs.map((m) => `<option value="${esc(m.name)}">${esc(m.name)} ${idText(m.id, m.extended)}</option>`).join("");
    const hex = (v) => "0x" + (+v).toString(16).toUpperCase();
    const o = modal(i === undefined ? "Add auto response scenario" : "Edit auto response scenario", `
      <fieldset><legend>When</legend><div class="fg">
        <label>Name</label><input data-f="name" value="${esc(r.name)}"><label>Enabled</label><label class="chk"><input type="checkbox" data-f="enabled"${r.enabled ? " checked" : ""}>On</label>
        <label>Message received</label><select data-pick="rx">${msgOpts}</select><label>On channel</label><select data-f="rx_channel">${chanOpts(r.rx_channel, "Any channel")}</select>
        <label>ID</label><input data-f="rx_id" class="mono" value="${hex(r.rx_id)}"><label>Extended</label><label class="chk"><input type="checkbox" data-f="rx_extended"${r.rx_extended ? " checked" : ""}>29-bit</label>
      </div></fieldset>
      <fieldset><legend>Condition</legend><div class="fg">
        <label>Match</label><select data-f="match"><option value="any">Any payload</option><option value="signal">DBC signal</option><option value="raw">Raw value</option></select>
        <label>Signal</label><select data-f="signal"></select>
        <label>Compare</label><select data-f="op">${OPS.map((op) => `<option${op === r.op ? " selected" : ""}>${op}</option>`).join("")}</select>
        <label>Value</label><div class="row"><input data-f="value" value="${esc(r.value)}" style="flex:1;min-width:60px">
          <select data-vpick class="hidden" title="Values of the DBC value table"></select><span data-vinfo class="muted"></span></div>
        <label>Raw offset</label><input type="number" data-f="raw_offset" value="${r.raw_offset}"><label>Raw size / order</label>
        <div class="row"><select data-f="raw_size">${[1, 2, 3, 4, 8].map((n) => `<option${n === r.raw_size ? " selected" : ""}>${n}</option>`).join("")}</select>
          <select data-f="raw_order"><option value="little">Intel (LE)</option><option value="big">Motorola (BE)</option></select></div>
      </div></fieldset>
      <fieldset><legend>Send</legend><div class="fg">
        <label>Response message</label><select data-pick="tx">${msgOpts}</select><label>Channel</label><select data-f="tx_channel">${chanOpts(r.tx_channel, "Where the trigger came in")}</select>
        <label>ID</label><input data-f="tx_id" class="mono" value="${hex(r.tx_id)}"><label>Extended</label><label class="chk"><input type="checkbox" data-f="tx_extended"${r.tx_extended ? " checked" : ""}>29-bit</label>
        <label>FD / BRS</label><div class="row"><label class="chk"><input type="checkbox" data-f="tx_fd"${r.tx_fd ? " checked" : ""}>FD</label><label class="chk"><input type="checkbox" data-f="tx_brs"${r.tx_brs ? " checked" : ""}>BRS</label></div>
        <label>Delay ms</label><input type="number" min="0" data-f="delay_ms" value="${r.delay_ms}">
      </div></fieldset>
      <fieldset><legend>Payload</legend><div class="fg">
        <label>Mode</label><select data-f="tx_mode"><option value="raw">Raw bytes</option><option value="signal">DBC signals</option></select><span></span><span></span>
        <label data-raw>Length (DLC)</label><input data-raw type="number" min="0" max="64" data-f="tx_dlc" value="${r.tx_dlc}">
        <label data-raw>Base data (hex)</label><input data-raw class="mono" data-f="tx_data" value="${esc(r.tx_data)}" placeholder="e.g. 00 11 22">
        <label data-raw>Value / expression</label><input data-raw data-f="tx_value" value="${esc(r.tx_value)}" placeholder="e.g. rx*10+1  (rx = matched value)">
        <label data-raw>At offset / size</label><div data-raw class="row"><input type="number" min="0" data-f="tx_offset" value="${r.tx_offset}" style="width:70px">
          <select data-f="tx_size">${[1, 2, 3, 4, 8].map((n) => `<option${n === r.tx_size ? " selected" : ""}>${n}</option>`).join("")}</select>
          <select data-f="tx_order"><option value="little">LE</option><option value="big">BE</option></select></div>
      </div><div data-sigs style="margin-top:8px"></div></fieldset>`, [
      ...(i === undefined ? [] : [{ label: "Delete", kind: "danger", left: true, action: () => { l.splice(i, 1); return this.save(l); } }]),
      { label: "Cancel" },
      { label: "Save", kind: "primary", action: async (ov) => {
        const nr = read(ov);
        if (i === undefined) l.push(nr); else l[i] = nr;
        await this.save(l);
      } }]);
    const f = (k) => $(`[data-f="${k}"]`, o);
    f("match").value = r.match; f("raw_order").value = r.raw_order; f("tx_order").value = r.tx_order; f("tx_mode").value = r.tx_mode;
    const byId = (id, ext) => msgs.find((m) => m.id === id && m.extended === ext);
    const range = (s) => (s.min === null && s.max === null) ? "" : `${fmt(s.min) || "…"} … ${fmt(s.max) || "…"}`;
    const choices = (s) => Object.entries(s.choices).sort((a, b) => +a[0] - +b[0]);
    const pickOpts = (s, cur) => `<option value="">Pick…</option>` + choices(s).map(([k, v]) =>
      `<option value="${k}"${String(cur).trim() === k ? " selected" : ""}>${esc(v)} (${k})</option>`).join("");
    const condSignal = () => {
      const m = byId(parseInt(f("rx_id").value), f("rx_extended").checked);
      return f("match").value === "signal" ? m?.signals.find((s) => s.name === f("signal").value) : null;
    };
    const updateCond = () => {                       // value table, unit and range of the compared signal
      const s = condSignal(), pick = $("[data-vpick]", o);
      const has = !!(s && Object.keys(s.choices).length);
      pick.innerHTML = has ? pickOpts(s, f("value").value) : "";
      pick.classList.toggle("hidden", !has);
      $("[data-vinfo]", o).textContent = s ? [s.unit, range(s) && `[${range(s)}]`].filter(Boolean).join(" ") : "";
    };
    const fillSignals = () => {
      const m = byId(parseInt(f("rx_id").value), f("rx_extended").checked);
      f("signal").innerHTML = (m?.signals || []).map((s) => `<option${s.name === r.signal ? " selected" : ""}>${esc(s.name)}</option>`).join("") || `<option value="">(no DBC message with this ID)</option>`;
      updateCond();
    };
    // Then send: the signals like the Interactive generator - Raw, Physical and Symbolic show one value; an empty
    // row sends the initial value (shown as the greyed placeholder); Physical also takes rx, rx * 10 ...
    const isNum = (t) => /^\s*-?(\d+\.?\d*|\.\d+)(e-?\d+)?\s*$/i.test(t) || /^\s*0x[0-9a-f]+\s*$/i.test(t);
    const physText = (s, v) => { const d = Number.isInteger(s.factor) ? 0 : Math.min(6, (String(s.factor).split(".")[1] || "").length || 1); return v.toFixed(d); };
    const rawOf = (s, v) => (s.factor ? Math.round((v - s.offset) / s.factor) : null);
    const symOpts = (s) => `<option value="">—</option>` + choices(s).map(([k, v]) => `<option value="${k}">${k}: ${esc(v)}</option>`).join("");
    const fillTxSignals = () => {
      const m = byId(parseInt(f("tx_id").value), f("tx_extended").checked), box = $("[data-sigs]", o);
      if (f("tx_mode").value !== "signal") { box.innerHTML = ""; return; }
      box.innerHTML = m ? `<table class="grid tx-sigs"><thead><tr><th>Signal</th><th class="r" style="width:80px">Raw</th>
        <th class="r" style="width:120px">Physical</th><th style="width:60px">Unit</th><th style="width:140px">Symbolic</th>
        <th style="width:100px">Range</th></tr></thead><tbody>${m.signals.map((s) => {
          const cur = r.tx_signals[s.name] ?? "", init = s.initial ?? 0, iraw = rawOf(s, init);
          const sym = Object.keys(s.choices).length ? `<select data-tsp="${esc(s.name)}" title="Symbolic value from the DBC value table">${symOpts(s)}</select>` : "";
          return `<tr data-row="${esc(s.name)}"><td class="nm">${esc(s.name)}</td>
            <td><input class="r" data-tr="${esc(s.name)}" placeholder="${iraw ?? "—"}" title="Raw value (physical = raw × ${s.factor} + ${s.offset})"></td>
            <td><input class="r" data-ts="${esc(s.name)}" value="${esc(cur)}" placeholder="${physText(s, init)}"
              title="Empty: sends the initial value. A number, or an expression of the received value: rx, rx * 10"></td>
            <td>${esc(s.unit)}</td><td>${sym}</td><td class="mono muted">${esc(range(s))}</td></tr>`;
        }).join("")}</tbody></table><p class="muted" style="margin:4px 0 0">Greyed = initial value · Physical also takes rx, rx * 10 (the value received)</p>`
        : `<p class="muted">Choose a response message from the databases to set its signals.</p>`;
      if (!m) return;
      const sync = (s, from) => {                    // one value, three views
        const q = (a) => $(`[${a}="${CSS.escape(s.name)}"]`, box);
        const phys = q("data-ts"), raw = q("data-tr"), sel = q("data-tsp");
        if (from === "raw") {
          const t = raw.value.trim();
          if (t === "") phys.value = ""; else if (/^-?\d+$/.test(t)) phys.value = String(+(+t * s.factor + s.offset).toFixed(6));
        } else if (from === "sym") phys.value = sel.value;
        const t = phys.value.trim();
        const num = t !== "" && isNum(t) ? Number(t) : null;
        if (from !== "raw") raw.value = t === "" ? "" : num === null ? "rx" : String(rawOf(s, num) ?? "—");
        const shown = t === "" ? (s.initial ?? 0) : num;        // unset: the initial value, greyed like the others
        if (sel) {
          sel.value = shown !== null && Number.isInteger(shown) && s.choices[shown] !== undefined ? String(shown) : "";
          sel.classList.toggle("init", t === "");
        }
        $(`[data-row="${CSS.escape(s.name)}"]`, box).classList.toggle("set", t !== "");
      };
      for (const s of m.signals) {
        const q = (a) => $(`[${a}="${CSS.escape(s.name)}"]`, box);
        q("data-ts").oninput = () => sync(s, "phys");
        q("data-tr").oninput = () => sync(s, "raw");
        if (q("data-tsp")) q("data-tsp").onchange = () => sync(s, "sym");
        sync(s, "phys");
      }
    };
    const syncMode = () => {
      const m = f("match").value;
      f("signal").disabled = m !== "signal"; f("op").disabled = f("value").disabled = m === "any";
      f("raw_offset").disabled = f("raw_size").disabled = f("raw_order").disabled = m !== "raw";
      $$("[data-raw]", o).forEach((x) => x.style.display = f("tx_mode").value === "raw" ? "" : "none");
      fillTxSignals();
      updateCond();
    };
    $("[data-pick=rx]", o).onchange = (e) => { const m = msgs.find((x) => x.name === e.target.value); if (m) { f("rx_id").value = hex(m.id); f("rx_extended").checked = m.extended; fillSignals(); } };
    $("[data-pick=tx]", o).onchange = (e) => { const m = msgs.find((x) => x.name === e.target.value); if (m) { f("tx_id").value = hex(m.id); f("tx_extended").checked = m.extended; f("tx_dlc").value = m.length; f("tx_fd").checked = m.fd || m.length > 8; fillTxSignals(); } };
    f("rx_id").onchange = fillSignals; f("tx_id").onchange = fillTxSignals; f("match").onchange = syncMode; f("tx_mode").onchange = syncMode;
    f("signal").onchange = updateCond;
    $("[data-vpick]", o).onchange = (e) => { if (e.target.value !== "") f("value").value = e.target.value; };
    f("value").oninput = () => {
      const p = $("[data-vpick]", o);
      if (!p.classList.contains("hidden")) p.value = [...p.options].some((op) => op.value === f("value").value.trim()) ? f("value").value.trim() : "";
    };
    fillSignals(); syncMode();
    const read = (ov) => {
      const n = { ...r };
      for (const inp of $$("[data-f]", ov)) {
        const k = inp.dataset.f;
        if (inp.type === "checkbox") n[k] = inp.checked;
        else if (["rx_id", "tx_id"].includes(k)) n[k] = parseInt(inp.value) || 0;
        else if (["raw_offset", "raw_size", "tx_dlc", "tx_offset", "tx_size", "delay_ms"].includes(k)) n[k] = +inp.value || 0;
        else n[k] = inp.value;
      }
      n.tx_signals = {};
      $$("[data-ts]", ov).forEach((x) => { if (x.value.trim()) n.tx_signals[x.dataset.ts] = x.value.trim(); });
      if (n.tx_mode === "signal" && !Object.keys(n.tx_signals).length) n.tx_signals = { ...r.tx_signals };
      return n;
    };
  },
};
$("#ar-add").onclick = () => Rules.edit();
$("#ar-enabled").onchange = (e) => post("/api/rules/enabled", { enabled: e.target.checked }).then(refreshState);
$("#ar-more").onclick = (e) => menu(e.clientX, e.clientY, [
  { label: "Reset hit counters", action: () => post("/api/rules/reset").then(refreshState) },
  { label: "Export rules…", action: () => {
    const blob = new Blob([JSON.stringify({ enabled: S.auto_response.enabled, rules: Rules.list() }, null, 2)], { type: "application/json" });
    download(URL.createObjectURL(blob), "autosim_rules.json"); } },
  { label: "Import rules…", action: () => pickFiles(".json", false, async ([f]) => {
    try { const d = JSON.parse(await f.text()); await Rules.save(d.rules || d, d.enabled); toast("Rules imported", true); } catch (err) { toast("Not a rules file"); }
  }) }]);

// ---------- Trace ----------
const ROW_H = 22;
// Trace name filter: "Engine|Speed|ADRV_*|!CHECKSUM" - the same rules as the desktop app (models/trace_model.py)
function nameQuery(text) {
  const terms = text.split("|").map((t) => t.trim().toLowerCase());
  const re = (t) => /[*?]/.test(t) ? new RegExp("^" + t.replace(/[.+^${}()[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$") : null;
  const mk = (list) => list.map((t) => ({ t, re: re(t) }));
  const include = mk(terms.filter((t) => t && !t.startsWith("!")));
  const exclude = mk(terms.filter((t) => t.startsWith("!") && t.slice(1).trim()).map((t) => t.slice(1).trim()));
  const hit = (name, list) => { const n = name.toLowerCase(); return list.some((x) => x.re ? x.re.test(n) : n.includes(x.t)); };
  const q = {
    active: include.length + exclude.length > 0, include: include.length > 0,
    msgHit: (m) => include.length > 0 && hit(m, include),
    sigShown: (m, s) => !hit(s, exclude) && (!include.length || q.msgHit(m) || hit(s, include)),
    matches: (m, sigs) => {
      if (m && hit(m, exclude)) return false;
      if (!include.length || q.msgHit(m)) return true;
      return sigs().some((s) => hit(s, include) && !hit(s, exclude));
    },
  };
  return q;
}
function signalNamesOf(id) {                  // signals of a message id in the databases in use (cached per DB load)
  if (signalNamesOf.db !== DB) { signalNamesOf.db = DB; signalNamesOf.cache = new Map(); }
  let names = signalNamesOf.cache.get(id);
  if (!names) {
    const m = DB.filter((d) => d.active !== false).flatMap((d) => d.messages).find((x) => x.id === id);
    names = m ? m.signals.map((s) => s.name) : [];
    signalNamesOf.cache.set(id, names);
  }
  return names;
}
const RECENT_KEY = "autosim.trace.recent";
const Recent = {
  load() { try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(v) ? v.slice(0, 5) : []; } catch (e) { return []; } },
  save(list) { try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 5))); } catch (e) { /* private window */ } },
  remember() {
    const e = { id: $("#t-id").value.trim(), name: $("#t-name").value.trim() };
    if (!e.id && !e.name) return;
    this.save([e, ...this.load().filter((x) => x.id !== e.id || x.name !== e.name)]);
  },
  label: (e) => [e.name, e.id && `ID ${e.id}`].filter(Boolean).join("   ·   "),
};

// ---------- Trace filters (like a logcat viewer) ----------
// Named rules, each on / off: match a CAN ID list or ranges ("118, 3E9, 100-1FF") or message / signal names (the
// name filter syntax: "Engine|Speed", "ADRV_*", "!CHECKSUM"); then Mark the row with a colour, Show only the matching
// rows, or Hide them. Several Show only rules: a row matching any of them is shown. Hide wins over Show only.
// The first matching Mark / Show only rule gives the row its colour. Kept in this browser (localStorage).
const TF_KEY = "autosim.trace.filters";
const TF_COLORS = ["#F59E0B", "#EF4444", "#22C55E", "#3B82F6", "#A855F7", "#EC4899", "#14B8A6", "#84CC16"];
const TF_MODES = { mark: "Mark", only: "Show only", hide: "Hide" };
function idQuery(text) {
  const ranges = text.split(/[,;\s]+/).filter(Boolean).map((p) => {
    const [a, b] = p.split("-").map((x) => parseInt(x.trim().replace(/^0x/i, ""), 16));
    return [a, b === undefined ? a : b];
  }).filter(([a, b]) => !isNaN(a) && !isNaN(b)).map(([a, b]) => [Math.min(a, b), Math.max(a, b)]);
  return { valid: ranges.length > 0, test: (id) => ranges.some(([a, b]) => id >= a && id <= b) };
}
const TF = {
  list: [], on: true, active: [], only: [], hide: [], db: null,
  norm(f) {
    return { name: String(f.name || ""), by: f.by === "id" ? "id" : "name", pattern: String(f.pattern || ""),
      mode: TF_MODES[f.mode] ? f.mode : "mark", color: TF_COLORS.includes(f.color) ? f.color : TF_COLORS[0], enabled: f.enabled !== false };
  },
  load() {
    try { const v = JSON.parse(localStorage.getItem(TF_KEY) || "{}"); this.list = (Array.isArray(v.list) ? v.list : []).map((f) => this.norm(f)); this.on = v.on !== false; this.open = !!v.open; }
    catch (e) { this.list = []; }
    this.compile();
  },
  save() { try { localStorage.setItem(TF_KEY, JSON.stringify({ on: this.on, open: !!this.open, list: this.list.map((f) => this.norm(f)) })); } catch (e) { /* private window */ } },
  compile() {
    this.db = DB;
    for (const f of this.list) {
      const cache = new Map();                                  // one answer per message (id + name)
      if (f.by === "id") { const q = idQuery(f.pattern); f.valid = q.valid; f.test = (r) => q.test(r[3]); }
      else {
        const q = nameQuery(f.pattern); f.valid = q.active;
        f.test = (r) => { const k = r[3] + "|" + r[5]; let v = cache.get(k);
          if (v === undefined) { v = q.matches(r[5], () => signalNamesOf(r[3])); cache.set(k, v); } return v; };
      }
    }
    this.active = this.on ? this.list.filter((f) => f.enabled && f.valid) : [];
    this.only = this.active.filter((f) => f.mode === "only");
    this.hide = this.active.filter((f) => f.mode === "hide");
    this.marks = this.active.filter((f) => f.mode !== "hide");
  },
  check() { if (this.db !== DB) this.compile(); },               // a database was loaded: names may match now
  pass(r) {
    if (!this.active.length) return true;
    if (this.hide.some((f) => f.test(r))) return false;
    return !this.only.length || this.only.some((f) => f.test(r));
  },
  color(r) { for (const f of this.marks) if (f.test(r)) return f.color; return null; },
  changed() { this.compile(); this.save(); this.render(); Trace.dirty = true; Trace.renderAll(true); },
  nextColor() { const used = new Set(this.list.map((f) => f.color)); return TF_COLORS.find((c) => !used.has(c)) || TF_COLORS[this.list.length % TF_COLORS.length]; },
  add(f) { this.list.push(this.norm({ color: this.nextColor(), ...f })); this.show(true); this.changed(); },
  show(on) { this.open = on; $("#tf-panel").classList.toggle("hidden", !on); $("#t-filters").classList.toggle("on", on); this.save(); },
  render() {
    const n = this.list.filter((f) => f.enabled).length;
    $("#t-filters").textContent = this.list.length ? `Filters (${n}/${this.list.length})` : "Filters";
    $("#tf-on").checked = this.on;
    $("#tf-stat").textContent = this.on ? "" : "off";
    const box = $("#tf-list");
    if (!this.list.length) {
      box.innerHTML = `<div class="tf-empty">No filter yet.<br>Press <b>Add</b>, or right-click a row of the trace to mark, show only or hide that message.</div>`;
      return;
    }
    box.innerHTML = this.list.map((f, i) => `<div class="tf-row${f.enabled ? "" : " off"}${f.valid ? "" : " bad"}" data-i="${i}" title="Double-click to edit">
      <input type="checkbox" data-en ${f.enabled ? "checked" : ""} title="On / off">
      <span class="sw ${f.mode}" style="--c:${f.color}"></span>
      <div class="tf-txt"><div class="tf-name">${esc(f.name || f.pattern || "(empty)")}</div>
        <div class="tf-sub"><span class="tf-mode ${f.mode}">${TF_MODES[f.mode]}</span> ${f.by === "id" ? "ID" : "Name"}: <span class="mono">${esc(f.pattern)}</span></div></div>
      <button class="btn sm icon" data-up title="Move up (the first matching filter gives the colour)">↑</button>
      <button class="btn sm icon" data-edit title="Edit">✎</button><button class="btn sm icon" data-del title="Remove">✕</button></div>`).join("");
  },
  edit(i = -1) {
    const f = i >= 0 ? this.list[i] : this.norm({ color: this.nextColor() });
    const o = modal(i >= 0 ? "Edit filter" : "Add filter", `<div class="tf-form">
      <label>Match</label><div class="seg" id="tf-by">${[["name", "Message / signal name"], ["id", "CAN ID"]].map(([v, l]) =>
        `<button type="button" class="btn sm${f.by === v ? " on" : ""}" data-v="${v}">${l}</button>`).join("")}</div>
      <label>Pattern</label><div><input id="tf-pat" style="width:100%" value="${esc(f.pattern)}" autocomplete="off"><div class="muted tf-hint" id="tf-hint"></div></div>
      <label>Action</label><div class="seg" id="tf-mode">${Object.entries(TF_MODES).map(([v, l]) =>
        `<button type="button" class="btn sm${f.mode === v ? " on" : ""}" data-v="${v}">${l}</button>`).join("")}</div>
      <label>Colour</label><div class="tf-colors" id="tf-col">${TF_COLORS.map((c) => `<button type="button" class="sw-btn${c === f.color ? " on" : ""}" data-v="${c}" style="--c:${c}"></button>`).join("")}</div>
      <label>Name</label><input id="tf-nm" value="${esc(f.name)}" placeholder="optional, e.g. Steering">
      </div>`, [
      { label: "Cancel" },
      { label: i >= 0 ? "Save" : "Add", kind: "primary", action: (o) => {
        const g = this.norm({ ...f, by: $("#tf-by .on", o).dataset.v, mode: $("#tf-mode .on", o).dataset.v, color: $("#tf-col .on", o).dataset.v,
          pattern: $("#tf-pat", o).value.trim(), name: $("#tf-nm", o).value.trim() });
        if (!(g.by === "id" ? idQuery(g.pattern).valid : nameQuery(g.pattern).active)) {
          toast(g.by === "id" ? "Pattern: hex CAN IDs or ranges, e.g. 118, 3E9, 100-1FF" : "Pattern: a message or signal name, e.g. Engine|Speed");
          return false;
        }
        if (i >= 0) this.list[i] = g; else this.list.push(g);
        this.show(true); this.changed();
      } }]);
    $(".modal", o).style.width = "min(560px, 94vw)";
    const hint = () => { $("#tf-hint", o).textContent = $("#tf-by .on", o).dataset.v === "id"
      ? "Hex IDs or ranges: 118, 3E9, 100-1FF"
      : "Names separated by |: Engine|Status, wildcards ADRV_*, !CHECKSUM to leave out. A message matches by its name or one of its signals."; };
    for (const id of ["#tf-by", "#tf-mode", "#tf-col"]) $(id, o).onclick = (e) => {
      const b = e.target.closest("[data-v]"); if (!b) return;
      $$("[data-v]", $(id, o)).forEach((x) => x.classList.toggle("on", x === b)); hint();
    };
    hint(); $("#tf-pat", o).focus();
    $("#tf-pat", o).onkeydown = (e) => { if (e.key === "Enter") $("footer .primary", o).click(); };
  },
  rowMenu(e) {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;
    e.preventDefault();
    const id = +tr.dataset.id, name = tr.dataset.name, hex = id.toString(16).toUpperCase();
    const items = Object.entries(TF_MODES).map(([m, l]) => ({ label: `${l}  ID 0x${hex}`, action: () => this.add({ by: "id", pattern: hex, mode: m, name: name || `0x${hex}` }) }));
    if (name) items.push("-", ...Object.entries(TF_MODES).map(([m, l]) => ({ label: `${l}  ${name}`, action: () => this.add({ by: "name", pattern: name, mode: m }) })));
    menu(e.clientX, e.clientY, items);
  },
};
$("#t-filters").onclick = () => TF.show($("#tf-panel").classList.contains("hidden"));
$("#tf-add").onclick = () => TF.edit();
$("#tf-on").onchange = (e) => { TF.on = e.target.checked; TF.changed(); };
$("#tf-list").onclick = (e) => {
  const row = e.target.closest(".tf-row"); if (!row) return;
  const i = +row.dataset.i;
  if (e.target.matches("[data-en]")) { TF.list[i].enabled = e.target.checked; TF.changed(); }
  else if (e.target.closest("[data-edit]")) TF.edit(i);
  else if (e.target.closest("[data-del]")) { TF.list.splice(i, 1); TF.changed(); }
  else if (e.target.closest("[data-up]") && i > 0) { [TF.list[i - 1], TF.list[i]] = [TF.list[i], TF.list[i - 1]]; TF.changed(); }
};
$("#tf-list").ondblclick = (e) => { const row = e.target.closest(".tf-row"); if (row && !e.target.closest("button,input")) TF.edit(+row.dataset.i); };
$("#tf-more").onclick = (e) => { e.stopPropagation(); const r = e.target.getBoundingClientRect(); menu(r.left - 150, r.bottom + 4, [
  { label: "Turn all on", action: () => { TF.list.forEach((f) => { f.enabled = true; }); TF.changed(); } },
  { label: "Turn all off", action: () => { TF.list.forEach((f) => { f.enabled = false; }); TF.changed(); } },
  "-",
  { label: "Export filters…", action: () => download(URL.createObjectURL(new Blob([JSON.stringify({ kind: "autosim-trace-filters", version: 1,
      filters: TF.list.map((f) => TF.norm(f)) }, null, 2)], { type: "application/json" })), "autosim_trace_filters.json") },
  { label: "Import filters…", action: () => pickFiles(".json", false, async ([file]) => {
      try { const d = JSON.parse(await file.text()); const l = Array.isArray(d) ? d : d.filters;
        if (!Array.isArray(l)) throw new Error(); TF.list.push(...l.map((f) => TF.norm(f))); TF.show(true); TF.changed(); toast(`${l.length} filters imported`, true); }
      catch (err) { toast("Not a trace filters file"); } }) },
  "-",
  { label: "Remove all filters", action: () => { TF.list = []; TF.changed(); } },
]); };
for (const id of ["#trace-vt", "#mini-vt"]) $(id).addEventListener("contextmenu", (e) => TF.rowMenu(e));

const MAX_ROWS = 100000;
const Trace = {
  rows: [], fixed: new Map(), paused: false, fixedMode: true, follow: true, flash: true, total: 0,
  expanded: new Set(), collapsed: new Set(), decoded: new Map(), dirty: true,
  filter: { ids: null, name: "", chan: "", dir: "", q: nameQuery("") },
  key(r) { return `${r[1]}|${r[2]}|${r[3]}|${r[4]}`; },
  reset(rows = []) { this.rows = []; this.fixed.clear(); this.total = 0; this.add(rows, true); },
  add(rows, initial = false) {
    const now = performance.now();
    for (const r of rows) {
      this.rows.push(r);
      const k = this.key(r), e = this.fixed.get(k);
      if (e) { e.count++; if (r[8] !== e.r[8]) e.flashAt = now; e.r = r; }
      else this.fixed.set(k, { r, count: 1, flashAt: initial ? 0 : now });
    }
    this.total += rows.length;
    if (this.rows.length > MAX_ROWS * 1.2) this.rows.splice(0, this.rows.length - MAX_ROWS);
    if (rows.length) this.dirty = true;
  },
  pass(r) {
    const f = this.filter;
    if (f.chan && r[1] !== f.chan) return false;
    if (f.dir !== "" && String(r[2]) !== f.dir) return false;
    if (f.ids && !f.ids.has(r[3])) return false;
    if (f.q.active && !f.q.matches(r[5], () => signalNamesOf(r[3]))) return false;
    return TF.pass(r);
  },
  channelsChanged() {
    const sel = $("#t-chan"), cur = sel.value;
    sel.innerHTML = `<option value="">All channels</option>` + S.channels.map((c) => `<option>${esc(c.channel)}</option>`).join("");
    sel.value = cur;
  },
  head(fixed) {
    return `<colgroup><col style="width:${fixed ? 120 : 110}px"><col style="width:70px"><col style="width:46px"><col style="width:90px">
      <col style="width:24%"><col style="width:58px"><col><col style="width:84px">${fixed ? '<col style="width:70px">' : ""}</colgroup>
      <thead><tr><th class="r">Time [s]</th><th>Channel</th><th>Dir</th><th>ID</th><th>Name / Signal</th><th class="r">DLC</th>
      <th>Data / Value</th><th class="r">Cycle [ms]</th>${fixed ? '<th class="r">Count</th>' : ""}</tr></thead>`;
  },
  rowHtml(r, fixedEntry, now, isOpen = false) {
    const len = r[8].length / 2;
    const mark = TF.color(r);
    let style = "", cls = mark ? ` class="tf-mark"` : "";
    if (fixedEntry && this.flash && r[10] !== 0) {
      const age = now - fixedEntry.flashAt;
      if (age < 600) style = mark                  // a marked row flashes in its own colour, a bit stronger
        ? `background:color-mix(in srgb, ${mark} ${Math.round(42 - age / 33)}%, transparent);`
        : `background:color-mix(in srgb, var(--flash) ${Math.round(100 - age / 6)}%, transparent);`;
    }
    if (mark) style = `--mark:${mark};` + style;
    style = style ? ` style="${style}"` : "";
    const k = fixedEntry ? this.key(r) : "";
    const tw = fixedEntry ? `<span class="tw-t" style="float:left" data-exp="${esc(k)}">${r[5] ? (isOpen ? "▾" : "▸") : ""}</span>` : "";
    return `<tr${style}${cls} data-id="${r[3]}" data-name="${esc(r[5])}"><td class="r mono">${tw}${r[0].toFixed(6)}</td><td>${esc(r[1])}</td><td class="${r[2] ? "dir-rx" : "dir-tx"}">${r[2] ? "Rx" : "Tx"}</td>
      <td class="mono">${hexId(r[3], r[4])}</td><td>${esc(r[5])}</td><td class="r mono">${len}${r[6] ? " FD" : ""}</td>
      <td class="mono col-data">${r[8].toUpperCase().replace(/(..)(?=.)/g, "$1 ")}</td><td class="r mono">${r[9] === null ? "" : r[9].toFixed(1)}</td>
      ${fixedEntry ? `<td class="r mono">${fixedEntry.count}</td>` : ""}</tr>`;
  },
  sigRows(k, r) {
    const d = this.decoded.get(k);
    if (!d || d.data !== r[8]) this.decode(k, r);
    if (!d) return [`<tr class="sig"><td colspan="9" class="muted">decoding…</td></tr>`];
    const q = this.filter.q;                                       // one <tr> per signal (rows of the virtual list)
    return d.signals.filter((s) => !q.active || q.sigShown(r[5], s.name)).map((s) => `<tr class="sig"><td></td><td></td><td></td><td></td><td>${esc(s.name)}</td><td></td>
      <td class="mono">${fmt(+s.value.toPrecision(10))}${s.unit ? " " + esc(s.unit) : ""}${s.text ? ` (${esc(s.text)})` : ""}</td><td></td><td></td></tr>`);
  },
  decode(k, r) {
    const d = this.decoded.get(k);
    if (d?.pending || (d && performance.now() - d.at < 250)) return;
    this.decoded.set(k, { ...(d || { signals: [] }), pending: true, at: performance.now(), data: d?.data });
    post("/api/decode", { channel: r[1], id: r[3], data: r[8] }).then((res) => {
      this.decoded.set(k, { signals: res.signals, data: r[8], at: performance.now(), pending: false }); this.dirty = true;
    }).catch(() => this.decoded.delete(k));
  },
  renderView(box, fixed, filtered) {
    if (!box.offsetParent) return;                                         // hidden desktop
    if (!box._init || box._fixed !== fixed) {
      box._init = true; box._fixed = fixed;
      box.innerHTML = `<table class="grid">${this.head(fixed)}<tbody></tbody></table>`;
      box.onscroll = () => {
        if (box._programmatic) { box._programmatic = false; return; }
        box._userScroll = box.scrollTop + box.clientHeight < box.scrollHeight - ROW_H * 2;
        this.renderAll(true);
      };
      box.onclick = (e) => { const t = e.target.closest("[data-exp]"); if (!t) return;
        const k = t.dataset.exp;
        if (this.filter.q.include) this.collapsed.has(k) ? this.collapsed.delete(k) : this.collapsed.add(k);   // opened by the filter
        else this.expanded.has(k) ? this.expanded.delete(k) : this.expanded.add(k);
        this.dirty = true; this.renderAll(true); };
    }
    const now = performance.now();
    let html;
    if (fixed) {
      const entries = [...this.fixed.values()].filter((e) => this.pass(e.r)).sort((a, b) =>
        a.r[3] - b.r[3] || a.r[1].localeCompare(b.r[1]) || b.r[2] - a.r[2]);
      const open = (k) => this.expanded.has(k) !== (this.filter.q.include && !this.collapsed.has(k));   // Speed|Gear: opened
      // virtual list too: a message row and its open signal rows are items; only the visible ones are built
      const items = [];
      for (const e of entries) {
        const k = this.key(e.r), o = open(k);
        items.push({ e, o });
        if (o) for (const h of this.sigRows(k, e.r)) items.push({ h });
      }
      const n = Math.ceil(box.clientHeight / ROW_H) + 10;
      const first = Math.max(0, Math.min(items.length - n, Math.floor(box.scrollTop / ROW_H) - 5));
      const last = Math.min(items.length, first + n);
      html = `<tr style="height:${first * ROW_H}px"></tr>` + items.slice(first, last).map((it) => it.h ?? this.rowHtml(it.e.r, it.e, now, it.o)).join("") +
        `<tr style="height:${(items.length - last) * ROW_H}px"></tr>`;
      $("tbody", box).innerHTML = html;
      return entries.length;
    }
    // chronological: only the visible rows exist; padding rows stand for the others (the header stays sticky)
    const rows = filtered, n = Math.ceil(box.clientHeight / ROW_H) + 10;
    let first;
    if (this.follow && !box._userScroll) first = Math.max(0, rows.length - n + 5);
    else first = Math.max(0, Math.min(rows.length - 1, Math.floor(box.scrollTop / ROW_H) - 5));
    const last = Math.min(rows.length, first + n);
    html = `<tr style="height:${first * ROW_H}px"></tr>` + rows.slice(first, last).map((r) => this.rowHtml(r, null, now)).join("") +
      `<tr style="height:${(rows.length - last) * ROW_H}px"></tr>`;
    $("tbody", box).innerHTML = html;
    if (this.follow && !box._userScroll && box.scrollTop + box.clientHeight < box.scrollHeight - 1) {
      box._programmatic = true; box.scrollTop = box.scrollHeight;
    }
    return rows.length;
  },
  renderAll(force = false) {
    if (TF.db !== DB) { TF.compile(); force = true; }               // a database was loaded: names may match now
    if (!force && !this.dirty && !(this.flash && this.fixedMode)) return;
    this.dirty = false;
    const filtered = this.fixedMode ? null : this.rows.filter((r) => this.pass(r));
    const n = this.renderView($("#trace-vt"), this.fixedMode, filtered);
    if (n !== undefined) $("#trace-count").textContent = this.fixedMode ? `${n} messages` : `${n} rows`;
    const keep = this.filter; this.filter = { ids: null, name: "", chan: "", dir: "", q: nameQuery("") };
    const m = this.renderView($("#mini-vt"), true, null);
    this.filter = keep;
    if (m !== undefined) $("#mini-count").textContent = `${m} messages`;
  },
  setPaused(on) {
    this.paused = on;
    $("#t-pause").classList.toggle("on", on); $("#t-pause").textContent = on ? "Resume" : "Pause";
    post("/api/trace/pause", { paused: on }).catch(() => {});
  },
  clear() { post("/api/trace/clear").catch(() => {}); this.reset(); this.decoded.clear(); this.renderAll(true); },
};
$("#t-fixed").onclick = (e) => { Trace.fixedMode = !Trace.fixedMode; e.target.classList.toggle("on", Trace.fixedMode); Trace.renderAll(true); };
$("#t-pause").onclick = () => Trace.setPaused(!Trace.paused);
$("#t-clear").onclick = () => Trace.clear();
$("#mini-clear").onclick = () => Trace.clear();
$("#full-view").onclick = () => showDesk("analysis");
$("#t-follow").onclick = (e) => { Trace.follow = !Trace.follow; e.target.classList.toggle("on", Trace.follow); $("#trace-vt")._userScroll = false; Trace.renderAll(true); };
$("#t-flash").onclick = (e) => { Trace.flash = !Trace.flash; e.target.classList.toggle("on", Trace.flash); Trace.renderAll(true); };
$("#t-export").onclick = (e) => menu(e.clientX, e.clientY, ["csv", "asc", "blf"].map((f) => ({
  label: { csv: "CSV (.csv)", asc: "Vector ASC (.asc)", blf: "Vector BLF (.blf)" }[f],
  action: () => download(`/api/trace/export?format=${f}`) })));
$("#t-id").oninput = (e) => {
  const ids = e.target.value.split(/[,\s]+/).filter(Boolean).map((x) => parseInt(x.replace(/^0x/i, ""), 16)).filter((x) => !isNaN(x));
  Trace.filter.ids = ids.length ? new Set(ids) : null; Trace.renderAll(true);
};
$("#t-name").oninput = (e) => {
  Trace.filter.name = e.target.value.trim(); Trace.filter.q = nameQuery(Trace.filter.name); Trace.collapsed.clear(); Trace.renderAll(true);
};
for (const id of ["#t-id", "#t-name"]) $(id).onchange = () => Recent.remember();          // Enter / leaving the field
$("#t-recent").onclick = (e) => {
  e.stopPropagation();
  const r = $("#t-recent").getBoundingClientRect(), list = Recent.load();
  menu(r.left, r.bottom + 4, list.length ? [...list.map((x) => ({ label: Recent.label(x), action: () => {
    $("#t-id").value = x.id; $("#t-name").value = x.name;
    $("#t-id").dispatchEvent(new Event("input")); $("#t-name").dispatchEvent(new Event("input")); Recent.remember();
  } })), "-", { label: "Clear recent filters", action: () => Recent.save([]) }]
    : [{ label: "No recent filters - press Enter in ID or Name to keep one", action: () => {} }]);
};
$("#t-chan").onchange = (e) => { Trace.filter.chan = e.target.value; Trace.renderAll(true); };
$("#t-dir").onchange = (e) => { Trace.filter.dir = e.target.value; Trace.renderAll(true); };
setInterval(() => Trace.renderAll(), 100);

// ---------- Test API: signals published for test systems ----------
const TestApi = {
  apis: [], live: new Map(), auth: false, expanded: new Set(), search: "", logRows: [],
  base() { return location.origin + "/api/v1/signals/"; },
  async load() {
    const r = await api("GET", "/api/testapi");
    this.apis = r.apis; this.auth = r.auth; this.token = r.token || ""; this.live = new Map(r.apis.map((a) => [a.name, a]));
    const tk = $("#ta-token");
    tk.classList.toggle("hidden", !this.token);
    $("code", tk).textContent = this.token;
    this.logRows = r.log; this.render(); this.renderLog(true);
  },
  messages() {                                   // every message of the databases in use
    const out = new Map();
    for (const d of DB) if (d.active !== false) for (const m of d.messages) if (!out.has(m.name)) out.set(m.name, m);
    return [...out.values()].sort((a, b) => a.name.localeCompare(b.name));
  },
  sigOf(message, signal) { return this.messages().find((m) => m.name === message)?.signals.find((s) => s.name === signal); },
  renderTree() {
    const tree = $("#ta-tree"); if (!tree) return;
    const q = this.search, msgs = this.messages();
    if (!msgs.length) { tree.innerHTML = `<div class="muted" style="padding:10px">No database in use - add one in Setup &gt; Database.</div>`; return; }
    const used = new Set(this.apis.map((a) => a.message + "." + a.signal));
    const out = [];
    for (const m of msgs) {
      const hitMsg = !q || m.name.toLowerCase().includes(q);
      const sigs = m.signals.filter((s) => hitMsg || s.name.toLowerCase().includes(q));
      if (!sigs.length) continue;
      const open = q || this.expanded.has(m.name);
      out.push(`<div class="node" data-tamsg="${esc(m.name)}" style="padding-left:4px"><span class="tw" data-tw>${open ? "▾" : "▸"}</span>
        <span class="nm">${esc(m.name)}</span><span class="aux">${idText(m.id, m.extended)}</span></div>`);
      if (open) for (const s of sigs) out.push(`<div class="node" data-tam="${esc(m.name)}" data-tas="${esc(s.name)}" style="padding-left:24px"
        title="Double-click: publish ${esc(s.name)} as an API"><span class="tw"></span><span class="nm">${used.has(m.name + "." + s.name) ? "● " : ""}${esc(s.name)}</span>
        <span class="aux">${esc(s.unit)}</span></div>`);
    }
    tree.innerHTML = out.join("") || `<div class="muted" style="padding:8px">No match</div>`;
    $$("[data-tamsg]", tree).forEach((n) => {
      const toggle = () => { const k = n.dataset.tamsg; this.expanded.has(k) ? this.expanded.delete(k) : this.expanded.add(k); this.renderTree(); };
      $("[data-tw]", n).onclick = toggle; n.ondblclick = toggle;
    });
    $$("[data-tas]", tree).forEach((n) => {
      n.ondblclick = () => this.quickAdd(n.dataset.tam, n.dataset.tas);
      n.oncontextmenu = (e) => { e.preventDefault(); menu(e.clientX, e.clientY, [
        { label: "Add API with defaults", action: () => this.quickAdd(n.dataset.tam, n.dataset.tas) },
        { label: "Add API…", action: () => this.edit(null, { message: n.dataset.tam, signal: n.dataset.tas }) }]); };
    });
  },
  async quickAdd(message, signal) {
    const r = await post("/api/testapi/add", { message, signal });
    toast(`API ${r.name}: ${this.base()}${r.name}`, true);
    await this.load(); this.flash(r.name);
  },
  flash(name) {
    const tr = $(`#ta-table tr[data-name="${CSS.escape(name)}"]`);
    if (tr) { tr.classList.add("sel"); tr.scrollIntoView({ block: "nearest" }); setTimeout(() => tr.classList.remove("sel"), 1500); }
  },
  valueText(a) {
    if (a.value === null || a.value === undefined) return "";
    return fmt(a.value) + (a.text ? ` (${a.text})` : "") + (a.unit ? " " + a.unit : "");
  },
  statusText(a) {
    return { ok: "ok", stale: "stale", no_data: "no data", inactive: "inactive (mux)", undecodable: "undecodable", error: a.error || "error" }[a.status] || a.status;
  },
  render() {
    $("#ta-base").textContent = this.base() + "<name>";
    const body = $("#ta-body"); if (!body) return;
    this.renderTree();
    if (!this.apis.length) {
      body.innerHTML = `<div class="empty"><div class="ico">⇄</div><h3>No API yet</h3>
        <p>Publish CAN signals for test systems: they read the value on the bus with GET and make AutoSim send a frame with PUT.
        Double-click a signal on the left, or add one here.</p><button class="btn primary" data-add>Add API</button></div>`;
      $("[data-add]", body).onclick = () => this.edit(null);
      return;
    }
    body.innerHTML = `<table class="grid" id="ta-table"><colgroup><col style="width:15%"><col><col style="width:70px">
      <col style="width:64px"><col style="width:120px"><col style="width:60px"><col style="width:76px"><col style="width:86px"><col style="width:60px"><col style="width:162px"></colgroup>
      <thead><tr><th>API name / URL</th><th>Signal</th><th>Channel</th><th>Access</th><th class="r">Value on the bus</th>
      <th class="r">Age</th><th>Status</th><th class="r" title="Value a PUT sends (starts at the DBC initial value)">Set value</th>
      <th class="r" title="GET / PUT calls">Calls</th><th></th></tr></thead><tbody>${this.apis.map((a) => `<tr data-name="${esc(a.name)}">
      <td><a class="url mono" href="${esc(this.base() + a.name)}" target="_blank" title="${esc(this.base() + a.name)}">${esc(a.name)}</a></td>
      <td title="${esc(a.message)}.${esc(a.signal)} ${esc(a.id || "")}"><b>${esc(a.signal)}</b> <span class="muted">${esc(a.message)} ${esc(a.id || "")}</span></td>
      <td>${esc(a.channel || "any")}</td>
      <td><span class="acc${a.access === "getset" ? " set" : ""}">${a.access === "getset" ? "GET + SET" : "GET"}</span></td>
      <td class="r mono val" data-f="value"></td><td class="r mono" data-f="age"></td><td data-f="status"></td>
      <td class="r mono" data-f="set"></td><td class="r mono" data-f="calls"></td>
      <td class="act"><button class="btn sm" data-copy title="Copy the URL">URL</button><button class="btn sm" data-curl title="Copy a curl command">curl</button>
        <button class="btn sm" data-edit title="Edit">✎</button><button class="btn sm" data-del title="Remove">🗑</button></td></tr>`).join("")}</tbody></table>`;
    $$("#ta-table tbody tr").forEach((tr) => {
      const a = () => this.apis.find((x) => x.name === tr.dataset.name);
      $("[data-copy]", tr).onclick = () => this.copy(this.base() + tr.dataset.name, "URL copied");
      $("[data-curl]", tr).onclick = () => this.copy(this.curl(a()), "curl command copied");
      $("[data-edit]", tr).onclick = () => this.edit(a());
      $("[data-del]", tr).onclick = async () => { if (confirm(`Remove API ${tr.dataset.name}?`)) { await post("/api/testapi/remove", { name: tr.dataset.name }); this.load(); } };
      tr.ondblclick = (e) => { if (!e.target.closest("button,a")) this.edit(a()); };
    });
    this.updateLive();
  },
  updateLive() {
    $$("#ta-table tbody tr").forEach((tr) => {
      const a = this.live.get(tr.dataset.name); if (!a) return;
      const f = (k) => $(`[data-f="${k}"]`, tr);
      f("value").textContent = this.valueText(a);
      f("age").textContent = a.age_ms === null || a.age_ms === undefined ? "" : a.age_ms < 10000 ? `${Math.round(a.age_ms)} ms` : `${(a.age_ms / 1000).toFixed(0)} s`;
      f("status").innerHTML = `<span class="st ${esc(a.status)}" title="${a.direction ? (a.direction === "rx" ? "received" : "sent") + " on " + esc(a.channel) : ""}">${esc(this.statusText(a))}</span>`;
      const sig = this.sigOf(a.message, a.signal), name = sig?.choices?.[String(a.set_value)];
      f("set").textContent = a.access === "getset" ? fmt(a.set_value) + (name ? ` (${name})` : "") : "—";
      f("calls").textContent = `${a.gets ?? 0} / ${a.sets ?? 0}`;
    });
  },
  onPush(m) {
    for (const a of m.live) this.live.set(a.name, a);
    if (m.live.length !== this.apis.length || m.live.some((a, i) => a.name !== this.apis[i]?.name)) { this.apis = m.live; this.render(); }
    else this.updateLive();
    if (m.log.length) { this.logRows.push(...m.log); if (this.logRows.length > 500) this.logRows.splice(0, this.logRows.length - 500); this.renderLog(); }
  },
  // Call log: one row per call; a click opens the full request / response / frame below it
  openLog: new Set(), lastLogId: 0,
  logRow(e) {
    const open = this.openLog.has(e.id);
    return `<tr class="lr${open ? " open" : ""}" data-id="${e.id}" title="Click to show the whole request and response">
      <td class="mono"><span class="tw-l">${open ? "▾" : "▸"}</span>${new Date(e.t * 1000).toLocaleTimeString()}</td><td>${esc(e.client)}</td>
      <td class="mono"><span class="m-${e.method === "GET" ? "get" : "put"}">${esc(e.method)}</span> ${esc(e.path)}${e.request ? ` <span class="muted">${esc(e.request.replace(/\s+/g, " "))}</span>` : ""}</td>
      <td class="r mono ${e.status < 300 ? "code-ok" : "code-err"}">${e.status}</td><td>${esc(e.answer)}</td>
      <td class="mono">${e.sent ? `${esc(e.sent.channel)} ${esc(e.sent.id)} [${esc(e.sent.data)}]${e.sent.fd ? " FD" : ""}` : ""}</td></tr>`
      + (open ? this.logDetail(e) : "");
  },
  pretty(text) {
    if (!text) return "";
    try { return JSON.stringify(JSON.parse(text), null, 2); } catch (er) { return text; }
  },
  logDetail(e) {
    const url = location.origin + e.path;
    const heads = Object.entries(e.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
    const d = new Date(e.t * 1000), p2 = (n, w = 2) => String(n).padStart(w, "0");
    const when = `${d.toLocaleDateString()} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}.${p2(d.getMilliseconds(), 3)}`;
    const req = `${e.method} ${url}\nClient: ${e.client}\nTime: ${when}`
      + (heads ? "\n" + heads : "") + (e.request ? "\n\n" + this.pretty(e.request) : "");
    const resp = this.pretty(e.response) || e.answer || "";
    const frame = e.sent ? `Channel: ${e.sent.channel}\nID:      ${e.sent.id}${e.sent.fd ? "  (CAN FD)" : ""}\nDLC:     ${e.sent.data.split(" ").length} bytes\nData:    ${e.sent.data}` : "";
    return `<tr class="ld" data-for="${e.id}"><td colspan="6"><div class="ld-grid${frame ? " three" : ""}">
      <section><h4>Request</h4><pre>${esc(req)}</pre></section>
      <section><h4>Response · <span class="${e.status < 300 ? "code-ok" : "code-err"}">${e.status}</span>${e.ms !== null && e.ms !== undefined ? ` · ${e.ms} ms` : ""}</h4><pre>${esc(resp)}</pre></section>
      ${frame ? `<section><h4>Frame sent by AutoSim</h4><pre>${esc(frame)}</pre></section>` : ""}</div>
      <div class="row ld-act"><button class="btn sm" data-lcurl="${e.id}">Copy as curl</button><button class="btn sm" data-lresp="${e.id}">Copy response</button>
        <button class="btn sm" data-lclose="${e.id}">Close</button></div></td></tr>`;
  },
  authHeader() {                                 // the stored token (the same at every start) in curl commands
    return this.auth ? ` -H "X-AutoSim-Token: ${this.token || "<token printed by autosim-web>"}"` : "";
  },
  curlOf(e) {
    const auth = this.authHeader();
    const url = location.origin + e.path;
    if (e.method === "GET") return `curl${auth} "${url}"`;
    return `curl${auth} -X ${e.method} -H "Content-Type: application/json" -d '${(e.request || "").replace(/'/g, "'\\''")}' "${url}"`;
  },
  toggleLog(id) {
    const e = this.logRows.find((x) => x.id === id), tr = $(`#ta-log tr.lr[data-id="${id}"]`);
    if (!e || !tr) return;
    if (this.openLog.has(id)) { this.openLog.delete(id); $(`#ta-log tr.ld[data-for="${id}"]`)?.remove(); }
    else { this.openLog.add(id); tr.insertAdjacentHTML("afterend", this.logDetail(e)); $("#ta-follow").checked = false; }   // reading: stop following
    tr.classList.toggle("open", this.openLog.has(id));
    $(".tw-l", tr).textContent = this.openLog.has(id) ? "▾" : "▸";
  },
  initLog() {
    const tb = $("#ta-log tbody");
    tb.addEventListener("click", (ev) => {
      const t = ev.target;
      const act = t.closest("[data-lcurl],[data-lresp],[data-lclose]");
      if (act) {
        const id = +(act.dataset.lcurl || act.dataset.lresp || act.dataset.lclose), e = this.logRows.find((x) => x.id === id);
        if (act.dataset.lclose) this.toggleLog(id);
        else if (e && act.dataset.lcurl) this.copy(this.curlOf(e), "curl command copied");
        else if (e) this.copy(this.pretty(e.response) || e.answer, "Response copied");
        return;
      }
      const tr = t.closest("tr.lr");
      if (tr && !String(getSelection())) this.toggleLog(+tr.dataset.id);   // selecting text does not toggle
    });
  },
  renderLog(all = false) {
    const tb = $("#ta-log tbody"); if (!tb) return;
    const first = this.logRows[0]?.id ?? Infinity;
    for (const id of [...this.openLog]) if (id < first) this.openLog.delete(id);
    if (all || !tb.childElementCount) tb.innerHTML = this.logRows.map((e) => this.logRow(e)).join("");
    else {
      $$("tr[data-id]", tb).forEach((tr) => { if (+tr.dataset.id < first) { tr.nextElementSibling?.classList.contains("ld") && tr.nextElementSibling.remove(); tr.remove(); } });
      tb.insertAdjacentHTML("beforeend", this.logRows.filter((e) => e.id > this.lastLogId).map((e) => this.logRow(e)).join(""));
    }
    this.lastLogId = this.logRows.length ? this.logRows[this.logRows.length - 1].id : 0;
    $("#ta-log-count").textContent = `${this.logRows.length} calls`;
    const box = $("#ta-log").parentElement;
    if ($("#ta-follow").checked) box.scrollTop = box.scrollHeight;
  },
  copy(text, ok) {
    (navigator.clipboard?.writeText(text) || Promise.reject()).then(() => toast(ok, true), () => prompt("Copy:", text));
  },
  curl(a) {
    const auth = this.authHeader();
    const url = this.base() + a.name;
    if (a.access !== "getset") return `curl${auth} ${url}`;
    return `curl${auth} ${url}\ncurl${auth} -X PUT -H "Content-Type: application/json" -d '{"value": ${a.set_value ?? 0}}' ${url}`;
  },
  edit(a, preset = {}) {
    const msgs = this.messages();
    if (!msgs.length) { toast("Add a database first (Setup > Database)"); return; }
    const cur = a || { name: "", message: preset.message || msgs[0].name, signal: preset.signal || "", channel: "", access: "get", stale_ms: 1000, set_value: null };
    const chans = [...new Set([...S.channels.map((c) => c.channel), ...(cur.channel ? [cur.channel] : [])])];
    const o = modal(a ? `Edit API ${a.name}` : "Add API", `<div class="fg">
      <label>Message</label><select data-f="message"${a ? " disabled" : ""}>${msgs.map((m) => `<option${m.name === cur.message ? " selected" : ""}>${esc(m.name)}</option>`).join("")}</select>
      <label>Signal</label><select data-f="signal"${a ? " disabled" : ""}></select>
      <label>API name</label><input data-f="name" value="${esc(cur.name)}" placeholder="the signal name" title="Letters, digits, _ . - (part of the URL)">
      <label>Channel</label><select data-f="channel" title="Where the value is read and the frame is sent"><option value="">Any (send on the database's channel)</option>
        ${chans.map((c) => `<option${c === cur.channel ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <label>Access</label><div class="row" style="grid-column:2/5;gap:16px">
        <label class="chk"><input type="radio" name="ta-acc" value="get"${cur.access !== "getset" ? " checked" : ""}>GET (read the bus)</label>
        <label class="chk"><input type="radio" name="ta-acc" value="getset"${cur.access === "getset" ? " checked" : ""}>GET + SET (PUT sends a frame)</label></div>
      <label>Value to send</label><div class="row" style="grid-column:2/5" title="What a PUT sends until a client sets another value (DBC initial value by default)">
        <input data-f="value" style="width:110px"><select data-f="pick" class="hidden" style="min-width:140px"></select><span class="muted" data-f="info"></span></div>
      <label>Stale after</label><div class="row" style="grid-column:2/5"><input data-f="stale" type="number" min="0" step="100" value="${cur.stale_ms ?? 1000}" style="width:110px">
        <span class="muted">ms without a frame: status "stale" (0 = never)</span></div>
      <label>URL</label><div class="ta-url mono muted" data-f="url"></div></div>`, [
      { label: "Cancel" },
      { label: a ? "Save" : "Add", kind: "primary", action: async (ov) => {
        const g = (k) => $(`[data-f="${k}"]`, ov);
        const access = $('input[name="ta-acc"]:checked', ov).value;
        const b = { channel: g("channel").value, access, stale_ms: +g("stale").value || 0, value: g("value").value.trim() };
        try {
          if (a) { b.name = a.name; b.new_name = g("name").value.trim(); await post("/api/testapi/update", b); }
          else { await post("/api/testapi/add", { ...b, message: g("message").value, signal: g("signal").value, name: g("name").value.trim() }); }
        } catch (e) { return false; }
        this.load();
      } }]);
    const g = (k) => $(`[data-f="${k}"]`, o);
    const fillSignals = () => {
      const m = msgs.find((x) => x.name === g("message").value);
      g("signal").innerHTML = (m?.signals || []).map((s) => `<option${s.name === cur.signal ? " selected" : ""}>${esc(s.name)}</option>`).join("");
      fillValue(true);
    };
    const fillValue = (reset) => {
      const s = this.sigOf(g("message").value, g("signal").value); if (!s) return;
      if (reset) g("value").value = a && a.set_value !== null && a.set_value !== undefined && s.name === a.signal ? fmt(a.set_value) : fmt(s.initial ?? 0);
      const ch = Object.entries(s.choices).sort((x, y) => +x[0] - +y[0]);
      g("pick").classList.toggle("hidden", !ch.length);
      g("pick").innerHTML = `<option value="">Pick…</option>` + ch.map(([k, v]) => `<option value="${k}">${k}: ${esc(v)}</option>`).join("");
      g("pick").value = ch.some(([k]) => k === g("value").value.trim()) ? g("value").value.trim() : "";
      const rng = s.min === null && s.max === null ? "" : `[${fmt(s.min) || "…"} … ${fmt(s.max) || "…"}]`;
      g("info").textContent = [s.unit, rng, `DBC initial ${fmt(s.initial ?? 0)}`].filter(Boolean).join("  ");
      updUrl();
    };
    const updUrl = () => { g("url").textContent = this.base() + (g("name").value.trim() || g("signal").value); };
    g("message").onchange = fillSignals;
    g("signal").onchange = () => fillValue(true);
    g("pick").onchange = () => { if (g("pick").value !== "") g("value").value = g("pick").value; };
    g("value").oninput = () => fillValue(false);
    g("name").oninput = updUrl;
    fillSignals();
    (a ? g("name") : g("signal")).focus();
  },
  more(e) {
    menu(e.clientX, e.clientY, [
      { label: "Export APIs (JSON)…", action: () => download("/api/testapi/export") },
      { label: "Import APIs (JSON)…", action: () => pickFiles(".json,application/json", false, async ([f]) => {
        let data; try { data = JSON.parse(await f.text()); } catch (er) { toast("Not a JSON file"); return; }
        const r = await post("/api/testapi/import", { data, replace: true });
        r.problems.forEach((p) => toast(p)); toast(`Imported ${f.name}`, true); this.load();
      }) }, "-",
      { label: "Copy base URL", action: () => this.copy(this.base(), "Base URL copied") },
      { label: "Copy GET all (every API in one call)", action: () => this.copy(`curl${this.authHeader()} ${this.base().replace(/\/$/, "")}`, "curl command copied") }]);
  },
};
$("#ta-add").onclick = () => TestApi.edit(null);
$("#ta-more").onclick = (e) => { e.stopPropagation(); TestApi.more(e); };
$("#ta-search").oninput = (e) => { TestApi.search = e.target.value.trim().toLowerCase(); TestApi.renderTree(); };
$("#ta-log-clear").onclick = async () => { await post("/api/testapi/log/clear"); TestApi.logRows = []; TestApi.renderLog(true); };
TestApi.initLog();
$("#ta-token-copy").onclick = () => TestApi.copy(TestApi.token, "Token copied");

// ---------- status bar ----------
function status(st) {
  $("#st-chans").innerHTML = st.channels.length ? st.channels.map((c) =>
    `<span title="${esc(c.state)}"><span class="dot ${c.state === "error" ? "err" : c.state === "running" ? "" : "warn"}"></span>${esc(c.channel)} ${c.load.toFixed(1)}% est. · ${c.fps} fr/s</span>`).join(" &nbsp; ") : "No channel open";
  $("#st-err").textContent = `Err ${st.errors}`;
  $("#st-trace").textContent = `${Trace.total} fr`;
  $("#st-rec").textContent = st.paused ? "❚❚ Paused" : "● Recording";
  $("#st-rec").style.color = st.paused ? "var(--warning)" : "";
  if (st.paused !== Trace.paused) { Trace.paused = st.paused; $("#t-pause").classList.toggle("on", st.paused); $("#t-pause").textContent = st.paused ? "Resume" : "Pause"; }
  Generator.live(st.cards);
  if (S && st.hits) $$("#ar-table tr[data-i]").forEach((tr) => {
    const h = st.hits[+tr.dataset.i]; if (h !== undefined) tr.lastElementChild.textContent = h;
  });
}

// ---------- WebSocket ----------
let ws = null, wsTry = 0;
function connectWs() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => { wsTry = 0; $("#conn").textContent = ""; $("#conn-banner")?.remove(); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "hello") {
      Trace.reset(m.rows); Trace.renderAll(true);
      $("#log").innerHTML = ""; m.logs.forEach(([t, x]) => addLog(t, x)); unseen.log = 0; $("#log-label").textContent = "Log";
      status(m.status); refreshState();
    } else if (m.type === "update") {
      if (m.rows.length) Trace.add(m.rows);
      m.logs.forEach(([t, x]) => addLog(t, x));
      if (m.nodes.length) Channels.updateNodes(m.nodes);
      if (m.version !== stateVersion) refreshState();
    } else if (m.type === "status") status(m);
    else if (m.type === "values") Generator.live(m.cards);
    else if (m.type === "api") TestApi.onPush(m);
    else if (m.type === "clear") { Trace.reset(); Trace.renderAll(true); }
  };
  ws.onclose = () => {
    if (!$("#conn-banner")) document.body.appendChild(el(`<div id="conn-banner" class="pill warn">Connection to the AutoSim server lost - reconnecting…</div>`));
    setTimeout(connectWs, Math.min(5000, 500 * ++wsTry));
  };
}

// ---------- keyboard ----------
addEventListener("keydown", (e) => {
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "") && document.activeElement.type !== "checkbox";
  if (e.ctrlKey && ["1", "2", "3", "4"].includes(e.key)) { e.preventDefault(); showDesk(["setup", "simulation", "analysis", "testapi"][+e.key - 1]); }
  else if (e.ctrlKey && e.key.toLowerCase() === "j") { e.preventDefault(); toggleLog(); }
  else if (e.ctrlKey && e.key.toLowerCase() === "l") { e.preventDefault(); Trace.clear(); }
  else if (e.key === " " && !typing && !$(".overlay")) { e.preventDefault(); Trace.setPaused(!Trace.paused); }
});

// ---------- start ----------
TF.load(); TF.render(); TF.show(TF.open);
Channels.init();
try { const d = localStorage.getItem("autosim.desk"); if (d) showDesk(d); } catch (e) {}
refreshState().then(connectWs, (e) => { toast("Cannot load the state: " + e.message); connectWs(); });
