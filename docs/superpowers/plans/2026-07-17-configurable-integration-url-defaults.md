# Configurable Integration URL Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the URL placeholder and default values of every integration `CodeMieToolConfig` overridable per deployment via environment variables, with no code changes required at deploy time.

**Architecture:** A single `config_url_or_fallback(config_attr, fallback)` utility in `src/codemie_tools/base/models.py` is called at class-definition time inside each model's `Field(...)` call. Its value is baked into field metadata before the LRU-cached `get_available_tools_configs_info()` runs at startup. New `DEFAULT_*_TOOL_URL` fields in `Config(BaseSettings)` are the canonical env-var hooks; pydantic-settings reads them automatically from environment or `.env`.

**Tech Stack:** Python 3.12, Pydantic v2, pydantic-settings, pytest, monkeypatch

## Global Constraints

- Call `config_url_or_fallback` at **class-definition time** (i.e., inside the `Field(...)` argument), never lazily, or the LRU cache at startup sees the un-overridden value.
- The `_TOOL_URL` suffix on every new env var distinguishes them from infrastructure vars (`ELASTIC_URL`, `KUBERNETES_API_URL`, etc.).
- Type A fields (`RequiredField`, `default=""`): override only `placeholder` in `json_schema_extra`.
- Type B fields (`Field(default="https://...")`, real runtime default): override both `default=` and `json_schema_extra.placeholder` with the same call.
- `ZephyrConfig.url` was a bare annotation (no `Field`). The fix preserves pydantic-required semantics — use `Field(...)` with no `default`, not `RequiredField`.
- Do not change `ZephyrSquadConfig` (vendor SaaS constant) or `SQLConfig` (host+port, no URL field).
- Tests: every change gets two assertions — (1) schema/fallback matches hardcoded default when env var is absent, (2) `config_url_or_fallback` returns override when config attr is patched.

---

## Task 1: Utility function + Config integration-URL fields

**Files:**
- Modify: `src/codemie_tools/base/models.py`
- Modify: `src/codemie/configs/config.py`
- Create: `tests/codemie_tools/base/test_config_url_or_fallback.py`

**Interfaces:**
- Produces: `config_url_or_fallback(config_attr: str, fallback: str) -> str` — importable from `codemie_tools.base.models`
- Produces: `Config.DEFAULT_JIRA_TOOL_URL`, `Config.DEFAULT_CONFLUENCE_TOOL_URL`, `Config.DEFAULT_GITLAB_TOOL_URL`, `Config.DEFAULT_GITHUB_TOOL_URL`, `Config.DEFAULT_AZURE_DEVOPS_TOOL_URL`, `Config.DEFAULT_KEYCLOAK_TOOL_URL`, `Config.DEFAULT_ELASTIC_TOOL_URL`, `Config.DEFAULT_EMAIL_SMTP_TOOL_URL`, `Config.DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL`, `Config.DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL`, `Config.DEFAULT_XRAY_TOOL_URL`, `Config.DEFAULT_ZEPHYR_TOOL_URL`, `Config.DEFAULT_SERVICENOW_TOOL_URL`, `Config.DEFAULT_REPORTPORTAL_TOOL_URL`, `Config.DEFAULT_KUBERNETES_TOOL_URL`, `Config.DEFAULT_SONAR_TOOL_URL`, `Config.DEFAULT_XWIKI_TOOL_URL`, `Config.DEFAULT_SHAREPOINT_TOOL_URL`

- [ ] **Step 1: Write failing tests**

```python
# tests/codemie_tools/base/test_config_url_or_fallback.py
import pytest
from codemie_tools.base.models import config_url_or_fallback


# Exhaustive parametrized test: every DEFAULT_*_TOOL_URL override is verified.
# Add new env vars here whenever a new integration is added.
ALL_TOOL_URL_ATTRS = [
    ("DEFAULT_JIRA_TOOL_URL", "https://jira.example.com/"),
    ("DEFAULT_CONFLUENCE_TOOL_URL", "https://confluence.example.com/"),
    ("DEFAULT_GITLAB_TOOL_URL", "https://gitlab.example.com"),
    ("DEFAULT_GITHUB_TOOL_URL", "https://api.github.com"),
    ("DEFAULT_AZURE_DEVOPS_TOOL_URL", "https://dev.azure.com"),
    ("DEFAULT_KEYCLOAK_TOOL_URL", "https://keycloak.example.com"),
    ("DEFAULT_ELASTIC_TOOL_URL", "https://your-elastic-instance.com"),
    ("DEFAULT_EMAIL_SMTP_TOOL_URL", "smtp.gmail.com:587"),
    ("DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL", "https://login.microsoftonline.com"),
    ("DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL", "https://outlook.office365.com/.default"),
    ("DEFAULT_XRAY_TOOL_URL", "https://xray.cloud.getxray.app"),
    ("DEFAULT_ZEPHYR_TOOL_URL", "https://api.zephyrscale.smartbear.com/v2"),
    ("DEFAULT_SERVICENOW_TOOL_URL", "https://your-instance.service-now.com"),
    ("DEFAULT_REPORTPORTAL_TOOL_URL", "https://reportportal.example.com"),
    ("DEFAULT_KUBERNETES_TOOL_URL", "https://kubernetes.default.svc"),
    ("DEFAULT_SONAR_TOOL_URL", "https://sonarqube.example.com"),
    ("DEFAULT_XWIKI_TOOL_URL", "https://wiki.example.com"),
    ("DEFAULT_SHAREPOINT_TOOL_URL", "https://yourtenant.sharepoint.com"),
]


@pytest.mark.parametrize("attr,expected_default", ALL_TOOL_URL_ATTRS)
def test_config_has_expected_default(attr, expected_default):
    from codemie.configs import config as app_config
    assert getattr(app_config, attr) == expected_default


@pytest.mark.parametrize("attr,fallback", ALL_TOOL_URL_ATTRS)
def test_returns_override_for_every_attr(attr, fallback, monkeypatch):
    from codemie.configs import config as app_config
    override = "https://company.internal/override"
    monkeypatch.setattr(app_config, attr, override)
    assert config_url_or_fallback(attr, fallback) == override


@pytest.mark.parametrize("attr,fallback", ALL_TOOL_URL_ATTRS)
def test_returns_fallback_when_attr_empty(attr, fallback, monkeypatch):
    from codemie.configs import config as app_config
    monkeypatch.setattr(app_config, attr, "")
    assert config_url_or_fallback(attr, fallback) == fallback


def test_returns_fallback_when_attr_missing():
    assert config_url_or_fallback("NON_EXISTENT_TOOL_URL_ATTR", "https://fallback.example.com/") == "https://fallback.example.com/"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/base/test_config_url_or_fallback.py -v
```
Expected: `ImportError` or `AttributeError` — `config_url_or_fallback` does not exist yet; all 55 parametrized cases plus the standalone case will fail.

- [ ] **Step 3: Add `config_url_or_fallback` to `src/codemie_tools/base/models.py`**

Add immediately after the `RequiredField` function (before `class ToolMetadata`):

```python
def config_url_or_fallback(config_attr: str, fallback: str) -> str:
    from codemie.configs import config as _app_config
    return getattr(_app_config, config_attr, None) or fallback
```

- [ ] **Step 4: Add new `DEFAULT_*_TOOL_URL` fields to `src/codemie/configs/config.py`**

Add a new section just before the `model_config = SettingsConfigDict(...)` line (currently line 752):

```python
    # ===========================================
    # Integration Tool Default URLs
    # Per-deployment placeholder/default for each integration config.
    # Set to override the hardcoded fallback at deploy time.
    # ===========================================
    DEFAULT_JIRA_TOOL_URL: str = "https://jira.example.com/"
    DEFAULT_CONFLUENCE_TOOL_URL: str = "https://confluence.example.com/"
    DEFAULT_GITLAB_TOOL_URL: str = "https://gitlab.example.com"
    DEFAULT_GITHUB_TOOL_URL: str = "https://api.github.com"
    DEFAULT_AZURE_DEVOPS_TOOL_URL: str = "https://dev.azure.com"
    DEFAULT_KEYCLOAK_TOOL_URL: str = "https://keycloak.example.com"
    DEFAULT_ELASTIC_TOOL_URL: str = "https://your-elastic-instance.com"
    DEFAULT_EMAIL_SMTP_TOOL_URL: str = "smtp.gmail.com:587"
    DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL: str = "https://login.microsoftonline.com"
    DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL: str = "https://outlook.office365.com/.default"
    DEFAULT_XRAY_TOOL_URL: str = "https://xray.cloud.getxray.app"
    DEFAULT_ZEPHYR_TOOL_URL: str = "https://api.zephyrscale.smartbear.com/v2"
    DEFAULT_SERVICENOW_TOOL_URL: str = "https://your-instance.service-now.com"
    DEFAULT_REPORTPORTAL_TOOL_URL: str = "https://reportportal.example.com"
    DEFAULT_KUBERNETES_TOOL_URL: str = "https://kubernetes.default.svc"
    DEFAULT_SONAR_TOOL_URL: str = "https://sonarqube.example.com"
    DEFAULT_XWIKI_TOOL_URL: str = "https://wiki.example.com"
    DEFAULT_SHAREPOINT_TOOL_URL: str = "https://yourtenant.sharepoint.com"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/base/test_config_url_or_fallback.py -v
```
Expected: 55 tests PASS (18 × 3 parametrized variants + 1 standalone missing-attr test).

- [ ] **Step 6: Commit**

```bash
git add src/codemie_tools/base/models.py src/codemie/configs/config.py tests/codemie_tools/base/test_config_url_or_fallback.py
git commit -m "feat(EPMCDME-10928): add config_url_or_fallback utility and DEFAULT_*_TOOL_URL config fields"
```

---

## Task 2: ZephyrConfig — bare annotation → Field with placeholder

**Files:**
- Modify: `src/codemie_tools/qa/zephyr/models.py`
- Create: `tests/codemie_tools/qa/zephyr/test_zephyr_models.py`

**Interfaces:**
- Consumes: `config_url_or_fallback` from Task 1
- ZephyrConfig.url remains pydantic-required (no `default` added); only adds `json_schema_extra` with placeholder

- [ ] **Step 1: Write failing test**

```python
# tests/codemie_tools/qa/zephyr/test_zephyr_models.py
import pytest
from pydantic import ValidationError
from codemie_tools.qa.zephyr.models import ZephyrConfig


def test_zephyr_url_remains_required():
    """url is pydantic-required: instantiation without it must raise ValidationError."""
    with pytest.raises(ValidationError):
        ZephyrConfig(token="mytoken")


def test_zephyr_url_placeholder_in_schema():
    schema = ZephyrConfig.model_json_schema()
    assert schema["properties"]["url"].get("placeholder") == "https://api.zephyrscale.smartbear.com/v2"


def test_zephyr_valid_config():
    config = ZephyrConfig(url="https://api.zephyrscale.smartbear.com/v2", token="mytoken")
    assert config.url == "https://api.zephyrscale.smartbear.com/v2"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/qa/zephyr/test_zephyr_models.py -v
```
Expected: `test_zephyr_url_placeholder_in_schema` FAIL (no `placeholder` key in schema currently).

- [ ] **Step 3: Update `src/codemie_tools/qa/zephyr/models.py`**

Add `config_url_or_fallback` to the import and convert the bare annotation:

```python
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, config_url_or_fallback

class ZephyrConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.ZEPHYR_SCALE, exclude=True, frozen=True)
    url: str = Field(
        description="Zephyr Scale API URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_ZEPHYR_TOOL_URL", "https://api.zephyrscale.smartbear.com/v2")},
    )
    token: str
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/qa/zephyr/test_zephyr_models.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/qa/zephyr/models.py tests/codemie_tools/qa/zephyr/test_zephyr_models.py
git commit -m "feat(EPMCDME-10928): add placeholder to ZephyrConfig.url without changing required semantics"
```

---

## Task 3: SharePointConfig — new CodeMieToolConfig model

> **Metadata-only model.** `CodeMieToolConfig` subclasses are consumed by `toolkit_provider.py:get_available_tools_configs_info()`, which returns their JSON schemas to the `/tools/configs` endpoint. This is how the UI knows what fields to show in the integration setup screen. The existing `SharePointCredentials` (`src/codemie/rest_api/models/settings.py`) handles actual runtime auth — it is a plain `BaseModel` unrelated to this class. `SharePointConfig` adds SharePoint to the `/tools/configs` schema surface alongside all other integrations; it does not replace `SharePointCredentials`.

**Files:**
- Create: `src/codemie_tools/data_management/sharepoint/__init__.py`
- Create: `src/codemie_tools/data_management/sharepoint/models.py`
- Create: `tests/codemie_tools/data_management/sharepoint/__init__.py`
- Create: `tests/codemie_tools/data_management/sharepoint/test_models.py`

**Interfaces:**
- Consumes: `CodeMieToolConfig`, `RequiredField`, `CredentialTypes`, `config_url_or_fallback` from Task 1
- Produces: `SharePointConfig(CodeMieToolConfig)` with `credential_type=CredentialTypes.SHAREPOINT` and `url: str = RequiredField(...)`

- [ ] **Step 1: Write failing test**

```python
# tests/codemie_tools/data_management/sharepoint/test_models.py
import pytest
from pydantic import ValidationError


def test_sharepoint_config_is_importable():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig
    assert SharePointConfig is not None


def test_sharepoint_url_placeholder_in_schema():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig
    schema = SharePointConfig.model_json_schema()
    assert schema["properties"]["url"].get("placeholder") == "https://yourtenant.sharepoint.com"
    assert schema["properties"]["url"].get("required_at_runtime") is True


def test_sharepoint_credential_type():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig
    from codemie_tools.base.models import CredentialTypes
    config = SharePointConfig(url="https://contoso.sharepoint.com")
    assert config.credential_type == CredentialTypes.SHAREPOINT


def test_sharepoint_url_is_required_at_runtime():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig
    # RequiredField sets default="" so pydantic allows empty, app enforces required_at_runtime
    config = SharePointConfig()
    assert config.url == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/data_management/sharepoint/test_models.py -v
```
Expected: `ModuleNotFoundError` — module does not exist yet.

- [ ] **Step 3: Create `__init__.py` files**

```python
# src/codemie_tools/data_management/sharepoint/__init__.py
# (empty file)
```

```python
# tests/codemie_tools/data_management/sharepoint/__init__.py
# (empty file)
```

- [ ] **Step 4: Create `src/codemie_tools/data_management/sharepoint/models.py`**

```python
from pydantic import Field

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField, config_url_or_fallback


class SharePointConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.SHAREPOINT, exclude=True, frozen=True)
    url: str = RequiredField(
        description="SharePoint tenant root URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_SHAREPOINT_TOOL_URL", "https://yourtenant.sharepoint.com")},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/data_management/sharepoint/test_models.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie_tools/data_management/sharepoint/ tests/codemie_tools/data_management/sharepoint/
git commit -m "feat(EPMCDME-10928): add SharePointConfig CodeMieToolConfig for /tools/configs surfacing"
```

---

## Task 4: Project management integrations (Jira, Confluence, XWiki)

All three are type A: `Field(default="", json_schema_extra={"placeholder": "..."})`. Only `placeholder` value changes.

**Files:**
- Modify: `src/codemie_tools/core/project_management/jira/models.py`
- Modify: `src/codemie_tools/core/project_management/confluence/models.py`
- Modify: `src/codemie_tools/core/project_management/xwiki/models.py`
- Modify: `tests/codemie_tools/core/project_management/jira/test_models.py`
- Modify: `tests/codemie_tools/core/project_management/confluence/test_models.py`
- Create or modify: `tests/codemie_tools/core/project_management/xwiki/test_models.py`

**Interfaces:**
- Consumes: `config_url_or_fallback` from Task 1

- [ ] **Step 1: Write failing tests — add to existing test files and create XWiki test**

Add to `tests/codemie_tools/core/project_management/jira/test_models.py`:
```python
def test_jira_url_placeholder_default():
    schema = JiraConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://jira.example.com/"

def test_jira_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_JIRA_TOOL_URL", "https://my-jira.company.com/")
    assert config_url_or_fallback("DEFAULT_JIRA_TOOL_URL", "https://jira.example.com/") == "https://my-jira.company.com/"
```

Add identical pattern to `tests/codemie_tools/core/project_management/confluence/test_models.py`:
```python
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig

def test_confluence_url_placeholder_default():
    schema = ConfluenceConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://confluence.example.com/"

def test_confluence_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_CONFLUENCE_TOOL_URL", "https://confluence.company.com/")
    assert config_url_or_fallback("DEFAULT_CONFLUENCE_TOOL_URL", "https://confluence.example.com/") == "https://confluence.company.com/"
```

Create `tests/codemie_tools/core/project_management/xwiki/test_models.py`:
```python
import pytest
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


def test_xwiki_url_placeholder_default():
    schema = XWikiConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://wiki.example.com"

def test_xwiki_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_XWIKI_TOOL_URL", "https://wiki.company.com")
    assert config_url_or_fallback("DEFAULT_XWIKI_TOOL_URL", "https://wiki.example.com") == "https://wiki.company.com"
```

- [ ] **Step 2: Run tests to verify placeholder tests fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/ -k "placeholder" -v
```
Expected: FAIL — `placeholder` currently a hardcoded string, not the function call (test passes on value, not wiring — but the override test will still be useful for future verification).

> Note: `test_jira_url_placeholder_default` and `test_confluence_url_placeholder_default` will PASS already since the hardcoded value equals the new fallback. Only `test_xwiki_url_placeholder_default` may fail if `placeholder` key is absent. Run to confirm status before implementing.

- [ ] **Step 3: Update Jira model**

In `src/codemie_tools/core/project_management/jira/models.py`, add `config_url_or_fallback` to import and update the url field:

```python
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, FileConfigMixin, config_url_or_fallback

# url field becomes:
    url: str = Field(
        default="",
        description="URL to your Jira instance, e.g. https://jira.example.com/ or https://jira.example.com/jira/",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_JIRA_TOOL_URL", "https://jira.example.com/")},
    )
```

- [ ] **Step 4: Update Confluence model**

In `src/codemie_tools/core/project_management/confluence/models.py`:

```python
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, config_url_or_fallback

# url field becomes:
    url: str = Field(
        default="",
        description="URL to your Confluence instance, e.g. http://confluence.example.com/",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_CONFLUENCE_TOOL_URL", "https://confluence.example.com/")},
    )
```

- [ ] **Step 5: Update XWiki model**

In `src/codemie_tools/core/project_management/xwiki/models.py`, check existing import and add `config_url_or_fallback`, then update the url field placeholder.

- [ ] **Step 6: Run tests to verify all pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/ -v
```
Expected: all tests PASS including the new placeholder tests.

- [ ] **Step 7: Commit**

```bash
git add src/codemie_tools/core/project_management/jira/models.py \
        src/codemie_tools/core/project_management/confluence/models.py \
        src/codemie_tools/core/project_management/xwiki/models.py \
        tests/codemie_tools/core/project_management/
git commit -m "feat(EPMCDME-10928): make Jira, Confluence, XWiki URL placeholders env-var configurable"
```

---

## Task 5: VCS integrations — GitLab (type A) and GitHub (type B)

GitHub is type B: both `default=` and `placeholder` use `config_url_or_fallback`.

**Files:**
- Modify: `src/codemie_tools/core/vcs/gitlab/models.py`
- Modify: `src/codemie_tools/core/vcs/github/models.py`
- Modify: `tests/codemie_tools/core/vcs/gitlab/test_models.py`
- Modify: `tests/codemie_tools/core/vcs/github/test_models.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie_tools/core/vcs/gitlab/test_models.py`:
```python
from codemie_tools.core.vcs.gitlab.models import GitlabConfig

def test_gitlab_url_placeholder_default():
    schema = GitlabConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://gitlab.example.com"

def test_gitlab_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_GITLAB_TOOL_URL", "https://gitlab.company.com")
    assert config_url_or_fallback("DEFAULT_GITLAB_TOOL_URL", "https://gitlab.example.com") == "https://gitlab.company.com"
```

Add to `tests/codemie_tools/core/vcs/github/test_models.py`:
```python
def test_github_url_default_value():
    """GitHub url has a real runtime default (type B) — verify it equals the fallback."""
    from codemie_tools.core.vcs.github.models import GithubConfig
    schema = GithubConfig.model_json_schema()
    assert schema["properties"]["url"].get("default") == "https://api.github.com"
    assert schema["properties"]["url"].get("placeholder") == "https://api.github.com"

def test_github_url_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_GITHUB_TOOL_URL", "https://github.company.com/api/v3")
    assert config_url_or_fallback("DEFAULT_GITHUB_TOOL_URL", "https://api.github.com") == "https://github.company.com/api/v3"
```

- [ ] **Step 2: Run tests to confirm status**

```bash
poetry run pytest tests/codemie_tools/core/vcs/ -k "placeholder or url_default or url_override" -v
```

- [ ] **Step 3: Update GitLab model (type A)**

In `src/codemie_tools/core/vcs/gitlab/models.py`, add `config_url_or_fallback` to the import from `codemie_tools.base.models` and update:

```python
    url: str = RequiredField(
        description="GitLab instance URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_GITLAB_TOOL_URL", "https://gitlab.example.com")},
    )
```

- [ ] **Step 4: Update GitHub model (type B)**

In `src/codemie_tools/core/vcs/github/models.py`, add `config_url_or_fallback` to the import and update:

```python
    url: Optional[str] = Field(
        default=config_url_or_fallback("DEFAULT_GITHUB_TOOL_URL", "https://api.github.com"),
        description="GitHub API URL, typically https://api.github.com",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_GITHUB_TOOL_URL", "https://api.github.com")},
    )
```

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/codemie_tools/core/vcs/ -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie_tools/core/vcs/gitlab/models.py \
        src/codemie_tools/core/vcs/github/models.py \
        tests/codemie_tools/core/vcs/gitlab/test_models.py \
        tests/codemie_tools/core/vcs/github/test_models.py
git commit -m "feat(EPMCDME-10928): make GitLab and GitHub URL defaults env-var configurable"
```

---

## Task 6: Azure DevOps integrations (4 configs, shared env var)

All four are type A using `DEFAULT_AZURE_DEVOPS_TOOL_URL`. `AzureDevOpsWorkItemConfig`, `AzureDevOpsWikiConfig`, and `AzureDevOpsTestPlanConfig` currently show `https://dev.azure.com/your-organization` as placeholder — after this change they will show `https://dev.azure.com` (the Config default) when no env var is set.

**Files:**
- Modify: `src/codemie_tools/core/vcs/azure_devops_git/models.py`
- Modify: `src/codemie_tools/azure_devops/work_item/models.py`
- Modify: `src/codemie_tools/azure_devops/wiki/models.py`
- Modify: `src/codemie_tools/azure_devops/test_plan/models.py`
- Modify: `tests/codemie_tools/core/vcs/azure_devops_git/test_models.py`
- Create or modify: `tests/codemie_tools/azure_devops/work_item/test_models.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie_tools/core/vcs/azure_devops_git/test_models.py`:
```python
from codemie_tools.core.vcs.azure_devops_git.models import AzureDevOpsGitConfig

def test_ado_git_url_placeholder_default():
    schema = AzureDevOpsGitConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://dev.azure.com"

def test_ado_git_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_AZURE_DEVOPS_TOOL_URL", "https://ado.company.com")
    assert config_url_or_fallback("DEFAULT_AZURE_DEVOPS_TOOL_URL", "https://dev.azure.com") == "https://ado.company.com"
```

Create `tests/codemie_tools/azure_devops/work_item/test_models.py` (and `__init__.py` if missing):
```python
from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig

def test_ado_work_item_organization_url_placeholder_default():
    schema = AzureDevOpsWorkItemConfig.model_json_schema()
    assert schema["properties"]["organization_url"]["placeholder"] == "https://dev.azure.com"
```

- [ ] **Step 2: Run tests to confirm status**

```bash
poetry run pytest tests/codemie_tools/core/vcs/azure_devops_git/ tests/codemie_tools/azure_devops/ -v 2>/dev/null || true
```

- [ ] **Step 3: Update all four models**

`src/codemie_tools/core/vcs/azure_devops_git/models.py`:
```python
# Add config_url_or_fallback to import, update url field:
    url: str = RequiredField(
        description="Azure DevOps base URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_AZURE_DEVOPS_TOOL_URL", "https://dev.azure.com")},
    )
```

`src/codemie_tools/azure_devops/work_item/models.py`:
```python
# Add config_url_or_fallback to import, update organization_url field:
    organization_url: str = RequiredField(
        description="Azure DevOps organization URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_AZURE_DEVOPS_TOOL_URL", "https://dev.azure.com")},
    )
```

Apply the same `organization_url` change to `wiki/models.py` and `test_plan/models.py`.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie_tools/core/vcs/azure_devops_git/ tests/codemie_tools/azure_devops/ -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/core/vcs/azure_devops_git/models.py \
        src/codemie_tools/azure_devops/work_item/models.py \
        src/codemie_tools/azure_devops/wiki/models.py \
        src/codemie_tools/azure_devops/test_plan/models.py \
        tests/codemie_tools/core/vcs/azure_devops_git/test_models.py \
        tests/codemie_tools/azure_devops/
git commit -m "feat(EPMCDME-10928): make Azure DevOps URL defaults env-var configurable (shared DEFAULT_AZURE_DEVOPS_TOOL_URL)"
```

---

## Task 7: Infrastructure + access integrations (Keycloak, Elastic, Kubernetes)

All type A. Elastic: note `DEFAULT_ELASTIC_TOOL_URL` is separate from the existing `ELASTIC_URL` infrastructure field. Do not touch `_handle_missing_config` in `settings.py`.

**Files:**
- Modify: `src/codemie_tools/access_management/keycloak/models.py`
- Modify: `src/codemie_tools/data_management/elastic/models.py`
- Modify: `src/codemie_tools/cloud/kubernetes/models.py`
- Create or modify: test files for each

- [ ] **Step 1: Write failing tests**

Create `tests/codemie_tools/access_management/keycloak/test_models.py` (check if exists first):
```python
from codemie_tools.access_management.keycloak.models import KeycloakConfig

def test_keycloak_base_url_placeholder_default():
    schema = KeycloakConfig.model_json_schema()
    assert schema["properties"]["base_url"]["placeholder"] == "https://keycloak.example.com"

def test_keycloak_base_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_KEYCLOAK_TOOL_URL", "https://keycloak.company.com")
    assert config_url_or_fallback("DEFAULT_KEYCLOAK_TOOL_URL", "https://keycloak.example.com") == "https://keycloak.company.com"
```

Create `tests/codemie_tools/data_management/elastic/test_models.py`:
```python
from codemie_tools.data_management.elastic.models import ElasticConfig

def test_elastic_url_placeholder_default():
    schema = ElasticConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://your-elastic-instance.com"
```

Create `tests/codemie_tools/cloud/kubernetes/test_models.py`:
```python
from codemie_tools.cloud.kubernetes.models import KubernetesConfig

def test_kubernetes_url_placeholder_default():
    schema = KubernetesConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://kubernetes.default.svc"
```

- [ ] **Step 2: Run to confirm status**

```bash
poetry run pytest tests/codemie_tools/access_management/keycloak/ tests/codemie_tools/data_management/elastic/ tests/codemie_tools/cloud/kubernetes/ -v 2>/dev/null || true
```

- [ ] **Step 3: Update Keycloak model**

In `src/codemie_tools/access_management/keycloak/models.py`, add `config_url_or_fallback` to import:
```python
    base_url: str = RequiredField(
        description="Base URL of the Keycloak server",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_KEYCLOAK_TOOL_URL", "https://keycloak.example.com")},
    )
```

- [ ] **Step 4: Update Elastic model**

In `src/codemie_tools/data_management/elastic/models.py`:
```python
    url: str = RequiredField(
        description="Elasticsearch instance URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_ELASTIC_TOOL_URL", "https://your-elastic-instance.com")},
    )
```

- [ ] **Step 5: Update Kubernetes model**

In `src/codemie_tools/cloud/kubernetes/models.py`:
```python
    url: str = RequiredField(
        description="Kubernetes API Server URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_KUBERNETES_TOOL_URL", "https://kubernetes.default.svc")},
    )
```

- [ ] **Step 6: Run tests**

```bash
poetry run pytest tests/codemie_tools/access_management/keycloak/ tests/codemie_tools/data_management/elastic/ tests/codemie_tools/cloud/kubernetes/ -v
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codemie_tools/access_management/keycloak/models.py \
        src/codemie_tools/data_management/elastic/models.py \
        src/codemie_tools/cloud/kubernetes/models.py \
        tests/codemie_tools/access_management/ \
        tests/codemie_tools/data_management/elastic/ \
        tests/codemie_tools/cloud/
git commit -m "feat(EPMCDME-10928): make Keycloak, Elastic, Kubernetes URL defaults env-var configurable"
```

---

## Task 8: QA integrations (Xray, Zephyr) + ITSM + monitoring (ServiceNow, ReportPortal, SonarConfig)

All type A.

**Files:**
- Modify: `src/codemie_tools/qa/xray/models.py`
- Modify: `src/codemie_tools/itsm/servicenow/models.py`
- Modify: `src/codemie_tools/report_portal/models.py`
- Modify: `src/codemie_tools/code/models.py`
- Modify: `tests/codemie_tools/qa/xray/test_xray_models.py`
- Create: `tests/codemie_tools/itsm/servicenow/test_models.py`
- Create: `tests/codemie_tools/report_portal/test_models.py`
- Create: `tests/codemie_tools/code/sonar/test_models.py` (or `tests/codemie_tools/code/test_models.py`)

Note: Zephyr was handled in Task 2 (prerequisite). The `DEFAULT_ZEPHYR_TOOL_URL` env var was already declared in Task 1 and the ZephyrConfig field was updated in Task 2. No further Zephyr changes needed.

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie_tools/qa/xray/test_xray_models.py`:
```python
from codemie_tools.qa.xray.models import XrayConfig

def test_xray_base_url_placeholder_default():
    schema = XrayConfig.model_json_schema()
    assert schema["properties"]["base_url"]["placeholder"] == "https://xray.cloud.getxray.app"

def test_xray_base_url_placeholder_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_XRAY_TOOL_URL", "https://xray.company.com")
    assert config_url_or_fallback("DEFAULT_XRAY_TOOL_URL", "https://xray.cloud.getxray.app") == "https://xray.company.com"
```

Create `tests/codemie_tools/itsm/servicenow/test_models.py`:
```python
from codemie_tools.itsm.servicenow.models import ServiceNowConfig

def test_servicenow_url_placeholder_default():
    schema = ServiceNowConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://your-instance.service-now.com"
```

Create `tests/codemie_tools/report_portal/test_models.py`:
```python
from codemie_tools.report_portal.models import ReportPortalConfig

def test_reportportal_url_placeholder_default():
    schema = ReportPortalConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://reportportal.example.com"
```

Create `tests/codemie_tools/code/sonar/test_models.py`:
```python
from codemie_tools.code.models import SonarConfig

def test_sonar_url_placeholder_default():
    schema = SonarConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "https://sonarqube.example.com"
```

- [ ] **Step 2: Run to confirm status**

```bash
poetry run pytest tests/codemie_tools/qa/xray/ tests/codemie_tools/itsm/ tests/codemie_tools/report_portal/ tests/codemie_tools/code/sonar/ -v 2>/dev/null || true
```

- [ ] **Step 3: Update Xray model**

In `src/codemie_tools/qa/xray/models.py`, add `config_url_or_fallback` to import:
```python
    base_url: str = RequiredField(
        description="Xray Cloud base URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_XRAY_TOOL_URL", "https://xray.cloud.getxray.app")},
    )
```

- [ ] **Step 4: Update ServiceNow model**

In `src/codemie_tools/itsm/servicenow/models.py`, add `config_url_or_fallback` to import and update `url` field placeholder.

- [ ] **Step 5: Update ReportPortal model**

In `src/codemie_tools/report_portal/models.py`, add `config_url_or_fallback` to import and update `url` field placeholder.

- [ ] **Step 6: Update SonarConfig model**

In `src/codemie_tools/code/models.py`, add `config_url_or_fallback` to import and update `url` field placeholder:
```python
    url: str = RequiredField(
        description="SonarQube instance URL",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_SONAR_TOOL_URL", "https://sonarqube.example.com")},
    )
```

- [ ] **Step 7: Run all tests**

```bash
poetry run pytest tests/codemie_tools/qa/xray/ tests/codemie_tools/itsm/ tests/codemie_tools/report_portal/ tests/codemie_tools/code/sonar/ -v
```
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/codemie_tools/qa/xray/models.py \
        src/codemie_tools/itsm/servicenow/models.py \
        src/codemie_tools/report_portal/models.py \
        src/codemie_tools/code/models.py \
        tests/codemie_tools/qa/xray/test_xray_models.py \
        tests/codemie_tools/itsm/ \
        tests/codemie_tools/report_portal/ \
        tests/codemie_tools/code/sonar/
git commit -m "feat(EPMCDME-10928): make Xray, ServiceNow, ReportPortal, SonarQube URL defaults env-var configurable"
```

---

## Task 9: Email integration (SMTP type A + OAuth fields type B)

Three fields in one file. `url` is type A (RequiredField). `oauth_authority` and `oauth_scope` are type B (Field with real runtime default).

**Files:**
- Modify: `src/codemie_tools/notification/email/models.py`
- Create: `tests/codemie_tools/notification/email/test_models.py`

- [ ] **Step 1: Write failing tests**

Create `tests/codemie_tools/notification/email/test_models.py`:
```python
from codemie_tools.notification.email.models import EmailToolConfig


def test_email_smtp_url_placeholder_default():
    schema = EmailToolConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "smtp.gmail.com:587"


def test_email_oauth_authority_default_value():
    """oauth_authority is type B — verify runtime default."""
    config = EmailToolConfig()
    assert config.oauth_authority == "https://login.microsoftonline.com"


def test_email_oauth_scope_default_value():
    config = EmailToolConfig()
    assert config.oauth_scope == "https://outlook.office365.com/.default"


def test_email_oauth_authority_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL", "https://login.microsoftonline.cn")
    assert config_url_or_fallback("DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL", "https://login.microsoftonline.com") == "https://login.microsoftonline.cn"


def test_email_oauth_scope_override(monkeypatch):
    from codemie.configs import config as app_config
    from codemie_tools.base.models import config_url_or_fallback
    monkeypatch.setattr(app_config, "DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL", "https://partner.outlook.cn/.default")
    assert config_url_or_fallback("DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL", "https://outlook.office365.com/.default") == "https://partner.outlook.cn/.default"
```

- [ ] **Step 2: Run to confirm status**

```bash
poetry run pytest tests/codemie_tools/notification/email/ -v 2>/dev/null || true
```

- [ ] **Step 3: Update email model**

In `src/codemie_tools/notification/email/models.py`, update imports:
```python
from codemie_tools.base.models import CodeMieToolConfig, RequiredField, CredentialTypes, config_url_or_fallback
```

Update the three URL fields:
```python
    # Type A — placeholder only
    url: str = RequiredField(
        description="SMTP server URL including port, e.g. smtp.gmail.com:587 or smtp.office365.com:587",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_EMAIL_SMTP_TOOL_URL", "smtp.gmail.com:587")},
    )

    # Type B — both default and placeholder
    oauth_authority: Optional[str] = Field(
        default=config_url_or_fallback("DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL", "https://login.microsoftonline.com"),
        description="OAuth authority base URL without tenant_id (optional, defaults to https://login.microsoftonline.com)",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL", "https://login.microsoftonline.com")},
    )
    oauth_scope: Optional[str] = Field(
        default=config_url_or_fallback("DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL", "https://outlook.office365.com/.default"),
        description="OAuth scope for token acquisition (optional, defaults to https://outlook.office365.com/.default)",
        json_schema_extra={"placeholder": config_url_or_fallback("DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL", "https://outlook.office365.com/.default")},
    )
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie_tools/notification/email/ -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/notification/email/models.py \
        tests/codemie_tools/notification/email/
git commit -m "feat(EPMCDME-10928): make Email SMTP and OAuth URL defaults env-var configurable"
```

---

## Task 10: Helm values.yaml — deployment override surface

**Files:**
- Modify: `deploy-templates/values.yaml`

- [ ] **Step 1: Add 18 new entries to the `customEnv` section of `deploy-templates/values.yaml`**

In the `customEnv:` list (currently contains `ELASTIC_URL`, `KEYCLOAK_LOGOUT_URL`, etc.), add the following block after the existing URL entries:

```yaml
  # Integration tool default URLs — set per deployment to override placeholder values
  - name: DEFAULT_JIRA_TOOL_URL
    value: ""
  - name: DEFAULT_CONFLUENCE_TOOL_URL
    value: ""
  - name: DEFAULT_GITLAB_TOOL_URL
    value: ""
  - name: DEFAULT_GITHUB_TOOL_URL
    value: ""
  - name: DEFAULT_AZURE_DEVOPS_TOOL_URL
    value: ""
  - name: DEFAULT_KEYCLOAK_TOOL_URL
    value: ""
  - name: DEFAULT_ELASTIC_TOOL_URL
    value: ""
  - name: DEFAULT_EMAIL_SMTP_TOOL_URL
    value: ""
  - name: DEFAULT_EMAIL_OAUTH_AUTHORITY_TOOL_URL
    value: ""
  - name: DEFAULT_EMAIL_OAUTH_SCOPE_TOOL_URL
    value: ""
  - name: DEFAULT_XRAY_TOOL_URL
    value: ""
  - name: DEFAULT_ZEPHYR_TOOL_URL
    value: ""
  - name: DEFAULT_SERVICENOW_TOOL_URL
    value: ""
  - name: DEFAULT_REPORTPORTAL_TOOL_URL
    value: ""
  - name: DEFAULT_KUBERNETES_TOOL_URL
    value: ""
  - name: DEFAULT_SONAR_TOOL_URL
    value: ""
  - name: DEFAULT_XWIKI_TOOL_URL
    value: ""
  - name: DEFAULT_SHAREPOINT_TOOL_URL
    value: ""
```

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('deploy-templates/values.yaml'))" && echo "YAML valid"
```
Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add deploy-templates/values.yaml
git commit -m "feat(EPMCDME-10928): add DEFAULT_*_TOOL_URL entries to Helm values.yaml for deployment override"
```

---

## Self-review notes

- Task 1 must complete before all other tasks (foundation).
- Task 2 must complete before Task 8's Zephyr test (the Zephyr placeholder is tested in Task 2 itself).
- Task 3 (SharePoint) is independent of Tasks 4–10.
- Tasks 4–10 are independent of each other and can be executed in any order after Task 1.
- Task 10 (Helm) is a documentation-only change and can be done last.
- The `or fallback` in `config_url_or_fallback` handles empty-string env vars (set to `""`) identically to "not set" — both return the fallback. This is by design.
- `DEFAULT_ELASTIC_TOOL_URL` is distinct from the infrastructure `ELASTIC_URL`. `_handle_missing_config` in `settings.py` is untouched.
