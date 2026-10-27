# Marketplace Management — production feature flags

Operator guide for the two flags that control the marketplace management
(moderation) feature in a real production environment.

Both ship **OFF**, so deploying this release changes nothing until an operator
turns them on. Management can be on without the gate, but **the gate does nothing
unless management is on too**: the approve, reject and force-publish routes are
only mounted with management, so a gate with no console would queue every publish
with nobody able to decide it. The gate is therefore armed only when both are on
(`is_approval_gate_armed()`), and a startup warning is logged when
`MARKETPLACE_APPROVAL_GATE_ENABLED=true` is set without management. Each flag has
a different blast radius.

| Flag | Default | Blast radius |
|---|---|---|
| `MARKETPLACE_MANAGEMENT_ENABLED` | `false` | Additive. Mounts the admin console API and its background jobs. No change to how anyone publishes. |
| `MARKETPLACE_APPROVAL_GATE_ENABLED` | `false` | **Breaking.** Changes what publishing an assistant does for every user. |

Defined in `src/codemie/configs/config.py` (lines 809 and 822). Read through
pydantic-settings, so in production they come from the container environment,
not from a file in the image.

---

## 1. `MARKETPLACE_MANAGEMENT_ENABLED`

The master kill switch for the moderation surface.

**Off (default).** The 16 `/v1/marketplace-management/*` routes are never
registered — they return 404 and do not appear in the OpenAPI schema. The audit
reaper, retention and purge schedulers do not register either. The feature is
dark: no routes, no background work, no database writes.

**On.** The routes mount, the schedulers start, and the admin UI's
`features:marketplaceManagement` flag flips to true. The UI flag is *derived*
from this env var at request time (`configs/customer_config.py:237`), not a
separate switch — there is nothing else to toggle to make the console appear,
and the two cannot drift.

This flag alone does **not** change publishing. Turning it on gives admins a
read-mostly console over existing data and starts the audit machinery. It is
the safe half of the rollout.

### Scope note — one endpoint is not behind the mount guard

`POST /admin/marketplace/reindex` lives on the always-mounted admin router and
carries its own per-request check instead. If you add a marketplace endpoint to
the admin router rather than the marketplace-management router, it needs that
same runtime guard — see the SYNC NOTE at `rest_api/main.py:1221`.

---

## 2. `MARKETPLACE_APPROVAL_GATE_ENABLED`

The publish-time approval gate. **This one changes user-visible behaviour and
needs a prerequisite in place before you enable it:** `MARKETPLACE_MANAGEMENT_ENABLED=true`
and at least one moderator account.

**Off (default).** Publishing self-approves: the assistant goes straight to
`marketplace_status = APPROVED` with `is_global = true`, on nothing more than
write access to the assistant. There is no approval step and no quality gate.

Two consequences of running with the gate off that matter for audit and
compliance:

- **No decision row is written.** `_publish_with_gate_disabled`
  (`routers/assistant.py:2113`) publishes directly and deliberately writes no
  `MarketplaceReviewDecision` row — the queue service's CAS relies on the gate
  being armed to own the state machine. The only trace is a structured
  `marketplace gate bypassed — no audit row written` warning naming the actor.
  If you need an audit trail of who published what, that trail does not exist
  until the gate is on; the log is it.
- **Editing an approved assistant does not trigger re-review.** The re-review
  path is itself gated (`routers/assistant.py:1089`), so with the gate off an
  owner can change a live, published assistant's content freely and it stays
  live with no further check.

**On.** Publishing runs the deterministic quality gates first:

- a CRITICAL finding is rejected with **422** and the publish does not happen;
- otherwise the assistant lands in `PENDING_REVIEW` and stays **private** until
  a moderator approves it through the console.

### Prerequisite: at least one moderator must exist

If the gate is on and nobody can approve, every publish accumulates in
`PENDING_REVIEW` with no way out, and assistant owners see their publishes
silently stop working. **Verify a moderator exists before enabling.**

A moderator is any user for whom `is_admin_or_maintainer` is true — that is,
`is_admin` **or** `is_maintainer` (`rest_api/security/permissions.py:20`). How
those resolve depends on your deployment:

- `ENABLE_USER_MANAGEMENT=true` (expected in production): both come from the
  database user record.
- `ENABLE_USER_MANAGEMENT=false`: `is_admin` is IDP-role based — the user id
  matches `ADMIN_USER_ID`, or the user carries `ADMIN_ROLE_NAME`.
- `ENV=local`: every user is an admin. A dev-only override — never relied on in
  production.

`is_maintainer = true` implies `is_admin = true` (`security/user.py:73`).

### Known defect — the startup warning does not work

`warn_marketplace_approval_gate_enabled()`
(`configs/marketplace_management.py:353`) is meant to warn at startup when the
gate is armed but no moderator exists. It calls
`user_repository.has_any_moderator(session)`, and **neither that method nor the
module it is imported from exists**. The call sits inside a broad
`except Exception` that defaults `has_moderator = False`, so:

- the warning fires on **every** startup while the gate is on, whether or not
  moderators exist, and
- it never actually checks anything.

Treat that log line as noise and confirm the moderator account by hand. Do not
use the absence of the warning as evidence of anything — it is never absent.

---

## Setting the flags in production

Env vars reach the pods through the Helm chart in `deploy-templates/`. The
deployment renders `extraEnv` + `customEnv` into the container's `env:` list
(`deploy-templates/templates/deployment.yaml:65-69`). Add the flags to
`extraEnv` in `deploy-templates/values.yaml`, alongside the existing
`TRIGGER_ENGINE_ENABLED` / `SUBWORKFLOW_POOL_ENABLED` entries:

```yaml
extraEnv:
  - name: MARKETPLACE_MANAGEMENT_ENABLED
    value: "true"
  - name: MARKETPLACE_APPROVAL_GATE_ENABLED
    value: "false"   # stage separately — see rollout below
```

Quote the values. These are strings in Kubernetes; pydantic coerces
`"true"`/`"false"`.

**After editing that section of `values.yaml`, run `helm-docs` from the project
root** to regenerate `deploy-templates/README.md`. The file says so inline, and
a chart change without it will show up as a docs diff later.

**Both flags are read at startup**, for the router mount and the scheduler
registration. Changing either requires a **pod restart / rollout** — there is no
runtime reload, and no admin API that flips them.

### Narrowing per entity kind (optional)

`MarketplaceManagementConfig` carries a per-kind `enabled` flag (for
`assistants` and `workflows`, default `true`), and
`is_marketplace_management_enabled(kind)` ANDs it with the env var. It can only
narrow, never widen: with the env var off, nothing turns the feature on. Useful
to enable the console for `assistants` while leaving `workflows` dark. Changed
live via `PUT /v1/marketplace-management/entities/{kind}/config`, no restart.

---

## Recommended rollout order

Stage the two flags; do not flip both in one release.

**Step 1 — console only.** `MARKETPLACE_MANAGEMENT_ENABLED=true`,
`MARKETPLACE_APPROVAL_GATE_ENABLED=false`. Publishing keeps working exactly as
before. Verify:

- `GET /v1/marketplace-management/entities/assistants/overview` returns 200 for
  an admin, 403 for a regular user;
- the Marketplace Management console appears in the admin UI;
- an audit run completes and the Overview and Cleanup pages populate;
- the reaper logs its interval and does not fail live runs.

Let an audit cycle or two run here. This is also where you confirm the
thresholds produce sane buckets for your real catalogue before any of it gates
a publish.

**Step 2 — confirm moderators.** Verify at least one `is_admin` or
`is_maintainer` user exists and can reach the pending queue. Do this by hand;
the startup warning is broken (above).

**Step 3 — arm the gate.** `MARKETPLACE_APPROVAL_GATE_ENABLED=true`. Announce
it: from this point publishing requires approval, and assistant owners will see
`PENDING_REVIEW` instead of an immediately-live assistant. Verify:

- publishing a compliant assistant lands it in `PENDING_REVIEW`, not live;
- an assistant with a CRITICAL finding is refused with 422 naming the rules;
- a moderator can approve it and it goes live;
- the pending queue is being watched — a queue nobody reads is the failure mode
  this gate introduces.

### Rollback

Set the flag back to `false` and roll out. Both are safe to switch off:

- Gate off → publishing self-approves again immediately. Assistants already
  sitting in `PENDING_REVIEW` **stay there** — they are not auto-approved and
  not auto-published. Clear the queue before or after, deliberately.
- Management off → the console and its API disappear; audit data is retained in
  the database, untouched.

Neither rollback deletes anything.

---

## Related flags, deliberately out of scope here

These are separate switches that interact with the above. Listed so they are
not mistaken for part of this feature's on/off story:

| Flag | Default | Note |
|---|---|---|
| `MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED` | **`true`** | Advisory LLM quality check on publish. Already on in production, independent of both flags above. |
| `MARKETPLACE_AUDIT_RETENTION_DAYS` | `0` | `0` keeps all history. Any positive value arms a **destructive, irreversible** daily purge at 02:30 UTC. The newest `SUCCEEDED` run per kind is never deleted. |
| `MARKETPLACE_AUDIT_HEARTBEAT_TIMEOUT_MINUTES` | `60` | Reaper threshold. `0` disables the reaper. Must stay aligned with `audit_service.AUDIT_HEARTBEAT_TIMEOUT`; a shorter value reaps audits that are still legitimately running. |
| `MARKETPLACE_AUDIT_REAPER_INTERVAL_MINUTES` | `5` | How often each pod checks for abandoned runs. Only the advisory-lock holder acts. |

## Known consequence for the UI regression harness

Two marketplace Playwright tests assert that publishing lands an assistant live
immediately. That is true with the gate off (the default), so they pass as
shipped. **In any environment where you set
`MARKETPLACE_APPROVAL_GATE_ENABLED=true`, those two tests will fail** — they are
asserting precisely the behaviour the gate exists to prevent. Updating them to
publish → approve → assert is owned by whoever enables the gate. Tracked as
comment #1 in `docs/marketplace-audit-20260820/mr-4070-review-comment-tracking.md`.
