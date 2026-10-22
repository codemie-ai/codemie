> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Commits: fb63a0596 (clone policy, models, drift guard + test), plus a follow-up line-length fix commit (see `git log -2`).
Changed paths:
- src/codemie/rest_api/models/mcp_config.py
- tests/codemie/rest_api/models/test_mcp_config_clone_policy.py

Test: `poetry run pytest tests/codemie/rest_api/models/test_mcp_config_clone_policy.py -v`
RED: ImportError cannot import name 'CLONE_POLICY_VALUES'. GREEN: 7 passed (plan said 6; parametrization yields 7). ruff check/format clean.
