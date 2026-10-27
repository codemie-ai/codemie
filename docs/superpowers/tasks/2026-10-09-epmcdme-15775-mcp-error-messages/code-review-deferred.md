# Deferred from code review — review-backend-3 (2026-10-09)

- **Unescaped exception text in catalog-resolve log** — `src/codemie/service/mcp/access_control.py:252` — `{e}` is interpolated raw into the "failed to resolve catalog config" warning. Pre-existing: the line is not changed by this MR and the text comes from admin-authored catalogue data, not from a user-supplied server name.
