# AutoAD Assistant Router / State / Policy Architecture v0.5

Status: implementation guide for intent-alignment Alpha
Scope: user intent alignment only; no Stage 3 pipeline execution, no patch planning, no multi-agent runtime.

## 1. Design Rule

Prompt profiles are not user-behavior enumerations. They represent coarse AutoAD Assistant operating modes.

User input is normalized into `AssistantEvent` envelopes, `TransitionPolicy` decides the next `AssistantMode`, and `PromptSelector` maps that mode to a small prompt profile set.

```text
user input / source update / system event
-> AssistantEvent
-> TransitionPolicy
-> AssistantMode
-> PromptSelector
-> PromptRegistry profile
-> schema-bound response or deterministic runtime output
-> Core validation and artifacts
```

## 2. v0.5 Conversation Contract

AutoAD Assistant should avoid becoming a form interviewer.

The desired behavior is:

```text
Probe first.
Propose before asking.
Ask only blocking questions.
Treat artifacts as evidence, not user confirmation.
Separate research goal from implementation approach.
```

The Assistant may suggest candidate baseline, dataset, metric, budget, or scope, but it must not treat candidates as confirmed facts. It must not decide methods, algorithms, hyperparameters, patch hooks, implementation variants, or execution approval.

## 3. Component Boundaries

| Component | Owns | Does not own |
|---|---|---|
| `AssistantEvent` | Coarse event envelope | User behavior taxonomy |
| `AutoADAssistantSession` | Minimal control state | Full conversation memory or task form |
| `TransitionPolicy` | Mode transition and invariants | LLM calls or pipeline execution |
| `SessionStore` | Fixed assistant artifacts under `assistant/` | Arbitrary artifact reads |
| `PromptSelector` | Mode-to-stage and mode-to-prompt mapping | User semantic routing |
| `PromptRegistry` | Versioned prompt profiles | Runtime state or event classification |
| `ResearchTaskDraftV1` | Five-element task draft | Method, algorithm, hyperparameter, patch, or variant decisions |

## 4. Round 2 Implementation

Round 2 adds:

```text
src/autoad_researcher/assistant/session_store.py
src/autoad_researcher/assistant/prompt_selector.py
```

`SessionStore` writes only fixed run-relative assistant artifacts:

```text
assistant/session.json
assistant/events.jsonl
assistant/transitions.jsonl
```

`PromptSelector` uses explicit mappings:

```text
goal_alignment      -> collecting_goal          -> assistant.collecting_goal.v1
material_alignment  -> guiding_materials        -> assistant.guiding_materials.v1
artifact_processing -> parsing_materials        -> assistant.progress_digest.v1
intent_structuring  -> understanding_intent     -> assistant.understanding_intent.v1
task_confirmation   -> confirming_task_draft    -> assistant.confirming_task_draft.v1
pipeline_ready      -> ready_for_pipeline        -> assistant.confirming_task_draft.v1
progress_reporting  -> progress_reporting       -> assistant.progress_digest.v1
```

The mapping deliberately allows several modes to share a profile. It is not a user-behavior prompt explosion.

## 5. Deferred Work

Round 2 does not implement `silent_probe`, `WhatWeKnow`, runtime routing, LLM backend, UI flow, or pipeline bridge. Those remain for later rounds.

## 6. Round 3 silent_probe / WhatWeKnow

Round 3 adds:

```text
src/autoad_researcher/assistant/probe.py
tests/fixtures/silent_probe_fixture/
tests/test_assistant_probe.py
```

`silent_probe(run_id, runs_root)` uses `run_dir_path` and `KNOWN_ARTIFACT_MAP` only. It does not accept user-provided subpaths, does not call an LLM, does not run shell commands, and does not mutate run artifacts.

Current stable artifact map:

```text
baseline_contract  -> baseline_architecture_contract.json
repo_summary       -> repository_intelligence/repo_summary.json
paper_sources      -> paper/artifacts/paper_idea_sources.json
paper_summary      -> paper/artifacts/paper_summary.json
context_draft      -> context/research_context_draft.json
variants           -> transfer_design/implementation_variants.json
transfer_analysis  -> transfer_design/transfer_analysis.json
```

`WhatWeKnow` intentionally excludes `preflight_passed`, because there is no stable preflight report artifact. It also does not infer `category` or `metric_direction`; those remain missing fields until user confirmation or a future stable artifact provides them.

## 7. Round 4 Deterministic Runtime Skeleton

Round 4 adds:

```text
src/autoad_researcher/assistant/runtime.py
tests/test_assistant_runtime_skeleton.py
```

The deterministic runtime wires the local control path:

```text
route_user_text
-> silent_probe
-> TransitionPolicy
-> PromptSelector
-> FakeIntentAlignmentBackend
-> SessionStore
```

The fake backend has three required behaviors:

```text
with artifacts: propose from WhatWeKnow and ask only blocking gaps
without artifacts: guide the user toward minimal useful materials without a long form
correction: accept the correction and return to intent_structuring
```

It still does not call a real LLM, generate confirmed tasks, approve execution, modify code, or start Stage 3 pipeline work.

