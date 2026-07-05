# Intent Alignment Alpha Repair Report

Status: implemented
Date: 2026-07-05

## Scope

This repair covers the human-facing intent-alignment path:

```text
Source Intake -> Research Chat context -> guarded assistant reply -> ResearchTaskDraftV1 -> user confirmation boundary
```

It does not implement patch generation, benchmark execution, repository cloning,
automatic web search, multi-agent debate, or experiment variant design.

## Implemented Fixes

### Source And Artifact Grounding

- Added `ResearchChatEvidenceContext`, a structured context partition with:
  - `known_facts`
  - `candidate_references`
  - `uploaded_unparsed_sources`
  - `parsed_paper_evidence`
  - `missing_blocking_gaps`
  - `forbidden_assumptions`
- Kept existing Source Intake statuses. Reference identifiers are represented as:

```text
kind = arxiv_id / doi / url / github_repo
status = user_provided_not_ingested
```

- `uploaded_not_parsed`, `parsing`, and `failed` sources are not treated as
  parsed paper evidence.
- Parsed paper claims are only grounded when `silent_probe` finds paper artifacts.

### Response Guard

- Added deterministic guardrails that rewrite unsafe replies when the assistant:
  - claims paper content without parsed paper artifacts;
  - claims repository code facts without repository evidence;
  - promises code changes or experiment execution without approval;
  - answers artifact-grounded questions without parsed paper evidence.

### Reproduction Versus Transfer Ambiguity

- Added router labels for:
  - `ambiguous_reproduction_or_transfer`
  - `method_transfer`
  - `paper_reproduction`
  - `baseline_reproduction`
  - `execution_request`
- The deterministic runtime now treats:

```text
复现论文，看看能不能用到我的项目里
```

as ambiguous, not as a locked full reproduction task.

### Task Draft Boundaries

- Kept the existing `ResearchTaskDraftV1` schema.
- Added required boundary constraints through the artifact service:
  - 当前不决定 hook
  - 当前不决定具体 patch
  - 当前不决定超参数
  - 当前不启动实验
  - 不修改 evaluation 逻辑
  - 任务确认不等于代码修改批准或实验执行批准

## Automated Regression Coverage

Added or extended tests for:

```text
tests/test_assistant_research_context_builder.py
tests/test_assistant_response_guard.py
tests/test_assistant_runtime_skeleton.py
tests/test_assistant_task_artifacts.py
tests/test_ui_chat_prompts.py
```

Focused validation:

```text
65 passed
110 passed
```

## Remaining Work

- Run the full manual Alpha conversation table against the Streamlit UI.
- Record human Alpha outcomes if any wording still feels too rigid or too
  engineering-facing.
- Optional later refinement: add a richer scenario-fixture layer only if normal
  pytest string/state assertions become too brittle.

## Acceptance Statement

Intent Alignment Alpha Repair accepted for deterministic regression scope.
No patch, benchmark, clone, or experiment execution is included in this repair.
