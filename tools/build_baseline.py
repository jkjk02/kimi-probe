"""Build a token-count baseline from the official Kimi tokenizer endpoint.

Run this once with an official platform key. The resulting JSON lets the probes
compare a relay's usage.prompt_tokens against official counts even when the
relay does not forward /v1/tokenizers/estimate-token-count.

Usage:
    set MOONSHOT_API_KEY=sk-...
    python tools/build_baseline.py --model kimi-k3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.client import DEFAULT_BASE_URL, KimiClient  # noqa: E402
from server.samples import HIDDEN_PROMPT_SAMPLES, TOKENIZER_SAMPLES  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="kimi-k3")
    parser.add_argument("--base-url", default=os.environ.get("MOONSHOT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("MOONSHOT_API_KEY"))
    parser.add_argument("--out", default=None, help="output path (default baselines/<model>.json)")
    args = parser.parse_args()
    if not args.api_key:
        print("error: set MOONSHOT_API_KEY or pass --api-key", file=sys.stderr)
        return 2

    client = KimiClient(args.base_url, args.api_key)
    samples: dict[str, dict] = {}
    try:
        for sid, text in TOKENIZER_SAMPLES + HIDDEN_PROMPT_SAMPLES:
            messages = [{"role": "user", "content": text}]
            res = await client.estimate_tokens(args.model, messages)
            if not res.ok:
                print(f"[{sid}] failed: HTTP {res.status} {res.error_message}", file=sys.stderr)
                return 1
            total = int(res.json["data"]["total_tokens"])
            samples[sid] = {"total_tokens": total, "chars": len(text)}
            print(f"[{sid}] {total} tokens")

        # Template overhead via doubling: overhead = 2*t(X) - t(X+X)
        x = HIDDEN_PROMPT_SAMPLES[2][1]
        r1 = await client.estimate_tokens(args.model, [{"role": "user", "content": x}])
        r2 = await client.estimate_tokens(args.model, [{"role": "user", "content": x + "\n\n" + x}])
        overhead = None
        if r1.ok and r2.ok:
            overhead = 2 * int(r1.json["data"]["total_tokens"]) - int(r2.json["data"]["total_tokens"])
            print(f"template overhead ≈ {overhead} tokens")
    finally:
        await client.aclose()

    out = Path(args.out) if args.out else ROOT / "baselines" / f"{args.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"model": args.model, "date": date.today().isoformat(), "base_url": args.base_url, "source": "official /v1/tokenizers/estimate-token-count"},
        "template_overhead": overhead,
        "samples": samples,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
