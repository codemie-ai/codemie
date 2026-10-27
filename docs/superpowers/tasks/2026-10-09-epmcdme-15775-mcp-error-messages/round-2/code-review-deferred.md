# Deferred from code review — review-backend-2 (2026-10-09)

- **Unescaped exception text in resolve_catalog_config log** — `src/codemie/service/mcp/access_control.py:247` — `{e}` from the catalog-config validation error is logged without repr/escaping and can be multi-line. Pre-existing: the `{e}` interpolation existed before this change (the diff only switched the name and id to repr); the text comes from an admin-authored catalog entry, not a user-supplied value.
