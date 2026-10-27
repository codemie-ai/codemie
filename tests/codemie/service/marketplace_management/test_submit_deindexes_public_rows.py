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

"""SUBMIT de-indexes a row that was public; publishing no longer pre-hides it (EPMCDME-14234)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.routers.assistant import marketplace as router
from codemie.service.marketplace_management.models import ReviewDecision
from codemie.service.marketplace_management.queue_service import TRANSITIONS, MarketplaceQueueService

SYNC = "codemie.service.marketplace_management.marketplace_index_sync"


def _outcome():
    outcome = MagicMock(ok=True)
    outcome.as_dict.return_value = {"state": "OK"}
    return outcome


class TestSubmitSideEffects:
    def test_submit_rule_opts_in_to_conditional_deindex(self):
        rule = TRANSITIONS[ReviewDecision.SUBMIT]
        assert rule.deindex_if_was_public is True
        assert rule.deindex is False  # never-public drafts must not trigger an ES delete

    def test_public_row_is_removed_from_the_index(self):
        with patch(f"{SYNC}.remove_assistant", return_value=_outcome()) as remove:
            result = MarketplaceQueueService._side_effects(
                TRANSITIONS[ReviewDecision.SUBMIT], "a-1", "Name", was_public=True
            )
        remove.assert_called_once_with("a-1", "Name")
        assert result == {"state": "OK"}

    def test_never_public_row_has_no_index_side_effect(self):
        with patch(f"{SYNC}.remove_assistant") as remove, patch(f"{SYNC}.index_assistant") as index:
            result = MarketplaceQueueService._side_effects(
                TRANSITIONS[ReviewDecision.SUBMIT], "a-1", "Name", was_public=False
            )
        assert result is None
        remove.assert_not_called()
        index.assert_not_called()

    @pytest.mark.parametrize("decision", [ReviewDecision.REJECT, ReviewDecision.WITHDRAW, ReviewDecision.RE_SUBMIT])
    def test_unconditional_deindex_rules_unchanged(self, decision):
        with patch(f"{SYNC}.remove_assistant", return_value=_outcome()) as remove:
            MarketplaceQueueService._side_effects(TRANSITIONS[decision], "a-1", "Name", was_public=False)
        remove.assert_called_once()

    def test_approve_still_reindexes(self):
        with patch(f"{SYNC}.index_assistant", return_value=_outcome()) as index:
            MarketplaceQueueService._side_effects(TRANSITIONS[ReviewDecision.APPROVE], "a-1", "Name")
        index.assert_called_once()


class TestSubmitAssistantForReview:
    def _assistant(self, *, is_global, status):
        assistant = MagicMock(spec=Assistant)
        assistant.id = "a-1"
        assistant.is_global = is_global
        assistant.marketplace_status = status
        return assistant

    def _submit(self, assistant):
        seen = {}

        def record_update(refresh=False):
            seen["is_global_at_save"] = assistant.is_global

        assistant.update.side_effect = record_update
        with patch.object(router, "_submit_for_review", return_value="2026-01-01T00:00:00") as submit:
            router._submit_assistant_for_review(assistant, MagicMock(), [], None)
        return seen, submit

    def test_public_draft_is_not_hidden_before_the_cas(self):
        assistant = self._assistant(is_global=True, status="DRAFT")
        seen, submit = self._submit(assistant)

        assert seen["is_global_at_save"] is True
        submit.assert_called_once()

    def test_response_reflects_what_the_cas_wrote(self):
        assistant = self._assistant(is_global=True, status="DRAFT")
        self._submit(assistant)

        assert assistant.marketplace_status == "PENDING_REVIEW"
        assert assistant.is_global is False

    def test_failed_submit_leaves_the_public_row_public(self):
        assistant = self._assistant(is_global=True, status="DRAFT")
        assistant.update.side_effect = lambda refresh=False: None
        with (
            patch.object(router, "_submit_for_review", side_effect=RuntimeError("409")),
            pytest.raises(RuntimeError),
        ):
            router._submit_assistant_for_review(assistant, MagicMock(), [], None)

        assert assistant.is_global is True

    def test_legacy_private_approved_row_is_still_normalised_to_draft(self):
        assistant = self._assistant(is_global=False, status="APPROVED")
        saved = {}
        assistant.update.side_effect = lambda refresh=False: saved.update(status=assistant.marketplace_status)
        with patch.object(router, "_submit_for_review", return_value=None):
            router._submit_assistant_for_review(assistant, MagicMock(), [], None)

        # SUBMIT's allowed_from excludes APPROVED, so the legacy row is saved as DRAFT first.
        assert saved["status"] == "DRAFT"
        assistant.refresh_marketplace_columns_from_db.assert_called_once()
