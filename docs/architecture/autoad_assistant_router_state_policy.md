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
