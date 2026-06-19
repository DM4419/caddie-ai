// caddie-ai — front-end. Talks to the FastAPI backend.
"use strict";

const modeCls = { remote: "m-remote", hybrid: "m-hybrid", onsite: "m-onsite" };
const modeLbl = { remote: "Remote", hybrid: "Hybrid", onsite: "Onsite" };
const stPill = {
  new: '<span class="status-pill st-new">new</span>',
  review: '<span class="status-pill st-review">in review</span>',
  approved: '<span class="status-pill st-approved">approved</span>',
  applied: '<span class="status-pill st-applied">applied</span>',
  skipped: '<span class="status-pill st-skipped">skipped</span>',
};

let currentJob = null;

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}
function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
let _sources = {};                  // board name -> domain, from /api/jobs
function urlHost(u) { try { return new URL(u).hostname.replace("www.", ""); } catch (e) { return ""; } }
function srcInitials(name) {
  const w = (name || "?").trim().split(/[\s\-_.]+/).filter(Boolean);
  return ((((w[0] || "")[0] || "") + ((w[1] || "")[0] || "")).toUpperCase()) || (name || "?").slice(0, 2).toUpperCase();
}
function srcColor(name) {
  let h = 0; for (const c of (name || "x")) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h},42%,52%)`;
}
function sourceIcon(j) {
  const name = j.source || urlHost(j.url) || j.company || "";
  const domain = _sources[j.source] || urlHost(j.url);
  const title = j.source ? `Source: ${esc(j.source)}` : (domain ? `Source: ${esc(domain)}` : "Source unknown");
  const av = `<span class="srcav" style="background:${srcColor(name)};${domain ? "display:none" : ""}" title="${title}">${esc(srcInitials(name))}</span>`;
  const fav = domain
    ? `<img class="srcfav" src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64" title="${title}" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex'">`
    : "";
  return `<span class="src">${fav}${av}</span>`;
}
function relTime(s) {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return days + "d ago";
  if (days < 365) return Math.floor(days / 30) + "mo ago";
  return Math.floor(days / 365) + "y ago";
}
// Prefer the board's posting date. When it's unknown, show the fetch date but
// label it so a recently-scraped stale listing doesn't read as freshly posted.
function postedLabel(j) {
  if (j.posted) return `posted ${relTime(j.posted)}`;
  return `<span title="No posting date from this board — this is when it was fetched, not posted">added ${relTime(j.date)} · posting date unknown</span>`;
}

// ---- navigation ----------------------------------------------------------
const views = ["jobs", "review", "boards", "settings"];
function go(v) {
  views.forEach(x => document.getElementById(x).classList.toggle("hide", x !== v));
  document.querySelectorAll("#nav button").forEach(b =>
    b.classList.toggle("active", b.dataset.view === v));
  window.scrollTo(0, 0);
  if (v === "jobs") loadJobs();
  if (v === "boards") loadBoards();
  if (v === "settings") loadSettings();
  writeHash();
}
document.querySelectorAll("#nav button").forEach(b => b.onclick = () => go(b.dataset.view));

// ---- URL router: deep-linkable role IDs + filter combos ------------------
// #/jobs?tab=voice&work=remote_uk&date=7&sort=weighted  ·  #/job/<id>  ·  #/boards  ·  #/settings
let _selfHash = null;
function writeHash() {
  const vis = id => !document.getElementById(id).classList.contains("hide");
  let h = "#/jobs";
  if (vis("review") && currentJob) h = `#/job/${currentJob.id}`;
  else if (vis("boards")) h = "#/boards";
  else if (vis("settings")) h = "#/settings";
  else {
    const p = new URLSearchParams();
    if (jobView !== "active") p.set("tab", jobView);
    if (workFilter !== "all") p.set("work", workFilter);
    if (dateFilter !== "any") p.set("date", dateFilter);
    if (sortBy !== "score") p.set("sort", sortBy);
    const q = p.toString();
    h = "#/jobs" + (q ? "?" + q : "");
  }
  if (location.hash !== h) { _selfHash = h; location.hash = h; }   // our own write — ignored by the listener
}
function syncJobControls() {
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  set("workFilter", workFilter); set("dateFilter", dateFilter); set("sortBy", sortBy);
  JOB_TABS.forEach(t => { const e = document.getElementById("tab-" + t); if (e) e.classList.toggle("on", jobView === t); });
}
function routeFromHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const seg = (path || "").split("/");
  if (seg[0] === "job" && seg[1]) { openReview(seg[1]); return; }
  if (path === "boards") { go("boards"); return; }
  if (path === "settings") { go("settings"); return; }
  const p = new URLSearchParams(query || "");           // jobs (default)
  jobView = p.get("tab") || "active";
  workFilter = p.get("work") || "all";
  dateFilter = p.get("date") || "any";
  sortBy = p.get("sort") || "score";
  syncJobControls();
  if (document.getElementById("jobs").classList.contains("hide")) go("jobs"); else loadJobs();
}
window.addEventListener("hashchange", () => {
  if (location.hash === _selfHash) { _selfHash = null; return; }   // skip hashes we wrote ourselves
  routeFromHash();
});

// ---- jobs list -----------------------------------------------------------
let jobView = "active";
const JOB_TABS = ["active", "founder", "voice", "bookmarked", "applied", "skipped", "archived"];
const FOUNDER_FLAGS = ["eir", "zero_to_one", "founder_welcome"];
const FLAG_CHIP = {           // job flags -> compact label
  eir: ["EIR", "Entrepreneur / Founder-in-Residence"],
  zero_to_one: ["0→1", "0→1 build focus"],
  founder_welcome: ["ex-founder", "Ex-founders welcome"],
  voice_ai: ["🎙 Voice", "Voice / conversational AI"],
  web3: ["⛓ Web3", "Web3 / Blockchain / crypto"],
};
function flagChips(j) {
  return (j.flags || []).map(f => {
    const [lbl, tip] = FLAG_CHIP[f] || [f, f];
    return `<span class="flagchip" title="${esc(tip)}">⚑ ${esc(lbl)}</span>`;
  }).join("");
}
function setJobView(v) {
  jobView = v;
  JOB_TABS.forEach(t => { const el = document.getElementById("tab-" + t); if (el) el.classList.toggle("on", v === t); });
  const sel = document.getElementById("statusSel");
  if (sel) { const isStatus = ["applied", "skipped", "archived"].includes(v); sel.value = isStatus ? v : ""; sel.classList.toggle("on", isStatus); }
  loadJobs();
  writeHash();
}
async function toggleBookmark(id, val) {
  await api(`/api/jobs/${id}/bookmark`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bookmarked: val }),
  });
  loadJobs();
}
// finer work classification: mode + location -> tier (best to worst)
const TIERS = [
  { key: "remote_any", label: "Remote · Anywhere", cls: "m-remote" },
  { key: "remote_uk", label: "Remote · UK", cls: "m-remote" },
  { key: "remote_eu", label: "Remote · EU", cls: "m-remote" },
  { key: "remote_country", label: "Remote · Other", cls: "m-remote" },
  { key: "remote_us", label: "Remote · US/Americas", cls: "m-onsite" },
  { key: "hybrid_london", label: "Hybrid · London/UK", cls: "m-hybrid" },
  { key: "hybrid_other", label: "Hybrid · Non-UK", cls: "m-hybrid" },
  { key: "office_uk", label: "In office · UK", cls: "m-onsite" },
  { key: "office_other", label: "In office · Non-UK", cls: "m-onsite" },
];
const T = k => TIERS.find(t => t.key === k);
const UK_RE = /\b(uk|u\.k\.|united kingdom|england|scotland|wales|britain|northern ireland|london|manchester|birmingham|leeds|glasgow|edinburgh|bristol|cambridge|oxford|belfast)\b/i;
const ANYWHERE_RE = /\b(anywhere|worldwide|global|fully remote|remote[-\s]?first|distributed)\b/i;
const US_RE = /\b(usa|u\.?s\.?a?\.?|united states|america|american|new york|san francisco|los angeles|chicago|boston|seattle|austin|miami|denver|atlanta|dallas|houston|philadelphia|nyc|brooklyn|manhattan|sf|washington dc|d\.c\.)\b|\bus\b|,\s*(ny|ca|tx|fl|ma|il|co|ga|va|nc|wa|az|pa|oh|mi|nj|md|mn|or|ut|tn)\b/i;
const AMERICAS_RE = /\b(americas?|latin america|latam|north america|south america|central america)\b/i;
const EU_RE = /\b(eu|e\.u\.|europe|european union|european|eea|euro ?zone|emea|germany|france|spain|netherlands|italy|poland|ireland|portugal|sweden|belgium|austria|denmark|finland|norway|switzerland|czech|romania|greece|hungary|bulgaria|croatia|estonia|lithuania|latvia|slovakia|slovenia|luxembourg|berlin|paris|madrid|amsterdam|barcelona|munich|dublin|lisbon|warsaw|stockholm|copenhagen|zurich|vienna|prague|milan|rome|sofia)\b/i;
function workTier(j) {
  const loc = (j.location || "").toLowerCase().trim();
  if (j.mode === "remote") {
    if (j.remote_anywhere || !loc || loc === "remote" || ANYWHERE_RE.test(loc)) return T("remote_any");
    if (US_RE.test(loc) || AMERICAS_RE.test(loc)) return T("remote_us");
    if (UK_RE.test(loc)) return T("remote_uk");
    if (EU_RE.test(loc)) return T("remote_eu");
    return T("remote_country");
  }
  if (j.mode === "hybrid") return UK_RE.test(loc) ? T("hybrid_london") : T("hybrid_other");
  return UK_RE.test(loc) ? T("office_uk") : T("office_other");
}

let _jobs = [];
let workFilter = "all";
let dateFilter = "any";
let sortBy = "score";
function setWorkFilter(v) { workFilter = v; renderJobRows(); writeHash(); }
function setDateFilter(v) { dateFilter = v; renderJobRows(); writeHash(); }
function setSortBy(v) { sortBy = v; renderJobRows(); writeHash(); }
function parseSalary(s) {
  if (!s) return -1;                              // no salary sorts to the bottom
  const cur = s.includes("$") ? 0.79 : s.includes("€") ? 0.86 : 1;  // rough → GBP
  const nums = (s.match(/\d[\d,]*\s*k?/gi) || []).map(x => {
    let n = parseFloat(x.replace(/[,\s]/g, ""));
    if (/k/i.test(x)) n *= 1000;
    return n;
  });
  return nums.length ? Math.max(...nums) * cur : -1;
}
function jobAgeDays(j) {
  const d = j.posted || j.date;
  if (!d) return null;
  const t = new Date(d).getTime();
  if (isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86400000);
}
// Age by the board's POSTING date only (null when unknown) — used by the
// date-posted sort so stale, recently-fetched listings don't read as newest.
function postedAgeDays(j) {
  if (!j.posted) return null;
  const t = new Date(j.posted).getTime();
  if (isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86400000);
}
// Higher = more senior. Used for the "Seniority" sort.
function seniorityRank(j) {
  const r = (j.role || "").toLowerCase();
  if (/\b(cpo|chief product officer)\b/.test(r)) return 6;
  if (/\b(vp|vice president)\b/.test(r)) return 5;
  if (/(head of product|director of product|product director)/.test(r)) return 4;
  if (/(group|principal|lead) product manager/.test(r)) return 3;
  if (/senior product manager|\bsenior pm\b/.test(r)) return 2;
  if (/product manager|\bpm\b/.test(r)) return 1;
  return 0;
}

async function loadJobs() {
  const data = await api(`/api/jobs?archived=${jobView === "archived"}`);
  document.getElementById("cvBanner").classList.toggle("hide", data.base.cv);
  _sources = data.sources || {};
  _jobs = data.jobs;
  const c = data.counts || {};
  const setOpt = (id, base, n) => { const o = document.getElementById(id); if (o) o.textContent = n ? `${base} (${n})` : base; };
  setOpt("opt-archived", "Archived", data.archived_count);
  setOpt("opt-applied", "Applied", c.applied);
  setOpt("opt-skipped", "Skipped", c.skipped);
  // founder / voice / bookmarked counts recomputed client-side so they match the rows
  // (excludes applied/skipped AND roles from companies you've already applied to).
  const applied = appliedCompanies();
  const inApplied = j => applied.has((j.company || "").trim().toLowerCase());
  const untri = j => j.status !== "applied" && j.status !== "skipped" && !j.role_off_target && !inApplied(j);
  const setN = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n ? `(${n})` : ""; };
  setN("founderCount", _jobs.filter(j => untri(j) && (j.flags || []).some(f => FOUNDER_FLAGS.includes(f))).length);
  setN("voiceCount", _jobs.filter(j => untri(j) && (j.flags || []).includes("voice_ai")).length);
  setN("bookmarkedCount", _jobs.filter(j => j.bookmarked && j.status !== "applied" && j.status !== "skipped" && !inApplied(j)).length);
  renderJobRows();
}

// Companies you've already applied to — their OTHER roles drop out of the active /
// founder / voice / bookmarked lists (they remain reachable only under Applied).
function appliedCompanies() {
  return new Set((_jobs || []).filter(j => j.status === "applied")
    .map(j => (j.company || "").trim().toLowerCase()).filter(Boolean));
}

function renderJobRows() {
  // tab scope: Active excludes applied/skipped + roles from applied companies
  let jobs = _jobs;
  const applied = appliedCompanies();
  const inApplied = j => applied.has((j.company || "").trim().toLowerCase());
  // active/founder/voice hide off-target roles (e.g. Technical PM); they stay reachable via their own status tabs
  const untriaged = j => j.status !== "applied" && j.status !== "skipped" && !j.role_off_target && !inApplied(j);
  if (jobView === "active") jobs = jobs.filter(untriaged);
  else if (jobView === "founder") jobs = jobs.filter(j => untriaged(j) && (j.flags || []).some(f => FOUNDER_FLAGS.includes(f)));
  else if (jobView === "voice") jobs = jobs.filter(j => untriaged(j) && (j.flags || []).includes("voice_ai"));
  else if (jobView === "bookmarked") jobs = jobs.filter(j => j.bookmarked && j.status !== "applied" && j.status !== "skipped" && !inApplied(j));
  else if (jobView === "applied") jobs = jobs.filter(j => j.status === "applied");
  else if (jobView === "skipped") jobs = jobs.filter(j => j.status === "skipped");
  const tabTotal = jobs.length;
  if (workFilter !== "all") jobs = jobs.filter(j => workTier(j).key === workFilter);
  if (dateFilter !== "any") {
    const max = parseInt(dateFilter, 10);
    jobs = jobs.filter(j => { const a = jobAgeDays(j); return a !== null && a <= max; });
  }
  const SORTS = {
    score: { label: "AI score", cmp: (a, b) => b.score - a.score },
    weighted: { label: "weighted score", cmp: (a, b) => (b.weight_score || 0) - (a.weight_score || 0) || b.score - a.score },
    salary: { label: "salary", cmp: (a, b) => parseSalary(b.salary) - parseSalary(a.salary) },
    seniority: { label: "seniority", cmp: (a, b) => seniorityRank(b) - seniorityRank(a) || b.score - a.score },
    posted: {
      label: "date posted", cmp: (a, b) => {
        // strictly by posting date; unknown-posting jobs sort to the bottom
        const da = postedAgeDays(a), db = postedAgeDays(b);
        if (da === null) return db === null ? 0 : 1;
        if (db === null) return -1;
        return da - db;
      }
    },
  };
  const sort = SORTS[sortBy] || SORTS.score;
  if (sortBy !== "score") jobs = jobs.slice().sort(sort.cmp);
  const meta = document.getElementById("jobsMeta");
  const parts = [];
  if (workFilter !== "all") parts.push(TIERS.find(t => t.key === workFilter).label);
  if (dateFilter !== "any") parts.push(`posted ≤ ${dateFilter}d`);
  const filt = parts.length ? ` · ${parts.join(" · ")}` : "";
  meta.textContent = tabTotal
    ? `${jobs.length} of ${tabTotal} ${jobView} job(s)${filt} · sorted by ${sort.label}`
    : (jobView === "archived" ? "No archived jobs."
      : jobView === "applied" ? "No applied jobs yet."
      : jobView === "skipped" ? "No skipped jobs."
      : jobView === "founder" ? "No founder-fit roles yet (EIR · 0→1 · ex-founder-welcome) — scan more boards."
      : jobView === "voice" ? "No Voice / conversational-AI roles yet — scan more boards."
      : jobView === "bookmarked" ? "No bookmarked jobs — tap the ☆ on any row to pin it here."
      : "No active jobs — click ↻ Refresh, or + Paste JD.");
  const tbody = document.getElementById("jobRows");
  if (!jobs.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">${tabTotal ? "No jobs match this work-type filter." : "Nothing here yet. Click “↻ Refresh” to scan boards, or “+ Paste JD”."}</td></tr>`;
    return;
  }
  tbody.innerHTML = jobs.map(j => {
    const s = j.score >= 80 ? "s-hi" : j.score >= 70 ? "s-mid" : "s-lo";
    const ws = j.weight_score >= 70 ? "s-hi" : j.weight_score >= 50 ? "s-mid" : "s-lo";
    const lang = j.language_block
      ? ` <span class="lang-block" title="${esc(j.language_note)}">🚫 LANG</span>` : "";
    const roff = j.role_off_target
      ? ` <span class="role-off" title="${esc(j.role_note)}">OFF-TARGET</span>` : "";
    const dead = j.live === "expired"
      ? ` <span class="role-off" title="${esc(j.live_note || "posting no longer at source")}">EXPIRED</span>` : "";
    const drivers = (j.drivers || []).length
      ? j.drivers.map(d => `<div class="muted">“${esc(d)}”</div>`).join("")
      : '<span class="muted">—</span>';
    const unmet = (j.unmet || []).length
      ? j.unmet.map(u => `<span class="unmet-pill">${esc(u)}</span>`).join("")
      : '<span class="muted">—</span>';
    const star = `<span class="star ${j.bookmarked ? "on" : ""}" title="${j.bookmarked ? "Bookmarked — won't be archived" : "Bookmark"}" onclick="event.stopPropagation();toggleBookmark('${j.id}',${!j.bookmarked})">${j.bookmarked ? "★" : "☆"}</span>`;
    const tier = workTier(j);
    return `<tr class="rowlink" onclick="openReview('${j.id}')">
      <td class="score ${s}" title="AI fit score (rubric)">${j.score}</td>
      <td class="score ${ws}" title="Weighted keyword-factor score">${j.weight_score ?? 0}</td>
      <td style="width:190px">${star}${esc(j.role)}${lang}${roff}${dead}${flagChips(j)}<div class="sub2" style="display:flex;align-items:center;gap:5px">${sourceIcon(j)}<span>${esc(j.company)} · ${postedLabel(j)}${j.salary ? ` · <strong style="color:#166534">${esc(j.salary)}</strong>` : ""}</span></div></td>
      <td class="muted" style="font-size:11px;max-width:90px;line-height:1.3">${esc(j.location) || "—"}</td>
      <td><span class="mode ${tier.cls}" style="white-space:nowrap">${tier.label}</span></td>
      <td style="font-size:11px;width:340px;line-height:1.4">${drivers}</td>
      <td style="max-width:200px">${unmet}</td>
      <td>${stPill[j.status] || esc(j.status)}</td></tr>`;
  }).join("");
}

function scanLine(s) {
  const all = s.boards || [];
  const done = all.filter(b => ["done", "error", "skipped"].includes(b.status)).length;
  const newSoFar = all.reduce((a, b) => a + (b.imported || 0), 0);
  if (!s.done)
    return `<span class="spin"></span> Refreshing from ${done}/${all.length} boards · ${all.length - done} to go · ${newSoFar} new so far`;
  const t = s.totals || {};
  const errs = all.filter(b => b.status === "error").length;
  return `✓ Refreshed ${all.length} boards · <strong>${t.imported || 0} new roles added</strong> · <strong>${t.good || 0} good match (&gt;75)</strong>`
    + (t.archived ? ` · ${t.archived} archived` : "") + (errs ? ` · ${errs} errored` : "");
}
async function pollScan(state) {
  const banner = document.getElementById("scanResult");
  banner.className = "banner"; banner.style = "background:#eef2ff;border-color:#c7d2fe;color:#3730a3";
  banner.classList.remove("hide");
  banner.innerHTML = scanLine(state);
  while (!state.done) {                       // poll until the background scan finishes
    await new Promise(r => setTimeout(r, 1500));
    state = await api("/api/jobs/refresh/status");
    banner.innerHTML = scanLine(state);
  }
  banner.style = "background:#dcfce7;border-color:#86efac;color:#166534";
  banner.innerHTML = scanLine(state);
  loadJobs();
}
async function refreshJobs() {
  const btn = document.getElementById("refreshBtn");
  const old = btn.textContent;
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Scanning…';
  document.getElementById("scanResult").classList.add("hide");
  try {
    const state = await api("/api/jobs/refresh", { method: "POST" });
    await pollScan(state);
  } catch (e) {
    const banner = document.getElementById("scanResult");
    banner.className = "banner"; banner.style = "background:#fee2e2;border-color:#fca5a5;color:#991b1b";
    banner.textContent = "✗ Refresh failed: " + e.message; banner.classList.remove("hide");
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}
async function scanGroup(group, slug) {
  try {
    let state = await api("/api/boards/group-scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group }),
    });
    expandGroup(slug);                          // open the group so rows update live
    renderGroupScan(state, slug);
    while (!state.done) {                        // poll, updating rows in-line
      await new Promise(r => setTimeout(r, 1500));
      state = await api("/api/jobs/refresh/status");
      renderGroupScan(state, slug);
    }
    const t = state.totals || {};
    const prog = document.getElementById("grpprog-" + slug);
    if (prog) prog.innerHTML = `· <span style="color:#166534">✓ ${t.imported || 0} new · ${t.dropped || 0} off-target · ${t.skipped || 0} dup</span>`;
    loadJobs();                                 // refresh job counts in the background
  } catch (e) { alert("Group scan failed: " + e.message); }
}

function togglePaste() {
  document.getElementById("pastePanel").classList.toggle("hide");
  document.getElementById("pasteMsg").textContent = "";
}
async function addPasted() {
  const url = document.getElementById("pUrl").value.trim();
  const text = document.getElementById("pText").value.trim();
  const msg = document.getElementById("pasteMsg");
  if (!url && !text) { msg.textContent = "Paste a job URL or description."; return; }
  const btn = document.getElementById("pasteAdd");
  btn.disabled = true; msg.innerHTML = '<span class="spin"></span>Fetching & scoring…';
  try {
    const { job } = await api("/api/jobs/paste", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, text }),
    });
    document.getElementById("pUrl").value = "";
    document.getElementById("pText").value = "";
    togglePaste();
    openReview(job.id);
  } catch (e) {
    msg.textContent = "✗ " + e.message;
  } finally {
    btn.disabled = false;
  }
}
function downloadCsv() { window.location = "/api/applications.csv"; }

// Edit a job's JD/application URL (so screening questions can be fetched from it).
function editJobUrl() {
  if (!currentJob) return;
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <div class="mh"><h3>Job / application URL</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb">
        <div class="rat">Point this at the <strong>application page</strong> so the real screening questions can be fetched (Lever &amp; Greenhouse auto-fetch on Generate; other ATSes use <strong>↑ Screening Qs</strong>).</div>
        <input type="text" id="jobUrlBox" value="${esc(currentJob.url || "")}" placeholder="https://…/apply" style="width:100%;border:1px solid var(--line);border-radius:8px;padding:8px;font-size:13px">
        <div style="margin-top:8px"><span id="jobUrlMsg" class="hint"></span></div>
      </div>
      <div class="mf"><button class="btn ghost" onclick="closeModal()">Cancel</button>
        <button class="btn ok" onclick="saveJobUrl()">Save URL</button></div>
    </div></div>`;
}
async function saveJobUrl() {
  const url = document.getElementById("jobUrlBox").value.trim();
  const msg = document.getElementById("jobUrlMsg");
  msg.innerHTML = '<span class="spin"></span>saving & checking for questions…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/url`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    currentJob.url = r.url;
    const note = r.questions_fetched
      ? `<span class="ok-tag">✓ saved</span> — ${r.questions_fetched} question(s) auto-fetchable; click Regenerate to use them.`
      : `<span class="ok-tag">✓ saved</span> — no questions auto-fetchable from this ATS; use ↑ Screening Qs to paste them.`;
    openReview(currentJob.id);     // refresh the meta line / JD link beneath the modal
    msg.innerHTML = note;          // modal stays open with the result
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

// ---- review --------------------------------------------------------------
async function openReview(id) {
  const { job, cv_options } = await api(`/api/jobs/${id}`);
  currentJob = job;
  currentJob._cvOptions = cv_options || [];
  currentJob._cvUsed = (job.draft && job.draft.cv_used) || null;
  const title = `${job.role} — ${job.company}`;
  document.getElementById("rwTitle").textContent = title;
  document.getElementById("rwH").textContent = title;
  const sCls = job.score >= 80 ? "s-hi" : job.score >= 70 ? "s-mid" : "s-lo";
  const jdLink = job.url
    ? `<a href="${esc(job.url)}" target="_blank">Open JD ↗</a>`
    : "pasted JD";
  const chips = [
    `<span class="rw-chip score ${sCls}" title="AI fit score">${job.score} fit</span>`,
    `<span class="rw-chip">${modeLbl[job.mode] || job.mode}</span>`,
  ];
  if (job.location) chips.push(`<span class="rw-chip">${esc(job.location)}</span>`);
  if (job.salary) chips.push(`<span class="rw-chip" style="color:#166534;font-weight:600">${esc(job.salary)}</span>`);
  chips.push(`<span class="rw-chip">${jdLink} <a style="cursor:pointer;color:var(--muted)" title="Edit the JD/application URL — point it at the apply page to fetch the real screening questions" onclick="editJobUrl()">✎</a></span>`);
  document.getElementById("rwMeta").innerHTML = chips.join("") + `<span id="liveBadge"></span>`;
  document.getElementById("rwActions").innerHTML = `
    <button class="btn ghost" id="bmBtn" onclick="toggleBookmarkReview()">${job.bookmarked ? "★ Bookmarked" : "☆ Bookmark"}</button>
    <button class="btn ghost" onclick="findPeople()" title="Shortlist people to contact on LinkedIn at this company">🔗 People</button>
    <span class="splitbtn" id="skipSplit">
      <button class="btn danger main" onclick="skipPlain()">Skip</button>
      <button class="btn danger caret" onclick="toggleSkipMenu(event)" title="More skip options">▾</button>
      <div class="menu">
        <div class="sub">Plain Skip changes no rankings. “Train” teaches the scorer from this role — fewer or more like it.</div>
        <button onclick="skipTrain()">Skip + Train…</button>
      </div>
    </span>
    <button class="btn ok" onclick="setStatus('applied')">Approve &amp; mark applied</button>`;
  let warns = "";
  if (job.language_block)
    warns += `<div class="banner red"><span>🚫 <strong>Language gate:</strong> ${esc(job.language_note)}. Score capped — you'd likely be filtered out. Skip unless the requirement is flexible.</span></div>`;
  if (job.role_off_target)
    warns += `<div class="banner"><span>⚠️ <strong>Off-target role:</strong> ${esc(job.role_note)}. Not one of your target PM roles — skip unless it's actually relevant.</span></div>`;
  document.getElementById("langWarn").innerHTML = warns;
  document.getElementById("whyline").innerHTML =
    `<strong>Why ${job.score}:</strong> ${esc(job.reason)}.`;
  renderDraftArea(job);
  loadAnalysis(job);
  loadLiveness(job);
  renderJd(job.jd && job.jd.requirements ? job.jd : "button");
  go("review");
}

// ---- liveness: is the posting still up at its source? --------------------
function renderLiveBadge(job) {
  const el = document.getElementById("liveBadge");
  if (!el || !currentJob || currentJob.id !== job.id) return;
  const recheck = `onclick="recheckLive()" style="cursor:pointer" title="Click to re-check now${job.live_checked_at ? " (last checked " + job.live_checked_at + ")" : ""}"`;
  if (job.live === "expired")
    el.innerHTML = `<span class="role-off" ${recheck}>⚠ EXPIRED</span> <span class="muted">${esc(job.live_note || "")}</span>`;
  else if (job.live === "live")
    el.innerHTML = `<span class="muted" ${recheck} style="color:#166534;cursor:pointer">● live</span>`;
  else if (job.live === "error")
    el.innerHTML = `<span class="muted" ${recheck}>couldn’t verify — ${esc(job.live_note || "")}</span>`;
  else
    el.innerHTML = "";
  // surface an expired posting as a prominent banner too
  if (job.live === "expired") {
    const w = document.getElementById("langWarn");
    if (w && !w.querySelector(".live-expired"))
      w.insertAdjacentHTML("beforeend",
        `<div class="banner red live-expired"><span>⚠️ <strong>Posting may be gone:</strong> ${esc(job.live_note || "")}. Verify at the source before drafting.</span></div>`);
  }
}
async function loadLiveness(job) {
  if (job.live) { renderLiveBadge(job); return; }   // use cached; recheck on demand
  const el = document.getElementById("liveBadge");
  if (el) el.innerHTML = '<span class="muted">checking if live…</span>';
  try {
    const res = await api(`/api/jobs/${job.id}/liveness`, { method: "POST" });
    if (currentJob && currentJob.id === job.id) {
      Object.assign(currentJob, { live: res.live, live_note: res.note, live_checked_at: res.checked_at, archived: res.archived });
      renderLiveBadge(currentJob);
      if (res.archived) loadJobs();    // expired → moved to Archived; refresh the list
    }
  } catch (e) {
    if (el) el.innerHTML = `<span class="muted">liveness check failed</span>`;
  }
}
async function recheckLive() {
  if (!currentJob) return;
  currentJob.live = "";                              // force a fresh check
  loadLiveness(currentJob);
}

// ---- fetched JD with requirement highlighting ----------------------------
function highlightJd(text, reqs) {
  let out = esc(text);
  (reqs || []).forEach(r => {
    const q = r.quote && r.quote.trim();
    if (!q) return;
    const eq = esc(q);
    const idx = out.toLowerCase().indexOf(eq.toLowerCase());
    if (idx >= 0) {
      out = out.slice(0, idx) + `<mark class="req-${r.level}" title="${esc(r.note || "")}">`
        + out.slice(idx, idx + eq.length) + `</mark>` + out.slice(idx + eq.length);
    }
  });
  return out.replace(/\n/g, "<br>");
}
function renderJd(jd) {
  const el = document.getElementById("jdSection");
  if (jd === "button") {
    el.innerHTML = `<div class="panel p" style="margin-bottom:14px"><button class="btn ghost" onclick="loadJd()">⬇ Assess JD requirements (match / partial / gap)</button></div>`;
    return;
  }
  if (jd === null) {
    el.innerHTML = '<div class="panel p" style="margin-bottom:14px"><span class="spin"></span>Reading the JD &amp; matching requirements…</div>';
    return;
  }
  const by = { match: [], stretch: [], mismatch: [] };
  (jd.requirements || []).forEach(r => (by[r.level] || by.stretch).push(r));
  const warn = jd.error ? `<div class="hint" style="color:var(--amber);margin-bottom:6px">⚠️ ${esc(jd.error)}</div>` : "";
  const group = (title, items) => items.length ? `<div class="anh" style="margin-top:12px">${title} (${items.length})</div>
    <ul style="margin:6px 0 0;padding-left:0;list-style:none">${items.map(r =>
      `<li style="margin:5px 0;font-size:12.5px;line-height:1.5"><mark class="req-${r.level}">${esc(r.quote)}</mark>${r.note ? ` <span class="muted">— ${esc(r.note)}</span>` : ""}</li>`).join("")}</ul>` : "";
  const body = (jd.requirements || []).length
    ? group("✅ You match", by.match) + group("🟡 Partial / a stretch", by.stretch) + group("🔴 Gap / not met", by.mismatch)
    : '<div class="muted" style="margin-top:6px">No specific requirements could be extracted from the available JD text.</div>';
  el.innerHTML = `<div class="panel p" style="margin-bottom:14px">
    <div class="anh" style="display:flex;align-items:center;gap:8px">JD requirements vs your fit <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">— pulled from the job description</span>
      <span style="flex:1"></span>
      <button class="btn ghost sm" onclick="loadJd()" title="Re-score the JD requirements against your CV + strengths (e.g. after adding a strength)">↻ Re-score</button></div>
    ${warn}${body}
  </div>`;
}
// Re-render the JD panel in its current state (so the unmet block appears once
// the async analysis lands). No-op while the JD itself is mid-fetch.
function refreshJdPanel() {
  if (jdBusy) return;
  renderJd(currentJob && currentJob.jd && currentJob.jd.requirements ? currentJob.jd : "button");
}
let jdBusy = false;
async function loadJd() {
  jdBusy = true;
  renderJd(null);
  try {
    const { jd } = await api(`/api/jobs/${currentJob.id}/jd`, { method: "POST" });
    if (currentJob) { currentJob.jd = jd; jdBusy = false; renderJd(jd); }
  } catch (e) {
    document.getElementById("jdSection").innerHTML =
      `<div class="hint" style="color:var(--red);margin-bottom:14px">JD fetch failed: ${esc(e.message)} <button class="btn sm" onclick="loadJd()">Retry</button></div>`;
  } finally {
    jdBusy = false;
  }
}

// ---- deep fit analysis (best-fit / shortcomings / skills) ----------------
function renderAnalysis(a) {
  const el = document.getElementById("fitAnalysis");
  if (!a) {
    el.innerHTML = '<div class="panel p" style="margin-bottom:14px"><span class="spin"></span>Analyzing fit…</div>';
    return;
  }
  if (a.error) {
    el.innerHTML = `<div class="hint" style="color:var(--amber);margin-bottom:12px">⚠️ ${esc(a.error)}</div>`;
    return;
  }
  // upgrade the short "Why N" headline to the full, uncapped score rationale
  if (a.score_rationale && currentJob) {
    document.getElementById("whyline").innerHTML =
      `<strong>Why ${currentJob.score}:</strong> ${esc(a.score_rationale)}`;
  }
  const matched = new Set((a.skills_matched || []).map(s => s.toLowerCase()));
  const chips = (a.skills_all || []).map(s => {
    const on = matched.has(s.toLowerCase());
    return `<span class="skill ${on ? "on" : ""}">${on ? "✓ " : ""}${esc(s)}</span>`;
  }).join(" ");
  const bd = (a.breakdown || []).map(d => {
    const col = d.score >= 70 ? "#16a34a" : d.score >= 45 ? "#d97706" : "#dc2626";
    return `<div style="display:flex;align-items:center;gap:8px;margin-top:5px;font-size:12px">
      <div style="width:118px">${esc(d.label)}</div>
      <div style="flex:1;background:#eef0f3;border-radius:6px;height:8px"><div style="width:${d.score}%;height:8px;background:${col};border-radius:6px"></div></div>
      <div style="width:28px;text-align:right;font-weight:700">${d.score}</div>
      <div class="muted" style="flex:1.4;font-size:11px">${esc(d.note)}</div></div>`;
  }).join("");
  el.innerHTML = `<div class="panel p" style="margin-bottom:14px">
    <div class="anh">Fit breakdown <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">— your true standing per dimension, judged from your CV</span></div>
    <div style="margin-top:6px">${bd || '<span class="muted">—</span>'}</div>
    <div class="anh" style="margin-top:14px">Where it fits</div><p class="anp">${esc(a.best_fit)}</p>
    <div class="anh" style="margin-top:12px">Shortcomings</div><p class="anp">${esc(a.shortcomings)}</p>
    <div class="anh" style="margin-top:14px">Skills considered <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">— ✓ = this role values it (${matched.size}/${(a.skills_all || []).length})</span></div>
    <div class="pill-row" style="margin-top:6px">${chips}</div>
  </div>`;
}
async function loadAnalysis(job) {
  if (job.analysis && (job.analysis.best_fit || job.analysis.error)) {
    renderAnalysis(job.analysis);
    return;
  }
  renderAnalysis(null);
  try {
    const { analysis } = await api(`/api/jobs/${job.id}/analysis`, { method: "POST" });
    if (currentJob && currentJob.id === job.id) {
      currentJob.analysis = analysis;
      renderAnalysis(analysis);
      refreshJdPanel();          // surface "Unmet qualifications" in the JD panel
    }
  } catch (e) {
    document.getElementById("fitAnalysis").innerHTML =
      `<div class="hint" style="color:var(--red);margin-bottom:12px">Analysis failed: ${esc(e.message)} <button class="btn sm" onclick="loadAnalysis(currentJob)">Retry</button></div>`;
  }
}

// Aggregator/job-board hosts whose links are NOT the real application — questions
// can't be fetched from these; the user must point the URL at the actual apply page.
const AGGREGATORS = { "adzuna": "Adzuna", "indeed": "Indeed", "linkedin": "LinkedIn",
  "glassdoor": "Glassdoor", "ziprecruiter": "ZipRecruiter", "totaljobs": "Totaljobs",
  "reed.co.uk": "Reed", "cv-library": "CV-Library", "monster": "Monster",
  "talent.com": "Talent.com", "jooble": "Jooble", "jobserve": "JobServe",
  "welcometothejungle": "Welcome to the Jungle", "otta": "Otta" };
function aggregatorName(url) {
  let h = ""; try { h = new URL(url).hostname.replace("www.", ""); } catch (e) { return ""; }
  for (const k in AGGREGATORS) if (h.includes(k)) return AGGREGATORS[k];
  return "";
}
function aggWarn(job) {
  const a = aggregatorName(job.url || "");
  if (!a) return "";
  return `<div class="hint" data-doc="d-sq" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
    <span class="muted">${a} aggregator link — screening answers below are inferred.</span>
    <button class="btn sm" type="button" onclick="resolveApply(this)">🔎 Find the real application &amp; fetch its questions</button>
    <span id="resolveMsg" class="hint"></span></div>`;
}
async function resolveApply(btn) {
  if (!currentJob) return;
  const msg = document.getElementById("resolveMsg");
  if (msg) msg.innerHTML = '<span class="spin"></span>following the link & checking for questions…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/resolve-apply`, { method: "POST" });
    currentJob.url = r.url;
    if (r.screening_html) {
      currentJob.draft.screening_html = r.screening_html;
      renderDraftArea(currentJob);
      dtab(null, "d-sq");
      alert(`Found the real listing and fetched ${r.count} live question(s) — the Screening tab now answers the actual application.\n\n${r.url}`);
    } else {
      renderDraftArea(currentJob);
      alert(`Resolved to the real listing:\n${r.url}\n\n` + (r.count
        ? `Found ${r.count} question(s).`
        : "No questions were auto-fetchable there (the apply form may be JS-only or an unsupported ATS). The shown answers stay inferred — you can paste the real ones via ↑ Screening Qs."));
    }
  } catch (e) {
    if (msg) msg.textContent = "✗ " + e.message;
  }
}

// Optional cover-letter inputs. The letter never invents a trigger, contact, or
// product fact — anything you leave blank shows as a [ placeholder ] to fill in.
function ctxFormHtml(job) {
  const c = job._draftCtx || {};
  const op = job._opener || "auto";
  const o = v => op === v ? "selected" : "";
  return `<details class="clctx" ${(c.why_excited||c.gap||c.cultural_fit||c.emphasis) ? "open" : ""} style="margin:10px 0;text-align:left">
    <summary class="hint">About this application <span class="muted">— optional; fills the letter's slots so it doesn't leave blanks</span></summary>
    <div style="display:grid;gap:6px;margin-top:8px">
      <div style="display:flex;align-items:center;gap:8px"><button class="btn ghost sm" type="button" onclick="researchPrefill(this)" title="Draft these notes from the JD + what's known about the company, for you to review and edit">✨ Research &amp; pre-fill</button><span id="rcMsg" class="hint"></span></div>
      <label class="hint">Opener
        <select id="dcOpener" style="border:1px solid var(--line);border-radius:6px;padding:3px 6px;margin-left:4px">
          <option value="auto" ${o("auto")}>Auto (by job flags)</option>
          <option value="standard" ${o("standard")}>Standard</option>
          <option value="cheeky" ${o("cheeky")}>Cheeky — "my app ranked you near the top"</option>
        </select></label>
      <textarea class="ta" id="dcWhy" placeholder="Why you're excited / recent trigger (e.g. their Series B; a mutual contact; the product)" style="height:48px">${esc(c.why_excited||"")}</textarea>
      <textarea class="ta" id="dcGap" placeholder="The honest gap to name (the JD requirement you lack)" style="height:40px">${esc(c.gap||"")}</textarea>
      <textarea class="ta" id="dcFit" placeholder="One true cultural / working-style fit point" style="height:40px">${esc(c.cultural_fit||"")}</textarea>
      <label class="hint" style="margin-top:2px">Cover-letter emphasis <span class="muted">— JD themes to accentuate &amp; link harder to your experience; rebuild the letter with this in mind</span></label>
      <textarea class="ta" id="dcEmph" placeholder="e.g. lean into their enterprise voice-AI roadmap and the 0→1 founding mandate; tie my 0→1 launch and scale story to it" style="height:52px">${esc(c.emphasis||"")}</textarea>
    </div>
  </details>`;
}

function renderDraftArea(job) {
  const area = document.getElementById("draftArea");
  if (!job.draft) {
    area.innerHTML =
      `<div class="panel p" style="text-align:center">
        <p class="muted">No drafts yet for this job.</p>
        ${ctxFormHtml(job)}
        <button class="btn" id="genBtn" onclick="generateDraft()">Generate tailored CV, cover letter &amp; screening</button>
      </div>`;
    return;
  }
  const d = job.draft;
  const errBar = d.error
    ? `<div class="hint" style="color:var(--amber);margin-bottom:8px">⚠️ ${esc(d.error)}</div>` : "";
  const used = job._cvUsed || { name: "Matching CV" };
  const opts = job._cvOptions || [];
  const switcher = opts.length
    ? `<label class="hint">tailored from
        <select onchange="generateDraft(this.value)" style="border:1px solid var(--line);border-radius:6px;padding:3px 6px;margin-left:4px">
          ${opts.map(o => `<option value="${o.id}" ${o.id === used.id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}
        </select></label>`
    : `<span class="hint">tailored from <strong>${esc(used.name)}</strong></span>`;
  area.innerHTML = `
    ${errBar}
    <div class="doctabs">
      <button class="dtab on" onclick="dtab(event,'d-cv')">CV</button>
      <button class="dtab" onclick="dtab(event,'d-cl')">Cover letter</button>
      <button class="dtab" onclick="dtab(event,'d-sq')">Screening</button>
      <span style="flex:1"></span>
      ${switcher}
      <button class="btn ghost sm" onclick="generateDraft(${used.id ? `'${used.id}'` : "''"})" title="Re-draft all three documents from scratch">↻ Regenerate</button>
    </div>
    <div class="docactions">
      <button class="btn ghost sm" id="editToggle" onclick="toggleEdit()">✎ Edit</button>
      <button class="btn ghost sm" onclick="acceptAllEdits()" title="Accept all remaining AI changes in the current document (keeps the AI version; leaves orange placeholders for you)">✓ Accept all edits</button>
      <button class="btn ghost sm" data-doc="d-cl" onclick="openPasteCL()" title="Paste your revised cover letter; the app infers why each change was made and learns from it">↑ Paste revised CL</button>
      <button class="btn ghost sm" data-doc="d-sq" onclick="openScreeningQs()" title="Paste the application's screening questions (any ATS) to generate answers">↑ Screening Qs</button>
      <button class="btn ghost sm" data-doc="d-sq" onclick="refreshScreeningQs()" title="Re-fetch the live questions from the job URL and re-answer them (keeps your CV/CL)">↻ Refresh Qs</button>
    </div>
    <details class="dlbar">
      <summary>⬇ Download documents <span class="muted" style="font-weight:400">— attach the <strong>Docx</strong> to ATS uploads; MD is for editing / pasting into text boxes</span></summary>
      <div class="dlgrid">
        <span class="dlhdr"></span><span class="dlhdr">ATS upload</span><span class="dlhdr">print</span><span class="dlhdr">text</span>
        <span class="dlname">CV</span><button class="btn sm" onclick="exportDocx('d-cv')">Docx</button><button class="btn sm" onclick="exportPdf('d-cv')">PDF</button><button class="btn sm" onclick="exportMd('d-cv')">MD</button>
        <span class="dlname">Cover letter</span><button class="btn sm" onclick="exportDocx('d-cl')">Docx</button><button class="btn sm" onclick="exportPdf('d-cl')">PDF</button><button class="btn sm" onclick="exportMd('d-cl')">MD</button>
        <span class="dlname">Screening answers</span><button class="btn sm" onclick="exportDocx('d-sq')">Docx</button><button class="btn sm" onclick="exportPdf('d-sq')">PDF</button><button class="btn sm" onclick="exportMd('d-sq')">MD</button>
        <span class="dlname" style="border-top:1px solid var(--line);padding-top:8px">Learnings summary</span>
        <button class="btn sm" style="border-top:1px solid var(--line);padding-top:8px" onclick="exportLearnings('docx')">Docx</button>
        <button class="btn sm" style="border-top:1px solid var(--line);padding-top:8px" onclick="exportLearnings('pdf')">PDF</button>
        <button class="btn sm" style="border-top:1px solid var(--line);padding-top:8px" onclick="exportLearnings('md')">MD</button>
      </div>
    </details>
    ${ctxFormHtml(job)}
    <div id="editTools" class="hide" style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:8px 10px;background:#eef6ff;border:1px solid #cfe3ff;border-radius:8px;font-size:12.5px">
      <strong>Editing</strong> — click any text, including section titles.
      <button class="btn sm" onclick="insertSection()">＋ Section</button>
      <button class="btn sm" onclick="insertBullet()">＋ Bullet</button>
      <button class="btn sm" onclick="insertPageBreak()" title="Force a new page here in the PDF/DOCX export">＋ Page break</button>
      <span class="muted">Select &amp; press Delete to remove. </span>
      <span style="flex:1"></span>
      <span id="editSaved" class="hint"></span>
    </div>
    ${aggWarn(job)}
    <div class="hint" id="previewHint" style="margin-bottom:10px">Full preview. <mark class="chg" onclick="return false">Yellow</mark> = changed from base; <mark class="chg gap" onclick="return false">orange</mark> = needs your input. Click a highlight to compare versions, or <strong>click any line</strong> (bullet, heading, section title) to rewrite it with a reason — the app learns your style for future drafts. Or hit <strong>✎ Edit</strong> for free-form editing. Cover-letter opener: <strong>${esc(job._opener && job._opener !== "auto" ? job._opener : (job._openerUsed || "auto"))}</strong>. ${job._questionsFetched ? `Screening answers <strong>${job._questionsFetched}</strong> real question(s) fetched from the application.` : `Screening uses likely questions (none fetchable from this posting).`}</div>
    <div id="d-cv" class="doc">${d.cv_html}</div>
    <div id="d-cl" class="doc hide">${d.cl_html}</div>
    <div id="d-sq" class="doc hide">${d.screening_html}</div>`;
  editMode = false;
  decorateScreening();
  syncDocActions("d-cv");
}

// Draft the "About this application" notes from the JD/company (user reviews & edits).
async function researchPrefill(btn) {
  if (!currentJob) return;
  const msg = document.getElementById("rcMsg");
  if (msg) msg.innerHTML = '<span class="spin"></span>researching the role…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/research-context`, { method: "POST" });
    if (r.error) { if (msg) msg.textContent = "✗ " + r.error; return; }
    const set = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
    set("dcWhy", r.why_excited); set("dcFit", r.cultural_fit);
    set("dcEmph", r.emphasis); set("dcGap", r.gap);
    if (msg) msg.textContent = "✓ pre-filled — review & edit, then ↻ Regenerate";
  } catch (e) { if (msg) msg.textContent = "✗ " + e.message; }
}

// Per-question copy button on screening answers (UI only — stripped from exports & saves).
const SQ_COPY_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
function decorateScreening() {
  document.querySelectorAll("#d-sq .sq").forEach(sq => {
    const q = sq.querySelector(".sq-q");
    if (!q || q.querySelector(".sq-copy")) return;
    const b = document.createElement("button");
    b.type = "button"; b.className = "sq-copy"; b.title = "Copy answer";
    b.setAttribute("onclick", "copyScreeningAnswer(this)");
    b.innerHTML = SQ_COPY_SVG;
    q.appendChild(b);
  });
}
function copyScreeningAnswer(btn) {
  const sq = btn.closest(".sq"), a = sq && sq.querySelector(".sq-a");
  if (!a) return;
  navigator.clipboard.writeText(a.innerText.trim()).then(() => {
    const old = btn.innerHTML;
    btn.classList.add("copied"); btn.innerHTML = "✓";
    setTimeout(() => { btn.innerHTML = old; btn.classList.remove("copied"); }, 1200);
  }).catch(() => {});
}

// ---- inline edit mode (titles, sections, free text; persisted) -----------
let editMode = false;
function docEls() { return ["d-cv", "d-cl", "d-sq"].map(id => document.getElementById(id)).filter(Boolean); }
function activeDoc() { return docEls().find(el => !el.classList.contains("hide")); }

function acceptAllEdits() {
  const doc = activeDoc();
  if (!doc) return;
  const marks = [...doc.querySelectorAll("mark.chg:not(.gap)")];   // leave orange placeholders
  if (!marks.length) { return; }
  marks.forEach(m => { m.classList.remove("chg"); m.classList.add("done"); m.title = "accepted — click to change"; });
  saveDraftEdits();
}
async function toggleEdit() {
  editMode = !editMode;
  const btn = document.getElementById("editToggle");
  const tools = document.getElementById("editTools");
  const hint = document.getElementById("previewHint");
  docEls().forEach(el => { el.contentEditable = editMode ? "true" : "false"; el.classList.toggle("editing", editMode); });
  if (editMode) docEls().forEach(el => el.querySelectorAll(".sq-q").forEach(q => q.contentEditable = "false"));  // questions stay read-only
  tools.classList.toggle("hide", !editMode);
  hint.classList.toggle("hide", editMode);
  if (editMode) {
    btn.textContent = "✓ Done";
    btn.classList.add("ok");
    const a = activeDoc(); if (a) a.focus();
  } else {
    btn.textContent = "✎ Edit";
    btn.classList.remove("ok");
    await saveDraftEdits();
  }
}

async function saveDraftEdits() {
  if (!currentJob || !currentJob.draft) return;
  const cv = document.getElementById("d-cv"), cl = document.getElementById("d-cl"), sq = document.getElementById("d-sq");
  const sqc = sq.cloneNode(true); sqc.querySelectorAll(".sq-copy").forEach(b => b.remove());
  const payload = { cv_html: cv.innerHTML, cl_html: cl.innerHTML, screening_html: sqc.innerHTML };
  Object.assign(currentJob.draft, payload);
  const saved = document.getElementById("editSaved");
  try {
    await api(`/api/jobs/${currentJob.id}/draft-edit`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (saved) saved.innerHTML = '<span class="ok-tag">✓ saved</span>';
  } catch (e) { if (saved) saved.textContent = "✗ couldn't save: " + e.message; }
}

function insertAtCaret(node) {
  const doc = activeDoc(); if (!doc) return;
  const sel = window.getSelection();
  let range = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
  if (!range || !doc.contains(range.commonAncestorContainer)) {
    doc.appendChild(node);
  } else {
    range.collapse(false);
    range.insertNode(node);
  }
  // place caret inside the new node and scroll to it
  const r = document.createRange(); r.selectNodeContents(node); r.collapse(false);
  sel.removeAllRanges(); sel.addRange(r);
  node.scrollIntoView({ block: "center" });
}
function insertSection() {
  if (!editMode) return;
  const h = document.createElement("div"); h.className = "role-h"; h.textContent = "New section";
  const ul = document.createElement("ul"); const li = document.createElement("li"); li.textContent = "New item"; ul.appendChild(li);
  const frag = document.createDocumentFragment(); frag.appendChild(h); frag.appendChild(ul);
  insertAtCaret(frag);
}
function insertPageBreak() {
  if (!editMode) return;
  const hr = document.createElement("hr"); hr.className = "pagebreak";
  insertAtCaret(hr);
}
function insertBullet() {
  if (!editMode) return;
  const li = document.createElement("li"); li.textContent = "New item";
  const sel = window.getSelection();
  const inLi = sel && sel.rangeCount ? sel.getRangeAt(0).commonAncestorContainer : null;
  const liParent = inLi && (inLi.nodeType === 1 ? inLi : inLi.parentElement)?.closest("li");
  if (liParent && liParent.parentElement) { liParent.parentElement.insertBefore(li, liParent.nextSibling); }
  else { const ul = document.createElement("ul"); ul.appendChild(li); insertAtCaret(ul); return; }
  const r = document.createRange(); r.selectNodeContents(li); r.collapse(false);
  sel.removeAllRanges(); sel.addRange(r);
}

// Label/filename for a given draft doc id.
function docMeta(id) {
  return { id, label: id === "d-cl" ? "Cover letter" : id === "d-sq" ? "Screening" : "CV" };
}
// Which draft tab is showing, + a label/filename for it.
function activeDocInfo() { return docMeta((activeDoc() || {}).id || "d-cv"); }
// Export filename: <DocType>_<Name>_<Role>  e.g. CV_Alex_Rivera_Product_Manager
function _slug(s) { return (s || "").trim().replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, ""); }
function candidateName() {
  const h = document.querySelector("#d-cv h3");
  return _slug(h ? h.textContent : "") || "Candidate";
}
function exportName(label) {
  const type = label === "Cover letter" ? "CoverLetter" : label === "Screening" ? "Screening" : "CV";
  const role = _slug(currentJob && currentJob.role) || "Role";
  return `${type}_${candidateName()}_${role}`;
}
// inner HTML of a doc with the review-marks stripped (keeps user edits)
function cleanDocHtml(id) { return cleanClone(id); }
// Pull the candidate's name + contact line from the CV, to head the cover letter.
function letterheadHtml() {
  const cv = document.getElementById("d-cv");
  if (!cv) return "";
  const h = cv.querySelector("h3");
  const name = h ? h.textContent.trim() : "";
  let contact = "";
  if (h) {
    let n = h.nextElementSibling;
    while (n && n.tagName !== "P") n = n.nextElementSibling;   // first paragraph = contacts
    if (n) contact = n.textContent.trim();
  }
  if (!name && !contact) return "";
  return `<h3>${esc(name)}</h3>${contact ? `<p>${esc(contact)}</p>` : ""}<hr class="lh-rule">`;
}
// Export HTML for a doc: the cover letter gets a name+contacts letterhead on top.
function docExportHtml(id) {
  const inner = cleanDocHtml(id);
  return id === "d-cl" ? letterheadHtml() + inner : inner;
}
// turn email / URL / LinkedIn text into clickable links (for the PDF/print view)
const LINK_RE = /([\w.+-]+@[\w-]+\.[\w.-]+)|(https?:\/\/[^\s)<>]+)|((?:www\.|linkedin\.com\/)[^\s)<>]+)/gi;
function linkify(html) {
  const tmp = document.createElement("div"); tmp.innerHTML = html;
  (function walk(node) {
    [...node.childNodes].forEach(c => {
      if (c.nodeType === 3) {
        if (LINK_RE.test(c.nodeValue)) {
          LINK_RE.lastIndex = 0;
          const span = document.createElement("span");
          span.innerHTML = c.nodeValue.replace(LINK_RE, (m, email, url, www) =>
            email ? `<a href="mailto:${email}">${email}</a>`
              : url ? `<a href="${url}">${url}</a>`
                : `<a href="https://${www}">${www}</a>`);
          c.replaceWith(span);
        }
      } else if (c.nodeType === 1 && c.tagName !== "A") walk(c);
    });
  })(tmp);
  return tmp.innerHTML;
}

async function exportDocx(docId) {
  if (!currentJob || !currentJob.draft) { alert("Generate a draft first."); return; }
  const { id, label } = docId ? docMeta(docId) : activeDocInfo();
  try {
    const res = await fetch(`/api/jobs/${currentJob.id}/export.docx`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html: docExportHtml(id), label }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = exportName(label) + ".docx";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { alert("DOCX export failed: " + e.message); }
}

// Refined print stylesheet shared by CV + cover-letter PDF export.
const PRINT_CSS = `
  *{box-sizing:border-box}
  html,body{margin:0}
  body{font:10.8pt/1.5 'Georgia','Iowan Old Style',Cambria,serif;color:#2b2f36;
       max-width:780px;margin:0 auto;padding:42px 46px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  /* name / letterhead */
  h3{font-family:'Helvetica Neue',Arial,sans-serif;font-size:25pt;font-weight:700;letter-spacing:.4px;
     text-align:center;color:#16202e;margin:0 0 4px}
  h3 + p{text-align:center;color:#5b6472;font-size:9.3pt;letter-spacing:.3px;margin:0 0 16px}
  h3 + p a{color:#2d6cdf}
  /* section / role headers */
  .role-h{font-family:'Helvetica Neue',Arial,sans-serif;font-size:10.2pt;font-weight:700;
          text-transform:uppercase;letter-spacing:1.6px;color:#2d6cdf;
          border-bottom:1.5px solid #2d6cdf;padding-bottom:3px;margin:18px 0 8px}
  p{margin:5px 0}
  strong{color:#16202e}
  a{color:#2d6cdf;text-decoration:none}
  /* custom round bullets */
  ul{margin:5px 0 11px;padding-left:18px;list-style:none}
  li{position:relative;margin:3.5px 0;padding-left:15px}
  li::before{content:"";position:absolute;left:1px;top:.62em;width:5px;height:5px;
             background:#2d6cdf;border-radius:50%}
  mark{background:none;color:inherit}
  hr.lh-rule{border:none;border-top:1px solid #d9dde3;margin:12px 0 22px}
  hr.pagebreak{border:none;height:0;margin:0;break-before:page;page-break-before:always}
  /* cover-letter specifics: smaller name, normal-weight body */
  body.letter{font-size:11pt}
  body.letter h3{font-size:17pt;font-weight:600;letter-spacing:.2px;margin:0 0 2px}
  body.letter p{margin:0 0 11px;font-weight:400}
  body.letter p strong, body.letter p b{font-weight:400}
  /* page-break hygiene */
  .role-h{break-after:avoid;page-break-after:avoid}
  li,p,h3{break-inside:avoid;page-break-inside:avoid}
  @media print{ body{padding:0.55in 0.62in;max-width:none} @page{margin:0} }
`;
function exportPdf(docId) {
  if (!currentJob || !currentJob.draft) { alert("Generate a draft first."); return; }
  const { id, label } = docId ? docMeta(docId) : activeDocInfo();
  const body = linkify(docExportHtml(id));
  const cls = id === "d-cl" ? "letter" : "cvdoc";
  printWindow(exportName(label), cls, body);
}
// Shared print-window opener used by doc PDF + learnings PDF.
function printWindow(title, bodyClass, bodyHtml) {
  const w = window.open("", "_blank");
  w.document.write(`<!doctype html><html><head><meta charset="utf-8">`
    + `<title>${esc(title)}</title>`
    + `<style>${PRINT_CSS}</style></head><body class="${bodyClass}">${bodyHtml}`
    + `<scr` + `ipt>setTimeout(()=>window.print(),400)</scr` + `ipt></body></html>`);
  w.document.close();
}
// Learnings summary (style.md) → Docx (server) or PDF (print server-rendered html).
// HTML -> Markdown for the .md export (good for text boxes / editing, NOT for ATS upload).
function htmlToMd(root) {
  const inline = node => {
    let s = "";
    node.childNodes.forEach(c => {
      if (c.nodeType === 3) s += c.nodeValue;
      else if (!c.tagName) s += "";
      else if (c.tagName === "STRONG" || c.tagName === "B") s += "**" + inline(c).trim() + "**";
      else if (c.tagName === "EM" || c.tagName === "I") s += "*" + inline(c).trim() + "*";
      else if (c.tagName === "A") s += "[" + inline(c) + "](" + (c.getAttribute("href") || "") + ")";
      else if (c.tagName === "BR") s += "\n";
      else s += inline(c);
    });
    return s;
  };
  const out = [];
  [...root.children].forEach(node => {
    const t = node.tagName || "", cls = node.className || "";
    if (t === "H3") out.push("# " + inline(node).trim());
    else if (cls.includes("role-h")) out.push("", "## " + inline(node).trim());
    else if (t === "UL" || t === "OL") [...node.children].forEach(li => out.push("- " + inline(li).trim()));
    else if (t === "HR") { if (!(node.className || "").includes("pagebreak")) out.push("", "---"); }
    else if (t === "DIV" && node.children.length) out.push("", htmlToMd(node));
    else { const x = inline(node).trim(); if (x) out.push("", x); }
  });
  return out.join("\n");
}
function downloadText(text, filename, mime) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: mime || "text/plain" }));
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function exportMd(docId) {
  if (!currentJob || !currentJob.draft) { alert("Generate a draft first."); return; }
  const { id, label } = docId ? docMeta(docId) : activeDocInfo();
  const tmp = document.createElement("div");
  tmp.innerHTML = docExportHtml(id);
  const md = htmlToMd(tmp).replace(/\n{3,}/g, "\n\n").trim() + "\n";
  downloadText(md, exportName(label) + ".md", "text/markdown");
}
async function exportLearnings(fmt) {
  if (fmt === "docx") { window.location.href = "/api/learnings.docx"; return; }
  if (fmt === "md") { window.location.href = "/api/learnings.md"; return; }
  try {
    const html = await (await fetch("/api/learnings.html")).text();
    printWindow("Learned drafting preferences", "cvdoc", "<h3>Learned drafting preferences</h3>" + html);
  } catch (e) { alert("Couldn't load learnings: " + e.message); }
}
async function generateDraft(cvId) {
  const area = document.getElementById("draftArea");
  // capture any cover-letter inputs before we replace the DOM
  const val = id => { const el = document.getElementById(id); return el ? el.value : ""; };
  const opener = val("dcOpener") || currentJob._opener || "auto";
  const ctx = { why_excited: val("dcWhy"), gap: val("dcGap"), cultural_fit: val("dcFit"), emphasis: val("dcEmph") };
  if (document.getElementById("dcWhy")) { currentJob._draftCtx = ctx; currentJob._opener = opener; }
  area.innerHTML = '<div class="panel p"><span class="spin"></span>Drafting with the model… this can take ~10–20s.</div>';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/draft`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cv_id: cvId || "",
        opener: (currentJob._opener && currentJob._opener !== "auto") ? currentJob._opener : "",
        ...(currentJob._draftCtx || {}),
      }),
    });
    currentJob.draft = r.draft;
    currentJob._openerUsed = r.opener_used || "";
    currentJob._questionsFetched = r.questions_fetched || 0;
    currentJob._cvUsed = r.cv_used; currentJob._cvOptions = r.cv_options || [];
    renderDraftArea(currentJob);
  } catch (e) {
    area.innerHTML = `<div class="panel p" style="color:var(--red)">Drafting failed: ${esc(e.message)}
      <div style="margin-top:8px"><button class="btn" onclick="generateDraft()">Retry</button></div></div>`;
  }
}

function dtab(e, id) {
  ["d-cv", "d-cl", "d-sq"].forEach(x => document.getElementById(x).classList.toggle("hide", x !== id));
  const tabs = [...document.querySelectorAll(".doctabs .dtab")];
  tabs.forEach(t => t.classList.remove("on"));
  const idx = { "d-cv": 0, "d-cl": 1, "d-sq": 2 }[id];
  const active = (e && e.target && e.target.classList && e.target.classList.contains("dtab")) ? e.target : tabs[idx];
  if (active) active.classList.add("on");
  syncDocActions(id);
}
// Show only the elements (per-tab action buttons + the aggregator note) relevant to
// the active document (CV / CL / Screening).
function syncDocActions(id) {
  document.querySelectorAll("#draftArea [data-doc]").forEach(b => b.classList.toggle("hide", b.dataset.doc !== id));
}

async function toggleBookmarkReview() {
  if (!currentJob) return;
  const v = !currentJob.bookmarked;
  await api(`/api/jobs/${currentJob.id}/bookmark`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bookmarked: v }),
  });
  currentJob.bookmarked = v;
  document.getElementById("bmBtn").textContent = v ? "★ Bookmarked" : "☆ Bookmark";
}

async function setStatus(status, reason, anchor) {
  if (!currentJob) return;
  if (status === "skipped" && reason === undefined) { openTrainModal(); return; }
  await api(`/api/jobs/${currentJob.id}/status`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, reason: reason || "", anchor: anchor || "" }),
  });
  go("jobs");
  loadJobs();                        // refresh so the job moves to its tab + counts update
}

// Plain Skip = no ranking impact (default). Skip + Train teaches the scorer:
// "fewer like this" -> skips.md (down-rank) or "more like this" -> likes.md (promote).
function skipPlain() { closeSkipMenu(); setStatus("skipped", ""); }
function skipTrain() { closeSkipMenu(); openTrainModal(); }
function toggleSkipMenu(e) { e.stopPropagation(); const s = document.getElementById("skipSplit"); if (s) s.classList.toggle("open"); }
function closeSkipMenu() { const s = document.getElementById("skipSplit"); if (s) s.classList.remove("open"); }
document.addEventListener("click", closeSkipMenu);   // click anywhere closes the skip menu
// Skipping asks why — the reason becomes a negative scoring anchor (skips.md) that
// down-ranks similar roles in future fetches.
const SKIP_REASONS = [
  ["too_tech", "Too technical"],
  ["strict_domain", "Narrow / strict domain"],
  ["culture", "Culture / working style"],
  ["applied_other", "Applied in another role"],
  ["other", "Other…"],
];
const SKIP_LABELS = Object.fromEntries(SKIP_REASONS);

// Best guess at why this role is being passed, to pre-select the dropdown.
function suggestSkipReason() {
  const j = currentJob || {};
  const co = (j.company || "").trim().toLowerCase();
  if (typeof appliedCompanies === "function" && appliedCompanies().has(co)) return "applied_other";
  const blob = ((j.unmet || []).join(" ") + " " + (j.role || "") + " " + (j.role_note || "")).toLowerCase();
  if (/technical|engineer|coding|software dev|hands-on cod|deep tech/.test(blob) || j.role_off_target) return "too_tech";
  const dom = (j.factors || []).find(f => f.key === "domain");
  if (/domain|industry|sector|vertical|category|fintech|healthcare|biotech|gaming/.test(blob)
      || (dom && dom.weight && dom.points / dom.weight < 0.34)) return "strict_domain";
  return "other";
}

// "More like this" reasons (positive anchor → up-rank similar roles).
const LIKE_REASONS = [
  ["ideal_stage", "Ideal company / stage"],
  ["perfect_domain", "Perfect domain"],
  ["great_mission", "Great mission / product"],
  ["right_role", "Right role type"],
  ["other", "Other…"],
];
const LIKE_LABELS = Object.fromEntries(LIKE_REASONS);
let _trainDir = "down";

function openTrainModal() {
  _trainDir = "down";
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <div class="mh"><h3>Skip + Train</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb">
        <div class="rat">Teach the scorer from this role. (Plain <strong>Skip</strong> changes no rankings.)</div>
        <div class="tabs2" style="margin-bottom:10px">
          <button id="train-down" class="on" type="button" onclick="setTrainDir('down')">👎 Fewer like this</button>
          <button id="train-up" type="button" onclick="setTrainDir('up')">👍 More like this</button>
        </div>
        <label class="fieldlab" id="trainReasonLabel"></label>
        <select id="trainReasonCat" onchange="document.getElementById('trainOtherWrap').classList.toggle('hide', this.value !== 'other')"
          style="width:100%;border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:4px 0;font-size:13px"></select>
        <div id="trainOtherWrap" class="hide">
          <input id="trainReasonOther" placeholder="Your reason…" style="width:100%;border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-top:6px;font-size:13px"></div>
      </div>
      <div class="mf"><button class="btn ghost" onclick="closeModal()">Cancel</button>
        <button class="btn danger" onclick="confirmTrain()">Skip + Train</button></div>
    </div></div>`;
  setTrainDir("down");
}
function setTrainDir(dir) {
  _trainDir = dir;
  document.getElementById("train-down").classList.toggle("on", dir === "down");
  document.getElementById("train-up").classList.toggle("on", dir === "up");
  document.getElementById("trainReasonLabel").textContent = dir === "up" ? "What's the draw?" : "Reason it's a poor fit";
  const reasons = dir === "up" ? LIKE_REASONS : SKIP_REASONS;
  const pre = dir === "up" ? "ideal_stage" : suggestSkipReason();
  document.getElementById("trainReasonCat").innerHTML =
    reasons.map(([v, l]) => `<option value="${v}" ${v === pre ? "selected" : ""}>${l}</option>`).join("");
  document.getElementById("trainOtherWrap").classList.toggle("hide", pre !== "other");
}
function confirmTrain() {
  const cat = document.getElementById("trainReasonCat").value;
  const labels = _trainDir === "up" ? LIKE_LABELS : SKIP_LABELS;
  const reason = cat === "other"
    ? (document.getElementById("trainReasonOther").value || "").trim()
    : (labels[cat] || "");
  closeModal();
  setStatus("skipped", reason, _trainDir);   // "up" -> likes.md (promote), else skips.md
}

function copyText(text, btn) {
  navigator.clipboard.writeText(text || "").then(() => {
    if (btn) { const o = btn.textContent; btn.textContent = "✓ Copied"; setTimeout(() => (btn.textContent = o), 1200); }
  }).catch(() => {});
}

// ---- shortlist people to contact on LinkedIn (per company) ---------------
async function findPeople() {
  if (!currentJob) return;
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal wide">
      <div class="mh"><h3>People to contact — ${esc(currentJob.company || "")}</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb" id="peopleBody"><span class="spin"></span> finding targets…</div>
    </div></div>`;
  let d;
  try { d = await api(`/api/jobs/${currentJob.id}/people`, { method: "POST" }); }
  catch (e) { const b = document.getElementById("peopleBody"); if (b) b.innerHTML = `<div class="hint" style="color:var(--red)">${esc(e.message)}</div>`; return; }
  const tgt = (d.targets || []).map(t => {
    const ppl = (t.people || []).length
      ? `<div style="margin:6px 0 0">${t.people.map(p => `<div style="font-size:12.5px;margin:2px 0"><a class="jd" href="${esc(p.url)}" target="_blank">${esc(p.name)}</a>${p.title ? ` — <span class="muted">${esc(p.title)}</span>` : ""}</div>`).join("")}</div>`
      : "";
    return `<div style="border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:8px">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <strong style="font-size:12.5px">${esc(t.label)}</strong><span style="flex:1"></span>
        <a class="btn sm ghost" href="${esc(t.google)}" target="_blank">Google ↗</a>
        <a class="btn sm ghost" href="${esc(t.linkedin)}" target="_blank">LinkedIn ↗</a>
        <a class="btn sm ghost" href="${esc(t.ddg)}" target="_blank">DDG ↗</a>
      </div>${ppl}</div>`;
  }).join("");
  const note = d.note
    ? `<div class="anh" style="margin:12px 0 4px">Connection note <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">— review &amp; personalise before sending</span></div>
       <div class="opt" style="cursor:default"><div class="txt" id="connNote">${esc(d.note)}</div></div>
       <button class="btn sm" onclick="copyText(document.getElementById('connNote').innerText, this)">⧉ Copy note</button>`
    : "";
  const hint = d.search_api ? ""
    : `<div class="hint" style="margin-bottom:10px">Open a search to see real names, roles &amp; recent posts in your logged-in browser. <span class="muted">(Set <code>BRAVE_API_KEY</code> in <code>.env</code> to list names here automatically.)</span></div>`;
  document.getElementById("peopleBody").innerHTML = hint + tgt + note;
}

// ---- compare modal (edit a changed span) ---------------------------------
let curMark = null, curChoice = "custom", curIsLine = false;
document.addEventListener("click", e => {
  if (editMode) return;                       // editing in place — don't hijack clicks
  const doc = e.target.closest(".doc");
  if (!doc) return;
  const m = e.target.closest("mark");
  if (m && (m.classList.contains("chg") || m.classList.contains("done"))
      && m.dataset.base !== undefined && m.onclick === null) {
    openModal(m); return;
  }
  // any other line: rewrite the whole line with a reason — teaches future drafts
  const line = e.target.closest("li, p, .role-h, h1, h2, h3");
  if (line && doc.contains(line) && line.textContent.trim() && !line.classList.contains("sq-q")) openLineModal(line);
});
// ---- word-level diff (base vs AI's version) ------------------------------
function _tokens(s) { return (s || "").match(/\S+|\s+/g) || []; }
function _wordDiff(a, b) {
  const A = _tokens(a), B = _tokens(b), n = A.length, m = B.length;
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const ops = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { ops.push(["=", A[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push(["-", A[i]]); i++; }
    else { ops.push(["+", B[j]]); j++; }
  }
  while (i < n) ops.push(["-", A[i++]]);
  while (j < m) ops.push(["+", B[j++]]);
  return ops;
}
// HTML for one side: side='new' shows = and + (inserts highlighted);
// side='base' shows = and - (deletes struck). Whitespace is never highlighted.
function _diffSide(ops, side) {
  const keep = side === "new" ? "+" : "-", cls = side === "new" ? "ins" : "del";
  return ops.map(([op, t]) => {
    if (op === "=") return esc(t);
    if (op !== keep) return "";
    return /^\s+$/.test(t) ? t : `<span class="${cls}">${esc(t)}</span>`;
  }).join("");
}

let curSuggested = "", curBase = "";
function openModal(m) {
  curMark = m; curChoice = "custom"; curIsLine = false;
  curSuggested = m.textContent;                       // the AI's tailored text
  curBase = m.dataset.base || "";
  const rat = m.dataset.rat || "";
  const ops = _wordDiff(curBase, curSuggested);
  const newHtml = _diffSide(ops, "new");
  const baseHtml = curBase ? _diffSide(ops, "base") : "<em class='muted'>(nothing in base — this is entirely new)</em>";
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal wide">
      <div class="mh"><h3>Review change</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb">
        <div class="rat"><strong>Why changed:</strong> ${esc(rat)}</div>
        <div class="opt sel" data-c="custom" onclick="pick('custom')">
          <div class="lab"><span class="dotsel"></span>Keep AI's version <span class="difflegend"><span class="ins">added/changed</span></span></div><div class="txt">${newHtml}</div></div>
        <div class="opt" data-c="base" onclick="pick('base')">
          <div class="lab"><span class="dotsel"></span>Base version <span class="difflegend"><span class="del">removed</span></span></div><div class="txt">${baseHtml}</div></div>
        <div class="opt" data-c="own" onclick="pick('own')">
          <div class="lab"><span class="dotsel"></span>Your version</div>
          <textarea class="ta" id="ownText" style="height:80px" placeholder="Write your own wording here…" oninput="markOwn()"></textarea></div>
        ${regenRowHtml()}
        <div style="margin-top:10px">
          <div class="lab" style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:4px">Rationale for the change <span style="font-weight:400;text-transform:none">— teaches future drafts</span></div>
          <textarea class="ta" id="changeReason" style="height:56px" placeholder="Why are you changing this? (e.g. 'lead with the founder angle, drop the agency jargon')"></textarea>
        </div>
      </div>
      <div class="mf"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn ok" onclick="saveChange()">Save</button></div>
    </div></div>`;
}
// Edit ANY line (not just AI changes). Rewrite + reason → teaches future drafts.
function openLineModal(el) {
  curMark = el; curIsLine = true; curChoice = "own";
  const cur = el.textContent.trim();
  curBase = cur; curSuggested = cur;          // no separate AI/base for a plain line
  const kind = el.classList.contains("role-h") ? "section heading"
    : el.tagName === "LI" ? "bullet" : el.tagName === "H3" ? "heading" : "line";
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal wide">
      <div class="mh"><h3>Edit ${kind}</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb">
        <div class="rat">Rewrite this ${kind} and say why — the app learns your preference and applies it to future drafts.</div>
        <div class="opt" data-c="keep" onclick="pick('keep')">
          <div class="lab"><span class="dotsel"></span>Current</div><div class="txt">${esc(cur)}</div></div>
        <div class="opt sel" data-c="own" onclick="pick('own')">
          <div class="lab"><span class="dotsel"></span>Your version</div>
          <textarea class="ta" id="ownText" style="height:80px" oninput="markOwn()">${esc(cur)}</textarea></div>
        ${regenRowHtml()}
        <div style="margin-top:10px">
          <div class="lab" style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:4px">Why this change <span style="font-weight:400;text-transform:none">— teaches future drafts</span></div>
          <textarea class="ta" id="changeReason" style="height:56px" placeholder="e.g. 'tighten to one line', 'lead with the metric', 'rename section to Leadership'"></textarea>
        </div>
        <div class="hint" style="margin-top:6px">Tip: clear the box and Save to delete this ${kind}.</div>
      </div>
      <div class="mf"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn ok" onclick="saveChange()">Save</button></div>
    </div></div>`;
}
// Set an edited line's text, preserving a bold "Label:" prefix on list items /
// category lines (so section sub-categories stay bold without an HTML editor).
// Section titles (.role-h) are bold via CSS, so plain text is fine for them.
function applyLineText(el, text) {
  // bold a leading "Label:" prefix (with or without inline content after it)
  const m = text.match(/^(\s*)([^:\n.]{1,35}:)(\s*)([\s\S]*)$/);
  const isCategory = (el.tagName === "LI" || el.tagName === "P") && !el.classList.contains("role-h");
  if (m && isCategory) {
    el.innerHTML = `${m[1]}<strong>${esc(m[2])}</strong>${m[3]}${esc(m[4])}`;
  } else {
    el.textContent = text;
  }
}
function pick(c) { curChoice = c; document.querySelectorAll(".opt").forEach(o => o.classList.toggle("sel", o.dataset.c === c)); }
function markOwn() { if (document.getElementById("ownText").value.trim()) pick("own"); }
// AI rewrite of this passage to a typed prompt — fills "Your version" for review.
function regenRowHtml() {
  return `<div style="margin-top:8px;border-top:1px dashed var(--line);padding-top:8px">
    <div class="lab" style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:4px">✨ Or have the AI rewrite it to a prompt</div>
    <div style="display:flex;gap:6px">
      <input id="regenPrompt" placeholder="e.g. 'tighter, lead with the metric; emphasise voice AI' (Enter to run)" style="flex:1;border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:12.5px" onkeydown="if(event.key==='Enter'){event.preventDefault();regenLine();}">
      <button class="btn sm" onclick="regenLine()">✨ Rewrite</button>
    </div>
    <span id="regenMsg" class="hint"></span></div>`;
}
async function regenLine() {
  const instr = (document.getElementById("regenPrompt").value || "").trim();
  const msg = document.getElementById("regenMsg");
  if (!instr) { msg.textContent = "Type how to rewrite it."; return; }
  const kind = (activeDoc() || {}).id === "d-cl" ? "cl" : "cv";
  msg.innerHTML = '<span class="spin"></span>rewriting…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/rewrite`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: curSuggested, instruction: instr, kind }),
    });
    document.getElementById("ownText").value = r.text;
    pick("own");
    msg.innerHTML = '<span class="ok-tag">✓ rewritten</span> — review/edit above, add a reason, then Save.';
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
async function saveChange() {
  if (!curMark) return;
  let text;
  if (curChoice === "base" || curChoice === "keep") text = curBase;
  else if (curChoice === "own") text = document.getElementById("ownText").value;
  else text = curSuggested;
  const reason = (document.getElementById("changeReason").value || "").trim();
  // teach future drafts when the text actually changes OR a rationale is given
  if (text.trim() !== curSuggested.trim() || reason) {
    try {
      await api(`/api/jobs/${currentJob.id}/override`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base: curBase, suggested: curSuggested, actual: text, reason }),
      });
    } catch (e) { /* non-blocking: still apply the edit locally */ }
  }
  if (text.trim() === "") {
    curMark.parentNode.removeChild(curMark);        // empty = delete the span/line
  } else {
    if (curIsLine) { applyLineText(curMark, text); curMark.classList.add("lineedited"); }
    else { curMark.textContent = text; curMark.classList.remove("chg"); curMark.classList.add("done"); }
    curMark.title = "edited — click to change again";
  }
  closeModal();
  saveDraftEdits();                                  // persist so it survives reload + exports
}
function closeModal() { document.getElementById("modal").innerHTML = ""; curMark = null; curIsLine = false; }

// ---- paste screening questions (any ATS) -> generate answers --------------
function currentScreeningQs() {
  const el = document.getElementById("d-sq");
  if (!el) return "";
  return [...el.querySelectorAll(".sq-q")]
    .map(q => q.innerText.replace(/^\s*\d+\.\s*/, "").trim()).filter(Boolean).join("\n");
}
function openScreeningQs() {
  if (!currentJob || !currentJob.draft) { alert("Generate a draft first."); return; }
  const cur = currentScreeningQs();
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal wide">
      <div class="mh"><h3>Screening questions</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb">
        <div class="rat">Paste the application's questions, <strong>one per line</strong> (works for any ATS — Ashby, Workable, etc., where they can't be auto-fetched). The app answers each in your voice, honoring your learnings.</div>
        <textarea class="ta" id="sqBox" style="height:180px;font-size:13px" placeholder="Tell us about something impressive that you've built&#10;How would you help accelerate the deployment of frontier AI within organizations?&#10;What excites you about Mistral?">${esc(cur)}</textarea>
        <div style="margin-top:8px"><span id="sqMsg" class="hint"></span></div>
      </div>
      <div class="mf"><button class="btn ghost" onclick="closeModal()">Cancel</button>
        <button class="btn ok" onclick="genScreeningQs()">Generate answers</button></div>
    </div></div>`;
}
async function refreshScreeningQs() {
  if (!currentJob || !currentJob.draft) { alert("Generate a draft first."); return; }
  try {
    const r = await api(`/api/jobs/${currentJob.id}/screening/refresh`, { method: "POST" });
    currentJob.draft.screening_html = r.screening_html;
    renderDraftArea(currentJob);
    dtab(null, "d-sq");
    alert(`Refreshed ${r.count} live question(s):\n\n- ` + (r.questions || []).join("\n- "));
    return;
  } catch (e) { /* direct fetch failed — fall through and try the real listing */ }
  try {
    const r = await api(`/api/jobs/${currentJob.id}/resolve-apply`, { method: "POST" });
    currentJob.url = r.url;
    if (r.screening_html) {
      currentJob.draft.screening_html = r.screening_html;
      renderDraftArea(currentJob);
      dtab(null, "d-sq");
      alert(`Followed the link to the real listing and fetched ${r.count} question(s).`);
    } else {
      renderDraftArea(currentJob);
      alert(`Couldn't auto-fetch questions from this posting${r.url ? ` (resolved to ${r.url})` : ""}.\n\nThe apply form may be JavaScript-only or on an unsupported ATS — paste the questions via “↑ Screening Qs” (works for any ATS).`);
    }
  } catch (e2) {
    alert(`Couldn't auto-fetch questions from this posting.\n\nPaste them via “↑ Screening Qs” (works for any ATS).`);
  }
}
async function genScreeningQs() {
  const t = document.getElementById("sqBox").value;
  const msg = document.getElementById("sqMsg");
  if (!t.trim()) { msg.textContent = "Paste at least one question."; return; }
  msg.innerHTML = '<span class="spin"></span>answering…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/screening`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions_text: t }),
    });
    currentJob.draft.screening_html = r.screening_html;
    closeModal();
    renderDraftArea(currentJob);
    dtab(null, "d-sq");
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

// ---- bulk: paste a revised cover letter -> infer reasons -> learn ---------
function curClText() {
  const el = document.getElementById("d-cl");
  if (!el) return "";
  const blocks = [...el.querySelectorAll("p,li,h1,h2,h3,div")].map(b => b.innerText.trim()).filter(Boolean);
  return blocks.length ? blocks.join("\n\n") : el.innerText.trim();
}
function openPasteCL() {
  if (!currentJob || !currentJob.draft) { alert("Generate a draft first."); return; }
  const cur = curClText();
  document.getElementById("modal").innerHTML = `
   <div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal wide">
      <div class="mh"><h3>Paste revised cover letter</h3><button class="x" onclick="closeModal()">×</button></div>
      <div class="mb">
        <div class="rat">Paste your refreshed letter below and hit <strong>Infer reasons</strong>. The app diffs it against the current draft, proposes why each change was made, and learns from it once you save. Edit the text and re-run, or edit any reason directly.</div>
        <textarea class="ta" id="pasteBox" style="height:200px;font-size:13px">${esc(cur)}</textarea>
        <div style="margin:8px 0"><button class="btn sm" onclick="processPasteCL()">⟳ Infer reasons</button> <span id="pasteMsg" class="hint"></span></div>
        <div id="pasteChanges"></div>
      </div>
      <div class="mf"><button class="btn ghost" onclick="closeModal()">Cancel</button>
        <button class="btn ok" id="pasteSave" onclick="savePasteCL()" disabled>Save changes &amp; learn</button></div>
    </div></div>`;
}
async function processPasteCL() {
  const t = document.getElementById("pasteBox").value;
  const msg = document.getElementById("pasteMsg");
  msg.innerHTML = '<span class="spin"></span>diffing & inferring…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/cl-infer`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_text: t }),
    });
    renderClChanges(r.changes || []);
    msg.textContent = (r.changes || []).length ? `${r.changes.length} change(s) found` : "No changes vs the current draft.";
    document.getElementById("pasteSave").disabled = false;
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
function renderClChanges(changes) {
  const box = document.getElementById("pasteChanges");
  if (!changes.length) { box.innerHTML = '<div class="muted" style="font-size:12.5px">Nothing changed — edit the text and re-run, or just Save to keep it.</div>'; return; }
  box.innerHTML = changes.map((c, i) => `
    <div class="clchg" data-i="${i}">
      <div class="clchg-old">${c.base ? esc(c.base) : "<em>(new passage)</em>"}</div>
      <div class="clchg-new">${c.actual ? esc(c.actual) : "<em>(deleted)</em>"}</div>
      <label class="hint" style="display:block;margin-top:4px">Reason (learned for future drafts)</label>
      <textarea class="ta clchg-reason" style="height:46px;font-size:12.5px">${esc(c.reason || "")}</textarea>
    </div>`).join("");
  box._changes = changes;
}
async function savePasteCL() {
  const box = document.getElementById("pasteChanges");
  const cards = [...box.querySelectorAll(".clchg")];
  const src = box._changes || [];
  const changes = cards.map(el => {
    const i = +el.dataset.i;
    return { base: src[i].base, actual: src[i].actual, reason: el.querySelector(".clchg-reason").value.trim() };
  });
  const new_text = document.getElementById("pasteBox").value;
  const msg = document.getElementById("pasteMsg");
  msg.innerHTML = '<span class="spin"></span>saving…';
  try {
    const r = await api(`/api/jobs/${currentJob.id}/cl-save`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_text, changes }),
    });
    currentJob.draft.cl_html = r.cl_html;
    closeModal();
    renderDraftArea(currentJob);
    dtab({ target: document.querySelector('.tabs2 button:nth-child(2)') }, "d-cl");
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

// ---- download as PDF (clean, via print) ----------------------------------
function cleanClone(id) {
  const src = document.getElementById(id).cloneNode(true);
  src.querySelectorAll("mark").forEach(m => {
    const t = document.createTextNode(m.textContent);
    m.parentNode.replaceChild(t, m);
  });
  src.querySelectorAll(".sq-copy").forEach(b => b.remove());   // UI-only copy buttons
  src.classList.remove("hide");
  return src.innerHTML;
}
function downloadDoc(id, title) {
  document.getElementById("printArea").innerHTML = cleanClone(id);
  const old = document.title; document.title = title;
  window.print();
  setTimeout(() => { document.title = old; document.getElementById("printArea").innerHTML = ""; }, 500);
}

// ---- settings ------------------------------------------------------------
let _base = {};
function renderBaseDoc(kind) {
  const up = _base.status && _base.status[kind];
  const file = (_base.files || {})[kind] || "";
  const text = kind === "cv" ? _base.cv : _base.cl;
  const el = document.getElementById(kind + "Status");
  const upload = document.getElementById(kind + "Upload");
  if (up) {
    el.innerHTML = `<span class="ok-tag">✓ ${esc(file || "uploaded")}</span> `
      + `<span class="muted">${(text || "").length.toLocaleString()} chars</span>`
      + `<span class="actions"><a class="jd" onclick="previewBase('${kind}')">view</a>`
      + `<a class="jd" onclick="replaceBase('${kind}')">replace</a>`
      + `<a class="jd" style="color:#b91c1c" onclick="deleteBase('${kind}')">delete</a></span>`;
    if (upload) upload.classList.add("hide");
  } else {
    el.innerHTML = '<span class="muted">· not uploaded</span>';
    if (upload) upload.classList.remove("hide");
  }
}
function togglePasteText(kind) {
  document.getElementById(kind + "Text").classList.toggle("hide");
}
function previewBase(kind) {
  const t = (kind === "cv" ? _base.cv : _base.cl) || "";
  const w = window.open("", "_blank");
  w.document.write(`<title>${esc((_base.files || {})[kind] || kind)}</title>`
    + `<pre style="white-space:pre-wrap;font:14px/1.6 system-ui;padding:28px;max-width:780px;margin:auto">${esc(t)}</pre>`);
}
async function deleteBase(kind) {
  if (!confirm(`Delete the base ${kind === "cv" ? "CV" : "cover letter"}?`)) return;
  await api(`/api/base-docs/${kind}`, { method: "DELETE" });
  loadSettings();
}
function replaceBase(kind) {
  const upload = document.getElementById(kind + "Upload");
  if (upload) upload.classList.remove("hide");
  const inp = document.getElementById(kind + "File");
  if (inp) inp.click();
}

// ---- CV library (application variants + reference PDF) -------------------
async function loadCvLibrary() {
  const d = await api("/api/cvs");
  const box = document.getElementById("appCvs");
  box.innerHTML = (d.applications || []).length
    ? d.applications.map(c => {
        const flags = (c.flags || []).map(f => `<span class="flagchip">${esc((FLAG_CHIP[f] || [f])[0])}</span>`).join("");
        return `<div class="appcv-row">
          <strong>${esc(c.name)}</strong>
          ${flags}
          <span class="muted" style="font-size:12px">${c.chars.toLocaleString()} chars</span>
          <span style="flex:1"></span>
          <a class="jd" style="cursor:pointer;color:#b91c1c" onclick="deleteAppCv('${c.id}','${esc(c.name)}')">remove</a></div>`;
      }).join("")
    : '<div class="muted" style="font-size:12.5px">No application variants yet — the matching CV is used for every draft until you add one.</div>';
  const ref = document.getElementById("refPdfStatus");
  ref.innerHTML = d.reference_pdf
    ? `<span class="ok-tag">✓ ${esc(d.reference_pdf)}</span> <a class="jd" href="/api/cvs/reference" target="_blank" style="margin-left:8px;cursor:pointer">view</a>`
    : '<span class="muted">No reference PDF uploaded.</span>';
}
function toggleAppForm() {
  const f = document.getElementById("appForm"), btn = document.getElementById("appAddBtn");
  const open = f.classList.toggle("hide") === false;
  btn.classList.toggle("hide", open);
  if (!open) {  // reset on close
    document.getElementById("appName").value = "";
    document.getElementById("appFile").value = "";
    document.querySelectorAll("#appForm .flagpick input").forEach(i => i.checked = false);
    document.getElementById("appMsg").textContent = "";
  }
}
async function addAppCv() {
  const name = document.getElementById("appName").value.trim();
  const flags = [...document.querySelectorAll("#appForm .flagpick input:checked")].map(i => i.value);
  const file = document.getElementById("appFile").files[0];
  const msg = document.getElementById("appMsg");
  if (!name) { msg.textContent = "Name the variant."; return; }
  if (!file) { msg.textContent = "Choose a CV file."; return; }
  const fd = new FormData();
  fd.append("name", name); fd.append("flags", flags.join(",")); fd.append("file", file);
  msg.innerHTML = '<span class="spin"></span>saving…';
  try {
    await api("/api/cvs/app", { method: "POST", body: fd });
    toggleAppForm();          // collapse + reset
    loadCvLibrary();
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
async function deleteAppCv(id, name) {
  if (!confirm(`Delete application CV “${name}”?`)) return;
  await api(`/api/cvs/app/${id}`, { method: "DELETE" }); loadCvLibrary();
}
async function uploadReference() {
  const file = document.getElementById("refPdfFile").files[0];
  const msg = document.getElementById("refMsg");
  if (!file) { msg.textContent = "Choose a PDF."; return; }
  const fd = new FormData(); fd.append("file", file);
  msg.innerHTML = '<span class="spin"></span>uploading…';
  try {
    await api("/api/cvs/reference", { method: "POST", body: fd });
    msg.innerHTML = '<span class="ok-tag">✓ saved</span>'; loadCvLibrary();
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
async function loadSettings() {
  _base = await api("/api/base-docs");
  renderBaseDoc("cv"); renderBaseDoc("cl");
  loadCvLibrary();
  const prof = await api("/api/profile");
  document.getElementById("rubricBox").value = prof.fit_rubric || "";
  renderScoring(prof.weights || {}, prof.keywords || {});
  renderFilters(prof.filters || {});
  loadRoleQueries();
  loadStyle();
}
let _stylePreamble = "", _styleEntries = [];
async function loadStyle() {
  const d = await api("/api/style");
  _stylePreamble = d.preamble || ""; _styleEntries = d.entries || [];
  const box = document.getElementById("styleBox");
  const rules = (d.rules || "").trim();
  if (!_styleEntries.length && !rules) {
    box.innerHTML = '<div class="muted" style="font-size:12px">Nothing learned yet — edit a highlighted change in a draft (with a rationale) and it shows here.</div>';
    return;
  }
  const entryCard = i => {
    const lines = (_styleEntries[i] || "").split("\n");
    const head = (lines[0] || "").replace(/^##\s*/, "");
    const body = lines.slice(1).join("\n").trim();
    return `<div style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:8px">
      <div style="display:flex;align-items:center;gap:8px">
        <strong style="font-size:12.5px">${esc(head)}</strong><span style="flex:1"></span>
        <button class="btn sm danger" onclick="deleteStyle(${i})" title="Forget this">✕</button></div>
      <pre style="white-space:pre-wrap;font:11.5px/1.5 ui-monospace,monospace;color:#475569;margin:6px 0 0">${esc(body)}</pre>
    </div>`;
  };
  // 1) the condensed, inferred guidelines & guardrails (what actually conditions drafts)
  const rulesHtml = rules
    ? `<div style="border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:#f8fafc;margin-bottom:12px;white-space:pre-wrap;font-size:12.5px;line-height:1.55">${esc(rules)}</div>`
    : `<div class="muted" style="font-size:12px;margin-bottom:12px">No distilled rules yet — accept a few edits, then ↻ Rebuild.</div>`;
  // 2) the most recent edits that shaped them (newest first)
  const n = _styleEntries.length;
  const recent = [];
  for (let k = n - 1; k >= Math.max(0, n - 3); k--) recent.push(k);
  const recentHtml = recent.length
    ? `<div class="anh" style="margin:4px 0 6px">Recent edits that shaped these <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">— newest first (AI draft → your change → why)</span></div>${recent.map(entryCard).join("")}`
    : "";
  // 3) full list for pruning
  const allHtml = n > recent.length
    ? `<details style="margin-top:4px"><summary class="hint">All captured edits (${n})</summary><div style="margin-top:8px">${_styleEntries.map((_, i) => entryCard(i)).join("")}</div></details>`
    : "";
  box.innerHTML =
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
       <strong style="font-size:12.5px">Inferred guidelines &amp; guardrails</strong>
       <span class="muted" style="font-size:11px">— condensed from your edits; applied to every draft</span>
       <span style="flex:1"></span>
       <button class="btn sm ghost" onclick="rebuildLearnings(this)" title="Re-distil the rules from your latest edits">↻ Rebuild</button></div>
     ${rulesHtml}${recentHtml}${allHtml}
     <div style="margin-top:6px"><button class="btn sm ghost" onclick="deleteStyle('all')">Clear all learned edits</button> <span id="styleMsg" class="hint"></span></div>`;
}
async function rebuildLearnings(btn) {
  if (btn) { btn.textContent = "rebuilding…"; btn.disabled = true; }
  try { await api("/api/learnings/rebuild", { method: "POST" }); } catch (e) {}
  loadStyle();
}
async function deleteStyle(i) {
  if (i === "all" && !confirm("Forget ALL learned preferences?")) return;
  const remaining = i === "all" ? [] : _styleEntries.filter((_, k) => k !== i);
  const text = remaining.length ? (_stylePreamble.trimEnd() + "\n\n" + remaining.join("\n\n") + "\n") : "";
  await api("/api/style", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
  });
  loadStyle();
}
async function saveRubric() {
  const msg = document.getElementById("rubricMsg");
  msg.innerHTML = '<span class="spin"></span>saving & re-scoring…';
  try {
    const d = await api("/api/profile/rubric", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rubric: document.getElementById("rubricBox").value }),
    });
    msg.innerHTML = `<span class="ok-tag">✓ saved · re-scored ${d.rescored}</span>`;
    loadJobs();
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

// ---- editable filters ----------------------------------------------------
// positive allow-list of work modes/regions. US options are OFF by default —
// leaving them unchecked is what drops US-only roles (no negative toggle).
const GEO_OPTS = [["remote", "Remote — anywhere / non-US"], ["remote-eu", "Remote — EU"],
  ["hybrid-uk", "Hybrid — UK"], ["hybrid-eu", "Hybrid — EU"], ["onsite-uk", "On-site — UK"],
  ["remote-us", "Remote — US"], ["remote-americas", "Remote — Americas / LatAm"],
  ["onsite-us", "On-site — US"]];
const US_GEO = ["remote-us", "remote-americas", "onsite-us"];
function renderFilters(f) {
  const geo = f.geo_gate || [];
  const spoken = (f.spoken || []).join(", ");
  const geoBoxes = GEO_OPTS.map(([k, lbl]) =>
    `<label style="display:block;font-size:13px;margin:3px 0"><input type="checkbox" class="geo-opt" value="${k}" ${geo.includes(k) ? "checked" : ""}> ${esc(lbl)}</label>`).join("");
  document.getElementById("filtersBox").innerHTML = `
    <div class="grid" style="grid-template-columns:1fr 1fr;gap:18px;max-width:680px">
      <div>
        <div class="opt-lab" style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:4px">Accepted work modes / regions</div>
        ${geoBoxes}
        <div class="hint" style="margin-top:6px">Only checked modes are kept. US options are off by default — check them to include US roles.</div>
      </div>
      <div>
        <label style="font-size:12px;display:block">Spoken languages <span class="muted">(comma-separated; gates roles needing others)</span><br>
          <input id="spokenLangs" value="${esc(spoken)}" style="width:100%;border:1px solid var(--line);border-radius:8px;padding:7px 10px;margin-top:4px"></label>
        <label style="font-size:12px;display:block;margin-top:10px">Recency window (days)<br>
          <input id="recencyDays" type="number" min="1" max="90" value="${f.recency_days || 7}" style="width:120px;border:1px solid var(--line);border-radius:8px;padding:7px 10px;margin-top:4px"></label>
      </div>
    </div>`;
}
async function saveFilters() {
  const geo = [...document.querySelectorAll(".geo-opt:checked")].map(c => c.value);
  const body = {
    geo_gate: geo,
    // US dropped unless a US option is explicitly checked
    exclude_us_onsite_hybrid: !geo.some(g => US_GEO.includes(g)),
    recency_days: parseInt(document.getElementById("recencyDays").value || "7", 10),
    spoken: document.getElementById("spokenLangs").value.split(",").map(s => s.trim()).filter(Boolean),
  };
  const msg = document.getElementById("filtersMsg");
  try {
    await api("/api/profile/filters", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    msg.innerHTML = '<span class="ok-tag">✓ saved — applies to future scans</span>';
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
function toggleRegistry() {
  const body = document.getElementById("registryBody");
  const open = !body.classList.toggle("hide");
  document.getElementById("regCaret").textContent = open ? "▾" : "▸";
}

// ---- editable board search queries --------------------------------------
let _rqBoards = [];
async function loadRoleQueries() {
  const d = await api("/api/role-queries");
  _rqBoards = d.query_boards || [];
  const ta = (id, label, val, ph) =>
    `<label style="display:block;font-size:12px;font-weight:600;margin-top:10px">${esc(label)}<br>
      <textarea id="${id}" class="ta" style="height:130px;font-weight:400" placeholder="${esc(ph)}">${esc((val || []).join("\n"))}</textarea></label>`;
  let html = ta("rq-default", "Default — all query-based boards", d.default, "one query per line");
  _rqBoards.forEach(qb => {
    html += ta("rq-" + qb.id, qb.name + " — override (blank = use default)",
               d.overrides[qb.id], "leave blank to use the default set");
  });
  document.getElementById("rqBox").innerHTML = html;
}
async function saveRoleQueries() {
  const lines = id => document.getElementById(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  const overrides = {};
  _rqBoards.forEach(qb => { const v = lines("rq-" + qb.id); if (v.length) overrides[qb.id] = v; });
  const msg = document.getElementById("rqMsg");
  try {
    await api("/api/role-queries", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default: lines("rq-default"), overrides }),
    });
    msg.innerHTML = '<span class="ok-tag">✓ saved</span>';
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

const tierLbl = { api: "API", browser: "Browser", manual: "Manual", ats: "ATS", listing: "Listing" };
const accessLbl = { none: "Public", api_key: "API key", token: "Token" };
function boardStatus(b) {
  if (!b.enabled) return ["disabled", "st-rejected"];
  if (b.manual_scan) return ["manual", "st-review"];
  if (b.fetchable) return ["auto", "st-approved"];
  if (b.tier === "manual") return ["by hand", "st-new"];
  if (b.auth && b.auth !== "none") return ["needs key", "st-new"];
  return ["pending", "st-new"];
}
async function addBoard() {
  const inp = document.getElementById("newBoardUrl");
  const msg = document.getElementById("addBoardMsg");
  const url = inp.value.trim();
  if (!url) { msg.textContent = "Paste a board or careers URL first."; return; }
  msg.innerHTML = '<span class="spin"></span>Adding…';
  try {
    const { board } = await api("/api/boards", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    inp.value = "";
    msg.innerHTML = board.tier === "ats"
      ? `<span class="ok-tag">✓ added ${esc(board.name)}</span> — ${esc(board.provider)} board, ready to scan below.`
      : `<span class="ok-tag">✓ added ${esc(board.name)}</span> — not a recognised ATS, kept as a manual link.`;
    loadBoards();
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
async function removeBoard(id, name) {
  if (!confirm(`Remove board “${name}”?`)) return;
  try {
    await api(`/api/boards/${id}`, { method: "DELETE" });
    loadBoards();
  } catch (e) { alert("Couldn't remove: " + e.message); }
}
async function loadBoards() {
  const data = await api("/api/boards");
  const boards = data.boards;
  document.getElementById("boardCount").textContent = `(${boards.length})`;
  const boardRow = (b, slug, collapsed) => {
    const [label, cls] = boardStatus(b);
    const rm = b.custom
      ? `<button class="btn sm danger" title="Remove this board" onclick="event.stopPropagation();removeBoard('${b.id}','${esc(b.name)}')">✕</button>`
      : "";
    const can = b.fetchable;
    const caret = can ? `<span class="caret" id="caret-${b.id}">▸</span> ` : '<span class="caret"></span> ';
    const hide = collapsed ? " hide" : "";
    const row = `<tr class="grp-${slug}${hide} ${can ? "boardrow" : ""}" ${can ? `onclick="toggleBoardPanel('${b.id}')"` : ""}>
      <td>${caret}<a class="jd" href="${esc(b.url || "#")}" target="_blank" onclick="event.stopPropagation()">${esc(b.name)}</a>${b.custom ? ' <span class="chip">custom</span>' : ""}</td>
      <td><span class="chip">${tierLbl[b.tier] || esc(b.tier)}</span></td>
      <td class="muted">${esc(accessLbl[b.auth] || b.auth || "")}</td>
      <td class="muted" style="font-size:11px">${b.last_scanned ? esc(b.last_scanned) : "never"}</td>
      <td id="bstat-${b.id}"><span class="status-pill ${cls}">${esc(label)}</span></td>
      <td>${rm}</td></tr>`;
    const panel = can
      ? `<tr class="grppanel-${slug} hide" id="panelrow-${b.id}"><td colspan="6" style="padding:0 0 8px">${fetchPanel(b)}</td></tr>`
      : "";
    return row + panel;
  };
  // ungrouped boards -> the registry table; named groups -> separate chip cards
  const ungrouped = boards.filter(b => !b.group);
  document.getElementById("boardRows").innerHTML = ungrouped.map(b => boardRow(b, "ungrouped", false)).join("");
  const groups = {};
  boards.forEach(b => { if (b.group) (groups[b.group] = groups[b.group] || []).push(b); });
  document.getElementById("groupCards").innerHTML = Object.keys(groups).sort().map(g => {
    const slug = g.replace(/[^a-z0-9]/gi, "").toLowerCase();
    const chips = groups[g].map(b => {
      const dot = b.last_scanned ? "#16a34a" : "#cbd5e1";   // scanned vs never
      const last = b.last_scanned ? esc(b.last_scanned.slice(5)) : "never";
      return `<span class="compchip" title="${esc(b.name)} — ${esc(b.provider || b.tier)} · last scan ${b.last_scanned || "never"}">
        <span style="color:${dot}">●</span><a href="${esc(b.url || "#")}" target="_blank" onclick="event.stopPropagation()">${esc(b.name)}</a>
        ${b.custom ? `<span class="compx" title="Remove" onclick="event.stopPropagation();removeBoard('${b.id}','${esc(b.name)}')">✕</span>` : ""}
        <span id="bstat-${b.id}" class="compstat muted">${last}</span></span>`;
    }).join("");
    return `<div class="panel p" style="margin-top:12px">
      <div onclick="toggleGroup('${slug}')" style="cursor:pointer;display:flex;align-items:center;gap:8px">
        <span class="caret" id="gcaret-${slug}">▸</span>
        <strong>${esc(g)}</strong> <span class="muted">· ${groups[g].length} boards</span>
        <span id="grpprog-${slug}" class="muted" style="font-size:11px"></span>
        <span style="flex:1"></span>
        <button class="btn sm ok" onclick="event.stopPropagation();scanGroup('${esc(g.replace(/'/g, ""))}','${slug}')">↻ Scan group</button>
      </div>
      <div id="gbody-${slug}" class="hide" style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">${chips}</div>
    </div>`;
  }).join("");
}
function toggleGroup(slug) {
  const body = document.getElementById("gbody-" + slug);
  const caret = document.getElementById("gcaret-" + slug);
  const open = body.classList.toggle("hide");
  if (caret) caret.textContent = open ? "▸" : "▾";
}
function expandGroup(slug) {
  const body = document.getElementById("gbody-" + slug);
  if (body) body.classList.remove("hide");
  const caret = document.getElementById("gcaret-" + slug);
  if (caret) caret.textContent = "▾";
}
function renderGroupScan(state, slug) {
  const prog = document.getElementById("grpprog-" + slug);
  const all = state.boards || [];
  const done = all.filter(b => ["done", "error", "skipped"].includes(b.status)).length;
  if (prog) prog.innerHTML = state.done
    ? `· <span style="color:#166534">✓ done</span>`
    : `· <span class="spin"></span> scanning ${done}/${all.length}…`;
  all.forEach(b => {
    const cell = document.getElementById("bstat-" + b.id);
    if (!cell) return;
    const m = { queued: ["queued", "#94a3b8"], in_progress: ["fetching…", "#2563eb"],
      done: [`✓ +${b.imported} new`, "#166534"], skipped: ["skipped", "#94a3b8"], error: ["error", "#b91c1c"] };
    const [lbl, col] = m[b.status] || m.queued;
    cell.innerHTML = `<span style="color:${col};font-size:11px;white-space:nowrap">${lbl}</span>`;
  });
}
function toggleBoardPanel(id) {
  const row = document.getElementById("panelrow-" + id);
  if (!row) return;
  const open = !row.classList.toggle("hide");
  const caret = document.getElementById("caret-" + id);
  if (caret) caret.textContent = open ? "▾" : "▸";
}

// ---- per-board fetch panels ---------------------------------------------
function fetchPanel(b) {
  const id = b.id;
  const queries = b.queries || [];
  const inp = "width:100%;border:1px solid var(--line);border-radius:8px;padding:7px 10px;margin-top:3px";
  const scope = b.query_based
    ? `<div class="hint" style="margin:6px 0 10px">Searches <strong>${queries.length} role queries</strong> (edit in <a class="jd" onclick="go('settings')" style="cursor:pointer">Settings</a>), merged &amp; deduped:
        <span style="display:inline-flex;gap:4px;flex-wrap:wrap;vertical-align:middle;margin-left:4px">${queries.map(q => `<span class="chip">${esc(q)}</span>`).join("")}</span></div>`
    : `<div class="hint" style="margin:6px 0 10px">Fetches the latest postings, filtered to your target roles. <span class="muted">${esc(b.note || "")}</span></div>`;
  return `<div class="panel p" style="margin-top:14px">
    <div style="display:flex;align-items:center;gap:8px">
      <strong>${esc(b.name)}</strong>
      <span class="chip">${tierLbl[b.tier] || esc(b.tier)}</span>
      ${b.manual_scan ? '<span class="chip" style="background:#fef3c7;border-color:#fcd34d;color:#92400e" title="Slow + rate-limited — not run by the ↻ Refresh button; scan it here on demand">manual scan</span>' : ""}
      <span class="mode m-remote hide" id="total-${id}"></span>
    </div>
    ${scope}
    <div class="hint" style="margin:6px 0 8px;line-height:1.6">
      <div><strong>Fetches:</strong> <code style="font-size:11px">${esc(b.fetch_url || b.url || "—")}</code>${b.query_based ? ` <span class="muted">· {q} = each role query</span>` : ""}</div>
      <div><strong>Next scan:</strong> ${b.last_scanned
        ? `incremental — only postings since <strong>${b.next_since}</strong> (last scanned ${b.last_scanned})`
        : `first scan — full backlog since <strong>${b.next_since}</strong> (${b.recency_days || 7}d)`}. <span class="muted">Preview always shows the full ${b.recency_days || 7}-day window.</span></div>
    </div>
    <div style="display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:8px 0">
      ${depthControls(b, id, inp)}
      <label style="font-size:12px;padding-bottom:8px"><input type="checkbox" id="rem-${id}"> Remote only</label>
    </div>
    <div style="margin-top:6px">
      <button class="btn" onclick="boardPreview('${id}')">Preview</button>
      <button class="btn ok" onclick="boardImport('${id}')">Import new to Jobs</button>
      <span id="msg-${id}" class="hint" style="margin-left:8px"></span>
    </div>
    <details style="margin-top:10px"><summary>Direct URL</summary>
      <div id="url-${id}" class="hint" style="word-break:break-all;margin-top:8px">Run a preview to see the exact fetch URL.</div></details>
    <div id="res-${id}" style="margin-top:12px"></div>
  </div>`;
}
const DEPTH_LABEL = {
  all: "every open role in one call",
  feed: "the whole feed within the age horizon",
  render: "one rendered page per role query",
};
function depthControls(b, id, inp) {
  const sizeInp = `<label style="font-size:12px">Page size<br>
    <input id="size-${id}" type="number" value="${b.page_size || 50}" min="1" max="100" style="${inp};width:84px"></label>`;
  const pgInp = `<label style="font-size:12px">Pages${b.query_based ? " / query" : ""}<br>
    <input id="pg-${id}" type="number" value="${b.pages || 5}" min="1" max="25" style="${inp};width:84px"></label>`;
  const save = `<button class="btn sm" style="margin-bottom:2px" onclick="saveBoardSettings('${id}')">Save depth</button>`;
  if (b.depth === "paginated") return sizeInp + pgInp + save;
  if (b.depth === "pages") return pgInp +
    `<span class="muted" style="font-size:11px;padding-bottom:9px">page size fixed by ${esc(b.name)}</span>` + save;
  return `<span class="muted" style="font-size:11.5px;padding-bottom:9px">Depth: fetches ${DEPTH_LABEL[b.depth] || "the window"} — nothing to tune.</span>`;
}
function boardParams(id) {
  const g = s => document.getElementById(s + "-" + id);
  return {
    remote_only: g("rem") ? g("rem").checked : false,
    page_size: g("size") ? parseInt(g("size").value || "0", 10) : 0,
    pages: g("pg") ? parseInt(g("pg").value || "0", 10) : 0,
  };
}
async function saveBoardSettings(id) {
  const p = boardParams(id);
  const msg = document.getElementById("msg-" + id);
  try {
    await api(`/api/boards/${id}/settings`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page_size: p.page_size, pages: p.pages }),
    });
    msg.innerHTML = '<span class="ok-tag">✓ depth saved</span>';
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
function boardPost(id, action) {
  return api(`/api/boards/${id}/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(boardParams(id)),
  });
}
function boardTable(jobs) {
  if (!jobs.length) return '<div class="muted">No jobs matched those filters.</div>';
  return `<table><thead><tr><th>Score</th><th>Role / Company</th><th>Mode</th>
      <th title="When the board listed the job — may differ from the original posting date">Listed</th></tr></thead><tbody>`
    + jobs.map(j => {
      const s = j.score >= 80 ? "s-hi" : j.score >= 70 ? "s-mid" : "s-lo";
      const lang = j.language_block
        ? ` <span class="lang-block" title="${esc(j.language_note)}">🚫 LANG</span>` : "";
      const co = j.url ? `<a class="jd" href="${esc(j.url)}" target="_blank">${esc(j.company)}</a>` : esc(j.company);
      return `<tr><td class="score ${s}">${j.score}</td>
        <td>${esc(j.role)}${lang}<div class="sub2">${co}</div></td>
        <td><span class="mode ${modeCls[j.mode] || ""}">${modeLbl[j.mode] || j.mode}</span></td>
        <td class="muted" title="${esc(j.posted || "")}">${relTime(j.posted)}</td></tr>`;
    }).join("") + `</tbody></table>`;
}
async function boardPreview(id) {
  const msg = document.getElementById("msg-" + id), res = document.getElementById("res-" + id);
  msg.innerHTML = '<span class="spin"></span>Fetching…'; res.innerHTML = "";
  try {
    const d = await boardPost(id, "preview");
    document.getElementById("url-" + id).textContent = d.direct_url;
    const total = document.getElementById("total-" + id);
    total.textContent = `${d.total} total`; total.classList.remove("hide");
    const q = (d.queries && d.queries.length > 1) ? `${d.queries.length} role queries · ` : "";
    const off = d.off_target ? ` · ${d.off_target} off-target` : "";
    const old = d.stale ? ` · ${d.stale} too old` : "";
    const dup = d.already ? ` · ${d.already} already in tracker` : "";
    const pg = d.pages_fetched ? ` across ${d.pages_fetched} page(s)` : "";
    const hz = d.since ? ` <span class="muted">(7-day window: posted since ${d.since})</span>` : "";
    msg.innerHTML = `${q}scanned <strong>${d.total}</strong> listings${pg} · <strong>${d.jobs.length}</strong> new product role(s)${dup}${off}${old}${hz}`;
    res.innerHTML = boardTable(d.jobs);
  } catch (e) { msg.textContent = "✗ " + e.message; }
}
async function boardImport(id) {
  const msg = document.getElementById("msg-" + id);
  msg.innerHTML = '<span class="spin"></span>Importing…';
  try {
    const d = await boardPost(id, "import");
    const drop = d.dropped ? `, dropped ${d.dropped} off-target` : "";
    msg.innerHTML = `<span class="ok-tag">✓ imported ${d.imported} new, skipped ${d.skipped} already${drop}</span> — <a class="jd" onclick="go('jobs')" style="cursor:pointer">view Jobs →</a>`;
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

async function uploadBase(kind) {
  const fileEl = document.getElementById(kind + "File");
  const textEl = document.getElementById(kind + "Text");
  const msg = document.getElementById(kind + "Msg");
  const fd = new FormData();
  if (fileEl.files.length) fd.append("file", fileEl.files[0]);
  else if (textEl.value.trim()) fd.append("text", textEl.value.trim());
  else { msg.textContent = "Choose a file or paste text."; return; }
  try {
    await api(`/api/base-docs/${kind}`, { method: "POST", body: fd });
    msg.innerHTML = '<span class="ok-tag">✓ saved</span>';
    fileEl.value = ""; textEl.value = "";
    loadSettings();
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

// weight key -> {label, keyword field (null = mode-based)}
const FACTORS = [
  { wkey: "skills", label: "Skills & quals", kw: "skills" },
  { wkey: "domain", label: "Domain match", kw: "domains" },
  { wkey: "stage", label: "Stage / operating style", kw: "stage_signals" },
];
let weights = {};
function renderScoring(w, kw) {
  weights = { ...w };
  document.getElementById("scoringBox").innerHTML = FACTORS.map(f => {
    const body = f.kw
      ? `<textarea id="kw-${f.kw}" class="ta" style="height:120px;font-weight:400;font-size:11.5px" placeholder="one keyword per line">${esc((kw[f.kw] || []).join("\n"))}</textarea>`
      : `<div class="hint" style="margin-top:8px">${f.note} — no keywords</div>`;
    return `<div style="border:1px solid var(--line);border-radius:10px;padding:10px 12px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <strong style="font-size:13px">${f.label}</strong>
        <label style="font-size:11px;color:var(--muted)">weight
          <input type="number" min="0" max="100" value="${weights[f.wkey] ?? 0}" data-w="${f.wkey}" oninput="onWeight(this)"
            style="width:62px;border:1px solid var(--line);border-radius:6px;padding:4px 6px;margin-left:4px"></label>
      </div>
      <div style="margin-top:8px">${body}</div>
    </div>`;
  }).join("");
  updateSum();
}
function onWeight(el) { weights[el.dataset.w] = parseInt(el.value || "0", 10); updateSum(); }
function updateSum() {
  const sum = Object.values(weights).reduce((a, b) => a + b, 0);
  const el = document.getElementById("weightSum");
  el.textContent = `Total: ${sum} / 100`;
  el.style.color = sum === 100 ? "var(--green)" : "var(--red)";
}
async function saveScoring() {
  const msg = document.getElementById("scoringMsg");
  const sum = Object.values(weights).reduce((a, b) => a + b, 0);
  if (sum !== 100) { msg.textContent = "Weights must sum to 100 (now " + sum + ")."; return; }
  const lines = id => { const e = document.getElementById(id); return e ? e.value.split("\n").map(s => s.trim()).filter(Boolean) : []; };
  msg.innerHTML = '<span class="spin"></span>saving & re-scoring…';
  try {
    const r = await api("/api/profile/scoring", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weights, skills: lines("kw-skills"), domains: lines("kw-domains"), stage_signals: lines("kw-stage_signals") }),
    });
    msg.innerHTML = `<span class="ok-tag">✓ saved · re-scored ${r.rescored} job(s)</span>`;
    loadJobs();
  } catch (e) { msg.textContent = "✗ " + e.message; }
}

// ---- boot ----------------------------------------------------------------
routeFromHash();   // honour a deep link in the URL on load; defaults to the jobs list
