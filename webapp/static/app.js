// Plain vanilla JS, no framework/build step -- every function here maps
// directly to one Flask route in webapp/app.py. State lives in a handful
// of top-level variables rather than a framework store; that's fine at
// this scale and keeps the file readable without extra machinery.

const state = {
  page: 0,
  pageSize: 25,
  lastQuery: {},
};

// ---------- small helpers ----------

function $(selector) { return document.querySelector(selector); }
function $all(selector) { return Array.from(document.querySelectorAll(selector)); }

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

function fmtMoney(n) {
  return n == null ? "-" : `$${n.toFixed(2)}`;
}
function fmtPct(n) {
  return n == null ? "-" : `${Math.round(n * 100)}%`;
}

// ---------- tabs ----------

function initTabs() {
  $all(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $all(".tab-btn").forEach((b) => b.classList.remove("active"));
      $all(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "dashboard") loadDashboard();
      if (btn.dataset.tab === "pipeline") loadJobHistory();
    });
  });
}

// ---------- corpus badge ----------

async function refreshCorpusBadge() {
  const summary = await api("/api/summary");
  $("#corpus-badge").textContent = `${summary.total_decks} decks in corpus`;
  return summary;
}

// ---------- commander autocomplete ----------

let commanderDebounce = null;
function initCommanderAutocomplete() {
  $("#f-commander").addEventListener("input", (e) => {
    clearTimeout(commanderDebounce);
    const q = e.target.value;
    commanderDebounce = setTimeout(async () => {
      const rows = await api(`/api/commanders?q=${encodeURIComponent(q)}`);
      const list = $("#commander-list");
      list.innerHTML = rows.map((r) => `<option value="${escapeHtml(r.commander_name)}">${r.n} decks</option>`).join("");
    }, 200);
  });
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function initSourceFilter() {
  const summary = await api("/api/summary");
  const select = $("#f-source");
  select.innerHTML = Object.keys(summary.source_counts)
    .map((s) => `<option value="${s}">${s} (${summary.source_counts[s]})</option>`)
    .join("");
}

// ---------- explorer: search + table ----------

function buildQueryParams() {
  const params = new URLSearchParams();
  const commander = $("#f-commander").value.trim();
  if (commander) params.set("commander", commander);

  const sources = $all("#f-source option:checked").map((o) => o.value);
  if (sources.length) params.set("source", sources.join(","));

  const bracket = $("#f-bracket").value;
  if (bracket) params.set("bracket", bracket);

  const maxCost = $("#f-max-cost").value;
  if (maxCost) params.set("max_cost", maxCost);

  const minOwned = $("#f-min-owned").value;
  if (minOwned) params.set("min_pct_owned", Number(minOwned) / 100);

  const minConf = $("#f-min-conf").value;
  if (minConf) params.set("min_confidence", Number(minConf) / 100);

  if ($("#f-starred").checked) params.set("starred_only", "1");
  if ($("#f-show-rejected").checked) params.set("show_rejected", "1");

  params.set("sort_by", $("#f-sort-by").value);
  params.set("limit", state.pageSize);
  params.set("offset", state.page * state.pageSize);
  return params;
}

async function runSearch() {
  const params = buildQueryParams();
  state.lastQuery = params;
  const data = await api(`/api/decks?${params.toString()}`);
  renderResultsMeta(data);
  renderResultsTable(data.decks);
  renderPager(data);
}

function renderResultsMeta(data) {
  const from = data.total === 0 ? 0 : data.offset + 1;
  const to = Math.min(data.offset + data.limit, data.total);
  $("#results-meta").textContent = `${data.total} matching decks, showing ${from}-${to}`;
}

function bracketPill(row) {
  if (row.feel_bracket == null) return "-";
  const lowConf = row.low_confidence_reason ? ' <span class="pill low-conf">low conf</span>' : "";
  return `<span class="pill bracket-${row.feel_bracket}">B${row.feel_bracket}</span>${lowConf}`;
}

function renderResultsTable(decks) {
  if (decks.length === 0) {
    $("#results-table").innerHTML = `<p class="hint">No decks matched. Loosen a filter and try again.</p>`;
    return;
  }
  const rows = decks.map((d) => `
    <tr class="deck-row ${d.rejected ? "rejected-row" : ""}" data-fingerprint="${d.fingerprint}">
      <td>
        ${escapeHtml(d.commander_name)}
        <div class="hint">${d.source}</div>
      </td>
      <td>${fmtMoney(d.usd_to_complete)}</td>
      <td>${fmtPct(d.pct_owned)}</td>
      <td>${bracketPill(d)}</td>
      <td>${fmtPct(d.confidence)}</td>
      <td>${d.combo_count ? `${d.top_combo_pieces}pc ${d.top_combo_result || ""}` : "none"}</td>
      <td>
        <button class="star-btn ${d.starred ? "active" : ""}" data-fp="${d.fingerprint}" data-field="starred">★</button>
        <button class="reject-btn ${d.rejected ? "active" : ""}" data-fp="${d.fingerprint}" data-field="rejected">✕</button>
      </td>
    </tr>
  `).join("");

  $("#results-table").innerHTML = `
    <table class="deck-table">
      <thead><tr>
        <th>Commander</th><th>Cost</th><th>% owned</th><th>Bracket</th>
        <th>Confidence</th><th>Top combo</th><th>Review</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  $all(".deck-row").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("button")) return; // don't open modal when clicking star/reject
      openDeckDetail(tr.dataset.fingerprint);
    });
  });
  $all(".star-btn, .reject-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await toggleAnnotationField(btn.dataset.fp, btn.dataset.field);
      runSearch();
    });
  });
}

async function toggleAnnotationField(fingerprint, field) {
  const current = await api(`/api/decks/${fingerprint}`);
  const body = { ...current.annotation, [field]: !current.annotation[field] };
  await api(`/api/decks/${fingerprint}/annotation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function renderPager(data) {
  const hasPrev = data.offset > 0;
  const hasNext = data.offset + data.limit < data.total;
  $("#results-pager").innerHTML = `
    <button id="pager-prev" ${hasPrev ? "" : "disabled"}>&larr; prev</button>
    <span>page ${state.page + 1}</span>
    <button id="pager-next" ${hasNext ? "" : "disabled"}>next &rarr;</button>
  `;
  if (hasPrev) $("#pager-prev").addEventListener("click", () => { state.page--; runSearch(); });
  if (hasNext) $("#pager-next").addEventListener("click", () => { state.page++; runSearch(); });
}

function initFilterForm() {
  $("#filter-form").addEventListener("submit", (e) => {
    e.preventDefault();
    state.page = 0;
    runSearch();
  });
}

// ---------- deck detail modal ----------

// All three modal controls (star, reject, save-note) write through this
// one function against one shared local object, specifically so that
// clicking star and then immediately saving a note can't race two
// independent read-modify-write POSTs against the same row and have the
// slower one silently clobber the faster one's change.
let modalAnnotation = null;

async function saveModalAnnotation(fingerprint, patch) {
  modalAnnotation = { ...modalAnnotation, ...patch };
  await api(`/api/decks/${fingerprint}/annotation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(modalAnnotation),
  });
  return modalAnnotation;
}

async function openDeckDetail(fingerprint) {
  const d = await api(`/api/decks/${fingerprint}`);
  window.__currentDetailFingerprint = fingerprint;
  modalAnnotation = { ...d.annotation };
  $("#modal-body").innerHTML = renderDeckDetail(d);
  $("#detail-modal").classList.remove("hidden");

  $("#detail-notes-save").addEventListener("click", async () => {
    await saveModalAnnotation(fingerprint, { notes: $("#detail-notes").value });
    $("#detail-notes-save").textContent = "Saved";
    setTimeout(() => { $("#detail-notes-save").textContent = "Save note"; }, 1200);
  });
}

function renderDeckDetail(d) {
  const score = d.score;
  const combos = d.combos;
  const contributions = (d.feature_contributions || []).map((entry) => `
    <div class="detail-section">
      <h4>P(bracket &ge; ${entry.threshold})</h4>
      <table class="contrib-table">
        ${entry.contributions.map((c) => `
          <tr>
            <td>${c.feature}${c.sentinel ? " (sentinel)" : ""}</td>
            <td>value=${c.value.toFixed(2)}</td>
            <td>coef=${c.coefficient.toFixed(3)}</td>
            <td class="${c.contribution >= 0 ? "contrib-pos" : "contrib-neg"}">
              ${c.contribution >= 0 ? "+" : ""}${c.contribution.toFixed(3)}
            </td>
          </tr>
        `).join("")}
      </table>
    </div>
  `).join("");

  return `
    <h2>${escapeHtml(d.commander_name)}</h2>
    <p class="hint"><a href="${d.source_url}" target="_blank" rel="noopener">${d.source_url}</a> (${d.source})</p>

    <div class="detail-section">
      <h4>Cost / ownership</h4>
      <div class="detail-row"><span>% owned</span><span>${fmtPct(d.cost.pct_owned)}</span></div>
      <div class="detail-row"><span>Cost to complete (estimate)</span><span>${fmtMoney(d.cost.usd_to_complete)}</span></div>
      ${d.cost.missing_no_price ? `<div class="detail-row"><span>Missing cards with no price data</span><span>${d.cost.missing_no_price}</span></div>` : ""}
    </div>

    <div class="detail-section">
      <h4>Rules-based bracket floor</h4>
      <div class="detail-row"><span>Floor</span><span>${d.bracket_floor.floor}</span></div>
      <div class="detail-row"><span>Game Changers</span><span>${d.bracket_floor.game_changer_count}</span></div>
      <div class="detail-row"><span>Mass land denial</span><span>${d.bracket_floor.has_mass_land_denial ? "yes" : "no"}</span></div>
    </div>

    <div class="detail-section">
      <h4>Combos</h4>
      <div class="detail-row"><span>Detected</span><span>${combos.combo_count}</span></div>
      ${combos.top_combo_variant_id ? `
        <div class="detail-row"><span>Top combo</span><span>${combos.top_combo_pieces}pc, ${combos.top_combo_result}</span></div>
        <div class="detail-row"><span>Assembly turn (median / p25)</span><span>${combos.median_assembly_turn ?? "never"} / ${combos.p25_assembly_turn ?? "-"}</span></div>
        <div class="detail-row"><span>Mana model</span><span>${combos.mana_model_version}</span></div>
      ` : `<p class="hint">No combo detected -- see known limitation: zero-combo decks are structurally underrated.</p>`}
    </div>

    <div class="detail-section">
      <h4>Other feel signals</h4>
      <div class="detail-row"><span>Tutors</span><span>${d.feel_signals.tutor_count}</span></div>
      <div class="detail-row"><span>Interaction</span><span>${d.feel_signals.interaction_count}</span></div>
      <div class="detail-row"><span>Ramp</span><span>${d.feel_signals.ramp_count}</span></div>
      <div class="detail-row"><span>Fast mana</span><span>${d.feel_signals.fast_mana_count}</span></div>
      <div class="detail-row"><span>Avg mana value</span><span>${d.feel_signals.avg_mana_value}</span></div>
    </div>

    ${score ? `
      <div class="detail-section">
        <h4>Calibrated score</h4>
        <div class="detail-row"><span>Declared bracket</span><span>${score.declared_bracket_raw ?? "none (unlabeled source)"}${score.label_conflict ? " (sandbagged, corrected for training)" : ""}</span></div>
        <div class="detail-row"><span>feel_score</span><span>${score.feel_score.toFixed(2)}</span></div>
        <div class="detail-row"><span>feel_bracket</span><span>${score.feel_bracket}</span></div>
        <div class="detail-row"><span>confidence</span><span>${fmtPct(score.confidence)}</span></div>
        ${score.low_confidence_reason ? `<p class="hint">&#9888; ${score.low_confidence_reason}</p>` : ""}
      </div>
      ${contributions}
    ` : ""}

    <div class="detail-section">
      <h4>Your review</h4>
      <button class="star-btn ${d.annotation.starred ? "active" : ""}" id="detail-star">★ starred</button>
      <button class="reject-btn ${d.annotation.rejected ? "active" : ""}" id="detail-reject">✕ rejected</button>
      <textarea class="notes-box" id="detail-notes" placeholder="Notes after eyeballing the decklist...">${d.annotation.notes || ""}</textarea>
      <button class="primary" id="detail-notes-save" style="margin-top:8px">Save note</button>
    </div>
  `;
}

function initModal() {
  $("#modal-close").addEventListener("click", () => $("#detail-modal").classList.add("hidden"));
  $("#detail-modal").addEventListener("click", (e) => {
    if (e.target.id === "detail-modal") $("#detail-modal").classList.add("hidden");
  });
  document.addEventListener("click", async (e) => {
    if (e.target.id === "detail-star" || e.target.id === "detail-reject") {
      const field = e.target.id === "detail-star" ? "starred" : "rejected";
      const fp = window.__currentDetailFingerprint;
      if (!fp || !modalAnnotation) return;
      await saveModalAnnotation(fp, { [field]: !modalAnnotation[field] });
      e.target.classList.toggle("active", modalAnnotation[field]);
    }
  });
}

// ---------- deep pull ----------

function initPullForm() {
  $("#pull-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const commander = $("#pull-commander").value.trim();
    const target = Number($("#pull-target").value);
    const button = e.target.querySelector("button");
    button.disabled = true;
    try {
      const { job_id } = await api("/api/jobs/pull_commander", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ commander, target }),
      });
      await pollJob(job_id, "#pull-job");
      await refreshCorpusBadge();
    } finally {
      button.disabled = false;
    }
  });
}

// ---------- pipeline control ----------

function initPipelineButtons() {
  $all(".stage-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const { job_id } = await api("/api/jobs/run_stage", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stage: btn.dataset.stage }),
        });
        await pollJob(job_id, null); // job history panel below shows it
        await loadJobHistory();
        await refreshCorpusBadge();
      } finally {
        btn.disabled = false;
      }
    });
  });
}

function renderJobEntry(job) {
  const statusClass = job.status;
  return `
    <div class="job-entry" data-job-id="${job.id}">
      <div class="job-title">
        <strong>${escapeHtml(job.name)}</strong>
        <span class="job-status ${statusClass}">${job.status}</span>
      </div>
      <div class="job-log">${job.log.map(escapeHtml).join("\n")}${job.progress_line ? "\n" + escapeHtml(job.progress_line) : ""}</div>
      ${job.error ? `<p class="hint" style="color:var(--bad)">${escapeHtml(job.error)}</p>` : ""}
      ${job.result ? `<p class="hint">result: ${escapeHtml(JSON.stringify(job.result))}</p>` : ""}
    </div>
  `;
}

async function pollJob(jobId, targetSelector) {
  return new Promise((resolve) => {
    const tick = async () => {
      const job = await api(`/api/jobs/${jobId}`);
      if (targetSelector) $(targetSelector).innerHTML = renderJobEntry(job);
      const entryInHistory = $(`.job-entry[data-job-id="${jobId}"]`);
      if (entryInHistory) entryInHistory.outerHTML = renderJobEntry(job);
      if (job.status === "running") {
        setTimeout(tick, 1000);
      } else {
        resolve(job);
      }
    };
    tick();
  });
}

async function loadJobHistory() {
  const jobs = await api("/api/jobs");
  $("#job-history").innerHTML = jobs.length
    ? jobs.map(renderJobEntry).join("")
    : `<p class="hint">No jobs run yet this session.</p>`;
  // keep polling any still-running jobs so the panel updates live
  jobs.filter((j) => j.status === "running").forEach((j) => pollJob(j.id, null));
}

// ---------- dashboard ----------

function gateBadge(pass) {
  if (pass === null || pass === undefined) return `<span class="hint">skipped</span>`;
  return pass ? `<span class="gate-pass">PASS</span>` : `<span class="gate-fail">FAIL</span>`;
}

async function loadDashboard() {
  const summary = await api("/api/summary");
  const run = summary.latest_calibration_run;
  const price = summary.price_crosscheck;
  const spellbook = summary.spellbook_crosscheck;

  const sourceRows = Object.entries(summary.source_counts)
    .sort((a, b) => b[1] - a[1])
    .map(([src, n]) => `<div class="detail-row"><span>${src}</span><span>${n}</span></div>`)
    .join("");

  const importancesHtml = run && run.feature_importances
    ? run.feature_importances.map((entry) => `
        <div class="detail-section">
          <h4>P(bracket &ge; ${entry.threshold})</h4>
          <table class="contrib-table">
            ${entry.coefficients.slice(0, 5).map(([name, coef]) => `
              <tr><td>${name}</td><td class="${coef >= 0 ? "contrib-pos" : "contrib-neg"}">${coef >= 0 ? "+" : ""}${coef.toFixed(3)}</td></tr>
            `).join("")}
          </table>
        </div>
      `).join("")
    : `<p class="hint">Run Stage 4 (Calibrate) to populate this.</p>`;

  $("#dashboard-content").innerHTML = `
    <div class="dash-grid">
      <div class="dash-card">
        <h4>Corpus (${summary.total_decks} decks)</h4>
        ${sourceRows}
        <p class="hint" style="margin-top:8px">A large biased sample, not a census.</p>
      </div>

      <div class="dash-card">
        <h4>Calibration accuracy</h4>
        ${run ? `
          <div class="dash-stat">${(run.test_exact_match * 100).toFixed(1)}%</div>
          <div class="dash-sub">test exact-match (${(run.test_within_one * 100).toFixed(1)}% within one bracket)</div>
          <div class="detail-row"><span>Labeled decks used</span><span>${run.n_labeled}</span></div>
          <div class="detail-row"><span>Label conflict (sandbagging) rate</span><span>${fmtPct(run.label_conflict_rate)}</span></div>
        ` : `<p class="hint">Run Stage 4 (Calibrate) first.</p>`}
      </div>

      <div class="dash-card">
        <h4>Sanity gates</h4>
        ${run ? `
          <div class="detail-row"><span>Precons score 1-2</span><span>${gateBadge(run.precon_gate_pass)} (${fmtPct(run.precon_gate_rate)})</span></div>
          <div class="detail-row"><span>EDHTop16 decks score 5</span><span>${gateBadge(run.cedh_gate_pass)} (${fmtPct(run.cedh_gate_rate)})</span></div>
        ` : `<p class="hint">Run Stage 4 (Calibrate) first.</p>`}
      </div>

      <div class="dash-card">
        <h4>Archidekt price cross-check</h4>
        ${price ? `
          <div class="dash-stat">${price.mean_ratio.toFixed(2)}x</div>
          <div class="dash-sub">mean (our missing-cost / their whole-deck price), ${price.n} Archidekt decks</div>
          <div class="detail-row"><span>Decks where ours exceeds theirs</span><span>${price.n_over_one}</span></div>
        ` : `<p class="hint">Run the price cross-check from the Pipeline tab.</p>`}
      </div>

      <div class="dash-card">
        <h4>Spellbook /estimate-bracket cross-check</h4>
        ${spellbook ? `
          <div class="dash-stat">${fmtPct(spellbook.n_agree / spellbook.n_comparable)}</div>
          <div class="dash-sub">agreement on ${spellbook.n_comparable}/${spellbook.n} comparable decks</div>
        ` : `<p class="hint">Run the Spellbook cross-check from the Pipeline tab.</p>`}
      </div>

      <div class="dash-card" style="grid-column: span 2">
        <h4>Feature importances (last calibration run)</h4>
        ${importancesHtml}
      </div>
    </div>
  `;
}

// ---------- init ----------

async function init() {
  initTabs();
  initFilterForm();
  initCommanderAutocomplete();
  initModal();
  initPullForm();
  initPipelineButtons();
  await initSourceFilter();
  await refreshCorpusBadge();
  await runSearch();
}

document.addEventListener("DOMContentLoaded", init);
