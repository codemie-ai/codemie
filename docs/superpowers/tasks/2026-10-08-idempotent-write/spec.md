# Spec: Idempotent write path for apply (EPMCDME-15310)

Backend only (`codemie`, Python/FastAPI). Size L (24/36). Source story: `maia-stories/sdk/2-iac-plan-apply/1-idempotent-write/story.md`.

## Goal

Re-submitting an unchanged document through an existing PUT, keyed by the caller-supplied key, leaves the stored entity unchanged. No new endpoint, no identity resolution, no contract change.

## Definition of "byte-identical stored entity"

Compared after the first apply versus after the second apply of the same document.

Excluded (server-managed, accepted to change on every PUT):
- `update_date`, `updated_date`, `last_reindex_date`.
- Re-randomised ciphertext of sensitive values resubmitted as plaintext on non-deterministic backends (Azure, AWS KMS, GCP KMS, Vault). See Known limitation.

Asserted:
- Every user-settable field is unchanged.
- No new version row is created (`AssistantVersionCompareService.has_configuration_changes` guards this).
- Guardrail assignment rows are unchanged.

Write behaviour is unchanged: no-op writes are not skipped, and the assistant PUT still writes twice (`repository.update`, then `assistant.update()` at `routers/assistant.py:948`).

## Rewrite audit (outcomes)

| Rewrite | Location | Outcome |
|---|---|---|
| `MCPAccessControlService.sanitize_for_save` | `service/mcp/access_control.py:141` | Validate-only (raises, never mutates; `strip_inline_config` / `_strip_one` are no-ops). No fix. Document it and add a test that proves it. |
| `_encrypt_prompt_variables_in_request` + `_restore_existing_encrypted_values` | `routers/assistant.py:2945`, `:2966` | The mask is skipped by encrypt, then restored from the stored value. Idempotent. Document as intentional, unchanged. |
| `_filter_invalid_datasources` / `AssistantService.drop_missing_context` | `routers/assistant.py:2911`, `service/assistant_service.py:968` | Order-preserving, in memory, drops only entries absent from the project's `IndexInfo`. Stable for valid documents. Risk: `Context.index_info_type_from_index_type` (`models/assistant.py:128`) falls back to `KNOWLEDGE_BASE`, so a valid entry of an unmapped type can be dropped. Add a keep-case per datasource type. Fix only where a valid entry is dropped. |
| Workflow `yaml_config` rewrite and model-id normalisation | `routers/workflow.py:101-147` | Intentional normalisation. A hand-authored YAML changes on the first PUT. Test: the second PUT equals the first (fixed point). |
| Category, skill update | `category_service.py:192`, `skill_service.py:602` | Expected idempotent. Assert via router tests. Skills version only on content change. |
| Integration update | `service/settings/settings.py:461-539` | Mask values are skipped. Non-mask secrets are re-encrypted (Known limitation). Keys absent from the request are dropped (risk below). GIT url normalisation is a fixed point. |
| Datasource `update_index` | `models/index.py:887` | Sets `last_reindex_date = update_date` on every call (excluded, server-managed; incremental reindex semantics are not touched). Other rewrites to be audited per route. |

## Acceptance criteria

- AC1: Re-PUT of an unchanged document against an existing key, for assistants, workflows, skills, categories and project/user settings (SCHEDULER, LITE_LLM, GIT, WEBHOOK), leaves the stored entity unchanged under the definition above. Asserted on Plain/Base64 encryption.
- AC2: An assistant sensitive prompt variable or an integration credential sent as `SENSITIVE_VALUE_MASK` leaves the stored value untouched. This extends the existing mask test in `tests/codemie/rest_api/routers/test_assistant.py`.
- AC3: For each datasource type, re-submitting unchanged through its type-specific PUT, keyed by `(project_name, name)` and not by id, leaves the stored datasource unchanged. This is reworded from the story (see Story correction).
- AC4: Documented intentional rewrites still happen exactly: an invalid datasource context entry is dropped, a masked prompt variable is restored, workflow YAML is normalised. Covered by the baseline tests.
- AC5: A POST with no id always creates a new entity, even if an identical one exists (`test_create_always_new.py`).
- AC6: No status code, response schema or validation-rule change on any existing PUT or POST. Silent drop and restore stay silent.

## Test plan

1. Contract baseline is written and committed first (after the user approves the commit). It is a characterization test through TestClient, passing on current code, with one case per datasource route. It records status codes, response shapes, validation failures and silent rewrites. It stays green and unedited. The per-route audit of the datasource PUT bodies (jira, xray, ADO wiki, ADO work item, xwiki, sharepoint, google, git_faq, file, provider, svn) comes after the baseline and is a prerequisite to any datasource fix.
2. Only the AC1-AC3 idempotency tests start red. Add `test_idempotent_write.py` per entity router and one test per datasource type in `test_index.py`.
3. Follow `.ai-run/guides/testing/testing-api-patterns.md`.

## Non-goals

- Decrypt-and-compare or any other change to how sensitive values are encrypted.
- Skipping no-op writes, removing the assistant double write, or changing timestamp behaviour.
- Changing `last_reindex_date` semantics.
- Any new route, including an id-keyed datasource route.
- Name or identity resolution, a combined create-or-update endpoint, or bulk update.
- Export, diff/plan, or Node IaC/SDK.
- Any change to `sanitize_for_save`, `_restore_existing_encrypted_values` or the mask constant.
- Changing status codes, response schemas or validation rules (AC6).
- New code must keep cyclomatic complexity at 15 or below.
- Nothing is committed without the user's explicit approval.

## Known limitation (accepted)

Re-applying a sensitive value as plaintext on Azure, AWS KMS, GCP KMS or Vault changes the stored ciphertext, because those backends are randomised. The plaintext is unchanged. This also covers `ENVIRONMENT_VARS` credentials, which are encrypted with `force_all`. The user accepted this; revisit it if the IaC tool ends up sending plaintext rather than the mask.

## Story correction (for the user and Jira)

Datasource PUTs are keyed by `(project_name, name)`, not by id (for example `index.py:1721`). AC3 in the story says "against its id". Update the story and Jira wording. No id route is added.

## Open risks

- The bodies of the other datasource PUTs are unread. Some routes may reset flags (such as `error`) via `update_index` defaults.
- `SettingsService.update_settings` drops credential keys absent from the request, so a partial document removes stored keys. OAuth, SharePoint and Google types have special paths and are outside the AC1 test set.
- The mapping fallback in `index_info_type_from_index_type` may drop valid context entries for some types.
- `sanitize_for_save` can reject a previously valid document if the MCP catalog state changes. That is validation, not rewriting, and is out of scope.
