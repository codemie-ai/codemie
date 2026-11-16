# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from datetime import datetime, timezone

from codemie.configs.llm_config import (
    CostConfig,
    LiteLLMRouterConfig,
    LLMFeatures,
    LLMModel,
    LLMProvider,
    LlmRouterOption,
    ModelConfigurationSection,
    ModelStatus,
    ModelType,
)
from codemie.core.models import LLMModelStatusBulkUpdateRequest, LLMModelStatusUpdateRequest
from codemie.rest_api.routers.admin import (
    add_application,
    bulk_update_llm_model_status,
    get_applications,
    get_llm_model_status,
    get_speech_token,
    reload_llm_models,
    router,
    update_llm_model_status,
    get_admin_llm_default,
    set_admin_llm_default,
    clear_admin_llm_default,
)
from codemie.rest_api.models.admin_global_default_api import AdminGlobalDefaultRequest
from codemie.rest_api.security.authentication import (
    authenticate,
    admin_access_only,
    admin_or_maintainer_or_auditor_access,
)
from codemie.rest_api.security.user import User
import pydantic

ADMIN_MODULE = "codemie.rest_api.routers.admin"


def test_get_applications_returns_application_names():
    applications = [
        SimpleNamespace(name="proj-a", display_name=None),
        SimpleNamespace(name="proj-b", display_name=None),
    ]

    with patch("codemie.rest_api.routers.admin.Application.search_by_name", return_value=applications):
        response = get_applications(search="proj", limit=2)

    assert [app.name for app in response.applications] == ["proj-a", "proj-b"]


def test_add_application_creates_application_and_emits_metric():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = SimpleNamespace(name="proj-a")

    with (
        patch("codemie.service.user.application_service.application_service.create_application") as mock_create,
        patch("codemie.rest_api.routers.admin.ProjectMonitoringService.send_project_creation_metric") as mock_metric,
    ):
        mock_create.return_value = SimpleNamespace(name="proj-a")
        response = add_application(request, admin=admin)

    assert response.message == f"Application {request} has been created"
    mock_create.assert_called_once_with("proj-a")
    mock_metric.assert_called_once_with(user=admin, project_name="proj-a")


def test_get_speech_token_uses_config_values():
    with patch("codemie.rest_api.routers.admin.config") as mock_config:
        mock_config.AZURE_SPEECH_SERVICE_KEY = "speech-token"
        mock_config.AZURE_SPEECH_REGION = "westus"

        response = get_speech_token()

    assert response == {
        "token": "speech-token",
        "region": "westus",
        "stt_wss_url": "wss://westus.stt.speech.microsoft.com/speech/universal/v2",
        "tts_url": "https://westus.tts.speech.microsoft.com/cognitiveservices/avatar/relay/token/v1",
    }


@pytest.mark.asyncio
async def test_reload_llm_models_refreshes_proxy_models():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    models = SimpleNamespace(chat_models=["chat-1", "chat-2"], embedding_models=["embed-1"])
    proxy_provider = SimpleNamespace(reload_models_cache=MagicMock())
    llm_service = SimpleNamespace(initialize_default_litellm_models=MagicMock())

    with (
        patch("codemie.enterprise.litellm.require_litellm_enabled") as mock_require,
        patch("codemie.enterprise.litellm.get_available_models", return_value=models),
        patch(
            "codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider",
            return_value=proxy_provider,
        ),
        patch("codemie.service.llm_service.llm_service.llm_service", llm_service),
    ):
        response = await reload_llm_models(admin=admin)

    assert response.message == "Successfully reloaded LLM models: 2 chat models, 1 embedding models"
    mock_require.assert_called_once_with()
    proxy_provider.reload_models_cache.assert_called_once_with()
    llm_service.initialize_default_litellm_models.assert_called_once_with(models)


class TestApplicationsSearchOpenToAuditor:
    """EPMCDME-10930: GET /v1/admin/applications must accept admin, maintainer, and auditor.

    The router previously gated every route in this file behind a single shared
    admin_access_only dependency, which blocked auditors from the project search
    used by the frontend's ProjectSelector/Analytics "Project" filter even though
    auditors should have read-only visibility into all platform projects. Fixed by
    moving auth to per-route dependencies so only this one GET route was loosened.
    """

    def _find_route(self, path: str, method: str):
        for route in router.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        raise AssertionError(f"{method} {path} route not found")

    def test_applications_route_uses_auditor_inclusive_dependency(self):
        route = self._find_route("/v1/admin/applications", "GET")
        dependency_functions = [dep.call for dep in route.dependant.dependencies]

        assert admin_or_maintainer_or_auditor_access in dependency_functions
        assert admin_access_only not in dependency_functions

    def test_other_admin_endpoints_still_use_admin_access_only(self):
        still_admin_only = {
            ("/v1/admin/application", "POST"),
            ("/v1/admin/users/{user_id}/conversations/{conversation_id}", "GET"),
            ("/v1/admin/users/{user_id}/conversations/{conversation_id}/feedback", "PUT"),
            ("/v1/speech/config", "GET"),
            ("/v1/admin/marketplace/reindex", "POST"),
            ("/v1/admin/llm/reload", "GET"),
            ("/v1/admin/llm/retire", "POST"),
            ("/v1/admin/llm/retire/bulk", "POST"),
        }

        for path, method in still_admin_only:
            route = self._find_route(path, method)
            dependency_functions = [dep.call for dep in route.dependant.dependencies]
            assert admin_access_only in dependency_functions, f"{method} {path} should still use admin_access_only"


# ── LLM model status admin endpoints (EPMCDME-15080) ────────────────────


class TestLLMModelStatusRoutesAreAdminOnly:
    """New llm/status routes must be gated by admin_access_only, mirroring retire_llm_model."""

    def _find_route(self, path: str, method: str):
        for route in router.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        raise AssertionError(f"{method} {path} route not found")

    def test_llm_status_routes_use_admin_access_only(self):
        admin_only_routes = {
            ("/v1/admin/llm/status", "GET"),
            ("/v1/admin/llm/{base_name}/status", "PUT"),
            ("/v1/admin/llm/status/bulk", "POST"),
        }

        for path, method in admin_only_routes:
            route = self._find_route(path, method)
            dependency_functions = [dep.call for dep in route.dependant.dependencies]
            assert admin_access_only in dependency_functions, f"{method} {path} should use admin_access_only"


def test_get_llm_model_status_defaults_to_enabled_when_no_persisted_row():
    models = [
        LLMModel(
            base_name='m1',
            deployment_name='dep-m1',
            enabled=True,
            label='Model One',
            provider=LLMProvider.AZURE_OPENAI,
            multimodal=True,
            features=LLMFeatures(tools=True),
            cost=CostConfig(input=1.5, output=3),
            is_premium=True,
            configuration=ModelConfigurationSection(client_headers={'private': 'not-for-response'}),
        ),
        LLMModel(
            base_name='m2',
            deployment_name='dep-m2',
            enabled=True,
            litellm_router=LiteLLMRouterConfig(),
            status=ModelStatus.DISABLED,
        ),
    ]
    embedding_models = [LLMModel(base_name='e1', deployment_name='dep-e1', enabled=True)]
    router = LlmRouterOption(base_name='sy-router', router_type='switchyard', status=ModelStatus.DEPRECATED)
    persisted_row = SimpleNamespace(
        base_name="m1",
        status="deprecated",
        updated_by_name="alice",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    llm_service_mock = SimpleNamespace(
        get_admin_model_catalog=MagicMock(
            return_value=[
                *((ModelType.CHAT, model) for model in models),
                *((ModelType.EMBEDDING, model) for model in embedding_models),
                (ModelType.CHAT, router),
            ]
        ),
    )

    with (
        patch(f"{ADMIN_MODULE}.llm_service", llm_service_mock),
        patch(
            f"{ADMIN_MODULE}.get_session",
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False)),
        ),
        patch(
            f"{ADMIN_MODULE}.LLMModelStatusRepository.get_all",
            return_value=[persisted_row, SimpleNamespace(base_name='sy-router', status='disabled')],
        ),
    ):
        response = get_llm_model_status()

    by_base_name = {item["base_name"]: item for item in response.data}
    assert by_base_name["m1"]["status"] == "deprecated"
    assert by_base_name["m1"]["updated_by_name"] == "alice"
    assert by_base_name["m2"]["status"] == "enabled"
    assert by_base_name["m2"]["updated_by_name"] is None
    # CR-005: embedding models must be listed alongside chat models.
    assert by_base_name["e1"]["status"] == "enabled"
    assert by_base_name['e1']['model_type'] == 'embedding'
    assert by_base_name['e1']['supports_tools'] is None
    assert by_base_name['m1']['model_type'] == 'chat'
    assert by_base_name['m1']['label'] == 'Model One'
    assert by_base_name['m1']['provider'] == 'azure_openai'
    assert by_base_name['m1']['multimodal'] is True
    assert by_base_name['m1']['supports_tools'] is True
    assert by_base_name['m1']['is_premium'] is True
    assert by_base_name['m1']['cost'] == {'input': 1.5, 'output': 3.0}
    assert by_base_name['m2']['label'] == 'm2'
    assert by_base_name['m2']['router_type'] == 'litellm_auto'
    assert by_base_name['sy-router']['router_type'] == 'switchyard'
    assert by_base_name['sy-router']['status'] == 'deprecated'
    assert 'configuration' not in by_base_name['m1']
    assert 'client_headers' not in str(response.data)
    assert 'deployment_name' not in by_base_name['m1']


def test_update_llm_model_status_persists_via_repository_upsert():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = LLMModelStatusUpdateRequest(status=ModelStatus.DEPRECATED)

    llm_service_mock = SimpleNamespace(
        get_all_llm_model_info=MagicMock(return_value=[SimpleNamespace(base_name="m1")]),
        get_all_embedding_model_info=MagicMock(return_value=[]),
        get_all_audio_transcription_model_info=MagicMock(return_value=[]),
        get_model_status_overrides=MagicMock(),
    )

    with (
        patch(f"{ADMIN_MODULE}.llm_service", llm_service_mock),
        patch(
            f"{ADMIN_MODULE}.get_session",
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False)),
        ),
        patch(f"{ADMIN_MODULE}.LLMModelStatusRepository.upsert") as mock_upsert,
    ):
        response = update_llm_model_status(base_name="m1", request=request, admin=admin)

    assert response.data == {"base_name": "m1", "status": "deprecated"}
    mock_upsert.assert_called_once()
    args = mock_upsert.call_args.args
    assert args[1:] == ("m1", "deprecated", admin.id, admin.name)
    llm_service_mock.get_model_status_overrides.assert_called_once_with(force_refresh=True)


def test_update_llm_model_status_rejects_unknown_base_name():
    """CR-006: PUT must reject a base_name that does not exist in the live catalog."""
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = LLMModelStatusUpdateRequest(status=ModelStatus.DEPRECATED)

    llm_service_mock = SimpleNamespace(
        get_all_llm_model_info=MagicMock(return_value=[SimpleNamespace(base_name="m1")]),
        get_all_embedding_model_info=MagicMock(return_value=[]),
        get_all_audio_transcription_model_info=MagicMock(return_value=[]),
    )

    with (
        patch(f"{ADMIN_MODULE}.llm_service", llm_service_mock),
        patch(f"{ADMIN_MODULE}.LLMModelStatusRepository.upsert") as mock_upsert,
    ):
        with pytest.raises(HTTPException) as exc_info:
            update_llm_model_status(base_name="does-not-exist", request=request, admin=admin)

    assert exc_info.value.status_code == 404
    mock_upsert.assert_not_called()


def test_llm_model_status_update_request_rejects_invalid_status():
    with pytest.raises(pydantic.ValidationError):
        LLMModelStatusUpdateRequest(status="not-a-real-status")


def test_llm_model_status_bulk_request_rejects_duplicate_base_names():
    """CR-001: a bulk request with two items for the same base_name must be rejected."""
    with pytest.raises(pydantic.ValidationError):
        LLMModelStatusBulkUpdateRequest(
            updates=[
                {"base_name": "m1", "status": "deprecated"},
                {"base_name": "m1", "status": "enabled"},
            ]
        )


def test_llm_model_status_bulk_request_allows_unique_base_names():
    request = LLMModelStatusBulkUpdateRequest(
        updates=[
            {"base_name": "m1", "status": "deprecated"},
            {"base_name": "m2", "status": "enabled"},
        ]
    )
    assert len(request.updates) == 2


def test_bulk_update_llm_model_status_reports_partial_success():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = LLMModelStatusBulkUpdateRequest(
        updates=[
            {"base_name": "m1", "status": "deprecated"},
            {"base_name": "m2", "status": "enabled"},
        ]
    )

    def upsert_side_effect(session, base_name, status, admin_id, admin_name):
        if base_name == "m2":
            raise ValueError("boom")
        return SimpleNamespace(base_name=base_name, status=status)

    with (
        patch(
            f"{ADMIN_MODULE}.get_session",
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False)),
        ),
        patch(f"{ADMIN_MODULE}.LLMModelStatusRepository.upsert", side_effect=upsert_side_effect),
        patch(
            f"{ADMIN_MODULE}.llm_service.get_all_llm_model_info",
            return_value=[SimpleNamespace(base_name="m1"), SimpleNamespace(base_name="m2")],
        ),
        patch(f"{ADMIN_MODULE}.llm_service.get_all_embedding_model_info", return_value=[]),
        patch(f"{ADMIN_MODULE}.llm_service.get_model_status_overrides") as refresh,
    ):
        response = bulk_update_llm_model_status(request=request, admin=admin)

    results = response.data["results"]
    assert response.data["total"] == 2
    assert response.data["succeeded"] == 1
    assert response.data["failed"] == 1
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]
    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0]["base_name"] == "m1"
    assert failures[0]["base_name"] == "m2"
    # CR-007: raw exception text ("boom") must never reach the client.
    assert "boom" not in failures[0]["error"]
    assert failures[0]["error"]
    refresh.assert_called_once_with(force_refresh=True)


@pytest.mark.parametrize("model_status", list(ModelStatus))
def test_bulk_model_status_rejects_router_and_unknown_names_without_writes(model_status):
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = LLMModelStatusBulkUpdateRequest(
        updates=[
            {"base_name": "sy-router", "status": model_status},
            {"base_name": "unknown", "status": model_status},
        ]
    )
    with (
        patch(f"{ADMIN_MODULE}.llm_service.get_all_llm_model_info", return_value=[SimpleNamespace(base_name="chat")]),
        patch(f"{ADMIN_MODULE}.llm_service.get_all_embedding_model_info", return_value=[]),
        patch(f"{ADMIN_MODULE}.get_session") as session,
        patch(f"{ADMIN_MODULE}.LLMModelStatusRepository.upsert") as upsert,
        patch(f"{ADMIN_MODULE}.llm_service.get_model_status_overrides") as refresh,
    ):
        response = bulk_update_llm_model_status(request=request, admin=admin)

    assert response.data["succeeded"] == 0
    assert response.data["failed"] == 2
    for result in response.data["results"]:
        assert result["success"] is False
        assert result["status"] == model_status.value
        assert "was not found in the current catalog" in result["error"]
    session.assert_not_called()
    upsert.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.parametrize("model_status", list(ModelStatus))
def test_bulk_model_status_accepts_chat_and_embedding_models_in_mixed_request(model_status):
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = LLMModelStatusBulkUpdateRequest(
        updates=[{"base_name": name, "status": model_status} for name in ("sy-router", "chat", "unknown", "embed")]
    )
    with (
        patch(f"{ADMIN_MODULE}.llm_service.get_all_llm_model_info", return_value=[SimpleNamespace(base_name="chat")]),
        patch(
            f"{ADMIN_MODULE}.llm_service.get_all_embedding_model_info",
            return_value=[SimpleNamespace(base_name="embed")],
        ),
        patch(f"{ADMIN_MODULE}.get_session", return_value=MagicMock()),
        patch(f"{ADMIN_MODULE}.LLMModelStatusRepository.upsert") as upsert,
        patch(f"{ADMIN_MODULE}.llm_service.get_model_status_overrides") as refresh,
    ):
        response = bulk_update_llm_model_status(request=request, admin=admin)

    assert response.data["total"] == 4
    assert response.data["succeeded"] == 2
    assert response.data["failed"] == 2
    assert [result["base_name"] for result in response.data["results"] if result["success"]] == ["chat", "embed"]
    assert [(call.args[1], call.args[2]) for call in upsert.call_args_list] == [
        ("chat", model_status.value),
        ("embed", model_status.value),
    ]
    refresh.assert_called_once_with(force_refresh=True)


def _admin_default_row(base_name="model-a"):
    return SimpleNamespace(
        base_name=base_name,
        updated_by_id="admin-1",
        updated_by_name="Admin",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _admin_default_service(saved=None, effective="model-a", source="admin"):
    service = MagicMock()
    service.get_admin_global_default_choice.return_value = saved
    service.get_all_llm_model_info.return_value = [SimpleNamespace(base_name=effective)]
    service.resolve_global_default.return_value = SimpleNamespace(
        model=SimpleNamespace(base_name=effective) if effective else None,
        source=source,
        saved_base_name=saved.base_name if saved else None,
    )
    service.set_admin_global_default.return_value = _admin_default_row()
    return service


def test_admin_default_read_distinguishes_saved_and_effective_fallback():
    service = _admin_default_service(_admin_default_row("old-model"), "model-b", "configured")
    with patch(f"{ADMIN_MODULE}.llm_service", service):
        response = get_admin_llm_default()
    assert response.saved_base_name == "old-model"
    assert response.updated_by_name == "Admin"
    assert response.updated_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert response.effective_base_name == "model-b"
    assert response.source == "configured"
    assert response.temporary_fallback is True


def test_admin_default_set_and_replace_delegate_to_service():
    admin = User(id="admin-1", username="admin", name="Admin", is_admin=True)
    service = _admin_default_service(_admin_default_row())
    with patch(f"{ADMIN_MODULE}.llm_service", service):
        for base_name in ("model-a", "model-b"):
            set_admin_llm_default(AdminGlobalDefaultRequest(base_name=base_name), admin=admin)
    assert service.set_admin_global_default.call_count == 2
    assert service.set_admin_global_default.call_args_list[0].args == ("model-a", "admin-1", "Admin")
    assert service.set_admin_global_default.call_args_list[1].args == ("model-b", "admin-1", "Admin")


def test_admin_default_clear_delegates_to_service_and_returns_configured_choice():
    service = _admin_default_service(None, "model-b", "configured")
    with patch(f"{ADMIN_MODULE}.llm_service", service):
        response = clear_admin_llm_default()
    service.clear_admin_global_default.assert_called_once_with()
    assert response.saved_base_name is None
    assert response.effective_base_name == "model-b"
    assert response.temporary_fallback is False


def test_admin_default_invalid_selection_does_not_clear_saved_choice():
    from codemie.core.exceptions import ModelNotSelectableError

    admin = User(id="admin-1", username="admin", name="Admin", is_admin=True)
    service = _admin_default_service(_admin_default_row("model-a"))
    service.set_admin_global_default.side_effect = ModelNotSelectableError("retired", "model is deprecated")
    with patch(f"{ADMIN_MODULE}.llm_service", service):
        with pytest.raises(HTTPException) as exc_info:
            set_admin_llm_default(AdminGlobalDefaultRequest(base_name="retired"), admin=admin)
    assert exc_info.value.status_code == 400
    assert "deprecated" in exc_info.value.detail
    service.clear_admin_global_default.assert_not_called()


def test_admin_default_routes_reject_non_admin_writes():
    from codemie.core.exceptions import ExtendedHTTPException

    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ExtendedHTTPException)
    async def handle_extended_http_exception(request: Request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message}})

    ordinary_user = User(id="user-1", username="user", is_admin=False)
    ordinary_user.is_admin = False

    async def authenticate_user(request: Request):
        request.state.user = ordinary_user
        return ordinary_user

    app.dependency_overrides[authenticate] = authenticate_user
    client = TestClient(app)
    with patch(f"{ADMIN_MODULE}.llm_service") as service:
        for method in ("put", "delete"):
            response = getattr(client, method)(
                "/v1/admin/llm/default", **({"json": {"base_name": "model-a"}} if method == "put" else {})
            )
            assert response.status_code == 403
        service.set_admin_global_default.assert_not_called()
        service.clear_admin_global_default.assert_not_called()


def test_admin_default_routes_use_admin_access_only():
    for method in ("GET", "PUT", "DELETE"):
        route = next(
            route for route in router.routes if route.path == "/v1/admin/llm/default" and method in route.methods
        )
        assert admin_access_only in [dependency.call for dependency in route.dependant.dependencies]
