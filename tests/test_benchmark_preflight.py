"""测试 preflight aggregator."""
import subprocess
from types import SimpleNamespace

from autoad_researcher.benchmarks.preflight import run_preflight


def _make_case(commit="a" * 40):
    return SimpleNamespace(
        case_id="test",
        repository=SimpleNamespace(url="https://github.com/t/r", commit_sha=commit,
            entrypoint_path="README.md", config_path=None, dependency_files=[]),
        evaluation=SimpleNamespace(evaluator_paths=[], protected_paths=[]),
        dataset=SimpleNamespace(name="x", category="bottle", root_env="DS_ROOT"),
        fixed_parameters={"gpu": 0},
    )


class TestPreflightAggregator:
    def test_repo_fails_dataset_passes(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "datasets" / "mvtec" / "bottle" / "train" / "good").mkdir(parents=True)
        (ws / "datasets" / "mvtec" / "bottle" / "test" / "good").mkdir(parents=True)
        (ws / "datasets" / "mvtec" / "bottle" / "test" / "broken_large").mkdir(parents=True)
        (ws / "datasets" / "mvtec" / "bottle" / "ground_truth" / "broken_large").mkdir(parents=True)
        for png in ["train/good/001.png", "test/good/002.png", "test/broken_large/003.png",
                     "ground_truth/broken_large/003_mask.png"]:
            (ws / "datasets" / "mvtec" / "bottle" / png).write_text("x")

        (ws / "envs" / "benchmark" / "bin").mkdir(parents=True)
        py = ws / "envs" / "benchmark" / "bin" / "python"
        py.write_text("fake"); py.chmod(0o755)
        env_root = tmp_path / "configs" / "benchmarks" / "environments" / "e"
        env_root.mkdir(parents=True)
        lf = env_root / "lock.txt"; lf.write_text("x")
        def r(python, source, timeout):
            return subprocess.CompletedProcess(args=[], returncode=0,
                stdout='{"python_version":"3.8","platform":"linux","torch_version":"1","torch_present":true,"torchvision_version":"1","torchvision_present":true,"faiss_version":"1","faiss_present":true,"timm_version":"1","timm_present":true,"cuda_available":true,"cuda_device_count":1,"cuda_runtime":"11","gpu":{"index":0,"name":"G","memory_mb":8192}}', stderr="")

        bundle = run_preflight(case=_make_case(), repo_path=ws / "repos" / "nonexistent",
            benchmark_python=py, lockfile_path=lf, workspace_root=ws,
            attempt="attempt_01", environ={"DS_ROOT": str(ws / "datasets" / "mvtec")}, probe_runner=r)

        assert bundle.report.passed is False
        assert bundle.repository_state is None
        assert bundle.dataset_manifest is not None
        assert len(bundle.report.checks) == 3
