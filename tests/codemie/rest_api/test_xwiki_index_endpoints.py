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

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.index import (
    IndexKnowledgeBaseXWikiRequest,
    UpdateKnowledgeBaseXWikiRequest,
)
from codemie.service.settings.settings_request_validator import (
    UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES,
    UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES,
)


def test_create_request_defaults_the_wiki():
    request = IndexKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1", description="d", space="KB")
    assert request.wiki == "xwiki"


def test_create_request_strips_and_rejects_a_blank_space():
    with pytest.raises(ValidationError):
        IndexKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1", description="d", space="   ")


def test_create_request_accepts_a_nested_space():
    request = IndexKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1", description="d", space="KB.Onboarding")
    assert request.space == "KB.Onboarding"


def test_update_request_accepts_a_new_space():
    request = UpdateKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1", space="KB.Onboarding")
    assert request.space == "KB.Onboarding"


def test_update_request_allows_omitting_the_space():
    request = UpdateKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1")
    assert request.space is None


def test_xwiki_supports_webhook():
    assert "knowledge_base_xwiki" not in UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES


def test_xwiki_does_support_the_scheduler():
    assert "knowledge_base_xwiki" not in UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES


def test_endpoints_are_registered():
    from codemie.rest_api.routers.index import router

    xwiki_path = "/v1/index/knowledge_base/xwiki"
    paths = {route.path for route in router.routes}
    assert xwiki_path in paths
    methods = {m for route in router.routes if route.path == xwiki_path for m in route.methods}
    assert {"POST", "PUT"} <= methods


def test_create_route_forwards_setting_id_to_credential_resolution():
    """The user picks an integration in the form; without setting_id the create route resolves
    whichever integration the project defaults to, so it can index a different wiki than the one
    the health check just validated."""
    import inspect

    from codemie.rest_api.routers.index import index_knowledge_base_xwiki

    source = inspect.getsource(index_knowledge_base_xwiki)
    creds_call = source.split("get_xwiki_creds", 1)[1].split(")", 1)[0]
    assert "setting_id=request.setting_id" in creds_call


def test_update_route_rejects_a_datasource_of_another_type():
    """The PUT looks the index up by project+name only, so it can match a non-xWiki row; without
    a guard the reindex path dereferences kb_index.xwiki and 500s."""
    import inspect

    from codemie.rest_api.routers.index import update_knowledge_base_xwiki

    source = inspect.getsource(update_knowledge_base_xwiki)
    assert "if not kb_index.xwiki:" in source
    assert source.index("if not kb_index.xwiki:") < source.index("kb_index.xwiki.space")


def test_update_route_guards_against_missing_xwiki_creds_on_full_reindex():
    """SettingsService.get_xwiki_creds returning None must raise HTTP 400 immediately;
    without the guard it reaches XWikiDatasourceProcessor and causes an unhandled
    AttributeError in the background task after the endpoint has answered 200."""
    from unittest.mock import MagicMock, patch

    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.routers.index import (
        INCORRECT_DATASOURCE_SETUP_MESSAGE,
        update_knowledge_base_xwiki,
    )

    _router = "codemie.rest_api.routers.index"
    kb_index = MagicMock()
    kb_index.xwiki = MagicMock()  # truthy — passes the type guard at the top of the handler

    with (
        patch(f"{_router}.KnowledgeBaseIndexInfo.filter_by_project_and_repo", return_value=[kb_index]),
        patch(f"{_router}._validate_project_change"),
        patch(f"{_router}.SettingsService.get_xwiki_creds", return_value=None),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_knowledge_base_xwiki(
                request=UpdateKnowledgeBaseXWikiRequest(name="kb-xwiki", project_name="p1"),
                raw_request=MagicMock(),
                background_tasks=MagicMock(),
                full_reindex=True,
                user=MagicMock(),
            )

    assert exc_info.value.code == 400
    assert exc_info.value.message == INCORRECT_DATASOURCE_SETUP_MESSAGE


def test_update_route_reindexes_under_the_new_project_name():
    """_index_name is derived from project_name; using the old value after update_index has moved
    the row rebuilds the previous Elasticsearch index."""
    import inspect

    from codemie.rest_api.routers.index import update_knowledge_base_xwiki

    source = inspect.getsource(update_knowledge_base_xwiki)
    assert "project_name=request.new_project_name or request.project_name" in source
