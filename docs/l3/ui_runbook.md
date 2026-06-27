# L3 UI Runbook — Phase 1

## Start the UI

```bash
cd workspace/AutoAD-Researcher
uv run --extra ui streamlit run src/autoad_researcher/ui/app.py
```

Opens at `http://localhost:8501`.

## Pages

### 1. Run Config
Set run parameters: `run_id`, dataset root, provider URL, mode.
Enter DeepSeek API key via password field — it stays in memory only.

### 2. Preflight Runner
One-click `l3-preflight` execution (no API calls, no real L3).
Shows structured JSON result on completion.
Real L3 execution is NOT available from the UI; a copy-ready command is shown.

### 3. Artifact Explorer
Lists all stage directories and their files for the current `run_id`.
Each stage is an expandable section showing file names, sizes, and paths.

### 4. Execution Monitor
Displays key runtime artifacts in tabbed panels:
- Execution Manifest
- Runner Intake Report
- GPU Evidence
- Events (tail of `events.jsonl`)
Refresh button reloads from disk.

### 5. Final Review
Three-panel status summary:
- **Engineering:** pipeline completed, real patch applied
- **GPU Execution:** GPU verified, 3/0/0 units
- **Scientific:** improvement demonstrated or not

Also shows artifact chain (stage handoff SHAs), final facts JSON, and final report markdown.

## Browse an Existing Run

Default run ID is `run_l3_bottle_001`. Change it in the sidebar to browse other runs.

## Limitations (Phase 1)

- Approval checkpoints are NOT implemented. Pipeline does NOT pause for user input.
- Real L3 execution is NOT available from the UI. Run manually:
  ```bash
  AUTOAD_L3_REAL_EXECUTION_ALLOWED=1 uv run autoad stage3-acceptance \
    --run-id "$RUN_ID" --mode l3-preflight \
    --provider-base-url "https://api.deepseek.com" --json
  ```
- The UI is read-only for existing artifacts. It does not modify pipeline state.
