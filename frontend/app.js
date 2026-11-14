"use strict";
// Same-origin by default (FastAPI serves this folder). To use a separate static server: index.html?api=http://127.0.0.1:8000
const API = (new URLSearchParams(location.search).get("api") || "") + "/api";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const pct = (v) => (v == null ? "n/a" : (v * 100).toFixed(1) + "%");
const STATUSES = ["OPEN", "IN_PROGRESS", "RESOLVED"];

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    let d = "";
    try { const j = await r.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { d = r.statusText; }
    throw new Error(r.status + " " + d);
  }
  return r.json();
}
const send = (method, path, body) => api(path, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
function showError(e) { const b = $("#error"); b.textContent = e ? String(e.message || e) : ""; b.hidden = !e; }
async function guard(fn) { try { showError(null); await fn(); } catch (e) { showError(e); } }
const sev = (s) => `<span class="sev-${esc(s)}">${esc(s)}</span>`;
const tags = (ids) => (ids || []).map((i) => `<span class="tag">${esc(i)}</span>`).join(" ");

// ---------------------------------------------------------------- tabs
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll("section").forEach((s) => (s.hidden = s.id !== "tab-" + b.dataset.tab));
  ({dashboard: loadDashboard, alerts: loadAlerts, investigations: loadInvestigations, warnings: loadWarnings})[b.dataset.tab]();
}));

// ---------------------------------------------------------------- dashboard
function dateQuery() {
  const p = new URLSearchParams();
  if ($("#date-from").value) p.set("date_from", $("#date-from").value);
  if ($("#date-to").value) p.set("date_to", $("#date-to").value);
  const s = p.toString();
  return s ? "?" + s : "";
}
function loadDashboard() {
  return guard(async () => {
    const [plant, lines] = await Promise.all([api("/kpi/plant" + dateQuery()), api("/kpi/lines" + dateQuery())]);
    $("#source").textContent = `${plant.machine_shifts} machine-shifts - source: ${plant.source}`;
    const card = (l, v) => `<div class="card"><div class="l">${l}</div><div class="v">${pct(v)}</div></div>`;
    $("#kpi-cards").innerHTML = card("Availability", plant.availability) + card("Performance", plant.performance) +
      card("Quality", plant.quality) + card("OEE", plant.oee) +
      `<div class="card"><div class="l">Status</div><div class="v" style="font-size:18px">${esc(plant.status)}</div>` +
      `<div class="muted">${esc((plant.flags || []).join(", ")) || "no flags"}</div></div>`;
    $("#line-table tbody").innerHTML = lines.items.map((l) => `<tr><td>${esc(l.line_id)}</td><td>${pct(l.availability)}</td>` +
      `<td>${pct(l.performance)}</td><td>${pct(l.quality)}</td><td>${pct(l.oee)}</td><td>${l.machine_shifts}</td><td>${esc(l.status)}</td></tr>`).join("");
  });
}
$("#dash-refresh").addEventListener("click", loadDashboard);

// ---------------------------------------------------------------- alerts
let selectedAlert = null;
function loadAlerts() {
  return guard(async () => {
    const p = new URLSearchParams();
    if ($("#f-severity").value) p.set("severity", $("#f-severity").value);
    if ($("#f-type").value) p.set("anomaly_type", $("#f-type").value);
    if ($("#f-machine").value.trim()) p.set("machine_id", $("#f-machine").value.trim());
    const data = await api("/alerts" + (p.toString() ? "?" + p : ""));
    $("#alerts-count").textContent = data.total + " alert(s)";
    const t = $("#alert-table"); t.classList.add("clickable");
    t.querySelector("tbody").innerHTML = data.items.map((a) => `<tr data-id="${esc(a.alert_id)}" class="${a.alert_id === selectedAlert ? "sel" : ""}">` +
      `<td>${sev(a.severity)}</td><td>${esc(a.anomaly_type)}</td><td>${esc(a.machine_id)}</td><td>${esc(a.shift_instance_id)}</td>` +
      `<td>${esc(a.kpi)}</td><td>${pct(a.measured_value)}</td><td>${pct(a.baseline_value)}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">No alerts. Run detection first (see README).</td></tr>`;
    t.querySelectorAll("tbody tr[data-id]").forEach((tr) => tr.addEventListener("click", () => showAlert(tr.dataset.id)));
  });
}
function showAlert(id) {
  return guard(async () => {
    selectedAlert = id;
    document.querySelectorAll("#alert-table tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
    const a = await api("/alerts/" + encodeURIComponent(id));
    const impact = a.kpi_impact || {};
    const invs = (a.investigations || []).map((i) => `<li><a href="#" data-inv="${esc(i.investigation_id)}">${esc(i.investigation_id)}</a> - ${esc(i.status)}${i.owner ? " (" + esc(i.owner) + ")" : ""}</li>`).join("");
    const active = (a.investigations || []).some((i) => i.status !== "RESOLVED");
    $("#alert-detail").innerHTML = `<h3>${esc(a.anomaly_type)} ${sev(a.severity)}</h3>
      <p><b>${esc(a.machine_id)}</b> - ${esc(a.shift_instance_id)}${a.order_id ? " - order " + esc(a.order_id) : ""}<br><span class="muted">${esc(a.alert_id)}</span></p>
      <p>${esc(a.explanation)}</p>
      <p><b>Measured</b> ${pct(a.measured_value)} vs <b>baseline</b> ${pct(a.baseline_value)} (n=${a.baseline_n}, z=${a.deviation_score})</p>
      <p><b>KPI impact</b></p><pre>${esc(JSON.stringify(impact, null, 1))}</pre>
      <p><b>Recommended action</b><br>${esc(a.recommended_action)}</p>
      <p><b>Evidence</b><br>${tags(a.evidence_ids)}</p>
      <p><button id="btn-ms">View machine-shift OEE record</button></p><div id="ms-detail"></div>
      <h4>Investigations</h4><ul>${invs || '<li class="muted">none yet</li>'}</ul>
      ${active ? '<p class="muted">An active investigation already exists for this alert.</p>' :
        `<div><b>Create investigation</b><br>Owner <input id="new-owner" placeholder="owner name"><br>
         <textarea id="new-notes" rows="2" placeholder="initial notes (optional)"></textarea>
         <button id="btn-create">Create investigation</button></div>`}`;
    $("#btn-ms").addEventListener("click", () => showMachineShift(a.aggregate_id));
    const create = $("#btn-create");
    if (create) create.addEventListener("click", () => guard(async () => {
      const inv = await send("POST", "/investigations", {alert_id: id, owner: $("#new-owner").value, notes: $("#new-notes").value});
      await showAlert(id);
      await openInvestigation(inv.investigation_id, true);
    }));
    $("#alert-detail").querySelectorAll("[data-inv]").forEach((x) => x.addEventListener("click", (e) => { e.preventDefault(); openInvestigation(x.dataset.inv, true); }));
  });
}
function showMachineShift(aggId) {
  return guard(async () => {
    const r = await api("/machine-shifts/" + encodeURIComponent(aggId));
    const s = r.summary, ev = r.detail.evidence || {};
    $("#ms-detail").innerHTML = `<pre>A ${pct(s.availability)}  P ${pct(s.performance)}  Q ${pct(s.quality)}  OEE ${pct(s.oee)}
status ${esc(s.status)}   flags ${esc(s.flags.join(", ") || "none")}
operating ${Math.round(s.operating_seconds / 60)} min / planned ${Math.round(s.planned_production_seconds / 60)} min, downtime ${Math.round(s.downtime_seconds / 60)} min
count ${s.total_count}, good ${s.good_count}</pre>` +
      Object.keys(ev).map((k) => `<div><b>${esc(k)}</b>: ${tags(ev[k])}</div>`).join("");
  });
}
$("#alerts-refresh").addEventListener("click", loadAlerts);

// ---------------------------------------------------------------- investigations
let selectedInv = null;
function loadInvestigations() {
  return guard(async () => {
    const f = $("#inv-filter").value;
    const data = await api("/investigations" + (f ? "?status=" + encodeURIComponent(f) : ""));
    $("#inv-count").textContent = data.total + " investigation(s)";
    const t = $("#inv-table"); t.classList.add("clickable");
    t.querySelector("tbody").innerHTML = data.items.map((i) => `<tr data-id="${esc(i.investigation_id)}" class="${i.investigation_id === selectedInv ? "sel" : ""}">` +
      `<td>${esc(i.investigation_id)}</td><td>${esc(i.status)}</td><td>${esc(i.owner || "-")}</td><td>${esc(i.alert_id)}</td>` +
      `<td>${esc(i.machine_id)}</td><td>${esc(i.shift_instance_id)}</td><td>${sev(i.severity)}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">No investigations yet. Create one from an alert.</td></tr>`;
    t.querySelectorAll("tbody tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openInvestigation(tr.dataset.id, false)));
  });
}
function openInvestigation(id, switchTab) {
  return guard(async () => {
    selectedInv = id;
    if (switchTab) document.querySelector('nav button[data-tab="investigations"]').click();
    const i = await api("/investigations/" + encodeURIComponent(id));
    $("#inv-detail").innerHTML = `<h3>${esc(i.investigation_id)} - ${esc(i.status)}</h3>
      <p>${esc(i.title)}<br><span class="muted">alert ${esc(i.alert_id)} | ${esc(i.aggregate_id)}${i.order_id ? " | order " + esc(i.order_id) : ""}</span></p>
      <p><b>Evidence</b><br>${tags(i.evidence_ids)}</p>
      <p>Status <select id="inv-status">${STATUSES.map((s) => `<option${s === i.status ? " selected" : ""}>${s}</option>`).join("")}</select>
         Owner <input id="inv-owner" value="${esc(i.owner || "")}"></p>
      <textarea id="inv-note" rows="2" placeholder="add a note (optional)"></textarea>
      <p><button id="inv-save">Save changes</button></p>
      <h4>History</h4><ul class="notes">${i.notes.map((n) => `<li><span class="muted">${esc(String(n.created_at).replace("T", " "))} ${esc(n.kind)}${n.author ? " - " + esc(n.author) : ""}</span><br>${esc(n.text)}</li>`).join("")}</ul>`;
    $("#inv-save").addEventListener("click", () => guard(async () => {
      const body = {};
      if ($("#inv-status").value !== i.status) body.status = $("#inv-status").value;
      if ($("#inv-owner").value !== (i.owner || "")) body.owner = $("#inv-owner").value;
      if ($("#inv-note").value.trim()) body.note = $("#inv-note").value;
      if (!Object.keys(body).length) throw new Error("Nothing changed.");
      await send("PATCH", "/investigations/" + encodeURIComponent(id), body);
      await loadInvestigations();
      await openInvestigation(id, false);
    }));
    document.querySelectorAll("#inv-table tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
  });
}
$("#inv-refresh").addEventListener("click", loadInvestigations);

// ---------------------------------------------------------------- data-quality warnings
function loadWarnings() {
  return guard(async () => {
    const data = await api("/warnings");
    $("#warn-table tbody").innerHTML = data.items.map((w) => `<tr><td>${sev(w.severity)}</td><td>${esc(w.issue_type)}</td><td>${esc(w.machine_id)}</td>` +
      `<td>${esc(w.shift_instance_id)}</td><td>${esc(w.explanation)}</td><td>${tags(w.evidence_ids)}</td><td>${esc(w.expected_decision)}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">No data-quality warnings.</td></tr>`;
  });
}

loadDashboard();
