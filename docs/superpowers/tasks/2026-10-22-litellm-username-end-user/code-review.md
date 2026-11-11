---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 046acbf6a2aa86cfac11c9f94f99cd3daa13b3a4
---

# Code review — 2026-10-22-litellm-username-end-user

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `046acbf` · **Rounds:** final → check

**Closed:** 2 fixed · 0 moot · 0 decided

Both prior blocking findings are resolved at HEAD+fix-up: CR-001 tests cover get_project_for_metric fallback, emit_llm_token_metric and the hard/soft limit reporters; CR-002 locals, log labels and docstring are renamed. The llm_factory personal-project comment stays deleted by the user's no-comments rule; 0 deferred, no new findings sought.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Approach 1a: authentication.py passes user.username to set_logging_info | pass | authentication.py:166; test added |
| Approach 1b: _setup_processing_context uses username or email or id | pass | base_datasource_processor.py:291 |
| Approach 1c: _resolve_user_email returns username or email or None | pass | core/dependecies.py _resolve_username |
| Approach 2: personal-project guard uses (personal_project or identity) | pass | llm_factory.py:620; None and empty fall back |
| Approach 3: pure rename user_email -> username across LiteLLM identity chain | partial (final round) | user_email locals remain in marketplace.py:387 and workflow.py:984; llm_factory comment deleted |
| Approach 4: MetricsAttributes USER_EMAIL/USER_NAME constants and labels unchanged | pass | only right-hand values renamed |
| AC4 inventory: readers renamed; pure propagation sites renamed only | pass | all listed sites updated |
| Non-goal: no change to genuine-email data | pass | personal_project=user.email untouched |
| Non-goal: no other behavior change; no code comments added | pass | other hunks are renames |
| AC1: code, Confluence/Jira and file indexing produce end_user=<username> | pass | live spend-log proof is Stage 7 |
| AC2: index search and tool invoke by service account produce end_user=<username> | pass | authenticate sets username |
| AC3: no new email-form end_user rows, checked live with repro.py | pending-stage-7 | live repro execution evidence pending |
| AC4: every reader in the inventory checked; label names unchanged | pass | Jira comment and dashboard owner check are non-code |
| AC5: unit tests for _setup_processing_context | pass | three parametrized cases |
| AC5: get_embeddings_model passes username | pass | test in test_core_dependencies.py |
| AC5: get_llm_by_credentials and _resolve_user_email username-wins and empty-username cases | pass | tests present |
| AC5: llm_factory guard tests | pass | five new tests |
| AC5: authentication sets username in logging context | pass | test_authentication.py |
| AC6: chargeback v3 follow-up and backfill linked in Jira | na | Jira-only deliverable |
| Renamed tests pass in affected modules and make ruff is clean | pending-stage-7 | execution evidence is Stage 7 |

Standards: commit-format pass · code-quality pass · security pass
