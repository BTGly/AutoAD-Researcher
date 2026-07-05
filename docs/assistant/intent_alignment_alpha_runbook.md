# AutoAD Assistant Intent Alignment Alpha Runbook

Status: Round 7 manual validation guide
Scope: human-facing intent alignment only. Do not run Stage 3 pipeline, patch code, approve execution, or generate experiment variants during this Alpha.

## Purpose

This runbook checks whether AutoAD Assistant can help a real user converge from a vague anomaly-detection research intent to a confirmed `ResearchTaskDraftV1`.

The expected interaction style is:

```text
Probe first.
Propose before asking.
Ask only blocking questions.
Accept corrections quickly.
Confirm task boundary separately from execution approval.
```

## Required Setup

Use a run directory with either:

```text
1. existing artifacts equivalent to tests/fixtures/silent_probe_fixture
2. an empty run directory for the from-zero scenario
```

Do not use private API keys in transcripts. Do not write raw LLM calls, token counts, or provider secrets into the repo.

## Scenario A — Existing Artifacts Fast Path

User message:

```text
继续这个异常检测方向
```

Expected behavior:

```text
Assistant mentions known baseline/artifact facts.
Assistant does not ask for baseline from scratch.
Assistant only asks blocking gaps such as dataset/category/metric direction when missing.
Assistant does not decide method, algorithm, hyperparameters, patch hook, or variant.
```

## Scenario B — From-Zero Path

User message:

```text
我想做异常检测，但还没有整理材料
```

Expected behavior:

```text
Assistant does not show a long form.
Assistant suggests the most useful next material, such as paper/method description or target repository.
Assistant can proceed with a one-sentence goal if the user has no material yet.
```

## Scenario C — User Correction Path

User message after a proposal:

```text
不是，我不是想先复现，我想先明确评价指标
```

Expected behavior:

```text
Assistant acknowledges the correction.
Assistant returns to intent structuring.
Assistant updates the draft direction within 1-2 turns.
```

## Scenario D — Blocking Gap Path

Expected behavior:

```text
If `category` or `metric_direction` is missing, Assistant asks only those blocking gaps.
Assistant must not invent them from benchmark defaults.
```

## Scenario E — Task Confirmation Path

Expected behavior:

```text
Assistant writes task/research_task_draft.json and task/research_task_draft.md.
After explicit confirmation, Assistant writes task/research_task_confirmed.json.
Session may set ready_for_pipeline=true.
Session must keep execution_approved=false.
```

## Pass Criteria

```text
1. The user can understand what AutoAD already knows.
2. The assistant does not repeat questions answered by artifacts.
3. The assistant does not behave like a long intake form.
4. Corrections are accepted without argument.
5. ResearchTaskDraftV1 contains only goal/evaluation constraints, not method decisions.
6. confirmed task is separate from execution approval.
```

## Current Automated Coverage

`tests/test_assistant_alpha_scenarios.py` covers the deterministic regression version of the scenarios above. Real human Alpha remains a manual validation step.
