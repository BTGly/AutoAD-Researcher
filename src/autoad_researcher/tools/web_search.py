"""Web search ToolSpec for candidate source discovery."""

from autoad_researcher.tools.contracts import ToolSpec


def web_search_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="web_search",
        description=(
            "Search the web for candidate sources only. Use 3-5 precise keywords, "
            "separate unrelated entities into separate searches, and treat results "
            "as leads rather than evidence until fetched and attested. Cite sources "
            "when search results inform a user-visible answer."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "type": {"type": "string", "enum": ["web", "news", "academic"]},
                "livecrawl": {"type": "boolean"},
                "numResults": {"type": "integer", "minimum": 1, "maximum": 10},
                "contextMaxCharacters": {"type": "integer", "minimum": 256, "maximum": 20000},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["results"],
            "additionalProperties": False,
        },
        read_only=True,
        destructive=False,
        concurrency_safe=True,
        deferred=True,
        permission_category="web",
    )
