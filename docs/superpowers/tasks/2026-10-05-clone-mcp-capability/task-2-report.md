> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Task 2: placeholders and URL / args sanitizing.

Commits: d6a65050a (implementation + tests), a0f6e715d (follow-up: remove unused import flagged by ruff in the test file; the first commit went in before ruff check was re-run correctly).

Changed paths:
- src/codemie/service/mcp/clone_constants.py (new)
- src/codemie/service/mcp/clone_sanitizer.py (new)
- tests/codemie/service/mcp/test_clone_sanitizer_url_args.py (new)

Deviation from plan: clone_sanitizer.py omits the plan's imports not yet used (fastapi status, ExtendedHTTPException, MCPServerConfigData, Any, Iterator) to keep ruff clean; later tasks add them when needed.

Test: `poetry run pytest tests/codemie/service/mcp/test_clone_sanitizer_url_args.py -v`
- RED: ModuleNotFoundError for codemie.service.mcp.clone_constants (EXIT=1)
- GREEN: 10 passed (EXIT=0)
- ruff check and ruff format clean on all three files.
