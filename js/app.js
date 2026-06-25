const METRIC_KEYS = [
  "deathScore",
  "crudScore",
  "aiWrapperScore",
  "moatDepth",
  "markdownReplaceable",
  "pricingAudacity",
];

const METRIC_LABELS = {
  deathScore: "Death Score",
  crudScore: "It's Just CRUD",
  aiWrapperScore: "Secret AI Wrapper",
  moatDepth: "Moat Depth",
  markdownReplaceable: "Markdown Replaceable",
  pricingAudacity: "Pricing Audacity",
};

const BATCH_ORDER = {
  Winter: 0,
  Spring: 1,
  Summer: 2,
  Fall: 3,
};

let dashboard = null;
let chart = null;
let selectedBatches = new Set();
let saasFilter = "all";
let selectedMetric = "deathScore";

function scoreColor(score) {
  if (score >= 65) return "#ff2d2d";
  if (score >= 40) return "#ffb800";
  return "#00ff41";
}

function batchSortKey(batch) {
  const [season, year] = batch.split(" ");
  return [Number(year), BATCH_ORDER[season] ?? 9];
}

function sortBatches(batches) {
  return [...batches].sort((a, b) => {
    const ka = batchSortKey(a);
    const kb = batchSortKey(b);
    return ka[0] - kb[0] || ka[1] - kb[1];
  });
}

function getMetricValue(company, metricKey) {
  const death = company.death || {};
  if (metricKey === "deathScore") return death.deathScore;
  return (death.metrics || {})[metricKey];
}

function filterCompanies() {
  if (!dashboard) return [];
  return dashboard.companies.filter((c) => {
    if (c.scrapeStatus !== "ok") return false;
    if (saasFilter !== "all" && c.saasTag !== saasFilter) return false;
    if (selectedBatches.size && !selectedBatches.has(c.batch)) return false;
    return true;
  });
}

function computeChartData(companies, metricKey) {
  const byBatch = {};
  for (const c of companies) {
    byBatch[c.batch] = byBatch[c.batch] || [];
    byBatch[c.batch].push(c);
  }
  const labels = sortBatches(Object.keys(byBatch));
  const values = labels.map((batch) => {
    const vals = byBatch[batch]
      .map((c) => getMetricValue(c, metricKey))
      .filter((v) => v != null);
    if (!vals.length) return null;
    return Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10;
  });
  return { labels, values };
}

function renderProgressBanner() {
  const el = document.getElementById("progress-banner");
  const s = dashboard.summary;
  const m = dashboard.manifest?.batches || {};
  const partial = Object.values(m).filter((b) => b.scraped > 0 && !b.complete).length;

  if (s.batchesComplete >= s.batchesTotal) {
    el.textContent = `Complete — ${s.scrapedCompanies} companies scraped`;
    el.classList.add("complete");
  } else {
    el.textContent = `${s.batchesComplete}/${s.batchesTotal} batches complete · ${s.scrapedCompanies} companies · ${partial} in progress`;
  }
}

function renderMetricSelect() {
  const sel = document.getElementById("metric-select");
  sel.innerHTML = "";
  for (const key of METRIC_KEYS) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = METRIC_LABELS[key];
    sel.appendChild(opt);
  }
  sel.value = selectedMetric;
  sel.onchange = () => {
    selectedMetric = sel.value;
    updateChart();
  };
}

function renderBatchChips() {
  const wrap = document.getElementById("batch-chips");
  wrap.innerHTML = "";
  const batches = sortBatches(dashboard.batches || []);
  const manifest = dashboard.manifest?.batches || {};

  for (const batch of batches) {
    const slug = batch.toLowerCase().replace(" ", "-");
    const info = manifest[slug] || {};
    const btn = document.createElement("button");
    btn.textContent = batch;
    btn.dataset.batch = batch;
    if (info.complete) btn.classList.add("active");
    else if (info.scraped > 0) btn.classList.add("partial");
    else btn.classList.add("pending");

    if (selectedBatches.has(batch) || (!selectedBatches.size && info.scraped > 0)) {
      btn.classList.add("active");
      selectedBatches.add(batch);
    }

    btn.onclick = () => {
      if (btn.classList.contains("pending")) return;
      if (selectedBatches.has(batch)) {
        selectedBatches.delete(batch);
        btn.classList.remove("active");
      } else {
        selectedBatches.add(batch);
        btn.classList.add("active");
      }
      if (!selectedBatches.size) {
        for (const b of batches) {
          const s = batch.toLowerCase().replace(" ", "-");
          if ((manifest[s] || {}).scraped > 0) selectedBatches.add(b);
        }
      }
      renderAll();
    };
    wrap.appendChild(btn);
  }
}

function renderSaasFilter() {
  document.querySelectorAll("#saas-filter button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.filter === saasFilter);
    btn.onclick = () => {
      saasFilter = btn.dataset.filter;
      renderSaasFilter();
      renderAll();
    };
  });
}

function updateChart() {
  const companies = filterCompanies();
  const { labels, values } = computeChartData(companies, selectedMetric);
  const color = selectedMetric === "deathScore" ? "#ff2d2d" : "#ffb800";

  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets[0].label = METRIC_LABELS[selectedMetric];
    chart.data.datasets[0].data = values;
    chart.data.datasets[0].borderColor = color;
    chart.data.datasets[0].backgroundColor = color + "33";
    chart.update();
    return;
  }

  const ctx = document.getElementById("cohort-chart");
  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: METRIC_LABELS[selectedMetric],
        data: values,
        borderColor: color,
        backgroundColor: color + "33",
        fill: true,
        tension: 0.25,
        pointRadius: 4,
        pointHoverRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#888", font: { family: "JetBrains Mono" } } },
      },
      scales: {
        x: {
          ticks: { color: "#666", font: { size: 10 } },
          grid: { color: "#1a1a1a" },
        },
        y: {
          min: 0,
          max: 100,
          ticks: { color: "#666" },
          grid: { color: "#1a1a1a" },
          title: {
            display: true,
            text: "Mean score",
            color: "#666",
          },
        },
      },
    },
  });
}

function renderCards() {
  const grid = document.getElementById("cards-grid");
  const companies = filterCompanies().sort((a, b) => {
    const sa = getMetricValue(a, "deathScore") ?? -1;
    const sb = getMetricValue(b, "deathScore") ?? -1;
    return sb - sa;
  });

  document.getElementById("company-count").textContent = `${companies.length} companies`;

  if (!companies.length) {
    grid.innerHTML = '<div class="empty-state">No scraped companies for this filter yet. Run the scraper for more batches.</div>';
    return;
  }

  grid.innerHTML = companies.map((c, idx) => renderCard(c, idx)).join("");
  grid.querySelectorAll(".card").forEach((el) => {
    el.addEventListener("click", () => openModal(companies[Number(el.dataset.idx)]));
  });
}

function renderCard(c, idx) {
  const death = c.death || {};
  const score = death.deathScore ?? 0;
  const color = scoreColor(score);

  const metrics = METRIC_KEYS.filter((k) => k !== "deathScore").map((key) => {
    const val = getMetricValue(c, key) ?? 0;
    return `
      <div class="metric-row">
        <div class="metric-label"><span>${METRIC_LABELS[key]}</span><span style="color:${scoreColor(val)}">${val}</span></div>
        <div class="metric-bar"><div class="metric-fill" style="width:${val}%;background:${scoreColor(val)}"></div></div>
      </div>`;
  }).join("");

  const tagClass = c.saasTag === "saasLikely" ? "tag-saas" : c.saasTag === "nonSaas" ? "tag-nonsaas" : "tag-unknown";
  const tagLabel = c.saasTag === "saasLikely" ? "SaaS-likely" : c.saasTag === "nonSaas" ? "Non-SaaS" : "Unknown";

  return `
    <article class="card" data-idx="${idx}">
      <div class="card-head">
        <div>
          <h3>${escapeHtml(c.name)}</h3>
          <div class="card-domain">${escapeHtml(c.domain || "")}</div>
        </div>
        <div class="score-ring">
          <div class="score-value" style="color:${color}">${score}</div>
          <div class="score-rating" style="color:${color}">${escapeHtml(death.deathRating || "")}</div>
        </div>
      </div>
      <div class="card-batch">${escapeHtml(c.batch)}</div>
      <p class="card-quote">"${escapeHtml(death.oneLiner || "")}"</p>
      ${metrics}
      <span class="tag ${tagClass}">${tagLabel}</span>
    </article>`;
}

function openModal(c) {
  const death = c.death || {};
  const modal = document.getElementById("detail-modal");
  const content = document.getElementById("modal-content");
  const score = death.deathScore ?? 0;

  content.innerHTML = `
    <h2>${escapeHtml(death.companyName || c.name)}</h2>
    <div class="modal-domain">${escapeHtml(c.domain || "")}</div>
    <div class="score-ring" style="margin:1rem 0">
      <div class="score-value" style="color:${scoreColor(score)};font-size:2.5rem">${score}</div>
      <div class="score-rating" style="color:${scoreColor(score)}">${escapeHtml(death.deathRating || "")}</div>
    </div>
    <p class="card-quote">"${escapeHtml(death.oneLiner || "")}"</p>

    <div class="modal-section">
      <h4>Vulnerability metrics</h4>
      ${METRIC_KEYS.filter((k) => k !== "deathScore").map((key) => {
        const val = getMetricValue(c, key) ?? 0;
        return `<div class="metric-row"><div class="metric-label"><span>${METRIC_LABELS[key]}</span><span style="color:${scoreColor(val)}">${val}</span></div><div class="metric-bar"><div class="metric-fill" style="width:${val}%;background:${scoreColor(val)}"></div></div></div>`;
      }).join("")}
    </div>

    <div class="modal-section">
      <h4>Cause of death</h4>
      <p style="color:#ff2d2d">${escapeHtml(death.causeOfDeath || "")}</p>
    </div>

    <div class="modal-section">
      <h4>Time until death</h4>
      <p style="color:#ffb800">${escapeHtml(death.timeUntilDeath || "")}</p>
    </div>

    <div class="modal-section">
      <h4>Eulogy</h4>
      <p><em>${escapeHtml(death.eulogy || "")}</em></p>
    </div>

    <div class="modal-section">
      <h4>Last words</h4>
      <p><em>${escapeHtml(death.lastWords || "")}</em></p>
    </div>

    <div class="modal-section">
      <h4>What Claude would say</h4>
      <p>${escapeHtml(death.whatClaudeWouldSay || "")}</p>
    </div>

    <div class="modal-section">
      <h4>Replacement file (SKILL.md)</h4>
      <pre>${escapeHtml(death.skillMdFile || "")}</pre>
    </div>

    <a class="modal-link" href="https://deathbyclawd.com?url=${encodeURIComponent(c.domain || "")}" target="_blank" rel="noopener">View on deathbyclawd.com →</a>
  `;

  modal.showModal();
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderAll() {
  renderProgressBanner();
  if (chart) {
    chart.destroy();
    chart = null;
  }
  updateChart();
  renderCards();
}

async function init() {
  try {
    const resp = await fetch("data/dashboard.json");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    dashboard = await resp.json();
    selectedBatches = new Set();
    renderMetricSelect();
    renderBatchChips();
    renderSaasFilter();
    renderAll();
  } catch (err) {
    document.getElementById("progress-banner").textContent = `Failed to load data: ${err.message}`;
    document.getElementById("cards-grid").innerHTML =
      '<div class="empty-state">Run scripts/fetch_yc.py then scripts/scrape_death.py --batch "Fall 2026"</div>';
  }

  document.getElementById("modal-close").onclick = () => document.getElementById("detail-modal").close();
  document.getElementById("detail-modal").onclick = (e) => {
    if (e.target.id === "detail-modal") e.target.close();
  };
}

init();
