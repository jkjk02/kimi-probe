"""FastAPI application: serves the web UI and streams probe results over SSE."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .client import DEFAULT_BASE_URL, KimiClient
from .framework import STATUS_ORDER, ProbeContext, run_probe
from .probes import REGISTRY, specs_sorted

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
BASELINE_DIR = ROOT / "baselines"
REPORT_DIR = ROOT / "reports"

app = FastAPI(title="kimi-probe", version=__version__)


class RunRequest(BaseModel):
    base_url: str = Field(default=DEFAULT_BASE_URL)
    api_key: str
    model: str = "kimi-k3"
    probes: list[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)
    # Optional second key on the official platform used only for the tokenizer endpoint.
    reference_api_key: str | None = None
    reference_base_url: str | None = None


def normalize_base_url(url: str) -> str:
    """Strip trailing slashes and append /v1 if the path has no version segment."""
    import re
    url = (url or DEFAULT_BASE_URL).rstrip("/")
    if not re.search(r"/v\d+(/|$)", url):
        url += "/v1"
    return url


def load_baseline(model: str) -> dict:
    """Load baselines/<model>.json if present (official token counts for the built-in samples)."""
    candidates = [BASELINE_DIR / f"{model}.json", BASELINE_DIR / "default.json"]
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data.setdefault("meta", {})["file"] = path.name
                return data
            except ValueError:
                continue
    return {}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/meta")
async def meta() -> dict:
    baselines = sorted(p.stem for p in BASELINE_DIR.glob("*.json")) if BASELINE_DIR.exists() else []
    return {
        "version": __version__,
        "default_base_url": DEFAULT_BASE_URL,
        "models": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"],
        "baselines": baselines,
        "probes": [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "description": s.description,
                "cost": s.cost,
                "default_on": s.default_on,
                "reference": s.reference,
            }
            for s in specs_sorted()
        ],
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/run")
async def run(req: RunRequest, request: Request) -> StreamingResponse:
    if not req.api_key.strip():
        raise HTTPException(400, "api_key 不能为空")
    selected = [REGISTRY[p] for p in req.probes if p in REGISTRY] if req.probes else [s for s in specs_sorted() if s.default_on]
    selected.sort(key=lambda s: s.order)

    async def gen():
        queue: asyncio.Queue[str] = asyncio.Queue()
        client = KimiClient(normalize_base_url(req.base_url), req.api_key.strip())
        ref_client = None
        if req.reference_api_key and req.reference_api_key.strip():
            ref_client = KimiClient(normalize_base_url(req.reference_base_url or DEFAULT_BASE_URL), req.reference_api_key.strip())
        baseline = load_baseline(req.model)

        async def log(msg: str) -> None:
            await queue.put(_sse("log", {"ts": datetime.now().strftime("%H:%M:%S"), "msg": msg}))

        shared: dict = {}
        if ref_client is not None:
            shared["ref_client"] = ref_client
        ctx = ProbeContext(client=client, model=req.model, options=req.options, baseline=baseline, log=log, shared=shared)
        results: list[dict] = []
        started = datetime.now(timezone.utc)

        yield _sse("start", {
            "model": req.model,
            "base_url": client.base_url,
            "probes": [s.id for s in selected],
            "baseline": baseline.get("meta"),
            "started_at": started.isoformat(),
        })

        for spec in selected:
            if await request.is_disconnected():
                break
            yield _sse("probe_start", {"id": spec.id, "name": spec.name})
            task = asyncio.create_task(run_probe(spec, ctx))
            while not task.done():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield item
                except asyncio.TimeoutError:
                    # keep-alive comment for proxies
                    yield ": ping\n\n"
            while not queue.empty():
                yield queue.get_nowait()
            result = task.result()
            results.append(result.to_dict())
            yield _sse("probe_result", result.to_dict())

        await client.aclose()
        if ref_client is not None:
            await ref_client.aclose()
        summary = summarize(results)
        report = {
            "version": __version__,
            "model": req.model,
            "base_url": client.base_url,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "baseline": baseline.get("meta"),
            "summary": summary,
            "results": results,
        }
        report_path = save_report(report)
        yield _sse("done", {"summary": summary, "report_file": report_path.name if report_path else None})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def summarize(results: list[dict]) -> dict:
    counts = {k: 0 for k in STATUS_ORDER}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    scored = [r for r in results if r["status"] in ("pass", "warn", "fail")]
    score = 0.0
    if scored:
        score = sum({"pass": 1.0, "warn": 0.5, "fail": 0.0}[r["status"]] for r in scored) / len(scored) * 100
    hard_fail_ids = {"identity", "tokenizer", "hidden_prompt", "params", "thinking", "streaming"}
    hard_fails = [r["id"] for r in results if r["status"] == "fail" and r["id"] in hard_fail_ids]
    if hard_fails:
        verdict = "疑似非官方 / 被篡改"
    elif counts["fail"]:
        verdict = "部分能力不可用"
    elif counts["warn"]:
        verdict = "基本正常，有可疑项"
    else:
        verdict = "与官方 Kimi 行为一致"
    return {"counts": counts, "score": round(score, 1), "verdict": verdict, "hard_fails": hard_fails}


def save_report(report: dict) -> Path | None:
    try:
        REPORT_DIR.mkdir(exist_ok=True)
        name = f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{report['model']}.json"
        path = REPORT_DIR / name
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


@app.get("/api/reports")
async def list_reports() -> list[dict]:
    if not REPORT_DIR.exists():
        return []
    out = []
    for p in sorted(REPORT_DIR.glob("report-*.json"), reverse=True)[:50]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({"file": p.name, "model": data.get("model"), "base_url": data.get("base_url"), "finished_at": data.get("finished_at"), "summary": data.get("summary")})
        except ValueError:
            continue
    return out


@app.get("/api/reports/{name}")
async def get_report(name: str) -> FileResponse:
    if "/" in name or "\\" in name or not name.endswith(".json"):
        raise HTTPException(400, "bad name")
    path = REPORT_DIR / name
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="application/json")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("KIMI_PROBE_HOST", "127.0.0.1")
    port = int(os.environ.get("KIMI_PROBE_PORT", "8765"))
    uvicorn.run("server.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
