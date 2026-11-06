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
import json
import re
from types import SimpleNamespace

import pytest
from elasticsearch.exceptions import NotFoundError
from pydantic import BaseModel

from codemie.core.models import CreatedByUser
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.index import (
    AzureDevOpsWikiIndexInfo,
    AzureDevOpsWorkItemIndexInfo,
    ConfluenceIndexInfo,
    IndexInfo,
    JiraIndexInfo,
    IndexInfoProviderFields,
    SharePointIndexInfo,
    UpdateIndexRequest,
    UpdateKnowledgeBaseAzureDevOpsWikiRequest,
    UpdateKnowledgeBaseAzureDevOpsWorkItemRequest,
    UpdateKnowledgeBaseConfluenceRequest,
    UpdateKnowledgeBaseGitFaqRequest,
    UpdateKnowledgeBaseGoogleRequest,
    UpdateKnowledgeBaseJiraRequest,
    UpdateKnowledgeBaseSharePointRequest,
    UpdateKnowledgeBaseXrayRequest,
    UpdateKnowledgeBaseXWikiRequest,
    XrayIndexInfo,
    XWikiIndexInfo,
)
from codemie.rest_api.models.provider import ProviderDataSourceTypeSchema, ProviderToolkitConfigParameter
from codemie.rest_api.security.user import User
from codemie.service.iac_export.document import ExportError, to_canonical_bytes
from codemie.service.iac_export.exporters.datasource import DATASOURCE_EXPORTERS, DatasourceDispatchExporter
from codemie.service.iac_export.references import NameEntry, NameResolver
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms

BASE = "codemie.service.iac_export.exporters.datasource"
SECRET_SETTING_ID = "setting-uuid-9"
INDEX_ID = "index-uuid-1"
# Update-request fields the document deliberately does not carry: renames are the apply side's job, the guardrail
# assignment table and the transient SharePoint token have no slot in the envelope, a project move is rejected by apply.
NOT_EXPORTED = {"new_project_name", "guardrail_assignments", "access_token"}
RENAMES = {"project_space_visible": "shared_with_project"}


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _model_fields(model: type[BaseModel]) -> set[str]:
    return {RENAMES.get(_snake(f), _snake(f)) for f in model.model_fields} - NOT_EXPORTED


def _assert_covers(spec: dict, model: type[BaseModel]) -> None:
    assert _model_fields(model) <= set(spec), sorted(_model_fields(model) - set(spec))


def _admin() -> User:
    user = User(id="admin-1", username="admin-1")
    user.is_admin = True
    return user


def _resolver() -> NameResolver:
    return NameResolver(lambda kind, scope: [NameEntry(SECRET_SETTING_ID, "My Integration")])


@pytest.fixture(autouse=True)
def _collaborators(monkeypatch):
    monkeypatch.setattr(
        f"{BASE}.base.SchedulerSettingsService.get_scheduler_settings_for_datasources",
        lambda user_id, ids: {INDEX_ID: {"cron_expression": "0 3 * * *", "timezone": "Europe/Warsaw"}},
    )


def _index(index_type: str, **kw) -> IndexInfo:
    values = {
        "id": INDEX_ID,
        "project_name": "demo",
        "repo_name": "my-ds",
        "index_type": index_type,
        "description": "desc",
        "project_space_visible": True,
        "setting_id": SECRET_SETTING_ID,
        "error": True,
        "text": "status text",
    }
    values.update(kw)
    return IndexInfo(**values)


def _export(index: IndexInfo) -> dict:
    from unittest.mock import patch

    exporter = DatasourceDispatchExporter(resolver=_resolver())
    with patch(f"{BASE}.IndexInfo.get_by_id", return_value=index):
        body = to_canonical_bytes(exporter.export(INDEX_ID, _admin()))
    assert INDEX_ID.encode() not in body
    assert SECRET_SETTING_ID.encode() not in body
    assert b"status text" not in body
    assert b"guardrail" not in body
    assert_conforms(json.loads(body))  # every datasource type parses in the envelope
    return json.loads(body)


def _code(vcs: str, index_type: str = "code") -> IndexInfo:
    return _index(
        index_type,
        vcs_type=vcs,
        prompt="p",
        embeddings_model="emb",
        docs_generation=True,
        branch="main",
        link="https://git.example/r.git",
        files_filter="*.py",
    )


@pytest.mark.parametrize("vcs", ["git", "svn"])
def test_code_spec_covers_every_update_field(vcs):
    doc = _export(_code(vcs))
    assert doc["meta"] == {"kind": "datasource", "name": "my-ds"}
    spec = doc["spec"]
    _assert_covers(spec, UpdateIndexRequest)
    assert spec["type"] == ("svn" if vcs == "svn" else "code")
    assert "vcs_type" not in spec and "guardrail_assignments" not in spec
    assert spec["index_type"] == "code"
    assert spec["project_name"] == "demo"
    assert spec["shared_with_project"] is True
    assert spec["setting_id"] == "My Integration"
    assert spec["cron_expression"] == "0 3 * * *"
    assert spec["timezone"] == "Europe/Warsaw"


@pytest.mark.parametrize("index_type", ["summary", "chunk-summary"])
def test_code_variants_are_dispatched(index_type):
    assert _export(_code("git", index_type))["spec"]["index_type"] == index_type


def test_confluence_spec_covers_every_update_field():
    index = _index(
        "knowledge_base_confluence",
        confluence=ConfluenceIndexInfo(cql="space = X", include_comments=True, max_pages=50, keep_newlines=False),
    )
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseConfluenceRequest)
    assert spec["type"] == "knowledge_base_confluence"
    assert spec["cql"] == "space = X"
    assert (spec["include_comments"], spec["max_pages"], spec["keep_newlines"]) == (True, 50, False)


def test_jira_spec_covers_every_update_field():
    index = _index("knowledge_base_jira", jira=JiraIndexInfo(jql="project = X", custom_fields=["cf_1"]))
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseJiraRequest)
    assert spec["jql"] == "project = X"
    assert spec["custom_fields"] == ["cf_1"]


def test_google_spec_covers_every_update_field():
    spec = _export(_index("llm_routing_google", google_doc_link="https://docs.google.com/d/1"))["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseGoogleRequest)
    assert spec["type"] == "llm_routing_google"
    assert spec["google_doc"] == "https://docs.google.com/d/1"


def test_xray_spec_covers_every_update_field():
    index = _index("knowledge_base_xray", xray=XrayIndexInfo(jql="project = X"))
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseXrayRequest)
    assert spec["jql"] == "project = X"
    assert spec["setting_id"] == "My Integration"


def test_azure_devops_wiki_spec_covers_every_update_field():
    index = _index(
        "knowledge_base_azure_devops_wiki",
        azure_devops_wiki=AzureDevOpsWikiIndexInfo(wiki_query="/docs", wiki_name="main"),
    )
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseAzureDevOpsWikiRequest)
    assert spec["wiki_query"] == "/docs"
    assert spec["wiki_name"] == "main"
    assert spec["setting_id"] == "My Integration"


def test_azure_devops_work_item_spec_covers_every_update_field():
    index = _index(
        "knowledge_base_azure_devops_work_item",
        azure_devops_work_item=AzureDevOpsWorkItemIndexInfo(wiql_query="SELECT 1"),
    )
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseAzureDevOpsWorkItemRequest)
    assert spec["wiql_query"] == "SELECT 1"
    assert spec["setting_id"] == "My Integration"


def test_git_faq_spec_covers_every_update_field():
    index = _index("knowledge_base_git_faq", link="https://git.example/faq.git", branch="main", files_filter="*.md")
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseGitFaqRequest)
    assert spec["type"] == "knowledge_base_git_faq"
    assert (spec["link"], spec["branch"], spec["files_filter"]) == ("https://git.example/faq.git", "main", "*.md")
    assert spec["setting_id"] == "My Integration"


def test_xwiki_spec_covers_every_update_field():
    index = _index("knowledge_base_xwiki", xwiki=XWikiIndexInfo(space="KB.Onboarding", wiki="main"))
    spec = _export(index)["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseXWikiRequest)
    assert (spec["space"], spec["wiki"]) == ("KB.Onboarding", "main")
    assert spec["setting_id"] == "My Integration"


def _sharepoint(**kw) -> IndexInfo:
    info = SharePointIndexInfo(
        site_url="https://t.sharepoint.com/sites/x",
        include_pages=True,
        include_documents=False,
        include_lists=True,
        max_file_size_mb=20,
        files_filter="*.docx",
        auth_type="oauth_custom",
        oauth_client_id="client-1",
        oauth_tenant_id="tenant-1",
        access_token="LIVE-TOKEN-VALUE",
        **kw,
    )
    return _index("knowledge_base_sharepoint", sharepoint=info, embeddings_model="emb-1")


def test_sharepoint_spec_covers_every_update_field():
    spec = _export(_sharepoint())["spec"]
    _assert_covers(spec, UpdateKnowledgeBaseSharePointRequest)
    assert spec["site_url"] == "https://t.sharepoint.com/sites/x"
    assert spec["include_documents"] is False
    assert spec["max_file_size_mb"] == 20
    assert spec["embedding_model"] == "emb-1"
    assert spec["auth_type"] == "oauth_custom"
    assert spec["setting_id"] == "My Integration"


def test_sharepoint_token_is_never_exported():
    from unittest.mock import patch

    exporter = DatasourceDispatchExporter(resolver=_resolver())
    with patch(f"{BASE}.IndexInfo.get_by_id", return_value=_sharepoint()):
        body = to_canonical_bytes(exporter.export(INDEX_ID, _admin()))
    assert b"LIVE-TOKEN-VALUE" not in body
    assert "access_token" not in json.loads(body)["spec"]  # transient: the envelope has no field for it


def _provider_index() -> IndexInfo:
    return _index(
        "provider",
        provider_fields=IndexInfoProviderFields(
            provider_id="provider-uuid-7",
            toolkit_id="toolkit-1",
            base_params={"url": "https://aice", "api_key": "ENCRYPTED-BLOB", "gen_id": "uuid-gen", "managed": "m"},
            create_params={"depth": "3"},
        ),
    )


def _param(name: str, kind: str) -> ProviderDataSourceTypeSchema.Parameter:
    return ProviderDataSourceTypeSchema.Parameter(
        name=name, description="", required=False, parameter_type=ProviderToolkitConfigParameter.ParameterType(kind)
    )


@pytest.fixture
def _provider(monkeypatch):
    schema = SimpleNamespace(
        base_schema=ProviderDataSourceTypeSchema(
            description="",
            parameters=[
                _param("url", "String"),
                _param("api_key", "Secret"),
                _param("gen_id", "UUID"),
                _param("managed", "String"),
            ],
        ),
        create_schema=ProviderDataSourceTypeSchema(description="", parameters=[_param("depth", "String")]),
    )
    toolkit = SimpleNamespace(toolkit_id="toolkit-1", get_managed_fields=lambda: {"managed": "m"})
    provider = SimpleNamespace(id="provider-uuid-7", name="AICE", provided_toolkits=[toolkit])
    monkeypatch.setattr(f"{BASE}.provider.Provider.get_by_id", lambda provider_id: provider)
    monkeypatch.setattr(
        f"{BASE}.provider.ProviderDatasourceSchemaService",
        lambda prov, user: SimpleNamespace(schema_for=lambda toolkit_id, include_autofilled=False: schema),
    )


def test_provider_spec_carries_provider_name_toolkit_and_settings(_provider):
    doc = _export(_provider_index())
    assert doc["meta"] == {"kind": "datasource", "name": "my-ds"}
    spec = doc["spec"]
    assert spec["type"] == "provider"
    assert (spec["provider_name"], spec["toolkit_id"]) == ("AICE", "toolkit-1")
    assert spec["settings"] == {"url": "https://aice", "api_key": "env:MY_DS_API_KEY", "depth": "3"}
    assert not {"index_type", "provider", "fields", "project_space_visible"} & set(spec)


def test_provider_spec_has_no_schedule_while_other_types_keep_it(_provider, monkeypatch):
    # The SDK provider request has no schedule fields, so the real engine could never write them.
    def _must_not_be_called(user_id, ids):
        raise AssertionError("schedule lookup is pointless for a provider datasource")

    monkeypatch.setattr(
        f"{BASE}.base.SchedulerSettingsService.get_scheduler_settings_for_datasources", _must_not_be_called
    )
    spec = _export(_provider_index())["spec"]  # _export also asserts conformance to the committed envelope schema
    assert "cron_expression" not in spec and "timezone" not in spec


def test_non_provider_spec_still_carries_the_schedule():
    spec = _export(_code("git"))["spec"]
    assert (spec["cron_expression"], spec["timezone"]) == ("0 3 * * *", "Europe/Warsaw")


def test_provider_setting_with_a_secret_looking_name_is_always_a_reference(_provider):
    # The envelope refuses a literal under such a name even if the provider schema calls the parameter harmless.
    index = _provider_index()
    index.provider_fields.base_params["access_key_id"] = "AKIA-LITERAL"
    assert _export(index)["spec"]["settings"]["access_key_id"] == "env:MY_DS_ACCESS_KEY_ID"


def test_provider_missing_provider_is_an_export_error(monkeypatch):
    from unittest.mock import patch

    def _missing(provider_id):
        raise NotFoundError("gone", None, {})

    monkeypatch.setattr(f"{BASE}.provider.Provider.get_by_id", _missing)
    with patch(f"{BASE}.IndexInfo.get_by_id", return_value=_provider_index()):
        with pytest.raises(ExportError):
            DatasourceDispatchExporter(resolver=_resolver()).export(INDEX_ID, _admin())


def test_export_is_byte_stable():
    from unittest.mock import patch

    exporter = DatasourceDispatchExporter(resolver=_resolver())
    with patch(f"{BASE}.IndexInfo.get_by_id", return_value=_code("git")):
        first = to_canonical_bytes(exporter.export(INDEX_ID, _admin()))
        second = to_canonical_bytes(exporter.export(INDEX_ID, _admin()))
    assert first == second


@pytest.mark.parametrize("index_type", ["knowledge_base_file", "something_new"])
def test_unsupported_type_error_names_the_type(index_type):
    with pytest.raises(ExportError) as exc:
        _export(_index(index_type))
    assert index_type in exc.value.message


def test_missing_datasource_is_404():
    from unittest.mock import patch

    with patch(f"{BASE}.IndexInfo.get_by_id", side_effect=KeyError("x")):
        with pytest.raises(ExtendedHTTPException) as exc:
            DatasourceDispatchExporter(resolver=_resolver()).export("nope", _admin())
    assert exc.value.code == 404


def test_user_without_read_access_is_forbidden():
    from unittest.mock import patch

    stranger = User(id="stranger", username="stranger")
    stranger.is_admin = False
    index = _code("git")
    index.project_space_visible = False
    index.created_by = CreatedByUser(id="owner-1", username="owner-1", name="Owner")
    with patch(f"{BASE}.IndexInfo.get_by_id", return_value=index):
        with pytest.raises(ExtendedHTTPException) as exc:
            DatasourceDispatchExporter(resolver=_resolver()).export(INDEX_ID, stranger)
    assert exc.value.code == 403


def test_svn_code_variant_is_its_own_envelope_type():
    spec = _export(_code("svn", "summary"))["spec"]
    assert (spec["type"], spec["index_type"], spec["link"]) == ("svn", "summary", "https://git.example/r.git")


def test_code_models_and_git_faq_embedding_model_are_exported():
    code = _export(_code("git"))["spec"]
    assert code["embeddings_model"] == "emb"
    faq = _export(_index("knowledge_base_git_faq", embeddings_model="emb-2"))["spec"]
    assert faq["embedding_model"] == "emb-2"


def test_xray_without_jql_is_an_export_error():
    with pytest.raises(ExportError, match="JQL"):
        _export(_index("knowledge_base_xray"))


def test_registry_keys():
    assert {"code", "summary", "chunk-summary", "knowledge_base_confluence", "knowledge_base_jira"} <= set(
        DATASOURCE_EXPORTERS
    )
    assert "llm_routing_google" in DATASOURCE_EXPORTERS
    assert {"knowledge_base_git_faq", "knowledge_base_sharepoint", "knowledge_base_xwiki", "provider"} <= set(
        DATASOURCE_EXPORTERS
    )


def test_schedule_is_looked_up_for_the_datasource_owner_not_the_requester(monkeypatch):
    seen = []

    def _schedules(user_id, ids):
        seen.append(user_id)
        return {INDEX_ID: {"cron_expression": "0 3 * * *", "timezone": "UTC"}}

    monkeypatch.setattr(f"{BASE}.base.SchedulerSettingsService.get_scheduler_settings_for_datasources", _schedules)
    index = _code("git")
    index.created_by = CreatedByUser(id="owner-7", username="owner", name="Owner")
    _export(index)
    assert seen == ["owner-7"]


def test_null_vcs_type_is_not_a_500():
    index = _code("git")
    index.vcs_type = None
    spec = _export(index)["spec"]
    assert "vcs_type" not in spec


def test_provider_param_without_schema_entry_is_treated_as_secret(_provider):
    index = _provider_index()
    index.provider_fields.base_params["mystery"] = "LEAKED-VALUE"
    assert "LEAKED-VALUE" not in json.dumps(_export(index))
    assert _export(index)["spec"]["settings"]["mystery"] == "env:MY_DS_MYSTERY"


def test_provider_missing_toolkit_is_an_export_error(_provider):
    index = _provider_index()
    index.provider_fields.toolkit_id = "gone"
    from unittest.mock import patch

    with patch(f"{BASE}.IndexInfo.get_by_id", return_value=index):
        with pytest.raises(ExportError, match="toolkit"):
            DatasourceDispatchExporter(resolver=_resolver()).export(INDEX_ID, _admin())


# CR-003
LEAKY_LINKS = [
    "https://user:ghp_s3cr3tvalue@git.example/r.git",
    "https://oauth2:s3cr3tvalue@git.example/r.git",
    "https://token-only@git.example/r.git",
    "https://git.example/r.git?private_token=s3cr3tvalue",
    "svn://u:s3cr3tvalue@svn.example/repo",
]


@pytest.mark.parametrize("link", LEAKY_LINKS)
@pytest.mark.parametrize("vcs", ["git", "svn"])
def test_code_and_svn_links_with_credentials_are_refused_not_referenced(vcs, link):
    # The engine's credential resolver does not walk a datasource `link`, so a reference there is never
    # substituted on apply; refuse the export instead of emitting one (CR 2aca0289).
    index = _code(vcs)
    index.link = link
    with pytest.raises(ExportError, match="credential") as exc:
        _export(index)
    assert "s3cr3tvalue" not in exc.value.message


@pytest.mark.parametrize("link", LEAKY_LINKS)
def test_git_faq_link_with_credentials_is_env_ref(link):
    spec = _export(_index("knowledge_base_git_faq", link=link))["spec"]
    assert spec["link"] == "env:MY_DS_LINK"


def test_google_doc_and_sharepoint_urls_with_credentials_are_env_refs():
    google = _export(_index("llm_routing_google", google_doc_link="https://docs.example/d?api_key=s3cr3tvalue"))["spec"]
    assert google["google_doc"] == "env:MY_DS_GOOGLE_DOC"


def test_sharepoint_site_url_with_credentials_is_refused_not_leaked():
    # The envelope accepts only a plain https URL for site_url, so a reference cannot replace it.
    sp = _sharepoint()
    sp.sharepoint.site_url = "https://u:s3cr3tvalue@t.sharepoint.com/sites/x"
    with pytest.raises(ExportError, match="credentials") as exc:
        _export(sp)
    assert "s3cr3tvalue" not in exc.value.message


def test_clean_links_are_kept():
    assert _export(_code("git"))["spec"]["link"] == "https://git.example/r.git"


# CR-010 / CR-004
def test_provider_setting_named_like_a_document_key_is_an_export_error(_provider):
    index = _provider_index()
    index.provider_fields.create_params = {"name": "x"}
    schema_param = _param("name", "String")
    assert schema_param  # the clash is judged on the exported key, whatever the schema says
    with pytest.raises(ExportError, match="document keys"):
        _export(index)


def test_provider_base_and_create_params_with_one_name_are_rejected(_provider):
    index = _provider_index()
    index.provider_fields.create_params = {"url": "https://other"}
    with pytest.raises(ExportError, match="more than once"):
        _export(index)


def test_provider_without_provider_fields_is_an_export_error():
    with pytest.raises(ExportError, match="no provider configuration"):
        _export(_index("provider"))


def test_provider_with_removed_toolkit_is_a_clean_error_before_schema_lookup(monkeypatch):
    provider = SimpleNamespace(id="provider-uuid-7", name="AICE", provided_toolkits=[])
    monkeypatch.setattr(f"{BASE}.provider.Provider.get_by_id", lambda provider_id: provider)

    def _boom(prov, user):
        raise AssertionError("schema must not be consulted for a removed toolkit")

    monkeypatch.setattr(f"{BASE}.provider.ProviderDatasourceSchemaService", _boom)
    with pytest.raises(ExportError, match="no longer exists"):
        _export(_provider_index())


def test_provider_schema_service_failure_is_an_export_error(monkeypatch):
    toolkit = SimpleNamespace(toolkit_id="toolkit-1", get_managed_fields=lambda: {})
    provider = SimpleNamespace(id="provider-uuid-7", name="AICE", provided_toolkits=[toolkit])
    monkeypatch.setattr(f"{BASE}.provider.Provider.get_by_id", lambda provider_id: provider)

    def _failing(prov, user):
        def schema_for(toolkit_id, include_autofilled=False):
            raise ExtendedHTTPException(code=404, message="Toolkit not found")

        return SimpleNamespace(schema_for=schema_for)

    monkeypatch.setattr(f"{BASE}.provider.ProviderDatasourceSchemaService", _failing)
    with pytest.raises(ExportError, match="my-ds"):
        _export(_provider_index())


def test_sharepoint_without_site_url_is_an_export_error():
    sp = _sharepoint()
    sp.sharepoint.site_url = ""
    with pytest.raises(ExportError):
        _export(sp)


def test_svn_without_link_is_an_export_error():
    index = _code("svn")
    index.link = None
    with pytest.raises(ExportError, match="no link"):
        _export(index)


# MR 4545 review: a datasource without the settings its type needs is refused, as sharepoint/provider/svn already do
@pytest.mark.parametrize(
    "index_type",
    [
        "knowledge_base_confluence",
        "knowledge_base_xwiki",
        "knowledge_base_azure_devops_wiki",
        "knowledge_base_azure_devops_work_item",
    ],
)
def test_datasource_without_its_type_block_is_an_export_error(index_type):
    with pytest.raises(ExportError, match="has no .* settings"):
        _export(_index(index_type))


def test_git_datasource_without_a_link_is_an_export_error():
    index = _code("git")
    index.link = None
    with pytest.raises(ExportError, match="no link"):
        _export(index)
