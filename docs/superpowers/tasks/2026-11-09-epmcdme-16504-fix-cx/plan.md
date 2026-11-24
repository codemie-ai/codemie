# EPMCDME-16504: API fix plan

EPMCDME-16504 / !4725 · 2026-11-09 · round cx · [combined plan and all review links](../JBBMAFR-8/save-validation-fix-plan-cx.md) · [16503 prerequisite](../EPMCDME-16503/fix-plan-cx.md)

**Consume the linker's locations, remove repeated line-map work, return safe errors, and close the route-test gaps.**

## 16504-A — Use source locations and one request-local line map

Rebase on the completed 16503 change. Replace `to_config_path`, `_owner`/`_tool_owning_bindings`, and general numeric-segment rewriting with the typed location from the report. Keep one formatter for the existing public `config_path` syntax. Use the typed segments for line lookup; never infer list indices from digits in a string.

Current sources: [contract_issues.py](../../codemie/src/codemie/workflows/validation/contract_issues.py), [line_lookup.py](../../codemie/src/codemie/workflows/validation/line_lookup.py), [workflow.py:348](../../codemie/src/codemie/workflows/workflow.py#L348), and [save_validation.py:129](../../codemie/src/codemie/service/workflow/save_validation.py#L129).

Build source metadata within a single request and share it between schema validation, rejection formatting, and successful response formatting where the source text is identical. Reuse the parsed source and YAML node marks already available from the schema stage when possible. Keep the public executor return shape compatible; an optional internal request-context argument can carry the metadata without adding mutable state to `ContractLinkReport`.

The metadata must distinguish original client YAML from normalised YAML. Invalidate or use a separate entry when text changes. Do not cache between requests or on a stored workflow object. Keep a null lookup for invalid or unavailable source. Preserve exact key tokens in the lookup, including dots/brackets in literal keys. Add a contract-specific token lookup if changing existing structural-error string paths would cause a broader migration.

Do not build a rejection-only finder on a successful executor path. Do not change MCP governance or skip the separate checks of raw request fields and parsed YAML. This fix removes repeated line-metadata work; it does not claim that all 12 parses can safely become one parse.

Regression tests:

- Drive real derive → report → issue formatting for state/tool ownership, empty bindings, custom nodes, and transitions.
- `tool_args."1"` has the actual YAML line. `"01"`, literal dots/brackets, Unicode, and true list indices do not collide.
- Comments and blank lines survive line navigation after model normalisation. A model ID changes from `o1` to `o1-2024-12-17`, while issue lines still refer to submitted text.
- One metadata build per distinct source text within a linked request. Exercise clean success, warning success, strict rejection, and two separate requests. A second request with changed text must not reuse the first map.
- Existing public `config_path`, `config_section`, IDs, warnings, coverage, and truncation behaviour stay compatible. Missing exact fields use only the documented parent fallback, never an unrelated item line.

Measure parse counts again, but test the single-build property rather than permanently pinning an incidental total of 10 or 12.

## 16504-B — Safe database errors across all four entry points

Review note [2515764](https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4725#note_2515764). Change database failures to HTTP 503 with a fixed public message and safe details. Never send SQL, bind values, connection strings, or the raw database exception in the response. Use existing server-side diagnostic logging with its redaction rules.

Cover create, update, new validate, and saved validate. Inspect preparation/model lookup, guardrail validation, shared validation, and persistence. Merely passing `db_error_message` to the new route is insufficient: `prepare` and guardrail code currently catch broad exceptions, and update has a separate SQL catch that returns `str(e)`.

Preserve `SQLAlchemyError` until the safe error mapper can classify it, or map it explicitly before each broad catch. Keep existing 403/404/410 responses and structured 400 contract errors intact. Update OpenAPI response documentation if the route contract changes.

Regression tests:

- Parameterise both error formats and all four routes. Inject a `StatementError` containing distinct synthetic SQL and parameter sentinels at each applicable stage.
- Require HTTP 503 and a safe response body; assert every sentinel is absent from the entire serialized response.
- For pre-persistence failure, assert no create/update call and no guardrail persistence. For persistence failure, assert no success response or success-only follow-up action.
- Recheck valid YAML errors, contract rejections with warnings/coverage, access refusals, and autonomous refusals to prevent over-broad 503 conversion.

## 16504-C — Make the final issue fallback independent

Review note [2516691](https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4725#note_2516691). `_shape` catches `_detail`, but `_minimal` can fail through the same `_issue_id` logic. Add a final bounded fallback that does not read the failing record again and can always be serialized.

The caller already knows whether it is shaping a blocking or advisory record. Pass that safe classification to the final fallback. A blocking record must stay an error; a warning must not disappear or turn into a blocking error solely because formatting failed. Use fixed public text. If a unique fallback ID is required, derive it from safe call-local information such as the record index and known class, not damaged record fields. Keep valid metadata when the normal or minimal path can supply it; do not invent `strict_source` after data is lost.

Test normal `_detail` failure, failure of both `_detail` and `_minimal`, and an ID-generation failure. Assert serializable output, preserved blocking/advisory classification, unique fallback IDs, and no raw exception text. A rejected save must remain a structured contract rejection, not a generic 500. Do not weaken the validation of valid `ContractRecord` objects to make malformed test fixtures easier to create.

## 16504-D — Complete route and response comparison tests

Extend `test_contract_parity.py`, route tests, and save-service tests. Keep resources/persistence mocked where needed, but run the actual code whose behaviour each test proves.

| Review concern | Required test |
|---|---|
| Notes 2515765 and 2516692: autonomous order | PUT and saved `/{id}/validate` return 403 for autonomous mode. Guardrail sync, shared validation, and persistence are not called. Cover the effective parsed mode. |
| Note 2516693: autonomous mode only in YAML | New `/validate` with default/top-level Sequential but YAML Autonomous returns 410 after preparation. Shared validation is not called. Retain the top-level Autonomous early-refusal test. |
| Note 2515765: normalisation is never exercised | Give the real model catalogue `o1 → o1-2024-12-17`. Run preparation normally. Assert normalised candidates on all four paths, plus equal findings/coverage within the corresponding pairs and preserved original YAML lines. |
| Note 2515766: access is always patched | Use a real non-admin non-member for 403 and a real plain project member without write/admin privileges for 200 on new `/validate`. Do not patch `project_access_check` or `Ability.can`. Mock data providers/authentication only as needed. Keep write permission required for saved validation. |
| Note 2515767: empty warnings in rejected comparison | Build one configuration with at least one blocking declared issue and one warning. Assert both lists are non-empty before comparing all four responses. Include coverage, IDs, locations, outcomes, and truncation where applicable. |
| Existing `strict_source` change | Exercise workflow/state strict, label-free issues, strict contract-form findings, graph failure caused by strict, coverage that blocks in warn, and settings failure. Verify metadata survives create/update/both checks and formatter fallback where recoverable. |
| Local gate failures | Add the feature key to `test_env_example.py`'s explicit allowlist. Parameterise the create-route expectation with the linker flag on and off. Set the flag in each relevant test instead of inheriting `.env`. |

The detailed `strict_source` requirement is note 2516574. Preserve its exception for `CAUSE_SETTINGS_FAILED` and the absence of metadata on coverage findings that block in every mode. Existing implementation tests already cover many cases; add missing boundary tests rather than duplicate them. Submit current evidence for both unresolved strict-source threads.

## 16504-E — Name encoding decision and Unicode literal cleanup

Review notes [2516689](https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4725#note_2516689) and [2516690](https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4725#note_2516690).

Proposed decision: JSON names remain data, with the existing documented control-character removal and display bounds. Do not HTML-entity-encode semantic names. JSON string escaping and safe HTML rendering are different responsibilities. Preserve exact internal location tokens even if the displayed field is shortened. Render messages, details, labels, and names as text in clients; any HTML-rendering consumer must sanitize at its render boundary.

The inspected 16505 `IssueGroups.tsx:100–102` renders messages and details as React text; `contractProtocol.ts` retains paths for navigation. This is evidence for these consumers, not proof about every SDK or future client. Trace the remaining field-message and legacy error consumers before closing the decision. Add a malicious-looking name response fixture and a UI rendering/navigation regression under 16505 if coverage is absent. Require literal text, no injected element/event execution, and unchanged semantic key lookup. Record the consumer contract in v6.1 and the backend guide.

Replace the literal U+2028/U+2029 characters in `_CONTROL_CHARS` with explicit `\u2028\u2029` escapes. Retain tests showing that these and C0/C1 controls are removed and ordinary Unicode plus `<>&` remain according to the decision. No general sanitizer rewrite is required. Extract the naming helpers from `policy.py` only if the resulting module has a clear shared responsibility; 693 lines alone is not a reason for broad refactoring.

## Spec, gates, and final review

Own the response/error/access parts of v6.1 listed in the combined plan. Add `strict_source` to the 16504 task spec and plan; retain the implementation's precise rules. Record the JSON/string rejection difference and the limits of check/save equality.

Run focused tests, then the full gate with enterprise and required tools:

```bash
poetry run pytest tests/codemie/workflows/validation tests/codemie/service/workflow/test_save_validation.py tests/codemie/rest_api/routers/test_workflow.py tests/codemie/configs/test_env_example.py -q -rs
make test
```

Run the other required delivery checks and live verification through the child-repository workflow. Compare OpenAPI with enterprise enabled; regenerate only an intended reviewed difference. Do not update a baseline to hide missing enterprise routes. Recheck final changes through code review, including the post-review `strict_source` code and all new fixes. Link test evidence to each related MR thread, with reviewer agreement before resolution.

## Revision

Remote !4725 and local backend head checked: `5b96bf5ce1b18bb211d03c5e1b3c20f7c2e7444c`. Frontend inspected: `06842dcc975f847347280848bca30a207c4a21ac`. Review state was read on 2026-11-09.

## Session scope

The user requested backend 16504 changes. A different agent owns the UI. No UI source is changed in this task. Rebase is required by the selected plan; commit, push, MR replies, and resolution are separate delivery actions.
