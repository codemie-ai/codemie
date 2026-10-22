# Deferred from code review — 2026-09-30-a2a-client-protocol (2026-09-30)

- **Legacy stored cards raise raw ValueError in handler** — `src/codemie/rest_api/handlers/assistant_handlers.py:1367` — `get_jsonrpc_url` raises for stored url-only or bedrock cards, surfacing as a 500 at chat time. Pre-existing: old-shape stored rows are explicitly slice 4 scope (already-triaged decision b), not this change.
