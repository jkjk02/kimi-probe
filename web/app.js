/* kimi-probe front-end: configuration, SSE streaming, filtering and rendering. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { meta: null, controller: null, results: {}, order: [], filter: "all", onlyIssues: false, summary: null };
  const STATUS_LABEL = { pass: "通过", warn: "可疑", fail: "失败", error: "异常", info: "信息", skip: "跳过", running: "运行中", pending: "等待" };
  const STATUS_ICON = { pass: "i-check", warn: "i-alert", fail: "i-x", error: "i-x", info: "i-info", skip: "i-minus", running: "i-loader", pending: "i-minus" };
  const STORE_KEY = "kimi-probe-config-v2";
  const RING = 188.5;  // 2π×30 for r=30 ring in new layout

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (id, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
  const PASS_RE = /^(与官方一致|符合|符合预期|完全正确|通过|正常|可用|是$|收到|回传被接受|召回成功|严格符合 schema|合法 JSON|续写未重复前缀|引用了工具结果|官方域名|官方风格|与请求一致|存在$|自述为 Kimi)/;
  const WARN_RE = /^(轻微偏差|未引用|部分|无官方基准|命中 \d|多次|非官方域名|未明确)/;
  const FAIL_RE = /^(明显偏差|被接受（官方会拒绝）|缺少|不符合|非合法|召回失败|未收到|否$|JSON 非法|请求失败|官方应报错|自述为其他|与请求 .* 不一致|多个 id|关闭思考后)/;
  const cell = (v) => {
    const s = String(v ?? "");
    if (PASS_RE.test(s)) return `<span class="tag pass">${icon("i-check")}${esc(s)}</span>`;
    if (FAIL_RE.test(s)) return `<span class="tag fail">${icon("i-x")}${esc(s)}</span>`;
    if (WARN_RE.test(s)) return `<span class="tag warn">${icon("i-alert")}${esc(s)}</span>`;
    return esc(s);
  };
  const isIssue = (st) => ["warn", "fail", "error"].includes(st);

  let toastTimer;
  function toast(msg, ok = false) {
    const el = $("toast");
    el.textContent = msg;
    el.className = `toast show${ok ? " ok" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
  }
  function setConn(cls, text) { const c = $("conn"); c.className = `conn ${cls}`; c.querySelector(".conn-text").textContent = text; }

  const currentModel = () => ($("model_custom").value.trim() || $("model_select").value);
  function loadConfig() {
    try {
      const c = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      if (c.base_url) $("base_url").value = c.base_url;
      if (c.model) { $("model_select").value = c.model; if ($("model_select").value !== c.model) $("model_custom").value = c.model; }
      if (c.ref_base) $("ref_base").value = c.ref_base;
      if (c.probes) document.querySelectorAll(".probe-item input").forEach((el) => (el.checked = c.probes.includes(el.value)));
    } catch { /* ignore */ }
  }
  function saveConfig() {
    const probes = [...document.querySelectorAll(".probe-item input:checked")].map((e) => e.value);
    localStorage.setItem(STORE_KEY, JSON.stringify({ base_url: $("base_url").value, model: currentModel(), ref_base: $("ref_base").value, probes }));
  }

  // ------------------------------------------------------------------- init
  async function init() {
    const meta = await (await fetch("/api/meta")).json();
    state.meta = meta;
    $("version").textContent = `v${meta.version}`;
    $("base_url").placeholder = meta.default_base_url;
    $("model_select").innerHTML = meta.models.map((m) => `<option value="${m}">${m}</option>`).join("");
    // New inline pill-style probe list
    $("probe-list").innerHTML = meta.probes.map((p) => `
      <label class="probe-item" title="${esc(p.reference)}">
        <input type="checkbox" value="${p.id}" ${p.default_on ? "checked" : ""}>
        <span class="pname">${esc(p.name)}</span>
        <span class="pdesc">${esc(p.description)}</span>
        <span class="pmeta"><span class="pcat">${esc(p.category)}</span><span class="cost ${p.cost}" title="token 消耗：${p.cost}"><i></i><i></i><i></i></span></span>
      </label>`).join("");
    $("probe-count").textContent = meta.probes.length;
    loadConfig();
    $("baseline-hint").textContent = meta.baselines.length ? `已内置官方分词基准：${meta.baselines.join("、")}` : "未找到分词基准文件；测中转站时请填写参考官方 Key。";
    setConn("ok", "服务就绪");

    // Config drawer toggle
    $("btn-config").onclick = () => {
      const drawer = $("config-drawer");
      const open = drawer.getAttribute("aria-hidden") === "false";
      drawer.setAttribute("aria-hidden", open ? "true" : "false");
      $("btn-config").setAttribute("aria-expanded", open ? "false" : "true");
    };
    // Close drawer on run
    const closeDrawer = () => {
      $("config-drawer").setAttribute("aria-hidden", "true");
      $("btn-config").setAttribute("aria-expanded", "false");
    };
    $("btn-run").addEventListener("click", closeDrawer, { once: false });
  }

  // 第三方/中转站预设：跳过 endpoint（需要官方响应头），保留其余含 params（测中转是否静默接受官方会拒绝的参数）
  const RELAY_PROBES = new Set(["identity","tokenizer","hidden_prompt","cache","latency","streaming","thinking","params","vision","video","tool_call","structured"]);
  $("sel-relay").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = RELAY_PROBES.has(e.value)));
  $("sel-all").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = true));
  $("sel-none").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = false));
  $("sel-default").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = !!state.meta.probes.find((p) => p.id === e.value)?.default_on));
  $("toggle-key").onclick = (ev) => {
    const inp = $("api_key"); const show = inp.type === "password";
    inp.type = show ? "text" : "password"; ev.currentTarget.setAttribute("aria-pressed", String(show));
  };
  $("api_key").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });

  // -------------------------------------------------------------------- run
  $("btn-run").onclick = run;
  $("btn-stop").onclick = () => state.controller?.abort();

  function options() {
    return {
      reasoning_effort: $("opt_effort").value,
      k26_thinking: $("opt_k26").value,
      latency_runs: Number($("opt_latency_runs").value) || 3,
      cache_rounds: Number($("opt_cache_rounds").value) || 3,
      needle_k_tokens: Number($("opt_needle").value) || 32,
      video_upload: $("opt_video_upload").checked,
      web_query: $("opt_web_query").value.trim() || null,
      custom_text: $("opt_custom_text") ? $("opt_custom_text").value : "",
    };
  }

  // 规范化 Base URL：去掉末尾斜杠，若路径中没有 /vN 则自动补 /v1
  function normalizeBaseUrl(raw) {
    if (!raw) return raw;
    let u = raw.trim().replace(/\/+$/, "");
    // 仅在路径里完全没有版本段时补充 /v1（e.g. go-kimi.com → go-kimi.com/v1）
    try {
      const parsed = new URL(u.startsWith("http") ? u : "https://" + u);
      if (!/\/v\d+(\/|$)/.test(parsed.pathname)) u = u + "/v1";
    } catch { /* 非法 URL，原样传给后端报错 */ }
    return u;
  }

  async function run() {
    if (state.controller) return;
    const apiKey = $("api_key").value.trim();
    if (!apiKey) { toast("请先填写 API Key"); $("api_key").focus(); return; }
    const probes = [...document.querySelectorAll(".probe-item input:checked")].map((e) => e.value);
    if (!probes.length) { toast("请至少选择一个检测项目"); return; }
    saveConfig();
    const effectiveBaseUrl = normalizeBaseUrl($("base_url").value.trim()) || state.meta.default_base_url;
    // 若自动补全了路径，把规范化结果回填输入框，避免用户下次再忘
    if ($("base_url").value.trim() && effectiveBaseUrl !== $("base_url").value.trim()) {
      $("base_url").value = effectiveBaseUrl;
    }
    state.results = {}; state.order = probes; state.summary = null;
    $("results").innerHTML = ""; $("log").textContent = ""; $("log-count").textContent = "";
    $("empty").classList.add("hidden"); $("summary").classList.remove("hidden"); $("toolbar").classList.remove("hidden");
    $("btn-report").classList.add("hidden"); $("btn-copy").classList.add("hidden");
    $("score").textContent = "–"; $("ring-fg").style.strokeDashoffset = RING; $("ring-fg").style.stroke = "var(--info)";
    $("verdict").textContent = "运行中…"; $("verdict").className = "verdict"; $("counts").innerHTML = "";
    $("summary-meta").textContent = `${currentModel()} @ ${effectiveBaseUrl}`;
    setProgress(0, probes.length);
    for (const id of probes) renderCard({ id, name: state.meta.probes.find((p) => p.id === id)?.name || id, category: state.meta.probes.find((p) => p.id === id)?.category || "", status: "pending" });
    renderFilters();
    $("btn-run").disabled = true; $("btn-stop").disabled = false; setConn("busy", "检测中");
    state.controller = new AbortController();
    const body = {
      base_url: effectiveBaseUrl,
      api_key: apiKey, model: currentModel(), probes, options: options(),
      reference_api_key: $("ref_key").value.trim() || null,
      reference_base_url: $("ref_base").value.trim() || null,
    };
    try {
      const resp = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: state.controller.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      await readSSE(resp.body, onEvent);
      setConn("ok", "服务就绪");
    } catch (err) {
      if (err.name !== "AbortError") { log(`错误：${err.message}`); toast(`运行出错：${err.message}`); $("verdict").textContent = "运行出错"; $("verdict").className = "verdict bad"; setConn("err", "出错"); }
      else { log("已停止"); $("verdict").textContent = "已停止"; setConn("ok", "服务就绪"); }
    } finally {
      $("btn-run").disabled = false; $("btn-stop").disabled = true; state.controller = null;
    }
  }

  async function readSSE(stream, handler) {
    const reader = stream.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
        let event = "message", data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) handler(event, JSON.parse(data));
      }
    }
  }

  function onEvent(event, data) {
    if (event === "log") log(`[${data.ts}] ${data.msg}`);
    else if (event === "start") log(`开始：模型 ${data.model}，${data.probes.length} 个探针${data.baseline ? `，基准 ${data.baseline.file}` : ""}`);
    else if (event === "probe_start") { renderCard({ ...cardMeta(data.id), status: "running" }); $("progress-text").textContent = `正在执行：${data.name}`; }
    else if (event === "probe_result") { state.results[data.id] = data; renderCard(data); setProgress(Object.keys(state.results).length, state.order.length); renderFilters(); }
    else if (event === "done") renderSummary(data.summary, data.report_file);
  }
  const cardMeta = (id) => { const p = state.meta.probes.find((x) => x.id === id) || {}; return { id, name: p.name || id, category: p.category || "" }; };

  function log(msg) {
    const el = $("log"); el.textContent += msg + "\n"; el.scrollTop = el.scrollHeight;
    $("log-count").textContent = el.textContent.split("\n").length - 1;
  }
  function setProgress(done, total) {
    $("progress-bar").style.width = total ? `${(done / total) * 100}%` : "0";
    if (done >= total) $("progress-text").textContent = `完成 ${done}/${total}`;
    else $("progress-text").textContent = `${done}/${total}`;
  }

  // ---------------------------------------------------------------- render
  function renderCard(r) {
    let card = document.getElementById(`card-${r.id}`);
    if (!card) { card = document.createElement("article"); card.id = `card-${r.id}`; $("results").appendChild(card); }
    const st = r.status || "pending";
    const done = !["pending", "running"].includes(st);
    const wasCollapsed = card.classList.contains("collapsed");
    card.className = `card ${st}${done && wasCollapsed ? " collapsed" : ""}`;
    card.dataset.category = r.category || "";
    card.dataset.status = st;
    const table = r.columns?.length ? `<div class="table-wrap"><table><thead><tr>${r.columns.map((c) => `<th scope="col">${esc(c)}</th>`).join("")}</tr></thead><tbody>${(r.rows || []).map((row) => `<tr>${row.map((v) => `<td>${cell(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
    const notes = r.notes?.length ? `<ul class="notes">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
    const evidence = r.evidence && Object.keys(r.evidence).length ? `<details class="evidence"><summary>${icon("i-chevron")}原始证据 (evidence)</summary><pre>${esc(JSON.stringify(r.evidence, null, 2))}</pre></details>` : "";
    card.innerHTML = `
      <button class="card-head" type="button" aria-expanded="${!card.classList.contains("collapsed")}" ${done ? "" : "disabled"}>
        ${icon(STATUS_ICON[st], "ic status-ic")}
        <span class="lead"><span class="title">${esc(r.name)}</span><span class="cat">${esc(r.category || "")}</span>${done && r.summary ? `<span class="brief">${esc(r.summary)}</span>` : ""}</span>
        <span class="right">${r.duration_ms ? `<span class="dur">${(r.duration_ms / 1000).toFixed(1)} s</span>` : ""}<span class="badge ${st}">${STATUS_LABEL[st]}</span>${done ? icon("i-chevron", "ic chev") : ""}</span>
      </button>
      ${done ? `<div class="card-body">
        <div class="card-summary">${esc(r.summary || "")}</div>
        ${notes}${table}
        ${r.reference ? `<div class="reference">依据：${esc(r.reference)}</div>` : ""}
        ${evidence}
      </div>` : ""}`;
    const head = card.querySelector(".card-head");
    if (done) head.onclick = () => { card.classList.toggle("collapsed"); head.setAttribute("aria-expanded", String(!card.classList.contains("collapsed"))); };
    applyFilter();
  }

  function renderSummary(s, reportFile, quiet = false) {
    state.summary = s;
    $("score").textContent = s.score;
    const tone = s.hard_fails.length ? "bad" : (s.counts.fail || s.counts.warn) ? "warn" : "ok";
    $("verdict").textContent = s.verdict; $("verdict").className = `verdict ${tone}`;
    const ring = $("ring-fg");
    ring.style.stroke = tone === "ok" ? "var(--pass)" : tone === "warn" ? "var(--warn)" : "var(--fail)";
    requestAnimationFrame(() => (ring.style.strokeDashoffset = RING * (1 - s.score / 100)));
    $("counts").innerHTML = Object.entries(s.counts).filter(([, n]) => n).map(([k, n]) => `<span class="pill ${k}">${icon(STATUS_ICON[k])}${STATUS_LABEL[k]} ${n}</span>`).join("");
    if (reportFile) { const a = $("btn-report"); a.href = `/api/reports/${encodeURIComponent(reportFile)}`; a.classList.remove("hidden"); }
    $("btn-copy").classList.remove("hidden");
    if (!quiet) {
      log(`完成：得分 ${s.score}，判定「${s.verdict}」${s.hard_fails.length ? `，关键失败项：${s.hard_fails.join(", ")}` : ""}`);
      toast(`检测完成：${s.verdict}（${s.score} 分）`, tone === "ok");
    }
  }

  // --------------------------------------------------------------- filters
  function renderFilters() {
    const cats = new Map();
    for (const id of state.order) { const c = cardMeta(id).category; cats.set(c, (cats.get(c) || 0) + 1); }
    const chip = (key, label, n) => `<button type="button" class="chip" data-cat="${esc(key)}" aria-pressed="${state.filter === key}">${esc(label)}<span class="n">${n}</span></button>`;
    $("cat-filter").innerHTML = chip("all", "全部", state.order.length) + [...cats].map(([c, n]) => chip(c, c, n)).join("");
    $("cat-filter").querySelectorAll(".chip").forEach((b) => (b.onclick = () => { state.filter = b.dataset.cat; renderFilters(); applyFilter(); }));
  }
  function applyFilter() {
    document.querySelectorAll(".card").forEach((c) => {
      const catOk = state.filter === "all" || c.dataset.category === state.filter;
      const issueOk = !state.onlyIssues || isIssue(c.dataset.status) || !["pass", "skip", "info"].includes(c.dataset.status);
      c.classList.toggle("hidden", !(catOk && issueOk));
    });
  }
  $("only-issues").onchange = (e) => { state.onlyIssues = e.target.checked; applyFilter(); };
  $("collapse-all").onclick = () => document.querySelectorAll(".card .card-head:not(:disabled)").forEach((h) => { h.parentElement.classList.add("collapsed"); h.setAttribute("aria-expanded", "false"); });
  $("expand-all").onclick = () => document.querySelectorAll(".card").forEach((c) => { c.classList.remove("collapsed"); c.querySelector(".card-head")?.setAttribute("aria-expanded", "true"); });

  $("btn-copy").onclick = async () => {
    const s = state.summary; if (!s) return;
    const lines = [`kimi-probe · ${currentModel()} @ ${$("base_url").value || state.meta.default_base_url}`, `判定：${s.verdict}（${s.score}/100）`, ""];
    for (const id of state.order) { const r = state.results[id]; if (r) lines.push(`[${STATUS_LABEL[r.status]}] ${r.name}：${r.summary}`); }
    try { await navigator.clipboard.writeText(lines.join("\n")); toast("摘要已复制到剪贴板", true); } catch { toast("复制失败，浏览器未授权剪贴板"); }
  };

  // --------------------------------------------------------------- history
  function renderReport(report, file) {
    state.results = {}; state.order = []; state.summary = null;
    $("results").innerHTML = "";
    $("empty").classList.add("hidden");
    $("summary").classList.remove("hidden");
    $("toolbar").classList.remove("hidden");
    for (const r of report.results || []) { state.results[r.id] = r; state.order.push(r.id); renderCard(r); }
    $("summary-meta").textContent = `${report.model} @ ${report.base_url}`;
    $("progress-bar").style.width = "100%";
    $("progress-text").textContent = `历史报告 · ${(report.finished_at || "").replace("T", " ").slice(0, 19)}`;
    renderFilters();
    if (report.summary) renderSummary(report.summary, file, true);
  }

  async function loadReport(file) {
    try {
      const resp = await fetch(`/api/reports/${encodeURIComponent(file)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      renderReport(await resp.json(), file);
      toast(`已载入报告 ${file}`, true);
    } catch (e) { toast(`载入报告失败：${e.message}`); }
  }

  $("btn-history").onclick = async () => {
    const list = await (await fetch("/api/reports")).json();
    $("history-list").innerHTML = list.length ? list.map((r) => `
      <div class="hist-item">
        <div>
          <div>${esc(r.model)} · ${esc(r.summary?.verdict || "")} · <code>${esc(r.summary?.score ?? "-")}</code> 分</div>
          <div class="mono">${esc(r.base_url)} · ${esc((r.finished_at || "").replace("T", " ").slice(0, 19))}</div>
        </div>
        <div class="hist-actions">
          <button type="button" class="btn ghost small" data-load="${esc(r.file)}">载入</button>
          <a class="btn ghost small" href="/api/reports/${encodeURIComponent(r.file)}" target="_blank">${icon("i-external")}JSON</a>
        </div>
      </div>`).join("") : '<p class="help">暂无报告</p>';
    $("history-list").querySelectorAll("[data-load]").forEach((b) => (b.onclick = () => { $("history-dialog").close(); loadReport(b.dataset.load); }));
    $("history-dialog").showModal();
  };
  $("history-close").onclick = () => $("history-dialog").close();

  init()
    .then(() => { const f = new URLSearchParams(location.search).get("report"); if (f) return loadReport(f); })
    .catch((e) => { log(`初始化失败：${e.message}`); setConn("err", "服务不可用"); toast(`初始化失败：${e.message}`); });
})();
