from __future__ import annotations

from autoad_researcher.assistant.chat_facts import extract_confirmed_from_chat
from autoad_researcher.assistant.metric_normalization import canonicalize_metrics


def test_canonicalize_explicit_metric_phrases():
    assert canonicalize_metrics("看 AUROC") == ["image_level_auroc"]
    assert canonicalize_metrics("看 pixel AUROC") == ["pixel_level_auroc"]


def test_confirmed_from_chat_extracts_mvtec_patchcore():
    facts = extract_confirmed_from_chat([
        {"role": "user", "content": "mvtec，baseline 是 pathcore"},
    ])

    assert facts["dataset"] == "MVTec AD"
    assert facts["baseline"] == "PatchCore"


def test_confirmed_from_chat_extracts_feature_extractor_direction():
    facts = extract_confirmed_from_chat([
        {"role": "user", "content": "我想在特征提取器这里发力，先看 feature extractor"},
    ])

    assert facts["research_direction"] == "feature_extractor"


def test_confirmed_from_chat_extracts_24h_budget():
    facts = extract_confirmed_from_chat([
        {"role": "user", "content": "每个候选方法最多一天，越高越好，看 AUROC"},
    ])

    assert facts["budget"] == {"per_candidate_time_limit": "24h"}
    assert facts["metric_direction"] == "higher_is_better"
    assert facts["metrics"] == ["image_level_auroc"]


def test_confirmed_from_chat_extracts_framework_constraint():
    facts = extract_confirmed_from_chat([
        {"role": "user", "content": "baseline 是 PatchCore，我不可能改变基础框架"},
    ])

    assert facts["baseline"] == "PatchCore"
    assert facts["framework_constraint"] == "preserve_patchcore_core_pipeline"
