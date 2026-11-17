# Deferred from code review — 2026-10-16-epmcdme-15605 (2026-10-16)

- **Replaced or orphaned icon files are never deleted** — `src/codemie/service/workflow_icon_service.py:59` — Uploaded icons stay in storage after a replace, a rejected save, or a workflow deletion, so objects accumulate. Pre-existing: the spec's non-goal explicitly excludes deleting replaced or orphaned icon files.
