# Spec: Persisted LLM Model Status + Admin API (`status-persistence-api`)

**Parent**: EPMCDME-15077 (platform model management) · **BE sub-task**: EPMCDME-15080
**Slice**: backend-only persistence + service-merge + admin API. Depends on nothing; two FE slices
(`admin-ui`, `catalog-status-surfacing`) depend on this one's contract, below.

## Problem

`LLMService` has no CodeMie-owned notion of model status. `LLMModel.enabled` is sourced live from
the provider's `model_info.enabled` on every `GET /v1/admin/llm/reload` and is overwritten on each
reload. There is no way for an admin to persistently Enable/Disable/Deprecate a model independent of
what the provider reports, and no way for that decision to survive a reload.

## Design

### Persistence

New table `llm_model_status` (SQLModel, Alembic migration), one row per **`base_name`** (see
Contract §2 for why):

- `id` (uuid, pk)
- `base_name` (str, unique, indexed) — the identity key
- `status` (str enum: `enabled` / `disabled` / `deprecated`, default `enabled`)
- `updated_by_id`, `updated_by_name` (admin who last changed it)
- `created_at`, `updated_at`

A repository (`LLMModelStatusRepository`, following `.ai-run/guides/data/repository-patterns.md`)
provides `get_all()`, `get_by_base_name()`, `upsert(base_name, status, admin)`.

**No row for a `base_name` ⇒ default status `enabled`.** The feature is purely additive: nothing
changes for a model until an admin acts on it. A row for a `base_name` no longer present in the live
provider catalog is left in place, untouched, on the next reload — it becomes inert (excluded from
any response, since responses are built by iterating the live catalog) until the model reappears.

### Merge into the catalog

`LLMModel` gains a new field `status: ModelStatus = ModelStatus.ENABLED`, populated by a merge step
run inside `initialize_default_litellm_models()` (and therefore automatically on `GET
/v1/admin/llm/reload`): for each live-catalog model, look up its persisted row by `base_name` and set
`model.status` from it (default `enabled` when absent). `LLMModel.enabled` is left untouched —
it continues to mean exactly what it means today (the provider's live signal).

Effective availability for the four `get_allowed_*` methods becomes:

```
available = model.enabled and model.status != ModelStatus.DISABLED
```

i.e. admin status can only **narrow** provider availability, never widen it — an admin cannot force a
provider-disabled model back into the allowed lists. This changes the filter condition in
`get_allowed_chat_models`, `get_allowed_embedding_models`, `get_allowed_image_generation_models`, and
`get_allowed_router_options` from `model.enabled` to the `available` expression above.

**Three-state read behavior (all four methods, `include_all=False`):**

| status | listed? | conflict with `enabled=False`? |
|---|---|---|
| `enabled` | yes | excluded if provider `enabled=False` |
| `deprecated` | **yes**, carrying `status: "deprecated"` | excluded if provider `enabled=False` |
| `disabled` | **no** | (moot — already excluded) |

`include_all=True` bypasses both the provider `forbidden_for_web` filter and the disabled-status
narrowing, returning every model the provider reports regardless of admin status (matching its
existing "show me everything" semantics) — the admin-facing status endpoints (below) always behave as
`include_all=True` internally, since an admin managing status needs to see disabled/deprecated models
too, not just what a regular user would be shown.

### Selection enforcement for `deprecated`

A `deprecated` model stays in the read lists (so it can be shown, badged, in existing dropdowns) but
must be rejected for **new** selection. This is enforced on the write path, not by omission from the
read path: `LLMService` exposes a new guard, `assert_model_selectable(base_name)`, raising a typed
`ModelNotSelectableError` when `status == deprecated` (and, for defense-in-depth, also when the
merged model is unavailable per the table above). Call sites — wherever a model name is accepted and
persisted for new use — include assistant create/update (`assistant_service.py`,
`rest_api/routers/assistant.py`), chat/session configuration (`conversation_service.py` and
adjacent chat-config paths), and workflow node configuration. **Enumerating every call site and wiring
the guard into each is plan-stage work**; this spec commits to the guard's existence, its exception
type, and the rule it enforces, since those are what downstream write paths need to depend on.
Existing entities that already reference a deprecated model are unaffected — the guard only fires on
new writes, never on read or on entities unchanged since before the model was deprecated. The
FE-visible "greyed out" treatment is UX over this backend rule per the project's existing
"backend authorization is defense-in-depth; UI authorization is UX-focused" precedent.

`disabled` models are absent from normal `get_allowed_*` listings and cannot be selected for new
work. Existing entities retain their stored model reference, but execution replaces a disabled
selection with the project's selectable default for project assets or the effective global default
for global assets. If the configured default is unavailable, resolution chooses a selectable model
within the applicable project constraints; if none exists, execution fails explicitly. Deprecated
models remain usable for ongoing work. Status checks apply independently of `projectModelOverride`.

Retirement (`llm_retirement_service.py`) is unrelated and untouched by this slice: it is a one-time,
admin-triggered bulk rewrite of existing DB references from one model name to another. Deprecated
status is a live, reversible flag on the catalog itself with no effect on existing rows. An admin may
deprecate a model now and choose to retire it (rewrite references) later, as a separate action.

## Cross-Team API Contract

*(Self-contained — this section is copied verbatim into `contract.md` in both frontend task dirs:
`admin-ui` and `catalog-status-surfacing`.)*

**1. Wire values**: `"enabled"` | `"disabled"` | `"deprecated"` (lowercase, matches every other
string enum in this codebase).

**2. Identity key**: `base_name`. Always address a model by `base_name` when reading or setting
status — not `deployment_name` (provider-specific, may vary per deployment alias of the same model).

**3. Admin endpoints** (in `admin.py`, `/v1/admin/...` prefix, `Depends(admin_access_only)`, logging
`admin.id`/`admin.name`, mirroring `retire`/`retire/bulk`):
- `GET /v1/admin/llm/status` → `BaseResponseWithData`, `data: [{base_name, status, updated_by_name, updated_at}]` for every model currently in the catalog (includes rows with no persisted override, defaulted to `"enabled"`).
- `PUT /v1/admin/llm/{base_name}/status` — body `{"status": "enabled"|"disabled"|"deprecated"}` → `BaseResponseWithData`, `data: {base_name, status}`.
- `POST /v1/admin/llm/status/bulk` — body `{"updates": [{"base_name": str, "status": str}, ...]}`, each processed independently → `BaseResponseWithData`, `data: {total, succeeded, failed, results: [...]}` (same per-item shape as `retire/bulk`).

**4. Public payload**: `GET /v1/llm_models` (and the other `llm_models.py` endpoints serializing
`LLMModel`) gains `status: "enabled"|"disabled"|"deprecated"` on every returned model, alongside the
existing `enabled` boolean. *Downstream note: existing UI tests asserting on payload shape
(`LLMSelector.test.tsx`, `ChatConfigLlmSelector.test.tsx`, `ChatPromptLlmSelector.test.tsx`) will see a
new field — informational for the sibling slices, not this one's work.*

**5. `Deprecated` runtime semantics**:
- Still returned by `get_allowed_chat_models` / `get_allowed_embedding_models` /
  `get_allowed_router_options` / `get_allowed_image_generation_models`, carrying
  `status: "deprecated"` — **not filtered out**.
- Cannot be selected for new work — enforced backend-side on the write path (assistant/chat/workflow
  config), independent of whatever the UI does.
- Existing entities already referencing it keep working unchanged.
- Net effect: visible + badged + blocked-for-new-use. Distinct from `Disabled` (not listed at all)
  and from retirement (a separate, one-time bulk reference rewrite — see Design section above).

## Non-goals

- The admin management UI page (`admin-ui` slice, `codemie-ui`).
- Surfacing/rendering status on `/help/models` or any other consumer UI (`catalog-status-surfacing`
  slice, `codemie-ui`) — this slice only makes the field available on the wire.
- Notification points and rules (out of scope for the whole parent story).
- Any change to `llm_retirement_service.py` or the `retire`/`retire/bulk` endpoints.
- Enumerating and wiring every write-path call site for the deprecated-selection guard (plan-stage
  discovery; this slice delivers the guard and its contract, not an exhaustive call-site audit).
- Reusing or renaming `LLMModel.enabled` — it remains the provider-sourced signal, untouched.

## Testing notes

New coverage needed: repository upsert/get, the merge step in `initialize_default_litellm_models`
(persisted status surviving a simulated reload; orphaned row on model disappearance; default
`enabled` for no-row case), the `available` filter change in all four `get_allowed_*` methods
(including the `enabled=False` + `deprecated` conflict case), the three new/changed admin endpoints,
and `assert_model_selectable` raising for `deprecated`.
