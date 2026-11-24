# Deferred from code review — 2026-10-19-EPMCDME-9989 (2026-10-19)

- **setting_id ownership/project not validated** — `src/codemie/service/index/datasource_health_check_service.py:161` — the guard checks existence and ADO type only, not that the caller can access the setting. Pre-existing: Jira/Confluence/Xray health checks and SettingsService resolve setting_id the same way; this change did not introduce the gap.
