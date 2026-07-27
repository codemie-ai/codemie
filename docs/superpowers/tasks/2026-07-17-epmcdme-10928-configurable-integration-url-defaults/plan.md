# Configurable Integration URL Defaults Implementation Plan

**Goal:** Make URL defaults, placeholder hint text, and select non-URL fields of every integration `CodeMieToolConfig` overridable per deployment via `tool_defaults` in `customer-config.yaml`, so the UI can pre-fill the correct instance URL and show deployment-relevant hints without code changes.

**Architecture:** Each model declares `TOOL_NAME: ClassVar[str]` and a module-level `_tool_default = customer_config.get_tool_default` alias. Fields read their defaults at class-definition time from YAML. The `tool_defaults` section in `config/customer/customer-config.yaml` ships pre-populated with placeholder hint text; operators uncomment and fill in URL defaults or non-URL field overrides as needed.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, monkeypatch

## Global Constraints

- Call `_tool_default` at **class-definition time** (i.e., inside the `Field(...)` argument), never lazily.
- YAML key naming must match the Python field name (`url`, `base_url`, `smtp_url`). Placeholder keys append `_placeholder` suffix.
- `get_tool_default` return type is `Optional[Any]` — supports `bool` and `str` values from YAML.
- `or ""` / `or False` fallbacks ensure typed fields never receive `None`. Exception: `email.auth_type` has no code fallback — the default value must be present in YAML.
- Do not change `ZephyrSquadConfig` (vendor SaaS constant).
- Per-tool VCS/ADO models (GitlabConfig, GithubConfig, AzureDevOpsGitConfig, work_item, wiki, test_plan) keep static placeholders; generic UI configs cover those credential entry needs.
- Tests: `test_*_placeholder_default` for schema placeholder assertions; `test_*_tool_default_override` for monkeypatch override tests.

---

## Task 1: `CustomerConfig` — add `tool_defaults` + `get_tool_default`

**Files:**
- Modify: `src/codemie/configs/customer_config.py`
- Add `tool_defaults: Dict[str, Dict[str, Any]]` field and `get_tool_default(tool, field) -> Optional[Any]` method.

- [x] Add `tool_defaults` dict field (loaded raw from YAML)
- [x] Add `get_tool_default` returning `(tool_defaults.get(tool) or {}).get(field)` (null-safe for YAML entries with all keys commented out)
- [x] Change return type annotation to `Optional[Any]`

---

## Task 2: `customer-config.yaml` — `tool_defaults` section

**Files:**
- Modify: `config/customer/customer-config.yaml`

- [x] Add `tool_defaults` section pre-populated with `*_placeholder` values for all supported tools
- [x] Comment all URL default and non-URL field keys for quick operator use
- [x] YAML key names match Python field names (see integration inventory in spec)

---

## Task 3: ZephyrConfig — bare annotation → Field

**Files:**
- Modify: `src/codemie_tools/qa/zephyr/models.py`
- Create: `tests/codemie_tools/qa/zephyr/test_zephyr_models.py`

- [x] Add `TOOL_NAME`, `_tool_default` alias
- [x] Convert `url: str` to `Field(default=_tool_default(TOOL_NAME, "url") or "", ..., json_schema_extra={"placeholder": _tool_default(..., "url_placeholder")})`
- [x] Create tests: placeholder default, monkeypatch override, valid config

---

## Task 4: SharePointConfig — new CodeMieToolConfig model

**Files:**
- Create: `src/codemie_tools/data_management/sharepoint/__init__.py`
- Create: `src/codemie_tools/data_management/sharepoint/models.py`
- Create: `tests/codemie_tools/data_management/sharepoint/test_models.py`

- [x] Create `SharePointConfig(CodeMieToolConfig)` with `TOOL_NAME = "sharepoint"`, `credential_type`, `url` field
- [x] Create tests: importable, placeholder in schema, credential type, url default

---

## Task 5: Project management integrations (Jira, Confluence, XWiki)

**Files:**
- Modify: `src/codemie_tools/core/project_management/{jira,confluence,xwiki}/models.py`
- Modify/create: test files for each

- [x] Add `TOOL_NAME`, `_tool_default` alias to each
- [x] Update `url` field: `default=_tool_default(TOOL_NAME, "url") or ""`, `placeholder=_tool_default(TOOL_NAME, "url_placeholder")`
- [x] Jira, Confluence: make `cloud` field default configurable: `default=_tool_default(TOOL_NAME, "cloud") or False`
- [x] XWiki: make `use_bearer` configurable: `default=_tool_default(TOOL_NAME, "use_bearer") or False`
- [x] Add placeholder default tests and tool_default override tests for each

---

## Task 6: GenericGitConfig — new generic UI credential config

**Files:**
- Create: `src/codemie_tools/core/vcs/git/__init__.py`
- Create: `src/codemie_tools/core/vcs/git/models.py`
- Create: `tests/codemie_tools/core/vcs/git/__init__.py`
- Create: `tests/codemie_tools/core/vcs/git/test_models.py`

- [x] Create `GenericGitConfig(CodeMieToolConfig)` with `TOOL_NAME = "git"`
- [x] `auth_type: str = Field(default=_tool_default(TOOL_NAME, "auth_type") or "pat")`
- [x] `url: str = RequiredField(default=_tool_default(TOOL_NAME, "url") or "", placeholder=_tool_default(TOOL_NAME, "url_placeholder"))`
- [x] `token: str = RequiredField(sensitive=True)`
- [x] Tests: valid config, explicit auth_type, url placeholder from tool_defaults, url default from tool_defaults, auth_type from tool_defaults

---

## Task 7: GenericAzureDevOpsConfig — new generic UI credential config

**Files:**
- Create: `src/codemie_tools/azure_devops/generic/__init__.py`
- Create: `src/codemie_tools/azure_devops/generic/models.py`
- Create: `tests/codemie_tools/azure_devops/generic/__init__.py`
- Create: `tests/codemie_tools/azure_devops/generic/test_models.py`

- [x] Create `GenericAzureDevOpsConfig(CodeMieToolConfig)` with `TOOL_NAME = "azuredevops"`
- [x] `url: str = RequiredField(default=_tool_default(TOOL_NAME, "url") or "", description="Azure DevOps organization URL", placeholder=_tool_default(TOOL_NAME, "url_placeholder"))`
- [x] `token: str = RequiredField(sensitive=True)`
- [x] Tests: valid config, url placeholder from tool_defaults, url default from tool_defaults

---

## Task 8: Infrastructure + access integrations (Keycloak, Elastic, Kubernetes)

**Files:**
- Modify: `src/codemie_tools/access_management/keycloak/models.py`
- Modify: `src/codemie_tools/data_management/elastic/models.py`
- Modify: `src/codemie_tools/cloud/kubernetes/models.py`
- Modify/create: test files

- [x] Add `TOOL_NAME`, `_tool_default` to each
- [x] Keycloak: YAML key `base_url` / `base_url_placeholder` (matches `base_url` field name)
- [x] Elastic, Kubernetes: YAML key `url` / `url_placeholder`
- [x] Add tests

---

## Task 9: QA + ITSM + monitoring (Xray, ServiceNow, ReportPortal, SonarConfig)

**Files:**
- Modify: `src/codemie_tools/qa/xray/models.py`
- Modify: `src/codemie_tools/itsm/servicenow/models.py`
- Modify: `src/codemie_tools/report_portal/models.py`
- Modify: `src/codemie_tools/code/models.py`
- Modify/create: test files

- [x] Add `TOOL_NAME`, `_tool_default` to each
- [x] Xray: YAML key `base_url` / `base_url_placeholder` (matches `base_url` field name)
- [x] ServiceNow, ReportPortal, SonarQube: YAML key `url` / `url_placeholder`
- [x] Add tests

---

## Task 10: Email integration (SMTP + OAuth)

**Files:**
- Modify: `src/codemie_tools/notification/email/models.py`
- Create: `tests/codemie_tools/notification/email/test_models.py`

- [x] Add `TOOL_NAME = "email"`, `_tool_default`
- [x] `url` (SMTP): `default=_tool_default(TOOL_NAME, "smtp_url") or ""`, `placeholder=_tool_default(TOOL_NAME, "smtp_url_placeholder")`
- [x] `auth_type`: `default=EmailAuthType(_tool_default(TOOL_NAME, "auth_type"))` — no code fallback; default value `"basic"` is set in `customer-config.yaml`
- [x] `oauth_authority`: `default=_tool_default(TOOL_NAME, "oauth_authority")`
- [x] `oauth_scope`: `default=_tool_default(TOOL_NAME, "oauth_scope")`
- [x] Add tests for each field: placeholder, default value, override

---

## Task 11: SQLConfig — dialect from YAML

**Files:**
- Modify: `src/codemie_tools/data_management/sql/models.py`
- Modify: `tests/codemie_tools/data_management/sql/test_sql_tools.py`

- [x] Add `TOOL_NAME = "sql"`, `_tool_default` alias, `customer_config` import
- [x] Convert `dialect: str` (bare required) to `RequiredField(default=_tool_default(TOOL_NAME, "dialect") or "")`
- [x] YAML entry `sql: { dialect: "mysql" }` ships a default dialect; runtime validator still rejects empty dialect
- [x] Add `test_dialect_from_tool_defaults` — monkeypatch override, assert `get_tool_default("sql", "dialect") == "postgres"`
