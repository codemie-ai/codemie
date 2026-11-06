# Public export REST endpoint — Story

**Date**: 2026-09-16 · **Updated**: 2026-10-14 · **Status**: Draft
**Ticket**: EPMCDME-15313
**Source**: MAIA_Stories_Sources_EN.md §2 (REQ-1.1.2, REQ-1.1.4, REQ-1.1.5; Q-006, Q-007)
**Depends on**: the Document envelope for the Python IaC tool ([[2-document-envelope]]) for the target
document shape — not the Python SDK, which never owns a document format (see that story's own rescoping).
Constrained by the plan/apply stories ([[1-idempotent-write]], [[2-sdk-plan-apply]]): an exported document
is later applied back with `apply --id`, so what it carries and omits must be safe to send back.

**Decisions taken**
- documents carry credential fields as resolvable references rather than values — the export surface
  never emits a credential value.
- **the credential-reference naming convention matches [[2-document-envelope]]'s `from_entity()`
  exactly** (updated 2026-09-25, resolving what was an open implementation call below): a deterministic
  `env:{ALIAS_OR_NAME}_{KEY}` placeholder derived only from the entity's own identifying fields, never
  from the credential's actual value. This backend serialiser can't import that Python IaC tool's code
  (cross-repo, and the wrong dependency direction regardless), so it re-implements the same convention
  independently — but it is the *same* convention, not a second one, so an entity exported from the
  platform and the "same" entity described in code by a user produce a reference that looks identical,
  not two differently-styled placeholders for what is conceptually the same gap.
- scope of "any entity" is parity with what the UI lets a user configure, not a hand-picked subset. An
  entity type is in scope for export if it is configurable through the UI — including categories as their
  own entity, and MCP configuration as part of an assistant's own definition rather than a separate
  entity. The bar moves with the UI's own surface, not with whatever the Python SDK happens to model
  today. **Settled 2026-10-14: guardrails are dropped from this story** (follow-up story) — the document
  envelope ([[2-document-envelope]]) defines no guardrail kind and the [[1-idempotent-write]] audit never
  covered guardrails, so an exported guardrail could be neither parsed nor safely applied. In scope:
  assistant, workflow, datasource, integration, skill, category.
- delivery order: a diff/apply engine (a separate story, already built and shipped) has its own internal
  serialisation of platform state for comparison purposes; this story's canonical export serialiser is
  written after that engine's internals are already settled, rather than the two sharing one
  implementation from the start. Accepted as a known risk — the two serialisers may need a follow-up
  unification pass rather than converging by construction — outweighed by the business priority of
  shipping create-and-apply before platform export. *(To be re-checked against the shipped engine: the
  plan/apply story as written names no live-entity-to-document serialiser.)*

**Decisions added 2026-10-14 (round-trip safety — an exported document must be safe to apply back)**
- **The response is exactly `version`, `meta` plus a spec made of the kind's own create/update request fields** *(`version: "v1"` added 2026-10-15; the envelope requires it)*. No
  platform id, timestamp, state flag (`error`, `completed`, counters, `current_state`), `created_by`,
  abilities, likes or mask string ever appears. The envelope parses with `extra="forbid"` and documents
  never carry a platform id, so any such field would make the response unparseable or leak identity.
- **Export is complete relative to the update (PUT) body.** Updates are full-replace, so a field the
  document omits is reset or cleared on apply-by-id. For every kind, each field the update endpoint
  accepts is either exported or listed in this story as deliberately non-exportable — never silently
  dropped. This matters most on the three PUT routes where a partial body is destructive (svn,
  sharepoint, provider; see [[1-idempotent-write]] Outcome).
- **Cross-entity references are exported as names, never ids.** Workflow `assistant_id` becomes an
  assistant name, assistant `skill_ids` become skill names, datasource `setting_id` becomes an integration
  alias. `meta.name` per kind is the key other documents reference: the assistant's name (the slug stays
  a data field, `spec.slug`), the integration alias, and the entity's own name for the remaining kinds
  *(amended 2026-10-15: the first implementation used the assistant slug, which broke bundle linking in
  the real engine)*. A name that is not unique and would make a reference
  ambiguous fails the export naming the clash — never an ambiguous reference. (Assistant name, workflow
  name and integration alias are not unique per project today.)
- **The credential reference is specified byte for byte** (it is re-implemented independently of the
  envelope, so it must be pinned): `env:{ALIAS_OR_NAME}_{KEY}`; the identifying part and the key are
  upper-cased with every run of non-ASCII-alphanumeric characters replaced by a single `_`; leading and
  trailing `_` are kept, and an identifier or key with no ASCII alphanumeric fails the export (422)
  instead of using a fallback *(amended 2026-10-15: identical to the envelope's
  `CredentialRef.from_entity`)*. The same golden vectors are shared with [[2-document-envelope]].
- **The read path already masks credentials.** Every GET/list of an integration masks sensitive values
  (`hide_sensitive_fields`), so the exporter never has a real secret to redact. It builds references from
  keys and identifying fields only and never reads a credential value. A mask string is never emitted:
  on create a mask is not stored and on update it means "leave untouched", so a mask in a document would
  silently break the credential.
- **Workflows are exported as a structured definition, not as the stored text.** The backend stores a
  flattened `yaml_config`; the document embeds a typed workflow definition. The exporter converts one into
  the other, replaces `assistant_id` with the assistant's name, and the conversion is stable: re-rendering
  an exported workflow reproduces the stored (normalised) text.
- **Datasources export every type that can be applied back (settled 2026-10-14: full parity with apply).**
  The candidate set is each datasource type with a create and update route today: code repositories
  (git, svn), confluence, jira, xray, azure_devops_wiki, azure_devops_work_item, git_faq, google,
  sharepoint, xwiki and provider. It is confirmed against the types the Python apply engine accepts.
  `knowledge_base_file` (rejected by apply on create and update) and any type apply does not accept fail
  with an error naming the type — never a partial document. Update routes are keyed by
  `(project_name, name)` (the provider route by id), so the exported name is the entity's live name.
- **Skill companion files are not exported (settled 2026-10-14).** A skill document carries content only,
  and the story states this limitation. Because skill updates are full-replace, the backend must show
  that applying an exported skill leaves its companion files intact (AC and test below); if it does not,
  that is raised before this story ships. A follow-up adds companion files if the envelope grows to
  carry them.
- **Large content is inline.** System prompt, workflow definition and skill content travel inside the one
  response; there are no side files, because a document is one file ([[2-document-envelope]]) and the CLI
  writes a single `--output` file.
- **Serialisation is deterministic**: fixed key order (the kind's field-declaration order, by alias), fixed
  list order, no nulls, no timestamps or run-specific values.

**Decisions amended 2026-10-15 (as shipped; these supersede earlier bullets where they differ)**

Implementation found that the document envelope ([[2-document-envelope]], implemented in the Python IaC
package) is `extra="forbid"` on every spec and uses the SDK request field names. The first
implementation did not parse (1 of 19 sample documents did). The exporters were re-based on the
envelope's specs; all 19 sample documents now parse, guarded by a committed JSON Schema generated from
the envelope. Consequences for this story:

- **The response is `version: "v1"`, `meta` and `spec`.** The envelope requires the version.
- **`project_name` is exported on datasources and integrations** (the envelope requires it; apply
  rejects a document whose project is not the target). This reverses "documents are target-agnostic".
- **`openapi_spec` is exported as an `env:` reference like every other integration credential.** The
  envelope has no inline slot, so the documented inline exception no longer exists.
- **The `env:` derivation is exactly the envelope's `CredentialRef.from_entity`**: each of identifier
  and key is upper-cased with every run of non-ASCII-alphanumerics replaced by `_`; leading and trailing
  `_` are **kept** (not stripped); an identifier or key with no ASCII alphanumeric fails the export (422)
  instead of using a fallback. This replaces the "stripped / fixed fallback" wording above. The golden
  vectors are shared with the envelope. A collision between two distinct credentials mapping to one
  reference within a document fails the export.
- **Sensitive prompt variables** are exported with an empty default (no flag, no reference): the envelope
  never resolves references inside prompt variables.
- **References are checked for access and uniqueness.** A referenced target the caller cannot read fails
  with 403 naming the reference id (never the name). A target whose name is shared with any other entity
  of the same kind in scope (the exported entity's project; categories are global) fails, naming the
  clash — whether or not that other entity is referenced. Stored data the envelope cannot represent
  fails with a 422 naming the entity kind and id, without echoing values.
- **Deliberately non-exportable (AC8)** — absent from the document because the envelope has no field for
  them, or because they are platform ids the envelope does not resolve:
  - Server-managed (all kinds): ids, timestamps, state flags, counters, `created_by`, abilities, likes,
    mask strings.
  - **Skill:** companion files, `toolkits` and their integration settings, `mcp_servers` and the MCP
    catalog id. The document cannot state that companion files are excluded.
  - **Assistant:** `skills`/`skill_ids`, `plan_prompt`, `agent_mode`, image-generation settings,
    `smart_tool_selection_enabled`, `file_attachment_enabled`, `interactive_enabled`, `hedging_config`,
    `custom_metadata`, `guardrail_assignments`, `bedrock`, `agentcore`, `agent_card`, toolkit/tool
    `auto_credentials_lookup`, per-tool credential maps, prompt-variable `is_sensitive`, MCP
    `mcp_config_id`, MCP config `audience` and `allow_issuer_prefix_match`.
  - **Workflow:** `start_hint`, `supervisor_prompt`, `meta_config`, `project`, engine switches in the
    yaml, `tool_access`, `keep_history`/`override_task`, assistant `skill_ids`/`datasource_ids`, MCP
    `mcp_config_id`, MCP `settings` snapshots and `mcp_connect_auth_token` (only the integration alias
    survives), `categories`. A workflow the envelope cannot represent fails naming the cause: a state
    that runs a sub-workflow, an enabled MCP server that is only a catalog link, an assistant without a
    prompt, name or catalog assistant.
  - **Datasource:** `guardrail_assignments`, the SharePoint transient access token, `new_project_name`,
    Azure DevOps `organization`/`project` (not stored on the index), and `cron_expression`/`timezone`
    for provider datasources only (the provider request has no such fields). A datasource missing a field the
    envelope requires (xray `jql`, svn `link`, SharePoint `site_url`) fails the export naming it.
  - **Assistant** also fails when it has no `llm_model_type`; an integration without `project_name`
    fails.
- **Known limited round trips (outside AC9 as narrowed).** Converting the document to the update
  request changes the stored entity for fields the document cannot carry: assistant and workflow inline
  MCP secrets (exported as `env:` references; the backend update has no mask path for them, though the
  real engine preserves them when the environment variable holds the same value), toolkit
  settings-snapshot credentials and the MCP catalog link (the real engine keeps `supervisor_prompt` and
  `meta_config`). Four strict `xfail` tests pin the backend side. Findings from
  the real-engine run, including requests for the envelope and engine owners (skill categories, MCP
  fields, SharePoint OAuth token), are in a comment on EPMCDME-15312.

---

## Context

- No existing export endpoint yields a re-creatable document. `GET /v1/skills/{skill_id}/export` emits
  only name and description; `GET /v1/index/{index_id}/export` is a status report, not a definition.
  There is no shared serialiser anywhere in the backend for "this entity as a document."
- This story builds the canonical serialiser and the public `/v1/export`-style REST surface that
  produces a document sufficient to re-create the entity, with credential fields as references rather
  than values.
- This work lives entirely in `codemie` (backend). Other independent work (an SDK `export` method and a
  CLI `export` command) depends on this story producing a stable, documented response shape they match
  byte-for-byte.
- Updates are `PUT` full-replace, and [[1-idempotent-write]] established that re-submitting a *complete*
  unchanged document leaves the stored entity unchanged on every route in scope. An exported document
  inherits that guarantee only if it is complete — which is why completeness is a requirement here, not a
  nicety.
- The server keeps touching a few fields on every write (timestamps, the `error` flag on some datasource
  routes); the plan engine is built to ignore them, so the export must simply not carry them.

---

## Complexity

**Size**: L (24/36) · **Recommended flow**: sdlc-standard

Re-assessed 2026-10-14 after the scope decisions (up from 23/36 when the story was first drafted).
Requirements clarity improved (4 to 3) now that guardrails, skill companion files and the datasource
scope are settled, but file changes rose to the top band (4 to 6): six entity routers, a new export
router, a serialiser core, about eleven datasource exporters (about seven with no existing converter),
the workflow converter, the reference resolver and the tests. Component scope and technical risk stay at
the maximum: the first end-to-end secret-free export (a mask must never leak, and the `env:` derivation
must match another repo), no precedent for byte-stable serialisation, and a destructive full-replace PUT
round-trip guarantee. No split recommended (single repo, below the 27 threshold), but the score is two
points under it. If scope grows, the natural cut is by feature within the repo: (1) serialiser core,
resolver and `env:` derivation with the simple kinds, then (2) the datasource exporters. The assessor
did not check this score against calibration examples.

---

## Story

**As a** platform engineer who needs to bring an entity that already exists on the platform under
version control, **I want** a REST endpoint that exports any entity as a complete, re-applicable
document, **so that** I can commit it to my team's repository without hand-transcribing its
configuration or leaking any credential value into source control.

---

## Background

Teams sometimes build entities directly through the UI before adopting the declarative workflow, or
need to pull an existing entity into a document to start managing it as code. Today there is no way to
do that for most entity types, and the two export endpoints that do exist produce partial, non-re-
creatable output. Without this endpoint, "bring an existing entity under version control" has no path
that doesn't involve manually reconstructing the document by hand.

---

## Acceptance Criteria

- [ ] Given an entity of any in-scope type that exists on the platform, when it is exported through
      this endpoint, then the response is a document sufficient to re-create that entity through apply.
- [ ] Given an entity carrying credential material, when it is exported, then the document contains a
      resolvable reference in place of each credential value, and no credential value — across every
      credential-bearing surface: integration credential values, datasource inline credentials, tool and
      toolkit settings, MCP settings and `mcp_connect_auth_token`, and sensitive prompt variables.
- [ ] Given the exported document, when it is fed to a diff/apply engine against a clean project, then
      the resulting entity is equivalent to the original, aside from platform-assigned identifiers.
- [ ] Given an entity id that does not exist or that the caller lacks access to, when export is
      requested, then the request fails with an error naming the problem rather than returning a
      partial or empty document.
- [ ] Given the same entity, when exported twice in a row with no change in between, then the two
      responses are byte-for-byte identical: same key order, same list order, no timestamps, no nulls.
- [ ] Given any exported document, when its content is inspected, then it contains no platform-assigned
      identifier, no server-managed field (timestamps, `error`/`completed` flags, counters, `created_by`,
      abilities) and no mask string.
- [ ] Given an entity that references another entity (a workflow's assistants, an assistant's skills and
      integrations, a datasource's integration), when it is exported, then each reference is expressed as
      the referenced entity's name and not its id.
- [ ] Given an exported document, when its fields are compared with the fields the kind's update endpoint
      accepts, then every accepted field is either present or listed in this story as deliberately
      non-exportable.
- [ ] Given an exported entity, when its document is converted to the kind's update request (credentials
      sent the way apply sends them: the value resolved from the environment, or the platform's mask
      where the backend defines one) and submitted against the same entity, then every field the
      document carries is unchanged, aside from server-managed timestamps and the datasource `error`
      flag already pinned by [[1-idempotent-write]]. *(The backend-checkable form of "the exported YAML
      can be applied back"; the full export, apply, re-export loop is verified on the IaC side.)*
      **Narrowed 2026-10-15.** Fields the document cannot carry (the non-exportable list above) are not
      covered: the update endpoints are full replace, so some of them change on update. Those cases are
      pinned by strict `xfail` tests and reported as findings to the envelope and engine owners
      (comment on EPMCDME-15312); closing them is a decision for those owners, not part of this story.
- [ ] Given a workflow, when it is exported, then the document carries a structured definition with
      assistants referenced by name, and re-rendering that definition reproduces the stored workflow
      configuration. *(Narrowed 2026-10-15: equality holds for the part of the workflow yaml the envelope
      models; the fields listed as non-exportable are not part of the comparison.)*
- [ ] Given a skill that has companion files, when the skill is exported and the document is converted
      to the skill update request and submitted against the same skill, then the skill's companion files
      are unchanged. *(Narrowed 2026-10-15: the envelope's skill spec has no field to state the
      exclusion, so the document does not say companion files are not included; this story states it.)*
- [ ] *(negative)* Given a datasource of a type that has no export support — including
      `knowledge_base_file` — when export is requested, then the request fails with an error naming the
      type, and no partial document is returned.
- [ ] *(negative)* Given an entity whose name would make a reference to it ambiguous (another entity of
      the same kind in the same scope has the same name), when an entity referencing it is exported,
      then the request fails with an error naming the clashing name rather than emitting an ambiguous
      reference.

---

## Design

Grounded in `codemie/src/codemie/rest_api/routers/*.py` — the two export precedents that already
exist there are partial, and the gap between them and this story is the design's own argument.

### 1. What exists today and why it doesn't satisfy this story

```python
# existing, unchanged — routers/skill.py
@router.get("/skills/{skill_id}/export")
def export_skill(skill_id: str, user: User = Depends(authenticate)):
    """Export a skill as a .md file — only name and description, not a re-creatable definition."""
    result = SkillService.export_skill(skill_id, user)
    return Response(content=result.content, media_type="text/markdown", ...)

# existing, unchanged — routers/index.py
@router.get("/index/{index_id}/export")
def get_index_info_export(index_id: str, user: User = Depends(authenticate)):
    """Export a datasource's STATUS as markdown — not its configuration."""
    content = IndexStatusService.get_index_status_markdown(index)
    ...
```

Neither endpoint returns a document `apply` could consume: `export_skill` drops every field except
name/description, and `get_index_info_export` isn't a definition at all — it's a human-readable status
report. This is why the story frames a "canonical serialiser," not an extension of either.

---

### 2. Modules involved

```mermaid
flowchart TB
    subgraph routers["rest_api/routers/*.py — existing, extended"]
        AR[assistant.py]
        WR[workflow.py]
        IR[index.py — datasources]
        SR[skill.py]
        CR[category.py]
        PSR[project_settings.py /<br/>user_settings.py — integrations]
    end

    subgraph new["new — canonical export serialiser"]
        SER[export/serializer.py<br/>EntityExporter — one method per kind]
        REF[export/references.py<br/>id to name rewriting, ambiguity check]
        CRED[export/credentials.py<br/>credential reference derivation]
        EP[export/router.py<br/>GET /v1/export/{kind}/{id}]
    end

    subgraph doc["codemie_iac.document (EPMCDME-15309, Python IaC tool) — shape only, no import"]
        DOC[Document, DocumentMeta, CredentialRef shape]
    end

    AR -->|reads current entity| SER
    WR --> SER
    IR --> SER
    SR --> SER
    CR --> SER
    PSR -->|the entity kind that actually carries<br/>credential_values — AC2's main case| SER
    SER --> REF
    SER --> CRED
    SER -->|produces a response matching| DOC
    EP --> SER
```

Integrations are not incidental here — they are the entity kind that actually carries
`credential_values` (a `list[CredentialValues]`, one per credential, per `models/integration.py`),
so `IntegrationExporter` is what AC2 ("no credential value") is really about; the other kinds
mostly carry references *to* an integration, not the secret itself.

The backend does not import the Python IaC tool's `Document` type (that would be a backend dependency on
a client-side package — the wrong direction); `SER` independently produces a response whose **shape**
matches `codemie_iac.document`'s format byte-for-byte, which is what lets story 2's export capability
parse the response straight into a `Document` with no translation layer. Because the envelope rejects
unknown fields, parity is enforced by golden fixtures kept in this repository (see Testing), not by an
import.

---

### 3. The canonical serialiser — one interface, one reader per kind

```python
# new — export/serializer.py
class EntityExporter(Protocol):
    def export(self, entity_id: str, user: User) -> ExportedDocument: ...


class AssistantExporter:
    """Reuses the existing read path — AssistantService.get(assistant_id) — then narrows
    credential-carrying fields to references and rewrites ids to names, exactly as
    Document.from_entity() will (story 1)."""

    def export(self, assistant_id: str, user: User) -> ExportedDocument:
        assistant = AssistantService.get(assistant_id, user)   # existing service call, unchanged
        return ExportedDocument(
            meta=DocumentMetaPayload(kind="assistant", name=assistant.slug),
            # request fields only (no id / timestamps), integration alias -> CredentialRef shape,
            # skill_ids -> skill names (fails naming the clash if a name is ambiguous)
            spec=_to_reference_spec(assistant),
        )


class IntegrationExporter:
    """The entity kind AC2 is really written for: credential_values is where a credential
    actually lives (models/integration.py). The read path already masks the value."""

    def export(self, integration_id: str, user: User) -> ExportedDocument:
        integration = IntegrationService.get(integration_id, user)   # existing, unchanged
        return ExportedDocument(
            meta=DocumentMetaPayload(kind="integration", name=integration.alias),
            spec=ExportedIntegrationSpec(
                credential_type=integration.credential_type,
                credential_values=[
                    # cv.value is never read here — only cv.key and integration.alias feed the
                    # placeholder. Same deterministic convention as codemie_iac.document's own
                    # from_entity() (env:{ALIAS}_{KEY}, uppercased/sanitized) — independently
                    # implemented (cross-repo), but the same convention, per Decisions taken.
                    CredentialRefPayload(key=cv.key, ref=_credential_ref_for(integration.alias, cv.key))
                    for cv in integration.credential_values
                ],
            ),
        )
```

Per-kind summary (the exported spec is always the kind's create/update request fields):

| Kind | `meta.name` | References exported by name | Notes |
|---|---|---|---|
| assistant | slug | integration alias (tool, toolkit, MCP settings); skill names (from `skill_ids`) | sensitive prompt variables become credential references; MCP configuration stays inside the assistant |
| workflow | workflow name | assistant name (in place of `assistant_id`) | `yaml_config` converted to a structured definition; re-render must equal the stored normalised text |
| datasource | datasource name | integration alias (from `setting_id`) | one exporter per supported type; inline credentials become references; `knowledge_base_file` and types without an exporter fail |
| integration | alias | — | every `credential_values` entry becomes a reference |
| skill | skill name | — | companion files are not exported (documented limitation); applying the document leaves them intact |
| category | category name | — | |

Datasources are the hard case flagged in Complexity: about eleven type-specific create and update route
pairs under `rest_api/routers/index/` (`code_repositories`, `kb_atlassian`, `kb_azure_devops`,
`kb_git_faq`, `kb_google`, `kb_sharepoint`, `kb_xwiki`, `providers`), only some of which already have an
IaC-shaped converter to build from (code, confluence, jira, google). `DatasourceExporter` is not one
class but one per-type exporter behind the same `EntityExporter` protocol — mirroring how the router
package itself is already type-specific rather than one generic CRUD surface (idempotent-write story
reaches the same conclusion for the write side, §4 there). A type without an exporter is an error, not a
best-effort partial document. The exportable set equals what apply can write back (every type above
except `knowledge_base_file`); otherwise export would produce documents apply rejects.

---

### 4. Usage algorithm

```mermaid
sequenceDiagram
    actor Eng as Engineer (bringing an existing entity under version control)
    participant EP as GET /v1/export/{kind}/{id}
    participant Reader as existing entity service (e.g. AssistantService)
    participant Exp as EntityExporter (kind-specific)

    Eng->>EP: GET /v1/export/assistant/{id}
    EP->>Reader: get(id)  — the same read path every other consumer uses
    alt not found / no access
        Reader-->>EP: 404 / 403
        EP-->>Eng: error naming the problem (AC4) — no partial document
    else found
        Reader-->>EP: entity (credential values already masked by the read path)
        EP->>Exp: export(entity)
        Exp->>Exp: request fields only; ids rewritten to names (ambiguity -> error);<br/>credential fields replaced by references (value never read)
        alt unsupported type / ambiguous name
            Exp-->>EP: error naming the type or name
            EP-->>Eng: error — no partial document
        else exportable
            Exp-->>EP: ExportedDocument
            EP-->>Eng: document — apply-ready, byte-stable on repeat export (AC5)
        end
    end
```

---

### 5. Out of scope, unchanged from story.md

An SDK `export` method and CLI `export` command (stories 2 and 3, matching this response byte-for-
byte); `plan`/`apply`; reconciling this serialiser with the diff/apply engine's own internal
serialisation (accepted risk, follow-up).

---

### 6. Testing

- `test_export_router.py`: one export test per in-scope entity kind, asserting the response equals a
  committed golden fixture for that kind (the shape contract with [[2-document-envelope]]).
- `test_export_round_trip.py`: per kind, export, convert the document to the kind's update request
  (credentials as the mask), submit against the same entity, assert the stored entity is unchanged aside
  from the pinned server-managed fields (AC3 backend form, AC8/9).
- `test_export_credentials.py`: exported document never contains a credential value for any entity
  kind that carries one, across every credential-bearing surface listed in AC2 (parametrised the same way
  `test_assistant.py`'s existing masking test is), and never a mask string.
- `test_export_credential_reference.py`: golden vectors for the reference derivation (case, runs of
  punctuation, missing alias, leading/trailing separators), shared with [[2-document-envelope]].
- `test_export_determinism.py`: same entity exported twice in a row (no change in between) produces
  byte-identical responses (AC5), including entities with null fields and unordered stored lists.
- `test_export_references.py`: references appear as names and no id appears anywhere; an ambiguous name
  fails naming the clash.
- `test_export_workflow.py`: structured definition with assistant names; re-rendering reproduces the
  stored normalised text.
- `test_export_datasource_types.py`: one round trip per exportable type (the eleven listed above, each
  keyed as its update route is); `knowledge_base_file` and a type apply does not accept fail naming the
  type.
- `test_export_skill_companion_files.py`: exporting a skill that has companion files, then submitting the
  document as the skill update, leaves the companion files unchanged.
- `test_export_not_found.py`: unknown id and inaccessible id both fail naming the problem (AC4), never
  a 200 with a partial body.

---

## Out of Scope

- An SDK `export` method and CLI `export` command — separate stories whose response shape this
  endpoint defines.
- `plan`, `apply`, and the identity/convergence work they depend on.
- How the plan engine compares name-based references and unchanged credential references against live
  state, and how single-entity apply resolves names against the target project — owned by the plan/apply
  stories; this story only guarantees a complete, name-referenced, credential-reference-only document.
- Reconciling this story's serialiser with any internal serialisation the diff/apply engine already
  uses — tracked as a follow-up per the accepted risk above.
- Guardrails — a follow-up story adds a guardrail kind to the envelope, an idempotency audit and the
  exporter.
- Skill companion files — exported only if a follow-up extends the envelope's skill spec to carry them.

---

## Open Questions

Settled 2026-10-14: guardrails (dropped to a follow-up), skill companion files (not exported, documented,
applying leaves them intact), exportable datasource types (full parity with apply).

- **Apply's datasource type list — settled 2026-10-15.** Checked against the Python apply engine: all 13
  types the exporter registers (code git, svn, summary and chunk-summary variants, confluence, jira,
  xray, azure_devops_wiki, azure_devops_work_item, git_faq, google, sharepoint, xwiki, provider) map to
  engine types with create and update both accepted, and no naming differences (the git FAQ type is
  `knowledge_base_git_faq` on both sides). The engine rejects only `knowledge_base_file`, which the
  exporter also rejects.
- **`project_name` on datasources — settled 2026-10-15:** exported (the envelope requires it).
- **`openapi_spec` — settled 2026-10-15:** exported as a credential reference (the envelope has no inline
  slot).
- **Accepted-risk wording.** Is the "two serialisers" risk still accurate for the shipped diff/apply
  engine?
