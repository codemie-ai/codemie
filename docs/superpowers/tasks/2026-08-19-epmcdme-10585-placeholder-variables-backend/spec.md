# EPMCDME-10585 — Placeholder Variables Backend

**Revision:** 2026-09-14 (design grill after Yana_Asadchaya review on MR !4035 / UI !1711). Replaces the 2026-08-19 “expose `raw_yaml` + reject on every create/update” design. Feature was never shipped — **drop** obsolete branch behaviour; no backward compatibility.

## Problem Statement

Prebuilt workflow templates need configurable slots (`${input:var_name}`) so users can fill assistant ids, names, and similar values before creating a workflow from the catalog.

The first backend approach rejected any `${input:…}` substring on every workflow create/update. That can break existing (and unrelated) workflows that happen to contain the same characters in prompts or code. Exposing raw template YAML for the UI to substitute also put escaping and parse rules on the client.

## Solution

Placeholders exist only on **prebuilt workflow templates**. The backend:

1. Loads catalog templates that may contain tokens (internal original source kept for processing).
2. Returns **`required_variables`** on GET-by-slug (not the raw file).
3. Provides **`POST …/prebuilt/{slug}/materialize`**: accepts values, substitutes server-side, returns a **form-ready seed** (`yaml_config`, `description`, `start_hint`).
4. Does **not** scan or reject `${input:…}` on normal `POST`/`PUT /workflows`.

Only the materialize call validates that template variables are complete; regular Save uses ordinary workflow validation.

## User Stories

1. As a workflow author, I want prebuilt templates to declare input slots, so that one catalog entry can be reused with different assistants or names.
2. As a workflow author, I want GET-by-slug to tell me which variables are required, so that the create UI can show a form without parsing YAML myself.
3. As a workflow author, I want to submit those values to the backend and receive ready-to-edit workflow fields, so that YAML escaping and unwrap of `execution_config` are correct and consistent.
4. As a workflow author, I want materialize to fail clearly when I leave a required variable empty, so that I can fix the dialog without opening a broken form.
5. As a workflow author, I want materialize to fail clearly when substituted YAML is invalid, so that I can retry with different values.
6. As a workflow author creating from a template with no placeholders, I want to skip materialize and open the form from the GET response, so that I am not delayed by an empty dialog or extra call.
7. As a workflow author editing an existing workflow, I want Save to succeed even if prompts contain `${input:…}`-like text, so that unrelated content is not treated as template slots.
8. As a workflow author creating a workflow from scratch (not from a template), I want Save to ignore placeholder-looking strings, so that free-form YAML is not blocked.
9. As an API client, I want template identity keyed by **slug**, so that I use the same key as the catalog UI.
10. As an API client, I want materialize to require authentication like GET-by-slug, so that template rendering is not anonymous.
11. As a platform operator, I want unquoted `${input:…}` tokens in catalog files to still load into the template cache, so that authors can write natural YAML.
12. As a developer, I want leftover create/update “unresolved placeholder” API behaviour removed from this branch, so that the shipped contract matches “templates only.”
13. As a developer, I want `raw_yaml` never returned on REST responses, so that clients cannot reimplement substitution against an unofficial contract.
14. As a reviewer, I want materialize errors distinct from workflow persistence errors, so that the UI can keep the variable dialog open on failure.

## Implementation Decisions

### Architecture

- Placeholders are a **Workflow Template** concern only. Persisted **Workflows** treat `${input:…}` as plain text.
- Drop (not deprecate) from this unshipped branch: save-time `_reject_unresolved_placeholders` on validate and PUT; create/update HTTP tests for `unresolved_placeholder`; public `raw_yaml` on GET-by-slug; any FE-oriented “scan raw file” contract.
- Keep (or add) internal original template text for catalog load + materialize; expose **`required_variables`** instead of the file.
- FE and BE develop in parallel on feature branches; nothing in production depends on the old contract.

### Placeholder syntax and scope

- Pattern: `${input:([^}]+)}` (name = capture group).
- Tokens may appear **anywhere in the entire template file** (wrapper metadata and `execution_config`).
- `required_variables`: unique names in first-seen order over that full file text.

### Catalog load

- Preprocess before `yaml.safe_load` so unquoted tokens do not break parse (existing branch approach: quote/sentinel helpers).
- Store original file text internally on the template model for materialize and for deriving `required_variables`.
- List endpoint must not serialize internal raw source; GET-by-slug must not serialize it either.

### API contracts

**`GET /v1/workflows/prebuilt/{slug}`** (auth unchanged)

- Adds `required_variables: string[]`.
- Does **not** include `raw_yaml`.

**`POST /v1/workflows/prebuilt/{slug}/materialize`** (auth same as GET-by-slug)

- Body: `{ "variables": { "<name>": "<string>", ... } }`.
- Strict: every name in `required_variables` present and non-empty after trim; extra keys ignored.
- Success **200** seed only: `{ "yaml_config": string, "description"?: string, "start_hint"?: string | null }` (unwrap full template file: dump `execution_config` into `yaml_config`; take description/start_hint from wrapper when present).
- After success, seed/source must contain no remaining `${input:…}` tokens.
- Missing/blank required → **400** `{ "error_type": "missing_placeholder_variables", "message": "...", "errors": [{ "placeholder": "…" }] }`.
- Invalid YAML / unwrap failure after substitute → **400** `{ "error_type": "materialization_failed", "message": "..." }`.

**`POST` / `PUT /v1/workflows`**

- No placeholder-specific validation or error type for leftovers.

### Helpers / modules

- Placeholder helpers: extract names, preprocess for load, substitute (trim + YAML-safe escape), build materialized seed.
- Remove workflow-field “collect unresolved across name/description/…” used only for save rejection (or stop calling it from validate/PUT).
- Remove or stop using `UNRESOLVED_PLACEHOLDER` on the workflow save path; materialize uses `missing_placeholder_variables` / `materialization_failed` instead.

### Identity

- All prebuilt routes stay **slug**-keyed (catalog entries do not have stable DB ids).

## Testing Decisions

- Prefer the **HTTP router** as the primary seam: GET-by-slug shape, materialize success/errors/auth, and regression that create/update with `${input:…}` in body still succeed when otherwise valid.
- Unit-test placeholder helpers and template `from_yaml` only where HTTP cannot cover load/extract/substitute/seed.
- Delete or replace tests that asserted save-time `unresolved_placeholder` and public `raw_yaml`.
- Good tests assert status codes and response contracts, not private method call graphs.
- Prior art: existing `tests/codemie/rest_api/routers/test_workflow.py` prebuilt and create/update cases; `test_workflow_config.py` for `from_yaml`.

## Out of Scope

- UI to author or edit catalog templates.
- Auto-creating a workflow inside materialize (create remains a separate `POST /workflows`).
- Placeholder fields on persisted workflow models.
- Help/docs product updates.
- Rich variable metadata (labels, types) beyond `string[]` names.
- Template id-based URLs.

## Further Notes

- Reviewer ask: move logic to BE; return required variables; separate endpoint returning generated YAML for the create page — this design is that ask, with **slug** instead of id, and **seed** instead of dumping the full substituted file to the client.
- Old MR behaviour (global reject + `raw_yaml`) must be removed from the branch before merge, not left as optional.
