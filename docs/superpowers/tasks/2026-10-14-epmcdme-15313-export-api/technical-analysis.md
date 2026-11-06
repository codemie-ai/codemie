# Technical Research

**Slice**: export-api · **Component**: `codemie` (backend) · **Feature**: iac-export

Findings below are existing-export-endpoint and backend-serialisation facts relevant to this story.

---

## Codebase Findings

**Existing partial export endpoints (`./codemie`)**

- `GET /v1/skills/{skill_id}/export` — emits only `name` and `description`. Not a re-creatable
  definition.
- `GET /v1/index/{index_id}/export` — a status report on the index, not a document sufficient to
  re-create the datasource.
- No shared serialiser exists between these two, or anywhere else in the backend, for "this entity as a
  portable document." This story introduces that serialiser for the first time.

**Backend REST surface, entity routers**

- FastAPI, routers under `src/codemie/rest_api/routers/*.py`, `/v1` prefix, registered in
  `main.py:920-970`. New export endpoints belong alongside each entity's existing router
  (`assistant.py`, `workflow.py`, `skill.py`, `category.py`, `settings.py`, `index.py` for
  datasources), following the same `APIRouter(prefix="/v1", ..., dependencies=[Depends(authenticate)])`
  pattern, typed Pydantic response models, and `project_access_check` / `Ability(user).can(...)`
  authorisation.
- Datasources remain the structurally hardest case for a uniform export shape: ~15 type-specific
  routes under `/v1/index/knowledge_base/*` and `/v1/application/{app_name}/index/*`, with only 5 types
  covered by existing IaC converters (`code`, `confluence`, `jira`, `google`, `file`).

**Findings added 2026-10-14 (from the plan/apply stories, the document envelope and the Node IaC `backup`)**

- **Read path already masks credentials.** Every GET/list of an integration masks sensitive values
  (`hide_sensitive_fields`); the exporter never holds a real secret. Update paths skip a credential equal
  to the mask (so a mask in a document would silently break a credential on create).
- **Updates are `PUT` full-replace.** Any field an exported document omits is reset or cleared on
  apply-by-id; svn, sharepoint and provider PUTs are destructive on partial bodies
  (`1-idempotent-write` Outcome). Re-submitting a complete document is already proven idempotent, which a
  complete export inherits.
- **Cross-entity references are stored as ids** (workflow `assistant_id`, assistant `skill_ids`,
  datasource `setting_id`); the document envelope has no id and resolves references by `(kind, name)`.
  The backend `WorkflowAssistant` has no `assistant_name` field, so the exporter must supply names.
  Assistant name, workflow name and integration alias are not unique per project (ambiguity risk).
- **Workflows**: the backend stores a flattened `yaml_config` (normalised on write); the document embeds
  a typed workflow definition, so export needs a stable inverse conversion with assistant ids rewritten
  to names.
- **Envelope contract**: documents parse with `extra="forbid"`; the response must carry exactly `meta` and
  the kind's create/update fields (no ids, timestamps, state flags). The envelope defines six kinds and no
  guardrail kind, so guardrails are dropped to a follow-up (decided 2026-10-14); skill companion files are
  not exported (decided 2026-10-14) and applying a skill document must leave them intact. The `env:`
  reference derivation is re-implemented independently across repos and needs shared golden vectors.
- **Datasource scope (decided 2026-10-14): every type apply can write back.** The router package is
  `rest_api/routers/index/` (`code_repositories.py`, `kb_atlassian.py`, `kb_azure_devops.py`,
  `kb_git_faq.py`, `kb_google.py`, `kb_sharepoint.py`, `kb_xwiki.py`, `providers.py`, plus `kb_files.py`
  which is unsupported). Types with create and update routes: code (git, svn), confluence, jira, xray,
  azure_devops_wiki, azure_devops_work_item, git_faq, google, sharepoint, xwiki, provider (provider update
  is keyed by id). Only code, confluence, jira and google have IaC converters today, so about seven
  exporters (xray, azure_devops_wiki, azure_devops_work_item, git_faq, sharepoint, xwiki, provider) have no
  precedent to build from.
- **Apply constraints on datasources**: `knowledge_base_file` is rejected on create and update;
  update routes are keyed by `(project_name, name)`; rename or project move is rejected.
- **Node IaC `backup`** (`backup.ts`, `backupTransformers.ts`) is a non-deterministic, client-side, bulk
  precedent that writes `credential_values` into its output; it shows which rewrites are needed (ids to
  names, skill and workflow content) but is not a reuse target.

---

## Risk Indicators

- **Secrets must never appear as literal values in an exported document.** Credentials are exported as
  resolvable references, never as values — this story is the first place that rule is actually
  implemented end-to-end for a full entity export, not just for update payloads.
- **Serialiser divergence risk.** A diff/apply engine built separately has its own internal document-
  shape handling for live-state comparison, built and shipped before this endpoint. This endpoint's
  serialiser is written second, against that engine's already-settled internals, and may not share an
  implementation with it from the start. A follow-up unification pass is anticipated, not ruled out by
  this story.
- **No prior art for byte-stable, deterministic serialisation** of a full entity anywhere in the
  backend — field ordering, formatting, and stability across repeated exports of an unchanged entity
  all need to be established here for the first time.

---

## Testing Landscape

- No cross-route document equivalence test exists anywhere in the codebase — this story is the
  producer side of "REST, CLI and SDK export produce the same document," verified separately in each
  of those layers.
- Backend test conventions (pytest, `tests/codemie/rest_api/routers/`, FastAPI `TestClient`) apply as
  the natural home for this endpoint's own round-trip and negative-path tests.
