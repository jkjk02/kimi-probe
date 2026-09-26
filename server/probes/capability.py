"""Tool calling, structured output, partial mode, long-context recall, protocol compatibility."""

from __future__ import annotations

import json
import random

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from ..samples import FILLER_PARAGRAPHS, NEEDLE_TEMPLATE
from .common import base_payload, finish_of, is_k3, message_of

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市当前天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}, "unit": {"type": "string", "enum": ["c", "f"]}},
            "required": ["city"],
        },
    },
}


@probe(
    "tool_call",
    "函数调用（tool_calls）",
    "能力",
    "自定义 function 工具，验证 tool_calls 结构、参数 JSON 合法性、流式分片拼接与 tool 结果回传后的二次回答。",
    order=140,
    cost="medium",
    reference="Tool Calls: finish_reason=tool_calls；流式时同一调用的分片共享 index，arguments 逐段拼接。",
)
async def probe_tool_call(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["tool_call"])
    r.columns = ["检查项", "结果", "说明"]
    messages = [{"role": "user", "content": "帮我查一下杭州现在的天气，用摄氏度。"}]
    extra: dict = {"tools": [WEATHER_TOOL]}
    if is_k3(ctx.model):
        extra["tool_choice"] = "required"
    await ctx.log("函数调用探针：流式首跳")
    st = await ctx.client.chat_stream(base_payload(ctx, messages, max_tokens=4096, **extra))
    if st.error:
        r.status, r.summary = "fail", f"请求失败：{short(st.error.get('message', ''))}"
        r.evidence["error"] = st.error
        return r
    calls = st.tool_calls_list()
    r.rows.append(["finish_reason", st.finish_reason or "-", "官方应为 tool_calls"])
    r.rows.append(["tool_calls 数量", len(calls), ""])
    args_ok = False
    call = calls[0] if calls else None
    if call:
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
            args_ok = isinstance(args, dict) and "city" in args
        except ValueError:
            args = None
        r.rows.append(["函数名", call["function"]["name"], "期望 get_weather"])
        r.rows.append(["arguments", short(call["function"]["arguments"], 100), "JSON 合法且含 city" if args_ok else "JSON 非法或缺少 city"])
        r.rows.append(["call id", call.get("id") or "-", "官方形如 get_weather:0 或随机 id"])
        r.rows.append(["type", call.get("type") or "-", "应为 function"])
    r.evidence["first_hop"] = {"finish": st.finish_reason, "tool_calls": calls, "reasoning_len": len(st.reasoning), "usage": st.usage}

    second_ok = False
    if call and args_ok:
        assistant_msg: dict = {"role": "assistant", "content": st.content or "", "tool_calls": [{"id": call["id"], "type": call.get("type") or "function", "function": call["function"]}]}
        if st.reasoning:
            assistant_msg["reasoning_content"] = st.reasoning
        messages.append(assistant_msg)
        messages.append({"role": "tool", "tool_call_id": call["id"], "name": "get_weather", "content": json.dumps({"city": "杭州", "weather": "小雨", "temperature_c": 21}, ensure_ascii=False)})
        await ctx.log("函数调用探针：回传工具结果")
        res = await ctx.client.chat(base_payload(ctx, messages, max_tokens=4096, tools=[WEATHER_TOOL]))
        if res.ok:
            content = message_of(res.json).get("content") or ""
            second_ok = ("21" in content) and ("雨" in content)
            r.rows.append(["二次回答", short(content, 120), "引用了工具结果" if second_ok else "未引用工具结果"])
        else:
            r.rows.append(["二次回答", f"HTTP {res.status}", short(res.error_message, 100)])
    if call and args_ok and second_ok:
        r.status, r.summary = "pass", "函数调用全流程正常：参数合法、结果回传后正确引用。"
    elif call and args_ok:
        r.status, r.summary = "warn", "首跳 tool_calls 正常，但回传结果后的回答未引用工具输出。"
    else:
        r.status, r.summary = "fail", "模型未发起合法的 tool_calls。"
    return r


@probe(
    "structured",
    "结构化输出与 Partial Mode",
    "能力",
    "验证 response_format=json_schema(strict) 的合规性、json_object 模式，以及 assistant partial=true 前缀续写。",
    order=150,
    reference="K3 Quickstart: json_schema strict 约束 message.content；Partial Mode 在 messages 末尾追加 partial=true 的 assistant 消息。",
)
async def probe_structured(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["structured"])
    r.columns = ["场景", "HTTP", "输出", "判定"]
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "person",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}, "city": {"type": "string"}},
                "required": ["name", "age", "city"],
                "additionalProperties": False,
            },
        },
    }
    await ctx.log("结构化输出：json_schema strict")
    res = await ctx.client.chat(base_payload(ctx, [{"role": "user", "content": "林小雨今年 28 岁，住在成都。请提取姓名、年龄和城市。"}], max_tokens=4096, response_format=schema))
    ok_schema = False
    if res.ok:
        content = message_of(res.json).get("content") or ""
        try:
            obj = json.loads(content)
            ok_schema = set(obj.keys()) == {"name", "age", "city"} and obj.get("age") == 28 and isinstance(obj.get("age"), int)
        except ValueError:
            obj = None
        r.rows.append(["json_schema strict", res.status, short(content, 100), "严格符合 schema" if ok_schema else "不符合 schema"])
    else:
        r.rows.append(["json_schema strict", res.status, short(res.error_message, 80), f"被拒绝：{res.error_type}"])

    await ctx.log("结构化输出：json_object")
    res2 = await ctx.client.chat(base_payload(ctx, [{"role": "system", "content": "只输出 JSON 对象。"}, {"role": "user", "content": "给出三种水果及其颜色，键为 fruits，值为对象数组，每个对象含 name 与 color。"}], max_tokens=4096, response_format={"type": "json_object"}))
    ok_json = False
    if res2.ok:
        content2 = message_of(res2.json).get("content") or ""
        try:
            obj2 = json.loads(content2)
            ok_json = isinstance(obj2, dict) and isinstance(obj2.get("fruits"), list) and len(obj2["fruits"]) >= 3
        except ValueError:
            pass
        r.rows.append(["json_object", res2.status, short(content2, 100), "合法 JSON" if ok_json else "非合法 JSON"])
    else:
        r.rows.append(["json_object", res2.status, short(res2.error_message, 80), f"被拒绝：{res2.error_type}"])

    await ctx.log("Partial Mode 前缀续写")
    prefix = "结论："
    res3 = await ctx.client.chat(base_payload(ctx, [
        {"role": "user", "content": "用一句话说明为什么 API 向后兼容很重要。"},
        {"role": "assistant", "content": prefix, "partial": True},
    ], max_tokens=4096))
    ok_partial = False
    if res3.ok:
        content3 = message_of(res3.json).get("content") or ""
        ok_partial = bool(content3.strip()) and not content3.lstrip().startswith(prefix)
        r.rows.append(["partial=true", res3.status, short(prefix + content3, 100), "续写未重复前缀" if ok_partial else "未按前缀续写（重复了前缀或为空）"])
    else:
        r.rows.append(["partial=true", res3.status, short(res3.error_message, 80), f"被拒绝：{res3.error_type}"])

    score = sum([ok_schema, ok_json, ok_partial])
    r.evidence["score"] = score
    if score == 3:
        r.status, r.summary = "pass", "json_schema、json_object 与 Partial Mode 均按官方行为工作。"
    elif score >= 1:
        r.status, r.summary = "warn", f"{score}/3 项符合官方行为。"
    else:
        r.status, r.summary = "fail", "结构化输出与 Partial Mode 均不可用，不是官方 Kimi 行为。"
    return r


@probe(
    "long_context",
    "长上下文召回（大海捞针）",
    "能力",
    "构造约 N K tokens 的填充文本并在随机位置插入口令，验证模型能否准确召回；同时观察长输入的 TTFT。",
    order=160,
    cost="high",
    default_on=False,
    reference="kimi-k3 上下文 1M；kimi-k2.6 / k2.7-code 256K。",
)
async def probe_long_context(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["long_context"])
    r.columns = ["检查项", "结果", "说明"]
    target_k = int(ctx.opt("needle_k_tokens", 32))
    target_k = max(8, min(target_k, 200))
    rng = random.Random()
    secret = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    codename = rng.choice(["北极星", "白鲸", "青鸾", "赤霞", "苍梧"])
    needle = NEEDLE_TEMPLATE.format(codename=codename, secret=secret)
    # ~0.72 tok/char for Chinese → chars = tokens / 0.72
    paragraphs: list[str] = []
    chars = 0
    target_chars = int(target_k * 1000 / 0.72)
    i = 0
    while chars < target_chars:
        p = FILLER_PARAGRAPHS[i % len(FILLER_PARAGRAPHS)]
        paragraphs.append(f"（第 {i + 1} 段）{p}")
        chars += len(p) + 8
        i += 1
    pos = rng.randint(int(len(paragraphs) * 0.3), int(len(paragraphs) * 0.7))
    paragraphs.insert(pos, needle)
    doc = "\n\n".join(paragraphs)
    messages = [
        {"role": "system", "content": "以下是公司内部会议纪要汇编，请仅依据文本回答。"},
        {"role": "user", "content": doc + f"\n\n问题：项目代号“{codename}”的紧急联络口令是什么？只回答口令本身。"},
    ]
    await ctx.log(f"长上下文探针：约 {target_k}K tokens，口令位于 {pos}/{len(paragraphs)} 段")
    st = await ctx.client.chat_stream(base_payload(ctx, messages, max_tokens=4096))
    if st.error:
        r.rows.append(["请求", "失败", short(st.error.get("message", ""), 120)])
        r.status, r.summary = "fail", f"长输入被拒绝：{short(st.error.get('message', ''), 100)}"
        return r
    usage = st.usage or {}
    answer = (st.content or "").strip()
    found = secret in answer.upper().replace(" ", "")
    r.rows.append(["实际 prompt_tokens", usage.get("prompt_tokens", "-"), f"目标约 {target_k}K"])
    r.rows.append(["口令位置", f"{pos}/{len(paragraphs)} 段", f"约 {pos / len(paragraphs) * 100:.0f}% 深度"])
    r.rows.append(["期望口令", secret, ""])
    r.rows.append(["模型回答", short(answer, 80), "召回成功" if found else "召回失败"])
    r.rows.append(["TTFT / 总耗时", f"{st.ttft_ms or 0:.0f} ms / {st.total_ms or 0:.0f} ms", ""])
    r.evidence = {"usage": usage, "answer": answer, "secret": secret, "ttft_ms": st.ttft_ms, "total_ms": st.total_ms}
    if found:
        r.status, r.summary = "pass", f"在约 {usage.get('prompt_tokens', target_k * 1000)} tokens 的上下文中成功召回口令。"
    else:
        r.status, r.summary = "fail", "长上下文召回失败，后端可能截断了输入或上下文窗口不足。"
    return r


@probe(
    "anthropic_compat",
    "Anthropic Messages 协议兼容",
    "能力",
    "官方平台同时提供 /anthropic/v1/messages 端点，检测网关是否转发该协议。",
    order=170,
    default_on=False,
    reference="API Overview: base_url https://api.moonshot.ai/anthropic，兼容 Anthropic SDK 与 Claude Code。",
)
async def probe_anthropic(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["anthropic_compat"])
    r.columns = ["检查项", "结果", "说明"]
    await ctx.log("POST /anthropic/v1/messages")
    res = await ctx.client.anthropic_messages({"model": ctx.model, "max_tokens": 256, "messages": [{"role": "user", "content": "回复 OK"}]})
    if res.ok and isinstance(res.json, dict) and res.json.get("type") == "message":
        blocks = res.json.get("content") or []
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        r.rows.append(["Anthropic 协议", "可用", short(text, 80)])
        r.rows.append(["usage", short(str(res.json.get("usage")), 120), ""])
        r.status, r.summary = "pass", "Anthropic Messages 端点可用。"
    else:
        r.rows.append(["Anthropic 协议", f"HTTP {res.status}", short(res.error_message, 120)])
        r.status, r.summary = "warn", "Anthropic Messages 端点不可用（中转常不转发；不影响 OpenAI 协议使用）。"
    return r
