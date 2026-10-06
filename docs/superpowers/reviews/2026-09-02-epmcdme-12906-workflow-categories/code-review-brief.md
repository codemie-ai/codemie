# Code review — 2026-09-02-epmcdme-12906-workflow-categories (2026-09-02)

**request-changes** · confidence: medium · 1 still-open (CR-015) · 4 resolved · 10 not in scope this round
Coverage: targeted verifier ✓ (4/5 scoped findings verified; 1 unverifiable via source)

## Still open

- `unknown` — [commit-format] CR-015: commit subject rebase fix unverifiable — file 'unknown' not in changed_files and git log is outside check-round allow-list — CR-015

## Resolved this round

- `src/codemie/core/workflow_models/workflow_config.py:113` — [other: null-deser] CR-002: categories now list[str] + model_validator coerces null to [] — resolved
- `src/codemie/core/workflow_models/workflow_config.py:150` — [schema] CR-003: CheckConstraint ck_workflows_categories_max_3 in __table_args__ — resolved
- `src/codemie/core/workflow_models/workflow_models.py:438` — [schema] CR-004: per-item max_length=256 via Annotated on both request models — resolved
- `src/codemie/service/assistant/category_service.py:19` — [other: code-quality] CR-011: future annotations added; all signatures use modern typing forms — resolved

## Checked and clean

code-quality ✓ (CR-011 resolved) · security ✓ · commit-format ? unverified (CR-015 rebase fix not confirmable via source)
