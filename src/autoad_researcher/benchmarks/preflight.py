"""Benchmark preflight aggregator — runs repo, dataset, env checks, collects report."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from autoad_researcher.benchmarks.dataset import build_dataset_manifest, resolve_dataset_root
from autoad_researcher.benchmarks.environment import collect_environment_snapshot
from autoad_researcher.benchmarks.errors import BenchmarkPreflightError
from autoad_researcher.benchmarks.evidence import (
    AllowedAttempt,
    BenchmarkDatasetManifest,
    BenchmarkEnvironmentSnapshot,
    BenchmarkPreflightCheck,
    BenchmarkPreflightReport,
    BenchmarkRepositoryState,
)
from autoad_researcher.benchmarks.repository import collect_repository_state


@dataclass(frozen=True)
class BenchmarkPreflightBundle:
    report: BenchmarkPreflightReport
    repository_state: BenchmarkRepositoryState | None
    dataset_manifest: BenchmarkDatasetManifest | None
    environment_snapshot: BenchmarkEnvironmentSnapshot | None


def _run_one(name: str, fn, *args, **kwargs):
    try:
        evidence = fn(*args, **kwargs)
        return evidence, BenchmarkPreflightCheck(name=name, status="passed", code=f"{name.upper()}_OK",
                                                  message=f"{name} contract satisfied")
    except BenchmarkPreflightError as exc:
        return None, BenchmarkPreflightCheck(name=name, status="failed", code=exc.code, message=exc.message)
    except Exception:
        return None, BenchmarkPreflightCheck(name=name, status="failed", code="PREFLIGHT_INTERNAL_ERROR",
                                              message="an unexpected internal preflight error occurred")


def run_preflight(*, case, repo_path: Path, benchmark_python: Path, lockfile_path: Path,
                  workspace_root: Path, attempt: AllowedAttempt,
                  environ: Mapping[str, str],
                  probe_runner=None) -> BenchmarkPreflightBundle:
    repo_ev, repo_check = _run_one("repository", collect_repository_state,
                                   case=case, repo_path=repo_path, workspace_root=workspace_root)
    ds_ev, ds_check = _run_one("dataset",
                               lambda: build_dataset_manifest(
                                   case=case,
                                   dataset_root=resolve_dataset_root(case=case, environ=environ, workspace_root=workspace_root),
                                   workspace_root=workspace_root))
    env_ev, env_check = _run_one("environment",
                                 lambda: collect_environment_snapshot(
                                     case=case, benchmark_python=benchmark_python,
                                     lockfile_path=lockfile_path, workspace_root=workspace_root,
                                     probe_runner=probe_runner))

    report = BenchmarkPreflightReport(
        schema_version=1, case_id=case.case_id, attempt=attempt,
        checks=[repo_check, ds_check, env_check],
        passed=all(c.status == "passed" for c in [repo_check, ds_check, env_check]),
    )
    return BenchmarkPreflightBundle(report=report, repository_state=repo_ev,
                                    dataset_manifest=ds_ev, environment_snapshot=env_ev)
