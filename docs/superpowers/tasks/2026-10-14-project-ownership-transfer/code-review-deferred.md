# Deferred from code review — 2026-10-14-project-ownership-transfer (2026-10-14)

- **Concurrent ownership transfers can race** — `src/codemie/service/project/project_service.py:301` — `update_project_ownership` fetches the project then writes with no lock or version check, so two concurrent transfers can silently overwrite each other with no conflict signal. Pre-existing: `update_project` and the other project-mutation methods in this same file use the identical fetch-then-update pattern with no locking anywhere in the module; this change does not introduce the gap.
