# HITL L3 Demo Runbook — Phase 2D

Phase 2D demonstrates the human-in-the-loop path from Research Assistant intent to pipeline-ready intake artifacts. It does not let the UI execute Stage 3, apply patches, run GPU jobs, or auto-approve any gate.

## Preconditions

- Run from the project root.
- UI dependencies are installed with the `ui` extra.
- A DeepSeek-compatible API key is available only in local memory or environment, never in repo files.
- Real L3 execution requires a prepared dataset, target repository, benchmark environment, and explicit user approval.

Start the UI:

```bash
uv run --extra ui streamlit run src/autoad_researcher/ui/app.py
```

## Expected Demo Flow

1. Open `运行配置`, create or select a run ID, and enter the API key.
2. Open `研究助手` and use `意图澄清` mode to describe the research goal.
3. Click `生成研究意图草案`.
4. Review `runs/{run_id}/ui_chat/intent_draft.json` in the UI.
5. Click `确认采用` to write `runs/{run_id}/approvals/intent_confirmation.json`.
6. In `Pipeline 输入准备`, click `生成 input_task.yaml`.
7. Run Stage 3 from the terminal. With no patch approval, the expected stop is `patch_applicator` blocked.
8. Return to the UI, review the patch plan/diff, and confirm `Patch Plan Approval`.
9. Run Stage 3 again. With no real execution approval, the expected stop is `runner_execute` blocked.
10. Return to the UI, review execution risk, and confirm `Real Execution Approval`.
11. Set the real-execution kill switch only for the terminal command that should run L3:

```bash
export AUTOAD_L3_REAL_EXECUTION_ALLOWED=1
```

12. Run Stage 3 again from the terminal.
13. Unset the kill switch after the run:

```bash
unset AUTOAD_L3_REAL_EXECUTION_ALLOWED
```

## Blocked Sequence

```text
no input_task.yaml
-> intake blocked

input_task.yaml exists, but no patch_approval
-> patch_applicator blocked

patch_approval exists, but no run_approval
-> runner_execute blocked

run_approval exists and AUTOAD_L3_REAL_EXECUTION_ALLOWED=1
-> runner_execute may execute
```

To demonstrate the intent gate specifically:

```text
no approved intent_confirmation
-> patch_planner blocked_missing_approval:intent_confirmation
```

## Important Boundary

`AUTOAD_L3_REAL_EXECUTION_ALLOWED=1` is only the terminal-side real-execution kill switch. It does not bypass `run_approval.json`; `runner_execute` still requires the UI/user approval artifact.

## Artifact Verification

After a demo attempt, inspect the HITL artifact path without running the pipeline:

```bash
uv run python scripts/verify_hitl_artifacts.py --run-id run_hitl_demo_001
```

A blocked verifier result is not automatically a failure. It tells the operator which human step or pipeline stage has not produced its expected artifact yet.

## Not In Scope

- Web login
- Multi-user permissions
- Remote approvals
- Database storage
- UI-triggered real L3 execution
- LLM-generated approvals
- Direct chat-to-patch execution
