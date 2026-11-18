# Combined Integrations List (backend) — Spec

**Ticket**: EPMCDME-17085 (child of EPMCDME-17084) · **Story**: `story.md` (this dir) · **Size**: M (16/36)

## Intent

The unified Integrations page (codemie-ui, EPMCDME-17086) shows User and Project integrations in one table with a Scope filter. Two separately paged lists cannot give correct pages, totals or a newest-first order across both scopes, so the backend serves one paged list over both scopes. It keeps exactly today's rules about who sees what (D11) and leaves the per-scope lists in place.

## Contract (new)

`GET /v1/settings`, in `routers/settings.py` (the module for endpoints that span both scopes, `:15`). Authenticated like the per-scope lists.

- `scope`: optional enum query param, `all | user | project`. Omitted means `all`.
- `filters`: JSON string with the same keys and meaning as the per-scope lists: `search`, `project`, `created_by`, `type`, `is_global` (`filter_services.py:94-114`).
- `page`: int >= 0, default 0. `per_page`: int >= 1, default 10.
- Response: the same envelope as `GET /v1/settings/user`, `{data, pagination: {page, per_page, total, pages}}` (`settings_index_service.py:49-51`). Each row is a `Settings` row as the per-scope lists return it. Its scope is `setting_type` (`"user"` or `"project"`) and its Global flag is `is_global`, both as stored. Project rows may carry `is_global` as stored; the Global flag is meaningful on User rows only.

## Behaviour

- **Visibility (unchanged rules).** A User row is visible when it belongs to the requester and its alias does not start with `__internal__` (`settings_index_service.py:76-78`). A Project row is visible when its project is one the requester administers, or any project for `is_admin` users (`:66-68`). Project-budget LiteLLM rows are hidden from anyone but admins and maintainers (`:69-75`). Rows with no `setting_type` appear in no list (`:65`).
- **One query.** Visibility for the requested scope(s), then filters, then `date` descending, then `id`, one count, then offset and limit. Scope All never merges two paged results.
- **Same masking** of sensitive values as the per-scope lists (`:44-47`).

## Constraints

- **D11, one home.** Each scope's visibility condition is defined once, in `SettingsIndexService`, and both the per-scope lists and the combined list are built from it. The combined list restates no rule.
- **Combining must not loosen.** A row passes only through its own scope's condition: each condition carries its own `setting_type` match. Scope User and Scope Project run the same query as today's per-scope list. The service receives the requested scopes as an explicit set (All = both). Today the scope match sits once, outside the branches (`settings_index_service.py:65`), and User is the `else` catch-all (`:76`). The User branch becomes an explicit `elif`, and any other value raises. All is never passed as the existing `None`.
- `GET /v1/settings/user` and `GET /v1/settings/project` keep their routes, parameters, responses and order. Rows with equal dates gain the `id` tiebreaker.
- Read-only. No schema change, no migration.
- The OpenAPI and route-precedence baselines (`tests/codemie/rest_api/router_guards/`) are regenerated for the new route.

## Acceptance criteria

- AC-1: Scope All returns both scopes as one list, newest first and paged, with a total that counts both scopes. Paging through the whole result returns every row exactly once, rows with equal dates included.
- AC-2: Under Scope All, each scope's rows are exactly those its per-scope list returns to that person, with the same exclusions (internal settings, project-budget LiteLLM rows for non-admins and non-maintainers).
- AC-3: Scope User and Scope Project each return the same rows, order and total as the matching per-scope list.
- AC-4: A request with no `scope` behaves as Scope All.
- AC-5: `search`, `project`, `type`, `is_global` and `created_by` narrow the result the way they narrow the per-scope lists, with any scope.
- AC-6: Every row carries `setting_type` (`user` or `project`) and `is_global`.
- AC-7: A member who administers no project gets an empty result for Scope Project.
- AC-8: A project admin's Scope All result contains no project-budget LiteLLM rows of the projects they administer.
- AC-9: An unknown `scope` gets a 422 that names `all`, `user` and `project`.
- AC-10: The per-scope lists return the same rows, order, totals and masking as before this change.
- AC-11: Sensitive values are masked exactly as in the per-scope lists.
- AC-12: `page < 0` or `per_page < 1` on the new route gets a 422.
- AC-13: Tests cover each visibility clause on both the per-scope path and the combined path: own rows, the internal-alias exclusion, administered projects, the admin bypass, and the budget exclusion. No such tests exist today (analysis §4). The tests assert on rows returned by a real engine: in-memory SQLite as in `tests/codemie/repository/test_spend_logs_access_boundary.py`, with a JSONB-to-JSON variant, or an opt-in Postgres fixture. They include two further cases. (a) Under Scope All, another person's User row in a project the requester administers is absent, for a project admin and for a platform admin. (b) A Project row the requester created in a project they do not administer is absent, for a requester who is not a platform admin. Each test sets `is_admin` and `ENV` explicitly rather than relying on the ambient `config.ENV`.

## Non-goals

- Any UI change (codemie-ui EPMCDME-17086).
- Creating, updating or deleting integrations, and their permission checks.
- The Schedulers list.
- `GET /v1/settings/user/available` (the pickers list) stays as it is.
- The Created-by options endpoint (`GET /v1/settings/project/users`, `get_users`) stays as it is. The page offers Created by only at Scope Project (parent D7).
- Merging the budget rule's Python predicate (`settings.py:305-313`) with the list's SQL condition.
- Moving rows with a missing `date`.
- Validating `page` and `per_page` on the per-scope lists.
- Reading `scope` from inside `filters` JSON. It stays ignored, like any unknown key (`base_filter_service.py:79-81`).

## Decisions

- `GET /v1/settings` in the cross-scope router over a new path under `/settings/user` or `/settings/project` (where does the combined list live?).
- An enum `scope` query param over a key inside the `filters` JSON (how is scope sent, and how is an unknown value rejected?).
- `all` as an explicit value as well as the default over accepting only "omitted" for All (contract names All, User, Project).
- Rows keep `setting_type` and `is_global` as stored over adding a `scope` field or clearing `is_global` on Project rows (how does a row state its scope and Global flag?).
- The per-scope lists' envelope and `filters`/`page`/`per_page` params over a new typed response model (what shape does the UI parse?).
- One shared order for the per-scope and combined lists, `date` descending then `id`, over date-only, because rows with equal dates need a stable order to page (what orders rows with equal dates?) — user, answers AR-2
- Filters apply to every row in the result, `is_global` on Project rows included, over limiting Global to User rows (what does Global do under Scope All?).
- A 422 for `page < 0` or `per_page < 1` on the new route over copying the per-scope lists' unvalidated ints (how is bad paging input handled?).
- The budget rule's Python twin stays where it is over merging it in this story (how far does "one home" reach?).
- The service receives the requested scopes as an explicit set (All = both) over passing All as the existing `None` (how does All reach the service?) — user, answers AR-1

## Assumptions

- The admin project page keeps calling the per-scope lists. This is unverified in codemie-ui, but AC-10 holds on the backend either way.

## Left to the plan

- Whether the combined query ORs the per-scope conditions or UNION ALLs the per-scope selects, provided the count and the order span both scopes.
- Which real engine the AC-13 tests use (in-memory SQLite or an opt-in Postgres fixture). There is no real-DB fixture for settings today (analysis §4).
