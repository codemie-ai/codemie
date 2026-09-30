# Technical Research

**Task**: zephyrsquad credential-types tool-catalog integrations deprecation
**Generated**: 2026-09-03
**Research path**: filesystem

---

## 1. Original Context

Fully remove the ZephyrSquad integration/tool from the CodeMie backend (repo: codemie, currently checked out at /Users/oleg_sotnichenko/codemie-dev/codemie-13968, branch EPMCDME-10913_remove-zephyrsquad). Product owner explicitly wants total removal — no deprecation flag, no backward compatibility, no data migration (no prod usage exists). This supersedes a previously-merged "deprecate" approach (MR codemie!3904) that added: a `deprecated: bool` field to `ToolMetadata`/`Tool`, marked `ZEPHYR_SQUAD_TOOL` as deprecated, and added a `DEPRECATED_CREDENTIAL_TYPES` registry + `validate_credential_type_not_deprecated` check in `settings_request_validator.py` that blocks writes for deprecated credential types with a 410.

Find and document EVERY place ZephyrSquad appears in the backend:
- The tool implementation directory (likely `codemie_tools/qa/zephyr_squad/` or similar — tools_vars.py, tools.py, models, etc.)
- Tool catalog registration (wherever tools are registered/listed for assistants — this is what shows up in "tools on assistant edit page")
- `CredentialTypes` enum (ZEPHYR_SQUAD member) and every place that enum is matched/dispatched on (settings routers, credential validation, integration filters — this is what shows up in "filters dropdown on integrations page")
- The `deprecated` field additions from MR 3904 in `codemie_tools/base/models.py` and `Tool.from_metadata` — determine whether `deprecated` is used by anything OTHER than ZephyrSquad; if ZephyrSquad is its only user, the field itself should be removable
- The `DEPRECATED_CREDENTIAL_TYPES` registry and `validate_credential_type_not_deprecated` in `settings_request_validator.py` — determine whether other credential types are in this registry; if ZephyrSquad was the only entry, the whole mechanism can be removed
- Tests referencing ZephyrSquad or the deprecated-credential-type mechanism (unit tests, router tests, QA toolkit tests)
- Any config/env vars, docs, README, or fixture data mentioning ZephyrSquad
- Any DB enum/migration referencing ZEPHYR_SQUAD (note: no migration needed per product decision, but flag if the enum is used in a DB column type — e.g. a Postgres enum type that would need an ALTER if we drop the value, vs a plain string column which is fine to just stop using)

Report exact file paths and line ranges for each hit, grouped by concern (tool implementation / catalog registration / credential type enum & validators / deprecated-flag mechanism / tests / docs). Call out anything that looks risky to remove blind (e.g. shared code paths, other credential types depending on the same generic mechanism).

---

## 2. Codebase Findings

### Existing Implementations

**Tool implementation directory — `src/codemie_tools/qa/zephyr_squad/` (delete whole package):**
- `src/codemie_tools/qa/zephyr_squad/__init__.py:1` — package marker
- `src/codemie_tools/qa/zephyr_squad/tools_vars.py:1-48` — `ZEPHYR_SQUAD_TOOL` `ToolMetadata` definition, including `deprecated=True` at line 47
- `src/codemie_tools/qa/zephyr_squad/models.py:1-54` — `ZephyrSquadConfig`, `ZephyrSquadToolInput`
- `src/codemie_tools/qa/zephyr_squad/tools.py:1-68` — `ZephyrSquadGenericTool` (the `CodeMieTool` implementation)
- `src/codemie_tools/qa/zephyr_squad/api_wrapper.py:1-206` — `ZephyrRestAPI`, a JWT-signed REST client (uses `jwt`, `requests`)

**Tool catalog registration:**
- `src/codemie_tools/qa/toolkit.py:21-22,31` — imports `ZephyrSquadGenericTool` / `ZEPHYR_SQUAD_TOOL` and registers `Tool.from_metadata(ZEPHYR_SQUAD_TOOL, ...)` inside `QualityAssuranceToolkitUI.tools`. This is the exact list that renders on the assistant tool-edit page.

**Credential type enum and dispatch:**
- `src/codemie_tools/base/models.py:97` — `CredentialTypes.ZEPHYR_SQUAD = "ZephyrSquad"` enum member. Sibling `_ZEPHYR_CLOUD = "ZephyrCloud"  # Deprecated` at line 96 is an unrelated, older, purely-cosmetic deprecation (underscore-prefix convention, no API flag) — leave it alone, out of scope.
- `src/codemie/service/settings/settings.py:42` — imports `ZephyrSquadConfig`
- `src/codemie/service/settings/settings.py:110-112` — `ZEPHYR_SQUAD_ACCOUNT_ID` / `ZEPHYR_SQUAD_ACCESS_KEY` / `ZEPHYR_SQUAD_SECRET_KEY` field-name constants (these are dict-key names for DB-backed settings fields, not OS env vars)
- `src/codemie/service/settings/settings.py:146-147` — access/secret key added to `LIST_OF_SENSITIVE_FIELDS`
- `src/codemie/service/settings/settings.py:175-179` — `ZEPHYR_SQUAD_FIELDS` mapping dict
- `src/codemie/service/settings/settings.py:246` — `ZephyrSquadConfig: CredentialTypes.ZEPHYR_SQUAD` entry in `__CREDENTIAL_CONFIG_TO_TYPE`
- `src/codemie/service/settings/settings.py:1654-1670` — `get_zephyr_squad_creds()` classmethod. **Confirmed dead code** — no callers found anywhere in `src/`; the real runtime path is the generic `SettingsService.get_config(config_class)`. Safe to delete without touching a live call chain.
- `src/codemie/service/settings/settings_tester.py:40-41` — imports `ZephyrSquadConfig` / `ZephyrSquadGenericTool`
- `src/codemie/service/settings/settings_tester.py:93` — `CredentialTypes.ZEPHYR_SQUAD: SettingsTester._test_zephyr_squad` dispatch entry
- `src/codemie/service/settings/settings_tester.py:132-133` — `_test_zephyr_squad()` method

**Cosmetic/no functional dependency:**
- `src/codemie/service/search_and_rerank/tool.py:255,257,268` — docstring example text uses the literal string `"ZephyrSquad"` purely as a tokenization/example sample; no functional dependency on the tool existing.

### Architecture and Layers Affected

- **Tool implementation layer** — `codemie_tools/qa/zephyr_squad/*` (LangChain-style `CodeMieTool` + Pydantic config/input models + raw REST client)
- **Tool catalog / toolkit registration layer** — `codemie_tools/qa/toolkit.py`, `codemie_tools/base/models.py`
- **Settings/credentials service layer** — `codemie/service/settings/settings.py`, `settings_tester.py`
- **API validation layer** — `codemie/service/settings/settings_request_validator.py` (`DEPRECATED_CREDENTIAL_TYPES` + `validate_credential_type_not_deprecated`)
- **REST router layer** — `codemie/rest_api/routers/user_settings.py`, `project_settings.py`
- **Persistence layer** — SQLModel `Settings.credential_type` column backed by a native Postgres ENUM type (`credentialtypes`), plus its Alembic migration history
- **Search/indexing layer** — `search_and_rerank/tool.py` (cosmetic docstring reference only, no functional coupling)

### Integration Points

- Toolkit registration pattern: `Tool.from_metadata(TOOL_METADATA_CONSTANT, tool_class=ToolClass)` inside a `ToolKit` subclass — mirrored by sibling `qa/zephyr` (Zephyr Scale, **kept, do not touch**) and `qa/xray` packages. Naming collision risk: do not confuse `zephyr` (Zephyr Scale) with `zephyr_squad` (removal target).
- Credential dispatch chain: `CredentialTypes` enum member → per-type `*_FIELDS` mapping dict → `__CREDENTIAL_CONFIG_TO_TYPE` dict (config class → enum) → generic `SettingsService.get_config(ConfigClass)` resolves stored settings at runtime.
- `SettingsTester` dispatch: a `CredentialTypes -> callable` dict (`settings_tester.py:93` area) maps to a `_test_<tool>()` instance method that instantiates the tool and calls `.healthcheck()`.
- `validate_credential_type_not_deprecated` call sites (4 total — 2 routers × create + update):
  - `src/codemie/rest_api/routers/user_settings.py:34` (import), `:133` (create), `:176` (update)
  - `src/codemie/rest_api/routers/project_settings.py:33` (import), `:95` (create), `:148` (update)
- Shared dependency check: `pyjwt` (`api_wrapper.py` does `import jwt`) is **also used independently** by `codemie/service/jwt_service.py` and `codemie/rest_api/security/idp/local.py` — do NOT remove this dependency from `pyproject.toml`. Same for `requests`, used ubiquitously elsewhere.
- `pyproject.toml:141` declares `zephyr-python-api = "^0.1.0"`. A grep for `zephyr_python_api`/`import zephyr` under `src/` returned no hits — the actual ZephyrSquad tool builds raw HTTP requests via its own `api_wrapper.py` rather than importing this package. **Action needed before removing this dependency**: explicitly check `src/codemie_tools/qa/zephyr_squad/api_wrapper.py` and `src/codemie_tools/qa/zephyr/` (Zephyr Scale, kept) for the actual import name to confirm the package isn't used by Zephyr Scale.

### Patterns and Conventions

- Per-tool file split convention: `tools_vars.py` (ToolMetadata constant) / `models.py` (Config + Input schema) / `tools.py` (CodeMieTool subclass) / `api_wrapper.py` (raw REST client) — remove the whole directory as a unit.
- Deprecation-registry pattern introduced by MR!3904: a single generic `DEPRECATED_CREDENTIAL_TYPES: dict[CredentialTypes, str]` plus one validator function called from both settings routers, explicitly designed so "no router changes needed" to deprecate another type (docstring at `settings_request_validator.py:92-112`). For full removal this entire pattern is now unused dead weight — ZephyrSquad was and remains its only entry.
- Native Postgres ENUM for the `credential_type` column, managed via `alembic_postgresql_enum`'s `op.sync_enum_values(...)` in each migration that adds a new credential type. Every past migration re-lists the full value set.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists but has no guide covering tool catalogs, credential types, or ZephyrSquad specifically. The only tangential hit — `.ai-run/guides/security/dependencies.md:38` — mentions "deprecation warnings" only in the context of `poetry check --lock` output; unrelated to this domain.

### Architectural Decisions

The prior "deprecate, don't remove" decision is fully documented in the superseded task folder `docs/superpowers/tasks/2026-07-31-deprecate-zephyrsquad/`:

- `spec.md:1-65` — explicitly **rejected removing** the `ZephyrSquad` enum value, stating it "would break deserialization of existing rows" (spec.md:46).
- `plan.md:33-95` — introduced `DEPRECATED_CREDENTIAL_TYPES` as a generic, reusable deprecation guard, framed as "Deprecating the next integration = one dict entry, zero router edits" (plan.md:61).
- `technical-analysis.md:77,166,169,177` — the key rationale record: `CredentialTypes.ZEPHYR_SQUAD` is persisted in Postgres, so "removing it from the Python enum would break deserialization of existing rows" (line 169). **This is the exact risk the current full-removal task must now revisit** — see Section 6.
- `code-review-final.json:10-19` — recorded review verdict confirming the shipped mechanism: `deprecated: Optional[bool] = False`; `ZEPHYR_SQUAD_TOOL` sets `deprecated=True`; 410 Gone blocking; `CredentialTypes.ZEPHYR_SQUAD` retained "so existing rows deserialize normally."
- `docs/superpowers/tasks/2026-09-03-epmcdme-10913-remove-zephyrsquad/.state.json` — the current in-progress task (sdlc-light, branch `EPMCDME-10913_remove-zephyrsquad`); only `.state.json` exists so far in this run directory before this technical-analysis.md was written.

### Derived Conventions

- Historically, credential-type enum values were never removed once persisted — only flagged (underscore-prefix `_ZEPHYR_CLOUD`, or the newer `DEPRECATED_CREDENTIAL_TYPES` dict). **The current task explicitly breaks this convention**, which is why the DB-enum risk below needs a deliberate decision in the plan, not a blind deletion.
- No TODO/HACK/NOTE/ADR/DECISION markers exist adjacent to ZephyrSquad or `DEPRECATED_CREDENTIAL_TYPES` code beyond the docstring in `settings_request_validator.py` itself.

### External Documentation Findings

Not applicable — no external library/API research was needed; this is a pure internal-removal task with no new third-party integration surface.

---

## 4. Testing Landscape

### Existing Coverage

**ZephyrSquad-specific test files (delete entirely):**
- `tests/codemie_tools/qa/zephyr_squad/test_squad_generic_tool.py:1-79` — unit tests for `ZephyrSquadGenericTool` (healthcheck, invoke, config)
- `tests/codemie_tools/qa/zephyr_squad/test_api_wrapper.py:1-97` — unit tests for `ZephyrRestAPI` request-signing wrapper
- `tests/codemie_tools/base/test_models_deprecated_flag.py:1-34` — tests the generic `deprecated` flag added purely for ZephyrSquad; no other feature depends on it, delete whole file

**Mixed files needing targeted edits (not full deletion):**
- `tests/codemie_tools/qa/test_qa_toolkit.py:17,25,33-41,46-54` — `test_get_definition` asserts `len(toolkit_ui.tools) == 5` (line 25); `test_tools_property` asserts `len == 5` + `"ZephyrSquad" in tool_names` (lines 33-41); `test_zephyr_squad_marked_deprecated` (46-50) and `test_zephyr_squad_metadata_deprecated_flag` (53-54) are entirely ZephyrSquad-specific — delete those two tests, update the count/membership assertions to drop by one, remove the `ZEPHYR_SQUAD_TOOL` import at line 17
- `tests/codemie/service/settings/test_settings_request_validator.py:25,27,344-386` — the whole "EPMCDME-10913 — deprecated credential-type registry" block: `test_zephyr_squad_is_registered_as_deprecated` (357-359, ZephyrSquad-only), `test_validate_credential_type_not_deprecated_rejects_registered_types` (362-371, parametrized generically over `list(DEPRECATED_CREDENTIAL_TYPES)`), `test_validate_credential_type_not_deprecated_allows_active_types` (374-386, includes `ZEPHYR_SCALE`, `GIT`, `JIRA`, `XRAY` as allowed cases). **Once the mechanism itself is deleted per this task, this entire block plus the imports at lines 25 and 27 must be removed** — the "generic" parametrized tests only ever had ZephyrSquad as their single real-world case, so they degenerate to no-ops.
- `tests/codemie/rest_api/routers/test_user_settings.py:497,565-609` — `ZEPHYR_SQUAD_DEPRECATION_MESSAGE` constant, `test_create_user_setting_zephyr_squad_blocked` (565-582), `test_update_user_setting_zephyr_squad_blocked` (588-609) — delete both tests + constant
- `tests/codemie/rest_api/routers/test_project_settings.py:216,438-487` — same pattern: `test_create_project_setting_zephyr_squad_blocked` (438-458), `test_update_project_setting_zephyr_squad_blocked` (463-487) — delete both + constant (216)
- `tests/codemie/service/search_and_rerank/test_search_and_rerank_tool.py:537-538` — `test_tokenize_tool_name_with_camel_case` uses `"ZephyrSquad"` purely as a camelCase tokenization sample string (`['zephyr','squad']`); no functional dependency, cosmetic optional cleanup only

**Confirmed NOT in scope (Zephyr Scale, unrelated, do not touch):**
- `tests/codemie/service/settings/test_settings_tester.py:217-254` — `test_test_zephyr_success`/`test_test_zephyr_fail` use `CredentialTypes.ZEPHYR_SCALE` and `_test_zephyr()`, not ZephyrSquad
- `tests/codemie/service/tools/test_toolkit_settings_service.py:87` — fixture uses `Tool(name="ZephyrScale", ...)`, unrelated
- `tests/codemie_tools/qa/zephyr/test_zephyr_models.py`, `tests/codemie_tools/qa/zephyr/test_generic_tool.py` — test the surviving Zephyr Scale integration

### Testing Framework and Patterns

- pytest `^8.3.1` (`pyproject.toml:181`), plus pytest-asyncio 0.23.7, pytest-cov 5.0.0, pytest-mock 3.14.0, pytest-httpx 0.35.0. Async router tests use `@pytest.mark.anyio`.
- Patterns observed in the affected tests: `@pytest.mark.parametrize("credential_type", list(DEPRECATED_CREDENTIAL_TYPES))` (dynamic parametrization over the registry dict keys); `@patch(...)` + `MagicMock()` for tool/service mocking; `pytest.raises(ExtendedHTTPException)` with assertions on `.code`/`.message`/`.details`/`.help` for the 410 deprecation path; `ASGITransport`/`AsyncClient` FastAPI integration-test pattern for router tests.
- No conftest.py fixtures or JSON/YAML fixture files reference Zephyr anywhere.

### Coverage Gaps

- After removing the 4 router-level "zephyr_squad_blocked" 410 tests, verify there is still generic coverage for rejecting an unrecognized/invalid `credential_type` string (a 422/400 path) so the "reject bad input" behavior isn't silently lost.
- After removing `CredentialTypes.ZEPHYR_SQUAD`, any remaining reference to it in test files (e.g. `test_settings_request_validator.py:359`) will fail to collect at import time — must be removed in the same change, not left dangling.
- No other test file asserts overall tool/catalog counts beyond `test_qa_toolkit.py` — a final catalog grep after code removal is worth doing to confirm no other count-based assertion breaks.

---

## 5. Configuration and Environment

### Environment Variables

No OS-level environment variables (`ZEPHYR_SQUAD_API_TOKEN`-style) exist in `src/` or `deploy-templates/`. ZephyrSquad credentials are stored as DB-backed settings, referenced via dict-key constants only:
- `ZEPHYR_SQUAD_ACCOUNT_ID` — `src/codemie/service/settings/settings.py:110`
- `ZEPHYR_SQUAD_ACCESS_KEY` — `src/codemie/service/settings/settings.py:111`, also `:146`
- `ZEPHYR_SQUAD_SECRET_KEY` — `src/codemie/service/settings/settings.py:112`, also `:147`

No `.env.example` file exists at repo root or anywhere in the repo.

### Configuration Files

- `config/customer/customer-config.yaml:389-391` — a generic "zephyr" URL placeholder text block that may be shared by Zephyr Scale/Squad UI hints — **verify whether this key is Scale-only or shared before touching**, do not delete blind.
- `config/index-dumps/codemie-codemie-onboarding.json` — FAQ/onboarding vector-index dump mentioning "Zephyr Scale"/"Zephyr Cloud" in sample Q&A text; not functional config, optional cleanup only.

### Feature Flags and Deployment Concerns

- `deprecated=True` on `ZEPHYR_SQUAD_TOOL` metadata (`src/codemie_tools/qa/zephyr_squad/tools_vars.py`) — this is the flag the task wants removed entirely along with the tool, not converted to a toggle.
- No Helm/K8s manifest, values file, or env-var declaration in `deploy-templates/` references Zephyr/ZephyrSquad — deployment layer needs no changes.
- No dedicated Alembic migration exists solely for adding the ZephyrSquad credential type as a distinct schema object (credential types share the `credentialtypes` enum type). Migrations found via a broad Zephyr grep — `e03e516e00da_add_xray_credential_type.py`, `300e51656562_add_oauth_credential_types.py`, `8b2c1a4d5e6f_add_ms_teams_credential_type.py`, etc. — matched only because they each enumerate the full `credentialtypes` enum value list via `op.sync_enum_values(...)`, not because they add Zephyr-specific logic.
- `README.md:318` lists "X-ray, Zephyr Scale, Zephyr Squad" under the QA toolkit description — update as a documentation-only change.
- `zephyr-python-api = "^0.1.0"` in `pyproject.toml:141` — usage unconfirmed (see Section 2, Integration Points); verify before removing.

---

## 6. Risk Indicators

- **DB native enum, not a plain string column — the "no data migration" assumption may be technically infeasible to satisfy at the DB-schema level.** The `credential_type` column (`src/codemie/rest_api/models/settings.py:254`) is a genuine Postgres `ENUM` type named `credentialtypes` (see `postgresql.ENUM(..., name='credentialtypes', create_type=False)` in `074d06e75b25_create_settings.py:68-97`), evolved via `alembic_postgresql_enum`'s `op.sync_enum_values(...)`. Postgres cannot drop an enum label that is still referenced by existing rows without a data rewrite. The prior deprecation spec explicitly cited this as the reason it kept `CredentialTypes.ZEPHYR_SQUAD` in the Python enum (`docs/superpowers/tasks/2026-07-31-deprecate-zephyrsquad/technical-analysis.md:169`). **This needs an explicit decision in the plan**: (a) remove the Python-side tool/integration entirely but leave `CredentialTypes.ZEPHYR_SQUAD` as an inert enum member (satisfies "no migration" but leaves a dead enum value/no full removal), or (b) confirm via a DB query that zero rows currently have `credential_type = 'ZephyrSquad'` (product owner says "no prod usage exists") and then safely run `op.sync_enum_values(...)` to drop the label as a genuinely no-op, low-risk migration. Given the PO has confirmed no prod usage, option (b) is very likely safe, but it should be verified with a real query against the target environment(s) before writing the migration, and the migration itself is unavoidable if full DB-level removal is required — it just carries near-zero data risk given zero usage.
- `settings_request_validator.py:30` imports `CredentialTypes` for other purposes beyond `DEPRECATED_CREDENTIAL_TYPES` — do not blindly strip this import when removing the deprecation mechanism; verify remaining usages in the file first.
- `config/customer/customer-config.yaml:389-391` — shared "zephyr" URL placeholder text of unconfirmed scope (Scale vs Squad vs shared) — needs a targeted check before edit.
- `pyproject.toml:141` `zephyr-python-api` dependency — usage is unconfirmed; removing it blind could silently break Zephyr Scale if the import name differs from the package name. Requires an explicit grep of `zephyr_squad/api_wrapper.py` and `qa/zephyr/` before deletion.
- `test_validate_credential_type_not_deprecated_rejects_registered_types` and `_allows_active_types` in `test_settings_request_validator.py` are written generically (parametrized over the registry / a hardcoded allow-list) but in practice only ever exercised ZephyrSquad as their one real deprecated case — deleting the mechanism guts this entire test block; confirm no other future-planned credential type currently relies on this scaffolding before deleting it outright (research found none — `DEPRECATED_CREDENTIAL_TYPES` has exactly one entry, `ZEPHYR_SQUAD`).
- `ToolMetadata.deprecated` / `Tool.deprecated` / `Tool.from_metadata` deprecated-flag propagation (`src/codemie_tools/base/models.py:59,158,184`) is confirmed to have **zero other consumers** (`deprecated=True` appears nowhere else in the codebase) — safe to remove the field entirely, not just its ZephyrSquad usage.
- `get_zephyr_squad_creds()` (`settings.py:1654-1670`) is confirmed dead code with no callers — safe to delete without further blast-radius analysis.
- No guide in `.ai-run/guides/` documents the credential-type deprecation pattern or tool catalog conventions — conventions had to be derived entirely from source and from the prior task's own planning docs.
- `search_and_rerank/tool.py:255,257,268` and `test_search_and_rerank_tool.py:537-538` reference the string "ZephyrSquad" purely as example/sample text with no functional coupling — low risk, optional cosmetic cleanup, should not block the removal PR.

---

## 7. Summary for Complexity Assessment

This is a moderate-breadth, low-technical-novelty removal task spanning six layers: tool implementation (`codemie_tools/qa/zephyr_squad/` — 4 files, ~375 lines, delete as a unit), tool catalog registration (`codemie_tools/qa/toolkit.py`), the shared `CredentialTypes` enum and its `deprecated`-flag mechanism (`codemie_tools/base/models.py`), the settings/credential service layer (`settings.py`, `settings_tester.py`), the API validation layer (`settings_request_validator.py`'s `DEPRECATED_CREDENTIAL_TYPES` + `validate_credential_type_not_deprecated`, called from 4 sites across 2 routers), and the persistence layer (a native Postgres ENUM column). Estimated file-change surface is roughly 15-20 source/test files plus one Alembic migration, all editing existing, well-understood code paths rather than introducing new patterns — every mechanism being removed (the `deprecated` field, the `DEPRECATED_CREDENTIAL_TYPES` registry) was confirmed to have ZephyrSquad as its sole real-world user, so removal is clean deletion rather than careful untangling of shared logic. No other integration or credential type depends on either mechanism.

Test coverage posture is good-to-excellent for the code being removed: dedicated unit tests exist for the tool and API wrapper, the toolkit registration, the deprecation registry, and both routers' 410-blocking behavior — all of which map directly onto deletion targets, reducing the risk of missing a code path. The one meaningful gap is post-removal verification: confirming a generic invalid-credential-type test still exists after the specific ZephyrSquad 410 tests are deleted, and updating count-based assertions in `test_qa_toolkit.py`.

The single genuine risk factor that should weigh on complexity/risk scoring is the DB-level native enum: `credential_type` is a real Postgres `ENUM` type (not a plain string column), and Postgres cannot drop an enum label with a data rewrite risk if rows still reference it. The product owner's "no data migration" instruction is almost certainly satisfiable because "no prod usage exists," but the plan should explicitly decide whether to (a) fully drop the DB enum label via a low-risk `sync_enum_values` migration after confirming zero rows reference it, or (b) leave `ZEPHYR_SQUAD` as an inert Python/DB enum value indefinitely. This decision affects whether the task needs exactly one small, verifiably-safe Alembic migration or none at all, and should be surfaced explicitly in the spec/plan rather than assumed.
