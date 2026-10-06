# Code review — 2026-09-01-epmcdme-12906-workflow-categories (2026-09-01)

**request-changes** · confidence: medium · 3 blocking · 0 deferred · 1 filtered as noise
Coverage: blind — n/a (compact profile) · edge-case ✓ · acceptance — n/a (no spec) · verification-gap — n/a (compact profile)  (1/4 lenses ran)

## Look here first

- `src/codemie/core/workflow_models/workflow_models.py:441` — [other: data-loss] categories silently wiped when PUT omits field; `[]` default passes `is not None` guard in `_update_workflow_values` — CR-001
- `src/codemie/rest_api/routers/workflow.py:302` — [infra] SQLAlchemy DB error inside `validate_category_ids` caught by broad `except Exception` and returned as HTTP 400, masking infra failure — CR-002
- `src/codemie/service/assistant/category_service.py:97` — [other: data-integrity] duplicate category IDs pass validation and are stored in JSONB; `validate_category_ids` deduplicates only for the diff check, not the return value — CR-003

## Checked and clean

1 finding dismissed: marketplace max-item guard is already enforced by `PublishWorkflowToMarketplaceRequest.categories Field(max_length=3)` and a `no_duplicate_categories` validator. Standards not expected for compact profile.
