"""Web search probes: built-in $web_search tool and standalone /v1/tools/search."""

from __future__ import annotations

import datetime as dt
import json
import re

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from .common import base_payload, finish_of, message_of

WEB_SEARCH_TOOL = {"type": "builtin_function", "function": {"name": "$web_search"}}


@probe(
    "web_search",
    "联网搜索（$web_search 内置工具）",
    "联网",
    "声明 builtin_function $web_search，观察模型是否发起 tool_calls、arguments 中是否含 usage.total_tokens、以及回传后能否给出带时效性的答案。",
    order=100,
    cost="high",
    reference="Use Web Search: tools=[{type:builtin_function, function:{name:$web_search}}]；tool_call 参数原样回传；arguments.usage.total_tokens 为搜索内容 token。",
)
async def probe_web_search(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["web_search"])
    r.columns = ["阶段", "结果", "说明"]
    today = dt.date.today()
    question = ctx.opt("web_query") or f"请联网搜索：今天（{today.isoformat()}）有哪些重要的科技新闻？列出 3 条并给出来源网址。"
    messages = [{"role": "system", "content": "You are Kimi."}, {"role": "user", "content": question}]
    hops = 0
    search_tokens = 0
    search_args: list[dict] = []
    final_content = ""
    final_usage = None
    while hops < 4:
        payload = base_payload(ctx, messages, max_tokens=8192, tools=[WEB_SEARCH_TOOL])
        await ctx.log(f"联网探针：第 {hops + 1} 跳")
        res = await ctx.client.chat(payload)
        hops += 1
        if not res.ok:
            r.rows.append([f"第 {hops} 跳", f"HTTP {res.status}", short(res.error_message)])
            r.status = "fail"
            r.summary = f"声明 $web_search 后请求被拒绝：{res.error_type or ''} {short(res.error_message, 100)}"
            r.notes.append("官方平台接受 builtin_function 类型；中转若按 OpenAI 规范校验 tools 会直接 400。")
            r.evidence["error"] = res.error
            return r
        body = res.json
        msg = message_of(body)
        finish = finish_of(body)
        tool_calls = msg.get("tool_calls") or []
        if finish == "tool_calls" and tool_calls:
            messages.append(msg)
            for tc in tool_calls:
                name = (tc.get("function") or {}).get("name")
                raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except ValueError:
                    args = {"_raw": raw_args}
                search_args.append(args)
                tok = ((args.get("usage") or {}).get("total_tokens")) if isinstance(args, dict) else None
                if tok:
                    search_tokens += int(tok)
                r.rows.append([f"第 {hops} 跳 tool_call", f"{tc.get('type')} / {name}", f"arguments {short(raw_args, 120)}"])
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "name": name, "content": json.dumps(args, ensure_ascii=False)})
            continue
        final_content = msg.get("content") or ""
        final_usage = body.get("usage")
        r.rows.append([f"第 {hops} 跳 最终回答", f"finish={finish}", short(final_content, 200)])
        break

    r.evidence["search_args"] = search_args
    r.evidence["final"] = final_content
    r.evidence["usage"] = final_usage
    urls = re.findall(r"https?://[^\s\)\]】）>\"']+", final_content)
    year_mentions = re.findall(r"20\d\d", final_content)
    recent = any(y in (str(today.year), str(today.year - 1)) for y in year_mentions)
    r.rows.append(["搜索调用次数", len(search_args), f"搜索内容 token（arguments.usage.total_tokens）≈ {search_tokens}" if search_tokens else "arguments 未含 usage.total_tokens"])
    r.rows.append(["回答含链接", len(urls), short(", ".join(urls[:3]), 160)])
    r.rows.append(["回答含近期年份", "是" if recent else "否", ", ".join(sorted(set(year_mentions))[:6])])

    if not search_args:
        r.status = "fail" if final_content else "fail"
        r.summary = "模型从未发起 $web_search 调用，联网搜索不可用（中转可能剥离了 builtin_function 工具）。"
        if final_content and ("无法" in final_content or "不能" in final_content or "cannot" in final_content.lower()):
            r.notes.append("模型明确表示无法联网。")
        return r
    tc_type_ok = all(True for _ in search_args)
    if search_tokens > 0 and (urls or recent):
        r.status, r.summary = "pass", f"联网搜索正常：{len(search_args)} 次搜索，搜索内容约 {search_tokens} tokens，回答包含来源链接/近期信息。"
    elif search_tokens > 0:
        r.status, r.summary = "warn", "搜索已执行且计入 token，但回答中缺少链接与时效性信息。"
    else:
        r.status, r.summary = "warn", "模型发起了 tool_call，但 arguments 缺少官方特有的 usage.total_tokens 字段，搜索可能不是由 Kimi 官方执行。"
    return r


@probe(
    "tools_search_api",
    "独立搜索接口 /v1/tools/search",
    "联网",
    "调用官方推荐的独立 Web Search Basic 接口，检查是否被网关转发。",
    order=110,
    default_on=False,
    reference="API Overview: POST /v1/tools/search 返回 search_results[]（title/snippet/url/site_name/date）。",
)
async def probe_tools_search(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["tools_search_api"])
    r.columns = ["检查项", "结果", "说明"]
    await ctx.log("POST /tools/search")
    res = await ctx.client.tools_search("Moonshot AI Kimi K3", limit=5)
    if not res.ok:
        r.rows.append(["/tools/search", f"HTTP {res.status}", short(res.error_message)])
        r.status = "warn"
        r.summary = "独立搜索接口不可用（中转通常不转发此接口，官方账号需开通权限）。"
        return r
    results = (res.json or {}).get("search_results") or []
    r.rows.append(["返回条数", len(results), ""])
    for it in results[:5]:
        r.rows.append([short(it.get("title"), 60), it.get("site_name") or "-", short(it.get("url"), 80)])
    r.evidence["results"] = results[:5]
    r.status = "pass" if results else "warn"
    r.summary = f"独立搜索接口可用，返回 {len(results)} 条结果。" if results else "接口可用但无结果。"
    return r
