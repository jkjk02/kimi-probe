"""Shared helpers for probes."""

from __future__ import annotations

from ..framework import ProbeContext


def is_k3(model: str) -> bool:
    return model.startswith("kimi-k3")


def is_k26(model: str) -> bool:
    return model.startswith("kimi-k2.6") or model.startswith("kimi-k2.5")


def is_k27(model: str) -> bool:
    return model.startswith("kimi-k2.7")


def base_payload(ctx: ProbeContext, messages: list[dict], max_tokens: int = 1024, **extra) -> dict:
    """Build a chat payload that respects per-model parameter constraints.

    - kimi-k3: reasoning always on, level via top-level reasoning_effort.
    - kimi-k2.6: thinking on by default; can be disabled via option.
    - kimi-k2.7-code: thinking always on, nothing to set.
    Temperature / top_p / n are never sent (fixed on the official platform).
    """
    payload: dict = {
        "model": ctx.model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if is_k3(ctx.model):
        effort = ctx.opt("reasoning_effort", "low")
        if effort in ("low", "high", "max"):
            payload["reasoning_effort"] = effort
    elif is_k26(ctx.model):
        if ctx.opt("k26_thinking", "enabled") == "disabled":
            payload["thinking"] = {"type": "disabled"}
    payload.update(extra)
    return payload


def usage_cached(usage: dict | None) -> int | None:
    """Return cached token count from either Kimi or OpenAI-style usage."""
    if not usage:
        return None
    if usage.get("cached_tokens") is not None:
        return int(usage["cached_tokens"])
    details = usage.get("prompt_tokens_details") or {}
    if details.get("cached_tokens") is not None:
        return int(details["cached_tokens"])
    return None


def usage_reasoning(usage: dict | None) -> int | None:
    if not usage:
        return None
    details = usage.get("completion_tokens_details") or {}
    if details.get("reasoning_tokens") is not None:
        return int(details["reasoning_tokens"])
    return None


def message_of(res_json: dict) -> dict:
    try:
        return res_json["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {}


def finish_of(res_json: dict) -> str | None:
    try:
        return res_json["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def norm_code(s: str) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())
