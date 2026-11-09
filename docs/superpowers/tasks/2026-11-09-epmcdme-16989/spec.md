# EPMCDME-16989: proxy fallback spend lands in the named project's budget

## Problem

A proxied call (`POST /v1/chat/completions`) that names a project via `X-CodeMie-Project`, made by a user
with no allocation in that project for the routed category, falls back to the personal budget:
`_flag_personal_budget_fallback` sets `budget_fallback_from` (`enterprise/litellm/proxy_router.py:750-771`),
no project runtime is resolved, the call goes out on the app key or the user's credentials
(`proxy_router.py:322-345`, `:1133-1144`) and LiteLLM bills the personal customer (`:1083-1085`).

The spend-logs metadata header still names the project: `_proxy_project_in_context`
(`proxy_router.py:418-432`) returns `request_info[PROJECT]` and never reads `budget_fallback_from`. The
uploader trusts the explicit `codemie_project`, stores the row `attr_form='project_scoped'`, and
`ProjectHolder` (`repository/spend_logs_repository.py:252`) adds it to the project's `current_spending`,
while `_personal_predicate` (`:330`) also counts it for the person. The project's ledger spend then exceeds
the spend on its LiteLLM key.

Reproduced locally: admin `user17`, not a member of `pr-700` and with no allocation there, sent a call with
`X-CodeMie-Project: pr-700`. LiteLLM row: `end_user=user17`, no key alias, `codemie_project=pr-700`;
stored as `project_scoped`/`pr-700`. pr-700's ledger rose 0.004378 -> 0.006716 while its key stayed at
0.004378. The assistant path produced a `principal` row, as it does today via
`llm_factory.py:_record_direct_budget_attribution`.

Re-run end to end with `.local/epmcdme-16989/verify.py` (workspace root, not committed) on current code.
Case B: an admin non-member with `X-CodeMie-Project` gets a `project_scoped` row, counted in both the
project and the personal figure. Case C: a member of a project with only a CLI budget makes a platform
call. The row is `project_scoped` with the member's personal `end_user`. It does not show in `/v1/projects`
`current_spending` or `/v1/analytics/project-member-spending`, because the project has no budget in that
category, but it is still filed under the project. Cases A (funded member -> project), D (no header ->
principal) and E (regular non-member -> 403) behave correctly.

The header is built after budget routing (routing at `proxy_router.py:2047`, header at `:1183`), so the
flag is already set when the project is read.

## Change

1. Routing records the attribution project once, on `request_info[SPEND_ATTRIBUTION_PROJECT]`. It is the
   twin of `SPEND_ATTRIBUTION_CATEGORY` (`proxy_router.py:124`, `:949`, `:966-971`) and is set on every
   branch of `_resolve_budget_attribution_stream`:
   - project-runtime branch (`:979-988`): the project name;
   - personal-fallback branch (`:990-992`): `None`. This covers an admin/maintainer non-member, a member
     with no allocation in the routed category, and a member with an allocation whose runtime dispatch
     returns no result (`:1130-1139`);
   - own-credentials bypass (`:938-950`): the project name, unchanged until its follow-up (Non-goals).

   The header builder (`:412`) reads that key in place of `request_info[PROJECT]` and keeps only the
   username/email filter (`:427-431`). It never reads `budget_fallback_from`. The helper is renamed, or
   its docstring (`:419`) is rewritten, so that it describes the attribution project and not "the project
   this call was made inside". A fallback row then keeps its user and category and is stored `principal`:
   counted in the personal budget, absent from every project figure.
2. `request_info[PROJECT]` itself is unchanged; credential, runtime and monitoring lookups still read it
   (`proxy_router.py:760-761`).
3. The `_personal_predicate` docstring (`spend_logs_repository.py:331-340`) is reworded for the rows that
   exist: new fallback rows arrive `principal`, but rows stored before the fix (since 2026-11-01) are
   still in both figures. That is why the explicit half keys on the customer rather than the form. The
   docstring must not say that the double count is gone. The predicate and its pinning tests
   (`test_spend_logs_repository.py:89-99`, `test_spend_logs_holder_access_boundary.py:425`) are unchanged.
4. The identity table in `service/spend/spend_logs_metadata.py:22` states the contract: `codemie_project`
   names the project whose project key or project-member customer was charged. The own-credentials
   bypass is the one known exception.

## Acceptance criteria

- AC1: A proxied call naming a project, with the personal-budget fallback taken, carries no
  `codemie_project` in `x-litellm-spend-logs-metadata`; `codemie_user_id` and `codemie_category` are
  still sent.
- AC2: That call's spend counts in the caller's personal budget only. The project's `current_spending` is
  not incremented.
- AC3: This applies to an admin/maintainer who is not a member, to a member with no allocation in the
  routed category, and to a member with an allocation whose runtime dispatch returns no result.
- AC4: For new traffic other than own-credentials calls (see Non-goals), the project's ledger spend
  matches the spend on its LiteLLM key.
- AC5: Regression: a member with an allocation in the routed category still sends `codemie_project`, and
  the call increments the project's `current_spending`.
- AC6: Tests cover the funded-member path and each fallback path in AC3, and show the row lands in exactly
  one budget: project for the funded member, personal for the fallback.
- AC7: Calls with no project header, or naming the caller's username/email, still send no
  `codemie_project` (existing behaviour).
- AC8: Own-credentials bypass calls naming a project still send `codemie_project` (unchanged).
- AC9: Manual check: `verify.py` Cases B and C produce `principal` rows counted in the personal figure
  only; Cases A, D and E behave as before.

## Non-goals

- Own-credentials bypass (`proxy_router.py:938-950`) keeps attributing to the named project, so AC4 does
  not cover own-key calls. It moves to a follow-up ticket (not yet filed). That ticket has to choose
  between an accurate project figure (drop the project: the project-member view then loses own-key calls,
  against the intent at `proxy_router.py:945-948` and `llm_factory.py:760-766`) and member visibility under
  a non-project slot. With Change item 1 the code change is one assignment on the bypass branch.
- Fallback rows already stored since 2026-11-01 stay as they are: no data fix, no hourly-rollup rebuild.
  A later correction would have to go through the codemie-enterprise uploader and a backfill re-run.
  Re-runs upsert from source rows that still carry `codemie_project`, so they would undo a ledger-only
  update.
- No read-time filter in `ProjectHolder` or any other spend predicate.
- No change to project-header authorization (`_authorize_project_header`), to who LiteLLM bills, or to
  `request_info[PROJECT]`.
- No change to the platform (assistant) path or to ES monitoring attribution.

## Decisions

- Fix the proxy's spend-logs header over a read-time filter in `ProjectHolder`, matching the platform path's existing fallback handling.
- Record the attribution project once at routing (`SPEND_ATTRIBUTION_PROJECT`) over a `budget_fallback_from` check in the header builder, following the category's existing convention.
- Follow the ticket over the EPMCDME-15204 docstring that accepts the double count, because the project key is not charged on fallback.
- Reword the `_personal_predicate` docstring to cover both new and stored rows over saying the double count is gone.
- Write down the `codemie_project` contract (the project charged, with the bypass as the one exception) over leaving the field's meaning implicit.
- Drop the fallback project from the ledger entirely, so only ES `budget_fallback_from` keeps it, over sending it under a non-identity metadata key.
- Leave the ES monitor deriving from `budget_fallback_from` over switching it to the new routing key.
- Treat a member with no allocation in the routed category the same as a non-member over fixing only true non-members — user
- Keep the own-credentials bypass out of scope over dropping its project too — user
- Leave already-stored fallback rows as they are over a one-off data fix or read-time filter — user

## Risks

- Project figures for the period since 2026-11-01 stay too high by the fallback calls already made.
- Project admins no longer see members' fallback calls in the project's per-member spend view
  (`member_spend_service.py:232` reads `attr_project`).
- Fallback calls also drop out of the project's dashboard filter and out of what the project admin can
  see, and they move into the caller's personal-project figure. This matches ES and is intended.
- Own-credentials calls that name a project keep counting in its figure until the follow-up lands.
- From this release on, the ledger does not record which project a fallback call named; only ES does.
