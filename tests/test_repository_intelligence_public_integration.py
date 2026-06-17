"""R16 CI-safe public-repository-shaped integration fixtures."""

import json
import subprocess
from pathlib import Path

from autoad_researcher.repository_intelligence import read_evidence_index
from autoad_researcher.repository_intelligence.cli_runner import run_local_repository_intelligence


PUBLIC_REPOSITORY_FIXTURES = {
    "patchcore_ad": {
        "README.md": (
            "# PatchCore\n\n"
            "Research code for industrial anomaly detection.\n"
            "Use `python train.py` for training and `python eval.py` for evaluation.\n"
        ),
        "pyproject.toml": "[project]\nname = 'patchcore-fixture'\ndependencies = ['torch', 'timm']\n",
        "train.py": "def main():\n    pass\n",
        "eval.py": "def main():\n    pass\n",
    },
    "fastflow_ad": {
        "README.md": (
            "# FastFlow\n\n"
            "Normalizing-flow anomaly detection research fixture.\n"
            "Training uses tools/train.py and evaluation uses tools/evaluate.py.\n"
        ),
        "requirements.txt": "torch\ntimm\nscikit-learn\n",
        "tools/train.py": "def train():\n    pass\n",
        "tools/evaluate.py": "def evaluate():\n    pass\n",
    },
    "resnet_classifier": {
        "README.md": (
            "# ResNet Classifier\n\n"
            "Image classification research fixture, not an anomaly detection repository.\n"
            "Run train.py for supervised training and eval.py for top-1 accuracy.\n"
        ),
        "environment.yml": "name: classifier\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.11\n  - pytorch\n",
        "train.py": "def train():\n    pass\n",
        "eval.py": "def evaluate():\n    pass\n",
    },
}


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)


def make_git_repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=root)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run(["git", "add", "."], cwd=root)
    run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "fixture"], cwd=root)


def test_public_research_repository_fixtures_run_full_local_pipeline(tmp_path: Path):
    runs_root = tmp_path / "runs"

    for fixture_name, files in PUBLIC_REPOSITORY_FIXTURES.items():
        repo = tmp_path / "repos" / fixture_name
        make_git_repo(repo, files)

        summary = run_local_repository_intelligence(
            run_id=f"run_{fixture_name}",
            runs_root=runs_root,
            local_path=repo,
            resume=False,
        )
        run_dir = runs_root / f"run_{fixture_name}"

        repository_summary = json.loads((run_dir / "repository_summary.json").read_text(encoding="utf-8"))
        dependency_evidence = json.loads((run_dir / "dependency_evidence.json").read_text(encoding="utf-8"))
        environment_context = json.loads((run_dir / "environment_context.json").read_text(encoding="utf-8"))
        validation = json.loads((run_dir / "evidence_validation.json").read_text(encoding="utf-8"))
        clarification = json.loads((run_dir / "clarification_question_candidates.json").read_text(encoding="utf-8"))
        evidence_paths = {
            record.evidence.path
            for record in read_evidence_index(run_dir / "evidence_index.jsonl")
            if getattr(record.evidence, "source_kind", None) == "repository_file"
        }

        assert summary.status == "success"
        assert summary.validation_status == "passed"
        assert validation["status"] == "passed"
        assert repository_summary["repository_purpose"]["status"] == "confirmed"
        assert dependency_evidence["dependency_declaration_files"]
        assert environment_context["final_decision"] is False
        assert len(clarification["questions"]) <= 3
        assert "README.md" in evidence_paths
        assert any(path in evidence_paths for path in ["pyproject.toml", "requirements.txt", "environment.yml"])
        assert (run_dir / "environment_plan_candidate.json").is_file()
        assert (run_dir / "repository_intelligence_result.json").is_file()
