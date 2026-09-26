/* kimi-probe front-end: configuration, SSE streaming and result rendering. */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { meta: null, controller: null, results: {}, order: [] };
  const STATUS_LABEL = { pass: "通过", warn: "可疑", fail: "失败", error: "异常", info: "信息", skip: "跳过", running: "运行中" };
  const STORE_KEY = "kimi-probe-config-v1";

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cell = (v) => {
    const s = String(v ?? "");
    if (/^(与官方一致|符合|符合预期|完全正确|通过|正常|可用|是|收到|回传被接受|召回成功|严格符合 schema|合法 JSON|续写未重复前缀|引用了工具结果)/.test(s)) return `<span class="tag pass">${esc(s)}</span>`;
    if (/^(轻微偏差|未引用|部分|无官方基准|命中 \d)/.test(s)) return `<span class="tag warn">${esc(s)}</span>`;
    if (/^(明显偏差|被接受（官方会拒绝）|缺少|不符合|非合法|召回失败|未收到|否$|JSON 非法|请求失败|官方应报错)/.test(s)) return `<span class="tag fail">${esc(s)}</span>`;
    return esc(s);
  };

  function loadConfig() {
    try {
      const c = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      if (c.base_url) $("base_url").value = c.base_url;
      if (c.model) { $("model_select").value = c.model; if ($("model_select").value !== c.model) $("model_custom").value = c.model; }
      if (c.ref_base) $("ref_base").value = c.ref_base;
      if (c.probes) for (const el of document.querySelectorAll(".probe-item input")) el.checked = c.probes.includes(el.value);
    } catch { /* ignore */ }
  }
  function saveConfig() {
    const probes = [...document.querySelectorAll(".probe-item input:checked")].map((e) => e.value);
    localStorage.setItem(STORE_KEY, JSON.stringify({ base_url: $("base_url").value, model: currentModel(), ref_base: $("ref_base").value, probes }));
  }
  const currentModel = () => ($("model_custom").value.trim() || $("model_select").value);

  // ------------------------------------------------------------------- init
  async function init() {
    const meta = await (await fetch("/api/meta")).json();
    state.meta = meta;
    $("version").textContent = `v${meta.version}`;
    $("base_url").placeholder = meta.default_base_url;
    $("model_select").innerHTML = meta.models.map((m) => `<option value="${m}">${m}</option>`).join("");
    $("probe-list").innerHTML = meta.probes.map((p) => `
      <label class="probe-item cost-${p.cost}" title="${esc(p.reference)}">
        <input type="checkbox" value="${p.id}" ${p.default_on ? "checked" : ""}>
        <span><div class="pname">${esc(p.name)}</div><div class="pdesc">${esc(p.description)}</div></span>
        <span class="pcat">${esc(p.category)}${p.cost === "high" ? " · 高消耗" : ""}</span>
      </label>`).join("");
    $("probe-count").textContent = `(${meta.probes.length})`;
    loadConfig();
    if (meta.baselines.length) {
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = `内置官方分词基准：${meta.baselines.join(", ")}`;
      $("probe-list").after(hint);
    }
  }

  $("sel-all").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = true));
  $("sel-none").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = false));
  $("sel-default").onclick = () => document.querySelectorAll(".probe-item input").forEach((e) => (e.checked = state.meta.probes.find((p) => p.id === e.value)?.default_on));

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
      custom_text: $("opt_custom_text").value,
    };
  }

  async function run() {
    const apiKey = $("api_key").value.trim();
    if (!apiKey) { alert("请填写 API Key"); return; }
    const probes = [...document.querySelectorAll(".probe-item input:checked")].map((e) => e.value);
    if (!probes.length) { alert("请至少选择一个检测项目"); return; }
    saveConfig();
    state.results = {}; state.order = probes;
    $("results").innerHTML = "";
    $("log").textContent = ""; $("log-count").textContent = "";
    $("empty").classList.add("hidden");
    $("summary").classList.remove("hidden");
    $("score").textContent = "–"; $("verdict").textContent = "运行中…"; $("counts").innerHTML = "";
    $("summary-meta").textContent = `${currentModel()} @ ${$("base_url").value || state.meta.default_base_url}`;
    for (const id of probes) renderCard({ id, name: state.meta.probes.find((p) => p.id === id)?.name || id, category: "", status: "pending" });
    $("btn-run").disabled = true; $("btn-stop").disabled = false;
    state.controller = new AbortController();
    const body = {
      base_url: $("base_url").value.trim() || state.meta.default_base_url,
      api_key: apiKey,
      model: currentModel(),
      probes,
      options: options(),
      reference_api_key: $("ref_key").value.trim() || null,
      reference_base_url: $("ref_base").value.trim() || null,
    };
    try {
      const resp = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: state.controller.signal });
      if (!resp.ok) { const t = await resp.text(); throw new Error(`HTTP ${resp.status}: ${t}`); }
      await readSSE(resp.body, onEvent);
    } catch (err) {
      if (err.name !== "AbortError") { log(`错误：${err.message}`); $("verdict").textContent = "运行出错"; }
      else { log("已停止"); $("verdict").textContent = "已停止"; }
    } finally {
      $("btn-run").disabled = false; $("btn-stop").disabled = true; state.controller = null;
    }
  }

  async function readSSE(stream, handler) {
    const reader = stream.getReader();
    const dec = new TextDecoder();
    let buf = "";
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
    else if (event === "probe_start") renderCard({ id: data.id, name: data.name, status: "running" });
    else if (event === "probe_result") { state.results[data.id] = data; renderCard(data); }
    else if (event === "done") renderSummary(data.summary, data.report_file);
  }

  function log(msg) {
    const el = $("log");
    el.textContent += msg + "\n";
    el.scrollTop = el.scrollHeight;
    $("log-count").textContent = `(${el.textContent.split("\n").length - 1})`;
  }

  // ---------------------------------------------------------------- render
  function renderCard(r) {
    let card = document.getElementById(`card-${r.id}`);
    if (!card) {
      card = document.createElement("div");
      card.id = `card-${r.id}`;
      $("results").appendChild(card);
    }
    const status = r.status === "pending" ? "skip" : r.status;
    card.className = `card ${status}`;
    const label = r.status === "pending" ? "等待" : STATUS_LABEL[r.status] || r.status;
    const table = r.columns?.length ? `<table><thead><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${(r.rows || []).map((row) => `<tr>${row.map((v) => `<td>${cell(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : "";
    const notes = r.notes?.length ? `<ul class="notes">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
    const evidence = r.evidence && Object.keys(r.evidence).length ? `<details class="evidence"><summary>原始证据 (evidence)</summary><pre>${esc(JSON.stringify(r.evidence, null, 2))}</pre></details>` : "";
    const open = r.status && !["pending", "running"].includes(r.status);
    card.innerHTML = `
      <div class="card-head" onclick="this.parentElement.classList.toggle('collapsed')">
        <div><span class="title">${esc(r.name)}</span><span class="cat">${esc(r.category || "")}</span></div>
        <div class="right">${r.duration_ms ? `<span>${(r.duration_ms / 1000).toFixed(1)} s</span>` : ""}<span class="badge ${r.status}">${label}</span></div>
      </div>
      ${open ? `<div class="card-body">
        <div class="card-summary"><b>${esc(r.summary || "")}</b></div>
        ${notes}${table}
        ${r.reference ? `<div class="reference">依据：${esc(r.reference)}</div>` : ""}
        ${evidence}
      </div>` : ""}`;
  }

  function renderSummary(s, reportFile) {
    $("score").textContent = s.score;
    $("verdict").textContent = s.verdict;
    $("verdict").style.color = s.hard_fails.length ? "var(--fail)" : s.counts.fail ? "var(--warn)" : s.counts.warn ? "var(--warn)" : "var(--pass)";
    $("counts").innerHTML = Object.entries(s.counts).filter(([, n]) => n).map(([k, n]) => `<span class="pill ${k}">${STATUS_LABEL[k]} ${n}</span>`).join("");
    if (reportFile) $("summary-meta").innerHTML += ` · <a href="/api/reports/${encodeURIComponent(reportFile)}" target="_blank">下载报告 JSON</a>`;
    log(`完成：得分 ${s.score}，判定「${s.verdict}」${s.hard_fails.length ? `，关键失败项：${s.hard_fails.join(", ")}` : ""}`);
  }

  // --------------------------------------------------------------- history
  $("btn-history").onclick = async () => {
    const list = await (await fetch("/api/reports")).json();
    $("history-list").innerHTML = list.length ? list.map((r) => `
      <div class="hist-item">
        <div><div>${esc(r.model)} · ${esc(r.summary?.verdict || "")} · 得分 ${esc(r.summary?.score ?? "-")}</div><div class="mono">${esc(r.base_url)} · ${esc((r.finished_at || "").replace("T", " ").slice(0, 19))}</div></div>
        <a href="/api/reports/${encodeURIComponent(r.file)}" target="_blank">打开</a>
      </div>`).join("") : '<p class="muted">暂无报告</p>';
    $("history-dialog").showModal();
  };
  $("history-close").onclick = () => $("history-dialog").close();

  init().catch((e) => log(`初始化失败：${e.message}`));
})();
