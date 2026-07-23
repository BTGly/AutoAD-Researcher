from __future__ import annotations

from autoad_researcher.source_normalizer import (
    extract_first_url,
    extract_source_candidates,
    is_repository_url,
    normalize_repository_reference,
    normalize_source_reference,
    source_kind_for_url,
)


def test_repository_url_with_following_chinese_text_normalizes_to_repo_root():
    candidate = normalize_repository_reference(
        "https://github.com/amazon-science/patchcore-inspection/；分析一下这个仓库，能clone"
    )

    assert candidate is not None
    assert candidate.source_kind == "github_repo"
    assert candidate.normalized_ref == "https://github.com/amazon-science/patchcore-inspection"
    assert candidate.owner == "amazon-science"
    assert candidate.repo == "patchcore-inspection"
    assert candidate.warnings == ["ignored_trailing_non_repo_path"]


def test_repository_url_typo_is_not_semantically_corrected():
    candidate = normalize_repository_reference("https://github.com/amazon-science/pathc0re-inspection")

    assert candidate is not None
    assert candidate.source_kind == "github_repo"
    assert candidate.normalized_ref == "https://github.com/amazon-science/pathc0re-inspection"
    assert candidate.repo == "pathc0re-inspection"


def test_known_mirror_repo_url_normalizes_git_suffix():
    candidate = normalize_repository_reference("https://gitee.com/example/patchcore-inspection.git")

    assert candidate is not None
    assert candidate.source_kind == "github_repo"
    assert candidate.normalized_ref == "https://gitee.com/example/patchcore-inspection"
    assert is_repository_url("https://gitee.com/example/patchcore-inspection.git")


def test_any_git_suffix_url_is_repository_source_without_host_allowlist():
    candidate = normalize_source_reference("https://code.example.edu/example-group/example-repo.git")

    assert candidate is not None
    assert candidate.source_kind == "github_repo"
    assert candidate.provider == "code.example.edu"
    assert candidate.normalized_ref == "https://code.example.edu/example-group/example-repo"
    assert is_repository_url("https://code.example.edu/example-group/example-repo.git")


def test_generic_host_without_git_suffix_stays_webpage_until_repo_intent_routes_it():
    source_candidate = normalize_source_reference("https://code.example.edu/example-group/example-repo")
    repo_candidate = normalize_repository_reference("https://code.example.edu/example-group/example-repo")

    assert source_candidate is not None
    assert source_candidate.source_kind == "webpage"
    assert repo_candidate is not None
    assert repo_candidate.source_kind == "github_repo"
    assert repo_candidate.normalized_ref == "https://code.example.edu/example-group/example-repo"


def test_plain_webpage_remains_webpage():
    url = "https://arxiv.org/abs/2303.15140v2"

    assert source_kind_for_url(url) == "webpage"
    assert is_repository_url(url) is False
    assert extract_first_url(f"读一下 {url}") == url


def test_extract_source_candidates_keeps_first_explicit_material_reference():
    candidates = extract_source_candidates(
        "资料一 https://arxiv.org/abs/2303.15140v2 代码 https://github.com/owner/repo"
    )

    assert [candidate.source_kind for candidate in candidates] == ["webpage", "webpage"]
    assert candidates[0].normalized_ref == "https://arxiv.org/abs/2303.15140v2"
    assert candidates[1].normalized_ref == "https://github.com/owner/repo"


def test_extract_source_candidates_stops_before_following_cjk_prose():
    candidates = extract_source_candidates(
        "执行仓库是：https://github.com/BTGly/autoad_micro_success_repo。这个仓库才是执行仓库。"
    )

    assert len(candidates) == 1
    assert candidates[0].normalized_ref == "https://github.com/BTGly/autoad_micro_success_repo"
