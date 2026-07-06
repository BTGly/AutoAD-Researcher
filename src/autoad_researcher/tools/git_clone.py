"""Git clone ToolSpec for read-only repository acquisition."""

from autoad_researcher.tools.contracts import ToolSpec


def git_clone_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="git_clone",
        description=(
            "Acquire a repository through the repository_intelligence acquisition "
            "allowlist. Allowed operations are clone/init, remote add/get-url, fetch, "
            "detached checkout, status, rev-parse, and symbolic-ref. This tool is for "
            "read-only acquisition and attestation only: do not commit, push, amend, "
            "open pull requests, run project code, install dependencies, or execute "
            "benchmarks."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "minLength": 1},
                "workspace_root": {"type": "string", "minLength": 1},
                "remote_url": {"type": ["string", "null"]},
                "local_path": {"type": ["string", "null"]},
                "resolved_ref": {"type": ["string", "null"]},
                "resolved_commit": {"type": ["string", "null"], "pattern": "^[0-9a-f]{40}$"},
                "acquisition_profile": {
                    "type": "string",
                    "enum": ["shallow_ref", "partial_exact", "local"],
                },
            },
            "required": ["source_id", "workspace_root", "acquisition_profile"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "source": {"type": "object"},
                "attestation": {"type": "object"},
                "tool_calls": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["status"],
            "additionalProperties": True,
        },
        read_only=False,
        destructive=False,
        concurrency_safe=False,
        deferred=True,
        permission_category="repository_acquisition",
    )
