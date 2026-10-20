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


"""AC1 idempotency tests (EPMCDME-15310): re-PUT of an unchanged document leaves the stored entity unchanged.

Every test goes through TestClient on the real app and applies the same document twice. Persistence is
stateful: a real in-memory SQLite database (the repo's SQLite pattern from ``tests/codemie/conftest.py``,
extended to the entity tables) is patched in as ``PostgresClient.get_engine`` so the second PUT sees what the
first one stored. The stored entity is read straight from the database rows, never from the HTTP response.

"Unchanged" means every user-settable field equal, ignoring ``update_date``/``updated_date``/
``last_reindex_date`` (see ``assert_stored_unchanged``). Settings are asserted on Plain and Base64
encryption, whose ciphertext is deterministic, so no decrypt-and-compare is needed. A red test here is a
proven rewrite of a valid unchanged document and is intentionally left failing.
"""

import json
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic.json import pydantic_encoder
from sqlalchemy import MetaData, create_engine, insert
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select

from codemie.clients.postgres import PostgresClient
from codemie.core.constants import SENSITIVE_VALUE_MASK
from codemie.core.models import CreatedByUser
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration, ContextType
from codemie.rest_api.models.category import Category
from codemie.rest_api.models.guardrail import Guardrail, GuardrailAssignment
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.settings import Settings, SettingType
from codemie.rest_api.models.skill import Skill
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.encryption.base_encryption_service import Base64EncryptionService, PlainEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory
from codemie.service.settings.base_settings import BaseSettingsService
from codemie_tools.base.models import CredentialTypes
from tests.codemie.rest_api.routers.idempotency_helpers import assert_stored_unchanged

ROUTERS = "codemie.rest_api.routers"
ASSISTANT = f"{ROUTERS}.assistant.management"
PROJECT = "demo"
GUARDRAIL_ID = "guardrail-1"

USER = User(id="user-1", username="tester", name="Tester", is_admin=True, is_maintainer=True, project_names=[PROJECT])
CREATOR = CreatedByUser(id=USER.id, username=USER.username, name=USER.name)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


# ---------------------------------------------------------------------------
# Harness: auth, client, in-memory database, encryption
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _auth():
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


TABLE_MODELS = (
    Assistant,
    AssistantConfiguration,
    Category,
    Guardrail,
    GuardrailAssignment,
    IndexInfo,
    Settings,
    Skill,
    WorkflowConfig,
)


def _plain_get_by_id(cls, id_: str):
    """Assistant.get_by_id / Skill.get_by_id outer-join ``applications`` only to add a display name."""
    with Session(PostgresClient.get_engine()) as session:
        result = session.get(cls, id_)
    if result is None:
        raise KeyError(f"No {cls.__tablename__} found with id {id_}")
    return result


@pytest.fixture
def db(monkeypatch):
    """Real in-memory SQLite database with the entity tables, standing in for PostgreSQL."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        json_serializer=lambda v: json.dumps(v, default=pydantic_encoder),
    )
    metadata = MetaData()
    for model in TABLE_MODELS:
        model.__table__.to_metadata(metadata).indexes.clear()  # PostgreSQL-only (GIN/expression) indexes
    metadata.create_all(engine)
    monkeypatch.setattr(PostgresClient, "get_engine", classmethod(lambda cls: engine))
    monkeypatch.setattr(Assistant, "get_by_id", classmethod(_plain_get_by_id))
    monkeypatch.setattr(Skill, "get_by_id", classmethod(_plain_get_by_id))
    yield engine
    engine.dispose()


@pytest.fixture(params=[PlainEncryptionService, Base64EncryptionService], ids=["plain", "base64"])
def encryption(request, monkeypatch):
    """Deterministic encryption services: equal plaintext gives equal ciphertext."""
    service = request.param()
    monkeypatch.setattr(BaseSettingsService, "encryption_service", service)
    monkeypatch.setattr(EncryptionFactory, "get_current_encryption_service", classmethod(lambda cls: service))
    return service


def _dump(model: Any) -> dict:
    return model.model_dump(mode="json")


def _read(db, model, row_id: str) -> dict:
    """Snapshot of the stored row, as the database holds it."""
    with Session(db) as session:
        row = session.get(model, row_id)
        assert row is not None, f"{model.__name__} {row_id} is not stored"
        return _dump(row)


def _read_all(db, model, **filters) -> list[dict]:
    with Session(db) as session:
        statement = select(model)
        for column, value in filters.items():
            statement = statement.where(getattr(model, column) == value)
        return [_dump(row) for row in session.exec(statement).all()]


def _put(client, headers, path: str, body: dict, **params) -> None:
    response = client.put(path, json=body, headers=headers, params=params)
    assert response.status_code == 200, response.text


def _put_twice(client, headers, path: str, body: dict, read) -> tuple[Any, Any]:
    """Apply the same document twice; return what ``read`` sees in storage after each apply."""
    _put(client, headers, path, body)
    first = read()
    _put(client, headers, path, body)
    return first, read()


# ---------------------------------------------------------------------------
# Assistants
# ---------------------------------------------------------------------------

ASSISTANT_DOC = {
    "name": "Idempotent Assistant",
    "description": "d",
    "system_prompt": "You are helpful.",
    "project": PROJECT,
    "llm_model_type": "gpt-4o",
    "conversation_starters": ["hello", "help"],
    "temperature": 0.2,
    "toolkits": [{"toolkit": "VCS", "tools": [{"name": "github", "label": "GitHub"}]}],
    "categories": ["engineering"],
    "prompt_variables": [
        {"key": "region", "default_value": "eu"},
        {"key": "token", "default_value": "plain-secret", "is_sensitive": True},
    ],
    "guardrail_assignments": [{"guardrail_id": GUARDRAIL_ID, "mode": "all", "source": "input"}],
    "skip_integration_validation": True,
}


@pytest.fixture
def assistant_env(db, encryption):
    """Collaborators of the assistant routes that are not persistence; the guardrail row is seeded."""
    Guardrail(id=GUARDRAIL_ID, name="gr", project_name=PROJECT, created_by=CREATOR).save()
    targets = (
        f"{ASSISTANT}.project_access_check",
        f"{ASSISTANT}.ensure_application_exists",
        f"{ASSISTANT}.check_user_can_access_assistant",
        f"{ASSISTANT}._validate_remote_entities_and_raise",
        f"{ASSISTANT}._track_assistant_management_metric",
        f"{ASSISTANT}._track_mcp_usage_changes",
        f"{ASSISTANT}._track_mcp_usage_on_create",
        f"{ASSISTANT}.category_service.validate_category_ids",
        "codemie.service.llm.model_availability_service.ModelAvailabilityService.validate_model_for_asset_creation",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target))
        yield db


def _create_assistant(client, headers, body: dict) -> str:
    response = client.post("/v1/assistants", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["assistantId"]


@dataclass
class AssistantState:
    entity: dict
    versions: list[dict]
    guardrails: list[dict]


def _assistant_state(db, assistant_id: str) -> AssistantState:
    return AssistantState(
        entity=_read(db, Assistant, assistant_id),
        versions=_read_all(db, AssistantConfiguration, assistant_id=assistant_id),
        guardrails=_read_all(db, GuardrailAssignment, entity_id=assistant_id),
    )


@pytest.fixture
def assistant_applied_twice(client, headers, assistant_env) -> tuple[AssistantState, AssistantState]:
    assistant_id = _create_assistant(client, headers, ASSISTANT_DOC)
    return _put_twice(
        client,
        headers,
        f"/v1/assistants/{assistant_id}",
        ASSISTANT_DOC,
        lambda: _assistant_state(assistant_env, assistant_id),
    )


class TestAssistantIdempotent:
    def test_reput_leaves_stored_assistant_unchanged(self, assistant_applied_twice):
        first, second = assistant_applied_twice

        assert_stored_unchanged(first.entity, second.entity)

    def test_reput_creates_no_new_version_row(self, assistant_applied_twice):
        first, second = assistant_applied_twice

        assert len(second.versions) == len(first.versions)
        assert_stored_unchanged(first.versions, second.versions)

    def test_reput_leaves_guardrail_assignment_rows_unchanged(self, assistant_applied_twice):
        first, second = assistant_applied_twice

        assert first.guardrails, "the document assigns a guardrail, so a row must exist"
        assert_stored_unchanged(first.guardrails, second.guardrails)


# One valid datasource per index type the UI can offer as assistant context (Context.index_info_type_from_index_type).
CODE_TYPES = ("code", "summary", "chunk-summary", "svn")
KB_TYPES = (
    "knowledge_base_bedrock",
    "knowledge_base_confluence",
    "knowledge_base_jira",
    "knowledge_base_xray",
    "knowledge_base_azure_devops_wiki",
    "knowledge_base_azure_devops_work_item",
    "knowledge_base_xwiki",
    "knowledge_base_sharepoint",
    "knowledge_base_file",
    "knowledge_base_git_faq",
    "llm_routing_google",
    "platform_marketplace_assistant",
)
DATASOURCE_CONTEXTS = (
    [pytest.param(t, ContextType.CODE, id=t) for t in CODE_TYPES]
    + [pytest.param(t, ContextType.KNOWLEDGE_BASE, id=t) for t in KB_TYPES]
    + [pytest.param("provider", ContextType.PROVIDER, id="provider")]
)


def _seed_datasource(db, index_type: str, name: str) -> None:
    with db.begin() as connection:
        connection.execute(
            insert(IndexInfo.__table__).values(
                id=f"idx-{name}", repo_name=name, index_type=index_type, project_name=PROJECT, full_name=f"{name}-full"
            )
        )


class TestAssistantKeepsValidContext:
    @pytest.mark.parametrize(("index_type", "context_type"), DATASOURCE_CONTEXTS)
    def test_context_entry_for_existing_datasource_survives_put(
        self, client, headers, assistant_env, index_type, context_type
    ):
        _seed_datasource(assistant_env, index_type, "ds-1")
        entry = {"context_type": context_type.value, "name": "ds-1"}
        body = {**ASSISTANT_DOC, "context": [entry]}
        assistant_id = _create_assistant(client, headers, body)

        _put(client, headers, f"/v1/assistants/{assistant_id}", body)

        assert _read(assistant_env, Assistant, assistant_id)["context"] == [entry]


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

WORKFLOW_YAML = """
assistants:
  - id: dev
    assistant_id: 196ede41-e7f0-4658-ae99-1dc0d83c8347
    model: 'gpt-4o-2024-11-20'  # hand-authored comment
states:
  - id: dev
    assistant_id: dev
    task: do the thing
    next:
      state_id: end
"""
WORKFLOW_DOC = {
    "name": "Idempotent Workflow",
    "description": "d",
    "project": PROJECT,
    "mode": "Sequential",
    "yaml_config": WORKFLOW_YAML,
}
WORKFLOW = f"{ROUTERS}.workflow"
WORKFLOW_SERVICE = "codemie.service.workflow_service.WorkflowService"


@pytest.fixture
def workflow_env(db):
    targets = (
        f"{WORKFLOW}.project_access_check",
        f"{WORKFLOW}._consumer_slot_warnings_sync",
        f"{WORKFLOW}.MCPAccessControlService.validate_on_save",
        f"{WORKFLOW_SERVICE}.validate_for_update",
        f"{WORKFLOW_SERVICE}.save_workflow_schema",
        "codemie.service.monitoring.workflow_monitoring_service.WorkflowMonitoringService.send_update_workflow_metric",
        "codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target))
        stack.enter_context(patch(f"{WORKFLOW}.collect_consumer_slot_integration_warnings", return_value=[]))
        stack.enter_context(patch(f"{WORKFLOW}._get_project_for_workflow", return_value=None))  # applications table
        stack.enter_context(patch(f"{WORKFLOW}.Ability.can", return_value=True))
        yield db


class TestWorkflowIdempotent:
    def test_reput_reaches_a_fixed_point_after_the_first_put(self, client, headers, workflow_env):
        # yaml_config is stored as sent unless a model id is normalised (EPMCDME-15759); the hand-authored
        # comment must survive both PUTs, so PUT1 == PUT2 holds trivially for the YAML and for the rest of the row.
        WorkflowConfig(
            id="wf-1", name=WORKFLOW_DOC["name"], description="d", project=PROJECT, yaml_config=WORKFLOW_YAML
        ).save()

        first, second = _put_twice(
            client, headers, "/v1/workflows/wf-1", WORKFLOW_DOC, lambda: _read(workflow_env, WorkflowConfig, "wf-1")
        )

        assert_stored_unchanged(first, second)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

SKILL_DOC = {
    "name": "idempotent-skill",
    "description": "A skill description long enough to be valid",
    "content": "Skill content. " * 20,
    "project": PROJECT,
    "visibility": "project",
    "categories": [],
}


@pytest.fixture
def skill_env(db):
    # count_assistants_using_skill only feeds the response and uses a PostgreSQL JSONB operator
    with (
        patch("codemie.service.skill_service.SkillMonitoringService.send_skill_management_metric"),
        patch("codemie.service.skill_service.SkillRepository.count_assistants_using_skill", return_value=0),
    ):
        yield db


class TestSkillIdempotent:
    def _seed(self) -> None:
        Skill(
            id="skill-1",
            name=SKILL_DOC["name"],
            description=SKILL_DOC["description"],
            content=SKILL_DOC["content"],
            project=PROJECT,
            created_by=CREATOR,
        ).save()

    def test_reput_leaves_stored_skill_unchanged(self, client, headers, skill_env):
        self._seed()

        first, second = _put_twice(
            client, headers, "/v1/skills/skill-1", SKILL_DOC, lambda: _read(skill_env, Skill, "skill-1")
        )

        assert_stored_unchanged(first, second)

    # No version assertion for skills: the Skill model has no version table or column, so there is
    # nothing to compare; test_reput_leaves_stored_skill_unchanged is the real guard.


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@pytest.fixture
def category_admin():
    from codemie.rest_api.routers import category as category_router

    app.dependency_overrides[category_router.admin_access_only] = lambda: None
    # get_category_stats only feeds the response and uses a PostgreSQL JSONB operator
    stats = {"marketplace_assistants_count": 0, "project_assistants_count": 0}
    with patch(f"{category_router.__name__}.category_service.get_category_stats", return_value=stats):
        yield


class TestCategoryIdempotent:
    def test_reput_leaves_stored_category_unchanged(self, client, headers, db, category_admin):
        Category(id="engineering", name="Engineering", description="d").save()
        body = {"name": "Engineering", "description": "d"}

        first, second = _put_twice(
            client,
            headers,
            "/v1/assistants/categories/engineering",
            body,
            lambda: _read(db, Category, "engineering"),
        )

        assert_stored_unchanged(first, second)


# ---------------------------------------------------------------------------
# Project and user settings
# ---------------------------------------------------------------------------

SETTINGS_DOCS = {
    CredentialTypes.SCHEDULER: [
        {"key": "resource_type", "value": "assistant"},
        {"key": "resource_id", "value": "assistant-1"},
        {"key": "schedule", "value": "0 9 * * *"},
        {"key": "timezone", "value": "UTC"},
        {"key": "is_enabled", "value": True},
    ],
    CredentialTypes.LITE_LLM: [{"key": "api_key", "value": "sk-plain-secret"}],
    CredentialTypes.GIT: [
        {"key": "url", "value": "https://github.com/org/repo"},
        {"key": "auth_type", "value": "pat"},
        {"key": "token", "value": "ghp-plain-secret"},
    ],
    CredentialTypes.WEBHOOK: [
        {"key": "resource_type", "value": "assistant"},
        {"key": "resource_id", "value": "assistant-1"},
        {"key": "webhook_id", "value": "webhook-1"},
    ],
}
SETTING_TYPES = [
    pytest.param(SettingType.PROJECT, "project", id="project"),
    pytest.param(SettingType.USER, "user", id="user"),
]


@pytest.fixture
def settings_env(db, encryption):
    targets = (
        "codemie.service.assistant.assistant_service.AssistantService.belongs_to_project",
        "codemie.enterprise.litellm.require_litellm_enabled",
        "codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache",
        "codemie.rest_api.models.settings.Settings.check_webhook_id_unique",
        f"{ROUTERS}.user_settings.project_access_check",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target))
        yield db


@pytest.mark.parametrize(("setting_type", "scope"), SETTING_TYPES)
@pytest.mark.parametrize("credential_type", list(SETTINGS_DOCS), ids=lambda t: t.value)
class TestSettingsIdempotent:
    def test_reput_leaves_stored_setting_unchanged(
        self, client, headers, settings_env, setting_type, scope, credential_type
    ):
        Settings(
            id="setting-1",
            project_name=PROJECT,
            alias="the-alias",
            credential_type=credential_type,
            credential_values=[],
            setting_type=setting_type,
            user_id=USER.id,
        ).save()
        body = {
            "project_name": PROJECT,
            "alias": "the-alias",
            "credential_type": credential_type.value,
            "credential_values": SETTINGS_DOCS[credential_type],
        }

        first, second = _put_twice(
            client, headers, f"/v1/settings/{scope}/setting-1", body, lambda: _read(settings_env, Settings, "setting-1")
        )

        assert_stored_unchanged(first, second)


# ---------------------------------------------------------------------------
# AC2: sensitive values sent back as the mask never overwrite the stored value
# ---------------------------------------------------------------------------


def _stored_prompt_variable(state: AssistantState, key: str) -> dict:
    return next(v for v in state.entity["prompt_variables"] if v["key"] == key)


class TestAssistantMaskedSensitiveValue:
    def test_masked_prompt_variable_does_not_overwrite_stored_value(self, client, headers, assistant_env):
        assistant_id = _create_assistant(client, headers, ASSISTANT_DOC)
        created = _assistant_state(assistant_env, assistant_id)
        stored_token = _stored_prompt_variable(created, "token")["default_value"]
        assert stored_token != SENSITIVE_VALUE_MASK
        get_shaped = {
            **ASSISTANT_DOC,
            "prompt_variables": [
                {"key": "region", "default_value": "eu"},
                {"key": "token", "default_value": SENSITIVE_VALUE_MASK, "is_sensitive": True},
            ],
        }

        _put(client, headers, f"/v1/assistants/{assistant_id}", get_shaped)

        after = _assistant_state(assistant_env, assistant_id)
        assert _stored_prompt_variable(after, "token")["default_value"] == stored_token

    def test_double_put_of_masked_document_leaves_stored_assistant_unchanged(self, client, headers, assistant_env):
        assistant_id = _create_assistant(client, headers, ASSISTANT_DOC)
        get_shaped = {
            **ASSISTANT_DOC,
            "prompt_variables": [
                {"key": "region", "default_value": "eu"},
                {"key": "token", "default_value": SENSITIVE_VALUE_MASK, "is_sensitive": True},
            ],
        }

        first, second = _put_twice(
            client,
            headers,
            f"/v1/assistants/{assistant_id}",
            get_shaped,
            lambda: _assistant_state(assistant_env, assistant_id),
        )

        assert_stored_unchanged(first.entity, second.entity)
        assert_stored_unchanged(first.versions, second.versions)


MASKED = BaseSettingsService.MASKED_VALUE
SENSITIVE_SETTINGS = {
    CredentialTypes.LITE_LLM: ("api_key", SETTINGS_DOCS[CredentialTypes.LITE_LLM]),
    CredentialTypes.GIT: ("token", SETTINGS_DOCS[CredentialTypes.GIT]),
}


@pytest.mark.parametrize(("setting_type", "scope"), SETTING_TYPES)
@pytest.mark.parametrize("credential_type", list(SENSITIVE_SETTINGS), ids=lambda t: t.value)
class TestSettingsMaskedSensitiveValue:
    def test_masked_credential_does_not_overwrite_stored_value(
        self, client, headers, settings_env, setting_type, scope, credential_type
    ):
        sensitive_key, real_values = SENSITIVE_SETTINGS[credential_type]
        Settings(
            id="setting-1",
            project_name=PROJECT,
            alias="the-alias",
            credential_type=credential_type,
            credential_values=[],
            setting_type=setting_type,
            user_id=USER.id,
        ).save()
        path = f"/v1/settings/{scope}/setting-1"

        def body(values):
            return {
                "project_name": PROJECT,
                "alias": "the-alias",
                "credential_type": credential_type.value,
                "credential_values": values,
            }

        _put(client, headers, path, body(real_values))
        stored = _read(settings_env, Settings, "setting-1")
        stored_sensitive = next(c for c in stored["credential_values"] if c["key"] == sensitive_key)["value"]
        assert stored_sensitive and stored_sensitive != MASKED
        get_shaped = [{**c, "value": MASKED} if c["key"] == sensitive_key else c for c in real_values]

        _put(client, headers, path, body(get_shaped))
        first = _read(settings_env, Settings, "setting-1")
        _put(client, headers, path, body(get_shaped))
        second = _read(settings_env, Settings, "setting-1")

        assert_stored_unchanged(stored, first)
        assert_stored_unchanged(first, second)
