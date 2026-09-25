# Code review resolution — EPMCDME-14888 router default propagation

Reviewed: `261e74ead` vs `origin/main` (`eb87dcb50`), gate `code-review.final`, compact profile.
Verdict as written: `code-review-final.json` (request-changes, confidence low: no spec, edge-case lens only).

## Findings

| ID | Title | Resolution |
|---|---|---|
| CR-001 | Default router breaks default-model cost fallback | **Won't fix — by design.** Billing is always charged on the actual model a router resolves to, not on the router entry, so a cost-less default router does not affect billed usage. |
| CR-002 | Router-first default diverges from category defaults | **Fixed in `597052c04`.** `default_llm_model`, `get_default_model_for_category` and `get_default_models_by_category` now share `_prefer_litellm_router`. Covered by `test_global_default_lookups_agree_when_router_and_concrete_both_default` (RED before the fix, GREEN after). |

## Deferred items (`code-review-deferred.md`)

- **Concrete-default fallback ignores enabled flag** — partly addressed by `597052c04`: `default_llm_model` now prefers enabled defaults and only returns a disabled one when no enabled default exists.
- **Import-time default args freeze the YAML default** — still open; this predates the change.

## Validation

- `make ruff` — passed
- `poetry run pytest tests/codemie/service tests/codemie/configs -q` — 5669 passed, 125 skipped
- `poetry run pytest tests/codemie/configs/test_authorized_apps_config.py -q` — 25 passed (circular-import regression check)
