# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Datasource PUT idempotency tests (EPMCDME-15310, AC3)."""

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from unittest.mock import patch

from codemie.rest_api.main import app
from codemie.core.models import CreatedByUser
from codemie.rest_api.routers.index import router
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

DEMO = "demo"

app_client = TestClient(app)
app_for_client = FastAPI()
app_for_client.include_router(router)
client = TestClient(app_for_client)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def authenticated_user():
    # Create a single user instance to ensure equality checks pass
    user = User(id='test_user', username='test_user', name='test_user')

    def mock_authenticate(request: Request = None):
        if request:
            request.state.user = user
        return user

    # Override for app level client
    app.dependency_overrides[authenticate] = mock_authenticate

    # Override for router level client


# ===========================================================================
# AC3 (EPMCDME-15310): per-datasource-type idempotency of the type-specific PUT routes
#
# Re-submitting the same body twice must leave the stored IndexInfo (and the type-specific SVNRepo /
# GitRepo row) unchanged apart from update_date / updated_date / last_reindex_date. Persistence is a real
# in-memory SQLite database patched in as PostgresClient.get_engine; the real router and the real
# IndexInfo.update_index run. Only external indexers/processors, credential lookups and KB checks are mocked.
#
# Field classes used in the test names and comments below:
#   * user-settable field  - set by the caller in the body (description, prompt, files_filter, setting_id, ...)
#   * state field          - maintained by the system (error, completed, counters, updated_by); NOT user-settable
#
# Source audit of what each route is expected to touch (Task 2):
# audit: svn PUT (routers/index/code_repositories.py) rewrites: update_index gets all defaults, prompt=None, files_filter="", project_space_visible=True, docs_generation=False (_repository_support.py, models/index.py:915,920,922)
# audit: repo PUT (routers/index/code_repositories.py) none: uses model_fields_set (code_repositories.py); explicit null prompt still sets None (models/index.py:915)
# audit: google PUT (routers/index/kb_google.py) rewrites: error=False via default reset_error=True (kb_google.py, models/index.py:930)
# audit: git_faq PUT (routers/index/kb_git_faq.py) rewrites: error=False via default reset_error=True (kb_git_faq.py); setting_id "" -> None ignored by models/index.py:942
# audit: file PUT (routers/index/kb_files.py) rewrites: error=False + updated_by (file_datasource_service.py:197, models/index.py:930,950); file changes also current_state/complete_state/processed_files (file_datasource_update_processor.py:61-101); keep-list-only counters: needs test proof
# audit: confluence PUT (routers/index/kb_atlassian.py) none: reset_error=False (kb_atlassian.py), cql same value
# audit: jira PUT (routers/index/kb_atlassian.py) none: same-value writes + flag_modified(jira); custom_fields=[] clears (models/index.py:822-825)
# audit: xray PUT (routers/index/kb_atlassian.py) none: same-value jql write (models/index.py:815-818)
# audit: ADO wiki PUT (routers/index/kb_azure_devops.py) none: same-value wiki_query write (models/index.py:828-830)
# audit: xwiki PUT (routers/index/kb_xwiki.py) none: same-value space/wiki write (models/index.py:831-844)
# audit: ADO work item PUT (routers/index/kb_azure_devops.py) none: same-value wiql_query write (models/index.py:935-937)
# audit: sharepoint PUT (routers/index/kb_sharepoint.py) rewrites: top-level files_filter=None clobber (models/index.py:920) + flag_modified(sharepoint) (models/index.py:880); top-level files_filter impact: needs test proof
# audit: provider PUT (routers/index/providers.py) rewrites: description/project_space_visible/repo_name/project_name from values.get (provider_datasource_base_service.py:28-33), sensitive params re-encrypted (provider/util.py:22-34); plain encryption: needs test proof
#
# needs test proof: PUT /index/knowledge_base/file WITH file changes (new uploads or a shortened keep-list) is
# not exercised here: it needs real multipart uploads plus file storage. Only the keep-everything /
# metadata-only path (the idempotent re-submission) is covered for the file route.
# ===========================================================================

import importlib as _idem_importlib  # noqa: E402
import json as _idem_json  # noqa: E402
from contextlib import ExitStack as _IdemExitStack  # noqa: E402
from dataclasses import dataclass as _idem_dataclass, field as _idem_field  # noqa: E402
from types import SimpleNamespace as _IdemNamespace  # noqa: E402
from typing import Any as _IdemAny  # noqa: E402

from pydantic.json import pydantic_encoder as _idem_pydantic_encoder  # noqa: E402
from sqlalchemy import MetaData as _IdemMetaData, create_engine as _idem_create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles as _idem_compiles  # noqa: E402
from sqlalchemy.pool import StaticPool as _IdemStaticPool  # noqa: E402
from sqlmodel import Session as _IdemSession, select as _idem_select  # noqa: E402

from codemie.clients.postgres import PostgresClient  # noqa: E402
from codemie.core.constants import CodeIndexType  # noqa: E402
from codemie.core.models import GitRepo, SVNRepo  # noqa: E402
from codemie.rest_api.models.index import (  # noqa: E402
    AzureDevOpsWikiIndexInfo,
    AzureDevOpsWorkItemIndexInfo,
    ConfluenceIndexInfo,
    IndexInfo,
    IndexInfoProviderFields,
    JiraIndexInfo,
    SharePointIndexInfo,
    XrayIndexInfo,
    XWikiIndexInfo,
)
from codemie.service.encryption.base_encryption_service import PlainEncryptionService  # noqa: E402
from tests.codemie.rest_api.routers.idempotency_helpers import assert_stored_unchanged  # noqa: E402

IDEM_ROUTERS = "codemie.rest_api.routers.index"
IDEM_MODULES = (
    "_common",
    "_repository_support",
    "code_repositories",
    "kb_atlassian",
    "kb_azure_devops",
    "kb_files",
    "kb_git_faq",
    "kb_google",
    "kb_sharepoint",
    "kb_xwiki",
    "providers",
    "queries",
)
IDEM_PROJECT = "demo"
IDEM_NAME = "idem-ds"
IDEM_USER = User(
    id="idem-user", username="idem", name="Idem", is_admin=True, is_maintainer=True, project_names=[IDEM_PROJECT]
)
IDEM_CREATOR = CreatedByUser(id=IDEM_USER.id, username=IDEM_USER.username, name=IDEM_USER.name)
IDEM_GIT_LINK = "https://github.com/org/idem-repo"
IDEM_SVN_LINK = "svn://svn.example.com/idem-repo"
IDEM_SETTING_ID = "setting-1"


def _idem_patch_all(enter, name: str, **kwargs) -> None:
    """Patch `name` in every routers.index submodule that imports it (each looks it up in its own globals).

    Dotted names (Class.attr) patch the shared class once."""
    root = name.split(".")[0]
    modules = [m for m in IDEM_MODULES if hasattr(_idem_importlib.import_module(f"{IDEM_ROUTERS}.{m}"), root)]
    assert modules, f"{name} is not imported by any routers.index submodule"
    for module in modules[:1] if "." in name else modules:
        enter(patch(f"{IDEM_ROUTERS}.{module}.{name}", **kwargs))


@_idem_compiles(JSONB, "sqlite")
def _idem_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@_idem_dataclass(frozen=True)
class IdemCase:
    """One type-specific datasource PUT route.

    ``seed`` holds the IndexInfo columns of the already-created datasource; ``body`` is a full valid body that
    re-sends the seeded values; ``minimal_body`` carries only the required fields. ``user_fields`` are the
    user-settable IndexInfo fields compared between the seed and the stored row after the first PUT.
    """

    id: str
    path: str
    seed: dict
    body: dict
    minimal_body: dict
    user_fields: tuple[str, ...]
    params: dict = _idem_field(default_factory=dict)
    send_as_query: bool = False
    extra_row: str | None = None  # "svn" | "repo": type-specific repo row stored next to IndexInfo
    resets_error: bool = False  # the route calls update_index with the default reset_error=True


_IDEM_COMMON_FIELDS = (
    "project_name",
    "repo_name",
    "index_type",
    "description",
    "prompt",
    "files_filter",
    "project_space_visible",
    "docs_generation",
    "embeddings_model",
    "setting_id",
    "branch",
    "link",
)


def _idem_seed(index_type: str, **overrides) -> dict:
    """A created datasource with realistic prior values (error=False, prompt/filter/setting/model set)."""
    seed = {
        "id": f"idx-{index_type}",
        "project_name": IDEM_PROJECT,
        "repo_name": IDEM_NAME,
        "index_type": index_type,
        "description": "seed description",
        "prompt": "seed prompt",
        "files_filter": "*.md",
        "project_space_visible": False,
        "setting_id": IDEM_SETTING_ID,
        "embeddings_model": "seed-embeddings",
        "error": False,
        "completed": True,
        "current_state": 7,
        "complete_state": 7,
        "created_by": IDEM_CREATOR,
    }
    seed.update(overrides)
    return seed


def _idem_kb_body(**extra) -> dict:
    body = {
        "name": IDEM_NAME,
        "project_name": IDEM_PROJECT,
        "description": "seed description",
        "project_space_visible": False,
        "setting_id": IDEM_SETTING_ID,
    }
    body.update(extra)
    return body


_IDEM_MINIMAL = {"name": IDEM_NAME, "project_name": IDEM_PROJECT}
_IDEM_CODE_BODY = {
    "name": IDEM_NAME,
    "description": "seed description",
    "prompt": "seed prompt",
    "embeddingsModel": "seed-embeddings",
    "projectSpaceVisible": False,
    "docsGeneration": True,
    "branch": "main",
    "link": IDEM_GIT_LINK,
    "filesFilter": "*.md",
    "setting_id": IDEM_SETTING_ID,
}
_IDEM_CODE_FIELDS = _IDEM_COMMON_FIELDS
_IDEM_KB = "/v1/index/knowledge_base"

IDEM_CASES = [
    IdemCase(
        id="confluence",
        path=f"{_IDEM_KB}/confluence",
        seed=_idem_seed("knowledge_base_confluence", confluence=ConfluenceIndexInfo(cql="space = DEMO")),
        body=_idem_kb_body(cql="space = DEMO"),
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("confluence",),
    ),
    IdemCase(
        id="jira",
        path=f"{_IDEM_KB}/jira",
        seed=_idem_seed(
            "knowledge_base_jira", jira=JiraIndexInfo(jql="project = DEMO", custom_fields=["customfield_1"])
        ),
        body=_idem_kb_body(jql="project = DEMO", custom_fields=["customfield_1"]),
        minimal_body={**_IDEM_MINIMAL, "jql": "project = DEMO"},
        user_fields=_IDEM_COMMON_FIELDS + ("jira",),
    ),
    IdemCase(
        id="xray",
        path=f"{_IDEM_KB}/xray",
        seed=_idem_seed("knowledge_base_xray", xray=XrayIndexInfo(jql="project = DEMO")),
        body=_idem_kb_body(jql="project = DEMO"),
        minimal_body={**_IDEM_MINIMAL, "jql": "project = DEMO"},
        user_fields=_IDEM_COMMON_FIELDS + ("xray",),
    ),
    IdemCase(
        id="ado_wiki",
        path=f"{_IDEM_KB}/azure_devops_wiki",
        seed=_idem_seed(
            "knowledge_base_azure_devops_wiki",
            azure_devops_wiki=AzureDevOpsWikiIndexInfo(wiki_query="/docs", wiki_name="wiki-1"),
        ),
        body=_idem_kb_body(wiki_query="/docs", wiki_name="wiki-1"),
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("azure_devops_wiki",),
    ),
    IdemCase(
        id="ado_work_item",
        path=f"{_IDEM_KB}/azure_devops_work_item",
        seed=_idem_seed(
            "knowledge_base_azure_devops_work_item",
            azure_devops_work_item=AzureDevOpsWorkItemIndexInfo(wiql_query="SELECT [Id] FROM WorkItems"),
        ),
        body=_idem_kb_body(wiql_query="SELECT [Id] FROM WorkItems"),
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("azure_devops_work_item",),
    ),
    IdemCase(
        id="xwiki",
        path=f"{_IDEM_KB}/xwiki",
        seed=_idem_seed("knowledge_base_xwiki", xwiki=XWikiIndexInfo(space="KB", wiki="xwiki")),
        body=_idem_kb_body(space="KB", wiki="xwiki"),
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("xwiki",),
    ),
    IdemCase(
        id="sharepoint",
        path=f"{_IDEM_KB}/sharepoint",
        seed=_idem_seed(
            "knowledge_base_sharepoint",
            files_filter="*.pdf",
            sharepoint=SharePointIndexInfo(
                site_url="https://tenant.sharepoint.com/sites/demo",
                include_pages=True,
                include_documents=True,
                include_lists=False,
                max_file_size_mb=25,
                files_filter="*.pdf",
                auth_type="integration",
            ),
        ),
        body=_idem_kb_body(
            site_url="https://tenant.sharepoint.com/sites/demo",
            include_pages=True,
            include_documents=True,
            include_lists=False,
            max_file_size_mb=25,
            files_filter="*.pdf",
            auth_type="integration",
            embedding_model="seed-embeddings",
        ),
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("sharepoint",),
    ),
    IdemCase(
        id="google",
        path=f"{_IDEM_KB}/google",
        seed=_idem_seed("llm_routing_google", google_doc_link="https://docs.google.com/document/d/abc"),
        body={
            "name": IDEM_NAME,
            "project_name": IDEM_PROJECT,
            "description": "seed description",
            "project_space_visible": False,
        },
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("google_doc_link",),
        resets_error=True,
    ),
    IdemCase(
        id="git_faq",
        path=f"{_IDEM_KB}/git_faq",
        seed=_idem_seed("knowledge_base_git_faq", link=IDEM_GIT_LINK, branch="main"),
        body={
            "name": IDEM_NAME,
            "project_name": IDEM_PROJECT,
            "description": "seed description",
            "project_space_visible": False,
        },
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS,
        resets_error=True,
    ),
    IdemCase(
        id="file",
        path=f"{_IDEM_KB}/file",
        seed=_idem_seed("knowledge_base_file", uploaded_files=["a.txt", "b.txt"], processed_files=["a.txt", "b.txt"]),
        body={
            "name": IDEM_NAME,
            "project_name": IDEM_PROJECT,
            "description": "seed description",
            "project_space_visible": False,
            "uploaded_files": _idem_json.dumps(["a.txt", "b.txt"]),
        },
        minimal_body=_IDEM_MINIMAL,
        user_fields=_IDEM_COMMON_FIELDS + ("uploaded_files",),
        send_as_query=True,
        resets_error=True,
    ),
    IdemCase(
        id="provider",
        path="/v1/index/provider/idx-provider",
        seed=_idem_seed(
            "provider",
            provider_fields=IndexInfoProviderFields(
                provider_id="prov-1",
                toolkit_id="toolkit-1",
                base_params={"endpoint": "https://provider.example.com"},
                create_params={"mode": "fast"},
            ),
        ),
        body={
            "name": IDEM_NAME,
            "project_name": IDEM_PROJECT,
            "description": "seed description",
            "project_space_visible": False,
            "endpoint": "https://provider.example.com",
            "mode": "fast",
        },
        minimal_body={**_IDEM_MINIMAL, "endpoint": "https://provider.example.com", "mode": "fast"},
        user_fields=_IDEM_COMMON_FIELDS + ("provider_fields",),
    ),
    IdemCase(
        id="svn",
        path=f"/v1/application/{IDEM_PROJECT}/index/svn/{IDEM_NAME}",
        seed=_idem_seed("code", vcs_type="svn", branch="trunk", link=IDEM_SVN_LINK, docs_generation=True),
        body={**_IDEM_CODE_BODY, "branch": "trunk", "link": IDEM_SVN_LINK},
        minimal_body={"name": IDEM_NAME},
        user_fields=_IDEM_CODE_FIELDS,
        params={"skip_reindex": "true"},
        extra_row="svn",
    ),
    IdemCase(
        id="code_repo",
        path=f"/v1/application/{IDEM_PROJECT}/index/{IDEM_NAME}",
        seed=_idem_seed("code", vcs_type="git", branch="main", link=IDEM_GIT_LINK, docs_generation=True),
        body=_IDEM_CODE_BODY,
        minimal_body={"name": IDEM_NAME},
        user_fields=_IDEM_CODE_FIELDS,
        params={"skip_reindex": "true"},
        extra_row="repo",
    ),
]
IDEM_CASE_PARAMS = [pytest.param(case, id=case.id) for case in IDEM_CASES]


def _idem_extra_row(case: IdemCase):
    """The type-specific repository row seeded next to the IndexInfo, if the route has one."""
    common = {
        "id": f"repo-{case.id}",
        "name": IDEM_NAME,
        "app_id": IDEM_PROJECT,
        "description": "seed description",
        "index_type": CodeIndexType.CODE,
        "prompt": "seed prompt",
        "embeddings_model": "seed-embeddings",
        "files_filter": "*.md",
        "docs_generation": True,
        "project_space_visible": False,
        "setting_id": IDEM_SETTING_ID,
    }
    if case.extra_row == "svn":
        return SVNRepo(**common, link=IDEM_SVN_LINK, branch="trunk")
    return GitRepo(**common, link=IDEM_GIT_LINK, branch="main")


@pytest.fixture
def idem_client():
    def _authenticate(request: Request = None):
        if request is not None:
            request.state.user = IDEM_USER
        return IDEM_USER

    app.dependency_overrides[authenticate] = _authenticate
    yield TestClient(app)
    app.dependency_overrides = {}


@pytest.fixture
def idem_db(monkeypatch):
    """Real in-memory SQLite database with the datasource tables, standing in for PostgreSQL."""
    engine = _idem_create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=_IdemStaticPool,
        json_serializer=lambda v: _idem_json.dumps(v, default=_idem_pydantic_encoder),
    )
    metadata = _IdemMetaData()
    for model in (IndexInfo, GitRepo, SVNRepo):
        model.__table__.to_metadata(metadata).indexes.clear()  # PostgreSQL-only (GIN/expression) indexes
    metadata.create_all(engine)
    monkeypatch.setattr(PostgresClient, "get_engine", classmethod(lambda cls: engine))
    yield engine
    engine.dispose()


@pytest.fixture
def idem_external_mocks(monkeypatch):
    """Mock only what talks to the outside world: indexers/processors, credentials, schedulers, providers."""
    provider_toolkit = _IdemNamespace(toolkit_id="toolkit-1", get_managed_fields=lambda: {})
    provider = _IdemNamespace(name="prov-1", provided_toolkits=[provider_toolkit])
    endpoint = _IdemNamespace(name="endpoint", parameter_type="String", default_value=None, required=True)
    mode = _IdemNamespace(name="mode", parameter_type="String", default_value=None, required=False)
    schema = _IdemNamespace(
        field_names=["endpoint", "mode"],
        base_schema=_IdemNamespace(parameters=[endpoint], get_sensetive_fields=lambda: []),
        create_schema=_IdemNamespace(parameters=[mode], get_sensetive_fields=lambda: []),
    )
    monkeypatch.setattr("codemie.service.provider.util.encryption_service", PlainEncryptionService())
    with _IdemExitStack() as stack:
        enter = stack.enter_context
        _idem_patch_all(enter, "SettingsService")
        _idem_patch_all(enter, "JiraDatasourceProcessor")
        _idem_patch_all(enter, "_update_datasource_scheduler")
        _idem_patch_all(enter, "_validate_git_credentials")
        _idem_patch_all(enter, "_build_git_faq_processor_from_index")
        _idem_patch_all(enter, "SVNIndexService")
        _idem_patch_all(enter, "SVNDatasourceProcessor")
        _idem_patch_all(enter, "run_in_background")
        _idem_patch_all(enter, "AgentMonitoringService")
        _idem_patch_all(enter, "Provider.get_by_id", return_value=provider)
        _idem_patch_all(enter, "Application.get_by_id", return_value=_IdemNamespace(name=IDEM_PROJECT))
        enter(
            patch(
                "codemie.service.provider.datasource.provider_datasource_update_service.ProviderDatasourceSchemaService",
                return_value=_IdemNamespace(schema_for=lambda *args, **kwargs: schema),
            )
        )
        yield


def _idem_dump(row: _IdemAny) -> dict:
    return row.model_dump(mode="json")


def _idem_stored(engine, case: IdemCase) -> dict:
    """Snapshot of the stored rows exactly as the database holds them (never the HTTP response)."""
    with _IdemSession(engine) as session:
        index_rows = session.exec(_idem_select(IndexInfo).where(IndexInfo.id == case.seed["id"])).all()
        assert len(index_rows) == 1, f"expected one stored IndexInfo for {case.id}"
        snapshot = {"index_info": _idem_dump(index_rows[0])}
        if case.extra_row:
            model = SVNRepo if case.extra_row == "svn" else GitRepo
            repo_rows = session.exec(_idem_select(model).where(model.id == f"repo-{case.id}")).all()
            assert len(repo_rows) == 1, f"expected one stored {model.__name__} for {case.id}"
            snapshot["repo_row"] = _idem_dump(repo_rows[0])
        return snapshot


@pytest.fixture
def idem_seeded(idem_db, idem_external_mocks):
    """Factory that stores the created datasource (and its repo row) before any PUT."""

    def _seed(case: IdemCase) -> None:
        with _IdemSession(idem_db) as session:
            session.add(IndexInfo(**case.seed))
            if case.extra_row:
                session.add(_idem_extra_row(case))
            session.commit()

    return _seed


def _idem_send(client: TestClient, case: IdemCase, body: dict, **extra_params):
    params = {**case.params, **extra_params}
    if case.send_as_query:  # file route: Depends() form model, every field is a query parameter
        return client.put(case.path, params={**body, **params}, headers={"user-id": IDEM_USER.id})
    return client.put(case.path, json=body, params=params, headers={"user-id": IDEM_USER.id})


def _idem_put(client: TestClient, case: IdemCase, body: dict, **extra_params) -> None:
    response = _idem_send(client, case, body, **extra_params)
    assert response.status_code in (200, 201), response.text


def _idem_user_fields(snapshot: dict, case: IdemCase) -> dict:
    index_info = snapshot["index_info"]
    selected = {"index_info": {field: index_info[field] for field in case.user_fields}}
    if "repo_row" in snapshot:
        selected["repo_row"] = snapshot["repo_row"]
    return selected


@pytest.mark.parametrize("case", IDEM_CASE_PARAMS)
def test_put_same_body_twice_leaves_stored_datasource_unchanged(case, idem_client, idem_db, idem_seeded):
    """Second identical PUT changes nothing in the stored rows except update/last-reindex dates."""
    idem_seeded(case)

    _idem_put(idem_client, case, case.body)
    after_first = _idem_stored(idem_db, case)
    _idem_put(idem_client, case, case.body)
    after_second = _idem_stored(idem_db, case)

    assert_stored_unchanged(after_first, after_second)


@pytest.mark.parametrize("case", IDEM_CASE_PARAMS)
def test_put_body_resending_seeded_values_keeps_user_settable_fields(case, idem_client, idem_db, idem_seeded):
    """A body that re-sends the seeded values must not change any user-settable field of the stored datasource."""
    idem_seeded(case)
    seeded = _idem_stored(idem_db, case)

    _idem_put(idem_client, case, case.body)
    after_first = _idem_stored(idem_db, case)

    assert_stored_unchanged(_idem_user_fields(seeded, case), _idem_user_fields(after_first, case))


# Routes whose omitted-optional-field behaviour differs from "keep stored values"; pinned separately below.
_IDEM_OMIT_PINNED_IDS = ("sharepoint", "provider", "svn")
IDEM_KEEP_ON_OMIT_PARAMS = [pytest.param(c, id=c.id) for c in IDEM_CASES if c.id not in _IDEM_OMIT_PINNED_IDS]


@pytest.mark.parametrize("case", IDEM_KEEP_ON_OMIT_PARAMS)
def test_put_omitting_optional_fields_keeps_stored_user_settable_fields(case, idem_client, idem_db, idem_seeded):
    """Optional fields left out of the body mean 'leave as is': the stored user-settable fields stay put."""
    idem_seeded(case)
    seeded = _idem_stored(idem_db, case)

    _idem_put(idem_client, case, case.minimal_body)
    after_first = _idem_stored(idem_db, case)

    assert_stored_unchanged(_idem_user_fields(seeded, case), _idem_user_fields(after_first, case))


def _idem_case(case_id: str) -> IdemCase:
    return next(c for c in IDEM_CASES if c.id == case_id)


# Pinned CURRENT behaviour (decision: keep, do not change src). Accepted because: PUT has full-replace semantics
# (fields omitted from the body take the request-model defaults), a second submit of the same request converges
# (asserted below), and changing it would alter existing behaviour under AC6. See the Task 2 `# audit:` block.
def test_put_omitting_optional_fields_pins_sharepoint_top_level_files_filter_reset(idem_client, idem_db, idem_seeded):
    """audit: sharepoint PUT rewrites top-level IndexInfo.files_filter to None when the body omits it."""
    case = _idem_case("sharepoint")
    idem_seeded(case)

    _idem_put(idem_client, case, case.minimal_body)
    after_first = _idem_stored(idem_db, case)
    _idem_put(idem_client, case, case.minimal_body)

    assert after_first["index_info"]["files_filter"] is None
    assert_stored_unchanged(after_first, _idem_stored(idem_db, case))


def test_put_omitting_optional_fields_pins_provider_server_error(idem_client, idem_db, idem_seeded):
    """audit: provider PUT overwrites description/project_space_visible from values.get; omitting them is a 500."""
    case = _idem_case("provider")
    idem_seeded(case)
    client = TestClient(app, raise_server_exceptions=False)  # observe the 500 instead of the re-raised IntegrityError

    first = _idem_send(client, case, case.minimal_body)
    after_first = _idem_stored(idem_db, case)
    second = _idem_send(client, case, case.minimal_body)

    assert first.status_code == 500
    assert second.status_code == 500
    assert_stored_unchanged(after_first, _idem_stored(idem_db, case))


def test_put_omitting_optional_fields_pins_svn_request_defaults(idem_client, idem_db, idem_seeded):
    """audit: svn PUT passes every request value, so omitted fields take UpdateIndexRequest defaults."""
    case = _idem_case("svn")
    idem_seeded(case)

    _idem_put(idem_client, case, case.minimal_body)
    after_first = _idem_stored(idem_db, case)
    _idem_put(idem_client, case, case.minimal_body)

    index_info, repo_row = after_first["index_info"], after_first["repo_row"]
    assert (index_info["prompt"], index_info["files_filter"]) == (None, "")
    assert (index_info["project_space_visible"], index_info["docs_generation"]) == (True, False)
    assert (repo_row["files_filter"], repo_row["project_space_visible"], repo_row["docs_generation"]) == (
        "",
        True,
        False,
    )
    assert_stored_unchanged(after_first, _idem_stored(idem_db, case))


@pytest.mark.parametrize("case", [pytest.param(c, id=c.id) for c in IDEM_CASES if c.resets_error])
def test_put_resets_error_state_of_errored_datasource(case, idem_client, idem_db, idem_seeded):
    """audit: google/git_faq/file PUT use update_index's default reset_error=True, so `error` True -> False.

    Pinned as accepted: `error` is a STATE field, a re-PUT is a retry that clears it, and the second submit converges.
    """
    idem_seeded(case)
    with _IdemSession(idem_db) as session:
        row = session.get(IndexInfo, case.seed["id"])
        row.error = True
        session.add(row)
        session.commit()

    _idem_put(idem_client, case, case.body)
    after_first = _idem_stored(idem_db, case)
    _idem_put(idem_client, case, case.body)

    assert after_first["index_info"]["error"] is False
    assert_stored_unchanged(after_first, _idem_stored(idem_db, case))
