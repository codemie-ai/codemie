> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Commit: 69e4bef27

Changed paths:
- src/codemie/service/mcp_config_service.py
- tests/codemie/service/test_mcp_config_service_placeholder_warning.py (new)

RED: 3 failed (create/update/clone "warns" tests, `assert 'placeholder_value_present' in []`), 3 passed.
GREEN: `poetry run pytest tests/codemie/service/test_mcp_config_service_placeholder_warning.py tests/codemie/service/test_mcp_config_service_clone.py tests/codemie/service/test_mcp_config_service_auth_crud.py -v` -> EXIT=0, 46 passed.
ruff check and format clean.

Notes: create/update/clone return-site text matched the plan verbatim; ConfigWarning was already imported. The clone_constants and sanitizer imports were added as specified.
