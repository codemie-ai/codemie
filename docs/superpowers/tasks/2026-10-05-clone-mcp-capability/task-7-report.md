> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Commit: bed2219c0 (EPMCDME-15538: Add POST /v1/mcp-configs/{id}/clone endpoint)

Changed paths:
- src/codemie/rest_api/routers/mcp_config.py
- tests/codemie/rest_api/routers/test_mcp_config.py (new)

Test command: `poetry run pytest tests/codemie/rest_api/routers/test_mcp_config.py -v`
- RED: 7 failed (route missing, 404s), as expected.
- GREEN: 7 passed, EXIT=0. `ruff check` and `ruff format` clean.

Test harness adaptation (one): tests run with ENV=local, where the `User` model validator sets `is_admin=True` at construction, so the plan's `regular_user` was actually an admin (403 tests failed with the service called). The fixture now sets `user.is_admin = False` and `user.is_maintainer = False` after construction. All asserted behaviors are unchanged. The `authenticate` dependency override, 403 error body shape and permission-before-validation all work as in the plan.
