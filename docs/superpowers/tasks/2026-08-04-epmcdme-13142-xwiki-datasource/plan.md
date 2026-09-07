# xWiki Datasource Implementation Plan (EPMCDME-13142)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user connect one xWiki Space so its pages — and every page in spaces beneath it — are indexed into Elasticsearch and answerable by an assistant.

**Architecture:** A new `XWikiLoader` crawls the xWiki REST API (space discovery → page listing → per-page fetch → rendered markdown via `/bin`), and a new `XWikiDatasourceProcessor` plugs it into the existing `BaseDatasourceProcessor`, which already owns batching, chunking, guardrails, retries, stats and cron scheduling. Everything else is type registration: one Alembic column and thirteen edited files.

**Tech Stack:** Python 3.12 · FastAPI · SQLModel + Alembic + PostgreSQL JSONB · LangChain `Document` · Elasticsearch vector store · `httpx` · `markdownify` · pytest + `unittest.mock`

**Spec:** [`spec.md`](spec.md) · **Verified codebase facts:** [`technical-analysis.md`](technical-analysis.md) §9

## Test Runner

**Every `pytest` command below runs through a throwaway container.** Define this once and reuse it:

```bash
xpytest() {
  docker run --rm -v ~/Projects/Work/codemie-dev/codemie:/repo -w /repo codemie-dev-codemie \
    sh -c "/venv/bin/python -m pytest $* -q"
}
```

So `poetry run pytest tests/... -v` in a task means `xpytest tests/...`.

Why not the alternatives, both measured:

- **The local poetry venv is incomplete** (`No module named 'starlette'`, then `tokenizers`, then a
  chain ending at `tree_sitter_languages`, which does not build on Python 3.13). Patching it by
  hand gets 18/21 donor tests passing; the container gets 21/21.
- **The long-lived `codemie-dev-codemie-1` container cannot be used**: `tests/` is not bind-mounted
  (it is baked into the image and stale — only `tests/codemie/rest_api` exists), and `.env` is a
  single-file bind pinned to a replaced inode, so anything importing config fails with `EPERM`.
  **Do not recreate that container to fix it** — it holds hand-installed redis and langfuse packages
  that the OSS build of `main` does not install, and the backend stops booting without them.

Baseline before starting: `xpytest tests/codemie/datasource/azure_devops_wiki/` → **21 passed**.

Alembic runs the same way, from the alembic directory:

```bash
docker run --rm -v ~/Projects/Work/codemie-dev/codemie:/repo \
  -w /repo/src/external/alembic codemie-dev-codemie sh -c '/venv/bin/alembic <command>'
```

## Global Constraints

- **No new dependencies.** `markdownify` is already declared (`pyproject.toml:119`, `^1.2.0`). A contributor **cannot run `poetry lock`** — it resolves against a private GCP Artifact Registry we have no access to. Reaching for a new package is a **hard stop**: raise it, do not improvise.
- **Backend only.** `codemie-ui` is a separate MR. Do not edit it.
- **TDD.** Write the failing test, see it fail, then implement. Every task below states `Test-first`.
- **Commits:** `EPMCDME-13142: <Capital sentence>`.
- **Never commit `.diff` artifacts** from `docs/superpowers/**`.
- **`make verify` silently skips the license gate on macOS** (no `.PHONY`; the `license` target resolves to the `LICENSE` file). Run `make license-check` explicitly.
- Every user-facing validation error carries `field_error` so the later frontend MR needs no backend change.
- Scheduled reindex is a **full delete-and-rebuild** (`reprocess()`), matching every comparable type. Do not implement a parallel incremental path.
- Naming: the datasources-config model is `XWikiDatasourceConfig`. `XWikiConfig` is already taken by the credential model at `src/codemie_tools/core/project_management/xwiki/models.py:27`.

## File Structure

| Path | Responsibility |
|---|---|
| `src/external/alembic/versions/<rev>_add_xwiki_column_to_index_info.py` | **Create** — the `xwiki` JSONB column |
| `src/codemie/datasource/loader/xwiki_loader.py` | **Create** — the crawl: discovery, pagination, content resolution, stats, typed errors |
| `src/codemie/datasource/xwiki/__init__.py`, `xwiki_datasource_processor.py` | **Create** — processor wiring |
| `src/codemie/core/constants.py`, `src/codemie/service/constants.py` | Type enums |
| `src/codemie/datasource/datasources_config.py`, `config/datasources/datasources-config.yaml` | Loader tuning |
| `src/codemie/rest_api/models/index.py` | Index info, request models, update helper |
| `src/codemie/rest_api/routers/index.py` | `POST` / `PUT /index/knowledge_base/xwiki` |
| `src/codemie/service/index/datasource_health_check_service.py` | Health-check arm |
| `src/codemie/service/settings/settings.py`, `settings_tester.py`, `settings_request_validator.py` | Credentials, test-connection, webhook deny-list |
| `src/codemie/triggers/trigger_models.py`, `actors/datasource.py`, `bindings/cron.py` | Scheduled reindex + stale-job resume |
| `tests/codemie/datasource/xwiki/` | **Create** — loader + processor tests |
| `tests/codemie/datasource/test_datasource_type_registration.py` | **Create** — guard test |

---

## Task 1: Alembic migration for the `xwiki` column

**Files:**
- Create: `src/external/alembic/versions/<generated>_add_xwiki_column_to_index_info.py`

**Interfaces:**
- Consumes: nothing
- Produces: the `index_info.xwiki` JSONB column that Task 3's `IndexInfo.xwiki` field persists into

**Test-first: no** — an additive DDL migration has no unit-testable behaviour; it is verified by `alembic upgrade head` succeeding and the column existing.

- [x] **Step 1: Confirm the current head — DONE**

```bash
docker run --rm -v ~/Projects/Work/codemie-dev/codemie:/repo \
  -w /repo/src/external/alembic codemie-dev-codemie sh -c '/venv/bin/alembic heads'
```

Output: `t1u2v3w4x5y6 (head)` — a single head, confirmed by alembic itself. `down_revision` is therefore `t1u2v3w4x5y6`.

- [ ] **Step 2: Write the migration**

Create the file (donor: `2b461b2f3d10_added_azure_devops_wiki_column_to_the_.py`):

```python
"""add xwiki column to the index_info table

Revision ID: <generated>
Revises: t1u2v3w4x5y6
Create Date: <generated>

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "<generated>"
down_revision: Union[str, None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("index_info", sa.Column("xwiki", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("index_info", "xwiki")
```

- [ ] **Step 3: Apply and verify the column exists**

```bash
docker exec -u root codemie-dev-codemie-1 sh -c 'cd /app/src/external/alembic && alembic upgrade head'
docker exec codemie-dev-postgres-1 psql -U postgres -d codemie -c "\d index_info" | grep xwiki
```

Expected: `xwiki | jsonb | | |`

- [ ] **Step 4: Verify downgrade is reversible, then re-apply**

```bash
docker exec -u root codemie-dev-codemie-1 sh -c 'cd /app/src/external/alembic && alembic downgrade -1 && alembic upgrade head'
```

Expected: both succeed with no error.

- [ ] **Step 5: Commit**

```bash
git add src/external/alembic/versions/
git commit -m "EPMCDME-13142: Add xwiki JSONB column to index_info"
```

---

## Task 2: Datasource type enums and loader config

**Files:**
- Modify: `src/codemie/core/constants.py:91-101`
- Modify: `src/codemie/service/constants.py` (`FullDatasourceTypes`)
- Modify: `src/codemie/datasource/datasources_config.py:87` (models), `:124-135` (`LoadersConfig`), `:175` area (constants + log lines)
- Modify: `config/datasources/datasources-config.yaml:143`
- Test: `tests/codemie/datasource/xwiki/test_xwiki_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `DatasourceTypes.XWIKI == "xwiki"` · `FullDatasourceTypes.XWIKI == "knowledge_base_xwiki"` · `XWIKI_CONFIG` with `.chunk_size`, `.chunk_overlap`, `.loader_batch_size`, `.loader_max_pages`, `.request_timeout_seconds`, `.max_failed_pages_floor`, `.max_failed_pages_ratio`

**Test-first: yes** — a test asserting `XWIKI_CONFIG` loads with the expected fields, failing with `AttributeError: module has no attribute 'XWIKI_CONFIG'`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/__init__.py` (empty) and `tests/codemie/datasource/xwiki/test_xwiki_config.py`:

```python
from codemie.core.constants import DatasourceTypes
from codemie.service.constants import FullDatasourceTypes


def test_datasource_type_members_exist():
    assert DatasourceTypes.XWIKI.value == "xwiki"
    assert FullDatasourceTypes.XWIKI.value == "knowledge_base_xwiki"


def test_xwiki_loader_config_is_loaded_from_yaml():
    from codemie.datasource.datasources_config import XWIKI_CONFIG

    assert XWIKI_CONFIG.chunk_size > 0
    assert XWIKI_CONFIG.chunk_overlap >= 0
    assert XWIKI_CONFIG.loader_batch_size > 0
    assert XWIKI_CONFIG.loader_max_pages > 0
    assert XWIKI_CONFIG.request_timeout_seconds > 0
    assert XWIKI_CONFIG.max_failed_pages_floor >= 0
    assert 0 < XWIKI_CONFIG.max_failed_pages_ratio <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_config.py -v`
Expected: FAIL — `AttributeError: XWIKI` on `DatasourceTypes`.

- [ ] **Step 3: Add the enum members**

`src/codemie/core/constants.py`, inside `DatasourceTypes` after `XRAY = "xray"`:

```python
    XWIKI = "xwiki"
```

`src/codemie/service/constants.py`, inside `FullDatasourceTypes` after `AZURE_DEVOPS_WORK_ITEM`:

```python
    XWIKI = "knowledge_base_xwiki"
```

- [ ] **Step 4: Add the config model and wiring**

`src/codemie/datasource/datasources_config.py`, after `class AzureDevOpsWikiConfig`:

```python
class XWikiDatasourceConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int
    loader_batch_size: int
    loader_max_pages: int
    request_timeout_seconds: int = 30
    max_failed_pages_floor: int = 5
    max_failed_pages_ratio: float = 0.1
```

In `LoadersConfig`, after `svn_loader: SVNConfig`:

```python
    xwiki_loader: XWikiDatasourceConfig
```

Beside the other module constants:

```python
XWIKI_CONFIG = datasources_config.loaders.xwiki_loader
```

And beside the other log lines:

```python
logger.info(f"XWikiDatasourceConfig instantiated: {XWIKI_CONFIG}")
```

- [ ] **Step 5: Add the YAML block**

`config/datasources/datasources-config.yaml`, after the `azure_devops_wiki_loader:` block. **`LoadersConfig` has no defaults for the required fields — omitting this block is a Pydantic validation error at import time and the whole app fails to boot.**

```yaml
  xwiki_loader:
    chunk_size: 1000
    chunk_overlap: 50
    loader_batch_size: 50
    loader_max_pages: 5000
    request_timeout_seconds: 30
    max_failed_pages_floor: 5
    max_failed_pages_ratio: 0.1
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/codemie/core/constants.py src/codemie/service/constants.py \
        src/codemie/datasource/datasources_config.py config/datasources/datasources-config.yaml \
        tests/codemie/datasource/xwiki/
git commit -m "EPMCDME-13142: Register xwiki datasource type and loader config"
```

---

## Task 3: `XWikiIndexInfo` model and `IndexInfo` persistence

**Files:**
- Modify: `src/codemie/rest_api/models/index.py` — `:95` (context-type tuple), `:155` (new model), `:285` (column), `:682`/`:707` (`IndexInfo.new`), `:766` area (update helper)
- Test: `tests/codemie/datasource/xwiki/test_xwiki_index_info.py`

**Interfaces:**
- Consumes: Task 1's DB column
- Produces: `XWikiIndexInfo(space: str, wiki: str = "xwiki")` · `IndexInfo.xwiki: Optional[XWikiIndexInfo]` · `IndexInfo.new(..., xwiki=...)` · `IndexInfo._update_xwiki_fields(**kwargs)`

**Test-first: yes** — a test constructing `XWikiIndexInfo(space="KB")` and asserting the default wiki, failing with `ImportError: cannot import name 'XWikiIndexInfo'`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/test_xwiki_index_info.py`:

```python
import pytest
from pydantic import ValidationError

from codemie.rest_api.models.index import IndexInfo, XWikiIndexInfo


def test_defaults_to_the_main_wiki():
    info = XWikiIndexInfo(space="KB")
    assert info.space == "KB"
    assert info.wiki == "xwiki"


def test_space_is_required():
    with pytest.raises(ValidationError):
        XWikiIndexInfo()


def test_index_info_exposes_an_xwiki_field():
    assert "xwiki" in IndexInfo.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_index_info.py -v`
Expected: FAIL — `ImportError: cannot import name 'XWikiIndexInfo'`.

- [ ] **Step 3: Add the model and the column**

`src/codemie/rest_api/models/index.py`, after `class AzureDevOpsWorkItemIndexInfo`:

```python
class XWikiIndexInfo(BaseModel):
    space: str  # dotted space id as shown in the URL: "KB", or "KB.Onboarding" for a subtree
    wiki: str = "xwiki"  # wiki id; part of every REST path, non-default only on subwiki farms
```

In `IndexInfo`, beside the other per-type columns:

```python
    xwiki: Optional[XWikiIndexInfo] = SQLField(default=None, sa_column=Column(PydanticType(XWikiIndexInfo)))
```

In `IndexTypeByContextTypeMapping.KNOWLEDGE_BASE` (`:90-101`), add to the tuple:

```python
        "knowledge_base_xwiki",
```

- [ ] **Step 4: Wire `IndexInfo.new()`**

Beside `azure_devops_wiki = kwargs.get("azure_devops_wiki")`:

```python
        xwiki = kwargs.get("xwiki")
```

and in the `cls(...)` call beside `azure_devops_wiki=azure_devops_wiki,`:

```python
            xwiki=xwiki,
```

- [ ] **Step 5: Add the update helper**

Mirroring `_update_sharepoint_fields`. `flag_modified` is required — SQLAlchemy does not persist an in-place edit to a JSONB column without it.

```python
    def _update_xwiki_fields(self, **kwargs) -> None:
        """Update xWiki-specific fields."""
        if not self.xwiki:
            return

        space = kwargs.get("space")
        wiki = kwargs.get("wiki")

        if space:
            self.xwiki.space = space
        if wiki:
            self.xwiki.wiki = wiki
        if space or wiki:
            flag_modified(self, "xwiki")
```

Call it from `update_index()` alongside the other per-type helpers:

```python
        self._update_xwiki_fields(**kwargs)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_index_info.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/models/index.py tests/codemie/datasource/xwiki/test_xwiki_index_info.py
git commit -m "EPMCDME-13142: Add XWikiIndexInfo model and IndexInfo persistence"
```

---

## Task 4: Loader path builders and percent-encoding

**Files:**
- Create: `src/codemie/datasource/loader/xwiki_loader.py`
- Test: `tests/codemie/datasource/xwiki/test_xwiki_loader_paths.py`

**Interfaces:**
- Consumes: `XWikiConfig` from `codemie_tools.core.project_management.xwiki.models`
- Produces: `XWikiLoader(config, space, wiki="xwiki")` · `XWikiLoader._rest_space_path(space) -> str` · `XWikiLoader._bin_page_path(space, name) -> str` · module constants `METADATA_*`

> **Deviation from spec §2.1, deliberate.** The spec says to reuse `build_spaces_path()`. It is **not** reused: `src/codemie_tools/core/project_management/xwiki/utils.py:20-27` does no percent-encoding, and encoding is a hard requirement (`Vacation Policy` fails without `%20`). We implement an encoding builder in the loader and leave the tools helper untouched.

**Test-first: yes** — a test asserting `_rest_space_path("KB.Onboarding")` returns the segment-repeating REST form and that spaces/Cyrillic are encoded, failing with `ModuleNotFoundError: codemie.datasource.loader.xwiki_loader`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/test_xwiki_loader_paths.py`:

```python
import pytest

from codemie_tools.core.project_management.xwiki.models import XWikiConfig
from codemie.datasource.loader.xwiki_loader import XWikiLoader


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080/", username="AdminAdmin", token="admin"),
        space="KB",
    )


def test_rest_path_repeats_the_spaces_segment(loader):
    assert loader._rest_space_path("KB.Onboarding") == "/spaces/KB/spaces/Onboarding"


def test_bin_path_uses_plain_slashes(loader):
    assert loader._bin_page_path("KB.Onboarding", "Checklist") == "/bin/get/KB/Onboarding/Checklist"


def test_spaces_in_names_are_percent_encoded(loader):
    assert loader._bin_page_path("KB", "Vacation Policy") == "/bin/get/KB/Vacation%20Policy"


def test_cyrillic_is_percent_encoded(loader):
    assert loader._bin_page_path("KB", "Довідка") == "/bin/get/KB/%D0%94%D0%BE%D0%B2%D1%96%D0%B4%D0%BA%D0%B0"


def test_base_url_trailing_slash_does_not_double(loader):
    assert loader._base == "http://xwiki:8080"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.datasource.loader.xwiki_loader'`.

- [ ] **Step 3: Create the module skeleton**

Create `src/codemie/datasource/loader/xwiki_loader.py`. Copy the Apache-2.0 header from `src/codemie/datasource/loader/base_datasource_loader.py:1-13` verbatim — `make license-check` requires it — then:

```python
import logging
from typing import Any, Iterator
from urllib.parse import quote

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig

logger = logging.getLogger(__name__)

DATASOURCE_TYPE = "xWiki"

METADATA_SOURCE = "source"
METADATA_PAGE_ID = "page_id"
METADATA_SPACE = "space"
METADATA_WIKI = "wiki"
METADATA_TITLE = "title"
METADATA_MODIFIED = "modified"
METADATA_VERSION = "version"
METADATA_AUTHOR = "author"
METADATA_CONTENT_FORMAT = "content_format"

CONTENT_FORMAT_RENDERED = "rendered"
CONTENT_FORMAT_RAW = "raw"


class XWikiLoader(BaseLoader, BaseDatasourceLoader):
    """Loads pages of one xWiki space and all of its descendant spaces."""

    def __init__(
        self,
        config: XWikiConfig,
        space: str,
        wiki: str = "xwiki",
        page_size: int = 50,
        max_pages: int = 5000,
        timeout_seconds: int = 30,
        max_failed_pages_floor: int = 5,
        max_failed_pages_ratio: float = 0.1,
    ):
        self.config = config
        self.space = space
        self.wiki = wiki
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.max_failed_pages_floor = max_failed_pages_floor
        self.max_failed_pages_ratio = max_failed_pages_ratio
        self._base = (config.url or "").rstrip("/")
        self._skipped = 0
        self._failed = 0
        self._truncated = False

    @staticmethod
    def _segments(space: str) -> list[str]:
        return [p.strip() for p in space.split(".") if p.strip()]

    def _rest_space_path(self, space: str) -> str:
        return "".join(f"/spaces/{quote(p, safe='')}" for p in self._segments(space))

    def _bin_page_path(self, space: str, name: str) -> str:
        parts = self._segments(space) + [name]
        return "/bin/get/" + "/".join(quote(p, safe="") for p in parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_paths.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/datasource/loader/xwiki_loader.py tests/codemie/datasource/xwiki/test_xwiki_loader_paths.py
git commit -m "EPMCDME-13142: Add xWiki loader path builders with percent-encoding"
```

---

## Task 5: HTTP layer and typed error mapping

**Files:**
- Modify: `src/codemie/datasource/loader/xwiki_loader.py`
- Test: `tests/codemie/datasource/xwiki/test_xwiki_loader_errors.py`

**Interfaces:**
- Consumes: Task 4's `_base`, `_rest_space_path`
- Produces: `XWikiLoader._auth_headers() -> dict` · `XWikiLoader._get_json(path, params=None) -> dict` · `XWikiLoader._get_text(path, params=None) -> str | None`

**Test-first: yes** — tests asserting 403 raises `UnauthorizedException` and a non-JSON 200 raises `ConnectionException`, failing with `AttributeError: '_get_json'`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/test_xwiki_loader_errors.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from codemie.datasource.exceptions import ConnectionException, UnauthorizedException
from codemie.datasource.loader.xwiki_loader import XWikiLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
    )


def _response(status_code=200, json_data=None, text="", raise_on_json=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if raise_on_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


def test_basic_auth_header_is_built_from_credentials(loader):
    assert loader._auth_headers()["Authorization"].startswith("Basic ")


def test_bearer_is_used_when_configured():
    loader = XWikiLoader(
        config=XWikiConfig(url="http://x", token="t", use_bearer=True), space="KB"
    )
    assert loader._auth_headers()["Authorization"] == "Bearer t"


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failures_raise_unauthorized(loader, status_code):
    with patch("httpx.Client.get", return_value=_response(status_code=status_code)):
        with pytest.raises(UnauthorizedException):
            loader._get_json("/rest/wikis")


def test_unexpected_status_raises_connection_error_naming_the_url_field(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=404, text="Not Found")):
        with pytest.raises(ConnectionException) as exc:
            loader._get_json("/rest/wikis")
    assert "/xwiki" in str(exc.value)  # the base-URL prefix hint


def test_html_body_on_200_raises_connection_error(loader):
    with patch("httpx.Client.get", return_value=_response(raise_on_json=True, text="<html>")):
        with pytest.raises(ConnectionException):
            loader._get_json("/rest/wikis")


def test_get_text_returns_none_on_non_200_instead_of_raising(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=302, text="")):
        assert loader._get_text("/bin/get/KB/Formatting") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_errors.py -v`
Expected: FAIL — `AttributeError: 'XWikiLoader' object has no attribute '_auth_headers'`.

- [ ] **Step 3: Implement the HTTP layer**

Add the imports to `xwiki_loader.py`:

```python
import base64

import httpx

from codemie.datasource.exceptions import ConnectionException, UnauthorizedException
```

and the methods. `_get_json` raises; `_get_text` is deliberately forgiving because `/bin` is a fallback-capable path (Task 7).

```python
    _BASE_URL_HINT = (
        "Check the xWiki base URL: a 404 on /rest usually means the URL is missing "
        "the /xwiki prefix, or carries one the instance does not use."
    )

    def _auth_headers(self) -> dict[str, str]:
        if self.config.use_bearer:
            return {"Authorization": f"Bearer {self.config.token}"}
        raw = f"{self.config.username or ''}:{self.config.token}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    def _get(self, path: str, params: dict | None = None, accept: str = "application/json"):
        headers = {**self._auth_headers(), "Accept": accept}
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                return client.get(f"{self._base}{path}", params=params, headers=headers)
        except httpx.RequestError as e:
            raise ConnectionException(DATASOURCE_TYPE, f"{e}. {self._BASE_URL_HINT}") from e

    def _get_json(self, path: str, params: dict | None = None) -> dict[str, Any]:
        response = self._get(path, params)
        if response.status_code in (401, 403):
            raise UnauthorizedException(datasource_type=DATASOURCE_TYPE)
        if response.status_code != 200:
            raise ConnectionException(
                DATASOURCE_TYPE, f"HTTP {response.status_code} for {path}. {self._BASE_URL_HINT}"
            )
        try:
            return response.json()
        except ValueError as e:
            raise ConnectionException(
                DATASOURCE_TYPE, f"Expected JSON from {path} but got a non-JSON body. {self._BASE_URL_HINT}"
            ) from e

    def _get_text(self, path: str, params: dict | None = None) -> str | None:
        """Fetch rendered HTML. Returns None when unavailable — the caller falls back to raw."""
        response = self._get(path, params, accept="text/html")
        if response.status_code != 200 or not response.text.strip():
            return None
        return response.text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_errors.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/datasource/loader/xwiki_loader.py tests/codemie/datasource/xwiki/test_xwiki_loader_errors.py
git commit -m "EPMCDME-13142: Add xWiki HTTP layer with typed error mapping"
```

---

## Task 6: Paginated space and page discovery

**Files:**
- Modify: `src/codemie/datasource/loader/xwiki_loader.py`
- Test: `tests/codemie/datasource/xwiki/test_xwiki_loader_discovery.py`

**Interfaces:**
- Consumes: Task 5's `_get_json`
- Produces: `XWikiLoader._paginate(path, result_key) -> Iterator[dict]` · `XWikiLoader._iter_spaces() -> Iterator[str]` · `XWikiLoader._iter_page_summaries(space_id) -> Iterator[dict]`

**Test-first: yes** — tests asserting the space listing is paged and that descendants are included while sibling spaces are not, failing with `AttributeError: '_iter_spaces'`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/test_xwiki_loader_discovery.py`:

```python
from unittest.mock import patch

import pytest

from codemie.datasource.loader.xwiki_loader import XWikiLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
        page_size=2,
    )


def _space(space_id):
    return {"id": space_id}


def test_only_the_target_space_and_its_descendants_are_kept(loader):
    payload = {
        "spaces": [
            _space("xwiki:KB"),
            _space("xwiki:KB.Onboarding"),
            _space("xwiki:KBase"),      # prefix lookalike, must NOT match
            _space("xwiki:Main"),
        ]
    }
    with patch.object(loader, "_get_json", side_effect=[payload, {"spaces": []}]):
        assert list(loader._iter_spaces()) == ["KB", "KB.Onboarding"]


def test_space_listing_is_paginated(loader):
    first = {"spaces": [_space("xwiki:KB"), _space("xwiki:KB.A")]}
    second = {"spaces": [_space("xwiki:KB.B")]}
    with patch.object(loader, "_get_json", side_effect=[first, second]) as get_json:
        assert list(loader._iter_spaces()) == ["KB", "KB.A", "KB.B"]
    assert get_json.call_count == 2
    assert get_json.call_args_list[1].kwargs["params"]["start"] == 2


def test_page_listing_stops_on_a_short_page(loader):
    first = {"pageSummaries": [{"name": "A"}, {"name": "B"}]}
    second = {"pageSummaries": [{"name": "C"}]}
    with patch.object(loader, "_get_json", side_effect=[first, second]) as get_json:
        names = [p["name"] for p in loader._iter_page_summaries("KB")]
    assert names == ["A", "B", "C"]
    assert get_json.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_discovery.py -v`
Expected: FAIL — `AttributeError: 'XWikiLoader' object has no attribute '_iter_spaces'`.

- [ ] **Step 3: Implement discovery**

```python
    def _paginate(self, path: str, result_key: str) -> Iterator[dict[str, Any]]:
        """Offset pagination. xWiki sends no total and no next link, so a short page ends it."""
        start = 0
        while True:
            payload = self._get_json(path, params={"number": self.page_size, "start": start})
            items = payload.get(result_key) or []
            for item in items:
                yield item
            if len(items) < self.page_size:
                return
            start += self.page_size

    def _iter_spaces(self) -> Iterator[str]:
        """Yield the target space and every descendant, as dotted ids without the wiki prefix."""
        prefix = f"{self.wiki}:"
        target = self.space
        for item in self._paginate(f"/rest/wikis/{quote(self.wiki, safe='')}/spaces", "spaces"):
            space_id = item.get("id") or ""
            if not space_id.startswith(prefix):
                continue
            dotted = space_id[len(prefix):]
            if dotted == target or dotted.startswith(f"{target}."):
                yield dotted

    def _iter_page_summaries(self, space_id: str) -> Iterator[dict[str, Any]]:
        path = f"/rest/wikis/{quote(self.wiki, safe='')}{self._rest_space_path(space_id)}/pages"
        yield from self._paginate(path, "pageSummaries")
```

> Note the `KBase` case in the test: matching must be `== target` or `startswith(target + ".")`, never a bare `startswith(target)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_discovery.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/datasource/loader/xwiki_loader.py tests/codemie/datasource/xwiki/test_xwiki_loader_discovery.py
git commit -m "EPMCDME-13142: Add paginated xWiki space and page discovery"
```

---

## Task 7: Content resolution, document building and load stats

**Files:**
- Modify: `src/codemie/datasource/loader/xwiki_loader.py`
- Test: `tests/codemie/datasource/xwiki/test_xwiki_loader_load.py`

**Interfaces:**
- Consumes: Tasks 5–6
- Produces: `XWikiLoader._resolve_content(space_id, page) -> tuple[str, str]` · `XWikiLoader._to_document(space_id, page) -> Document` · `XWikiLoader.lazy_load() -> Iterator[Document]` · `XWikiLoader.fetch_remote_stats() -> dict` · `XWikiLoader.get_load_stats() -> dict`

**Test-first: yes** — tests asserting rendered content is markdownified, that a failed `/bin` fetch falls back to raw with `content_format="raw"`, that empty pages are skipped, and that `fetch_remote_stats` propagates instead of returning 0.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/test_xwiki_loader_load.py`:

```python
from unittest.mock import patch

import pytest

from codemie.datasource.exceptions import ConnectionException
from codemie.datasource.loader.xwiki_loader import (
    CONTENT_FORMAT_RAW,
    CONTENT_FORMAT_RENDERED,
    METADATA_CONTENT_FORMAT,
    METADATA_MODIFIED,
    METADATA_SOURCE,
    XWikiLoader,
)
from codemie_tools.core.project_management.xwiki.models import XWikiConfig

PAGE = {
    "id": "xwiki:KB.Formatting",
    "name": "Formatting",
    "title": "Formatting",
    "content": "= Heading =\n\nSome **bold** text",
    "version": "1.1",
    "modified": 1785836295000,
    "author": "XWiki.AdminAdmin",
    "xwikiAbsoluteUrl": "http://xwiki:8080/bin/view/KB/Formatting",
}


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
    )


def test_rendered_html_is_converted_to_markdown(loader):
    with patch.object(loader, "_get_text", return_value="<h1>Heading</h1><p>Some <strong>bold</strong> text</p>"):
        text, fmt = loader._resolve_content("KB", PAGE)
    assert fmt == CONTENT_FORMAT_RENDERED
    assert "# Heading" in text
    assert "**bold**" in text


def test_falls_back_to_raw_when_rendering_is_unavailable(loader):
    with patch.object(loader, "_get_text", return_value=None):
        text, fmt = loader._resolve_content("KB", PAGE)
    assert fmt == CONTENT_FORMAT_RAW
    assert text == PAGE["content"]


def test_document_carries_all_nine_metadata_keys(loader):
    with patch.object(loader, "_get_text", return_value=None):
        doc = loader._to_document("KB", PAGE)
    assert doc.metadata[METADATA_SOURCE] == PAGE["xwikiAbsoluteUrl"]
    assert doc.metadata[METADATA_MODIFIED] == 1785836295000
    assert doc.metadata[METADATA_CONTENT_FORMAT] == CONTENT_FORMAT_RAW
    for key in ("page_id", "space", "wiki", "title", "version", "author"):
        assert key in doc.metadata


def test_empty_pages_are_skipped_and_counted(loader):
    empty = {**PAGE, "content": "", "name": "Empty"}
    with patch.object(loader, "_iter_spaces", return_value=iter(["KB"])), \
         patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "Empty"}])), \
         patch.object(loader, "_fetch_page", return_value=empty), \
         patch.object(loader, "_get_text", return_value=None):
        docs = list(loader.lazy_load())
    assert docs == []
    assert loader.get_load_stats()[XWikiLoader.SKIPPED_DOCUMENTS_KEY] == 1


def test_a_single_failing_page_does_not_kill_the_run(loader):
    with patch.object(loader, "_iter_spaces", return_value=iter(["KB"])), \
         patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "A"}, {"name": "B"}])), \
         patch.object(loader, "_fetch_page", side_effect=[ConnectionException("xWiki", "boom"), PAGE]), \
         patch.object(loader, "_get_text", return_value=None):
        docs = list(loader.lazy_load())
    assert len(docs) == 1
    assert loader.get_load_stats()[XWikiLoader.FAILED_DOCUMENTS_KEY] == 1


def test_failure_rate_above_the_threshold_fails_the_run(loader):
    loader.max_failed_pages_floor = 1
    loader.max_failed_pages_ratio = 0.1
    summaries = [{"name": f"P{i}"} for i in range(4)]
    with patch.object(loader, "_iter_spaces", return_value=iter(["KB"])), \
         patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)), \
         patch.object(loader, "_fetch_page", side_effect=ConnectionException("xWiki", "boom")):
        with pytest.raises(ConnectionException):
            list(loader.lazy_load())


def test_max_pages_marks_the_result_truncated(loader):
    loader.max_pages = 1
    summaries = [{"name": "A"}, {"name": "B"}]
    with patch.object(loader, "_iter_spaces", return_value=iter(["KB"])), \
         patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)), \
         patch.object(loader, "_fetch_page", return_value=PAGE), \
         patch.object(loader, "_get_text", return_value=None):
        docs = list(loader.lazy_load())
    assert len(docs) == 1
    assert loader.get_load_stats()["truncated"] is True


def test_fetch_remote_stats_propagates_instead_of_reporting_zero(loader):
    with patch.object(loader, "_iter_spaces", side_effect=ConnectionException("xWiki", "boom")):
        with pytest.raises(ConnectionException):
            loader.fetch_remote_stats()


def test_fetch_remote_stats_counts_pages_across_descendant_spaces(loader):
    with patch.object(loader, "_iter_spaces", return_value=iter(["KB", "KB.Onboarding"])), \
         patch.object(loader, "_iter_page_summaries", side_effect=[
             iter([{"name": "A"}, {"name": "B"}]), iter([{"name": "C"}])
         ]):
        stats = loader.fetch_remote_stats()
    assert stats[XWikiLoader.DOCUMENTS_COUNT_KEY] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_load.py -v`
Expected: FAIL — `AttributeError: 'XWikiLoader' object has no attribute '_resolve_content'`.

- [ ] **Step 3: Implement content resolution and loading**

Add the import:

```python
from markdownify import markdownify
```

and the methods:

```python
    def _fetch_page(self, space_id: str, name: str) -> dict[str, Any]:
        """Page summaries carry neither content nor modified, so a per-page GET is mandatory."""
        path = (
            f"/rest/wikis/{quote(self.wiki, safe='')}"
            f"{self._rest_space_path(space_id)}/pages/{quote(name, safe='')}"
        )
        return self._get_json(path)

    def _resolve_content(self, space_id: str, page: dict[str, Any]) -> tuple[str, str]:
        """Prefer rendered markdown; fall back to raw wiki syntax. Never fail on rendering."""
        html = self._get_text(self._bin_page_path(space_id, page.get("name") or ""), params={"xpage": "plain"})
        if html:
            return markdownify(html, heading_style="ATX").strip(), CONTENT_FORMAT_RENDERED
        logger.warning(
            "xWiki rendered content unavailable for %s in space %s; using raw wiki syntax",
            page.get("name"),
            space_id,
        )
        return (page.get("content") or "").strip(), CONTENT_FORMAT_RAW

    def _to_document(self, space_id: str, page: dict[str, Any]) -> Document:
        text, content_format = self._resolve_content(space_id, page)
        return Document(
            page_content=text,
            metadata={
                METADATA_SOURCE: page.get("xwikiAbsoluteUrl"),
                METADATA_PAGE_ID: page.get("id"),
                METADATA_SPACE: space_id,
                METADATA_WIKI: self.wiki,
                METADATA_TITLE: page.get("title"),
                METADATA_MODIFIED: page.get("modified"),
                METADATA_VERSION: page.get("version"),
                METADATA_AUTHOR: page.get("author"),
                METADATA_CONTENT_FORMAT: content_format,
            },
        )

    def _check_failure_budget(self, seen: int) -> None:
        budget = max(self.max_failed_pages_floor, int(self.max_failed_pages_ratio * seen))
        if self._failed > budget:
            raise ConnectionException(
                DATASOURCE_TYPE,
                f"{self._failed} of {seen} pages failed to load, above the allowed {budget}. "
                "Refusing to index a partial space as a success.",
            )

    def lazy_load(self) -> Iterator[Document]:
        self._skipped = self._failed = 0
        self._truncated = False
        seen = emitted = 0

        for space_id in self._iter_spaces():
            for summary in self._iter_page_summaries(space_id):
                if emitted >= self.max_pages:
                    self._truncated = True
                    logger.warning(
                        "xWiki space %s truncated at loader_max_pages=%s; some pages are not indexed",
                        self.space,
                        self.max_pages,
                    )
                    return
                seen += 1
                try:
                    page = self._fetch_page(space_id, summary.get("name") or "")
                    document = self._to_document(space_id, page)
                except UnauthorizedException:
                    raise
                except Exception as e:  # noqa: BLE001 - one bad page must not kill the crawl
                    self._failed += 1
                    logger.warning("Failed to load xWiki page %s in %s: %s", summary.get("name"), space_id, e)
                    self._check_failure_budget(seen)
                    continue

                if not document.page_content:
                    self._skipped += 1
                    continue

                emitted += 1
                yield document

    def fetch_remote_stats(self) -> dict[str, Any]:
        """xWiki returns no total-count field anywhere, so this enumerates. Never swallows errors."""
        count = 0
        for space_id in self._iter_spaces():
            for _ in self._iter_page_summaries(space_id):
                count += 1
                if count >= self.max_pages:
                    self._truncated = True
                    return {self.DOCUMENTS_COUNT_KEY: count, self.TOTAL_DOCUMENTS_KEY: count, "truncated": True}
        return {self.DOCUMENTS_COUNT_KEY: count, self.TOTAL_DOCUMENTS_KEY: count, "truncated": False}

    def get_load_stats(self) -> dict[str, Any]:
        return {
            self.SKIPPED_DOCUMENTS_KEY: self._skipped,
            self.FAILED_DOCUMENTS_KEY: self._failed,
            "truncated": self._truncated,
        }
```

> `UnauthorizedException` is re-raised rather than counted: a 403 is a configuration problem for the whole crawl, not one bad page.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_loader_load.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the whole loader suite**

Run: `poetry run pytest tests/codemie/datasource/xwiki/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/datasource/loader/xwiki_loader.py tests/codemie/datasource/xwiki/test_xwiki_loader_load.py
git commit -m "EPMCDME-13142: Add xWiki content resolution, document building and load stats"
```

---

## Task 8: `XWikiDatasourceProcessor`

**Files:**
- Create: `src/codemie/datasource/xwiki/__init__.py`, `src/codemie/datasource/xwiki/xwiki_datasource_processor.py`
- Test: `tests/codemie/datasource/xwiki/test_xwiki_datasource_processor.py`

**Interfaces:**
- Consumes: Task 2's `XWIKI_CONFIG`, Task 3's `XWikiIndexInfo`, Task 7's `XWikiLoader`
- Produces: `XWikiDatasourceProcessor(datasource_name, user, project_name, credentials, space, wiki="xwiki", description="", project_space_visible=False, index_info=None, callbacks=None, request_uuid=None, guardrail_assignments=None, **kwargs)` · `INDEX_TYPE = "knowledge_base_xwiki"` · `_check_docs_health() -> int`

**Test-first: yes** — tests asserting `_index_name`, that `_process_chunk` carries all nine metadata keys, and that `_check_docs_health` propagates rather than returning 0.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/datasource/xwiki/test_xwiki_datasource_processor.py`:

```python
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from codemie.core.models import CreatedByUser
from codemie.datasource.exceptions import ConnectionException
from codemie.datasource.loader.xwiki_loader import (
    METADATA_AUTHOR, METADATA_CONTENT_FORMAT, METADATA_MODIFIED, METADATA_PAGE_ID,
    METADATA_SOURCE, METADATA_SPACE, METADATA_TITLE, METADATA_VERSION, METADATA_WIKI,
)
from codemie.datasource.xwiki.xwiki_datasource_processor import XWikiDatasourceProcessor
from codemie.rest_api.models.index import XWikiIndexInfo
from codemie.rest_api.security.user import User
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def processor():
    index_info = MagicMock()
    index_info.index_type = "knowledge_base_xwiki"
    index_info.created_by = CreatedByUser(id="1", username="testuser")
    index_info.xwiki = XWikiIndexInfo(space="KB", wiki="xwiki")
    index_info.update_date = datetime.now()
    return XWikiDatasourceProcessor(
        datasource_name="kb_ds",
        user=User(id="1", username="testuser"),
        project_name="test_project",
        credentials=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
        index_info=index_info,
        setting_id="setting_id",
    )


def test_index_name_combines_project_and_datasource(processor):
    assert processor._index_name == "test_project-kb_ds"


def test_index_type_constant(processor):
    assert processor.INDEX_TYPE == "knowledge_base_xwiki"


def test_process_chunk_preserves_every_metadata_key(processor):
    metadata = {
        METADATA_SOURCE: "http://xwiki:8080/bin/view/KB/Formatting",
        METADATA_PAGE_ID: "xwiki:KB.Formatting",
        METADATA_SPACE: "KB",
        METADATA_WIKI: "xwiki",
        METADATA_TITLE: "Formatting",
        METADATA_MODIFIED: 1785836295000,
        METADATA_VERSION: "1.1",
        METADATA_AUTHOR: "XWiki.AdminAdmin",
        METADATA_CONTENT_FORMAT: "rendered",
        "chunk_num": 2,
    }
    result = processor._process_chunk("chunk text", metadata, Document(page_content="x"))
    assert result.page_content == "chunk text"
    for key in metadata:
        if key != "chunk_num":
            assert result.metadata[key] == metadata[key]


def test_splitter_uses_the_xwiki_chunk_settings(processor):
    from codemie.datasource.datasources_config import XWIKI_CONFIG

    splitter = XWikiDatasourceProcessor._get_splitter()
    assert splitter._chunk_size == XWIKI_CONFIG.chunk_size


def test_check_docs_health_returns_the_remote_count(processor):
    loader = MagicMock()
    loader.fetch_remote_stats.return_value = {"documents_count_key": 7}
    with patch.object(processor, "_init_loader", return_value=loader):
        assert processor._check_docs_health() == 7


def test_check_docs_health_propagates_errors_instead_of_reporting_zero(processor):
    loader = MagicMock()
    loader.fetch_remote_stats.side_effect = ConnectionException("xWiki", "boom")
    with patch.object(processor, "_init_loader", return_value=loader):
        with pytest.raises(ConnectionException):
            processor._check_docs_health()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_datasource_processor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.datasource.xwiki'`.

- [ ] **Step 3: Implement the processor**

Create `src/codemie/datasource/xwiki/__init__.py` (empty) and `src/codemie/datasource/xwiki/xwiki_datasource_processor.py`. Copy the Apache-2.0 header, then mirror `azure_devops_wiki_datasource_processor.py` minus the vision/attachment wiring:

```python
import logging
from typing import Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.core.dependecies import llm_service
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor
from codemie.datasource.datasources_config import XWIKI_CONFIG
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.xwiki_loader import (
    METADATA_AUTHOR, METADATA_CONTENT_FORMAT, METADATA_MODIFIED, METADATA_PAGE_ID,
    METADATA_SOURCE, METADATA_SPACE, METADATA_TITLE, METADATA_VERSION, METADATA_WIKI,
    XWikiLoader,
)
from codemie.rest_api.models.index import IndexInfo, XWikiIndexInfo
from codemie_tools.core.project_management.xwiki.models import XWikiConfig

logger = logging.getLogger(__name__)

_CHUNK_METADATA_KEYS = (
    METADATA_SOURCE, METADATA_PAGE_ID, METADATA_SPACE, METADATA_WIKI, METADATA_TITLE,
    METADATA_MODIFIED, METADATA_VERSION, METADATA_AUTHOR, METADATA_CONTENT_FORMAT,
)


class XWikiDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_xwiki"

    def __init__(
        self,
        *,
        datasource_name: str,
        user,
        project_name: str,
        credentials: XWikiConfig,
        space: str,
        wiki: str = "xwiki",
        description: str = "",
        project_space_visible: bool = False,
        index_info: Optional[IndexInfo] = None,
        callbacks=None,
        request_uuid: Optional[str] = None,
        guardrail_assignments=None,
        **kwargs,
    ):
        self.project_name = project_name
        self.description = description
        self.credentials = credentials
        self.space = space
        self.wiki = wiki
        self.project_space_visible = project_space_visible
        self.setting_id = kwargs.get("setting_id")
        self.embedding_model = kwargs.get("embedding_model")
        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index_info,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
            cron_expression=kwargs.get("cron_expression"),
        )

    @property
    def _index_name(self) -> str:
        return KnowledgeBase(
            name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE
        ).get_identifier()

    @property
    def _processing_batch_size(self) -> int:
        return XWIKI_CONFIG.loader_batch_size

    def _init_index(self):
        if not self.index:
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                xwiki=XWikiIndexInfo(space=self.space, wiki=self.wiki),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )
        self._assign_and_sync_guardrails()

    def _init_loader(self) -> BaseDatasourceLoader:
        return XWikiLoader(
            config=self.credentials,
            space=self.space,
            wiki=self.wiki,
            page_size=XWIKI_CONFIG.loader_batch_size,
            max_pages=XWIKI_CONFIG.loader_max_pages,
            timeout_seconds=XWIKI_CONFIG.request_timeout_seconds,
            max_failed_pages_floor=XWIKI_CONFIG.max_failed_pages_floor,
            max_failed_pages_ratio=XWIKI_CONFIG.max_failed_pages_ratio,
        )

    def _process_chunk(self, chunk: str, chunk_metadata: dict, _document: Document) -> Document:
        metadata = {key: chunk_metadata.get(key) for key in _CHUNK_METADATA_KEYS}
        return Document(page_content=chunk, metadata=metadata)

    @classmethod
    def _get_splitter(cls, document=None) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=XWIKI_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=XWIKI_CONFIG.chunk_overlap,
        )

    def _check_docs_health(self) -> int:
        loader = self._init_loader()
        return loader.fetch_remote_stats().get(XWikiLoader.DOCUMENTS_COUNT_KEY, 0)
```

> `_check_docs_health` deliberately does **not** wrap in `try/except`. The donor swallows every exception and reports 0 documents; review gate #3 requires the error to reach the user.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/datasource/xwiki/test_xwiki_datasource_processor.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/datasource/xwiki/ tests/codemie/datasource/xwiki/test_xwiki_datasource_processor.py
git commit -m "EPMCDME-13142: Add XWikiDatasourceProcessor"
```

---

## Task 9: Credential accessor and Test-connection handler

**Files:**
- Modify: `src/codemie/service/settings/settings.py` (new `get_xwiki_creds`, beside `get_confluence_creds` at `:1145`)
- Modify: `src/codemie/service/settings/settings_tester.py:80-97` (handlers dict) and a new `_test_xwiki`
- Test: `tests/codemie/service/settings/test_xwiki_settings.py`

**Interfaces:**
- Consumes: `XWikiConfig` (already mapped to `CredentialTypes.XWIKI` at `settings.py:224`)
- Produces: `SettingsService.get_xwiki_creds(user_id, project_name, assistant_id=None, setting_id=None, tool_config=None) -> XWikiConfig` · `SettingsTester._test_xwiki() -> Tuple[bool, str]`

**Test-first: yes** — a test asserting `SettingsTester` resolves a handler for `CredentialTypes.XWIKI`, failing today with `SettingsTesterHandlerNotFound`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/service/settings/test_xwiki_settings.py`:

```python
from unittest.mock import MagicMock, patch

from codemie.service.settings.settings import SettingsService
from codemie.service.settings.settings_tester import SettingsTester
from codemie_tools.base.models import CredentialTypes


def _tester():
    tester = SettingsTester.__new__(SettingsTester)
    tester.credential_type = CredentialTypes.XWIKI
    tester.credential_values = {"url": "http://xwiki:8080", "username": "AdminAdmin", "token": "admin"}
    return tester


def test_a_handler_is_registered_for_xwiki():
    assert CredentialTypes.XWIKI in _tester().handlers


def test_success_message_does_not_claim_the_password_is_valid():
    tester = _tester()
    with patch(
        "codemie.service.settings.settings_tester.ListWikisTool"
    ) as tool:
        tool.return_value.healthcheck.return_value = (True, "ok")
        ok, message = tester.test()
    assert ok is True
    assert "credentials accepted" in message.lower()
    assert "valid password" not in message.lower()


def test_get_xwiki_creds_resolves_through_get_config():
    with patch.object(SettingsService, "get_config", return_value=MagicMock()) as get_config:
        SettingsService.get_xwiki_creds(user_id="u1", project_name="p1")
    assert get_config.call_args.kwargs["user_id"] == "u1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/settings/test_xwiki_settings.py -v`
Expected: FAIL — `AttributeError: type object 'SettingsService' has no attribute 'get_xwiki_creds'`.

- [ ] **Step 3: Add the credential accessor**

`src/codemie/service/settings/settings.py`, after `get_confluence_creds`:

```python
    @classmethod
    def get_xwiki_creds(
        cls,
        user_id: str,
        project_name: str,
        assistant_id: Optional[str] = None,
        setting_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> XWikiConfig:
        return cls.get_config(
            config_class=XWikiConfig,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            integration_id=setting_id,
            tool_config=tool_config,
        )
```

Import `XWikiConfig` from `codemie_tools.core.project_management.xwiki.models` if it is not already imported.

- [ ] **Step 4: Register the Test-connection handler**

`src/codemie/service/settings/settings_tester.py` — add the import for `ListWikisTool` and `XWikiConfig`, add to the `handlers` dict:

```python
            CredentialTypes.XWIKI: SettingsTester._test_xwiki,
```

and the method. The message must state what was actually verified: at credential-configuration time there is no target space yet, and xWiki has no "current user" REST resource, so on a guest-readable wiki a wrong password still returns 200.

```python
    def _test_xwiki(self) -> Tuple[bool, str]:
        ok, message = ListWikisTool(config=XWikiConfig(**self.credential_values)).healthcheck()
        if not ok:
            return ok, message
        return True, (
            "xWiki instance reachable, credentials accepted. Note: xWiki grants guest read access "
            "by default, so this check cannot confirm the password is correct — the datasource "
            "health check reports the real page count for the space you connect."
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/settings/test_xwiki_settings.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/settings/ tests/codemie/service/settings/test_xwiki_settings.py
git commit -m "EPMCDME-13142: Add xWiki credential accessor and Test-connection handler"
```

---

## Task 10: Datasource health check

**Files:**
- Modify: `src/codemie/service/index/datasource_health_check_service.py` — `:49` (match arm), `:142` area (new classmethod), `:237` (`get_invalid_field`)
- Modify: `src/codemie/rest_api/models/index.py` — `DatasourceHealthCheckRequest` gains optional `space` and `wiki`
- Test: `tests/codemie/service/index/test_xwiki_health_check.py`

**Interfaces:**
- Consumes: Task 8's processor, Task 9's `get_xwiki_creds`
- Produces: `IndexHealthCheckService.health_check_xwiki(request, user_id) -> DatasourceHealthCheckResponse`

**Test-first: yes** — tests asserting the real page count is returned, that a `ConnectionException` becomes an `ErrorMessage`, and that hitting the cap reports truncation rather than health.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/service/index/test_xwiki_health_check.py`:

```python
from unittest.mock import MagicMock, patch

from codemie.core.constants import DatasourceTypes
from codemie.datasource.exceptions import ConnectionException
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService


def _request(space="KB"):
    request = MagicMock()
    request.index_type = DatasourceTypes.XWIKI
    request.project_name = "p1"
    request.space = space
    request.wiki = "xwiki"
    return request


def test_returns_the_real_page_count():
    processor = MagicMock()
    processor._check_docs_health.return_value = 6
    with patch("codemie.service.index.datasource_health_check_service.SettingsService.get_xwiki_creds"), \
         patch(
             "codemie.service.index.datasource_health_check_service.XWikiDatasourceProcessor",
             return_value=processor,
         ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    assert response.documents_count == 6
    assert response.error is None


def test_connection_failure_becomes_a_field_scoped_error():
    with patch("codemie.service.index.datasource_health_check_service.SettingsService.get_xwiki_creds"), \
         patch(
             "codemie.service.index.datasource_health_check_service.XWikiDatasourceProcessor",
             side_effect=ConnectionException("xWiki", "HTTP 404 for /rest/wikis"),
         ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    assert response.error is not None
    assert "404" in response.error.details


def test_missing_space_is_rejected_with_a_field_error():
    response = IndexHealthCheckService.health_check_datasource(_request(space=""), "u1")
    assert response.error.field_error == "space"


def test_invalid_field_for_xwiki_is_space():
    assert IndexHealthCheckService.get_invalid_field(DatasourceTypes.XWIKI) == "space"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/index/test_xwiki_health_check.py -v`
Expected: FAIL — the match falls through to `DatasourceHealthCheckResponse(implemented=False)`, so `documents_count` is `None`.

- [ ] **Step 3: Add the optional request fields**

`src/codemie/rest_api/models/index.py`, on `DatasourceHealthCheckRequest` (beside `svn_repo_url`, `git_url`):

```python
    space: Optional[str] = None
    wiki: Optional[str] = "xwiki"
```

- [ ] **Step 4: Add the health-check arm**

`src/codemie/service/index/datasource_health_check_service.py` — import `XWikiDatasourceProcessor`, add the arm after `case DatasourceTypes.AZURE_DEVOPS_WORK_ITEM:`:

```python
                case DatasourceTypes.XWIKI:
                    return cls.health_check_xwiki(request, user_id)
```

and the classmethod:

```python
    @classmethod
    def health_check_xwiki(cls, request: DatasourceHealthCheckRequest, user_id: str):
        if not request.space:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message="xWiki space is required",
                    details="Provide the dotted space id shown in the page URL, for example 'KB'.",
                    help="Open the space in xWiki; the id appears in the URL after /bin/view/.",
                    field_error="space",
                )
            )

        xwiki_creds = SettingsService.get_xwiki_creds(
            user_id=user_id,
            project_name=request.project_name,
        )

        processor = XWikiDatasourceProcessor(
            datasource_name="health_check",
            user=None,  # Not needed for health check
            project_name=request.project_name,
            credentials=xwiki_creds,
            space=request.space,
            wiki=request.wiki or "xwiki",
        )

        documents_count = processor._check_docs_health()
        loader_truncated = processor._init_loader().get_load_stats().get("truncated", False)
        if loader_truncated:
            return DatasourceHealthCheckResponse(
                documents_count=documents_count,
                error=ErrorMessage(
                    message=f"Space contains more than {documents_count} pages; only the first "
                            f"{documents_count} will be indexed.",
                    details="The loader page cap was reached while counting pages.",
                    help="Connect a narrower space, or raise loader_max_pages for the xWiki loader.",
                    field_error="space",
                ),
            )
        return DatasourceHealthCheckResponse(documents_count=documents_count)
```

In `get_invalid_field`, after the `AZURE_DEVOPS_WORK_ITEM` arm:

```python
            case DatasourceTypes.XWIKI:
                return "space"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/index/test_xwiki_health_check.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/index/datasource_health_check_service.py src/codemie/rest_api/models/index.py \
        tests/codemie/service/index/test_xwiki_health_check.py
git commit -m "EPMCDME-13142: Add xWiki datasource health check"
```

---

## Task 11: REST create and update endpoints

**Files:**
- Modify: `src/codemie/rest_api/models/index.py` — `:1441` area (`IndexKnowledgeBaseXWikiRequest`), `:1622` area (`UpdateKnowledgeBaseXWikiRequest`)
- Modify: `src/codemie/rest_api/routers/index.py` — imports at `:69`/`:89`/`:104`, `POST` after `:1174`, `PUT` after `:1775`
- Modify: `src/codemie/service/settings/settings_request_validator.py:56`
- Test: `tests/codemie/rest_api/test_xwiki_index_endpoints.py`

**Interfaces:**
- Consumes: Tasks 3, 8, 9
- Produces: `POST /v1/index/knowledge_base/xwiki` · `PUT /v1/index/knowledge_base/xwiki`

**Test-first: yes** — a test posting a valid create payload and asserting the processor is scheduled, plus a test asserting an empty `space` is a 422.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/rest_api/test_xwiki_index_endpoints.py`:

```python
import pytest
from pydantic import ValidationError

from codemie.rest_api.models.index import (
    IndexKnowledgeBaseXWikiRequest,
    UpdateKnowledgeBaseXWikiRequest,
)
from codemie.service.settings.settings_request_validator import (
    UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES,
)


def test_create_request_defaults_the_wiki():
    request = IndexKnowledgeBaseXWikiRequest(
        name="kb-xwiki", project_name="p1", description="d", space="KB"
    )
    assert request.wiki == "xwiki"


def test_create_request_rejects_a_blank_space():
    with pytest.raises(ValidationError):
        IndexKnowledgeBaseXWikiRequest(
            name="kb-xwiki", project_name="p1", description="d", space="   "
        )


def test_update_request_accepts_a_new_space():
    request = UpdateKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1", space="KB.Onboarding")
    assert request.space == "KB.Onboarding"


def test_xwiki_has_no_webhook_support():
    assert "knowledge_base_xwiki" in UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/test_xwiki_index_endpoints.py -v`
Expected: FAIL — `ImportError: cannot import name 'IndexKnowledgeBaseXWikiRequest'`.

- [ ] **Step 3: Add the request models**

`src/codemie/rest_api/models/index.py`, after `IndexKnowledgeBaseAzureDevOpsWikiRequest`:

```python
class IndexKnowledgeBaseXWikiRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    space: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    wiki: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = "xwiki"
    setting_id: Optional[str] = None
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None
```

after `UpdateKnowledgeBaseAzureDevOpsWikiRequest`:

```python
class UpdateKnowledgeBaseXWikiRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    space: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = None
    wiki: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = None
    project_name: str
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change

    description: str = Field(default="", max_length=500)
    project_space_visible: Optional[bool] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None
```

- [ ] **Step 4: Add the endpoints**

`src/codemie/rest_api/routers/index.py` — add imports for `XWikiDatasourceProcessor`, `IndexKnowledgeBaseXWikiRequest`, `UpdateKnowledgeBaseXWikiRequest`, then the create endpoint mirroring `:1145`:

```python
@router.post("/index/knowledge_base/xwiki", status_code=status.HTTP_200_OK)
def index_knowledge_base_xwiki(
    request: IndexKnowledgeBaseXWikiRequest, raw_request: Request, background_tasks: BackgroundTasks
):
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    xwiki_creds = SettingsService.get_xwiki_creds(
        user_id=raw_request.state.user.id,
        project_name=request.project_name,
    )

    datasource_processor = XWikiDatasourceProcessor(
        datasource_name=request.name,
        user=raw_request.state.user,
        project_name=request.project_name,
        credentials=xwiki_creds,
        space=request.space,
        wiki=request.wiki,
        description=request.description,
        project_space_visible=request.project_space_visible,
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")
```

and the update endpoint mirroring `:1695`. Note `update_index` receives `space`/`wiki` through `**kwargs`, which Task 3's `_update_xwiki_fields` consumes:

```python
@router.put("/index/knowledge_base/xwiki", status_code=status.HTTP_200_OK)
def update_knowledge_base_xwiki(
    request: UpdateKnowledgeBaseXWikiRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
    user: User = Depends(authenticate),
):
    cron_expression_provided = "cron_expression" in request.model_fields_set

    kb_search_results = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
        project_name=request.project_name,
        repo_name=request.name,
    )
    if not kb_search_results:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with name '{request.name}' in project '{request.project_name}' could not be found.",
            help=INDEX_NOT_FOUND_HELP,
        )

    kb_index = kb_search_results[0]
    _validate_project_change(request.new_project_name, request.project_name, request.name, user)

    kb_index.update_index(
        user=user,
        description=request.description,
        project_space_visible=request.project_space_visible,
        space=request.space,
        wiki=request.wiki,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )

    if not full_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    xwiki_creds = SettingsService.get_xwiki_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id or kb_index.setting_id,
    )
    project_space_visible = (
        request.project_space_visible if request.project_space_visible is not None else kb_index.project_space_visible
    )

    datasource_processor = XWikiDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=xwiki_creds,
        space=request.space or kb_index.xwiki.space,
        wiki=request.wiki or kb_index.xwiki.wiki,
        description=request.description,
        project_space_visible=project_space_visible,
        setting_id=request.setting_id,
        index_info=kb_index,
        request_uuid=raw_request.state.uuid,
        cron_expression=request.cron_expression if cron_expression_provided else None,
    )

    logger.info(f"Reindexing datasource. Name={request.name}")
    datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")
```

> There is no `incremental_reindex` query parameter: xWiki v1 has no incremental path (spec §7).

- [ ] **Step 5: Add to the webhook deny-list**

`src/codemie/service/settings/settings_request_validator.py:56` — add to `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES`:

```python
    "knowledge_base_xwiki",
```

and extend `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES_HELP_MESSAGE` at `:66-70` to mention xWiki. Do **not** add it to `UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES` — scheduled reindex is in scope.

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/test_xwiki_index_endpoints.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/ src/codemie/service/settings/settings_request_validator.py \
        tests/codemie/rest_api/test_xwiki_index_endpoints.py
git commit -m "EPMCDME-13142: Add xWiki knowledge base create and update endpoints"
```

---

## Task 12: Scheduled reindex and stale-job resume

**Files:**
- Modify: `src/codemie/triggers/trigger_models.py:59` area
- Modify: `src/codemie/triggers/actors/datasource.py` — new actor after `:430`, `_resume_xwiki` and `_RESUME_DISPATCH` at `:917`
- Modify: `src/codemie/triggers/bindings/cron.py` — imports at `:41`/`:57`, branch after `:636`
- Test: `tests/codemie/triggers/test_xwiki_reindex.py`

**Interfaces:**
- Consumes: Tasks 3, 8, 9
- Produces: `XWikiReindexTask(ReindexTaskPayload)` with `xwiki_index_info: XWikiIndexInfo` · `reindex_xwiki(payload)` · `_RESUME_DISPATCH["knowledge_base_xwiki"]`

**Test-first: yes** — a test asserting the actor runs `processor.reprocess` (a full rebuild, not incremental), and a test asserting the resume dispatch has an xwiki entry.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/triggers/test_xwiki_reindex.py`:

```python
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.index import XWikiIndexInfo
from codemie.triggers.actors.datasource import _RESUME_DISPATCH, reindex_xwiki
from codemie.triggers.trigger_models import XWikiReindexTask


def _payload():
    index_info = MagicMock()
    index_info.id = "idx-1"
    index_info.index_type = "knowledge_base_xwiki"
    index_info.xwiki = XWikiIndexInfo(space="KB", wiki="xwiki")
    index_info.description = "d"
    index_info.project_space_visible = True
    index_info.embeddings_model = "text-embedding-3-small"

    payload = MagicMock(spec=XWikiReindexTask)
    payload.index_info = index_info
    payload.project_name = "p1"
    payload.resource_id = "job-1"
    payload.resource_name = "kb_ds"
    payload.user = MagicMock(id="u1")
    return payload


def test_scheduled_reindex_is_a_full_rebuild():
    processor = MagicMock()
    with patch("codemie.triggers.actors.datasource.IndexInfo.stamp_reindex_triggered_at"), \
         patch("codemie.triggers.actors.datasource.SettingsService.get_xwiki_creds", return_value=MagicMock()), \
         patch("codemie.triggers.actors.datasource.XWikiDatasourceProcessor", return_value=processor), \
         patch("codemie.triggers.actors.datasource.datasource_concurrency_manager") as manager:
        reindex_xwiki(_payload())
    manager.run.assert_called_once()
    assert manager.run.call_args.args[0] == processor.reprocess


def test_missing_index_info_aborts_without_running():
    payload = _payload()
    payload.index_info.xwiki = None
    with patch("codemie.triggers.actors.datasource.IndexInfo.stamp_reindex_triggered_at"), \
         patch("codemie.triggers.actors.datasource.datasource_concurrency_manager") as manager:
        reindex_xwiki(payload)
    manager.run.assert_not_called()


def test_stale_job_watchdog_knows_about_xwiki():
    assert "knowledge_base_xwiki" in _RESUME_DISPATCH
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/triggers/test_xwiki_reindex.py -v`
Expected: FAIL — `ImportError: cannot import name 'reindex_xwiki'`.

- [ ] **Step 3: Add the payload model**

`src/codemie/triggers/trigger_models.py` — import `XWikiIndexInfo` and add after `AzureDevOpsWorkItemReindexTask`:

```python
class XWikiReindexTask(ReindexTaskPayload):
    xwiki_index_info: XWikiIndexInfo = Field(..., description="The xWiki index information.")
```

- [ ] **Step 4: Add the actor and the resume entry**

`src/codemie/triggers/actors/datasource.py` — import `XWikiDatasourceProcessor` and `XWikiReindexTask`, then mirror `reindex_azure_devops_wiki`:

```python
def reindex_xwiki(payload: XWikiReindexTask):
    """
    Initiates the reindexing process for an xWiki datasource.

    Scheduled refresh is a full delete-and-rebuild, matching every comparable datasource type.
    """
    IndexInfo.stamp_reindex_triggered_at(payload.index_info.id)

    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    xwiki_index_info = payload.index_info.xwiki if payload.index_info and payload.index_info.xwiki else None
    if not xwiki_index_info:
        error_msg = (
            f"xWiki index not found for resource '{payload.resource_name}' "
            f"in project '{payload.project_name}'."
        )
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    xwiki_creds = SettingsService.get_xwiki_creds(
        user_id=payload.user.id,
        project_name=payload.project_name,
    )
    if not xwiki_creds:
        error_msg = f"xWiki credentials not found for project '{payload.project_name}'."
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    processor = XWikiDatasourceProcessor(
        datasource_name=payload.resource_name,
        user=payload.user,
        project_name=payload.project_name,
        credentials=xwiki_creds,
        space=xwiki_index_info.space,
        wiki=xwiki_index_info.wiki,
        description=payload.index_info.description or "",
        project_space_visible=payload.index_info.project_space_visible or False,
        index_info=payload.index_info,
        request_uuid=str(uuid4()),
        embedding_model=payload.index_info.embeddings_model,
    )

    datasource_concurrency_manager.run(processor.reprocess, processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )
```

Add the resume helper beside `_resume_azure_devops` and register it in `_RESUME_DISPATCH`:

```python
def _resume_xwiki(index_info: IndexInfo) -> None:
    xwiki_index_info = index_info.xwiki
    XWikiDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=index_info.created_by,
        project_name=index_info.project_name,
        credentials=SettingsService.get_xwiki_creds(
            user_id=index_info.created_by.id,
            project_name=index_info.project_name,
            setting_id=index_info.setting_id,
        ),
        space=xwiki_index_info.space,
        wiki=xwiki_index_info.wiki,
        index_info=index_info,
        setting_id=index_info.setting_id,
    ).resume()
```

```python
    "knowledge_base_xwiki": _resume_xwiki,
```

- [ ] **Step 5: Add the cron branch**

`src/codemie/triggers/bindings/cron.py` — import `reindex_xwiki` and `XWikiReindexTask`, add after the `AZURE_DEVOPS_WORK_ITEM` branch:

```python
        elif index_type_str == FullDatasourceTypes.XWIKI.value:
            payload = XWikiReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                xwiki_index_info=index_info.xwiki,
            )
            return self.scheduler.add_job(
                reindex_xwiki,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
            )
```

> Do **not** touch `src/codemie/triggers/bindings/utils.py:70` — that list is the webhook allow-list, and xWiki is deliberately webhook-unsupported (Task 11 step 5).

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/triggers/test_xwiki_reindex.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add src/codemie/triggers/ tests/codemie/triggers/test_xwiki_reindex.py
git commit -m "EPMCDME-13142: Add scheduled xWiki reindex and stale-job resume"
```

---

## Task 13: Registration guard test

**Files:**
- Create: `tests/codemie/datasource/test_datasource_type_registration.py`

**Interfaces:**
- Consumes: everything registered in Tasks 2, 10
- Produces: a failing test whenever a datasource type is added without its config, enum counterpart, or health-check arm

**Test-first: yes** — this task *is* the test. It must pass on the first run because Tasks 2 and 10 already wired xWiki; the point is that removing any one of those wirings turns it red.

- [ ] **Step 1: Write the guard test**

Three of this ticket's registration points fail at application boot or silently rather than in tests. The enums are genuinely partial today, so the exemption map is explicit and documented — the goal is that a **new** type must be either wired or consciously exempted.

Create `tests/codemie/datasource/test_datasource_type_registration.py`:

```python
"""Guard against half-registered datasource types.

Adding a DatasourceTypes member without wiring its loader config, its
FullDatasourceTypes counterpart, or its health-check arm fails at application
boot or silently at runtime. This test turns those failures red instead.

The exemptions below record the pre-existing partial state of these enums as of
EPMCDME-13142. Do not add to them casually: an entry here means "this type
deliberately has no such registration".
"""

import inspect

import pytest

from codemie.core.constants import DatasourceTypes
from codemie.datasource.datasources_config import datasources_config
from codemie.service.constants import FullDatasourceTypes
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService

NO_LOADER_CONFIG = {
    DatasourceTypes.GIT,      # uses code_loader
    DatasourceTypes.GOOGLE,   # Google Docs loader is not configured per-type
}

NO_FULL_TYPE = {
    DatasourceTypes.SVN,
    DatasourceTypes.JSON,
    DatasourceTypes.XRAY,
}

NO_HEALTH_CHECK = {
    DatasourceTypes.FILE,
    DatasourceTypes.JSON,
    DatasourceTypes.GOOGLE,
}


@pytest.mark.parametrize("datasource_type", list(DatasourceTypes))
def test_every_type_has_a_loader_config(datasource_type):
    if datasource_type in NO_LOADER_CONFIG:
        pytest.skip(f"{datasource_type.value} deliberately has no per-type loader config")
    assert hasattr(datasources_config.loaders, f"{datasource_type.value}_loader"), (
        f"{datasource_type.value} has no '{datasource_type.value}_loader' entry. "
        "A missing datasources-config.yaml block fails at import time."
    )


@pytest.mark.parametrize("datasource_type", list(DatasourceTypes))
def test_every_type_has_a_full_type_counterpart(datasource_type):
    if datasource_type in NO_FULL_TYPE:
        pytest.skip(f"{datasource_type.value} deliberately has no FullDatasourceTypes member")
    assert datasource_type.name in FullDatasourceTypes.__members__, (
        f"DatasourceTypes.{datasource_type.name} has no FullDatasourceTypes counterpart."
    )


@pytest.mark.parametrize("datasource_type", list(DatasourceTypes))
def test_every_type_has_a_health_check_arm(datasource_type):
    if datasource_type in NO_HEALTH_CHECK:
        pytest.skip(f"{datasource_type.value} deliberately has no health check")
    source = inspect.getsource(IndexHealthCheckService.health_check_datasource)
    assert f"DatasourceTypes.{datasource_type.name}" in source, (
        f"health_check_datasource has no 'case DatasourceTypes.{datasource_type.name}' arm. "
        "_check_docs_health() is duck-typed — without this arm it is never called."
    )


def test_xwiki_is_fully_registered():
    assert hasattr(datasources_config.loaders, "xwiki_loader")
    assert FullDatasourceTypes.XWIKI.value == "knowledge_base_xwiki"
    assert "DatasourceTypes.XWIKI" in inspect.getsource(IndexHealthCheckService.health_check_datasource)
```

- [ ] **Step 2: Run the test**

Run: `poetry run pytest tests/codemie/datasource/test_datasource_type_registration.py -v`
Expected: PASS. If any parametrized case fails for a **pre-existing** type, add it to the matching exemption set with a one-line reason — do not weaken the assertion.

- [ ] **Step 3: Verify the guard actually guards**

Temporarily comment out the `xwiki_loader:` block in `config/datasources/datasources-config.yaml`, re-run, confirm a failure, then restore it.

Run: `poetry run pytest tests/codemie/datasource/test_datasource_type_registration.py -v`
Expected: FAIL while the block is commented out; PASS after restoring.

- [ ] **Step 4: Commit**

```bash
git add tests/codemie/datasource/test_datasource_type_registration.py
git commit -m "EPMCDME-13142: Add guard test for datasource type registration"
```

---

## Task 14: Quality gates and manual end-to-end verification

**Files:** none — verification only.

**Test-first: n/a** — this task runs the gates the previous tasks were written against.

- [ ] **Step 1: Run the full backend test suite**

Run: `poetry run pytest tests/codemie/datasource/ tests/codemie/service/ tests/codemie/rest_api/ tests/codemie/triggers/ -v`
Expected: PASS, no regressions.

- [ ] **Step 2: Run lint and the license gate explicitly**

```bash
make ruff
make license-check
make gitleaks
```

Expected: all clean. **`make verify` is not sufficient** — it silently skips the license gate on macOS because the Makefile has no `.PHONY` and the `license` target resolves to the `LICENSE` file.

- [ ] **Step 3: Restart the backend and confirm it boots**

A missing or malformed `xwiki_loader:` YAML block is an import-time Pydantic error, so booting is the only way to catch it.

```bash
docker compose restart codemie && docker logs --tail 50 codemie-dev-codemie-1
```

Expected: `XWikiDatasourceConfig instantiated: ...` in the logs, no traceback.

- [ ] **Step 4: Create the datasource against the live KB space**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/v1/local-auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin@codemie.ai","password":"password"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://localhost:8000/v1/index/knowledge_base/xwiki \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"xwiki-kb-13142","project_name":"<your project>","description":"EPMCDME-13142 e2e",
       "space":"KB","wiki":"xwiki","setting_id":"<your xwiki integration id>"}'
```

Expected: `Indexing of datasource xwiki-kb-13142 has been started in the background`.

The integration must point at `http://xwiki:8080` (the base URL **as seen from the backend container** — no `/xwiki` prefix on this instance) with username `AdminAdmin` and password `admin`.

- [ ] **Step 5: Confirm all six fixture pages plus the nested space were indexed**

```bash
curl -s "http://localhost:9200/<project>-xwiki-kb-13142/_search?size=100" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(sorted({h["_source"]["metadata"]["source"] for h in d["hits"]["hits"]}))'
```

Expected: sources covering `WebHome`, `Vacation Policy`, `Довідка`, `Formatting`, `Onboarding` **and** `Onboarding/Checklist` + `Onboarding/WebHome`. `Empty` must be **absent** (skipped, not indexed).

Also confirm metadata is present: `modified`, `version`, `author`, `content_format` on every chunk.

- [ ] **Step 6: Search the indexed content**

Query the assistant or the search endpoint for something only present in a nested page (for example the onboarding checklist) and confirm a hit with the right `source` URL.

- [ ] **Step 7: Verify the failure paths by hand**

- Wrong base URL (add a bogus `/xwiki` prefix to the integration) → health check returns a `ConnectionException` message with `field_error: "url"`.
- Blank `space` → `field_error: "space"`.
- "Test connection" on the xWiki integration → succeeds with the "instance reachable, credentials accepted" wording, and no claim about password validity.

- [ ] **Step 8: Commit any fixes, then confirm nothing stray is staged**

```bash
git status --porcelain
git log --oneline origin/main..HEAD
```

Expected: no `.diff` files from `docs/superpowers/**` anywhere in the branch.

---

## Self-Review

**Spec coverage.** §1 modules → Tasks 1, 4–8. §2.1 path grammars → Task 4. §2.2 crawl incl. paginated space listing → Task 6. §2.3 rendered-with-fallback → Task 7. §2.4 network robustness → Tasks 5, 7. §2.5 stats → Task 7. §2.6 page cap → Tasks 7, 10. §3 chunk metadata → Tasks 7, 8. §4 processor → Task 8. §5 errors → Tasks 5, 10. §5.1 credential-check honesty → Tasks 9, 10. §6 registration table → Tasks 2, 3, 9, 10, 11, 12. §6.1 API shape → Tasks 3, 11. §7 full rebuild → Task 12. §8 testing → every task. §8.1 no dependency changes → Global Constraints. §9 known limitations → documented in code comments and the MR. §10 definition of done → Task 14.

**Deviation on record.** Spec §2.1 says to reuse `build_spaces_path()`; Task 4 does not, because that helper does no percent-encoding. Flagged in Task 4.

**Naming consistency.** `XWikiLoader`, `XWikiDatasourceProcessor`, `XWikiIndexInfo`, `XWikiDatasourceConfig`, `XWIKI_CONFIG`, `XWikiReindexTask`, `get_xwiki_creds`, `_test_xwiki`, `health_check_xwiki`, `reindex_xwiki`, `_resume_xwiki` — used identically in every task that references them. `XWikiConfig` refers only to the pre-existing credential model.
