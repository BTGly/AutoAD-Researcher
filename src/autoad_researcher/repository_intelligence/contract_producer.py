"""3.1 Repository Intelligence → baseline_architecture_contract producer.

Produces a BaselineArchitectureContract from existing repository analysis
artifacts when triggered by a RepositoryReanalysisRequest from Step 3.4.
"""

from autoad_researcher.schemas.baseline_architecture import (
    ArchitectureComponent,
    BaselineArchitectureContract,
    ExecutionPhaseContract,
    InterfaceSpec,
    ModificationHook,
)
from autoad_researcher.schemas.transfer_design import RepositoryReanalysisRequest


def produce_baseline_contract(
    request: RepositoryReanalysisRequest,
    repository_source_id: str,
    repository_commit: str,
    repository_summary: dict | None = None,
    entrypoints: list[dict] | None = None,
    modifiable_paths: list[dict] | None = None,
) -> BaselineArchitectureContract:
    """Produce a BaselineArchitectureContract from repository artifacts.

    This is a deterministic stub. In production, an LLM (repository_primary_v1)
    would populate detailed TensorSpec, ExecutionPhaseContract, and
    ModificationHook entries from the repository source code.

    The stub produces a minimal valid contract with:
    - model_name from repository_summary
    - basic architecture_components from modifiable_paths
    - placeholder phases and hooks
    - repository evidence tracing

    Args:
        request: the reanalysis request from Step 3.4.
        repository_source_id: fixed source id.
        repository_commit: resolved commit SHA (40-char hex).
        repository_summary: optional repository_summary dict.
        entrypoints: optional entrypoints list.
        modifiable_paths: optional modifiable_paths list.

    Returns:
        BaselineArchitectureContract (always valid, even if minimal).
    """
    model_name = _extract_model_name(repository_summary, entrypoints)

    components = _build_components(repository_summary, modifiable_paths)
    hooks = _build_hooks(modifiable_paths, request.target_hooks)
    phases = _build_default_phases()

    evidence_ids = []
    if repository_summary:
        evidence_ids.append("ev_repo_summary")
    if modifiable_paths:
        evidence_ids.append("ev_modifiable_paths")

    return BaselineArchitectureContract(
        model_name=model_name,
        repository_source_id=repository_source_id,
        repository_commit=repository_commit,
        architecture_components=components,
        phases=phases,
        tensors=[],
        modifiable_hooks=hooks,
        evidence_ids=evidence_ids,
    )


def _extract_model_name(
    repository_summary: dict | None,
    entrypoints: list[dict] | None,
) -> str:
    """Extract model name from repository artifacts."""
    if repository_summary:
        name = repository_summary.get("model_name") or repository_summary.get("name", "")
        if name:
            return name
    if entrypoints:
        for ep in entrypoints:
            name = ep.get("model") or ep.get("module", "")
            if name:
                return name
    return "unknown_model"


def _build_components(
    repository_summary: dict | None,
    modifiable_paths: list[dict] | None,
) -> list[ArchitectureComponent]:
    """Build ArchitectureComponent list from modifiable paths."""
    components: list[ArchitectureComponent] = []
    seen_roles: set[str] = set()

    if modifiable_paths:
        for i, mp in enumerate(modifiable_paths):
            role = mp.get("role", mp.get("purpose", f"component_{i}"))
            if role not in seen_roles:
                seen_roles.add(role)
                components.append(ArchitectureComponent(
                    component_id=f"comp_{i:03d}",
                    name=mp.get("name", mp.get("path", f"component_{i}")),
                    role=role,
                    semantic_description=mp.get("description") or mp.get("semantic_description") or f"Component {role}",
                    evidence_ids=["ev_modifiable_paths"] if modifiable_paths else [],
                ))

    if not components:
        components.append(ArchitectureComponent(
            component_id="comp_000",
            name="model",
            role="model",
            semantic_description="Model entrypoint",
        ))

    return components


def _build_hooks(
    modifiable_paths: list[dict] | None,
    target_hooks: list[str] | None,
) -> list[ModificationHook]:
    """Build ModificationHook list."""
    hooks: list[ModificationHook] = []

    if modifiable_paths:
        for i, mp in enumerate(modifiable_paths):
            path = mp.get("path", mp.get("module_path", ""))
            if not path:
                continue
            hooks.append(ModificationHook(
                hook_id=f"hook_{i:03d}",
                hook_name=mp.get("name", mp.get("semantic_role", f"hook_{i}")),
                module_path=path,
                symbol=mp.get("symbol"),
                semantic_role=mp.get("semantic_role", mp.get("role", "modifiable")),
                path_classification=mp.get("path_classification", "modifiable_candidate"),
                protected_reasons=mp.get("protected_reasons", []),
                allowed_for_transfer_design=mp.get("allowed_for_transfer_design", True),
                evidence_ids=["ev_modifiable_paths"],
            ))

    if not hooks:
        hooks.append(ModificationHook(
            hook_id="hook_000",
            hook_name="default_hook",
            module_path="model.py",
            symbol="forward",
            semantic_role="inference",
            path_classification="modifiable_candidate",
            allowed_for_transfer_design=True,
            evidence_ids=[],
        ))

    return hooks


def _build_default_phases() -> list[ExecutionPhaseContract]:
    """Build default execution phases for anomaly detection baselines."""
    return [
        ExecutionPhaseContract(
            phase_id="phase_infer",
            phase="infer",
            uses_gradient=False,
            mutates_model_state=False,
            inputs=[
                InterfaceSpec(
                    interface_name="input_image",
                    tensors=[],
                    is_batched=True,
                )
            ],
            outputs=[
                InterfaceSpec(
                    interface_name="inference_output",
                    tensors=[],
                    is_batched=True,
                )
            ],
            evidence_ids=[],
        ),
    ]
