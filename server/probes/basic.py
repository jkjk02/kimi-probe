"""Basic connectivity, endpoint inventory and identity probes."""

from __future__ import annotations

import re

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from .common import base_payload, message_of

KNOWN_MODELS = ["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"]
RETIRED_MODELS = [
    "kimi-k2.5",
    "moonshot-v1-8k",
    "moonshot-v1-32k",
    "moonshot-v1-128k",
    "moonshot-v1-auto",
    "kimi-k2-0905-preview",
    "kimi-k2-thinking",
    "kimi-latest",
]


@probe(
    "endpoint",
    "端点与网关指纹",
    "基础",
    "枚举 /models、/users/me/balance，采集响应头，判断目标是官方直连还是中转网关。",
    order=10,
    reference="API Overview: /v1/models, /v1/users/me/balance；官方响应头含 msh-request-id 等。",
)
async def probe_endpoint(ctx: ProbeContext) -> ProbeResult:
    spec = REGISTRY["endpoint"]
    r = new_result(spec)
    await ctx.log("GET /models")
    models = await ctx.client.list_models()
    await ctx.log("GET /users/me/balance")
    bal = await ctx.client.balance()

    r.columns = ["检查项", "结果", "说明"]
    model_ids: list[str] = []
    if models.ok and isinstance(models.json, dict):
        model_ids = [m.get("id", "") for m in models.json.get("data", []) if isinstance(m, dict)]
        present = [m for m in KNOWN_MODELS if m in model_ids]
        retired_present = [m for m in RETIRED_MODELS if m in model_ids]
        r.rows.append(["/models 可用", f"{len(model_ids)} 个模型", ", ".join(model_ids[:12]) + ("…" if len(model_ids) > 12 else "")])
        r.rows.append(["现役模型覆盖", f"{len(present)}/{len(KNOWN_MODELS)}", ", ".join(present) or "无"])
        if retired_present:
            r.rows.append(["列表含已退役模型", ", ".join(retired_present), "官方于 2026-08-31 前已下线这些模型，列表仍含则很可能是中转自维护的列表"])
            r.notes.append("模型列表包含官方已退役的模型名，说明列表并非官方实时返回。")
        if ctx.model not in model_ids:
            r.rows.append(["目标模型在列表中", "否", f"{ctx.model} 未出现在 /models 返回中（部分中转不返回完整列表）"])
        else:
            r.rows.append(["目标模型在列表中", "是", ctx.model])
    else:
        r.rows.append(["/models 可用", f"HTTP {models.status}", short(models.error_message)])

    if bal.ok and isinstance(bal.json, dict):
        data = bal.json.get("data", bal.json)
        r.rows.append(["余额接口", "可用", f"available_balance={data.get('available_balance')} voucher={data.get('voucher_balance')} cash={data.get('cash_balance')}"])
        r.evidence["balance"] = data
    else:
        r.rows.append(["余额接口", f"HTTP {bal.status}", short(bal.error_message) or "中转通常不转发此接口"])

    hdrs = models.headers or bal.headers
    r.evidence["headers_models"] = models.headers
    r.evidence["headers_balance"] = bal.headers
    fingerprint: list[str] = []
    if any(k in hdrs for k in ("msh-request-id", "x-msh-request-id")):
        fingerprint.append("存在 msh-request-id（Moonshot 网关特征）")
    if "cf-ray" in hdrs:
        fingerprint.append("经过 Cloudflare（cf-ray）")
    if hdrs.get("server"):
        fingerprint.append(f"server={hdrs['server']}")
    if hdrs.get("x-powered-by"):
        fingerprint.append(f"x-powered-by={hdrs['x-powered-by']}")
    rl = {k: v for k, v in hdrs.items() if k.startswith("x-ratelimit")}
    if rl:
        fingerprint.append("返回 x-ratelimit-* 头：" + ", ".join(f"{k}={v}" for k, v in rl.items()))
    r.rows.append(["响应头指纹", "; ".join(fingerprint) or "无显著特征", short(str(hdrs), 200)])

    base = ctx.client.base_url
    official = base.startswith("https://api.moonshot.ai") or base.startswith("https://api.moonshot.cn")
    r.rows.append(["base_url", base, "官方域名" if official else "非官方域名（中转/代理）"])

    if not models.ok and not bal.ok:
        r.status = "fail"
        r.summary = "模型列表与余额接口均不可用，请检查 base_url 与 API Key。"
    elif models.ok and bal.ok:
        r.status = "pass"
        r.summary = "模型列表与余额接口均可用，" + ("具备 Moonshot 官方网关特征。" if fingerprint and "msh-request-id" in " ".join(fingerprint) else "未发现官方网关特有响应头。")
    else:
        r.status = "warn"
        r.summary = "部分管理类接口不可用；中转站通常只转发 chat/completions。"
    return r


@probe(
    "identity",
    "模型自述与响应元数据",
    "基础",
    "询问模型身份，并核对响应中的 model / id / object 字段是否符合官方格式。",
    order=20,
    reference="Chat Completions: 响应 model 字段按请求返回；官方 id 形如 cmpl-<hex>。",
)
async def probe_identity(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["identity"])
    payload = base_payload(
        ctx,
        [{"role": "user", "content": "你是谁？由哪家公司研发？请用一句话回答，并说明你的模型名称。"}],
        max_tokens=2048,
    )
    await ctx.log("POST /chat/completions（身份询问）")
    res = await ctx.client.chat(payload)
    r.columns = ["字段", "值", "判定"]
    if not res.ok:
        r.status = "fail"
        r.summary = f"请求失败：HTTP {res.status} {short(res.error_message)}"
        r.evidence["response"] = res.json or res.text[:500]
        return r
    body = res.json
    msg = message_of(body)
    content = msg.get("content") or ""
    r.evidence["content"] = content
    r.evidence["reasoning_len"] = len(msg.get("reasoning_content") or "")
    r.evidence["headers"] = res.headers

    resp_model = body.get("model")
    rid = str(body.get("id") or "")
    obj = body.get("object")

    model_ok = resp_model == ctx.model
    r.rows.append(["response.model", resp_model, "与请求一致" if model_ok else f"与请求 {ctx.model} 不一致"])
    id_ok = bool(re.match(r"^(cmpl|chatcmpl)-[0-9a-f]{16,}$", rid))
    r.rows.append(["response.id", rid, "官方风格（cmpl-<hex32>）" if rid.startswith("cmpl-") and id_ok else ("OpenAI 风格" if rid.startswith("chatcmpl-") else "非常见格式")])
    r.rows.append(["response.object", obj, "正常" if obj == "chat.completion" else "异常"])
    r.rows.append(["reasoning_content", f"{r.evidence['reasoning_len']} 字符", "存在" if r.evidence["reasoning_len"] else "不存在"])

    low = content.lower()
    claims_kimi = any(k in low for k in ("kimi", "moonshot", "月之暗面"))
    claims_other = [k for k in ("openai", "chatgpt", "gpt-4", "gpt-5", "anthropic", "claude", "gemini", "deepseek", "qwen", "通义", "文心", "llama", "grok") if k in low]
    verdict = "自述为 Kimi / Moonshot" if claims_kimi else ("自述为其他厂商：" + ",".join(claims_other) if claims_other else "未明确自述身份")
    r.rows.append(["模型自述", short(content, 140), verdict])

    if claims_other and not claims_kimi:
        r.status = "fail"
        r.summary = "模型自述为非 Kimi 厂商，极可能被替换为其他模型。"
    elif not model_ok:
        r.status = "warn"
        r.summary = "响应 model 字段与请求不一致，网关可能做了模型映射。"
    elif claims_kimi and id_ok and rid.startswith("cmpl-"):
        r.status = "pass"
        r.summary = "模型自述为 Kimi，响应元数据符合官方格式。"
    elif claims_kimi and rid.startswith("chatcmpl-"):
        r.status = "warn"
        r.summary = "模型自述为 Kimi，但响应 id 为 chatcmpl- 格式（官方为 cmpl-），响应经过中转网关重写。"
    else:
        r.status = "warn"
        r.summary = "身份自述或响应格式存在轻微偏差，请结合其他探针判断。"
    return r
