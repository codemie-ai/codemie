> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Commit: 1026e7a94 (EPMCDME-15538: Characterize OAuth2 auto-discovery for cloned MCP configs)

Changed paths:
- tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py (new)
- src/codemie/service/mcp_config_service.py (clone docstring Note)

Tests: `poetry run pytest tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py -v` -> 6 passed.
Re-run with tests/codemie/service/test_mcp_config_service_clone.py -> 21 passed. ruff check/format clean.

Findings: all no-challenge cases (no http cause, 401 without header, 403, 500) return None; a 401 with WWW-Authenticate returns a candidate that forwards allow_issuer_prefix_match. The plan's facts hold, so the stop rule did not fire.

Deviations from the plan (harness only):
- The plan names the class `ToolkitService`. The real class is `MCPToolkitService`, so the test imports and calls that.
- The docstring note was appended after the already-wrapped Raises text (the Raises lines had been wrapped in an earlier task) and names `MCPToolkitService`.
