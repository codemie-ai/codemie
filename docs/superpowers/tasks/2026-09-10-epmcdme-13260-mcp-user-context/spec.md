# Spec: EPMCDME-13260 — finish the MCP path of user_context propagation

## Problem

`user_context` is populated on `MCPExecutionContext` (toolkit_service.py:243), declared on both
`MCPExecutionContext` (models.py:156-163) and the outbound `MCPToolInvocationRequest`
(models.py:504-508), then stripped on the wire by the exclude set in
`MCPExecutionContext.to_request_fields()` (models.py:185). EPMCDME-13546 added that exclusion because
MCP-Connect `/bridge` was `extra="forbid"`. The counterpart fix (EPMCDME-14847) that makes the bridge
accept the field exists but is not yet deployed. This task re-enables the field on the MCP wire,
adds PII-masked diagnostic logging on both the MCP and DSP tool-invocation paths, updates the tests
that lock the old behaviour, and ships the tool-owner contract doc. It must not deploy before
EPMCDME-14847 is fully rolled out.

The DSP path (provider_tool_factory.py:175) is the working, tested mirror of the target wire
behaviour.

## Resolved design decisions

- **Helper location.** `masked_for_logging()` is a method on `UserContext` in
  `src/codemie/rest_api/security/user.py`; `_mask_value()` and `_mask_email()` are module-level
  helpers in the same file. models.py already imports from this module and `service/` ->
  `rest_api/security/` is a safe import direction, so no cycle.
- **`extra_attributes` on the MCP wire.** Excluded. Add `"extra_attributes"` to the same
  `to_request_fields()` exclude set (models.py:185) from which `"user_context"` is removed. Scoped to
  the MCP path only. Rationale to record inline: `extra_attributes` has no MCP consumer, carries
  deployment-variable raw IdP claims, and appears in no published contract; forwarding it onto a
  brand-new path extends an unconsumed PII surface for no benefit. One-line, reversible.
- **Masked log output for unbounded fields.** In log output only, replace `extra_attributes` and
  `knowledge_bases` with a non-revealing summary placeholder, e.g.
  `"<redacted, 5 keys>"` / `"<redacted, 3 items>"`. Never emit raw values. `knowledge_bases` stays a
  normal field on the wire; this is log redaction, not a wire change.
- **Return type.** `masked_for_logging()` returns a `dict`. Callers log it as a structured field;
  tests assert per key.
- **Tool-owner doc path.** `docs/user-context-in-mcp-tool-invocations.md` — flat file, matching the
  existing `docs/*-reference.md` ad-hoc convention.
- **Diagnostic log line.** A dedicated `logger.debug` line at each site, kept symmetric: in
  `client.py` on the invoke path next to the existing tool-name log (~:237), and in
  `provider_tool_factory.py` `invoke()` just after the request is built (~:175-182). Existing
  `logger.info` messages are not modified.

## Requirements

### 1. Re-enable `user_context`, exclude `extra_attributes` (MCP wire)

- In `MCPExecutionContext.to_request_fields()` (models.py:179-186): remove `"user_context"` from the
  exclude set; add `"extra_attributes"` to it. Do **not** add `exclude_none` to this method — the
  load-bearing `exclude_none=True` is at client.py:128 and drops a `user_context=None` key before the
  wire; it must stay as-is (keeps test_client.py payload assertions green).
- Rewrite both docstrings that cite EPMCDME-13546 as current justification: the field description at
  models.py:156-163 and the method docstring at models.py:165-177. They must state that
  `user_context` IS serialised and forwarded (EPMCDME-13260), that this requires an MCP-Connect
  release which accepts the field, and that `extra_attributes` is deliberately excluded on this path.
- No change to `MCPToolInvocationRequest` (models.py:504), `toolkit_service.py:243`, or
  `client.py:128`.

### 2. `UserContext.masked_for_logging()` (~40 lines, written fresh on `main`)

Do NOT cherry-pick from `origin/EPMCDME-13260`. Returns a `dict`.

- **Masked:** `email` -> `_mask_email()` (`j***@epam.com` shape), `name`, `picture`, `username`
  (partial).
- **Kept readable:** `id`, `roles`, `is_admin`, `is_maintainer`, `is_auditor`, `user_type`,
  `project_names`, `admin_project_names`.
- **Redacted to a summary placeholder:** `extra_attributes` (count of keys), `knowledge_bases`
  (count of items). Raw values never emitted.
- `_mask_value()` / `_mask_email()` are module-level in `user.py`. `None` fields are omitted or
  passed through unchanged (match existing `model_dump(exclude_none=True)` habit; pin exact behaviour
  in the plan).

### 3. Masked diagnostic log at both sites

- `src/codemie/service/mcp/client.py`: one `logger.debug` line on the invoke path (~:237), logging
  the masked dict as a structured field. Module uses `from codemie.configs.logger import logger`.
- `src/codemie/service/provider/provider_tool_factory.py`: one net-new `logger.debug` line in
  `invoke()` just after the request is built (~:175-182). Module uses `from codemie.configs import
  logger`. `extra_attributes` is still forwarded on the DSP wire here, so the shared helper's
  redaction of it is load-bearing on this path.
- Existing `logger.info` "Invoking tool" / success messages unchanged.

### 4. Tests

- **Flip the exclusion asserts:** `test_models.py` asserts at :87, :107, :140;
  `test_models_1_2_auth_config.py` asserts at :224, :245.
- **Delete** the two dedicated tests: `test_models.py::test_to_request_fields_excludes_user_context_regardless_of_value`
  (:177) and `::test_to_request_fields_never_includes_user_context` (:197). Do not invert them.
- **Every exact-dict-equality assertion** on `to_request_fields()` output across those two files must
  account for `"user_context": None` now being emitted (add the key, or switch to a subset
  assertion). `extra_attributes` must NOT appear.
- **Grep** `test_execution_context_integration.py` and `test_client.py` for `user_context` /
  exact-payload assertions and reconcile (test_client.py cases are expected to stay green via
  client.py:128 — verify, do not assume).
- **Add positive coverage:** `user_context` present in the serialised body when set; absent when
  `None`; `extra_attributes` absent from the MCP wire even when set on the `UserContext`;
  `auth_token` never present in `UserContext`; `masked_for_logging()` masks `email`/`name`/`picture`
  and keeps `roles`/`is_admin`/`project_names`; `extra_attributes` and `knowledge_bases` never
  appear raw in the masked output.
- Helper tests live near `UserContext` (`tests/codemie/rest_api/security/test_user_context.py`).

### 5. Tool-owner documentation

New file `docs/user-context-in-mcp-tool-invocations.md`. Must cover: the `_meta` key
`codemie.ai/user-context`; the field list, all optional; values backend-populated from the
authenticated session, tools cannot inject or override; trust model is transport-level (only the
CodeMie backend can reach the tool), NOT signed; no credentials (`auth_token` is excluded — to act
as the user against an external system use `mcp_headers` / `env`); PII obligations for tool owners
handling `email` / `name` / `picture`; it is a per-call snapshot, not a session. Note that
`extra_attributes` is not forwarded on the MCP path.

### 6. MR description — deploy sequencing

Must state: MCP-Connect EPMCDME-14847 must be merged, deployed, and fully rolled out across all
bridge buckets (StatefulSet; backend routes across buckets at client.py:85-98) BEFORE this MR
merges. Un-excluding the field early recreates the EPMCDME-13546 platform-wide regression that
reopened this ticket. Ship as two separate releases with a completed rollout in between. Ask a
reviewer to post `/sanity` (touches security-relevant code).

## Acceptance criteria

- `user_context` is emitted on the MCP wire when a user is present, and the key is absent (via
  `exclude_none=True` at client.py:128) when it is `None`.
- `extra_attributes` is absent from the MCP wire payload (excluded in `to_request_fields()`) even
  when set on the `UserContext`, while remaining present on the DSP wire and unchanged in
  `UserContext.from_user()` (user.py:165).
- Both docstrings (models.py:156-163 and models.py:165-177) describe the EPMCDME-13260 behaviour and
  the `extra_attributes` exclusion; no stale EPMCDME-13546 rationale remains.
- `to_request_fields()` gains no `exclude_none`; client.py:128 is unchanged.
- `UserContext.masked_for_logging()` returns a dict that masks `email` (`j***@epam.com`), `name`,
  `picture`, partial `username`; keeps `id`, `roles`, `is_admin`, `is_maintainer`, `is_auditor`,
  `user_type`, `project_names`, `admin_project_names` readable; replaces `extra_attributes` and
  `knowledge_bases` with a count-only placeholder.
- `extra_attributes` and `knowledge_bases` never appear raw in any log output on either path.
- A dedicated `logger.debug` line logs the masked context on the MCP invoke path (client.py) and in
  `provider_tool_factory.invoke()`; existing `info` messages are untouched.
- The two dedicated exclusion tests are deleted (not inverted); all exact-dict-equality assertions on
  `to_request_fields()` output across test_models.py and test_models_1_2_auth_config.py pass with the
  new key set; test_execution_context_integration.py and test_client.py are reconciled.
- Positive tests listed in Requirement 4 exist and pass.
- `docs/user-context-in-mcp-tool-invocations.md` exists and covers every point in Requirement 5.
- The MR description calls out the EPMCDME-14847 rollout-before-merge sequencing, the two-release
  plan, and the `/sanity` request.

## Non-goals

- No new wire field, model, schema entry, or feature flag.
- No conditional on `tools/list` vs `tools/call` in the backend (backend sends unconditionally;
  MCP-Connect forwards only on `tools/call`).
- No change to `exclude_none` semantics anywhere, including no `exclude_none` on `to_request_fields()`.
- No change to what the DSP path forwards (it still forwards `extra_attributes`).
- No removal of `extra_attributes` from `UserContext.from_user()` (user.py:165).
- No addition of `is_auditor` or `extra_attributes` to `epam_ai_run.spi.schema.json`.
- No masking of any wire payload — masking is log output only.
- No changes in the codemie-mcp-connect-service repo; no deletion of the stale
  `origin/EPMCDME-13260` branch; no cherry-pick from it.
- No change to the env user-context injection at toolkit_service.py:1696, the `UserContext` model
  shape, or toolkit_service.py:243.

## Open risks

- Deploy-ordering regression is unmitigated (no feature flag): merging before EPMCDME-14847 is fully
  rolled out across all bridge buckets recreates the platform-wide EPMCDME-13546 failure. Controlled
  only by the MR-description sequencing note and reviewer discipline.
- Test surface may exceed the brief's enumerated sites; `test_execution_context_integration.py` was
  not inventoried for `user_context` / payload assertions in Stage 1 and must be grepped during
  planning.
- Correctness depends on a separate-repo release this spec cannot verify.
