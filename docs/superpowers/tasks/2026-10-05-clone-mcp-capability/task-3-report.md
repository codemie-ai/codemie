> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Commit: 48be54789

Changed paths:
- src/codemie/service/mcp/clone_sanitizer.py (added imports + validate_copy_values, sanitize_config, config_contains_placeholder)
- tests/codemie/service/mcp/test_clone_sanitizer_config.py (new)

TDD: RED seen (ImportError: cannot import name 'config_contains_placeholder'), then GREEN.
Test command: poetry run pytest tests/codemie/service/mcp/test_clone_sanitizer_config.py tests/codemie/service/mcp/test_clone_sanitizer_url_args.py -v
Result: 32 passed, EXIT=0. ruff check and ruff format clean.

Notes: _is_confidential_oauth2_auth_config exists in codemie.service.mcp_config_service (same name); lazy import works. One test assertion was split into an `expected` variable for line length; otherwise plan code verbatim.
