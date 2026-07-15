from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from autoad_researcher.repository_intelligence import (
    RepositorySource,
    read_evidence_index,
    run_targeted_repository_analysis,
    targets_from_contract,
)


def _source() -> RepositorySource:
    return RepositorySource(
        schema_version=1,
        source_id="src_repo",
        kind="local_workspace",
        canonical_remote_url=None,
        requested_ref=None,
        acquisition_profile="local",
        resolved_commit="a" * 40,
        tree_sha="b" * 64,
        detached_head=False,
        dirty=False,
        local_path_label="repos/src_repo",
        source_fingerprint="c" * 64,
    )


def test_exact_contract_targets_are_projected_without_normalization():
    contract = SimpleNamespace(
        baseline_entrypoint="scripts/run.py",
        baseline_config="configs/eval.toml",
        user_target_module_hints=["TargetKernel", "targetkernel"],
    )

    targets = targets_from_contract(contract, job_targets=["TargetKernel"])

    assert [(target.source_field, target.value) for target in targets] == [
        ("baseline_entrypoint", "scripts/run.py"),
        ("baseline_config", "configs/eval.toml"),
        ("user_target_module_hints", "TargetKernel"),
        ("user_target_module_hints", "targetkernel"),
        ("job_payload", "TargetKernel"),
    ]


def test_targeted_analysis_records_exact_file_line_commit_and_snippet_hash(tmp_path: Path):
    repository_root = tmp_path / "repo"
    target_path = repository_root / "level2" / "40_Matmul_Scaling_ResidualAdd.py"
    target_path.parent.mkdir(parents=True)
    target_path.write_text(
        "class Model:\n"
        "    def forward(self, input_tensor):\n"
        "        return input_tensor\n",
        encoding="utf-8",
    )
    (repository_root / "README.md").write_text("Repository documentation.\n", encoding="utf-8")
    output_dir = tmp_path / "analysis"
    targets = targets_from_contract(SimpleNamespace(
        baseline_entrypoint=None,
        baseline_config="missing.toml",
        user_target_module_hints=["40_Matmul_Scaling_ResidualAdd.py", "forward"],
    ))

    result = run_targeted_repository_analysis(
        source=_source(),
        repository_root=repository_root,
        output_dir=output_dir,
        targets=targets,
        evidence_index_path=output_dir / "evidence_index.jsonl",
    )

    assert [resolution.status for resolution in result.resolutions] == ["not_evidenced", "found", "found"]
    basename_match = result.resolutions[1].matches[0]
    assert basename_match.path == "level2/40_Matmul_Scaling_ResidualAdd.py"
    assert basename_match.match_kind == "exact_basename"
    content_match = result.resolutions[2].matches[0]
    assert content_match.path == "level2/40_Matmul_Scaling_ResidualAdd.py"
    assert content_match.start_line == 2
    assert content_match.repository_commit == "a" * 40
    assert len(content_match.file_sha256) == 64
    assert len(content_match.snippet_sha256) == 64
    assert result.compatibility.status == "uncertain"
    assert any("missing.toml" in item for item in result.unresolved_facts)

    evidence = [record.evidence for record in read_evidence_index(output_dir / "evidence_index.jsonl")]
    by_id = {item.evidence_id: item for item in evidence}
    ref = by_id[content_match.evidence_id]
    assert ref.repository_commit == "a" * 40
    assert ref.path == "level2/40_Matmul_Scaling_ResidualAdd.py"
    assert ref.start_line == 2
    assert len(ref.snippet_sha256) == 64
    repository_map = json.loads((output_dir / "repository_map.json").read_text(encoding="utf-8"))
    assert repository_map["truncated"] is False
    assert {entry["path"] for entry in repository_map["files"]} == {
        "README.md",
        "level2/40_Matmul_Scaling_ResidualAdd.py",
    }


def test_no_exact_targets_builds_map_without_reading_repository_content(tmp_path: Path):
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / "module.py").write_text("value = 1\n", encoding="utf-8")
    output_dir = tmp_path / "analysis"

    result = run_targeted_repository_analysis(
        source=_source(),
        repository_root=repository_root,
        output_dir=output_dir,
        targets=[],
        evidence_index_path=output_dir / "evidence_index.jsonl",
    )

    assert result.files_read == 0
    assert result.resolutions == []
    assert result.compatibility.status == "uncertain"
    assert "No exact repository target identifiers" in result.unresolved_facts[0]
    assert not (output_dir / "evidence_index.jsonl").exists()


def test_repository_map_and_read_budget_are_explicitly_bounded(tmp_path: Path):
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    for index in range(5):
        (repository_root / f"file_{index}.txt").write_text(f"TargetValue {index}\n", encoding="utf-8")
    output_dir = tmp_path / "analysis"

    result = run_targeted_repository_analysis(
        source=_source(),
        repository_root=repository_root,
        output_dir=output_dir,
        targets=targets_from_contract(SimpleNamespace(
            baseline_entrypoint=None,
            baseline_config=None,
            user_target_module_hints=["TargetValue"],
        )),
        evidence_index_path=output_dir / "evidence_index.jsonl",
        map_file_limit=3,
        read_file_limit=2,
        read_byte_limit=1024,
    )

    repository_map = json.loads((output_dir / "repository_map.json").read_text(encoding="utf-8"))
    assert repository_map["truncated"] is True
    assert repository_map["omitted_file_count"] == 2
    assert result.files_read == 2
    assert any("read budget was exhausted" in item for item in result.unresolved_facts)
