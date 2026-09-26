"""Probe package: importing registers every probe."""

from . import basic, tokenizer, cache, perf, reasoning, web, multimodal, capability  # noqa: F401
from ..framework import REGISTRY, specs_sorted

__all__ = ["REGISTRY", "specs_sorted"]
