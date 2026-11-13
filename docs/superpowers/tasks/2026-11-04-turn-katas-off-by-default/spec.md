# Spec: Turn Katas Off by Default (EPMCDME-16850)

## Problem

Katas have no on/off setting. Both kata routers are always registered and check authentication only (`main.py:1485-1486`, `ai_kata.py:71-75`, `user_kata_progress.py:34-38`). A default installation therefore serves katas the administrator never chose to offer. Other customer-config features already refuse calls with 404 when off (`cli_analytics.py:154`, `routing.py:39`).

## Behavior

1. Both shipped customer configs (`config/customer/customer-config.yaml`, `standalone/external-config/customer-config.yaml`) get a `features:katas` component with `enabled: false`, `name`, `description`, `availableForExternal: false`, in the shape of `features:cliAnalytics` (yaml line 119). `FEATURE_KATAS=true` then enables it through the existing override (`customer_config.py:131-147`).
2. Every route of `ai_kata.py` (content, tags, roles, CRUD, publish/unpublish/archive, reactions) and `user_kata_progress.py` (leaderboard, progress/my, start, complete, progress) is refused while the feature is off: 404, message "Katas feature is not available", with details and help fields as in `cli_analytics.py:154`. No kata data is read or changed.
3. The check is `customer_config.is_feature_enabled("katas")`. A missing component reads as off with no error (`customer_config.py:393-413`), so a customer config without a katas entry needs no change in code.
4. The feature list served to the frontend comes from `customer_config.components`; the new entry appears there with enabled false. No extra code.
5. Startup seeding (`main.py:544-553`, `import_preconfigured_katas`) is untouched and runs while katas are off.
6. When on (env override or a customer-config entry), routes behave as today; the gate touches no tables, so earlier progress is intact.
7. Release note (text delivered in the MR description and a short `standalone/README.md` paragraph next to the cliAnalytics one at ~line 262; the release-notes source is not in this repo): katas turn off on upgrade; enable with `FEATURE_KATAS=true` on the shipped config, or a `features:katas` entry with `enabled: true` in a customer config.

## Design

One guard function, `_ensure_katas_enabled`, defined once in `ai_kata.py` and used by both routers via the router-level `dependencies=[Depends(authenticate), Depends(_ensure_katas_enabled)]`. Authentication runs first, then the gate. The guard raises `ExtendedHTTPException` as described above. Router-level dependency is used instead of 17 handler-first-line calls because every route must be covered, a forgotten route would be a leak, and tests that call handlers directly are not affected.

## Acceptance criteria

- AC1 Both shipped YAMLs contain `features:katas` with `enabled: false`; with `FEATURE_KATAS=true` `is_feature_enabled("katas")` is true.
- AC2 The components served to the frontend list katas with enabled false by default.
- AC3 With the default config, each route in both kata routers returns 404 "Katas feature is not available" and does not call the service layer.
- AC4 Startup with default config still runs `import_preconfigured_katas`.
- AC5 With the feature on, kata routes work as before; existing router tests still pass.
- AC6 A customer config with no katas component: `is_feature_enabled("katas")` is false, no exception, routes return 404.
- AC7 Release note text and README paragraph state the upgrade behavior and both ways to enable.
- Tests: a gate test covering every route of both routers (off returns 404, on passes through), config tests for the shipped entry and env override, and an update of the enumerated feature list in `tests/codemie/configs/test_customer_config.py:852`.

## Non-goals

- Frontend changes.
- Removing or changing the seed, kata data or content.
- Leaderboard scoring and usage attribution (`service/leaderboard`, `usage_attribution/settings.py`).
- An admin-settings switch or a `SettingDeclaration` for katas.
- Not registering/unregistering routers at startup; gating is per request.
- Standardizing the "feature not available" response across features.

## Decisions

- Refuse with 404 "feature not available" over 403 as cost centers and teams use — user (D1)
- Seed runs while katas are off over skipping it when off — user (D2)
- No admin-settings switch over adding a `SettingDeclaration` — user (D3)
- Gate all routes of both routers, including tags/roles/CRUD/reactions, over only the endpoints the ticket names, so no kata surface remains served while off
- Router-level dependency guard over per-handler first-line calls (the cliAnalytics/routing style), for full coverage and unaffected direct-handler tests
- Single guard defined in `ai_kata.py` and reused by `user_kata_progress.py` over a copy per router or a new shared module
- Katas-specific message "Katas feature is not available" over a generic shared constant, matching the per-feature wording precedent
- Release note delivered as MR text plus a README paragraph over a new file, since the release-notes source is outside the repo

## Assumptions

- The frontend (outside this repo) tolerates 404 on kata calls when katas is off; unverified.
- Leaderboard code mentioning katas (not read) does not call the kata routes.
