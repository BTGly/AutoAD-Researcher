"""Fail-closed physical readiness gate for the first 07H baseline only."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from autoad_researcher.benchmarks.config import compute_case_sha256
from autoad_researcher.benchmarks.patchcore_07h_data import verify_07h_data
from autoad_researcher.benchmarks.patchcore_attempt import WEIGHT_SHA256
from autoad_researcher.benchmarks.patchcore_smoke import build_patchcore_smoke_command_plan
from autoad_researcher.experiment.gpu import ResourceLease, resource_leases_path
from autoad_researcher.schemas import InternalBenchmarkCase

SMOKE_CASE_SHA256 = "faad414c83732280fd15bf78c5c05004989a1054ebeb65477334fe2353bf1a5b"


@dataclass(frozen=True)
class PhysicalReadinessInputs:
    case: InternalBenchmarkCase
    source_root: Path
    run_dir: Path
    repository_path: Path
    benchmark_python: Path
    lockfile_path: Path
    environment_spec_path: Path
    weight_path: Path
    required_free_vram_mb: int
    maximum_used_vram_mb: int


class PhysicalReadinessGate:
    """Validate prerequisites without creating an Attempt or allocating a GPU."""

    def __init__(self, *, command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self._command_runner = command_runner

    def check(self, inputs: PhysicalReadinessInputs) -> dict:
        blockers: list[str] = []
        case_sha = compute_case_sha256(inputs.case)
        if case_sha != SMOKE_CASE_SHA256:
            blockers.append("07H smoke case SHA-256 differs from the frozen 07H-A value")
        if inputs.required_free_vram_mb <= 0 or inputs.maximum_used_vram_mb < 0:
            blockers.append("GPU capacity thresholds must be explicit non-negative values")

        dataset = self._check_data(inputs, blockers)
        gpu = self._check_gpu(inputs, blockers)
        environment = self._check_environment(inputs, blockers)
        command = self._check_command_contract(inputs, blockers)
        protected_hashes = self._protected_hashes(inputs, blockers)
        self._check_runtime_safety(inputs, blockers)
        report = {
            "schema_version": 1,
            "status": "blocked" if blockers else "ready",
            "case_id": inputs.case.case_id,
            "case_sha256": case_sha,
            "gpu": gpu,
            "environment": environment,
            "dataset": dataset,
            "command": command,
            "protected_hashes": protected_hashes,
            "blockers": blockers,
        }
        _write_json(inputs.run_dir / "artifacts" / "07h" / "physical_readiness.json", report)
        return report

    def _check_data(self, inputs: PhysicalReadinessInputs, blockers: list[str]) -> dict:
        try:
            prepared = verify_07h_data(source_root=inputs.source_root, run_dir=inputs.run_dir)
            return {
                "train_manifest_sha256": prepared.train_manifest_sha256,
                "b_dev_manifest_sha256": prepared.b_dev_manifest_sha256,
                "b_test_manifest_sha256": prepared.b_test_manifest_sha256,
                "b_dev_projection": "data/b_dev",
                "b_test_projection": "data/b_test",
            }
        except Exception as exc:
            blockers.append(
                "07H data manifests/projections are not valid "
                f"({type(exc).__name__})"
            )
            return {}

    def _check_gpu(self, inputs: PhysicalReadinessInputs, blockers: list[str]) -> dict:
        try:
            result = self._command_runner(
                ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=15, shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            blockers.append("nvidia-smi is unavailable")
            return {}
        if result.returncode != 0:
            blockers.append("nvidia-smi did not return GPU inventory")
            return {}
        records = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 4:
                continue
            try:
                records.append({"index": int(parts[0]), "name": parts[1], "total_vram_mb": int(parts[2]), "used_vram_mb": int(parts[3])})
            except ValueError:
                continue
        device = next((item for item in records if item["index"] == 0), None)
        if device is None:
            blockers.append("GPU 0 is unavailable")
            return {"observed": records}
        if "RTX 4090" not in device["name"]:
            blockers.append("GPU 0 is not an RTX 4090")
        if device["total_vram_mb"] - device["used_vram_mb"] < inputs.required_free_vram_mb:
            blockers.append("GPU 0 has insufficient free VRAM")
        if device["used_vram_mb"] > inputs.maximum_used_vram_mb:
            blockers.append("GPU 0 exceeds the permitted pre-run usage threshold")
        return device

    def _check_environment(self, inputs: PhysicalReadinessInputs, blockers: list[str]) -> dict:
        report: dict[str, object] = {}
        repo = inputs.repository_path
        if not repo.is_dir() or not (repo / ".git").exists():
            blockers.append("pinned PatchCore repository is missing")
        else:
            commit = _git(repo, "rev-parse", "HEAD")
            report["repository_commit"] = commit or None
            if commit != inputs.case.repository.commit_sha:
                blockers.append("PatchCore repository commit differs from the frozen case")
            if _git(repo, "status", "--porcelain"):
                blockers.append("PatchCore repository must be clean")
        if not inputs.benchmark_python.is_file() or not os.access(inputs.benchmark_python, os.X_OK):
            blockers.append("locked benchmark Python is missing or not executable")
        if not inputs.lockfile_path.is_file() or not inputs.environment_spec_path.is_file():
            blockers.append("benchmark lockfile or environment spec is missing")
        else:
            try:
                spec = yaml.safe_load(inputs.environment_spec_path.read_text(encoding="utf-8"))
                expected = spec["lockfile_sha256"]
                observed = _sha256(inputs.lockfile_path)
                report["lockfile_sha256"] = observed
                if observed != expected:
                    blockers.append("benchmark lockfile SHA-256 differs from environment spec")
            except Exception:
                blockers.append("benchmark environment spec is malformed")
        if not inputs.weight_path.is_file():
            blockers.append("offline WideResNet50 weight is missing")
        elif _sha256(inputs.weight_path) != WEIGHT_SHA256:
            blockers.append("offline WideResNet50 weight SHA-256 differs from the frozen asset")
        if inputs.benchmark_python.is_file() and os.access(inputs.benchmark_python, os.X_OK) and repo.is_dir():
            env = {"PYTHONPATH": str(repo / "src"), "HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "0"}
            probe = self._command_runner(
                [str(inputs.benchmark_python), "-c", "import torch, patchcore; assert torch.cuda.is_available(); assert torch.cuda.device_count() >= 1"],
                check=False, capture_output=True, text=True, timeout=30, shell=False, env={**os.environ, **env},
            )
            report["torch_patchcore_probe_returncode"] = probe.returncode
            if probe.returncode != 0:
                blockers.append("locked Python cannot import PatchCore with CUDA available")
            help_result = self._command_runner(
                [str(inputs.benchmark_python), str(repo / inputs.case.repository.entrypoint_path), "--help"],
                check=False, capture_output=True, text=True, timeout=30, shell=False, env={**os.environ, **env},
            )
            report["patchcore_help_returncode"] = help_result.returncode
            if help_result.returncode != 0:
                blockers.append("PatchCore smoke command --help failed")
        return report

    def _check_command_contract(self, inputs: PhysicalReadinessInputs, blockers: list[str]) -> dict:
        try:
            plan = build_patchcore_smoke_command_plan(
                case=inputs.case,
                run_id=inputs.run_dir.name,
                attempt="baseline_seed_0",
                dataset_path="../data/b_dev",
            )
            if plan.environment.get("CUDA_VISIBLE_DEVICES") != "0" or plan.network is not False:
                blockers.append("smoke command violates frozen GPU/network contract")
            return {"command_id": plan.command_id, "expected_outputs": plan.expected_outputs, "timeout_seconds": plan.timeout_seconds}
        except Exception as exc:
            blockers.append(f"smoke command contract is invalid: {exc}")
            return {}

    def _protected_hashes(self, inputs: PhysicalReadinessInputs, blockers: list[str]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for relative in inputs.case.evaluation.protected_paths:
            path = inputs.repository_path / relative
            if not path.is_file():
                blockers.append(f"protected path is missing: {relative}")
            else:
                hashes[relative] = _sha256(path)
        return hashes

    def _check_runtime_safety(self, inputs: PhysicalReadinessInputs, blockers: list[str]) -> None:
        leases_path = resource_leases_path(inputs.run_dir)
        if leases_path.is_file():
            try:
                leases = [ResourceLease.model_validate(item) for item in json.loads(leases_path.read_text(encoding="utf-8"))]
                if any(lease.status == "active" for lease in leases):
                    blockers.append("an active ResourceLease already exists for this run")
            except Exception:
                blockers.append("ResourceLease evidence is malformed")
        if (inputs.run_dir / "baseline_seed_0").exists():
            blockers.append("baseline output directory already exists")


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repository, text=True, capture_output=True, check=False, shell=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
