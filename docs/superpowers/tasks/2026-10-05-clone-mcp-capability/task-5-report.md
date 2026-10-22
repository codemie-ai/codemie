> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

# Task 5: MCPConfigService.clone

Commits: 29d42f22a (clone + tests), ca626e5fd (follow-up: docstring line length, E501 was missed in the first commit because of a failed sed).

Changed paths:
- src/codemie/service/mcp_config_service.py
- tests/codemie/service/test_mcp_config_service_clone.py (new)

TDD: RED = 15 errors (patch target `allocate_name` missing); GREEN = 15 passed.

Test command: `poetry run pytest tests/codemie/service/test_mcp_config_service*.py -q` -> EXIT=0, 98 passed (includes the 15 clone tests, no regressions).
Ruff check and format clean.

Notes:
- All helper names in the plan exist in mcp_config_service.py; no adaptation needed.
- No circular import: `import codemie.service.mcp.clone_sanitizer` then `codemie.service.mcp_config_service` works.
- Pre-existing failures unrelated to this task: tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py (2 tests) fail identically with the service file stashed.
- Starlette emits a deprecation warning for HTTP_422_UNPROCESSABLE_ENTITY (from the Task 3 validator); not touched here.
