from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path
from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.executor_handoff import ExecutorAttemptHandoffService, ExecutorHandoffRequest
from autoad_researcher.experiment.executor_adapters import ExecutorAdapterInputs
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.patch_protocol import SearchReplaceEdit
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.worker.main import _process_pending_jobs

def _git(path: Path, *args: str): return subprocess.run(["git",*args],cwd=path,check=True,capture_output=True,text=True,shell=False).stdout.strip()
def _repo(root: Path, adapter_id: str):
    root.mkdir(); _git(root,"init","-b","main"); _git(root,"config","user.email","fixture@example.invalid"); _git(root,"config","user.name","fixture")
    (root/"evaluate.py").write_text("protected=True\n")
    (root/"run.py").write_text("import json, os\nfrom pathlib import Path\nPath(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 1}))\n")
    (root/"autoad_executor_adapter.json").write_text(json.dumps({"adapter_id":adapter_id,"entrypoint":"run.py","smoke_argv":[sys.executable,"run.py"],"metrics_output":"metrics.json","allowed_paths":["run.py"],"protected_paths":["evaluate.py"],"activation_evidence":"unverified"}))
    _git(root,"add","."); _git(root,"commit","-m","fixture"); return root
def _ready(run: Path):
    store=ExperimentSessionStore(); session,_=store.create_or_get(run,task_ref="task",task_hash="a"*64,execution_mode="agent_assisted_after_approval"); store.update_environment_state(run,session_id=session.session_id,status="READY_FOR_BASELINE",environment_status="ready",readiness_status="ready",readiness_blockers=[]); return session.session_id
def test_three_adapter_styles_handoff_to_existing_attempt_worker_and_finalizer(tmp_path: Path):
  for adapter_id in ["generic_python","patchcore_style","anomalib_style"]:
    run=tmp_path/adapter_id; repo=_repo(tmp_path/(adapter_id+"_repo"),adapter_id); sid=_ready(run)
    protected=run/"protected.py"; protected.parent.mkdir(parents=True,exist_ok=True); protected.write_text("fixed\n")
    contract=run/"evaluation.json"; contract.write_text(json.dumps({"metrics":[{"name":"score","required":True,"direction":"maximize","unit":"ratio","absolute_tolerance":0}],"evaluator_paths":["protected.py"],"protected_paths":["protected.py"],"raw_result_paths":["metrics.json"],"fingerprint_strategy":"repo_commit_paths_and_config_v1"}))
    hashes=run/"protected_hashes.json"; hashes.write_text(json.dumps({"schema_version":1,"hashes":{"protected.py":sha256_file(protected)}}))
    request=ExecutorHandoffRequest(session_id=sid,job_type="experiment_baseline",idempotency_key="executor:"+adapter_id,repository_path=repo,base_commit="HEAD",environment_snapshot_ref="environment/snapshot.json",adapter_inputs=ExecutorAdapterInputs(run_id=run.name,worktree_ref="ignored",repository_fingerprint="fixture",environment_sha256="b"*64,dataset_manifest_sha256="c"*64,asset_manifest_sha256="d"*64,python_executable=sys.executable),intervention_contract=InterventionContract(idea_id="idea_000001",mechanism="parameter",hypothesis="h",target_modules=["run.py"],allowed_paths=["run.py"],forbidden_paths=["evaluate.py"],time_budget=30),job_timeout_sec=30,evaluation_contract_ref="evaluation.json",evaluation_contract_sha256=sha256_file(contract),protected_artifact_report_ref="protected_hashes.json",protected_artifact_report_sha256=sha256_file(hashes))
    proposal=lambda _t: ExecutorProposal(edits=[SearchReplaceEdit(path="run.py",search="Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 1}))\n",replace="Path(os.environ['AUTOAD_ATTEMPT_DIR']).joinpath('metrics.json').write_text(json.dumps({'score': 2}))\n")],changed_symbols=["score"],confidence=1)
    first=ExecutorAttemptHandoffService().handoff(run,request=request,proposal_provider=proposal)
    second=ExecutorAttemptHandoffService().handoff(run,request=request,proposal_provider=proposal)
    assert first.status==second.status=="queued" and first.attempt["attempt_id"]==second.attempt["attempt_id"]
    for _ in range(50):
      _process_pending_jobs(run); a=ExperimentAttemptStore().load(run,first.attempt["attempt_id"])
      if a and a.runtime_status=="COMPLETED": break
      time.sleep(.02)
    artifact=run/"attempts"/first.attempt["attempt_id"]
    assert (artifact/"outcome_card.json").is_file() and json.loads((artifact/"outcome_card.json").read_text())["attempt_category"]=="scientifically_evaluable"
    assert (artifact/"executor_summary.json").is_file() and (artifact/"workspace.json").is_file() and (artifact/"patch.diff").is_file()
