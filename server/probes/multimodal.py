"""Multimodal probes: image (base64), animated GIF (decoded as video), video via file upload."""

from __future__ import annotations

import random

from ..framework import ProbeContext, ProbeResult, new_result, probe, short, REGISTRY
from ..media import make_code_image, make_digit_gif, random_code, to_data_url
from .common import base_payload, message_of, norm_code


@probe(
    "vision",
    "图像理解（base64 image_url）",
    "多模态",
    "运行时生成含随机验证码的图片，要求模型逐字读出；同时用官方分词接口核对图片 token 计费。",
    order=120,
    cost="medium",
    reference="Vision: content 必须为数组；image_url 仅支持 base64 / ms://file-id，不支持公网 URL；图片按分辨率动态计 token。",
)
async def probe_vision(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["vision"])
    r.columns = ["检查项", "结果", "说明"]
    seed = random.randint(1, 10**9)
    code = random_code(6, random.Random(seed))
    png = make_code_image(code, seed=seed)
    data_url = to_data_url(png, "image/png")
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": "图片中央有一串 6 位大写字母与数字组成的代码，请逐字读出，只回答这串代码。"},
        ]},
    ]
    await ctx.log("图像探针：发送验证码图片")
    res = await ctx.client.chat(base_payload(ctx, messages, max_tokens=4096))
    r.evidence["expected"] = code
    r.evidence["image_bytes"] = len(png)
    if not res.ok:
        r.rows.append(["图像请求", f"HTTP {res.status}", short(res.error_message)])
        r.status, r.summary = "fail", f"图像输入被拒绝：{res.error_type or ''} {short(res.error_message, 100)}"
        return r
    body = res.json
    answer = message_of(body).get("content") or ""
    usage = body.get("usage") or {}
    got = norm_code(answer)
    exact = code in got
    # Partial credit: count matching positions in the best alignment.
    best = 0
    for i in range(max(1, len(got) - len(code) + 1)):
        seg = got[i:i + len(code)]
        best = max(best, sum(1 for a, b in zip(seg, code) if a == b))
    r.rows.append(["期望代码", code, f"PNG {len(png) // 1024} KB, 640×280"])
    r.rows.append(["模型回答", short(answer, 100), "完全正确" if exact else f"{best}/{len(code)} 位正确"])
    r.rows.append(["prompt_tokens", usage.get("prompt_tokens", "-"), "图片按分辨率动态计 token，纯文本约 40 tokens"])
    r.evidence["answer"] = answer
    r.evidence["usage"] = usage

    # Cross-check billing with the official tokenizer (vision supported there too).
    if not ctx.shared.get("tokenizer_unavailable"):
        est = await ctx.client.estimate_tokens(ctx.model, messages)
        if est.ok and isinstance(est.json, dict) and "data" in est.json:
            official = est.json["data"]["total_tokens"]
            pt = usage.get("prompt_tokens")
            r.rows.append(["官方分词（含图片）", official, f"与 usage 差 {pt - official:+d}" if isinstance(pt, int) else "-"])
        else:
            ctx.shared["tokenizer_unavailable"] = True

    pt = usage.get("prompt_tokens")
    if exact:
        r.status, r.summary = "pass", "模型准确读出随机验证码，图像理解可用。"
    elif best >= 4:
        r.status, r.summary = "warn", f"识别 {best}/{len(code)} 位，图像通道可用但精度一般。"
    else:
        r.status, r.summary = "fail", "模型未能读出图片内容，图像可能被网关丢弃或后端不支持视觉。"
    if isinstance(pt, int) and pt < 60:
        r.notes.append("prompt_tokens 过低，图片很可能没有被送入模型。")
        r.status = "fail"
    return r


@probe(
    "video",
    "视频理解（动图 / 文件上传）",
    "多模态",
    "生成逐帧显示不同数字的动画 GIF（平台按视频解码），要求模型按顺序读出；可选用 /files 上传后以 ms:// 引用。",
    order=130,
    cost="medium",
    reference="Vision: 动图经 image_url 传入会按视频解码并按视频计 token；视频可用 files.create(purpose=video) 后以 ms://<file-id> 引用 video_url。",
)
async def probe_video(ctx: ProbeContext) -> ProbeResult:
    r = new_result(REGISTRY["video"])
    r.columns = ["检查项", "结果", "说明"]
    rng = random.Random()
    digits = [rng.randint(2, 9) for _ in range(4)]
    while len(set(digits)) < 3:
        digits = [rng.randint(2, 9) for _ in range(4)]
    gif = make_digit_gif(digits)
    expected = "".join(str(d) for d in digits)
    r.evidence["expected"] = expected

    async def ask(content_part: dict, label: str) -> tuple[bool, dict]:
        messages = [{"role": "user", "content": [
            content_part,
            {"type": "text", "text": "这是一个动画，每一帧显示一个大数字，共 4 帧。请按时间顺序写出这 4 个数字，只回答数字串。"},
        ]}]
        await ctx.log(f"视频探针：{label}")
        res = await ctx.client.chat(base_payload(ctx, messages, max_tokens=4096))
        # Gateway timeouts (502/503/504) and network errors are often transient: retry once.
        if not res.ok and (res.status == 0 or res.status >= 500):
            await ctx.log(f"视频探针：HTTP {res.status}，重试一次")
            res = await ctx.client.chat(base_payload(ctx, messages, max_tokens=4096))
        if not res.ok:
            r.rows.append([label, f"HTTP {res.status}", short(res.error_message, 120)])
            return False, {"status": res.status, "error": res.error, "http_error": True}
        answer = message_of(res.json).get("content") or ""
        usage = (res.json or {}).get("usage") or {}
        # Thinking can consume the whole budget and leave no visible answer; retry with more room.
        if not answer.strip() and (usage.get("completion_tokens") or 0) >= 4000:
            await ctx.log(f"视频探针：思考耗尽 token 预算，扩大到 16K 重试")
            res2 = await ctx.client.chat(base_payload(ctx, messages, max_tokens=16384))
            if res2.ok:
                answer = message_of(res2.json).get("content") or ""
                usage = (res2.json or {}).get("usage") or {}
        if not answer.strip():
            r.rows.append([label, "（无可见回答）", f"思考耗尽 token 预算；completion_tokens={usage.get('completion_tokens', '-')}，prompt_tokens={usage.get('prompt_tokens', '-')}"])
            return False, {"answer": "", "usage": usage, "overlap": 0, "empty": True}
        got = "".join(ch for ch in answer if ch.isdigit())
        exact = expected in got
        # Count frames read correctly in order (best alignment), not a digit-set overlap:
        # a random 4-digit guess shares digits with the answer far too often.
        overlap = 0
        for i in range(max(1, len(got) - len(expected) + 1)):
            seg = got[i:i + len(expected)]
            overlap = max(overlap, sum(1 for a, b in zip(seg, expected) if a == b))
        r.rows.append([label, short(answer, 80), ("完全正确" if exact else f"按顺序读对 {overlap}/{len(expected)} 帧") + f"；prompt_tokens={usage.get('prompt_tokens', '-')}"])
        return exact, {"answer": answer, "usage": usage, "overlap": overlap}

    r.rows.append(["期望序列", expected, f"GIF {len(gif) // 1024} KB, 4 帧 × 0.7 s"])
    ok_gif, ev_gif = await ask({"type": "image_url", "image_url": {"url": to_data_url(gif, "image/gif")}}, "GIF via image_url")
    r.evidence["gif"] = ev_gif

    ok_file = None
    if ctx.opt("video_upload", False):
        await ctx.log("视频探针：上传文件 purpose=video")
        up = await ctx.client.upload_file("probe.gif", gif, "video", "image/gif")
        if up.ok and isinstance(up.json, dict) and up.json.get("id"):
            fid = up.json["id"]
            r.rows.append(["文件上传", fid, "purpose=video"])
            ok_file, ev_file = await ask({"type": "video_url", "video_url": {"url": f"ms://{fid}"}}, "video_url ms://file-id")
            r.evidence["file"] = ev_file
            await ctx.client.delete_file(fid)
        else:
            r.rows.append(["文件上传", f"HTTP {up.status}", short(up.error_message, 120)])
            r.notes.append("文件上传接口不可用，中转通常不转发 /files。")

    if ok_gif or ok_file:
        r.status, r.summary = "pass", "模型按顺序读出了动画各帧内容，视频理解可用。"
    elif ev_gif.get("http_error") and not r.evidence.get("file"):
        st = ev_gif.get("status")
        r.status = "fail"
        r.summary = f"动图请求未成功（HTTP {st}），重试后仍失败：网关超时或不支持视频输入，模型没有收到内容。"
        if st in (502, 503, 504):
            r.notes.append("5xx 来自网关而非模型，说明中转对视频/动图请求处理超时或未接入视频通道。")
    elif ev_gif.get("empty"):
        r.status = "fail"
        r.summary = "模型对动图思考到 token 上限仍未给出答案，大概率没有看到逐帧内容（视频通道未接入或被降级为静态图）。"
        r.notes.append("动图按视频解码时 prompt_tokens 应明显高于单张图片；数值偏低说明只送入了单帧或没有送入。")
    elif ev_gif.get("overlap", 0) >= 3 or (r.evidence.get("file", {}).get("overlap", 0) >= 3):
        r.status, r.summary = "warn", "模型识别出部分帧，但顺序或完整性不足。"
    else:
        r.status, r.summary = "fail", "模型未能读取动画内容，视频通道不可用或被网关降级为静态图。"
    return r
