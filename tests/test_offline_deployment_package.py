from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_offline_deployment_compose_has_no_build_context_and_keeps_worker_enabled():
    path = PROJECT_ROOT / "docker" / "docker-compose.offline.yml"
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))

    service = compose["services"]["autoad"]
    assert "build" not in service
    assert service["image"].startswith("${AUTOAD_IMAGE:?")
    assert service["environment"]["AUTOAD_EMBEDDED_WORKER"] == "${AUTOAD_EMBEDDED_WORKER:-1}"
    assert "runs_data:/app/runs" in service["volumes"]
    assert "config_data:/root/.autoad" in service["volumes"]
    assert service["healthcheck"]["test"][0] == "CMD"


def test_offline_package_scripts_are_shell_valid_and_documented():
    build_script = PROJECT_ROOT / "scripts" / "build_offline_image.sh"
    package_script = PROJECT_ROOT / "scripts" / "package_offline_deployment.sh"

    for script in (build_script, package_script):
        subprocess.run(["bash", "-n", str(script)], check=True)
        completed = subprocess.run(
            ["bash", str(script), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "Usage:" in completed.stdout

    guide = PROJECT_ROOT / "docs" / "deployment" / "offline-linux-amd64.md"
    text = guide.read_text(encoding="utf-8")
    assert "docker save" in text
    assert "docker load" in text
    assert "AUTOAD_EMBEDDED_WORKER=0" in text
    assert "离线安装" in text
    assert "运行时网络" in text


def test_verify_and_push_gate_detects_untracked_delivery_files():
    script = (PROJECT_ROOT / "scripts" / "verify_and_push.sh").read_text(encoding="utf-8")

    assert "git status --porcelain" in script
    assert "git diff --quiet && git diff --cached --quiet" not in script


def test_manual_offline_package_workflow_uses_existing_verified_package_script():
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "package-offline.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "bash scripts/verify.sh" in workflow
    assert "scripts/package_offline_deployment.sh" in workflow
    assert "docker build" not in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "retention-days: 14" in workflow
