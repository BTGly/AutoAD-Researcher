# HITL L3 Demo Script — Phase 2D

Use this as the spoken/checklist script for a local demo.

## Opening

Today we demonstrate the safe path from research conversation to Stage 3 intake. The UI can write auditable intent and approval artifacts, but it cannot execute the pipeline or approve anything automatically.

## Script

1. Show `运行配置` and the selected run ID.
2. Show `研究助手` in `意图澄清` mode.
3. Ask the assistant for a research intent draft.
4. Open the generated `intent_draft.json` panel and summarize the goal, metrics, allowed scope, and forbidden scope.
5. Click `确认采用` and show `approvals/intent_confirmation.json` exists.
6. Show `Pipeline 输入准备` and click `生成 input_task.yaml`.
7. Explain that this is the bridge from UI intent to Stage 3 intake, not a pipeline execution.
8. Run the pipeline from a terminal and show the first expected approval block.
9. Use the UI to approve the patch plan only after reviewing the proposed diff and validation report.
10. Run the pipeline again and show the real-execution approval block.
11. Use the UI to approve real execution only after reviewing the risk text.
12. Set `AUTOAD_L3_REAL_EXECUTION_ALLOWED=1` in the terminal and run the final execution attempt.
13. Unset the environment variable.
14. Run `uv run python scripts/verify_hitl_artifacts.py --run-id <run_id>`.
15. Show final facts and explain whether the result supports a scientific claim.

## Safety Lines To Say Explicitly

- The Research Assistant is advisory.
- The UI writes artifacts, not patches.
- The pipeline enforces approvals again even if the UI is bypassed.
- The real-execution environment variable is not an approval substitute.
- Missing artifacts fail closed instead of silently continuing.
