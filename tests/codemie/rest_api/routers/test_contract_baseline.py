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


"""Contract baseline characterization tests (EPMCDME-15310).

These tests pin the CURRENT HTTP contract (status code, response keys, one validation failure) of every
write route that the idempotent-write work touches, plus the documented silent rewrites (AC4). They must
pass on unmodified code and must stay unedited while the idempotency fixes land, so any contract drift
shows up here first.

Routes are exercised through TestClient on the real app; only persistence and external collaborators are
mocked.
"""

import importlib
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from codemie.core.constants import SENSITIVE_VALUE_MASK
from codemie.core.exceptions import ValidationException
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import (
    Assistant,
    Context,
    ContextType,
    MCPServerDetails,
    PromptVariable,
)
from codemie.rest_api.models.category import Category
from codemie.rest_api.models.skill import SkillDetailResponse, SkillVisibility
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.mcp.access_control import MCPAccessControlService

ROUTERS = "codemie.rest_api.routers"
INDEX = f"{ROUTERS}.index"
ASSISTANT = f"{ROUTERS}.assistant.management"
WORKFLOW = f"{ROUTERS}.workflow"
PROJECT = "demo"

USER = User(id="user-1", username="tester", name="Tester", is_admin=True, is_maintainer=True, project_names=[PROJECT])


@pytest.fixture(autouse=True)
def _auth():
    """Authenticate every request as the admin test user and expose it on request.state."""

    def _authenticate(request: Request = None):
        if request is not None:
            request.state.user = USER
        return USER

    app.dependency_overrides[authenticate] = _authenticate
    yield
    app.dependency_overrides = {}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def headers():
    return {"user-id": USER.id}


def _enter(stack: ExitStack, *targets: str, **kwargs):
    """Enter patches for all targets on the stack and return the mocks in order."""
    return [stack.enter_context(patch(target, **kwargs)) for target in targets]


INDEX_MODULES = (
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


def _patch_index(stack: ExitStack, name: str, **kwargs):
    """Patch `name` in every routers.index submodule that imports it (each submodule looks it up in its own globals).

    Dotted names (Class.attr) patch the shared class once. Returns the last mock entered."""
    root = name.split(".")[0]
    modules = [m for m in INDEX_MODULES if hasattr(importlib.import_module(f"{INDEX}.{m}"), root)]
    assert modules, f"{name} is not imported by any routers.index submodule"
    if "." in name:
        modules = modules[:1]
    return [stack.enter_context(patch(f"{INDEX}.{m}.{name}", **kwargs)) for m in modules][-1]


def _enter_index(stack: ExitStack, *names: str, **kwargs):
    """Index-package counterpart of `_enter`: returns one mock per name."""
    return [_patch_index(stack, name, **kwargs) for name in names]


# ---------------------------------------------------------------------------
# Datasource routes (keyed by project_name + name)
# ---------------------------------------------------------------------------

KB_BASE = {"project_name": PROJECT, "name": "kb-1"}
KB_ROUTES = [
    pytest.param("put", "/v1/index/knowledge_base/google", {**KB_BASE, "description": "d"}, id="google"),
    pytest.param("put", "/v1/index/knowledge_base/git_faq", {**KB_BASE, "description": "d"}, id="git_faq_put"),
    pytest.param("put", "/v1/index/knowledge_base/confluence", {**KB_BASE, "cql": "space = A"}, id="confluence"),
    pytest.param("put", "/v1/index/knowledge_base/jira", {**KB_BASE, "jql": "project = A"}, id="jira"),
    pytest.param("put", "/v1/index/knowledge_base/xray", {**KB_BASE, "jql": "project = A"}, id="xray"),
    pytest.param(
        "put", "/v1/index/knowledge_base/azure_devops_wiki", {**KB_BASE, "wiki_query": "q"}, id="azure_devops_wiki"
    ),
    pytest.param("put", "/v1/index/knowledge_base/xwiki", {**KB_BASE, "space": "S", "wiki": "xwiki"}, id="xwiki"),
    pytest.param(
        "put",
        "/v1/index/knowledge_base/azure_devops_work_item",
        {**KB_BASE, "wiql_query": "select 1"},
        id="azure_devops_work_item",
    ),
    pytest.param(
        "put",
        "/v1/index/knowledge_base/sharepoint",
        {**KB_BASE, "site_url": "https://x.sharepoint.com/sites/a"},
        id="sharepoint",
    ),
]


def _kb_index_mock() -> MagicMock:
    kb_index = MagicMock()
    kb_index.setting_id = "setting-1"
    kb_index.project_space_visible = True
    kb_index.xwiki = MagicMock()
    kb_index.sharepoint = MagicMock(auth_type="integration")
    return kb_index


def _kb_patches(stack: ExitStack, found: list | None):
    lookup = _patch_index(stack, "KnowledgeBaseIndexInfo.filter_by_project_and_repo")
    lookup.return_value = found
    _enter_index(stack, "_validate_project_change", "_update_datasource_scheduler", "SettingsService")
    return lookup


class TestDatasourceKnowledgeBaseRoutes:
    @pytest.mark.parametrize(("method", "path", "body"), KB_ROUTES)
    def test_metadata_update_status_and_keys(self, client, headers, method, path, body):
        kb_index = _kb_index_mock()
        with ExitStack() as stack:
            _kb_patches(stack, [kb_index])
            response = getattr(client, method)(path, json=body, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message"}
        kb_index.update_index.assert_called_once()

    @pytest.mark.parametrize(("method", "path", "body"), KB_ROUTES)
    def test_missing_name_is_validation_error(self, client, headers, method, path, body):
        invalid = {k: v for k, v in body.items() if k != "name"}
        with ExitStack() as stack:
            lookup = _kb_patches(stack, [_kb_index_mock()])
            response = getattr(client, method)(path, json=invalid, headers=headers)

        assert response.status_code == 422
        lookup.assert_not_called()

    @pytest.mark.parametrize(("method", "path", "body"), KB_ROUTES)
    def test_unknown_datasource_is_404(self, client, headers, method, path, body):
        with ExitStack() as stack:
            _kb_patches(stack, [])
            response = getattr(client, method)(path, json=body, headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Index not found"


class TestGitFaqPostAliasIsShadowed:
    """POST /index/knowledge_base/git_faq is declared on the update handler too, but the create route is
    registered first and wins, so an update-shaped body is rejected as an incomplete create request."""

    def test_update_shaped_post_hits_create_route(self, client, headers):
        with ExitStack() as stack:
            lookup = _kb_patches(stack, [_kb_index_mock()])
            response = client.post(
                "/v1/index/knowledge_base/git_faq", json={**KB_BASE, "description": "d"}, headers=headers
            )

        assert response.status_code == 422
        assert "link: Field required" in response.json()["error"]["message"]
        lookup.assert_not_called()


class TestAzureDevOpsWorkItemPostRoute:
    """POST create route: scheduling a background indexing run; an existing name is rejected, never updated."""

    PATH = "/v1/index/knowledge_base/azure_devops_work_item"
    BODY = {**KB_BASE, "description": "d", "wiql_query": "select 1"}

    def _patches(self, stack: ExitStack, existing: list):
        unique = _patch_index(stack, "IndexInfo.filter_by_project_and_repo")
        unique.return_value = existing
        _enter_index(stack, "_kb_demo_user_check", "SettingsService")
        return _patch_index(stack, "AzureDevOpsWorkItemDatasourceProcessor")

    def test_status_and_keys(self, client, headers):
        with ExitStack() as stack:
            processor = self._patches(stack, [])
            response = client.post(self.PATH, json=self.BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message"}
        assert response.json()["message"] == "Indexing of datasource kb-1 has been started in the background"
        processor.return_value.schedule.assert_called_once()

    def test_missing_name_is_validation_error(self, client, headers):
        invalid = {k: v for k, v in self.BODY.items() if k != "name"}
        with ExitStack() as stack:
            processor = self._patches(stack, [])
            response = client.post(self.PATH, json=invalid, headers=headers)

        assert response.status_code == 422
        processor.assert_not_called()

    def test_existing_name_is_conflict_and_not_rewritten(self, client, headers):
        with ExitStack() as stack:
            processor = self._patches(stack, [MagicMock()])
            response = client.post(self.PATH, json=self.BODY, headers=headers)

        assert response.status_code == 409
        processor.assert_not_called()


class TestDatasourceFileRoute:
    PATH = "/v1/index/knowledge_base/file"

    def test_status_and_keys(self, client, headers):
        with patch(f"{INDEX}.kb_files.UpdateFileDatasourceUseCase") as use_case:
            use_case.return_value.execute.return_value = {"message": "ok"}
            response = client.put(self.PATH, params={**KB_BASE, "description": "d"}, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message"}
        use_case.return_value.execute.assert_called_once()

    def test_missing_name_is_validation_error(self, client, headers):
        with patch(f"{INDEX}.kb_files.UpdateFileDatasourceUseCase") as use_case:
            response = client.put(self.PATH, params={"project_name": PROJECT}, headers=headers)

        assert response.status_code == 422
        use_case.return_value.execute.assert_not_called()


class TestDatasourceCodeRoutes:
    """Git repo and SVN repo PUT routes (skip_reindex=true keeps them on the metadata-only path)."""

    REPO_PATH = f"/v1/application/{PROJECT}/index/repo-1"
    SVN_PATH = f"/v1/application/{PROJECT}/index/svn/repo-1"
    BODY = {"name": "repo-1", "description": "d"}

    def _common(self, stack: ExitStack):
        index_info = MagicMock(repo_type="git", project_name=PROJECT)
        lookup = _patch_index(stack, "IndexInfo.filter_by_project_and_repo")
        lookup.return_value = [index_info]
        _enter_index(
            stack,
            "request_summary_manager",
            "_validate_project_change",
            "_update_datasource_scheduler",
        )
        _patch_index(stack, "Ability.can", return_value=True)
        return index_info

    def test_repo_put_status_and_keys(self, client, headers):
        with ExitStack() as stack:
            self._common(stack)
            _enter_index(
                stack,
                "Application.get_by_id",
                "_get_repository_with_project_change",
                "_handle_index_and_repository_update",
                "_validate_git_credentials",
            )
            response = client.put(self.REPO_PATH, params={"skip_reindex": "true"}, json=self.BODY, headers=headers)

        assert response.status_code == 201, response.text
        assert set(response.json()) == {"message"}
        assert response.json()["message"] == "repo-1 updated successfully!"

    def test_repo_put_validation_error(self, client, headers):
        body = {**self.BODY, "description": "x" * 501}
        with ExitStack() as stack:
            self._common(stack)
            response = client.put(self.REPO_PATH, params={"skip_reindex": "true"}, json=body, headers=headers)

        assert response.status_code == 422

    def test_svn_put_status_and_keys(self, client, headers):
        svn_repo = MagicMock(link="https://svn.example/r", setting_id="s1")
        svn_repo.name = "repo-1"
        with ExitStack() as stack:
            self._common(stack)
            get_repos = _patch_index(stack, "SVNRepo.get_by_app_id")
            get_repos.return_value = [svn_repo]
            _enter_index(stack, "_update_svn_index_and_repo_fields", "SVNIndexService.validate_credentials")
            response = client.put(self.SVN_PATH, params={"skip_reindex": "true"}, json=self.BODY, headers=headers)

        assert response.status_code == 201, response.text
        assert set(response.json()) == {"message"}

    def test_svn_put_validation_error(self, client, headers):
        body = {**self.BODY, "description": "x" * 501}
        with ExitStack() as stack:
            self._common(stack)
            response = client.put(self.SVN_PATH, params={"skip_reindex": "true"}, json=body, headers=headers)

        assert response.status_code == 422


class TestDatasourceProviderRoute:
    PATH = "/v1/index/provider/idx-1"

    def test_status_and_keys(self, client, headers):
        with ExitStack() as stack:
            find, provider, service = _enter_index(
                stack,
                "IndexInfo.find_by_id",
                "Provider.get_by_id",
                "ProviderDatasourceUpdateService",
            )
            find.return_value = MagicMock()
            _patch_index(stack, "Ability.can", return_value=True)
            provider.return_value = MagicMock()
            service.return_value.updated_message = "updated"
            response = client.put(self.PATH, json={"description": "d"}, headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"message": "updated"}
        service.return_value.run.assert_called_once()

    def test_non_object_body_is_validation_error(self, client, headers):
        response = client.put(self.PATH, json=["not", "an", "object"], headers=headers)

        assert response.status_code == 422

    def test_unknown_index_is_404(self, client, headers):
        with patch(f"{INDEX}.providers.IndexInfo.find_by_id", return_value=None):
            response = client.put(self.PATH, json={"description": "d"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Index not found"


# ---------------------------------------------------------------------------
# Assistants
# ---------------------------------------------------------------------------

ASSISTANT_BODY = {
    "name": "Baseline Assistant",
    "description": "d",
    "system_prompt": "p",
    "project": PROJECT,
    "llm_model_type": "gpt-4o",
    "skip_integration_validation": True,
}


def _stored_assistant(**overrides) -> Assistant:
    fields = {
        "id": "assistant-1",
        "name": "Baseline Assistant",
        "description": "d",
        "system_prompt": "p",
        "project": PROJECT,
        "llm_model_type": "gpt-4o",
    }
    fields.update(overrides)
    return Assistant(**fields)


def _assistant_update_patches(stack: ExitStack, assistant: Assistant):
    """Patch persistence and side-effect collaborators of PUT /assistants/{id}; return the update spy."""
    _enter(
        stack,
        f"{ASSISTANT}.project_access_check",
        f"{ASSISTANT}.check_user_can_access_assistant",
        f"{ASSISTANT}._validate_remote_entities_and_raise",
        f"{ASSISTANT}.GuardrailService.sync_guardrail_assignments_for_entity",
        f"{ASSISTANT}._track_assistant_management_metric",
        f"{ASSISTANT}._track_mcp_usage_changes",
        f"{ASSISTANT}.category_service.validate_category_ids",
    )
    compare = "codemie.service.assistant.assistant_version_compare_service.AssistantVersionCompareService"
    stack.enter_context(patch(f"{compare}.has_configuration_changes", return_value=False))
    stack.enter_context(patch(f"{ASSISTANT}.get_assistant_by_id_or_raise", return_value=assistant))
    return stack.enter_context(patch.object(Assistant, "update"))


def _put_assistant(client, headers, body, assistant, existing_keys=None):
    with ExitStack() as stack:
        update = _assistant_update_patches(stack, assistant)
        if existing_keys is not None:
            stack.enter_context(
                patch(
                    "codemie.service.assistant_service.AssistantService._existing_context_keys",
                    return_value=existing_keys,
                )
            )
        response = client.put(f"/v1/assistants/{assistant.id}", json=body, headers=headers)
    return response, update


class TestAssistantContract:
    def test_create_status_and_keys(self, client, headers):
        with ExitStack() as stack:
            _enter(
                stack,
                f"{ASSISTANT}.project_access_check",
                f"{ASSISTANT}.ensure_application_exists",
                f"{ASSISTANT}.GuardrailService.sync_guardrail_assignments_for_entity",
                f"{ASSISTANT}._track_mcp_usage_on_create",
                f"{ASSISTANT}._track_assistant_management_metric",
                "codemie.service.assistant.assistant_version_service.AssistantVersionService.create_initial_version",
                "codemie.service.llm.model_availability_service.ModelAvailabilityService."
                "validate_model_for_asset_creation",
            )
            stack.enter_context(patch.object(Assistant, "save"))
            response = client.post("/v1/assistants", json=ASSISTANT_BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message", "assistantId", "validation"}
        assert response.json()["message"] == "Specified assistant saved"

    def test_create_missing_name_is_validation_error(self, client, headers):
        body = {k: v for k, v in ASSISTANT_BODY.items() if k != "name"}

        response = client.post("/v1/assistants", json=body, headers=headers)

        assert response.status_code == 422

    def test_update_status_and_keys(self, client, headers):
        response, update = _put_assistant(client, headers, ASSISTANT_BODY, _stored_assistant())

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message", "validation"}
        assert response.json()["message"] == "Specified assistant updated"
        update.assert_called()

    def test_update_missing_name_is_validation_error(self, client, headers):
        body = {k: v for k, v in ASSISTANT_BODY.items() if k != "name"}

        response, update = _put_assistant(client, headers, body, _stored_assistant())

        assert response.status_code == 422
        update.assert_not_called()


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

WORKFLOW_YAML = """
assistants:
  - id: dev
    assistant_id: 196ede41-e7f0-4658-ae99-1dc0d83c8347
    model: 'gpt-4o-2024-11-20'  # inline comment stored as sent
states:
  - id: dev
    assistant_id: dev
    task: do the thing
    next:
      state_id: end
"""
WORKFLOW_BODY = {
    "name": "Baseline Workflow",
    "description": "d",
    "project": PROJECT,
    "mode": "Sequential",
    "yaml_config": WORKFLOW_YAML,
}


def _stored_workflow(**overrides) -> WorkflowConfig:
    fields = {"id": "wf-1", "name": "Baseline Workflow", "description": "d", "project": PROJECT, "yaml_config": ""}
    fields.update(overrides)
    return WorkflowConfig(**fields)


def _workflow_patches(stack: ExitStack, stored: WorkflowConfig):
    _enter(
        stack,
        f"{WORKFLOW}.project_access_check",
        f"{WORKFLOW}.GuardrailService.sync_guardrail_assignments_for_entity",
        f"{WORKFLOW}.GuardrailService.get_entity_guardrail_assignments",
        f"{WORKFLOW}._consumer_slot_warnings_sync",
        f"{WORKFLOW}.MCPAccessControlService.validate_on_save",
        "codemie.service.workflow_service.WorkflowService.validate_for_update",
        "codemie.service.workflow_service.WorkflowService.save_workflow_schema",
        "codemie.service.workflow_service.WorkflowService.create_workflow",
        "codemie.workflows.workflow.WorkflowExecutor.validate_workflow",
        "codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw",
    )
    stack.enter_context(patch(f"{WORKFLOW}._get_project_for_workflow", return_value=None))
    stack.enter_context(patch(f"{WORKFLOW}.collect_consumer_slot_integration_warnings", return_value=[]))
    stack.enter_context(patch(f"{WORKFLOW}.Ability.can", return_value=True))
    stack.enter_context(patch("codemie.service.workflow_service.WorkflowService.get_workflow", return_value=stored))
    return stack.enter_context(
        patch(
            "codemie.service.workflow_service.WorkflowService.update_workflow",
            side_effect=lambda existing, updated, user: updated,
        )
    )


class TestWorkflowContract:
    def test_create_status_and_keys(self, client, headers):
        with ExitStack() as stack:
            _workflow_patches(stack, _stored_workflow())
            stack.enter_context(
                patch(
                    "codemie.service.workflow_service.WorkflowService.create_workflow",
                    side_effect=lambda config, user: config,
                )
            )
            response = client.post("/v1/workflows", json=WORKFLOW_BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message", "data", "warnings"}
        assert response.json()["message"] == "Workflow created successfully"

    def test_create_autonomous_is_rejected(self, client, headers):
        with ExitStack() as stack:
            _workflow_patches(stack, _stored_workflow())
            response = client.post("/v1/workflows", json={**WORKFLOW_BODY, "mode": "Autonomous"}, headers=headers)

        assert response.status_code == 410

    def test_update_status_and_keys(self, client, headers):
        with ExitStack() as stack:
            _workflow_patches(stack, _stored_workflow())
            response = client.put("/v1/workflows/wf-1", json=WORKFLOW_BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"message", "data", "warnings"}
        assert response.json()["message"] == "Workflow updated successfully"

    def test_update_missing_name_is_validation_error(self, client, headers):
        body = {k: v for k, v in WORKFLOW_BODY.items() if k != "name"}
        with ExitStack() as stack:
            _workflow_patches(stack, _stored_workflow())
            response = client.put("/v1/workflows/wf-1", json=body, headers=headers)

        assert response.status_code == 422

    def test_update_unparseable_yaml_is_400(self, client, headers):
        with ExitStack() as stack:
            _workflow_patches(stack, _stored_workflow())
            response = client.put(
                "/v1/workflows/wf-1", json={**WORKFLOW_BODY, "yaml_config": "a: [unclosed"}, headers=headers
            )

        assert response.status_code == 400
        assert response.json()["error"]["message"] == "Workflow Configuration error"


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

SKILL_BODY = {
    "name": "baseline-skill",
    "description": "A skill description long enough to be valid",
    "content": "Skill content. " * 20,
    "project": PROJECT,
}


def _skill_response() -> SkillDetailResponse:
    return SkillDetailResponse(
        id="skill-1",
        name="baseline-skill",
        description="A skill description long enough to be valid",
        content="Skill content",
        project=PROJECT,
        visibility=SkillVisibility.PRIVATE,
        categories=[],
        created_date=datetime(2024, 1, 1, tzinfo=UTC),
    )


SKILL_RESPONSE_KEYS = set(SkillDetailResponse.model_fields) - {"created_date", "updated_date"} | {
    "createdDate",
    "updatedDate",
}


class TestSkillContract:
    def test_create_status_and_keys(self, client, headers):
        with patch(f"{ROUTERS}.skill.SkillService.create_skill", return_value=_skill_response()) as create:
            response = client.post("/v1/skills", json=SKILL_BODY, headers=headers)

        assert response.status_code == 201, response.text
        assert set(response.json()) == SKILL_RESPONSE_KEYS
        create.assert_called_once()

    def test_create_short_name_is_validation_error(self, client, headers):
        with patch(f"{ROUTERS}.skill.SkillService.create_skill") as create:
            response = client.post("/v1/skills", json={**SKILL_BODY, "name": "ab"}, headers=headers)

        assert response.status_code == 422
        create.assert_not_called()

    def test_update_status_and_keys(self, client, headers):
        with patch(f"{ROUTERS}.skill.SkillService.update_skill", return_value=_skill_response()) as update:
            response = client.put("/v1/skills/skill-1", json=SKILL_BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == SKILL_RESPONSE_KEYS
        update.assert_called_once()

    def test_update_short_name_is_validation_error(self, client, headers):
        with patch(f"{ROUTERS}.skill.SkillService.update_skill") as update:
            response = client.put("/v1/skills/skill-1", json={"name": "ab"}, headers=headers)

        assert response.status_code == 422
        update.assert_not_called()


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@pytest.fixture
def category_admin():
    from codemie.rest_api.routers import category as category_router

    app.dependency_overrides[category_router.admin_access_only] = lambda: None
    yield


def _category() -> Category:
    return Category(
        id="engineering",
        name="Engineering",
        description="d",
        date=datetime(2024, 1, 1, tzinfo=UTC),
        update_date=datetime(2024, 1, 2, tzinfo=UTC),
    )


CATEGORY_KEYS = {
    "id",
    "name",
    "description",
    "marketplaceAssistantCount",
    "projectAssistantCount",
    "createdAt",
    "updatedAt",
}
STATS = {"marketplace_assistants_count": 0, "project_assistants_count": 0}


class TestCategoryContract:
    SVC = f"{ROUTERS}.category.category_service"

    def test_create_status_and_keys(self, client, headers, category_admin):
        with (
            patch(f"{self.SVC}.create_category", return_value=_category()),
            patch(f"{self.SVC}.get_category_stats", return_value=STATS),
        ):
            response = client.post("/v1/assistants/categories", json={"name": "Engineering"}, headers=headers)

        assert response.status_code == 201, response.text
        assert set(response.json()) == CATEGORY_KEYS

    def test_create_empty_name_is_validation_error(self, client, headers, category_admin):
        response = client.post("/v1/assistants/categories", json={"name": ""}, headers=headers)

        assert response.status_code == 422

    def test_create_duplicate_is_400(self, client, headers, category_admin):
        with patch(f"{self.SVC}.create_category", side_effect=ValueError("exists")):
            response = client.post("/v1/assistants/categories", json={"name": "Engineering"}, headers=headers)

        assert response.status_code == 400

    def test_update_status_and_keys(self, client, headers, category_admin):
        with (
            patch(f"{self.SVC}.update_category", return_value=_category()),
            patch(f"{self.SVC}.get_category_stats", return_value=STATS),
        ):
            response = client.put(
                "/v1/assistants/categories/engineering", json={"name": "Engineering"}, headers=headers
            )

        assert response.status_code == 200, response.text
        assert set(response.json()) == CATEGORY_KEYS

    def test_update_empty_name_is_validation_error(self, client, headers, category_admin):
        response = client.put("/v1/assistants/categories/engineering", json={"name": ""}, headers=headers)

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Project and user settings
# ---------------------------------------------------------------------------

SETTING_BODY = {
    "project_name": PROJECT,
    "alias": "aws-main",
    "credential_type": "AWS",
    "credential_values": [{"key": "aws_key", "value": "v"}],
}
SETTINGS_SERVICE = "codemie.service.settings.settings.SettingsService"


class TestSettingsContract:
    @pytest.mark.parametrize("scope", ["project", "user"])
    def test_create_status_and_keys(self, client, headers, scope):
        with patch(f"{SETTINGS_SERVICE}.create_setting") as create:
            response = client.post(f"/v1/settings/{scope}", json=SETTING_BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"message": "Specified credentials saved"}
        create.assert_called_once()

    @pytest.mark.parametrize("scope", ["project", "user"])
    def test_create_missing_alias_is_validation_error(self, client, headers, scope):
        body = {k: v for k, v in SETTING_BODY.items() if k != "alias"}
        with patch(f"{SETTINGS_SERVICE}.create_setting") as create:
            response = client.post(f"/v1/settings/{scope}", json=body, headers=headers)

        assert response.status_code == 422
        create.assert_not_called()

    @pytest.mark.parametrize("scope", ["project", "user"])
    def test_update_status_and_keys(self, client, headers, scope):
        with ExitStack() as stack:
            _enter(stack, f"{SETTINGS_SERVICE}.get_setting_ability")
            # The handler compares the request's project with the stored one to detect a project change;
            # the stored setting must be in the request's project for this to be a plain same-project update.
            stack.enter_context(
                patch(f"{ROUTERS}.project_settings.Settings.get_by_id", return_value=MagicMock(project_name=PROJECT))
            )
            stack.enter_context(patch("codemie.core.ability.Ability.can", return_value=True))
            stack.enter_context(patch(f"{SETTINGS_SERVICE}.is_budget_integration_hidden_from", return_value=False))
            update = stack.enter_context(patch(f"{SETTINGS_SERVICE}.update_settings"))
            response = client.put(f"/v1/settings/{scope}/setting-1", json=SETTING_BODY, headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"message": "Specified credentials updated"}
        update.assert_called_once()

    @pytest.mark.parametrize("scope", ["project", "user"])
    def test_update_missing_alias_is_validation_error(self, client, headers, scope):
        body = {k: v for k, v in SETTING_BODY.items() if k != "alias"}

        response = client.put(f"/v1/settings/{scope}/setting-1", json=body, headers=headers)

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# AC4: documented silent rewrites
# ---------------------------------------------------------------------------


class TestSilentRewrites:
    def test_assistant_put_drops_context_missing_from_index_info_without_error(self, client, headers):
        alive = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
        gone = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-gone")
        assistant = _stored_assistant()
        body = {**ASSISTANT_BODY, "context": [alive.model_dump(), gone.model_dump()]}

        response, update = _put_assistant(
            client, headers, body, assistant, existing_keys={("kb-alive", ContextType.KNOWLEDGE_BASE)}
        )

        assert response.status_code == 200, response.text
        assert response.json()["message"] == "Specified assistant updated"
        assert [c.name for c in assistant.context] == ["kb-alive"]
        assert update.called

    def test_assistant_put_restores_masked_sensitive_prompt_variable_from_stored_value(self, client, headers):
        stored_secret = "stored-ciphertext"
        assistant = _stored_assistant(
            prompt_variables=[PromptVariable(key="token", default_value=stored_secret, is_sensitive=True)]
        )
        body = {
            **ASSISTANT_BODY,
            "prompt_variables": [{"key": "token", "default_value": SENSITIVE_VALUE_MASK, "is_sensitive": True}],
        }

        response, _ = _put_assistant(client, headers, body, assistant)

        assert response.status_code == 200, response.text
        assert assistant.prompt_variables[0].default_value == stored_secret

    def test_workflow_put_stores_yaml_config_as_sent_when_no_model_id_changes(self, client, headers):
        captured = {}

        def _capture(existing, updated, user):
            captured["config"] = updated
            return updated

        with ExitStack() as stack:
            update = _workflow_patches(stack, _stored_workflow())
            update.side_effect = _capture
            response = client.put("/v1/workflows/wf-1", json=WORKFLOW_BODY, headers=headers)

        assert response.status_code == 200, response.text
        stored_yaml = captured["config"].yaml_config
        assert stored_yaml == WORKFLOW_YAML
        assert "inline comment stored as sent" in stored_yaml

    def test_sanitize_for_save_returns_servers_unmutated(self):
        servers = [MCPServerDetails(name="ok", command="npx", enabled=True)]
        snapshot = [s.model_dump() for s in servers]
        mode = MagicMock()
        mode.is_component_enabled.return_value = False

        with patch("codemie.service.mcp.access_control.customer_config", mode):
            result = MCPAccessControlService.sanitize_for_save(servers)

        assert result == servers
        assert all(a is b for a, b in zip(result, servers, strict=True))
        assert [s.model_dump() for s in servers] == snapshot

    def test_sanitize_for_save_none_becomes_empty_list(self):
        assert MCPAccessControlService.sanitize_for_save(None) == []

    def test_sanitize_for_save_raises_on_violation_and_leaves_servers_unmutated(self):
        servers = [MCPServerDetails(name="rogue", command="npx", enabled=True)]
        snapshot = [s.model_dump() for s in servers]
        mode = MagicMock()
        mode.is_component_enabled.return_value = True

        with patch("codemie.service.mcp.access_control.customer_config", mode):
            with pytest.raises(ValidationException, match="rogue"):
                MCPAccessControlService.sanitize_for_save(servers)

        assert [s.model_dump() for s in servers] == snapshot
