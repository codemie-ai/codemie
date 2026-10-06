# Deferred from code review — 2026-10-06-epmcdme-15559-custom-tools (2026-10-06)

- **Basename-only workspace path collision** — `src/codemie/service/agent_workspace_service.py:779` — two run files from different owners with the same basename map to one workspace path and the second is silently skipped. Pre-existing: `_normalize_uploaded_file_path` (basename-only) and the known_paths skip predate this change.
