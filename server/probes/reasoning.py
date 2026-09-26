"""Thinking mode and per-model parameter constraint probes.

These are the strongest "is this really the official Kimi backend" signals:
the official platform rejects a fixed set of parameter values with well-defined
error types, and each model family has a distinct thinking configuration.
"""

from __future__ import annotations

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from .common import base_payload, is_k26, is_k27, is_k3, message_of, usage_reasoning


@probe(
    "thinking",
    "思考模式与 reasoning_content",
    "推理",
    "验证 reasoning_content 是否返回、reasoning_effort / thinking 参数是否按官方规则生效或报错、Preserved Thinking 回传是否被接受。",
    order=80,
    cost="medium",
    reference="Thinking Models: K3 恒思考、reasoning_effort=low/high/max；K2.6 thinking.type 可关；K2.7-code 仅接受 enabled+keep=all。",
)
async def probe_thinking(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["thinking"])
    r.columns = ["场景", "HTTP", "reasoning_content", "content 长度", "推理 tokens", "判定"]
    q = "一个水池有进水管和出水管，单开进水管 6 小时注满，单开出水管 9 小时放空。两管同开需要多少小时注满？只给出最终数字。"
    model = ctx.model

    async def run(label: str, expect_reasoning: bool | None, **extra) -> tuple[bool, dict]:
        payload = base_payload(ctx, [{"role": "user", "content": q}], max_tokens=8192)
        # Remove defaults so that extra can override cleanly.
        payload.pop("reasoning_effort", None)
        payload.pop("thinking", None)
        payload.update(extra)
        await ctx.log(f"思考探针：{label}")
        res = await ctx.client.chat(payload)
        msg = message_of(res.json or {}) if res.ok else {}
        rc = msg.get("reasoning_content") or ""
        content = msg.get("content") or ""
        usage = (res.json or {}).get("usage") if res.ok and isinstance(res.json, dict) else None
        rt = usage_reasoning(usage)
        has_rc = len(rc) > 0
        if not res.ok:
            verdict = f"{res.error_type or 'error'}: {short(res.error_message, 70)}"
            ok = expect_reasoning is None  # None means "expected to error"
        elif expect_reasoning is None:
            verdict, ok = "官方应报错，但被接受", False
        elif expect_reasoning and not has_rc:
            verdict, ok = "缺少 reasoning_content", False
        elif not expect_reasoning and has_rc:
            verdict, ok = "关闭思考后仍返回 reasoning", False
        else:
            verdict, ok = "符合预期", True
        r.rows.append([label, res.status, f"{len(rc)} 字符" if has_rc else "无", len(content), rt if rt is not None else "-", verdict])
        return ok, {"status": res.status, "error": res.error, "reasoning_len": len(rc), "content": content[:200], "usage": usage, "reasoning_head": rc[:300]}

    results: dict[str, tuple[bool, dict]] = {}
    if is_k3(model):
        results["默认（恒思考）"] = await run("默认（恒思考）", True)
        results["reasoning_effort=low"] = await run("reasoning_effort=low", True, reasoning_effort="low")
        results["thinking.disabled（K3 不支持，应报错）"] = await run("thinking.disabled（K3 不支持，应报错）", None, thinking={"type": "disabled"})
    elif is_k26(model):
        results["默认（思考开启）"] = await run("默认（思考开启）", True)
        results["thinking.disabled"] = await run("thinking.disabled", False, thinking={"type": "disabled"})
        results["thinking.enabled+keep=all"] = await run("thinking.enabled+keep=all", True, thinking={"type": "enabled", "keep": "all"})
    elif is_k27(model):
        results["默认（恒思考）"] = await run("默认（恒思考）", True)
        results["thinking.disabled（应报错）"] = await run("thinking.disabled（应报错）", None, thinking={"type": "disabled"})
    else:
        results["默认"] = await run("默认", True)

    # Preserved thinking round-trip: send the assistant message back including reasoning_content.
    first = next(iter(results.values()))[1] if results else None
    if first and first.get("status") == 200 and first.get("reasoning_len"):
        await ctx.log("思考探针：Preserved Thinking 回传")
        messages = [
            {"role": "user", "content": q},
            {"role": "assistant", "content": first["content"], "reasoning_content": first["reasoning_head"] + "…"},
            {"role": "user", "content": "请把你上一条回答中的数字乘以 2，只回答数字。"},
        ]
        res = await ctx.client.chat(base_payload(ctx, messages, max_tokens=4096))
        ok = res.ok
        r.rows.append(["Preserved Thinking 回传", res.status, "-", len(message_of(res.json or {}).get("content", "")) if res.ok else 0, "-", "回传被接受" if ok else f"{res.error_type}: {short(res.error_message, 60)}"])
        results["preserved"] = (ok, {"status": res.status, "error": res.error})

    r.evidence = {k: v[1] for k, v in results.items()}
    fails = [k for k, v in results.items() if not v[0]]
    if not fails:
        r.status, r.summary = "pass", "思考模式行为与官方文档描述完全一致。"
    elif any("reasoning_content" in r.rows[i][5] for i in range(len(r.rows))):
        r.status, r.summary = "fail", "缺少 reasoning_content 或思考开关无效，后端不是官方 Kimi 思考模型的行为。"
    else:
        r.status, r.summary = "warn", "部分场景与官方行为不一致：" + "、".join(fails)
    return r


@probe(
    "params",
    "固定参数约束校验",
    "推理",
    "官方平台对 temperature / top_p / n / tool_choice 等参数有固定值约束，传入非法值应返回 invalid_request_error；中转与套壳通常不会报错。",
    order=90,
    reference="Model Parameter Reference: temperature 固定 1.0、top_p 固定 0.95、n 固定 1，传入其他值返回错误；K2.x 不支持 tool_choice=required。",
)
async def probe_params(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["params"])
    r.columns = ["参数", "发送值", "官方预期", "HTTP", "error.type", "判定"]
    model = ctx.model
    msgs = [{"role": "user", "content": "回复“OK”。"}]

    cases: list[tuple[str, dict, str]] = [
        ("temperature", {"temperature": 0.2}, "reject"),
        ("temperature", {"temperature": 1.0}, "accept"),
        ("top_p", {"top_p": 0.5}, "reject"),
        ("n", {"n": 2}, "reject"),
        ("presence_penalty", {"presence_penalty": 1.0}, "reject"),
        ("model", {"model": "kimi-k2-0905-preview"}, "reject"),
    ]
    if is_k3(model):
        cases.append(("reasoning_effort", {"reasoning_effort": "medium"}, "reject"))
    if is_k26(model) or is_k27(model):
        cases.append(("tool_choice", {"tool_choice": "required", "tools": [{"type": "function", "function": {"name": "noop", "description": "no-op", "parameters": {"type": "object", "properties": {}}}}]}, "reject"))
    if is_k27(model):
        cases.append(("thinking.keep", {"thinking": {"type": "enabled", "keep": "none"}}, "reject"))

    score = 0
    total = 0
    accepted_bad: list[str] = []
    for name, extra, expect in cases:
        payload = base_payload(ctx, msgs, max_tokens=64)
        payload.update(extra)
        await ctx.log(f"参数探针：{name}={extra.get(name, extra)}")
        res = await ctx.client.chat(payload)
        rejected = not res.ok
        ok = rejected if expect == "reject" else not rejected
        total += 1
        score += int(ok)
        if expect == "reject" and not rejected:
            accepted_bad.append(name)
        r.rows.append([
            name,
            short(str(extra.get(name, extra)), 40),
            "拒绝 (400)" if expect == "reject" else "接受",
            res.status,
            res.error_type or "-",
            "符合" if ok else ("被接受（官方会拒绝）" if expect == "reject" else f"被拒绝：{short(res.error_message, 60)}"),
        ])
        r.evidence[f"{name}:{extra.get(name)}"] = {"status": res.status, "error": res.error}

    r.evidence["score"] = f"{score}/{total}"
    if score == total:
        r.status, r.summary = "pass", f"{score}/{total} 项参数约束与官方一致，网关未改写请求参数。"
    elif accepted_bad and len(accepted_bad) >= 3:
        r.status, r.summary = "fail", f"非法参数 {', '.join(accepted_bad)} 均被接受，后端不是官方 Kimi 平台（中转清洗参数或非 Kimi 模型）。"
    else:
        r.status, r.summary = "warn", f"{score}/{total} 项一致；不一致项：{', '.join(accepted_bad) or '见表'}。"
    return r
