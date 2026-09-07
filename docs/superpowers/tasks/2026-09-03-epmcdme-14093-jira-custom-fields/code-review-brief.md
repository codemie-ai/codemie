# Code review — 2026-09-03-epmcdme-14093-jira-custom-fields (2026-09-03)

**approve** · confidence: medium · 0 blocking · 12/12 prior findings accounted for
Coverage: targeted verifier ✓  (re-check of code-review-final.json, no lens fan-out)

## Fix-up verification

- 10 resolved by code, verified against current source at HEAD `7133a5a5a`
- 2 resolved by recorded owner decision (CR-004, CR-012) — no code change
- 0 unresolved · 0 superseded · 0 ungraded

## Resolved by code

- `src/codemie/datasource/loader/jira_loader.py:179` — [other: correctness] name index is now collision-aware and skips nameless fields; strict raises `AmbiguousCustomFieldException` (subclass of `InvalidCustomFieldException`), lenient picks the lowest field ID — CR-002, CR-003
- `src/codemie/datasource/loader/jira_loader.py:344` — [other: correctness] ADF walker guards null `content` via `(node.get('content') or [])` — CR-005
- `src/codemie/rest_api/routers/index.py:1582` — [public API] PUT now runs strict `check_jira_query` before persisting and maps failure to 422 — CR-008
- `src/codemie/rest_api/routers/index.py:2304` — [public API] `get_jira_datasource_fields` annotated `-> list[JiraFieldInfo]` — CR-009
- `src/codemie/rest_api/models/index.py:143` — [schema] `custom_fields: list[str] | None`; no `Optional[]`/`List[]` remains on any added source line — CR-006
- Test gaps closed at processor, model, route and health-check layers — CR-001, CR-007, CR-010, CR-011

## Resolved by decision

- CR-004 — lenient warn-and-skip kept by design; the persist-resolved-IDs + UI-surfacing remedy moved to its own EPMCDME ticket (needs codemie-ui work)
- CR-012 — commit `7d6b8f4d4 "Sonar fix"` kept; branch squash-merges under the ticket-prefixed MR title, no pipeline task validates commit subjects

## Checked and clean

commit-format · code-quality · security — carried forward from code-review-final.json, not re-audited this round
