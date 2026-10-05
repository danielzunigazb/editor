"use strict";
// Everything the model or the user wrote is put on the page with textContent, never innerHTML.
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v); }
  for (const kid of kids) if (kid) e.append(kid);
  return e;
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: the page works without it */ } },
};
const state = { pid: null, project: null, streaming: false, abort: null, limits: {}, refreshTimer: null, exportPoll: null };

class ApiError extends Error { constructor(status, code, message) { super(message); this.status = status; this.code = code; } }

async function api(path, opts = {}) {
  const headers = { ...(opts.body && typeof opts.body === "string" ? { "content-type": "application/json" } : {}), ...(opts.headers || {}) };
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers });
  let data = null;
  try { data = await r.json(); } catch { /* not json */ }
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new ApiError(401, "UNAUTHORIZED", "Sign in to continue"); }
  if (!r.ok) throw new ApiError(r.status, data?.error?.code, data?.error?.message || r.statusText);
  return data;
}

// ---------------------------------------------------------------------------------------------------------------- theme, login, projects
function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; }
applyTheme(store.get("theme"));
$("#theme").onclick = () => {
  const dark = (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const next = dark ? "light" : "dark"; applyTheme(next); store.set("theme", next);
};

function showLogin() { const d = $("#login"); if (!d.open) d.showModal(); $("#token").focus(); }
$("#login-form").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ token: $("#token").value }) });
    $("#token").value = ""; $("#login-error").textContent = ""; $("#login").close(); init();
  } catch (e) { $("#login-error").textContent = e.message; }
};

async function init() {
  let data;
  try { data = await api("/api/projects"); } catch { return; }
  state.limits = data.limits;
  const sel = $("#project-select"); sel.replaceChildren();
  for (const p of data.projects) sel.append(el("option", { value: p.id, text: p.name }));
  const want = store.get("project");
  const pid = data.projects.find((p) => p.id === (state.pid || want))?.id || data.projects[0]?.id;
  $("#empty").hidden = !!pid; $("#app").hidden = !pid;
  if (pid) { sel.value = pid; await loadProject(pid); }
}
$("#project-select").onchange = (e) => loadProject(e.target.value);
for (const id of ["new-project", "empty-new"]) $("#" + id).onclick = () => { $("#new-error").textContent = ""; $("#new-dialog").showModal(); $("#p-name").focus(); };
$("#new-cancel").onclick = () => $("#new-dialog").close();
$("#new-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const [w, h] = $("#p-format").value.split("x").map(Number);
  try {
    const p = await api("/api/projects", { method: "POST", body: JSON.stringify({ name: $("#p-name").value, width: w, height: h, fps: Number($("#p-fps").value) }) });
    $("#new-dialog").close(); $("#p-name").value = ""; state.pid = p.id; await init();
  } catch (e) { $("#new-error").textContent = e.message; }
};

async function loadProject(pid) {
  state.pid = pid; store.set("project", pid);
  clearInterval(state.exportPoll);
  const p = await api(`/api/projects/${pid}`);
  state.project = p;
  $("#viewer").hidden = true; $("#viewer").removeAttribute("src"); $("#viewer-empty").hidden = false; $("#viewer-tab").hidden = true;
  $("#export-status").replaceChildren();
  renderChat(p.chat);
  renderProject(p);
}

async function refreshProject() {
  const p = await api(`/api/projects/${state.pid}`);
  state.project = p; renderProject(p);
}

function renderProject(p) {
  renderCost(p.usage);
  renderTimeline(p.timeline);
  renderSources(p);
  const t = p.timeline || {};
  $("#undo").disabled = !t.can_undo || state.streaming; $("#redo").disabled = !t.can_redo || state.streaming;
}
function renderCost(u) {
  const lim = state.limits || {};
  $("#cost").textContent = u ? `Model cost: $${u.usd.toFixed(3)} of $${(lim.max_usd_project ?? 0).toFixed(2)}` : "";
}

// ---------------------------------------------------------------------------------------------------------------- timeline and edits
const KIND_COLOR = { text: "var(--t)", graphic: "var(--g)", image: "var(--p)", pip: "var(--p)", subtitles: "var(--t)" };
function renderTimeline(t) {
  const box = $("#timeline"); box.replaceChildren();
  const dur = t?.duration_s || 0;
  $("#duration").textContent = dur ? `${dur.toFixed(1)} s` : "";
  $("#warnings").hidden = !(t?.warnings || []).length; $("#warnings").textContent = (t?.warnings || []).join(" · ");
  if (!dur) { box.append(el("span", { class: "muted", text: "Nothing on the timeline yet." })); }
  else {
    const pct = (s) => `${(s / dur) * 100}%`;
    const step = dur > 120 ? 30 : dur > 40 ? 10 : dur > 15 ? 5 : 1;
    for (let s = 0; s <= dur; s += step) { const k = el("span", { class: "tick", text: `${s}s` }); k.style.left = pct(s); k.style.transform = s === 0 ? "none" : s >= dur - 1e-6 ? "translateX(-100%)" : ""; box.append(k); }
    const row = (bars) => { const r = el("div", { class: "track" }); for (const b of bars) r.append(b); box.append(r); };
    const bar = (s, e, label, color, title) => { const b = el("div", { class: "bar", text: label, title: title || label }); b.style.left = pct(s); b.style.width = pct(Math.max(e - s, 0.05)); b.style.background = color; return b; };
    const vbars = (t.entries || []).map((e) => bar(e.start_s, e.end_s, e.source, "var(--v)", `${e.source}: ${e.start_s}–${e.end_s} s (from ${e.source_in_s} s of the source)`));
    for (const x of t.crossfades || []) {
      const a = t.entries[x.between[0]], b = t.entries[x.between[1]];
      if (a && b) { const f = el("div", { class: "xf", title: `crossfade ${x.dur_s} s` }); f.style.left = pct(b.start_s); f.style.width = pct(a.end_s - b.start_s); vbars.push(f); }
    }
    row(vbars);
    const tracks = {};
    for (const o of t.overlays || []) (tracks[o.track] ||= []).push(bar(o.start_s, o.end_s, o.text || o.graphic || o.kind, KIND_COLOR[o.kind] || "var(--g)", `${o.kind}${o.text ? ": " + o.text : ""} (${o.start_s}–${o.end_s} s)`));
    for (const k of Object.keys(tracks).sort()) row(tracks[k]);
    if ((t.audio || []).length) row(t.audio.map((a) => bar(a.start_s ?? 0, a.end_s ?? dur, a.name || "audio", "var(--a)", a.name || "audio")));
    box.setAttribute("aria-label", `Timeline, ${dur.toFixed(1)} seconds, ${(t.entries || []).length} clips and ${(t.overlays || []).length} overlays`);
  }
  const ol = $("#edits"); ol.replaceChildren();
  for (const o of t?.ops || []) {
    const { op, id, index, ...rest } = o;
    ol.append(el("li", {}, document.createTextNode(`${op} `), el("span", { class: "muted", text: Object.entries(rest).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ").slice(0, 110) }), document.createTextNode(" "), el("code", { text: id })));
  }
}
for (const name of ["undo", "redo"]) $("#" + name).onclick = async () => {
  try { await api(`/api/projects/${state.pid}/${name}`, { method: "POST" }); await refreshProject(); } catch (e) { alert(e.message); }
};

// ---------------------------------------------------------------------------------------------------------------- tabs, viewer
for (const t of document.querySelectorAll('[role=tab]')) t.onclick = () => {
  for (const o of document.querySelectorAll('[role=tab]')) { const on = o === t; o.setAttribute("aria-selected", on); $("#" + o.getAttribute("aria-controls")).hidden = !on; }
};
$("#open-viewer").onclick = async () => {
  try {
    const v = await api(`/api/projects/${state.pid}/viewer`, { method: "POST" });
    const f = $("#viewer"); f.src = v.url; f.hidden = false; $("#viewer-empty").hidden = true;
    $("#viewer-tab").href = v.url; $("#viewer-tab").hidden = false;
  } catch (e) { $("#viewer-empty").textContent = e.message; $("#viewer-empty").hidden = false; }
};

// ---------------------------------------------------------------------------------------------------------------- files
function renderSources(p) {
  const s = $("#sources"); s.replaceChildren();
  const src = p.sources || {};
  for (const [id, v] of Object.entries(src)) s.append(el("li", { text: `${id} — ${v.duration_s}s, ${v.width}×${v.height}${v.has_audio ? ", audio" : ""}` }));
  if (!Object.keys(src).length) s.append(el("li", { class: "muted", text: "No sources yet: add videos above." }));
  const ex = $("#exports"); ex.replaceChildren();
  for (const n of p.exports || []) ex.append(el("li", {}, el("a", { href: `/api/projects/${p.id}/exports/${encodeURIComponent(n)}`, download: n, text: n })));
  if (!(p.exports || []).length) ex.append(el("li", { class: "muted", text: "No exports yet." }));
}
function upload(file) {
  const li = el("li", {}, el("div", { text: file.name }), el("progress", { max: "100", value: "0" }), el("div", { class: "muted", text: "uploading…" }));
  $("#uploads").prepend(li);
  const x = new XMLHttpRequest();
  x.open("PUT", `/api/projects/${state.pid}/uploads/${encodeURIComponent(file.name)}`);
  x.upload.onprogress = (e) => { if (e.lengthComputable) $("progress", li).value = (e.loaded / e.total) * 100; };
  x.onload = async () => {
    let d = {}; try { d = JSON.parse(x.responseText); } catch { /* */ }
    const note = $(".muted", li);
    if (x.status === 201) { note.textContent = d.source_id ? `imported as ${d.source_id}` : "uploaded"; note.className = "ok"; await refreshProject(); }
    else { note.textContent = d.error?.message || `failed (${x.status})`; note.className = "bad"; }
    $("progress", li).hidden = true;
  };
  x.onerror = () => { $(".muted", li).textContent = "network error"; $(".muted", li).className = "bad"; };
  x.send(file);
}
$("#file").onchange = (e) => { for (const f of e.target.files) upload(f); e.target.value = ""; };
const drop = $("#drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); for (const f of e.dataTransfer.files) upload(f); };
drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") $("#file").click(); };

// ---------------------------------------------------------------------------------------------------------------- export
$("#export").onclick = async () => {
  const st = $("#export-status"); st.textContent = "Starting…";
  try {
    const r = await api(`/api/projects/${state.pid}/export`, { method: "POST", body: JSON.stringify({ quality: $("#quality").value }) });
    if (!r.ok) { st.textContent = r.result?.text || "The export could not start"; return; }
    const jid = r.result.job_id; clearInterval(state.exportPoll);
    const bar = el("progress", { max: "100", value: "0" }), label = el("div", { text: "Exporting…" });
    st.replaceChildren(bar, label);
    state.exportPoll = setInterval(async () => {
      try {
        const j = (await api(`/api/projects/${state.pid}/jobs/${jid}`)).result;
        if (j.state === "running") { bar.value = j.percent || 0; label.textContent = `Exporting… ${Math.round(j.percent || 0)}%${j.eta_s ? ` · about ${Math.round(j.eta_s)} s left` : ""}`; return; }
        clearInterval(state.exportPoll);
        if (j.state === "done") { st.replaceChildren(el("span", { class: "ok", text: `Done (${j.result.duration_s} s). ` }), el("a", { href: `/api/projects/${state.pid}/exports/${encodeURIComponent(r.name)}`, download: r.name, text: "Download" })); await refreshProject(); }
        else st.replaceChildren(el("span", { class: "bad", text: `Export ${j.state}: ${j.error || ""}` }));
      } catch (e) { clearInterval(state.exportPoll); st.textContent = e.message; }
    }, 1000);
  } catch (e) { st.textContent = e.message; }
};

// ---------------------------------------------------------------------------------------------------------------- chat
const log = $("#log");
const scroll = () => { log.scrollTop = log.scrollHeight; };
function bubble(role, text = "") { const m = el("div", { class: `msg ${role}`, text }); log.append(m); scroll(); return m; }
function toolBox(name, input) {
  const d = el("details", { class: "tool run" }, el("summary", { text: `${name}${briefArgs(input)}` }), el("pre", { text: JSON.stringify(input, null, 2) }));
  log.append(d); scroll(); return d;
}
const briefArgs = (i) => { const s = Object.entries(i || {}).slice(0, 3).map(([k, v]) => `${k}=${typeof v === "object" ? "…" : String(v).slice(0, 24)}`).join(", "); return s ? ` (${s})` : ""; };
function finishTool(d, ok, text, images) {
  d.classList.remove("run"); if (!ok) d.classList.add("bad");
  d.append(el("pre", { text: text || "(no output)" }));
  if (images?.length) { const th = el("div", { class: "thumbs" }); for (const src of images) { const i = el("img", { src, alt: "Still from the edit" }); i.onclick = () => { $("#zoom-img").src = src; $("#zoom").showModal(); }; th.append(i); } log.append(th); }
  scroll();
}
function renderChat(messages) {
  log.replaceChildren();
  const tools = {};
  for (const m of messages || []) {
    if (m.role === "user" && typeof m.content === "string") { bubble("user", m.content); continue; }
    for (const b of Array.isArray(m.content) ? m.content : []) {
      if (b.type === "text" && b.text) bubble(m.role === "user" ? "user" : "assistant", b.text);
      else if (b.type === "tool_use") tools[b.id] = toolBox(b.name, b.input);
      else if (b.type === "tool_result" && tools[b.tool_use_id]) {
        const txt = (b.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
        finishTool(tools[b.tool_use_id], !b.is_error, txt.slice(0, 4000), []);
      }
    }
  }
  if (!(messages || []).length) bubble("assistant", "Hi! Add your videos in Files, then tell me what to make.");
  scroll();
}
function setBusy(on) {
  state.streaming = on; $("#send").hidden = on; $("#stop").hidden = !on; $("#input").disabled = on;
  $("#undo").disabled = on || !state.project?.timeline?.can_undo; $("#redo").disabled = on || !state.project?.timeline?.can_redo;
}
function scheduleRefresh() { clearTimeout(state.refreshTimer); state.refreshTimer = setTimeout(() => refreshProject().catch(() => {}), 400); }

async function send(text) {
  if (state.streaming || !text.trim()) return;
  bubble("user", text); setBusy(true);
  let cur = null; const tools = {};
  state.abort = new AbortController();
  try {
    const r = await fetch(`/api/projects/${state.pid}/chat`, { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify({ message: text }), signal: state.abort.signal });
    if (r.status === 401) { showLogin(); return; }
    if (!r.ok) { const d = await r.json().catch(() => ({})); bubble("assistant", "").append(el("span", { class: "err", text: `${d.error?.code || r.status}: ${d.error?.message || r.statusText}` })); return; }
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === "text_delta") { cur ||= bubble("assistant"); cur.textContent += ev.text; scroll(); }
        else if (ev.type === "tool_call") { cur = null; tools[ev.id] = toolBox(ev.name, ev.input); }
        else if (ev.type === "tool_result") finishTool(tools[ev.id], ev.ok, ev.text, ev.images);
        else if (ev.type === "project_changed") scheduleRefresh();
        else if (ev.type === "usage") { renderCost({ usd: ev.project_usd }); }
        else if (ev.type === "error") { cur = null; bubble("assistant").append(el("span", { class: "err", text: `${ev.code}: ${ev.message}` })); }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") bubble("assistant").append(el("span", { class: "err", text: `Connection problem: ${e.message}` }));
    else bubble("assistant").append(el("span", { class: "muted", text: "Stopped." }));
  } finally {
    setBusy(false); state.abort = null; $("#input").focus();
    refreshProject().catch(() => {});
  }
}
$("#composer").onsubmit = (e) => { e.preventDefault(); const t = $("#input").value; $("#input").value = ""; send(t); };
$("#input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); } };
$("#stop").onclick = () => state.abort?.abort();

init();
