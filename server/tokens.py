"""Local rough token estimation.

This is NOT the official Kimi tokenizer. It is only used as a sanity reference
when neither the official tokenizer endpoint nor a stored baseline is available.
Kimi's tokenizer encodes CJK text at roughly 0.6-0.8 tokens per character and
English at roughly 1 token per 4 characters.
"""

from __future__ import annotations

import re

_CJK = re.compile(
    r"[一-鿿㐀-䶿　-〿＀-￯぀-ヿ가-힯]"
)
_ASCII_TOKEN = re.compile(r"[A-Za-z]+|\d+|\s+|[^\sA-Za-z\d]")


def estimate_tokens(text: str) -> int:
    """Return a rough token estimate for a piece of text."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    rest = _CJK.sub(" ", text)
    ascii_tokens = 0.0
    for piece in _ASCII_TOKEN.findall(rest):
        if piece.isspace():
            # Newlines usually cost a token, runs of spaces are cheap.
            ascii_tokens += piece.count("\n") * 0.5
        elif piece.isalpha():
            ascii_tokens += max(1.0, len(piece) / 4.2)
        elif piece.isdigit():
            ascii_tokens += max(1.0, len(piece) / 3.0)
        else:
            ascii_tokens += 1.0
    return int(round(cjk * 0.72 + ascii_tokens))


def estimate_messages(messages: list[dict]) -> int:
    """Rough estimate for a message list (adds a small per-message template cost)."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
        total += 4
    return total + 3
