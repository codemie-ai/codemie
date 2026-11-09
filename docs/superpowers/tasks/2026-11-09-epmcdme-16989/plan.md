# EPMCDME-16989 Proxy Fallback Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A proxied call that takes the personal-budget fallback stops sending `codemie_project`, so its spend lands in the personal budget only.

**Architecture:** `_resolve_budget_attribution_stream` records the attribution project once on `request_info[SPEND_ATTRIBUTION_PROJECT]`, as it already does for `SPEND_ATTRIBUTION_CATEGORY`. The spend-logs header builder reads that key instead of `request_info[PROJECT]`. Ledger predicates stay as they are; only their docstrings change.

**Tech Stack:** Python, pytest + pytest-asyncio, SQLAlchemy over in-memory SQLite for the ledger sums.

**Spec:** `docs/superpowers/tasks/2026-11-09-epmcdme-16989/spec.md`

Paths are relative to `repos/codemie/`. Commit per task using the repository's existing convention.

## Global Constraints

- `request_info[PROJECT]` is never reassigned. Credential, runtime and monitoring lookups still read it.
- The header builder never reads `budget_fallback_from`.
- No new metadata key carries the fallback project. The ledger drops it; only ES keeps it.
- Unchanged: `predicate_for_holder`, `_personal_predicate`, their pinning tests (`tests/codemie/repository/test_spend_logs_repository.py:89-99`, `test_spend_logs_holder_access_boundary.py:425`), `_authorize_project_header`, who LiteLLM bills, `llm_factory.py`, `llm_proxy_monitoring_service.py`, codemie-enterprise, stored rows.

## Review Focus

- A bypass call whose `PROJECT` is the caller's email (personal project header) still sends no `codemie_project`. The username/email filter has to apply to the new key too (Task 1 test).
- A member funded in `platform` making a `cli` call is a fallback and must not name the project (Task 1 case `member_other_category`).
- A header built with no routing run (no category) still sends no identity. This is already pinned at `test_spend_logs_metadata_transport.py:278`; keep it green.

---

### Task 1: Routing records the attribution project; header reads it

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:124` (constant), `:949` (bypass), `:971-992` (runtime and fallback branches), `:412`, `:418-432` (helper)
- Test: `tests/enterprise/litellm/test_spend_logs_metadata_transport.py`

**Interfaces:**
- Produces: `SPEND_ATTRIBUTION_PROJECT = "spend_attribution_project"`; `_proxy_attribution_project(request_info: dict, user: User | None) -> str | None` (renamed from `_proxy_project_in_context`, which no test references).

Test-first: yes — a fallback call naming `project-a` is shown to carry `codemie_project` and to land in both the project and the personal ledger sum.

- [ ] **Step 1: Write the failing tests** in a new class `TestTheProxyNamesOnlyTheProjectItCharged`.
  - Case table, parametrized over `(case, probe_scopes, runtime, expected_project, expected_budget)`. Request: `client_type="codemie-cli"`, `PROJECT="project-a"`, user `MagicMock(id=USER_ID, username="alice", email="alice@example.com")`.
    - `funded_member`: `{CLI}`, runtime with headers `{"x-litellm-customer-id": f"codemie:project:project-a:category:cli:user:{USER_ID}"}`, mode `PROJECT_BUDGET_WITH_MEMBER_TRACKING` → `"project-a"`, `"project"`
    - `admin_non_member`: `set()`, `None` → `None`, `"personal"`
    - `member_other_category`: `{PLATFORM}`, `None` → `None`, `"personal"`
    - `allocation_dispatch_none`: `{CLI}`. Leave `_resolve_project_budget_runtime` unpatched; patch `ensure_project_member_runtime_ready`, `get_async_session`, `budget_resolution_service.resolve` (MagicMock result) and `budget_resolution_service.dispatch_runtime=AsyncMock(return_value=None)` → `None`, `"personal"`
  - Patch routing the way `tests/enterprise/litellm/test_proxy_router.py:1576-1595` does, plus `check_user_budget` for the global branch. Use the router's `codemie.enterprise.litellm.budget_categories.BudgetCategory` for the scopes.
  - `test_routing_records_the_project_it_charged`: after `_resolve_budget_attribution_stream`, `request_info[SPEND_ATTRIBUTION_PROJECT] == expected_project` and `request_info[PROJECT] == "project-a"`.
  - `test_a_fallback_header_keeps_identity_but_names_no_project` (fallback cases): the payload from `_apply_spend_logs_metadata_header` has no `codemie_project`, `codemie_user_id == USER_ID` and `codemie_category == "cli"`. For `funded_member`, `codemie_project == "project-a"`.
  - `test_the_row_lands_in_exactly_one_budget`: build a row with `_ledger_row(payload, end_user=request_info["litellm_customer_id"])`, load it into SQLite (adapter as in `test_spend_logs_holder_access_boundary.py:132-175`), then sum `ProjectHolder("project-a", BudgetCategory.CLI)` and `PersonalHolder(USER_ID, BudgetCategory.CLI, frozenset({"alice", "alice@example.com"}))` with `SpendLogsRepository().sum_for_holder(..., None)`. The set of holders with a non-zero sum must be `{expected_budget}`.
  - In `TestTheOwnCredentialsBypassStillSendsIdentity` (`:403-433`), add `PROJECT="project-a"` to a copy of the bypass test and assert `SPEND_ATTRIBUTION_PROJECT == "project-a"` and `codemie_project == "project-a"`. Add a second copy with `PROJECT="alice@example.com"` that asserts no `codemie_project`.

  `_ledger_row` is a new test helper that mirrors codemie-enterprise `spend_uploader/attribution.py:decide` for a complete identity triple:

```python
def _ledger_row(payload: dict, end_user: str) -> Row:
    project = payload.get("codemie_project")
    return Row(
        request_id="req-1", start_time=IN_PERIOD, end_time=IN_PERIOD, spend=Decimal("1.25"),
        status="success", prompt_tokens=0, completion_tokens=0, total_tokens=0,
        attr_source="explicit", attr_form="project_scoped" if project else "principal",
        attr_project=project, attr_category=payload["codemie_category"],
        attr_user_id=payload["codemie_user_id"], end_user=end_user,
    )
```

- [ ] **Step 2: Run the tests and confirm they fail.** Run `poetry run pytest tests/enterprise/litellm/test_spend_logs_metadata_transport.py -v`. Expected: an `ImportError` or `KeyError` on `SPEND_ATTRIBUTION_PROJECT`. Once that resolves, the fallback cases fail because `codemie_project` is present and both holders are non-zero.
- [ ] **Step 3: Implement.**
  - Add the constant next to `:124`.
  - Bypass `:949`: set `request_info.get(PROJECT) or None`.
  - Project-runtime branch: set `project_name` before the `return` at `:980`.
  - Fallback branch at `:990-992`: set `None`.
  - Rename the helper, make it read `SPEND_ATTRIBUTION_PROJECT`, and keep the username/email filter at `:427-431`. Rewrite its docstring to say it returns the project routing charged, not "the project this call was made inside".
  - Update the call at `:412`.
- [ ] **Step 4: Update the forged-header test** at `:230-234`. It sets `PROJECT` only, so it also needs `SPEND_ATTRIBUTION_PROJECT: "acme"` to keep asserting `codemie_project == "acme"` at `:241`.
- [ ] **Step 5: Run the tests and confirm they pass.** Run `poetry run pytest tests/enterprise/litellm/test_spend_logs_metadata_transport.py tests/enterprise/litellm/test_proxy_router.py tests/codemie/repository/test_spend_logs_repository.py tests/codemie/repository/test_spend_logs_holder_access_boundary.py -v`. Expected: all PASS.

### Task 2: State the `codemie_project` contract and reword `_personal_predicate`

**Files:** Modify `src/codemie/service/spend/spend_logs_metadata.py:15-25` and `src/codemie/repository/spend_logs_repository.py:331-340`.

Test-first: no — docstring-only change, and the existing pinning tests must stay unchanged and green.

- [ ] **Step 1: Edit the docstrings, then run the pinning tests.**
  - `spend_logs_metadata.py`: after the group table, add one sentence: `codemie_project` names the project whose project key or project-member customer was charged; the own-credentials bypass is the one known exception.
  - `_personal_predicate`: replace the "in this figure *and* in the project's attributed one" sentence. New fallback rows arrive `principal`. Rows stored before the fix (since 2026-11-01) are still `project_scoped` with the person's customer and stay in both figures, which is why the explicit half keys on the customer and not on the form. Do not say the double count is gone.
  - Run `poetry run pytest tests/codemie/repository/test_spend_logs_repository.py -v`. Expected: PASS.
