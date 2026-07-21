"""stdlib-only Worker child adapter for the locked PatchCore environment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def run(*, command_file: Path, repository: Path, command_sha256: str) -> int:
    attempt_dir = Path(os.environ["AUTOAD_ATTEMPT_DIR"]).resolve()
    if _sha256(command_file) != command_sha256:
        return 76
    command = json.loads(command_file.read_text(encoding="utf-8"))
    argv = command["argv"]
    protected = command["protected_paths"]
    _write_json(attempt_dir / "command.json", {"command_id": command["command_id"], "argv": argv, "command_sha256": command_sha256})
    before = _hash_protected(repository, protected); _write_json(attempt_dir / "protected_hash_before.json", before)
    result = subprocess.run([sys.executable, *argv], cwd=attempt_dir, env=os.environ.copy(), shell=False, check=False)
    after = _hash_protected(repository, protected); _write_json(attempt_dir / "protected_hash_after.json", after)
    if result.returncode != 0:
        return result.returncode
    if before != after:
        return 74
    report, values = _parse_csv(attempt_dir / command["results_path"], command["metrics"])
    _write_json(attempt_dir / "parsed_metrics.json", report)
    if report["status"] != "passed":
        return 75
    _write_json(attempt_dir / "metrics.json", values)
    return 0


def _parse_csv(path: Path, metrics: list[dict]) -> tuple[dict, dict[str, float]]:
    values: dict[str, float] = {}; parsed = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        row = next(item for item in rows if item.get("Row Names") == "mvtec_bottle")
        for metric in metrics:
            name = metric["name"]
            try:
                value = float(row[name])
                if not 0.0 <= value <= 1.0: raise ValueError("value outside [0, 1]")
                values[name] = value; parsed.append({"metric_name": name, "value": value, "required": metric["required"], "parse_status": "parsed"})
            except Exception as exc:
                parsed.append({"metric_name": name, "required": metric["required"], "parse_status": "invalid", "failure_message": str(exc)})
    except Exception as exc:
        parsed = [{"metric_name": item["name"], "required": item["required"], "parse_status": "missing", "failure_message": str(exc)} for item in metrics]
    required = [item for item in parsed if item["required"]]
    status = "passed" if all(item["parse_status"] == "parsed" for item in required) else "failed"
    return {"schema_version": 1, "metrics": parsed, "required_parsed": sum(item["parse_status"] == "parsed" for item in required), "required_total": len(required), "status": status}, values


def _hash_protected(repository: Path, paths: list[str]) -> dict[str, str]: return {path: _sha256(repository / path) for path in paths}
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()
def _write_json(path: Path, value: object) -> None: path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--command-file", required=True); parser.add_argument("--repository", required=True); parser.add_argument("--command-sha256", required=True)
    args = parser.parse_args(); return run(command_file=Path(args.command_file), repository=Path(args.repository), command_sha256=args.command_sha256)
if __name__ == "__main__": raise SystemExit(main())
