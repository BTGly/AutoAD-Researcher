#!/usr/bin/env python3
"""Verify Design Plan Seal Record SHA256 matches scoped document content."""

import hashlib
import re
import sys
from pathlib import Path


def get_seal_record_hash(content):
    m = re.search(r'document_sha256:\s*"([a-f0-9]{64})"', content)
    return m.group(1) if m else None


def get_seal_record_section_bounds(lines):
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## 16.3 Design Plan Seal Record":
            start = i
        if start is not None and i > start and line.strip() == "---":
            return (start, i + 1)
    return None


def compute_scoped_sha256(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    bounds = get_seal_record_section_bounds(lines)
    if not bounds:
        print("ERROR: cannot find Seal Record section boundaries", file=sys.stderr)
        sys.exit(2)

    start, end = bounds
    scoped_lines = [l for i, l in enumerate(lines) if i < start or i >= end]
    scoped = "".join(scoped_lines)
    return hashlib.sha256(scoped.encode("utf-8")).hexdigest()


def main():
    root = Path(__file__).resolve().parent.parent
    doc_path = root / "docs" / "3.6-3.7开发计划.md"

    if not doc_path.exists():
        print("ERROR: {} not found".format(doc_path), file=sys.stderr)
        sys.exit(2)

    content = doc_path.read_text(encoding="utf-8")
    recorded = get_seal_record_hash(content)
    if not recorded:
        print("ERROR: document_sha256 not found in Seal Record", file=sys.stderr)
        sys.exit(2)

    actual = compute_scoped_sha256(str(doc_path))

    if recorded == actual:
        print("OK: Seal Record hash matches ({})".format(actual))
        sys.exit(0)
    else:
        print("FAIL: Seal Record hash mismatch", file=sys.stderr)
        print("  recorded: {}".format(recorded), file=sys.stderr)
        print("  actual:   {}".format(actual), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
