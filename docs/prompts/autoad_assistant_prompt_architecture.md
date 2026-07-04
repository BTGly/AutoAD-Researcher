# AutoAD Assistant Prompt Architecture v0.1

Status: initial engineering design
Scope: AutoAD Assistant entry layer prompts, prompt registry, prompt versioning, and schema-bound prompt contracts.
Non-goals: this document does not redesign the Stage 3 pipeline, implement a multi-agent runtime, or let prompts bypass artifacts, Pydantic schemas, permissions, or approval gates.

## 1. Technical Conclusion

AutoAD Assistant must not be controlled by one large system prompt. It needs a prompt architecture made of:

```text
Layer 0: Global Invariants
Layer 1: Assistant State Prompts
Layer 2: Schema-Bound Draft Prompts
Layer 3: Pipeline Specialist Prompts
Layer 4: User-Facing Progress Prompts
Prompt Registry: versioned prompt selection and contracts
```

The design follows the project direction recorded in the repository docs:

```text
docs/reference_provenance.md
docs/AutoAD_参考资料汇总.md
docs/prompts/system_prompt_reference_analysis.md
```

It also borrows mature product patterns summarized in `references/system-prompts/_INDEX.md`, the overview files, and representative full prompt references under `references/system-prompts/OPENAI`, `references/system-prompts/XAI`, `references/system-prompts/ANTHROPIC`, `references/system-prompts/CURSOR`, and `references/system-prompts/MANUS`.

The detailed reference pass is recorded in:

```text
docs/prompts/system_prompt_reference_analysis.md
```

The useful patterns are structural rather than textual:

- major LLM products separate identity, safety, style, tool policy, memory, search, and output format;
- coding agents separate project instructions, tool contracts, execution workflow, verification, and repository rules;
- automation agents separate event stream, planner, knowledge, datasource, tool-use rules, and error handling;
- research/search products distinguish source evidence, synthesis, progress summaries, and report generation.

For AutoAD, these patterns translate into a simple rule:

```text
LLM asks, interprets, summarizes, and drafts.
AutoAD Core records, validates, gates, executes, and reports.
```

## 2. Layer 0: Global Invariants

Global invariants are inherited by every AutoAD Assistant prompt. They are not optional style hints.

Required invariants:

1. Do not fabricate execution results.
2. Do not claim code was modified, experiments were run, or reports were generated unless artifacts prove it.
3. Do not silently decide baseline, dataset, metrics, category, compute budget, or evaluation protocol.
4. Candidate parameters must be described as candidates, never as confirmed facts.
5. User-confirmed facts must not be rewritten by the LLM.
6. Hide raw paths, run_id, provider, stage names, and JSON/internal field names from ordinary user-facing replies.
7. Do not bypass Pydantic schemas or write free-form JSON artifacts.
8. Do not treat chat text as approval for patching, execution, or budget expansion.
9. Do not write unsupported inference as fact.
10. Explicitly state risks, failures, missing evidence, and uncertainty when they matter.

Current code anchor:

```text
src/autoad_researcher/assistant/prompt_profiles.py::GLOBAL_INVARIANTS_TEXT
```

## 3. Layer 1: Assistant State Prompts

Assistant prompts should be selected by persisted assistant state, not by a single flat chat mode.

Initial states:

```text
collecting_goal
  user goal is vague or just starting

guiding_materials
  user needs help deciding which materials to provide

registering_sources
  AutoAD Core is registering user-provided materials

parsing_materials
  parsers are producing normalized artifacts and summaries

understanding_intent
  assistant forms structured understanding from conversation and artifacts

confirming_task_draft
  user reviews and corrects a research task draft

ready_for_pipeline
  task boundary is confirmed and can bridge to existing pipeline

progress_reporting
  assistant summarizes long-running progress from events and artifacts
```

State prompts are allowed to talk to users, but they must not write artifacts directly. Artifact writes happen through AutoAD code paths and schema validation.

Current registry anchors:

```text
assistant.collecting_goal.v1
assistant.guiding_materials.v1
assistant.understanding_intent.v1
assistant.confirming_task_draft.v1
```

The first registry revision deliberately split `collecting_goal` and `understanding_intent`. The former is a user-visible exploration prompt; the latter is an internal structuring prompt that separates user raw goals, confirmed facts, candidate parameters, missing slots, and uncertainty.

## 4. Layer 2: Schema-Bound Research Task Draft Prompts

This layer generates the future research task book.

User-facing name:

```text
研究任务书草案
```

Planned artifacts:

```text
runs/{run_id}/task/research_task_draft.json
runs/{run_id}/task/research_task_draft.md
runs/{run_id}/task/research_task_confirmed.json
```

The prompt output must be schema-bound. It should distinguish:

```text
confirmed_parameters: user-provided or user-confirmed facts
candidate_parameters: detected or recommended but not confirmed
missing_slots: information still needed
input_sources: materials already registered
budget_policy: time/GPU/API/experiment limits
automation_policy: what the system may do automatically
failure_policy: retry, downgrade, skip, stop, or report behavior
report_requirements: final report questions
```

Current registry anchor:

```text
assistant.research_task_draft.v1
```

## 5. Layer 3: Pipeline Specialist Prompts

Pipeline specialist prompts remain planned work. They should not be implemented all at once before the entry layer stabilizes.

Priority:

```text
P0: paper intelligence, repository intelligence, task draft
P1: experiment planner, results analysis, final report
P2: advanced transfer design, advanced patch planner
```

Specialist prompts must consume evidence artifacts and produce schema-bound outputs. They must not read raw user materials directly unless the corresponding parser or source registry has produced controlled artifacts.

## 6. Layer 4: User-Facing Progress Prompts

AutoAD tasks may run for hours or days. User-facing progress prompts are not debug log summarizers.

Required output levels:

```text
progress_event
  structured event for system use

progress_digest
  short user-facing newsfeed item

stage_summary
  phase summary for user decisions
```

User-facing progress must hide raw paths, run_id, provider, internal stage names, and stack traces by default. It should say what happened, what evidence exists, what is uncertain, and what comes next.

Current registry anchors:

```text
assistant.run_explanation.v1
assistant.next_experiment.v1
assistant.progress_digest.v1
```

## 7. Prompt Registry

Prompt Registry is the engineering boundary between product design and prompt strings.

A prompt profile records:

```text
prompt_id
prompt_version
layer
assistant_stage
input_schema
output_schema
required_artifacts
produced_artifacts
forbidden_outputs
visibility
source_references
changelog
```

Current code anchors:

```text
src/autoad_researcher/assistant/prompt_io.py
src/autoad_researcher/assistant/prompt_profiles.py
src/autoad_researcher/assistant/prompt_registry.py
```

Prompt IDs use dotted lowercase names ending in `.vN`, for example:

```text
assistant.collecting_goal.v1
assistant.research_task_draft.v1
assistant.progress_digest.v1
```

## 8. Mapping Existing Prompts

The current `src/autoad_researcher/ui/chat_prompts.py` prompts are mapped or preserved in the registry without changing UI execution behavior:

```text
RUN_EXPLANATION_PROMPT
  -> assistant.run_explanation.v1

NEXT_EXPERIMENT_PROMPT
  -> assistant.next_experiment.v1
```

The legacy intent clarification behavior has now been split into dedicated state prompts:

```text
assistant.collecting_goal.v1
assistant.guiding_materials.v1
assistant.understanding_intent.v1
assistant.confirming_task_draft.v1
```

This keeps Phase 2F UI behavior stable while introducing the architecture needed for a stateful assistant.

## 9. Testing Rules

Prompt architecture tests must verify:

1. prompt IDs are unique;
2. every profile has strict schema metadata;
3. all user-visible prompts inherit global invariants when rendered;
4. artifact paths in prompt IO contracts are run-relative and safe;
5. existing chat prompts are represented in the registry;
6. candidate-vs-confirmed and no-fabrication rules are present;
7. no prompt profile claims it can execute pipeline actions.

## 10. Implementation Plan

P0 current step:

```text
- Add this architecture document.
- Add assistant prompt profile / registry / IO schemas.
- Register existing chat prompts.
- Add tests.
```

P1 next step:

```text
- Add AutoADAssistantSession schema.
- Add state machine transition checks.
- Add material guidance prompt profile.
- Add task draft builder skeleton.
```

P2 later:

```text
- Add specialist prompt profiles.
- Add progress digest artifacts.
- Evaluate hypothesis tree and multi-agent applicability.
```
