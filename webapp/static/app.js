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

  $("#detail-stress-test-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Running 2000 simulations...";
    try {
      const report = await api(`/api/decks/${fingerprint}/stress_test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n_simulations: 2000 }),
      });
      $("#detail-stress-report").innerHTML = renderStressTestReport(report);
    } finally {
      e.target.disabled = false;
      e.target.textContent = "Run deep stress test";
    }
  });
}

// ---------- shared stress-test report rendering (modal + Simulate tab) ----------

function renderStressTestReport(report) {
  const comboRows = report.combo_stats.length
    ? report.combo_stats.map((c) => `
        <div class="sim-combo-line">
          <span class="hint">${c.piece_count}pc ${c.is_game_ender ? "game-ending" : (c.is_infinite ? "infinite" : "value")}${c.card_names && c.card_names.length ? `: ${escapeHtml(c.card_names.join(" + "))}` : ""}</span>
          <span>
            median ${c.median_turn ?? "-"} / p25 ${c.p25_turn ?? "-"} / p75 ${c.p75_turn ?? "-"}
            &mdash; never assembled in ${fmtPct(c.never_rate)} of games
          </span>
        </div>
      `).join("")
    : `<p class="hint">No detected combos to track.</p>`;

  return `
    <div class="stress-report">
      <h4>Stress test (${report.n_simulations} simulations, v2 color-aware mana model)</h4>
      <div class="detail-row"><span>Mulligan rate</span><span>${fmtPct(report.mulligan_rate)} (avg ${report.avg_mulligans_taken.toFixed(2)} mulligans/game)</span></div>
      <div class="detail-row"><span>First castable spell (median / p75)</span><span>turn ${report.first_spell_turn_median ?? "-"} / turn ${report.first_spell_turn_p75 ?? "-"}</span></div>
      <div class="detail-row"><span>Color-screw rate (through turn 6)</span><span>${fmtPct(report.color_screw_game_rate)}</span></div>
      <h4 style="margin-top:12px">Combo assembly distributions</h4>
      ${comboRows}
    </div>
  `;
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
      <button class="primary" id="detail-stress-test-btn" style="margin-top:8px">Run deep stress test</button>
      <div id="detail-stress-report"></div>
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

// ---------- simulate tab ----------

const simSlots = { you: null, opp1: null, opp2: null, opp3: null };
let simSearchDebounce = null;

function initImportDeckForm() {
  $("#import-deck-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#import-deck-url").value.trim();
    if (!url) return;
    const button = e.target.querySelector("button");
    button.disabled = true;
    try {
      const { job_id } = await api("/api/jobs/import_deck", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const job = await pollJob(job_id, "#import-deck-job");
      if (job.status === "completed" && job.result && job.result.fingerprint) {
        selectSimDeck("you", {
          fingerprint: job.result.fingerprint,
          commander_name: job.result.commander_name,
          source: "archidekt",
        });
        $("#import-deck-url").value = "";
      }
    } finally {
      button.disabled = false;
    }
  });
}

function renderSimPreview(p) {
  if (!p) return "";
  const bracket = p.feel_bracket != null
    ? `<span class="pill bracket-${p.feel_bracket}">B${p.feel_bracket}</span>`
    : `<span class="hint">no model trained</span>`;
  const freshNote = p.signals_computed_fresh
    ? `<p class="hint">facts computed on the spot -- this deck hasn't been through a full Analyze run</p>`
    : "";
  const comboLines = p.combos.length
    ? p.combos.map((c) => `
        <div class="sim-combo-line">
          <span class="hint">${c.piece_count}pc ${c.is_game_ender ? "game-ending" : (c.is_infinite ? "infinite" : "value")}</span>
          <span>${escapeHtml(c.card_names.join(" + "))}</span>
        </div>
      `).join("")
    : `<p class="hint">No detected combos.</p>`;

  return `
    <div class="detail-row"><span>Bracket</span><span>${bracket}</span></div>
    <div class="detail-row"><span>Cost to complete</span><span>${fmtMoney(p.usd_to_complete)} (${fmtPct(p.pct_owned)} owned)</span></div>
    <div class="detail-row"><span>Game Changers</span><span>${p.game_changer_count}</span></div>
    <div class="detail-row"><span>Combos detected</span><span>${p.combo_count}</span></div>
    ${comboLines}
    ${freshNote}
  `;
}

async function selectSimDeck(slot, deck) {
  simSlots[slot] = deck;
  const picker = $(`.sim-picker[data-slot="${slot}"]`);
  picker.querySelector(".sim-search-results").innerHTML = "";
  picker.querySelector(".sim-search").value = "";
  picker.querySelector(".sim-selected").innerHTML = `
    <div class="sim-chip">
      <span>${escapeHtml(deck.commander_name)} <span class="hint">(${deck.source})</span></span>
      <button type="button" class="sim-chip-clear">&times;</button>
    </div>
  `;
  picker.querySelector(".sim-chip-clear").addEventListener("click", () => {
    simSlots[slot] = null;
    picker.querySelector(".sim-selected").innerHTML = "";
    picker.querySelector(".sim-preview").innerHTML = "";
  });

  const previewEl = picker.querySelector(".sim-preview");
  previewEl.innerHTML = `<p class="hint">loading deck facts...</p>`;
  try {
    const preview = await api(`/api/decks/${deck.fingerprint}/preview`);
    if (simSlots[slot] && simSlots[slot].fingerprint === deck.fingerprint) {
      previewEl.innerHTML = renderSimPreview(preview);
    }
  } catch (err) {
    previewEl.innerHTML = `<p class="hint">could not load deck facts: ${escapeHtml(err.message)}</p>`;
  }
}

function initSimPickers() {
  $all(".sim-picker").forEach((picker) => {
    const slot = picker.dataset.slot;
    const input = picker.querySelector(".sim-search");
    const results = picker.querySelector(".sim-search-results");
    input.addEventListener("input", () => {
      clearTimeout(simSearchDebounce);
      const q = input.value.trim();
      if (!q) { results.innerHTML = ""; return; }
      simSearchDebounce = setTimeout(async () => {
        const decks = await api(`/api/decks/search?q=${encodeURIComponent(q)}`);
        results.innerHTML = decks.map((d, i) => `
          <div class="sim-search-result" data-idx="${i}">
            ${escapeHtml(d.commander_name)} <span class="hint">(${d.source})</span>
          </div>
        `).join("") || `<div class="hint" style="padding:6px">no matches</div>`;
        Array.from(results.querySelectorAll(".sim-search-result")).forEach((row, i) => {
          row.addEventListener("click", () => selectSimDeck(slot, decks[i]));
        });
      }, 200);
    });
  });
}

function renderTableSimReport(report) {
  const rows = report.players.map((p) => `
    <div class="detail-row">
      <span>${escapeHtml(p.commander_name)}${p.fingerprint === report.players[0].fingerprint ? " (you)" : ""}</span>
      <span>won the race in ${fmtPct(report.win_race_rate[p.fingerprint] || 0)} of table-games</span>
    </div>
  `).join("");

  return `
    <div class="stress-report">
      <h4>Table simulation (${report.n_simulations} games per deck)</h4>
      <div class="detail-row"><span>Your raw combo-assembly rate</span><span>${fmtPct(report.your_raw_combo_rate)}</span></div>
      <div class="detail-row"><span>Estimated disruption chance per attempt</span><span>${fmtPct(report.your_disruption_chance)}</span></div>
      <div class="detail-row"><span>Your adjusted (post-disruption) combo rate</span><span>${fmtPct(report.your_adjusted_combo_rate)}</span></div>
      <h4 style="margin-top:12px">Race results</h4>
      ${rows}
      <p class="hint" style="margin-top:10px">
        Not a real interactive simulator: each deck's game was simulated independently, with no
        stack, targeting, or blocking. "Won the race" means this deck's fastest detected combo
        assembled earliest among the four in a given paired game. The disruption chance is a
        single rough estimate from the table's average interaction density, not a prediction of
        when or how an opponent would actually respond.
      </p>
    </div>
  `;
}

function initSimButtons() {
  $("#run-stress-test-btn").addEventListener("click", async (e) => {
    if (!simSlots.you) {
      $("#sim-report").innerHTML = `<p class="hint">Select "Your deck" first.</p>`;
      return;
    }
    e.target.disabled = true;
    try {
      const report = await api(`/api/decks/${simSlots.you.fingerprint}/stress_test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n_simulations: 2000 }),
      });
      $("#sim-report").innerHTML = renderStressTestReport(report);
    } finally {
      e.target.disabled = false;
    }
  });

  $("#run-table-sim-btn").addEventListener("click", async (e) => {
    const fingerprints = ["you", "opp1", "opp2", "opp3"]
      .map((s) => simSlots[s])
      .filter(Boolean)
      .map((d) => d.fingerprint);
    if (fingerprints.length < 2) {
      $("#sim-report").innerHTML = `<p class="hint">Select your deck plus at least one opponent.</p>`;
      return;
    }
    e.target.disabled = true;
    try {
      const report = await api("/api/table_sim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fingerprints, n_simulations: 1500 }),
      });
      $("#sim-report").innerHTML = renderTableSimReport(report);
    } finally {
      e.target.disabled = false;
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
  initImportDeckForm();
  initSimPickers();
  initSimButtons();
  initPipelineButtons();
  await initSourceFilter();
  await refreshCorpusBadge();
  await runSearch();
}

document.addEventListener("DOMContentLoaded", init);
