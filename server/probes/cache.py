"""Context cache hit-rate probe."""

from __future__ import annotations

import asyncio
import uuid

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from ..samples import CACHE_EXPECTED, CACHE_PREFIX, CACHE_QUESTIONS
from .common import base_payload, message_of, usage_cached


@probe(
    "cache",
    "上下文缓存命中率",
    "缓存",
    "使用同一长前缀连续请求，观察 usage.cached_tokens 的命中比例与首字延迟变化；并验证 prompt_cache_key 与前缀变更的影响。",
    order=50,
    cost="medium",
    reference="Context Caching: 自动前缀缓存，前一请求 prompt_tokens > 256 才会建立缓存；usage.cached_tokens 表示命中数。",
)
async def probe_cache(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["cache"])
    r.columns = ["轮次", "prompt_tokens", "cached_tokens", "命中率", "耗时", "回答", "正确"]
    session = f"kimi-probe-{uuid.uuid4().hex[:12]}"
    rounds = int(ctx.opt("cache_rounds", 3))
    rounds = max(2, min(rounds, 6))
    history: list[dict] = [{"role": "system", "content": CACHE_PREFIX}]
    hit_rates: list[float] = []
    ttfts: list[float] = []
    first_prompt_tokens: int | None = None
    all_correct = True

    use_key = True
    for i in range(rounds):
        q = CACHE_QUESTIONS[i % len(CACHE_QUESTIONS)]
        expected = CACHE_EXPECTED[i % len(CACHE_EXPECTED)]
        messages = history + [{"role": "user", "content": q}]
        await ctx.log(f"缓存探针第 {i + 1}/{rounds} 轮")
        extra = {"prompt_cache_key": session} if use_key else {}
        st = await ctx.client.chat_stream(base_payload(ctx, messages, max_tokens=4096, **extra))
        # A gateway that does not know prompt_cache_key rejects the whole request.
        # Drop the field and retry so automatic prefix caching can still be measured.
        if st.error and use_key and "prompt_cache_key" in str(st.error.get("message", "")):
            use_key = False
            r.notes.append("网关拒绝 prompt_cache_key 字段（官方支持该参数），已去掉该字段重试后续请求。")
            r.evidence["prompt_cache_key_rejected"] = st.error
            await ctx.log("缓存探针：网关不支持 prompt_cache_key，去掉该字段重试")
            st = await ctx.client.chat_stream(base_payload(ctx, messages, max_tokens=4096))
        if st.error:
            r.rows.append([i + 1, "-", "-", "-", "-", short(st.error.get("message", ""), 80), "-"])
            r.status = "fail"
            r.summary = f"第 {i + 1} 轮请求失败：{short(st.error.get('message', ''))}"
            r.evidence["error"] = st.error
            return r
        usage = st.usage or {}
        pt = usage.get("prompt_tokens")
        cached = usage_cached(usage)
        rate = (cached / pt * 100) if (pt and cached is not None) else None
        answer = (st.content or "").strip()
        correct = expected in answer
        all_correct = all_correct and correct
        if i == 0:
            first_prompt_tokens = pt
        else:
            if rate is not None:
                hit_rates.append(rate)
        if st.ttft_ms is not None:
            ttfts.append(st.ttft_ms)
        r.rows.append([
            i + 1,
            pt if pt is not None else "-",
            cached if cached is not None else "-",
            f"{rate:.0f}%" if rate is not None else "-",
            f"{st.total_ms:.0f} ms (TTFT {st.ttft_ms:.0f})" if st.total_ms is not None and st.ttft_ms is not None else "-",
            short(answer, 40),
            "是" if correct else "否",
        ])
        history.append({"role": "user", "content": q})
        assistant_msg: dict = {"role": "assistant", "content": answer}
        if st.reasoning:
            assistant_msg["reasoning_content"] = st.reasoning
        history.append(assistant_msg)
        r.evidence.setdefault("usages", []).append(usage)
        # Give the cache a moment to be written before the next request.
        await asyncio.sleep(1.0)

    # Extra: change the prefix and confirm cache drops (prefix-cache semantics).
    await ctx.log("缓存探针：改变前缀验证失效")
    altered = [{"role": "system", "content": "【已修订版本】" + CACHE_PREFIX}, {"role": "user", "content": CACHE_QUESTIONS[0]}]
    alt_extra = {"prompt_cache_key": session} if use_key else {}
    alt = await ctx.client.chat(base_payload(ctx, altered, max_tokens=64, **alt_extra))
    alt_usage = (alt.json or {}).get("usage") if alt.ok and isinstance(alt.json, dict) else None
    alt_cached = usage_cached(alt_usage)
    alt_pt = alt_usage.get("prompt_tokens") if alt_usage else None
    alt_rate = (alt_cached / alt_pt * 100) if (alt_pt and alt_cached is not None) else None
    r.rows.append(["前缀变更", alt_pt if alt_pt is not None else "-", alt_cached if alt_cached is not None else "-", f"{alt_rate:.0f}%" if alt_rate is not None else "-", f"{alt.elapsed_ms:.0f} ms", "(验证前缀缓存语义)", "-"])
    r.evidence["altered_usage"] = alt_usage

    reports_cached = any(usage_cached(u) is not None for u in r.evidence.get("usages", []))
    if not reports_cached:
        r.status = "warn"
        r.summary = "usage 中没有 cached_tokens 字段，网关未透传缓存信息（官方响应必含该字段）。"
        r.notes.append("官方 Kimi API 的 usage 对象包含 cached_tokens；缺失通常意味着中转改写了 usage。")
        return r
    avg_hit = sum(hit_rates) / len(hit_rates) if hit_rates else 0.0
    r.evidence["avg_hit_rate"] = round(avg_hit, 1)
    if first_prompt_tokens is not None and first_prompt_tokens < 256:
        r.notes.append("首轮 prompt_tokens < 256，按官方规则不会建立缓存。")
    if len(ttfts) >= 2 and ttfts[0] > 0:
        gain = (ttfts[0] - min(ttfts[1:])) / ttfts[0] * 100
        r.notes.append(f"TTFT 首轮 {ttfts[0]:.0f} ms，后续最低 {min(ttfts[1:]):.0f} ms（{gain:+.0f}%）。")
    if alt_rate is not None and alt_rate > 50:
        r.notes.append("前缀被修改后 cached_tokens 仍然很高，不符合前缀缓存语义，疑似伪造 usage。")
        r.status = "fail"
        r.summary = f"平均命中率 {avg_hit:.0f}%，但前缀变更后缓存未失效，缓存数据可疑。"
        return r
    if not all_correct:
        r.notes.append("有回答与手册内容不符，模型可能未真正读取被缓存的上下文。")
    if avg_hit >= 70:
        r.status = "pass"
        r.summary = f"后续轮次平均缓存命中率 {avg_hit:.0f}%，自动前缀缓存工作正常。"
    elif avg_hit > 0:
        r.status = "warn"
        r.summary = f"平均缓存命中率仅 {avg_hit:.0f}%，可能是中转多账号轮询或请求被改写。"
    else:
        r.status = "fail"
        r.summary = "连续同前缀请求 cached_tokens 始终为 0，缓存未生效（多 Key 轮询、请求被改写或非官方后端）。"
    if not all_correct and r.status == "pass":
        r.status = "warn"
    return r
