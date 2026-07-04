# System Prompt Reference Analysis for AutoAD Assistant

Status: reference-derived design notes
Scope: mature prompt engineering patterns that should influence AutoAD Assistant prompts and the Prompt Registry.
Non-goals: this file does not reproduce third-party system prompts verbatim and does not make those prompts part of AutoAD runtime behavior.

## 1. References Reviewed

Representative local references inspected for this pass:

```text
references/system-prompts/OPENAI/ChatGPT5-08-07-2025.mkd
references/system-prompts/OPENAI/Codex.md
references/system-prompts/OPENAI/Codex_Sep-15-2025.md
references/system-prompts/XAI/GROK-4.1_Nov-17-2025.txt
references/system-prompts/CURSOR/Cursor_Prompt.md
references/system-prompts/CURSOR/Cursor_2.0_Sys_Prompt.txt
references/system-prompts/MANUS/Manus_Prompt.txt
references/system-prompts/ANTHROPIC/CLAUDE-FABLE-5.md
references/system-prompts/_INDEX.md
```

The files above are used as architecture references only. AutoAD should borrow design patterns, not copy product text.

## 2. Patterns Worth Borrowing

### 2.1 Layered Instruction Blocks

Mature prompts do not rely on one undifferentiated instruction blob. They separate identity, product facts, tone, safety, tool use, search behavior, persistence, final answer format, and environment constraints.

AutoAD translation:

```text
Global Invariants
Assistant State Prompts
Schema-Bound Draft Prompts
Pipeline Specialist Prompts
User-Facing Progress Prompts
Prompt Registry
```

### 2.2 Tool and Capability Boundaries

Coding agents and general assistants define what tools can do, when to use them, and what not to claim. They also separate user-facing language from internal tool names.

AutoAD translation:

```text
The assistant may suggest and summarize.
AutoAD Core registers sources, writes artifacts, validates schemas, enforces permissions, and runs pipeline stages.
Prompts must not claim they executed code, parsed files, generated reports, or approved actions unless artifacts prove it.
```

### 2.3 Project/Workspace Instruction Precedence

Coding agents emphasize repository-local instructions, scoped rules, status checks, and verification before finalizing work.

AutoAD translation:

```text
Prompt output must obey run artifact boundaries, schema contracts, decision-source protocol, and approval/automation gates.
Prompt profiles should record input_schema, output_schema, required_artifacts, produced_artifacts, and forbidden_outputs.
```

### 2.4 Event Stream and Planner Separation

Automation agents distinguish user messages, tool actions, observations, planner state, knowledge, and datasource documentation. This prevents the LLM from treating every message as the same type of evidence.

AutoAD translation:

```text
conversation/chat_transcript.jsonl
conversation/assistant_understanding.jsonl
conversation/user_corrections.jsonl
input/source_manifest.json
task/research_task_draft.json
task/research_task_confirmed.json
```

The future AssistantSession should distinguish user text, parsed material summaries, candidate parameters, confirmed parameters, missing slots, and progress events.

### 2.5 User-Facing vs Internal Output

Mature products hide internal tool names, raw paths, and irrelevant machinery in normal conversation, while preserving enough internal state for audit and debugging.

AutoAD translation:

```text
Default UI: task name, status, next action, research draft, progress digest.
Developer info: run_id, artifact_dir, provider, raw artifacts, gate reports, LLM context.
```

This matches the Phase 2F Research Assistant UX simplification.

### 2.6 Freshness and Evidence Rules

General assistants often have explicit rules for when to search, when to cite, and when to admit uncertainty. Research/search products emphasize source evidence and synthesis.

AutoAD translation:

```text
Do not write unsupported inference as fact.
Do not claim scientific improvement unless final facts and metrics support it.
Distinguish preliminary progress from final results.
Show uncertainty and missing evidence explicitly.
```

### 2.7 Memory and Persistence as Product Features

Some mature prompts define memory/persistent storage separately from ordinary chat. That separation matters because persistence changes user expectations and privacy boundaries.

AutoAD translation:

```text
Chat transcript is not the research task.
Assistant understanding is not user confirmation.
Research task draft is not confirmed task.
Confirmed task is not approval to patch or execute.
```

## 3. Patterns AutoAD Should Not Borrow Directly

AutoAD should avoid copying these patterns literally:

```text
Huge monolithic product prompts.
General-purpose policy bundles unrelated to anomaly-detection research.
Tool-name-heavy user-facing language.
Always-call-tool or always-plan loops.
Opaque hidden state without artifacts.
Chat messages treated as execution approval.
```

AutoAD needs a smaller, auditable, domain-specific prompt system tied to artifacts and schemas.

## 4. Prompt Registry Refinement Decisions

This review motivates the following registry refinements:

```text
assistant.collecting_goal.v1
  Open-ended goal exploration. User-visible. No long form. No hidden defaults.

assistant.guiding_materials.v1
  Material guidance. User-visible. Prioritizes P0/P1/P2 materials.

assistant.understanding_intent.v1
  Structured understanding. Internal. Separates user raw goal, confirmed facts, candidate parameters, missing slots, and uncertainty.

assistant.confirming_task_draft.v1
  User-facing confirmation prompt. Presents the task draft and asks for explicit confirm/revise/supply-material decision.

assistant.research_task_draft.v1
  Schema-bound draft generation. Internal. Produces future research task book artifacts.

assistant.progress_digest.v1
  User-facing progress newsfeed. Hides internals and distinguishes preliminary vs final evidence.
```

## 5. Engineering Guardrails

The prompt system remains a registry foundation only:

```text
No LLM calls are added here.
No Stage 3 pipeline behavior changes here.
No multi-agent runtime is introduced here.
No approval gate is bypassed here.
No prompt output is trusted without downstream schema validation.
```

Future work should add `AutoADAssistantSession` and state-machine transitions before any prompt profile starts controlling product behavior.
