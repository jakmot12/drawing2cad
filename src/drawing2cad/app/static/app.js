// drawing2cad desktop UI (vanilla ES modules, no build step, works offline).
import { ModelViewer } from "/static/viewer.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ICONS = {
  ok: '<svg class="i" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  warn: '<svg class="i" viewBox="0 0 24 24"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17.3v.2"/></svg>',
  err: '<svg class="i" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  file: '<svg class="i" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
  image: '<svg class="i" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 15l3-3 3 3 2-2 3 3"/><circle cx="8.5" cy="8.5" r="1.3"/></svg>',
  cube: '<svg class="i" viewBox="0 0 24 24"><path d="M12 2.8 20.5 7.4v9.2L12 21.2l-8.5-4.6V7.4z"/><path d="M3.5 7.4 12 12l8.5-4.6M12 12v9.2"/></svg>',
  download: '<svg class="i" viewBox="0 0 24 24"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>',
  external: '<svg class="i" viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  folder: '<svg class="i" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  report: '<svg class="i" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/></svg>',
  rotate: '<svg class="i" viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5"/></svg>',
  edges: '<svg class="i" viewBox="0 0 24 24"><path d="M12 2.8 20.5 7.4v9.2L12 21.2l-8.5-4.6V7.4z"/></svg>',
  stop: '<svg class="i" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  check: '<svg class="i" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  pdf: '<svg class="i" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 15h1.5a1.5 1.5 0 0 0 0-3H8v5M13 12v5h1a2 2 0 0 0 2-2v-1a2 2 0 0 0-2-2z"/></svg>',
};

const STATUS = {
  "OK": { cls: "ok", st: "st-ok", label: "OK" },
  "NIEPEWNE": { cls: "warn", st: "st-warn", label: "NIEPEWNE" },
  "BŁĄD": { cls: "err", st: "st-err", label: "BŁĄD" },
};
const SEVERITY = { blad_rysunku: "błąd rysunku", ostrzezenie: "ostrzeżenie", info: "informacja" };

const state = {
  info: null,
  pdf: null,
  job: null,
  pollTimer: null,
  part: 0,
  tab: "dims",
  viewer: null,
  viewerOpts: { edges: true, rotate: false },
  deciding: false, // a decision request is in flight (decision buttons are disabled)
  decideSeq: 0, // number of the newest decision request; older responses are ignored
};

const isDesktop = () => !!window.pywebview;

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtNum(v, digits = 2) {
  return Number(v).toLocaleString("pl-PL", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtUsd(v) {
  return `${fmtNum(v ?? 0, (v ?? 0) > 0 && v < 0.1 ? 3 : 2)} USD`;
}
function fmtTime(sec) {
  sec = Math.max(0, Math.round(sec));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function fmtSize(b) {
  return b > 1e6 ? `${fmtNum(b / 1e6, 1)} MB` : `${fmtNum(b / 1e3, 0)} kB`;
}
function badge(status, size = "") {
  const s = STATUS[status] || STATUS["NIEPEWNE"];
  return `<span class="badge ${size} ${s.st}">${ICONS[s.cls]}${esc(s.label)}</span>`;
}

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isErr);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isErr ? 6000 : 3000);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (init.method !== "GET") init.headers["X-Drawing2cad"] = "1";
  if (opts.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  } else if (opts.body) {
    init.body = opts.body;
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new Error(detail || `Błąd serwera (${res.status})`);
  }
  return data;
}

// ---------------------------------------------------------------- theme
function initTheme() {
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("d2c-theme", next); } catch (e) { /* ignore */ }
    const r = currentReport();
    if (state.viewer) state.viewer.refreshTheme(r && r.rejected_model);
  });
}

// ---------------------------------------------------------------- PDF selection
function initPdfInput() {
  const dz = $("#dropzone");
  const input = $("#file-input");
  input.addEventListener("change", () => input.files[0] && uploadFile(input.files[0]));
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => {
    const f = [...(e.dataTransfer?.files || [])].find((x) => /\.pdf$/i.test(x.name) || x.type === "application/pdf");
    if (f) uploadFile(f); else toast("Upuść plik PDF.", true);
  });
  // dropping a file anywhere else must not navigate the window away
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file, file.name);
  setPdfLoading(file.name);
  try {
    setPdf(await api("/api/upload", { method: "POST", body: fd }));
  } catch (e) {
    setPdf(null);
    toast(e.message, true);
  }
}

async function selectSample(name) {
  setPdfLoading(name);
  try {
    setPdf(await api(`/api/samples/${encodeURIComponent(name)}`, { method: "POST" }));
  } catch (e) {
    setPdf(null);
    toast(e.message, true);
  }
}

function setPdfLoading(name) {
  const box = $("#pdf-info");
  box.hidden = false;
  box.innerHTML = `<div class="pdf-head">${ICONS.pdf}<div><div class="pdf-name">${esc(name)}</div><div class="pdf-meta">Odczyt rysunku…</div></div></div>`;
}

function setPdf(pdf) {
  state.pdf = pdf;
  const box = $("#pdf-info");
  if (!pdf) { box.hidden = true; updateStart(); return; }
  box.hidden = false;
  let body;
  if (pdf.parts.length) {
    body = `<ul class="part-checks">${pdf.parts.map((p) => `
      <li><label><input type="checkbox" class="part-check" value="${esc(p.drawing_no)}" checked>
      <span class="mono">${esc(p.drawing_no || "?")}</span><span>${esc(p.name || "część")}</span>
      <span class="muted small" style="margin-left:auto">${p.n_dims} wym.</span></label></li>`).join("")}</ul>`;
  } else if (pdf.is_assembly) {
    body = `<div class="pdf-note">Rysunek złożeniowy: ${pdf.bom.length} pozycji BOM. Program odczyta BOM, bez budowy modeli.</div>`;
  } else {
    body = `<div class="pdf-note">Nie znaleziono części na rysunku.</div>`;
  }
  const kind = pdf.is_assembly ? "złożenie" : `${pdf.parts.length} ${plural(pdf.parts.length, "część", "części", "części")}`;
  box.innerHTML = `<div class="pdf-head">${ICONS.pdf}<div><div class="pdf-name">${esc(pdf.name)}</div>
    <div class="pdf-meta">${fmtSize(pdf.size)} · ${kind}${pdf.title ? " · " + esc(pdf.title) : ""}</div></div></div>${body}`;
  $$(".part-check", box).forEach((c) => c.addEventListener("change", updateStart));
  updateStart();
}

function plural(n, one, few, many) {
  if (n === 1) return one;
  const d = n % 10, h = n % 100;
  return d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
}

function selectedParts() {
  const checks = $$(".part-check");
  if (!checks.length) return null;
  const sel = checks.filter((c) => c.checked).map((c) => c.value);
  return sel.length === checks.length ? null : sel;
}

function currentMode() {
  return $("input[name=mode]:checked").value;
}

function updateStart() {
  const btn = $("#start-btn"), hint = $("#start-hint"), label = $("#start-label");
  const mode = currentMode();
  const running = state.job && ["queued", "running"].includes(state.job.status);
  $("#max-cost-field").hidden = mode !== "ai";
  btn.classList.toggle("paid", mode === "ai");
  label.textContent = mode === "ai" ? "Oszacuj koszt i uruchom…" : "Uruchom";
  const noneChecked = $$(".part-check").length > 0 && !$$(".part-check").some((c) => c.checked);
  let msg = "";
  if (!state.pdf) msg = "Najpierw wybierz rysunek PDF.";
  else if (running) msg = "Trwa przetwarzanie…";
  else if (noneChecked) msg = "Zaznacz co najmniej jedną część.";
  else if (mode === "ai") msg = "Przed startem zobaczysz najgorszy koszt i limit.";
  else msg = "Bez wywołań API, 0 zł.";
  btn.disabled = !state.pdf || running || noneChecked;
  hint.textContent = msg;
}

// ---------------------------------------------------------------- start / cost confirmation
async function onStart() {
  const mode = currentMode();
  const payload = {
    pdf_id: state.pdf.id,
    mode,
    max_attempts: Number($("#max-attempts").value),
    parts: selectedParts(),
  };
  try {
    if (mode === "ai") {
      const maxCost = Number($("#max-cost").value);
      if (!(maxCost > 0)) { toast("Podaj limit kosztu większy od zera.", true); return; }
      const est = await api("/api/estimate", { method: "POST", json: { ...payload, max_cost: maxCost } });
      if (!(await confirmCost(est))) return;
      Object.assign(payload, { max_cost: maxCost, confirm_cost: true, accepted_max_cost: est.max_cost });
    }
    const job = await api("/api/jobs", { method: "POST", json: payload });
    followJob(job);
    loadHistory();
  } catch (e) {
    toast(e.message, true);
  }
}

function confirmCost(est) {
  const dlg = $("#cost-modal");
  const accept = $("#cost-accept");
  const confirmBtn = $("#cost-confirm");
  accept.checked = false;
  confirmBtn.disabled = true;
  $("#cost-content").innerHTML = `
    <table class="cost-table">
      <tr><td>Dostawca i model</td><td>${esc(est.provider)} · ${esc(est.model || "brak")}</td></tr>
      <tr><td>Części × maks. prób</td><td>${est.n_parts} × ${est.max_attempts}</td></tr>
      <tr><td>Szacunek za wywołanie</td><td>~${fmtUsd(est.per_call_usd)}</td></tr>
      <tr><td>Limit kosztu przebiegu (max_cost)</td><td>${fmtUsd(est.max_cost)}</td></tr>
    </table>
    <div class="cost-worst"><span>Najgorszy przypadek</span><b>~${fmtUsd(est.worst_usd)}</b></div>
    ${est.key_configured ? "" : `<div class="alert alert-err">Brak klucza <span class="mono">${esc(est.key_env)}</span> w .env: przebieg zakończy się błędem konfiguracji, bez kosztów.</div>`}
    <div class="alert alert-info">Odpowiedzi zapisane w cache (<span class="mono">${esc(est.cache_dir || "")}</span>) są darmowe. Po osiągnięciu limitu kolejne części dostają BŁĄD „Limit kosztów” zamiast nowych wywołań. Przebieg można przerwać.</div>`;
  $("#cost-accept-label").textContent = `Rozumiem, że ten przebieg może kosztować do ~${fmtUsd(est.worst_usd)} (limit ${fmtUsd(est.max_cost)}).`;
  accept.onchange = () => (confirmBtn.disabled = !accept.checked);
  return new Promise((resolve) => {
    dlg.onclose = () => resolve(dlg.returnValue === "confirm" && accept.checked);
    dlg.returnValue = "";
    dlg.showModal();
  });
}

// ---------------------------------------------------------------- jobs
function showView(name) {
  $("#view-empty").hidden = name !== "empty";
  $("#view-progress").hidden = name !== "progress";
  $("#view-result").hidden = name !== "result";
}

function followJob(job) {
  clearTimeout(state.pollTimer);
  state.job = job;
  if (["queued", "running"].includes(job.status)) {
    renderProgress(job);
    const tick = async () => {
      try {
        const j = await api(`/api/jobs/${job.id}`);
        if (state.job?.id !== j.id) return;
        state.job = j;
        if (["queued", "running"].includes(j.status)) {
          renderProgress(j);
          state.pollTimer = setTimeout(tick, 700);
        } else {
          state.part = 0;
          renderResult(j);
          loadHistory();
          updateStart();
          if (j.status === "done") toast("Gotowe: wyniki przetwarzania są poniżej.");
        }
      } catch (e) {
        state.pollTimer = setTimeout(tick, 1500);
      }
    };
    state.pollTimer = setTimeout(tick, 400);
  } else {
    renderResult(job);
  }
  updateStart();
  markHistory();
}

async function openJob(id) {
  try {
    state.part = 0;
    followJob(await api(`/api/jobs/${id}`));
  } catch (e) {
    toast(e.message, true);
  }
}

function renderProgress(job) {
  showView("progress");
  disposeViewer();
  const done = job.parts.filter((p) => p.state === "done").length;
  const total = job.parts.length || 1;
  const pct = Math.max(4, Math.round(((done + (job.current != null ? 0.5 : 0)) / total) * 100));
  const root = $("#view-progress");
  const log = job.log.map((l) => `<div class="${esc(l.level)}"><span class="t">${fmtTime(l.t)}</span>${esc(l.text)}</div>`).join("");
  root.innerHTML = `
    <div class="pv-head">
      <div><div class="pv-title">Przetwarzanie: ${esc(job.pdf_name)}</div>
      <div class="pv-sub">${esc(job.mode_label)} · maks. ${job.max_attempts} ${plural(job.max_attempts, "próba", "próby", "prób")} na część</div></div>
      <div><div class="pv-timer">${fmtTime(job.elapsed)}</div>
      <div class="live-cost">${esc(job.costs?.summary || "")}</div></div>
    </div>
    <div class="pv-phase"><span class="spinner"></span>${esc(job.phase)}${job.cancel_requested ? " (przerywanie…)" : ""}</div>
    <div class="bar"><div style="width:${pct}%"></div></div>
    <div class="pv-grid">
      <div>
        <p class="section-label">Części (${done}/${job.parts.length})</p>
        <ul class="pv-parts">${job.parts.map((p) => `
          <li class="${p.state}">
            <span class="state-dot ${p.state}">${p.state === "running" ? '<span class="spinner"></span>' : p.state === "done" ? ICONS.check : ""}</span>
            <span class="mono muted">${esc(p.drawing_no)}</span><span class="p-name">${esc(p.name || "")}</span>
            ${p.attempt ? `<span class="p-att">próba ${p.attempt}</span>` : ""}
          </li>`).join("")}</ul>
        <div style="margin-top:14px"><button class="btn btn-sm" id="cancel-btn">${ICONS.stop}Przerwij</button></div>
      </div>
      <div>
        <p class="section-label">Dziennik</p>
        <pre class="log" id="log">${log}</pre>
      </div>
    </div>`;
  const logEl = $("#log");
  logEl.scrollTop = logEl.scrollHeight;
  $("#cancel-btn").addEventListener("click", async () => {
    try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); toast("Przerywam po bieżącej części."); }
    catch (e) { toast(e.message, true); }
  });
}

// ---------------------------------------------------------------- results
function currentReport() {
  return state.job?.result?.reports?.[state.part] || null;
}

function renderResult(job) {
  showView("result");
  const root = $("#view-result");
  const res = job.result || { reports: [] };
  const c = job.counts || { OK: 0, NIEPEWNE: 0, "BŁĄD": 0 };
  const costs = job.costs || { calls: 0, usd: 0, summary: "" };
  const reportBtn = res.html_report
    ? (isDesktop()
      ? `<button class="btn btn-sm" data-open="report">${ICONS.report}Raport HTML</button>`
      : `<a class="btn btn-sm" href="/api/jobs/${job.id}/report" target="_blank" rel="noopener">${ICONS.report}Raport HTML</a>`)
    : "";
  const statusNote = job.status === "error" ? `<div class="alert alert-err" style="margin-top:10px">Przebieg zakończony błędem: ${esc(job.error || "")}</div>`
    : job.status === "cancelled" ? `<div class="alert alert-info" style="margin-top:10px">Przebieg przerwany przez użytkownika.</div>` : "";
  const tile = (n, cls, label) => `<div class="s-tile s-count ${n ? cls : "zero"}"><div class="n">${n}</div><div class="l">${label}</div></div>`;
  root.innerHTML = `
    <div class="summary">
      <div class="s-tile s-title">
        <h1 title="${esc(job.pdf_name)}">${esc(job.pdf_name)}</h1>
        <div class="meta">${esc(job.mode_label)} · ${fmtDate(job.created)} · czas ${fmtTime(job.elapsed)}</div>
        <div class="actions">${reportBtn}<button class="btn btn-sm" data-open="folder">${ICONS.folder}Folder wyników</button></div>
        ${statusNote}
      </div>
      ${tile(c.OK, "ok", "OK")}${tile(c.NIEPEWNE, "warn", "NIEPEWNE")}${tile(c["BŁĄD"], "err", "BŁĄD")}
      <div class="s-tile s-cost">
        <p class="section-label">Koszt AI</p>
        <div class="cost-big"><span class="cost-usd">${fmtUsd(costs.usd)}</span>
        <span class="cost-calls">${costs.calls} ${plural(costs.calls, "wywołanie", "wywołania", "wywołań")} API</span></div>
        <div class="cost-summary">${esc(costs.summary)}${costs.max_cost != null && job.mode === "ai" ? ` Limit: ${fmtUsd(costs.max_cost)}.` : ""}</div>
      </div>
    </div>
    ${res.reports.length ? `<div class="parts-grid" id="parts-grid"></div><div class="detail" id="detail"></div>` : renderAssembly(res)}`;
  $$("[data-open]", root).forEach((b) => b.addEventListener("click", () => openOnDisk(b.dataset.open)));
  if (res.reports.length) {
    renderPartCards();
    renderDetail();
  } else {
    disposeViewer();
  }
}

function renderAssembly(res) {
  if (!res.is_assembly) {
    return `<div class="detail"><div class="empty-tab">Brak przetworzonych części.</div></div>`;
  }
  const t = res.assembly_title || {};
  return `<div class="detail">
    <div class="detail-head"><div class="dh-left"><h2>${esc(t.nazwa || "Złożenie")}</h2><span class="mono">${esc(t.nr_rysunku || "")}</span></div>
    <span class="muted">Rysunek złożeniowy: wykaz części (BOM)</span></div>
    <div class="tab-body"><table class="data"><thead><tr><th>Poz.</th><th>Nazwa</th><th>Nr rysunku</th><th>Ilość</th><th>Znormalizowana</th></tr></thead>
    <tbody>${res.bom.map((b) => `<tr><td class="num">${b.poz}</td><td>${esc(b.nazwa)}</td><td class="num">${esc(b.nr_rysunku || "–")}</td><td class="num">${b.ilosc}</td><td>${b.znormalizowana ? "tak" : "nie"}</td></tr>`).join("")}</tbody></table></div></div>`;
}

function renderPartCards() {
  const reports = state.job.result.reports;
  $("#parts-grid").innerHTML = reports.map((r, i) => {
    const s = STATUS[r.status] || STATUS["NIEPEWNE"];
    const views = r.view_checks.length ? `${r.n_views_ok}/${r.view_checks.length}` : "–";
    return `<button class="part-card ${s.st} ${i === state.part ? "active" : ""}" data-idx="${i}">
      <div class="pc-no">${esc(r.drawing_no)}</div>
      <div class="pc-name">${esc(r.name)}</div>
      ${badge(r.status, "badge-xl")}
      <div class="pc-stats"><span title="OK / wszystkie">Wymiary ${r.n_checks_ok}/${r.checks.length}${r.n_checks_warn ? ` <b class="c-warn">+${r.n_checks_warn}?</b>` : ""}</span><span>Widoki ${views}</span><span>Próby ${r.attempts}</span></div>
    </button>`;
  }).join("");
  $$(".part-card").forEach((b) => b.addEventListener("click", () => {
    state.part = Number(b.dataset.idx);
    $$(".part-card").forEach((x) => x.classList.toggle("active", x === b));
    renderDetail();
  }));
}

function renderDetail() {
  const job = state.job, r = currentReport(), i = state.part;
  const base = `/api/jobs/${job.id}/parts/${i}`;
  const stepBtns = r.has_step ? `
      <button class="btn btn-sm" data-step="step">${ICONS.external}Otwórz STEP</button>
      <a class="btn btn-sm" href="${base}/model.step" download="${esc(r.step_name)}">${ICONS.download}Pobierz STEP</a>
      <button class="btn btn-sm" data-step="step_folder">${ICONS.folder}Pokaż w folderze</button>` : `<span class="muted small">Brak pliku STEP (bryła nie powstała)</span>`;
  const [bx, by, bz] = r.bounding_box_mm || [0, 0, 0];
  const ambs = r.ambiguities_raw || r.ambiguities;
  const nAmb = ambs.length, nErr = r.errors.length;
  const nPending = ambs.filter((a) => a.blocking && !a.decision).length;
  const dimCls = r.checks.some((c) => c.status === "BŁĄD") ? "err" : r.checks.some((c) => c.status === "NIEPEWNE") ? "warn" : "";
  const viewCls = r.view_checks.some((v) => !v.ok && !v.uncertain) ? "err" : r.view_checks.some((v) => !v.ok) ? "warn" : "";
  const cntCls = (n, cls) => (n ? cls : "");
  $("#detail").innerHTML = `
    <div class="detail-head">
      <div class="dh-left">${badge(r.status, "badge-xl")}<div><h2>${esc(r.name)}</h2><span class="mono">${esc(r.drawing_no)}${r.step_name ? " · " + esc(r.step_name) : ""}</span></div></div>
      <div class="dh-actions">${stepBtns}</div>
    </div>
    <div class="panes">
      <div class="pane">
        <div class="pane-head"><h3>${ICONS.image}Rysunek (wycinek PDF)</h3><span class="muted small">kliknij, aby powiększyć</span></div>
        <div class="pane-body sheet" id="sheet">${r.has_image ? `<img src="${base}/image" alt="Rysunek części ${esc(r.name)}">` : `<div class="pane-empty">Brak wycinka rysunku</div>`}</div>
      </div>
      <div class="pane">
        <div class="pane-head"><h3>${ICONS.cube}Model 3D</h3>
          <div class="pane-tools">
            <button class="tool" data-view="iso">Izometria</button><button class="tool" data-view="front">Przód</button>
            <button class="tool" data-view="top">Góra</button><button class="tool" data-view="right">Bok</button>
            <button class="tool ${state.viewerOpts.edges ? "on" : ""}" id="t-edges" title="Krawędzie">${ICONS.edges}</button>
            <button class="tool ${state.viewerOpts.rotate ? "on" : ""}" id="t-rotate" title="Obrót automatyczny">${ICONS.rotate}</button>
          </div></div>
        <div class="pane-body viewer" id="viewer">
          ${r.has_step ? `<div class="pane-empty" id="viewer-loading"><span class="spinner"></span></div>` : `<div class="pane-empty">Brak modelu 3D: ${esc(r.errors[0] || "bryła nie powstała")}</div>`}
          ${r.rejected_model ? `<div class="viewer-flag"><span class="badge badge-sm st-err">${ICONS.err}Model odrzucony przez weryfikator</span></div>` : ""}
          <div class="viewer-note">LPM obrót · PPM przesuw · kółko zoom</div>
        </div>
      </div>
    </div>
    <div class="facts" style="margin-top:14px">
      <div class="fact"><span class="k">GABARYTY [mm]</span><span class="v">${fmtNum(bx, 1)} × ${fmtNum(by, 1)} × ${fmtNum(bz, 1)}</span></div>
      <div class="fact"><span class="k">OBJĘTOŚĆ</span><span class="v">${fmtNum((r.volume_mm3 || 0) / 1000, 2)} cm³</span></div>
      <div class="fact"><span class="k">BRYŁA</span><span class="v">${r.is_valid_solid ? "poprawna" : "–"}</span></div>
      <div class="fact"><span class="k">PRÓBY</span><span class="v">${r.attempts}</span></div>
      ${r.threads.length ? `<div class="fact"><span class="k">GWINTY</span><span class="v">${r.threads.map((t) => esc(`${t.spec} ${t.direction}${t.length ? `, ${fmtNum(t.length, 0)} mm` : ""}`)).join(" · ")}</span></div>` : ""}
      ${r.unused_dimensions.length ? `<div class="fact"><span class="k">NIEUŻYTE WYMIARY</span><span class="v c-err">${esc(r.unused_dimensions.join(", "))}</span></div>` : ""}
    </div>
    <div class="tabs" role="tablist">
      <button class="tab" data-tab="dims">Wymiary <span class="cnt ${dimCls}">${r.checks.length}</span></button>
      <button class="tab" data-tab="views">Widoki <span class="cnt ${viewCls}">${r.view_checks.length}</span></button>
      <button class="tab" data-tab="amb">Zastrzeżenia <span class="cnt ${cntCls(nPending, "warn")}" title="${nPending} do decyzji">${nAmb}</span></button>
      <button class="tab" data-tab="errors">Błędy <span class="cnt ${cntCls(nErr, "err")}">${nErr}</span></button>
    </div>
    <div class="tab-body" id="tab-body"></div>`;

  if (r.has_image) $("#sheet").addEventListener("click", () => openLightbox(`${base}/image`));
  $$("[data-step]").forEach((b) => b.addEventListener("click", () => openOnDisk(b.dataset.step, i)));
  $$(".tab").forEach((t) => t.addEventListener("click", () => { state.tab = t.dataset.tab; renderTab(); }));
  if (!r.checks.length && state.tab === "dims" && nErr) state.tab = "errors";
  renderTab();
  mountViewer(r, base);
}

function renderTab() {
  const r = currentReport();
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === state.tab));
  const body = $("#tab-body");
  const empty = (msg) => `<div class="empty-tab">${msg}</div>`;
  if (state.tab === "dims") {
    body.innerHTML = r.checks.length ? `<table class="data"><thead><tr>
        <th>Id</th><th>Wymiar z rysunku</th><th>Cecha</th><th>Oczekiwane</th><th>Zmierzone</th><th>Status</th><th>Uwagi</th></tr></thead><tbody>
        ${r.checks.map((c) => `<tr class="${c.status === "BŁĄD" ? "row-err" : c.status === "NIEPEWNE" ? "row-warn" : ""}">
          <td class="num muted">${esc(c.dim_id)}</td><td class="dim">${esc(c.raw_text)}</td><td>${esc(c.target)}</td>
          <td class="num">${esc(c.expected)}</td><td class="num">${esc(c.measured)}</td><td>${badge(c.status, "badge-sm")}</td>
          <td class="msg">${esc(c.message)}</td></tr>`).join("")}</tbody></table>`
      : empty("Brak sprawdzonych wymiarów: model nie został zweryfikowany (zobacz „Błędy”).");
  } else if (state.tab === "views") {
    body.innerHTML = r.view_checks.length ? `<table class="data"><thead><tr>
        <th>Widok</th><th>Status</th><th>Rzut</th><th>Skala</th><th>Uwagi</th></tr></thead><tbody>
        ${r.view_checks.map((v) => {
          const st = v.ok ? "OK" : v.uncertain ? "NIEPEWNE" : "BŁĄD";
          return `<tr class="${st === "BŁĄD" ? "row-err" : st === "NIEPEWNE" ? "row-warn" : ""}"><td class="num">#${v.view_index + 1}</td><td>${badge(st, "badge-sm")}</td>
            <td>${esc(v.projection || "–")}</td><td class="num">${v.scale ? fmtNum(v.scale, 3) : "–"}</td><td class="msg">${esc(v.message)}</td></tr>`;
        }).join("")}</tbody></table>`
      : empty("Brak porównania widoków (bez niego wynik jest najwyżej NIEPEWNE).");
  } else if (state.tab === "amb") {
    const ambs = r.ambiguities_raw || r.ambiguities;
    const canDecide = !!r.ambiguities_raw && !!state.job.decisions_path;
    body.innerHTML = ambs.length ? `
      ${canDecide && ambs.some((a) => a.blocking) ? `<p class="decide-hint">Program nie zgaduje po cichu: potwierdź albo odrzuć przyjęte założenia.
        Potwierdzone nie blokują statusu OK, odrzucone dają BŁĄD. Decyzje trafiają do <span class="mono">${esc(state.job.decisions_path)}</span> (ten sam plik co <span class="mono">--pytaj</span> w CLI).</p>` : ""}
      <ul class="notes">${ambs.map((a, n) => {
        const d = a.decision;
        // each entry has its own key (ids may repeat within a part); n keeps DOM hooks unique
        const k = esc(a.key || "");
        const st = d ? (d.decyzja === "potwierdzone" ? "st-ok" : "st-err")
          : a.severity === "blad_rysunku" ? "st-err" : a.severity === "ostrzezenie" ? "st-warn" : "";
        let controls = "";
        if (canDecide && a.blocking) {
          controls = d ? `<div class="decision">
              <span class="badge badge-sm ${d.decyzja === "potwierdzone" ? "st-ok" : "st-err"}">${d.decyzja === "potwierdzone" ? ICONS.ok + "Potwierdzone" : ICONS.err + "Odrzucone"}</span>
              <span class="sev">${esc(d.czas)}${d.komentarz ? " · " + esc(d.komentarz) : ""}</span>
              <button class="btn btn-sm btn-ghost" data-decide="" data-amb="${esc(a.id)}" data-key="${k}">Cofnij decyzję</button></div>`
            : `<div class="decision">
              ${a.decision_ambiguous ? `<p class="sev">Wcześniejsza decyzja dotyczyła kilku jednakowych zastrzeżeń naraz albo nie da się jej jednoznacznie przypisać, więc nie jest stosowana: zdecyduj o każdym osobno.</p>` : ""}
              <button class="btn btn-sm btn-ok" data-decide="potwierdzone" data-amb="${esc(a.id)}" data-key="${k}">${ICONS.ok}Potwierdzam założenie</button>
              <button class="btn btn-sm btn-err-ghost" data-reject="${n}">${ICONS.err}Odrzucam…</button>
              <div class="reject-box" data-box="${n}" hidden>
                <input type="text" class="reject-comment" placeholder="Co jest nie tak? (opcjonalnie)" maxlength="500">
                <button class="btn btn-sm btn-danger" data-decide="odrzucone" data-amb="${esc(a.id)}" data-key="${k}">Odrzuć</button>
              </div></div>`;
        }
        return `<li class="note ${st}"><div class="note-head">
          <span class="src ${a.source === "program" ? "src-program" : "src-ai"}">${a.source === "program" ? "PROGRAM" : "AI"}</span>
          <span class="mono">${esc(a.id)}</span><span class="sev">${esc(SEVERITY[a.severity] || a.severity)}</span>
          ${a.affected_dimensions.length ? `<span class="sev">· wymiary: ${esc(a.affected_dimensions.join(", "))}</span>` : ""}</div>
          <p>${esc(a.description)}</p>${a.assumption ? `<p class="assume">Założenie: ${esc(a.assumption)}</p>` : ""}${controls}</li>`;
      }).join("")}</ul>`
      : empty("Brak zastrzeżeń AI ani programu.");
    $$("[data-reject]", body).forEach((b) => b.addEventListener("click", () => {
      const box = $(`[data-box="${CSS.escape(b.dataset.reject)}"]`, body);
      box.hidden = !box.hidden;
      if (!box.hidden) $("input", box).focus();
    }));
    $$(".reject-comment", body).forEach((inp) => inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") $("[data-decide]", inp.closest(".reject-box")).click();
    }));
    $$("[data-decide]", body).forEach((b) => b.addEventListener("click", () => {
      const box = b.closest(".reject-box");
      decide(b.dataset.amb, b.dataset.key, b.dataset.decide || null, box ? $("input", box).value : "");
    }));
    setDecideButtons(!state.deciding);
  } else {
    body.innerHTML = r.errors.length ? `<ul class="notes">${r.errors.map((e) => `<li class="note st-err"><div class="err-item">${ICONS.err}<span>${esc(e)}</span></div></li>`).join("")}</ul>`
      : empty("Brak błędów.");
  }
}

function setDecideButtons(enabled) {
  $$("[data-decide], [data-reject], .reject-comment").forEach((b) => { b.disabled = !enabled; });
}

// One decision request at a time; a response is shown only if it is the newest request and
// the same run (and part) is still on screen, so a late answer never overwrites newer state.
async function decide(ambId, ambKey, decision, comment) {
  if (state.deciding) return;
  const seq = (state.decideSeq = (state.decideSeq || 0) + 1);
  const jobId = state.job.id, part = state.part;
  const current = () => seq === state.decideSeq && state.job?.id === jobId;
  const y = window.scrollY;
  state.deciding = true;
  setDecideButtons(false);
  try {
    const job = await api(`/api/jobs/${jobId}/decisions`, {
      method: "POST",
      json: { part, ambiguity_id: ambId, ambiguity_key: ambKey || null, decision, comment },
    });
    if (!current() || job.id !== jobId) return;
    state.job = job;
    state.deciding = false;
    renderResult(job);
    window.scrollTo(0, y);
    loadHistory();
    const r = job.result.reports[part];
    toast(`${r.name}: ${decision === "potwierdzone" ? "założenie potwierdzone" : decision ? "założenie odrzucone" : "decyzja cofnięta"}, status ${r.status}.`);
  } catch (e) {
    if (current()) toast(e.message, true);
  } finally {
    if (seq === state.decideSeq) {
      state.deciding = false;
      setDecideButtons(true);
    }
  }
}

// ---------------------------------------------------------------- 3D viewer
function disposeViewer() {
  if (state.viewer) { state.viewer.dispose(); state.viewer = null; }
}

async function mountViewer(r, base) {
  disposeViewer();
  if (!r.has_step) return;
  const host = $("#viewer");
  let viewer;
  try {
    viewer = new ModelViewer(host);
  } catch (e) {
    $("#viewer-loading").innerHTML = "Podgląd 3D niedostępny (brak WebGL).";
    return;
  }
  state.viewer = viewer;
  viewer.setEdges(state.viewerOpts.edges);
  viewer.setAutoRotate(state.viewerOpts.rotate);
  $$("[data-view]").forEach((b) => b.addEventListener("click", () => viewer.setView(b.dataset.view)));
  $("#t-edges").addEventListener("click", (e) => {
    state.viewerOpts.edges = !state.viewerOpts.edges;
    viewer.setEdges(state.viewerOpts.edges);
    e.currentTarget.classList.toggle("on", state.viewerOpts.edges);
  });
  $("#t-rotate").addEventListener("click", (e) => {
    state.viewerOpts.rotate = !state.viewerOpts.rotate;
    viewer.setAutoRotate(state.viewerOpts.rotate);
    e.currentTarget.classList.toggle("on", state.viewerOpts.rotate);
  });
  try {
    await viewer.load(`${base}/model.stl`, `${base}/edges.json`, { rejected: r.rejected_model });
    $("#viewer-loading")?.remove();
  } catch (e) {
    const l = $("#viewer-loading");
    if (l && state.viewer === viewer) l.textContent = `Nie udało się wczytać modelu: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------- misc actions
async function openOnDisk(what, part = null) {
  try {
    await api(`/api/jobs/${state.job.id}/open`, { method: "POST", json: { what, part } });
  } catch (e) {
    toast(e.message, true);
  }
}

function openLightbox(src) {
  const lb = $("#lightbox");
  $("#lightbox-img").src = src;
  lb.showModal();
}

async function loadHistory() {
  try {
    const jobs = await api("/api/jobs");
    const ul = $("#history");
    if (!jobs.length) { ul.innerHTML = '<li class="muted small">Brak przebiegów.</li>'; return; }
    ul.innerHTML = jobs.map((j) => {
      const c = j.counts;
      const badges = ["queued", "running"].includes(j.status) ? '<span class="mini run">trwa</span>'
        : j.status === "error" ? '<span class="mini err">błąd</span>'
        : `${c.OK ? `<span class="mini ok">${c.OK}</span>` : ""}${c.NIEPEWNE ? `<span class="mini warn">${c.NIEPEWNE}</span>` : ""}${c["BŁĄD"] ? `<span class="mini err">${c["BŁĄD"]}</span>` : ""}`;
      return `<li><button data-job="${esc(j.id)}"><span class="h-name">${esc(j.pdf_name)}</span><span class="mini-badges">${badges}</span>
        <span class="h-meta">${fmtDate(j.created)} · ${esc(j.mode_label)}${j.costs?.calls ? ` · ${fmtUsd(j.costs.usd)}` : ""}</span></button></li>`;
    }).join("");
    $$("[data-job]", ul).forEach((b) => b.addEventListener("click", () => openJob(b.dataset.job)));
    markHistory();
    return jobs;
  } catch (e) {
    return [];
  }
}

function markHistory() {
  $$("[data-job]").forEach((b) => b.classList.toggle("active", b.dataset.job === state.job?.id));
}

// ---------------------------------------------------------------- init
async function init() {
  initTheme();
  initPdfInput();
  $$("input[name=mode]").forEach((r) => r.addEventListener("change", updateStart));
  $("#start-btn").addEventListener("click", onStart);
  $("#lightbox").addEventListener("click", () => $("#lightbox").close());
  $("#lightbox-close").addEventListener("click", () => $("#lightbox").close());
  try {
    state.info = await api("/api/info");
  } catch (e) {
    toast("Brak połączenia z serwerem aplikacji.", true);
    return;
  }
  const info = state.info;
  $("#version").textContent = `v${info.version}`;
  $("#max-cost").value = info.default_max_cost.toFixed(2);
  for (const m of info.modes) {
    const el = $(`.mode[data-mode="${m.id}"]`);
    if (!m.available) { el.classList.add("disabled"); el.title = "Niedostępne w tej instalacji (brak plików z repozytorium)."; }
  }
  if (!info.modes.find((m) => m.id === "demo").available) $("input[value=ai]").checked = true;
  $("#samples").innerHTML = info.samples.length
    ? `<span>Przykłady:</span>${info.samples.map((s) => `<button class="chip" data-sample="${esc(s.name)}">${ICONS.file}${esc(s.name)}</button>`).join("")}` : "";
  $$("[data-sample]").forEach((b) => b.addEventListener("click", () => selectSample(b.dataset.sample)));
  updateStart();
  const jobs = await loadHistory();
  if (info.running) {
    openJob(info.running);
  } else {
    const done = jobs.filter((j) => j.status === "done");
    const last = done.find((j) => j.counts.OK + j.counts.NIEPEWNE + j.counts["BŁĄD"] > 0) || done[0];
    if (last) openJob(last.id);
  }
}

init();
