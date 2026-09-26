"""Thin async HTTP client for the Kimi (Moonshot AI) OpenAI-compatible API.

Every call records wall-clock timing and returns raw headers so that probes can
fingerprint the gateway in front of the model.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)

# Response headers worth surfacing for gateway fingerprinting.
INTERESTING_HEADERS = (
    "server",
    "x-request-id",
    "msh-request-id",
    "x-msh-request-id",
    "cf-ray",
    "cf-cache-status",
    "via",
    "x-powered-by",
    "x-envoy-upstream-service-time",
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "content-type",
    "date",
    "alt-svc",
)


@dataclass
class HttpResult:
    status: int
    json: Any
    text: str
    headers: dict[str, str]
    elapsed_ms: float
    url: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def error(self) -> dict | None:
        if isinstance(self.json, dict) and "error" in self.json:
            err = self.json["error"]
            return err if isinstance(err, dict) else {"message": str(err)}
        if not self.ok:
            return {"message": self.text[:300], "type": f"http_{self.status}"}
        return None

    @property
    def error_type(self) -> str | None:
        err = self.error
        return err.get("type") if err else None

    @property
    def error_message(self) -> str:
        err = self.error
        if not err:
            return ""
        return str(err.get("message") or err)


@dataclass
class StreamResult:
    """Aggregated view of a streamed chat completion."""

    status: int
    headers: dict[str, str]
    url: str
    t_start: float
    t_first_byte: float | None = None
    t_first_chunk: float | None = None
    t_first_reasoning: float | None = None
    t_first_content: float | None = None
    t_last_chunk: float | None = None
    t_end: float | None = None
    chunks: list[dict] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    reasoning: str = ""
    content: str = ""
    tool_calls: dict[int, dict] = field(default_factory=dict)
    usage: dict | None = None
    usage_chunk_had_empty_choices: bool | None = None
    finish_reason: str | None = None
    done_seen: bool = False
    role_chunks: int = 0
    model_names: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)
    error: dict | None = None
    text_on_error: str = ""

    def ms(self, t: float | None) -> float | None:
        return None if t is None else round((t - self.t_start) * 1000, 1)

    @property
    def ttfb_ms(self) -> float | None:
        return self.ms(self.t_first_byte)

    @property
    def ttft_ms(self) -> float | None:
        """Time to first token of any kind (reasoning or content)."""
        cands = [t for t in (self.t_first_reasoning, self.t_first_content) if t is not None]
        return self.ms(min(cands)) if cands else None

    @property
    def ttfc_ms(self) -> float | None:
        """Time to first visible content token."""
        return self.ms(self.t_first_content)

    @property
    def total_ms(self) -> float | None:
        return self.ms(self.t_end)

    @property
    def generation_ms(self) -> float | None:
        first = None
        cands = [t for t in (self.t_first_reasoning, self.t_first_content) if t is not None]
        if cands:
            first = min(cands)
        if first is None or self.t_last_chunk is None:
            return None
        return round((self.t_last_chunk - first) * 1000, 1)

    @property
    def content_chunks(self) -> int:
        return sum(1 for c in self.chunks for ch in (c.get("choices") or []) if (ch.get("delta") or {}).get("content"))

    @property
    def looks_pseudo_stream(self) -> bool:
        """True when a non-trivial answer arrived in one or two bursts."""
        gen = self.generation_ms
        total_text = len(self.content) + len(self.reasoning)
        if total_text < 60:
            return False
        if self.content_chunks <= 2 and not self.reasoning:
            return True
        return gen is not None and gen < 150

    @property
    def tokens_per_second(self) -> float | None:
        gen = self.generation_ms
        if not gen or gen < 50 or not self.usage:
            return None
        completion = self.usage.get("completion_tokens")
        if not completion:
            return None
        return round(completion / (gen / 1000.0), 1)

    def tool_calls_list(self) -> list[dict]:
        return [self.tool_calls[k] for k in sorted(self.tool_calls)]


def pick_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in INTERESTING_HEADERS:
        v = headers.get(k)
        if v is not None:
            out[k] = v
    return out


class KimiClient:
    def __init__(self, base_url: str, api_key: str, timeout: httpx.Timeout = DEFAULT_TIMEOUT):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ helpers
    @property
    def root_url(self) -> str:
        """Base URL without the trailing /v1 (used for /anthropic and similar)."""
        if self.base_url.endswith("/v1"):
            return self.base_url[:-3]
        return self.base_url

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "kimi-probe/1.0",
        }
        if extra:
            h.update(extra)
        return h

    async def request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        *,
        absolute: bool = False,
        headers: dict | None = None,
        files: Any = None,
        data: Any = None,
    ) -> HttpResult:
        url = path if absolute else f"{self.base_url}{path}"
        hdrs = self._headers(headers)
        if files is not None:
            hdrs.pop("Content-Type", None)
        result = await self._send(method, url, json_body, hdrs, files, data)
        # Relays often return transient 5xx / network errors; retry once so a single
        # gateway hiccup is not reported as a missing capability.
        if files is None and (result.status == 0 or result.status >= 500):
            await asyncio.sleep(1.5)
            result = await self._send(method, url, json_body, hdrs, files, data)
        return result

    async def _send(self, method: str, url: str, json_body: Any, hdrs: dict, files: Any, data: Any) -> HttpResult:
        t0 = time.perf_counter()
        try:
            resp = await self._client.request(
                method, url, json=json_body, headers=hdrs, files=files, data=data
            )
        except httpx.HTTPError as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return HttpResult(0, {"error": {"type": "network_error", "message": str(exc)}}, str(exc), {}, elapsed, url)
        elapsed = (time.perf_counter() - t0) * 1000
        text = resp.text
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None
        return HttpResult(resp.status_code, parsed, text, pick_headers(resp.headers), round(elapsed, 1), url)

    # ------------------------------------------------------------- endpoints
    async def list_models(self) -> HttpResult:
        return await self.request("GET", "/models")

    async def balance(self) -> HttpResult:
        return await self.request("GET", "/users/me/balance")

    async def estimate_tokens(self, model: str, messages: list[dict]) -> HttpResult:
        return await self.request(
            "POST", "/tokenizers/estimate-token-count", {"model": model, "messages": messages}
        )

    async def chat(self, payload: dict) -> HttpResult:
        payload = dict(payload)
        payload["stream"] = False
        return await self.request("POST", "/chat/completions", payload)

    async def tools_search(self, query: str, limit: int = 5) -> HttpResult:
        return await self.request(
            "POST", "/tools/search", {"text_query": query, "limit": limit, "timeout_seconds": 15}
        )

    async def upload_file(self, filename: str, content: bytes, purpose: str, mime: str) -> HttpResult:
        return await self.request(
            "POST",
            "/files",
            files={"file": (filename, content, mime)},
            data={"purpose": purpose},
        )

    async def delete_file(self, file_id: str) -> HttpResult:
        return await self.request("DELETE", f"/files/{file_id}")

    async def anthropic_messages(self, payload: dict) -> HttpResult:
        url = f"{self.root_url}/anthropic/v1/messages"
        return await self.request(
            "POST",
            url,
            payload,
            absolute=True,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )

    async def responses(self, payload: dict) -> HttpResult:
        return await self.request("POST", "/responses", payload)

    # --------------------------------------------------------------- streaming
    async def chat_stream(self, payload: dict, *, include_usage: bool = True) -> StreamResult:
        result = await self._chat_stream_once(payload, include_usage=include_usage)
        if result.error and (result.status == 0 or result.status >= 500) and not result.chunks:
            await asyncio.sleep(1.5)
            result = await self._chat_stream_once(payload, include_usage=include_usage)
        return result

    async def _chat_stream_once(self, payload: dict, *, include_usage: bool = True) -> StreamResult:
        payload = dict(payload)
        payload["stream"] = True
        if include_usage:
            payload["stream_options"] = {"include_usage": True}
        url = f"{self.base_url}/chat/completions"
        t_start = time.perf_counter()
        result = StreamResult(status=0, headers={}, url=url, t_start=t_start)
        try:
            async with self._client.stream("POST", url, json=payload, headers=self._headers()) as resp:
                result.status = resp.status_code
                result.headers = pick_headers(resp.headers)
                if resp.status_code != 200:
                    body = await resp.aread()
                    result.text_on_error = body.decode("utf-8", "replace")
                    try:
                        parsed = json.loads(result.text_on_error)
                        result.error = parsed.get("error", parsed) if isinstance(parsed, dict) else {"message": result.text_on_error[:300]}
                    except ValueError:
                        result.error = {"type": f"http_{resp.status_code}", "message": result.text_on_error[:300]}
                    result.t_end = time.perf_counter()
                    return result
                async for line in _iter_sse_lines(resp, result):
                    self._consume_line(line, result)
        except httpx.HTTPError as exc:
            result.error = {"type": "network_error", "message": str(exc)}
        result.t_end = time.perf_counter()
        return result

    def _consume_line(self, line: str, result: StreamResult) -> None:
        now = time.perf_counter()
        result.raw_lines.append(line)
        if not line.startswith("data:"):
            return
        data = line[5:].strip()
        if data == "[DONE]":
            result.done_seen = True
            return
        try:
            chunk = json.loads(data)
        except ValueError:
            return
        if result.t_first_chunk is None:
            result.t_first_chunk = now
        result.t_last_chunk = now
        result.chunks.append(chunk)
        if isinstance(chunk, dict):
            if chunk.get("model"):
                result.model_names.add(str(chunk["model"]))
            if chunk.get("id"):
                result.ids.add(str(chunk["id"]))
            if chunk.get("error"):
                result.error = chunk["error"]
            choices = chunk.get("choices") or []
            if chunk.get("usage"):
                result.usage = chunk["usage"]
                result.usage_chunk_had_empty_choices = len(choices) == 0
            for choice in choices:
                if choice.get("usage") and not result.usage:
                    result.usage = choice["usage"]
                if choice.get("finish_reason"):
                    result.finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("role"):
                    result.role_chunks += 1
                rc = delta.get("reasoning_content")
                if rc:
                    if result.t_first_reasoning is None:
                        result.t_first_reasoning = now
                    result.reasoning += rc
                c = delta.get("content")
                if c:
                    if result.t_first_content is None:
                        result.t_first_content = now
                    result.content += c
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = result.tool_calls.setdefault(
                        idx, {"id": None, "type": None, "function": {"name": None, "arguments": ""}}
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if tc.get("type"):
                        slot["type"] = tc["type"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]


async def _iter_sse_lines(resp: httpx.Response, result: StreamResult) -> AsyncIterator[str]:
    buffer = ""
    async for raw in resp.aiter_bytes():
        if result.t_first_byte is None:
            result.t_first_byte = time.perf_counter()
        buffer += raw.decode("utf-8", "replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                yield line
    if buffer.strip():
        yield buffer.strip()
