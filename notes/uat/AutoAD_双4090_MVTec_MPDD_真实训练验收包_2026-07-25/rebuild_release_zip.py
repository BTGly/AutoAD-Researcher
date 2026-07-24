#!/usr/bin/env python3
"""Rebuild and verify the executable UAT ZIP from Git-safe Base64 parts."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import sys
import zipfile

EXPECTED_SHA256 = "95a3b970b5f29d52026aba178e3cca9ae667159e8e520a650db22349cb239077"
ZIP_NAME = "AutoAD_MVTec_MPDD_4090x2_UAT_2026-07-25.zip"
EXPECTED_BASE64_BYTES = 46_200
EXPECTED_ZIP_BYTES = 34_650


def main() -> int:
    root = Path(__file__).resolve().parent
    parts_dir = root / "release_b64"
    parts = sorted(parts_dir.glob("part[0-9][0-9]"))
    expected_names = [f"part{i:02d}" for i in range(8)]
    actual_names = [part.name for part in parts]
    if actual_names != expected_names:
        raise RuntimeError(
            f"release parts mismatch: expected {expected_names}, got {actual_names}"
        )

    encoded = b"".join(part.read_bytes().strip() for part in parts)
    if len(encoded) != EXPECTED_BASE64_BYTES:
        raise RuntimeError(
            f"Base64 payload length mismatch: {len(encoded)} != {EXPECTED_BASE64_BYTES}"
        )

    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RuntimeError(f"Base64 payload is invalid: {exc}") from exc

    if len(payload) != EXPECTED_ZIP_BYTES:
        raise RuntimeError(
            f"ZIP byte length mismatch: {len(payload)} != {EXPECTED_ZIP_BYTES}"
        )

    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"SHA256 mismatch: {digest} != {EXPECTED_SHA256}")

    output = root / ZIP_NAME
    temporary = root / f".{ZIP_NAME}.tmp"
    temporary.write_bytes(payload)

    try:
        with zipfile.ZipFile(temporary, "r") as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise RuntimeError(f"ZIP CRC failure: {bad_member}")
            members = archive.namelist()
            if not members:
                raise RuntimeError("ZIP contains no members")
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"ZIP central directory is invalid: {exc}") from exc

    temporary.replace(output)
    print(f"created: {output}")
    print(f"size: {output.stat().st_size} bytes")
    print(f"sha256: {digest}")
    print(f"members: {len(members)}")
    print("zip integrity: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
