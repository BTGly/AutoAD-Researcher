"""Web fetch ToolSpec wrapping SecureWebFetchProvider."""

from autoad_researcher.tools.contracts import ToolSpec


def web_fetch_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="web_fetch",
        description=(
            "Fetch a public HTTP(S) URL through SecureWebFetchProvider. Authenticated "
            "or credential-bearing URLs are not supported. The provider applies SSRF "
            "guards for localhost, private IPs, and unsafe redirects before content is "
            "used as source material."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1},
                "format": {"type": "string", "enum": ["text", "markdown", "html"]},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "status_code": {"type": "integer"},
                "content": {"type": "string"},
                "content_sha256": {"type": "string"},
            },
            "required": ["url", "status_code", "content", "content_sha256"],
            "additionalProperties": False,
        },
        read_only=True,
        destructive=False,
        concurrency_safe=True,
        deferred=True,
        permission_category="web",
    )
