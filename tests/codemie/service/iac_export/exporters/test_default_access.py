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

"""The production path: each exporter builds its own `NameResolver(access=reference_access(user))`.

No resolver is injected here, so removing `access=` from an exporter makes these tests fail.
"""

from unittest.mock import patch

import pytest

from codemie.rest_api.security.user import User
from codemie.service.iac_export import references
from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.exporters.assistant import AssistantExporter
from codemie.service.iac_export.exporters.datasource import DatasourceDispatchExporter
from codemie.service.iac_export.exporters.workflow import WorkflowExporter
from tests.codemie.service.iac_export.exporters import test_assistant, test_workflow
from tests.codemie.service.iac_export.exporters.datasource import test_datasource

MEMBER = User(id="member-1", username="member-1")  # not an admin
REFERENCED_NAMES = ("Jira", "Docs Env", "Docs Token", "Sub Helper", "Helper", "My Integration")


@pytest.fixture(autouse=True)
def _nothing_is_readable():
    def deny(kind: str, id_: str):
        raise ExportError(f"Referenced {kind} '{id_}' is not accessible to the current user", 403)

    # Categories are unchecked in production; denying them here makes the first reference fail before any lookup.
    with patch.dict(references._READ_CHECKS, {"assistant": deny, "integration": deny, "category": deny}):
        yield


def _assert_forbidden_without_names(error: ExportError):
    assert error.status_code == 403
    assert not any(name in error.message for name in REFERENCED_NAMES)


def test_assistant_export_rejects_an_unreadable_reference():
    with (
        patch(f"{test_assistant.MODULE}.get_assistant_by_id_or_raise", return_value=test_assistant._assistant()),
        patch(f"{test_assistant.MODULE}.check_user_can_access_assistant"),
        pytest.raises(ExportError) as exc,
    ):
        AssistantExporter().export("assistant-uuid-1", user=MEMBER)
    _assert_forbidden_without_names(exc.value)


def test_workflow_export_rejects_an_unreadable_reference():
    with (
        patch(f"{test_workflow.MODULE}.workflow_service.get_workflow", return_value=test_workflow._workflow()),
        patch(f"{test_workflow.MODULE}.Ability") as ability,
        pytest.raises(ExportError) as exc,
    ):
        ability.return_value.can.return_value = True
        WorkflowExporter().export("wf-uuid-1", user=MEMBER)
    _assert_forbidden_without_names(exc.value)


def test_datasource_export_rejects_an_unreadable_integration(monkeypatch):
    monkeypatch.setattr(
        f"{test_datasource.BASE}.base.SchedulerSettingsService.get_scheduler_settings_for_datasources",
        lambda user_id, ids: {},
    )
    index = test_datasource._code("git")
    with (
        patch(f"{test_datasource.BASE}.IndexInfo.get_by_id", return_value=index),
        pytest.raises(ExportError) as exc,
    ):
        DatasourceDispatchExporter().export(test_datasource.INDEX_ID, MEMBER)
    _assert_forbidden_without_names(exc.value)
