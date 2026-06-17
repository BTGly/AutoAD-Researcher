#!/usr/bin/env python3
"""Inspect host capabilities for benchmark environment planning."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from autoad_researcher.benchmarks.host_capabilities import collect_host_capabilities  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Inspect host capabilities")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    out = Path(args.output)
    if out.exists():
        print(f"error: output already exists: {out}", file=sys.stderr)
        return 2

    try:
        caps = collect_host_capabilities()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3 if "platform" in str(exc).lower() else 4
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 6

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(caps.model_dump(mode="json", exclude_none=True), indent=2))
    print(json.dumps(caps.model_dump(mode="json", exclude_none=True), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
