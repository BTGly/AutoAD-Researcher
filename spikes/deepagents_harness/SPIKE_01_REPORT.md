# Spike 01 Report: DeepAgentsHarness filesystem whitelist demo

## Scope

Step 1固化DeepAgents spike代码和离线验收证据，但不提交真实运行日志、LLM调用记录、token统计、`.env`或API key。

## Committed evidence

```text
spikes/deepagents_harness/
  README.md
  run_spike.py
  schema.py
  task.md
  task_security_test.md
  runs/
    run_demo/
      input_task.yaml
      paper_summary.json
```

## Excluded artifacts

```text
spikes/deepagents_harness/runs/run_demo/experiment_plan.json
spikes/deepagents_harness/runs/run_demo/patch_plan.json
spikes/deepagents_harness/__pycache__/
```

## Verification gate

`scripts/verify.sh` checks that the Spike 01 source files and static demo fixtures exist. The gate intentionally does not call `run_spike.py`, because that path depends on a real LLM API and GitHub Actions should remain offline-verifiable.
