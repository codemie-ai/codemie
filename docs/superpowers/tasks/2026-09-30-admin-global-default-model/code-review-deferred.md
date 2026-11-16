# Deferred from code review — 2026-09-30-admin-global-default-model (2026-09-30)

- **Validate bulk model-status names** — `src/codemie/rest_api/routers/admin.py:574` — bulk status updates accept unknown catalog names. Pre-existing: the bulk status endpoint preceded the requested global-default feature and is outside its intent.
- **Reword older commit subjects** — `unknown` — two status-persistence-api commit subjects in the branch history omit the ticket prefix. Pre-existing: both commits preceded this global-default task; the standards audit still records commit-format as partial.
