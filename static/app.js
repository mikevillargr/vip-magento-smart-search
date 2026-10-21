// ---------------------------------------------------------------------------
// Smart search prototype — compares today's site search, Claude Haiku and TypeSafe Jev.
// Everything shown to users is in plain terms: accuracy, speed, cost.
// ---------------------------------------------------------------------------
const ENGINES = [
  { id: "baseline", name: "Today's site search", who: "current Magento search", color: "var(--c-baseline)" },
  { id: "haiku", name: "Claude Haiku", who: "AI by Anthropic", color: "var(--c-haiku)" },
  { id: "jev", name: "TypeSafe Jev", who: "AI by TypeSafe", color: "var(--c-jev)" },
];
const EXAMPLES = [
  "99 ford bronco oil filter", "1999 bronco filter", "2022 bronco air filter", "f150 2019 floor mats",
  "jl wrangler wipers", "'15 ram 1500 tonneau", "silverdo 2020 mud flaps", "2021 raptor grill",
  "chevy floor mats", "4105409AE",
];
const IMG_BASE = "https://d3j7mquu4xakgo.cloudfront.net/production/catalog/product";
const PLACEHOLDER = "data:image/svg+xml;utf8," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#f2f2f2"/><path d="M18 40l9-11 7 8 5-6 7 9z" fill="#ccc"/><circle cx="40" cy="24" r="4" fill="#ccc"/></svg>');
const DEBOUNCE_MS = 250;
const SEARCH_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="10.5" cy="10.5" r="7"/><path d="M16 16l5 5"/></svg>';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const secs = (ms) => (ms == null ? "—" : ms < 100 ? "under 0.1 s" : (ms / 1000).toFixed(ms < 1000 ? 2 : 1) + " s");
const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
const per1k = (usd) => (usd == null ? "—" : usd === 0 ? "$0" : "$" + (usd * 1000).toFixed(usd * 1000 < 1 ? 3 : 2));
const fmtX = (x) => (x >= 10 ? Math.round(x) : x.toFixed(1));

let ymm = null;
let acSeq = 0;
let searchSeq = 0;
let acTimer = null;
// Type-ahead calls made since the last full search, per engine (they are part of that search's cost).
let typing = freshTyping();
const scores = Object.fromEntries(ENGINES.map((e) => [e.id, { searches: 0, fit: 0, shown: 0, resultMs: [], suggestMs: [], cost: [] }]));

function freshTyping() {
  return Object.fromEntries(ENGINES.map((e) => [e.id, { calls: 0, cost: 0, tokens: 0, out: 0, ms: [], byQuery: {} }]));
}
function opts() {
  const p = new URLSearchParams({ fastpath: $("#opt-fastpath").checked, cache: $("#opt-cache").checked });
  if (ymm) { p.set("year", ymm.year); p.set("make", ymm.make); p.set("model", ymm.model); }
  return p;
}

// ---------- boot ----------
(async function boot() {
  $("#examples").innerHTML = EXAMPLES.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  $("#examples").addEventListener("click", (e) => { const q = e.target.dataset?.q; if (q) { $("#q").value = q; runSearch(q); } });

  const input = $("#q");
  input.addEventListener("input", () => {
    clearTimeout(acTimer);
    const q = input.value.trim();
    if (q.length < 2) { closeAc(); return; }
    acTimer = setTimeout(() => suggest(q), DEBOUNCE_MS);
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAc(); });
  input.addEventListener("focus", () => { if ($("#ac").innerHTML && input.value.trim().length >= 2) openAc(); });
  $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); const q = input.value.trim(); if (q) runSearch(q); });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search-wrap")) closeAc(); });
  $("#ac").addEventListener("click", (e) => {
    if (e.target.closest("#ac-go")) { const q = input.value.trim(); if (q) runSearch(q); return; }
    const el = e.target.closest("[data-go]");
    if (el) { input.value = el.dataset.go; runSearch(el.dataset.go); }
  });

  const status = await (await fetch("api/status")).json();
  $("#key-status").innerHTML = Object.entries(status).map(([k, v]) =>
    `${k === "haiku" ? "Claude Haiku" : "TypeSafe Jev"}: ${v === "ready" ? "connected" : `<span class="bad">${esc(v)}</span>`}`).join(" · ");
  initPicker(await (await fetch("api/ymm")).json());

  const params = new URLSearchParams(location.search);
  if (params.get("type")) typeOut(params.get("type"));
  else if (params.get("q")) { input.value = params.get("q"); runSearch(params.get("q")); }
})();

// Simulated typing (?type=...), handy for demos and screenshots.
async function typeOut(text) {
  const input = $("#q");
  input.focus();
  for (const ch of text) {
    input.value += ch;
    input.dispatchEvent(new Event("input"));
    await new Promise((r) => setTimeout(r, 90));
  }
}

// ---------- Year / Make / Model ----------
function initPicker(tree) {
  const ys = $("#ymm-year"), ms = $("#ymm-make"), md = $("#ymm-model");
  ys.innerHTML += Object.keys(tree).sort((a, b) => b - a).map((y) => `<option>${y}</option>`).join("");
  ys.onchange = () => {
    ms.innerHTML = '<option value="">Select Make</option>' + Object.keys(tree[ys.value] || {}).map((m) => `<option>${esc(m)}</option>`).join("");
    ms.disabled = !ys.value; md.disabled = true; md.innerHTML = '<option value="">Select Model</option>';
    setYmm(null);
  };
  ms.onchange = () => {
    md.innerHTML = '<option value="">Select Model</option>' + (tree[ys.value]?.[ms.value] || []).map((m) => `<option>${esc(m)}</option>`).join("");
    md.disabled = !ms.value; setYmm(null);
  };
  md.onchange = () => setYmm(md.value ? { year: ys.value, make: ms.value, model: md.value } : null);
  $("#ymm-clear").onclick = () => { ys.value = ""; ys.onchange(); };
}
function setYmm(v) {
  ymm = v;
  $("#ymm-clear").hidden = !v;
  const q = $("#q").value.trim();
  if (v && q && !$("#results").hidden) runSearch(q);
}

// ---------- type-ahead ----------
function openAc() { $("#ac").hidden = false; $("#q").setAttribute("aria-expanded", "true"); }
function closeAc() { $("#ac").hidden = true; $("#q").setAttribute("aria-expanded", "false"); }

async function suggest(q) {
  const seq = ++acSeq;
  const ac = $("#ac");
  if (!ac.querySelector(".ac-cols")) {
    ac.innerHTML = `<div class="ac-head"><span>Suggestions from each search, side by side</span><button type="button" class="ac-go" id="ac-go">See full comparison — accuracy, speed &amp; cost ↵</button></div>
      <div class="ac-cols">${ENGINES.map((e) => `
        <div class="ac-col" id="ac-${e.id}" style="--engine:${e.color}">
          <div class="ac-colhead"><b>${esc(e.name)}</b><span class="ac-time wait">…</span></div>
          <div class="ac-body"></div>
        </div>`).join("")}</div>`;
  }
  openAc();
  const t0 = performance.now();
  for (const e of ENGINES) {
    const t = $(`#ac-${e.id} .ac-time`);
    t.className = "ac-time wait";
    t.textContent = "thinking…";
  }
  await Promise.all(ENGINES.map(async (e) => {
    let d;
    try { d = await (await fetch(`api/suggest?engine=${e.id}&q=${encodeURIComponent(q)}&${opts()}`)).json(); }
    catch { return; }
    const ms = performance.now() - t0;
    // Every call counts toward cost, even if the shopper has typed more since.
    if (!d.cached) {
      const t = typing[e.id];
      const calls = d.fast_path || e.id === "baseline" || d.error ? 0 : 1;
      t.calls += calls;
      const tok = d.usage?.input_tokens || 0;
      t.cost += d.usage?.cost_usd || 0;
      t.tokens += tok;
      t.out += d.usage?.output_tokens || 0;
      t.ms.push(ms);
      const prev = t.byQuery[q] || { calls: 0, cost: 0, tokens: 0 };
      t.byQuery[q] = { calls: prev.calls + calls, cost: prev.cost + (d.usage?.cost_usd || 0), tokens: prev.tokens + tok,
        out: (prev.out || 0) + (d.usage?.output_tokens || 0) };
    }
    if (seq === acSeq && !$("#ac").hidden) renderAcCol(e, d, ms);
  }));
}

function renderAcCol(e, d, ms) {
  const col = $(`#ac-${e.id}`);
  const t = col.querySelector(".ac-time");
  t.className = "ac-time";
  t.textContent = secs(ms);
  const parts = [];
  if (d.error) parts.push(`<div class="ac-note">AI unavailable — showing today's search instead</div>`);
  if (d.notes?.length) parts.push(`<div class="ac-note">${esc(d.notes[0])}</div>`);
  if (d.suggestions?.length) {
    parts.push(`<div class="ac-section">Suggested searches</div>`);
    parts.push(d.suggestions.map((s) => `<div class="ac-sugg" data-go="${esc(s.query)}">${SEARCH_ICON}<span>${esc(s.label)}</span><span class="n">${s.count} part${s.count === 1 ? "" : "s"}</span></div>`).join(""));
  }
  if (d.results?.length) {
    parts.push(`<div class="ac-section">Products</div>`);
    parts.push(d.results.map((r) => `
      <div class="ac-prod" data-go="${esc(r.sku)}">
        <img src="${esc(imgUrl(r.image))}" onerror="this.onerror=null;this.src='${PLACEHOLDER}'" alt="" loading="lazy" />
        <div><div class="ac-name">${esc(r.name)}</div>
          <div class="ac-meta"><span class="ac-price">$${r.price.toFixed(2)}</span>${r.fit_label ? `<span class="fit fit-fits">${esc(r.fit_label)}</span>` : fitTag(r.fit)}</div></div>
      </div>`).join(""));
  } else {
    parts.push(`<div class="ac-empty">No suggestions</div>`);
  }
  const f = fitCounts(d.results || []);
  const fitline = e.id === "baseline" && f.vehicle && f.bad ? `${f.bad} of ${f.shown} don't fit` : "";
  parts.push(`<div class="ac-foot"><span>See all ${d.total ?? 0} results</span><span class="fitline">${fitline}</span></div>`);
  col.querySelector(".ac-body").innerHTML = parts.join("");
}

// ---------- full search ----------
async function runSearch(q) {
  clearTimeout(acTimer);
  acSeq++; // ignore late suggestion responses
  closeAc();
  const id = ++searchSeq;
  const typed = typing;
  typing = freshTyping();
  // A lookup for exactly the final text is the same work as the final search: count it once.
  for (const e of ENGINES) {
    const dup = typed[e.id].byQuery[q];
    if (dup) { typed[e.id].calls -= dup.calls; typed[e.id].cost -= dup.cost; typed[e.id].tokens -= dup.tokens; typed[e.id].out -= dup.out || 0; }
  }
  history.replaceState(null, "", `${location.pathname}?q=${encodeURIComponent(q)}`);
  $("#hero").hidden = true;
  $("#results").hidden = $("#scoreboard").hidden = false;
  $("#verdict").innerHTML = `<h2>Searching for “${esc(q)}”…</h2>`;
  $("#columns").innerHTML = ENGINES.map(colShell).join("");

  const p = opts();
  p.set("rerank", false);
  const t0 = performance.now();
  const timer = setInterval(() => {
    for (const v of document.querySelectorAll(".kpi-speed .v.running")) v.textContent = secs(performance.now() - t0);
  }, 50);

  const runs = await Promise.all(ENGINES.map(async (e) => {
    let d;
    try { d = await (await fetch(`api/search?engine=${e.id}&q=${encodeURIComponent(q)}&${p}`)).json(); }
    catch (err) { d = { error: String(err), results: [], usage: {}, timings_ms: {} }; }
    const run = { e, d, ms: performance.now() - t0, typed: typed[e.id] };
    run.cost = (d.cached ? 0 : d.usage?.cost_usd || 0) + typed[e.id].cost;
    run.tokens = (d.cached ? 0 : d.usage?.input_tokens || 0) + typed[e.id].tokens;
    run.out = (d.cached ? 0 : d.usage?.output_tokens || 0) + typed[e.id].out;
    run.fit = fitCounts(d.results || []);
    if (id === searchSeq) renderCol(run);
    return run;
  }));
  clearInterval(timer);
  if (id !== searchSeq) return;
  renderVerdict(q, runs);
  record(runs);
}

function colShell(e) {
  return `
  <article class="col" id="col-${e.id}" style="--engine:${e.color}">
    <div class="col-head">
      <div class="col-title"><h3>${esc(e.name)}</h3><span class="who">${esc(e.who)}</span></div>
      <div class="kpis">
        <div class="kpi kpi-fit"><div class="k">Accuracy</div><div class="v running">…</div><div class="s"></div></div>
        <div class="kpi kpi-speed"><div class="k">Speed</div><div class="v running">0 s</div><div class="s"></div></div>
        <div class="kpi kpi-cost"><div class="k">Cost</div><div class="v running">…</div><div class="s"></div></div>
      </div>
      <div class="extra"></div>
    </div>
    <div class="col-body">${'<div class="skeleton"></div>'.repeat(4)}</div>
  </article>`;
}

function renderCol({ e, d, ms, typed, cost, tokens, out, fit }) {
  const col = $(`#col-${e.id}`);
  const set = (cls, v, s, tone) => {
    const k = col.querySelector(`.${cls}`);
    k.className = `kpi ${cls} ${tone || ""}`;
    k.querySelector(".v").className = "v";
    k.querySelector(".v").textContent = v;
    k.querySelector(".s").textContent = s;
  };
  if (!fit.vehicle) set("kpi-fit", "—", "no vehicle in search");
  else set("kpi-fit", `${fit.good} of ${fit.shown}`, fit.bad ? `${fit.bad} don't fit your vehicle` : "all fit your vehicle",
    fit.bad === 0 ? "good" : fit.bad >= fit.shown / 2 ? "bad" : "warn");
  const sug = median(typed.ms);
  set("kpi-speed", secs(ms), sug != null ? `suggestions: ${secs(sug)}` : "to show results");
  if (e.id === "baseline") set("kpi-cost", "$0", "no AI cost");
  else set("kpi-cost", per1k(cost), `per 1,000 searches · this search: ${tokens.toLocaleString()} tokens in, ${out.toLocaleString()} out${e.id === "jev" ? " (free)" : ""}${typed.calls ? ", incl. typing" : ""}`);

  const extra = [];
  if (d.error) extra.push(`<span class="tag-err" title="${esc(d.error)}">AI unavailable — showing today's search instead</span>`);
  if (d.fast_path) extra.push(`<span class="tag-fast">Simple search — answered without AI</span>`);
  if (e.id !== "baseline" && !d.error) {
    const p = d.parsed || {};
    const bits = [];
    if (p.year) bits.push(`<b>${p.year}</b>`);
    if (p.vehicles?.length) bits.push(`<b>${esc(vehicleText(p.vehicles))}</b>`);
    if (p.partnames?.length) bits.push(`<b>${esc(p.partnames.slice(0, 2).join(" or "))}</b>`);
    else if (p.sku) bits.push(`part number <b>${esc(p.sku)}</b>`);
    else if (p.keywords) bits.push(`“${esc(p.keywords)}”`);
    extra.push(`<div class="understood">Understood as: ${bits.join(" · ") || "—"}</div>`);
  } else if (e.id === "baseline") {
    extra.push(`<div class="understood">Matches words only — doesn't know which vehicle you mean</div>`);
  }
  col.querySelector(".extra").innerHTML = extra.join("");

  const body = [];
  if (d.notes?.length) body.push(`<div class="notes">${d.notes.map(esc).join("<br>")}</div>`);
  if (!d.results?.length) body.push(`<div class="empty">No results</div>`);
  else body.push(`<ul class="items">${d.results.slice(0, 10).map(card).join("")}</ul>`);
  body.push(techDetails(d, typed));
  col.querySelector(".col-body").innerHTML = body.join("");
}

function card(r) {
  return `
  <li class="card">
    <img class="thumb" loading="lazy" src="${esc(imgUrl(r.image))}" onerror="this.onerror=null;this.src='${PLACEHOLDER}'" alt="" />
    <div>
      <div class="card-name">${esc(r.name)}</div>
      <div class="card-sub">Part # ${esc(r.sku)}</div>
      <div class="card-row"><span class="price">$${r.price.toFixed(2)}</span>${fitTag(r.fit, r.ymm_fitment)}</div>
    </div>
  </li>`;
}

function techDetails(d, typed) {
  const t = d.timings_ms || {}, u = d.usage || {};
  const row = (k, v) => `<tr><td>${k}</td><td>${v}</td></tr>`;
  return `<details class="tech"><summary>Technical details</summary><table>
    ${row("Reading the search (code)", `${t.normalize ?? 0} ms`)}
    ${row("AI call", `${t.interpret_network ?? 0} ms`)}
    ${row("Finding parts", `${t.retrieve ?? 0} ms`)}
    ${t.rerank ? row("AI re-ordering", `${t.rerank} ms`) : ""}
    ${row("Server total", `${t.total ?? 0} ms`)}
    ${row("AI tokens in / out", `${u.input_tokens || 0} / ${u.output_tokens || 0}${u.cached_tokens ? ` (${u.cached_tokens} cached)` : ""}`)}
    ${row("AI cost: search + typing", `$${(u.cost_usd || 0).toFixed(6)} + $${typed.cost.toFixed(6)} (${typed.calls} lookups)`)}
    ${d.request_id ? row("Request id", esc(d.request_id)) : ""}
    ${d.error ? row("Error", esc(d.error)) : ""}
  </table></details>`;
}

// ---------- plain-language verdict ----------
function renderVerdict(q, runs) {
  const by = Object.fromEntries(runs.map((r) => [r.e.id, r]));
  const ai = runs.filter((r) => r.e.id !== "baseline" && !r.d.error);
  const lines = [];
  const veh = ai.find((r) => r.d.parsed?.vehicles?.length);
  const vlabel = veh ? `${veh.d.parsed.year ? veh.d.parsed.year + " " : ""}${vehicleText(veh.d.parsed.vehicles)}` : null;

  const nat = by.baseline;
  if (nat?.fit.vehicle) {
    const aiBad = ai.map((r) => `${r.e.name} ${r.fit.bad}`).join(", ");
    lines.push(nat.fit.bad
      ? `<b>Accuracy:</b> today's site search showed <b>${nat.fit.bad} of ${nat.fit.shown} parts that don't fit</b> ${vlabel ? "a " + esc(vlabel) : "your vehicle"}. Parts that don't fit with AI: ${esc(aiBad)}.`
      : `<b>Accuracy:</b> all three showed parts that fit ${vlabel ? "a " + esc(vlabel) : "your vehicle"}.`);
  } else if (ai.length) {
    lines.push(`<b>Accuracy:</b> no vehicle in this search, so results can't be checked for fit.`);
  }
  const h = by.haiku, j = by.jev;
  if (h && j && !h.d.error && !j.d.error) {
    lines.push(`<b>Speed:</b> ${compare(j, h, (r) => r.ms, "faster", secs)}`);
    lines.push(`<b>Cost:</b> ${compare(j, h, (r) => r.cost, "cheaper", (v) => per1k(v) + " per 1,000 searches")}`);
  }
  for (const r of runs) if (r.d.error && r.e.id !== "baseline") lines.push(`<b>${esc(r.e.name)}</b> was unavailable, so it showed today's search results instead.`);
  for (const r of runs) if (r.d.fast_path) lines.push(`<b>${esc(r.e.name)}</b> didn't need the AI for this simple search (free and instant).`);
  if (h && j && !h.d.error && !j.d.error && !h.d.fast_path && !j.d.fast_path && understoodKey(h.d.parsed) !== understoodKey(j.d.parsed)) {
    lines.push(`The two AIs read this search differently — compare “Understood as” below.`);
  }
  $("#verdict").innerHTML = `<h2>“${esc(q)}”</h2><ul>${lines.map((l) => `<li>${l}</li>`).join("")}</ul>`;
}

// "TypeSafe Jev was 7× faster than Claude Haiku (0.30 s vs 2.1 s)"
function compare(a, b, f, better, fmt) {
  const va = f(a), vb = f(b);
  if (!va && !vb) return `both free for this search.`;
  const [win, lose] = va <= vb ? [a, b] : [b, a];
  const ratio = f(lose) / Math.max(f(win), 1e-9);
  if (ratio < 1.15) return `about the same — ${esc(a.e.name)} ${fmt(va)}, ${esc(b.e.name)} ${fmt(vb)}.`;
  if (!f(win)) return `<b>${esc(win.e.name)} cost nothing</b> for this search vs ${fmt(f(lose))} for ${esc(lose.e.name)}.`;
  return `<b>${esc(win.e.name)} was ${fmtX(ratio)}× ${better}</b> than ${esc(lose.e.name)} (${fmt(f(win))} vs ${fmt(f(lose))}).`;
}

// ---------- scoreboard ----------
function record(runs) {
  for (const r of runs) {
    if (r.d.cached) continue;
    const s = scores[r.e.id];
    s.searches++;
    s.resultMs.push(r.ms);
    s.suggestMs.push(...r.typed.ms);
    s.cost.push(r.cost);
    if (r.fit.vehicle) { s.fit += r.fit.good; s.shown += r.fit.shown; }
  }
  const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
  $("#score-body").innerHTML = ENGINES.map((e) => {
    const s = scores[e.id];
    return `<tr><td><i style="background:${e.color}"></i>${esc(e.name)}</td><td>${s.searches}</td>
      <td>${s.shown ? Math.round((100 * s.fit) / s.shown) + "%" : "—"}</td>
      <td>${secs(median(s.suggestMs))}</td><td>${secs(median(s.resultMs))}</td>
      <td>${s.resultMs.length ? secs(Math.max(...s.resultMs)) : "—"}</td>
      <td>${e.id === "baseline" ? "$0" : per1k(avg(s.cost))}</td></tr>`;
  }).join("");

  const h = scores.haiku, j = scores.jev, n = scores.baseline;
  if (!h.searches || !j.searches) return;
  const sp = median(h.resultMs) / median(j.resultMs);
  const co = avg(h.cost) / Math.max(avg(j.cost), 1e-12);
  const speed = sp >= 1.15 ? `TypeSafe Jev has been <b>${fmtX(sp)}× faster</b>` : sp <= 0.87 ? `Claude Haiku has been <b>${fmtX(1 / sp)}× faster</b>` : `both AIs have been about as fast`;
  const cost = co >= 1.15 ? `<b>${fmtX(co)}× cheaper</b> (Jev)` : co <= 0.87 ? `<b>${fmtX(1 / co)}× cheaper</b> (Haiku)` : `about the same cost`;
  const pct = (s) => (s.shown ? Math.round((100 * s.fit) / s.shown) + "%" : "—");
  $("#score-sentence").innerHTML = `So far ${speed}, at ${cost}. Parts that fit the vehicle searched: Jev ${pct(j)}, Haiku ${pct(h)}, today's site search ${pct(n)}.`;
}

// ---------- helpers ----------
function fitCounts(results) {
  const top = results.slice(0, 10);
  const vehicle = top.some((r) => r.fit !== "n/a");
  const good = top.filter((r) => r.fit === "fits" || r.fit === "universal").length;
  const bad = top.filter((r) => r.fit === "unverified").length;
  return { vehicle, shown: top.length, good, bad, other: top.length - good - bad };
}
function fitTag(fit, title = "") {
  const label = { fits: "Fits", universal: "Fits all", other_years: "Other years", unverified: "Doesn't fit" }[fit];
  return label ? `<span class="fit fit-${fit}" title="${esc(title)}">${label}</span>` : "";
}
function imgUrl(path) { return path ? IMG_BASE + (path.startsWith("/") ? "" : "/") + path : PLACEHOLDER; }
function vehicleText(vs) {
  if (vs.length > 3 && new Set(vs.map((v) => v[0])).size === 1) return `${vs[0][0]} (any model)`;
  return vs.slice(0, 2).map((v) => v.join(" ")).join(" or ") + (vs.length > 2 ? " …" : "");
}
function understoodKey(p) {
  return JSON.stringify([p?.year ?? null, (p?.vehicles || []).map((v) => v.join(" ")).sort(), (p?.partnames || []).slice().sort()]);
}
