"""Latency, streaming behaviour and throughput probes."""

from __future__ import annotations

import statistics

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from .common import base_payload, usage_reasoning


@probe(
    "latency",
    "首字延迟与吞吐（TTFT / TPS）",
    "性能",
    "多次流式请求，统计 TTFB、首推理 token、首内容 token、总耗时与输出吞吐。",
    order=60,
    cost="medium",
    reference="Streaming: reasoning_content 先于 content 到达；stream_options.include_usage 返回末尾 usage 块。",
)
async def probe_latency(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["latency"])
    n = max(1, min(int(ctx.opt("latency_runs", 3)), 8))
    r.columns = ["轮次", "TTFB", "首推理 token", "首内容 token", "总耗时", "completion_tokens", "推理 tokens", "吞吐 (tok/s)", "finish"]
    prompt = "请写一段 200 字左右的短文，介绍量子计算与经典计算的主要区别。"
    ttfb, ttft, ttfc, total, tps = [], [], [], [], []
    pseudo_runs = 0
    for i in range(n):
        await ctx.log(f"延迟探针第 {i + 1}/{n} 轮（流式）")
        st = await ctx.client.chat_stream(base_payload(ctx, [{"role": "user", "content": prompt}], max_tokens=4096))
        if st.error:
            r.rows.append([i + 1, "-", "-", "-", "-", "-", "-", "-", short(st.error.get("message", ""), 60)])
            continue
        usage = st.usage or {}
        if st.ttfb_ms is not None:
            ttfb.append(st.ttfb_ms)
        if st.t_first_reasoning is not None:
            ttft.append(st.ms(st.t_first_reasoning))
        if st.ttfc_ms is not None:
            ttfc.append(st.ttfc_ms)
        if st.total_ms is not None:
            total.append(st.total_ms)
        if st.tokens_per_second:
            tps.append(st.tokens_per_second)
        if st.looks_pseudo_stream:
            pseudo_runs += 1
        rt = usage_reasoning(usage)
        r.rows.append([
            i + 1,
            f"{st.ttfb_ms:.0f} ms" if st.ttfb_ms is not None else "-",
            f"{st.ms(st.t_first_reasoning):.0f} ms" if st.t_first_reasoning is not None else "-",
            f"{st.ttfc_ms:.0f} ms" if st.ttfc_ms is not None else "-",
            f"{st.total_ms:.0f} ms" if st.total_ms is not None else "-",
            usage.get("completion_tokens", "-"),
            rt if rt is not None else (f"≈{len(st.reasoning)} 字符" if st.reasoning else "-"),
            st.tokens_per_second or "-",
            st.finish_reason or "-",
        ])
        r.evidence.setdefault("runs", []).append({
            "ttfb_ms": st.ttfb_ms,
            "ttft_ms": st.ttft_ms,
            "ttfc_ms": st.ttfc_ms,
            "total_ms": st.total_ms,
            "usage": usage,
            "chunks": len(st.chunks),
            "headers": st.headers,
        })
    if not total:
        r.status, r.summary = "fail", "所有流式请求均失败。"
        return r

    def med(xs: list[float]) -> float | None:
        return statistics.median(xs) if xs else None

    m_ttfb, m_ttft, m_ttfc, m_total, m_tps = med(ttfb), med(ttft), med(ttfc), med(total), med(tps)
    r.rows.append([
        "中位数",
        f"{m_ttfb:.0f} ms" if m_ttfb else "-",
        f"{m_ttft:.0f} ms" if m_ttft else "-",
        f"{m_ttfc:.0f} ms" if m_ttfc else "-",
        f"{m_total:.0f} ms" if m_total else "-",
        "-",
        "-",
        f"{m_tps:.1f}" if m_tps else "-",
        "-",
    ])
    r.evidence["median"] = {"ttfb_ms": m_ttfb, "ttft_ms": m_ttft, "ttfc_ms": m_ttfc, "total_ms": m_total, "tps": m_tps}
    first_token = m_ttft or m_ttfc
    if first_token is None:
        r.status, r.summary = "warn", "未收到任何 token 增量。"
    elif first_token <= 2500:
        r.status, r.summary = "pass", f"首 token 中位延迟 {first_token:.0f} ms，输出吞吐中位 {m_tps or 0:.1f} tok/s。"
    elif first_token <= 6000:
        r.status, r.summary = "warn", f"首 token 中位延迟 {first_token:.0f} ms 偏高，可能存在排队或中转开销。"
    else:
        r.status, r.summary = "fail", f"首 token 中位延迟 {first_token:.0f} ms，明显异常。"
    if pseudo_runs:
        r.notes.append(f"{pseudo_runs}/{n} 轮输出为一次性突发到达（伪流式：网关先整体生成再转发），吞吐数据不可信。")
        if r.status == "pass":
            r.status = "warn"
    return r


@probe(
    "streaming",
    "流式协议合规性",
    "性能",
    "检查 SSE 格式：role 首块、reasoning 先于 content、[DONE] 终止、include_usage 末尾统计块、伪流式检测。",
    order=70,
    reference="Streaming: 以 data: [DONE] 判定结束；include_usage 时末尾块 choices 为空且含顶层 usage。",
)
async def probe_streaming(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["streaming"])
    r.columns = ["检查项", "结果", "说明"]
    await ctx.log("流式协议探针")
    st = await ctx.client.chat_stream(base_payload(ctx, [{"role": "user", "content": "从 1 数到 30，用逗号分隔，不要其他内容。"}], max_tokens=2048))
    if st.error:
        r.status, r.summary = "fail", f"流式请求失败：{short(st.error.get('message', ''))}"
        r.evidence["error"] = st.error
        return r
    n_chunks = len(st.chunks)
    content_chunks = sum(1 for c in st.chunks for ch in (c.get("choices") or []) if (ch.get("delta") or {}).get("content"))
    r.rows.append(["数据块总数", n_chunks, f"其中含 content 的 {content_chunks} 块"])
    r.rows.append(["首块含 role", "是" if st.role_chunks >= 1 else "否", "官方首块 delta 含 role=assistant"])
    r.rows.append(["role 重复次数", st.role_chunks, "官方仅首块出现一次 role" if st.role_chunks <= 1 else "多次出现 role，非官方直出"])
    r.rows.append(["[DONE] 终止标记", "收到" if st.done_seen else "未收到", "官方以 data: [DONE] 结束"])
    r.rows.append(["末尾 usage 块", "有" if st.usage else "无", "choices 为空" if st.usage_chunk_had_empty_choices else ("choices 非空" if st.usage else "-")])
    r.rows.append(["finish_reason", st.finish_reason or "-", ""])
    r.rows.append(["reasoning 先于 content", "是" if (st.t_first_reasoning is None or st.t_first_content is None or st.t_first_reasoning <= st.t_first_content) else "否", "官方推理增量总是先到达"])
    r.rows.append(["响应 id 数量", len(st.ids), "官方同一次流所有块 id 相同" if len(st.ids) <= 1 else "多个 id，疑似拼接"])
    r.rows.append(["model 字段", ", ".join(sorted(st.model_names)) or "-", "与请求一致" if st.model_names == {ctx.model} else "与请求不一致"])

    # Pseudo-stream heuristic: content arriving in one or two bursts.
    pseudo = st.looks_pseudo_stream
    if st.t_first_content is not None and st.t_last_chunk is not None:
        span = (st.t_last_chunk - st.t_first_content) * 1000
        avg_chunk_chars = len(st.content) / max(1, content_chunks)
        r.rows.append(["内容增量时间跨度", f"{span:.0f} ms", f"平均每块 {avg_chunk_chars:.1f} 字符" + ("；疑似伪流式（整体生成后一次转发）" if pseudo else "")])
    r.evidence["sample_lines"] = st.raw_lines[:6] + (["…"] if len(st.raw_lines) > 8 else []) + st.raw_lines[-3:]

    problems = []
    if not st.done_seen:
        problems.append("缺少 [DONE]")
    if not st.usage:
        problems.append("缺少 usage")
    if st.role_chunks > 1:
        problems.append("role 重复")
    if len(st.ids) > 1:
        problems.append("多 id")
    if pseudo:
        problems.append("伪流式")
    if st.model_names and st.model_names != {ctx.model}:
        problems.append("model 不一致")
    if not problems:
        r.status, r.summary = "pass", "SSE 流格式完全符合官方规范。"
    elif any(p in problems for p in ("伪流式", "model 不一致", "多 id")):
        r.status, r.summary = "fail", "流式行为异常：" + "、".join(problems)
    else:
        r.status, r.summary = "warn", "流式格式存在偏差：" + "、".join(problems)
    return r
