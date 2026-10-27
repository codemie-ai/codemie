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

"""Re-review triggers for marketplace-reviewed, non-versioned assistant fields (EPMCDME-14234)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantRequest,
    PromptVariable,
    SubAssistantPublishSettings,
    ToolKitDetails,
)
from codemie.rest_api.routers.assistant import _sub_assistants as subs
from codemie.rest_api.routers.assistant import management as router
from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService

COMPARE = "codemie.service.assistant.assistant_version_compare_service.AssistantVersionCompareService"


def _assistant(**overrides) -> Assistant:
    fields = {
        "id": "a-1",
        "name": "Helper",
        "description": "d",
        "system_prompt": "p",
        "llm_model_type": "gpt-4",
        "categories": ["cat-1"],
        "skill_ids": ["skill-1"],
        "icon_url": "http://icon/1",
        "plan_prompt": "plan",
        "prompt_variables": [PromptVariable(key="k", description="d", default_value="v")],
        "marketplace_status": "APPROVED",
        "marketplace_version": 3,
    }
    fields.update(overrides)
    return Assistant(**fields)


def _request(**overrides) -> AssistantRequest:
    fields = {
        "name": "Helper",
        "system_prompt": "p",
        "llm_model_type": "gpt-4",
        "categories": ["cat-1"],
        "skill_ids": ["skill-1"],
    }
    fields.update(overrides)
    return AssistantRequest(**fields)


class TestHasMarketplaceFieldChanges:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"name": "Renamed"},
            {"categories": ["cat-2"]},
            {"skill_ids": ["skill-1", "skill-2"]},
            {"icon_url": "http://icon/2"},
            {"plan_prompt": "other plan"},
            {"prompt_variables": [PromptVariable(key="k", description="d", default_value="changed")]},
        ],
    )
    def test_changed_reviewed_field_detected(self, overrides):
        request = _request(**overrides)
        assert AssistantVersionCompareService.has_marketplace_field_changes(_assistant(), request) is True

    def test_identical_fields_not_a_change(self):
        request = _request()
        assert AssistantVersionCompareService.has_marketplace_field_changes(_assistant(), request) is False

    def test_omitted_fields_do_not_count(self):
        # Only name is set explicitly; defaulted categories/skill_ids must not read as a wipe.
        request = AssistantRequest.model_validate({"name": "Helper", "system_prompt": "p", "llm_model_type": "gpt-4"})
        assert AssistantVersionCompareService.has_marketplace_field_changes(_assistant(), request) is False

    def test_explicit_clear_counts(self):
        request = AssistantRequest.model_validate(
            {"name": "Helper", "system_prompt": "p", "llm_model_type": "gpt-4", "skill_ids": []}
        )
        assert AssistantVersionCompareService.has_marketplace_field_changes(_assistant(), request) is True


class TestRequiresReReview:
    @pytest.mark.parametrize(
        ("config_changed", "field_changed", "expected"),
        [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
    )
    def test_either_signal_triggers(self, config_changed, field_changed, expected):
        with (
            patch.object(AssistantVersionCompareService, "has_configuration_changes", return_value=config_changed),
            patch.object(AssistantVersionCompareService, "has_marketplace_field_changes", return_value=field_changed),
        ):
            assert AssistantVersionCompareService.requires_re_review(_assistant(), _request()) is expected


class TestModelNoLongerBouncesPendingReview:
    """The model's generic merge must not touch the review state of a pending submission.

    Invalidating a review is a queue-service transition (RE_SUBMIT) run by the caller; a
    silent status write here left the submission out of the queue with no record.
    """

    def _run_update(self, assistant, config_changed):
        with (
            patch(f"{COMPARE}.has_configuration_changes", return_value=config_changed),
            patch.object(Assistant, "_map_assistant_request"),
            patch.object(Assistant, "refresh_marketplace_columns_from_db"),
            patch.object(Assistant, "update"),
            patch("codemie.service.assistant.assistant_version_service.AssistantVersionService") as version_service,
        ):
            assistant.update_assistant(_request(), MagicMock())
            return version_service

    @pytest.mark.parametrize("config_changed", [True, False])
    def test_pending_review_status_and_version_are_left_alone(self, config_changed):
        assistant = _assistant(marketplace_status="PENDING_REVIEW")
        self._run_update(assistant, config_changed)

        assert assistant.marketplace_status == "PENDING_REVIEW"
        assert assistant.marketplace_version == 3

    def test_versions_only_when_config_changed(self):
        changed = self._run_update(_assistant(marketplace_status="PENDING_REVIEW"), config_changed=True)
        unchanged = self._run_update(_assistant(marketplace_status="PENDING_REVIEW"), config_changed=False)

        changed.create_new_version.assert_called_once()
        unchanged.create_new_version.assert_not_called()


class TestUpdateRouterReSubmit:
    """Seam: the router must reach _trigger_re_review for a reviewed-field-only edit."""

    def _call(self, gate_enabled, config_changed, field_changed):
        assistant = _assistant()
        user = MagicMock()
        with (
            patch.object(router, "project_access_check"),
            patch.object(router, "get_assistant_by_id_or_raise", return_value=assistant),
            patch.object(router, "check_user_can_access_assistant"),
            patch.object(router, "_validate_remote_entities_and_raise"),
            patch.object(router, "_update_validation_response", return_value=None),
            patch.object(router.MCPAccessControlService, "sanitize_for_save", side_effect=lambda x: x),
            patch.object(router, "AssistantRepository"),
            patch.object(router, "category_service"),
            patch.object(router, "_track_mcp_usage_changes"),
            patch.object(router, "_filter_invalid_datasources"),
            patch.object(router, "_track_assistant_management_metric"),
            patch.object(router.GuardrailService, "sync_guardrail_assignments_for_entity"),
            patch.object(router, "_trigger_re_review") as trigger,
            patch.object(router, "is_approval_gate_armed", return_value=gate_enabled),
            patch.object(AssistantVersionCompareService, "has_configuration_changes", return_value=config_changed),
            patch.object(AssistantVersionCompareService, "has_marketplace_field_changes", return_value=field_changed),
        ):
            router.update_assistant("a-1", _request(), MagicMock(), user)
        return trigger

    def test_reviewed_field_only_edit_triggers_re_submit(self):
        assert self._call(True, False, True).call_count == 1

    def test_config_edit_triggers_re_submit(self):
        assert self._call(True, True, False).call_count == 1

    def test_no_change_does_not_trigger(self):
        assert self._call(True, False, False).call_count == 0

    def test_gate_disabled_does_not_trigger(self):
        assert self._call(False, True, True).call_count == 0


def _sub(status, **overrides):
    sub = MagicMock(spec=Assistant)
    sub.id = "sub-1"
    sub.name = "Sub"
    sub.marketplace_status = status
    sub.toolkits = [ToolKitDetails(toolkit="git", tools=[])]
    sub.mcp_servers = []
    sub.categories = ["cat-1"]
    for key, value in overrides.items():
        setattr(sub, key, value)
    return sub


class TestRejectUnreviewedSubAssistantChanges:
    @pytest.mark.parametrize("status", ["APPROVED", "PENDING_REVIEW"])
    def test_toolkit_change_rejected(self, status):
        settings = SubAssistantPublishSettings(
            assistant_id="sub-1", toolkits=[ToolKitDetails(toolkit="jira", tools=[])]
        )
        with pytest.raises(ExtendedHTTPException) as exc:
            subs._reject_unreviewed_sub_assistant_changes(_sub(status), settings)
        assert exc.value.code == 409

    def test_category_change_rejected(self):
        settings = SubAssistantPublishSettings(assistant_id="sub-1", categories=["cat-2"])
        with pytest.raises(ExtendedHTTPException):
            subs._reject_unreviewed_sub_assistant_changes(_sub("APPROVED"), settings)

    def test_unchanged_settings_allowed(self):
        settings = SubAssistantPublishSettings(
            assistant_id="sub-1", toolkits=[ToolKitDetails(toolkit="git", tools=[])], categories=["cat-1"]
        )
        subs._reject_unreviewed_sub_assistant_changes(_sub("APPROVED"), settings)

    def test_visibility_only_settings_allowed(self):
        subs._reject_unreviewed_sub_assistant_changes(
            _sub("APPROVED"), SubAssistantPublishSettings(assistant_id="sub-1", is_global=False)
        )

    def test_no_settings_allowed(self):
        subs._reject_unreviewed_sub_assistant_changes(_sub("APPROVED"), None)

    @pytest.mark.parametrize("status", ["DRAFT", "REJECTED", None])
    def test_unreviewed_status_may_be_reconfigured(self, status):
        settings = SubAssistantPublishSettings(
            assistant_id="sub-1", toolkits=[ToolKitDetails(toolkit="jira", tools=[])]
        )
        subs._reject_unreviewed_sub_assistant_changes(_sub(status), settings)


class TestPublishSubAssistantsGuard:
    def _publish(self, gate_armed):
        sub = _sub("APPROVED")
        settings = [
            SubAssistantPublishSettings(assistant_id="sub-1", toolkits=[ToolKitDetails(toolkit="jira", tools=[])])
        ]
        parent = MagicMock(spec=Assistant)
        parent.assistant_ids = ["sub-1"]
        with (
            patch.object(subs, "_validate_sub_assistant", return_value=("sub-1", sub)),
            patch.object(subs, "_publish_single_sub_assistant", return_value=True) as publish_one,
        ):
            try:
                subs._publish_sub_assistants(parent, settings, MagicMock(), gate_armed, False, None)
            finally:
                self.publish_one = publish_one

    def test_gate_armed_rejects_before_any_publish(self):
        with pytest.raises(ExtendedHTTPException):
            self._publish(gate_armed=True)
        self.publish_one.assert_not_called()

    def test_gate_disabled_publishes_as_before(self):
        self._publish(gate_armed=False)
        self.publish_one.assert_called_once()
