"""Probe framework: shared context, result model and registry."""

from __future__ import annotations

import asyncio
import inspect
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .client import KimiClient

Status = str  # "pass" | "warn" | "fail" | "skip" | "info" | "error"

STATUS_ORDER = {"fail": 0, "error": 1, "warn": 2, "pass": 3, "info": 4, "skip": 5}


@dataclass
class ProbeResult:
    id: str
    name: str
    category: str
    status: Status = "info"
    summary: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    # Which sentence of the docs this probe relies on, shown in the UI.
    reference: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "summary": self.summary,
            "columns": self.columns,
            "rows": self.rows,
            "notes": self.notes,
            "evidence": self.evidence,
            "duration_ms": self.duration_ms,
            "reference": self.reference,
        }


@dataclass
class ProbeContext:
    client: KimiClient
    model: str
    options: dict[str, Any]
    baseline: dict[str, Any]
    log: Callable[[str], Awaitable[None]]
    shared: dict[str, Any] = field(default_factory=dict)

    def opt(self, key: str, default: Any = None) -> Any:
        v = self.options.get(key)
        return default if v is None else v


@dataclass
class ProbeSpec:
    id: str
    name: str
    category: str
    description: str
    run: Callable[[ProbeContext], Awaitable[ProbeResult]]
    cost: str = "low"  # low | medium | high  (rough token cost)
    default_on: bool = True
    order: int = 100
    reference: str = ""


REGISTRY: dict[str, ProbeSpec] = {}


def probe(
    id: str,
    name: str,
    category: str,
    description: str,
    *,
    cost: str = "low",
    default_on: bool = True,
    order: int = 100,
    reference: str = "",
):
    def deco(fn: Callable[[ProbeContext], Awaitable[ProbeResult]]):
        REGISTRY[id] = ProbeSpec(
            id=id,
            name=name,
            category=category,
            description=description,
            run=fn,
            cost=cost,
            default_on=default_on,
            order=order,
            reference=reference,
        )
        return fn

    return deco


def specs_sorted() -> list[ProbeSpec]:
    return sorted(REGISTRY.values(), key=lambda s: (s.order, s.id))


def new_result(spec: ProbeSpec) -> ProbeResult:
    return ProbeResult(id=spec.id, name=spec.name, category=spec.category, reference=spec.reference)


async def run_probe(spec: ProbeSpec, ctx: ProbeContext) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        result = await spec.run(ctx)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - we want every probe isolated
        result = new_result(spec)
        result.status = "error"
        result.summary = f"探针执行异常：{type(exc).__name__}: {exc}"
        result.evidence["traceback"] = traceback.format_exc()[-4000:]
    result.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    if not result.reference:
        result.reference = spec.reference
    return result


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 1)


def fmt_ms(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f} ms"


def short(text: Any, n: int = 160) -> str:
    s = str(text or "")
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


__all__ = [
    "ProbeContext",
    "ProbeResult",
    "ProbeSpec",
    "REGISTRY",
    "STATUS_ORDER",
    "fmt_ms",
    "inspect",
    "new_result",
    "pct",
    "probe",
    "run_probe",
    "short",
    "specs_sorted",
]
