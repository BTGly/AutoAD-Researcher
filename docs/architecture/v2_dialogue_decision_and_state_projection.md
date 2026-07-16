# V2 Dialogue Decision and State Projection Design

Status: approved implementation design

Scope: the FastAPI/React V2 research-dialogue control plane. This design does
not restore the removed Stage 3 pipeline, authorize code modification, or
authorize experiment execution.

## 1. Problem and Non-goals

The current V2 chain is:

```text
ResearchDecisionAgent -> DialogueGate -> ResearchReplyAgent
```

Its decision carries `dialogue_mode`, a research-policy assessment, and a
small number of candidate actions. In particular, `act_request` currently
means both “the user asked for an action” and “the action belongs to the
execution boundary.” That prevents the control plane from representing a
requested source operation, such as creating a new paper parse attempt,
without treating it as a code or experiment request.

This design separates interaction, action scope, permission, evidence, and
conversation state. It deliberately does not introduce keyword routing,
case-specific benchmark branches, a new generic agent loop, or a new
permission engine.

## 2. Design Principles

1. The model interprets language; deterministic code validates identifiers,
   state, permissions, idempotency, and side effects.
2. A user-visible interaction mode is not an authorization decision.
3. An action request is not necessarily a code or experiment action.
4. Scientific misconduct, insufficient evidence, and an infeasible target are
   distinct outcomes.
5. Persistent run artifacts, not an unbounded transcript, provide continuity.
6. A rejected or unapproved action must produce no job, source mutation,
   experiment task, code modification, or execution session.
7. Existing `PermissionEngine`, source registry, parse-attempt records,
   pipeline job ledger, TaskBridge, and event log remain the systems of record.

## 3. Orthogonal Decision Contract

The replacement decision contract contains the following independent axes.
The final Pydantic enum names are the contract below; the migration removes
the overloaded `reject` mode and the overloaded `act_request` name.

| Axis | Values | Meaning |
|---|---|---|
| `dialogue_mode` | `ask`, `plan`, `act` | Primary interaction requested by the user. |
| `action_scope` | `none`, `source`, `repository`, `code`, `experiment`, `system` | The highest scope of the proposed side effect. |
| `policy` | `allow`, `ask_permission`, `deny` | Whether the proposed action may proceed, needs explicit approval, or is prohibited. |
| `evidence_status` | `sufficient`, `insufficient`, `conflicting`, `unavailable` | Sufficiency of evidence for the requested conclusion. |
| `conversation_transition` | `new`, `continue`, `revise`, `confirm`, `cancel` | Relationship of this turn to persistent run state. |

The decision also carries two bounded scientific-communication assessments:

- feasibility: whether the stated objective is feasible, infeasible as stated,
  or not assessed from the current evidence;
- claim boundary: whether a precise predictive numeric claim is allowed from
  the available evidence.

The existing structured research-policy category remains only for an actual
policy denial such as evaluation leakage, evaluation manipulation, evidence
falsification, evidence destruction, or unsafe operation. It is not used for
an infeasible target or evidence insufficiency.

### 3.1 Required distinctions

| Situation | Interaction / policy / evidence result | Required reply behavior |
|---|---|---|
| “Guarantee 100% AUROC and 10x speed” | `plan` / `allow` / feasibility infeasible as stated | Do not promise success; reformulate as a measurable trade-off or hypothesis. |
| “From the abstract, predict the AUROC gain exactly” | `ask` or `plan` / `allow` / evidence insufficient, numeric claim forbidden | Refuse the numeric prediction while offering evidence-bounded risk analysis or a validation plan. |
| “Reparse the latest paper” with a registered paper source | `act` / source scope / permission derived by the Gate | Preserve old attempts and route a typed source action through authorization and idempotency checks. |
| “Modify evaluation to make results look better” | interaction may be any mode / `deny` / misconduct category | Remove every candidate action and provide the compliant alternative. |

The model must not treat the table as a lexical trigger list. It receives the
typed state projection and applies the contract semantically.

## 4. Typed Candidate Actions

Candidate actions are proposals, never direct mutations. Each action is a
typed schema with a precise target identifier and an action-specific payload.
The initial implementation covers the already-supported V2 boundaries:

| Action family | Example | System of record | Side-effect rule |
|---|---|---|---|
| source | request a new parse attempt, select an active parse attempt, request source removal | source registry + parse attempts | validate exact `source_id`; never overwrite prior attempts |
| repository | request analysis of a registered repository/target | source registry + Repository Intelligence jobs | validate source and Adapter selectors |
| task | prepare a plan-only task | TaskBridge | retain current confirmation contract |
| code / experiment / system | modify, run, remove data, unrestricted shell | existing permission/execution boundaries | unavailable from the V2 dialogue action dispatcher |

An action includes a deterministic idempotency identity derived from its
canonical target, action type, relevant parent artifact/version, and requested
payload. Repeating an equivalent pending or completed request returns the
existing record instead of creating a duplicate job.

The first source action is a reparse request. Its Gate validation requires:

1. the source id is registered and identifies a paper source with a stored
   input suitable for parsing;
2. the prior parse attempt remains immutable;
3. an equivalent queued or running parse action is not already present;
4. the authorization result permits the action;
5. the created job and event include the source id and action identity.

Whether an explicit user reparse request is immediate `allow` or requires a
second UI confirmation is a product-policy choice. The dispatcher must support
both outcomes without changing its state or idempotency rules.

## 5. Permission Integration

V2 uses the existing `autoad_researcher.tools.PermissionEngine`; it does not
introduce a second authorization implementation. The dialogue dispatcher
constructs a `PermissionRequest` from the typed action after identifier and
state validation, records the decision, and only dispatches on `allow`.

The profile is action-scope based:

```text
source/repository read or parse -> allowed only by their dedicated profile
destructive source mutation     -> ask
code/experiment/system          -> denied by the V2 dialogue dispatcher
```

The policy assessment from the model is a necessary input but not sufficient
authorization. `deny` removes actions before permission dispatch. A model
cannot grant itself access by emitting a tool name, path, source id, or
permission result.

This follows the same separation used by the local reference implementations:

- OpenHands SDK separates `SecurityRisk` from its confirmation policy;
- OpenCode associates a permission request with a tool, action, path, and
  session before executing it;
- DeepAgents matches filesystem permission by operation and path;
- MiMoCode uses persistent task state rather than prose alone to control
  continuation.

## 6. State Projection

`build_llm_context()` remains the single builder called once per turn. It is
extended with a read-only `dialogue_state` projection assembled before the
Decision call and passed unchanged to the Reply call.

The projection contains only bounded, persisted, user-task-relevant state:

| Field group | Source of truth | Purpose |
|---|---|---|
| previous interaction | last persisted gated decision/transition record | distinguish new, continue, revise, confirm, and cancel |
| research summary | `summary.json` | retain goal, confirmed facts, conflicts, and blocker |
| source state | source registry and active parse attempts | identify registered sources, active attempts, and immutable history |
| evidence state | V2 evidence service | distinguish usable, unavailable, and conflicting evidence |
| action state | action ledger plus pipeline jobs | expose pending/complete equivalent actions without replaying them |
| task state | TaskBridge and `input_task.yaml` | separate plan-only preparation from confirmed task/execution boundaries |
| authorization state | permission decisions and V2 capability profile | tell the model what can be proposed, never what it can bypass |

The projection excludes raw provider output, secret values, absolute host paths,
unbounded job histories, and arbitrary previous chat text. The existing bounded
transcript tail remains conversational context, but is not the state authority.

Every accepted gated decision appends a compact transition/audit record. That
record is the next turn's continuity input. A reply-summary write failure must
not manufacture a state transition.

## 7. Gate and Dispatch Order

```text
build state projection
  -> ResearchDecisionAgent proposes typed decision and actions
  -> schema validation
  -> policy consistency check
  -> exact target/state/idempotency validation
  -> PermissionEngine decision and audit record
  -> dispatch only allowed actions
  -> persist gated transition
  -> ResearchReplyAgent receives frozen gated decision plus projection
  -> persist validated summary
```

`ResearchReplyAgent` never dispatches actions and cannot alter the frozen
decision. If a decision, target, permission, or state check fails, the reply
describes the resulting boundary; it does not claim an action happened.

## 8. Benchmark Contract

The benchmark will move from one expected mode per case to declarative
preconditions and multi-axis behavior assertions. It must record the proposed
decision, gated decision, permission decision, created action/job identifiers,
and observable side effects.

Hard failures remain deterministic: unauthorized code or experiment activity,
policy-denied action dispatch, evidence overwrite, duplicate action/job,
cross-run contamination, and protected scientific-integrity violations.
Interaction usefulness, plan versus ask preference, and natural wording remain
semantic evaluation dimensions rather than fixed-string or single-label gates.

## 9. Compatibility and Verification

The migration updates all V2 decision producers, Gate consumers, reply prompt
inputs, API result projections, and deterministic tests together. No legacy
mode is silently reinterpreted.

Before this design is used for a live model run, verification must include:

1. schema and Gate unit tests for every axis and incompatible combination;
2. source reparse integration tests proving old attempts remain and duplicate
   requests do not create duplicate jobs;
3. permission audit tests for allow, ask, deny, and V2 code/experiment denial;
4. state-projection transition tests for revise, confirm, cancel, and recovery;
5. declarative benchmark fixture validation; and
6. the full project verification gate.
