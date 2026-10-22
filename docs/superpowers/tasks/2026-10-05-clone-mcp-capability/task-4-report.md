> **Superseded in part (2026-10-07):** describes work on stripping/`copy_values` that was later removed; see `spec.md` (revised) in this directory.

DONE

Commit: 66f3a0b1f (EPMCDME-15538: Add MCP clone copy-name allocation)

Changed paths:
- src/codemie/service/mcp/clone_naming.py (new)
- tests/codemie/service/mcp/test_clone_naming.py (new)

Test command: poetry run pytest tests/codemie/service/mcp/test_clone_naming.py -v
RED: ImportError (cannot import name 'clone_naming'), EXIT=1.
GREEN: 9 passed, EXIT=0.
ruff check and ruff format clean on both files.
