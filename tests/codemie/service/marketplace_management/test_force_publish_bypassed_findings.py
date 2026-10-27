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

"""Force-publish records which CRITICAL gate findings it bypassed (EPMCDME-14234)."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.routers import marketplace_management as mm_router
from codemie.service.marketplace_management import queue_service as qs
from codemie.service.marketplace_management.models import ForcePublishResponse, ReviewDecision
from codemie.service.marketplace_management.queue_service import MarketplaceQueueService
from codemie.service.marketplace_management.validator.models import Severity


def _finding(rule, severity):
    return SimpleNamespace(rule=rule, severity=severity, message=f"{rule} failed")


@pytest.fixture
def service():
    return MarketplaceQueueService()


class TestCriticalGateFindings:
    def _run(self, findings=None, assistant=MagicMock(), boom=None):
        with (
            patch.object(qs.Assistant, "find_by_id", return_value=assistant),
            patch("codemie.service.marketplace_management.entity_adapter.assistant_to_record"),
            patch("codemie.service.marketplace_management.validator.config.Config.for_assistant_gates"),
            patch(
                "codemie.service.marketplace_management.validator.gates.run_all",
                side_effect=boom,
                return_value=findings,
            ),
        ):
            return MarketplaceQueueService._critical_gate_findings("a-1")

    def test_keeps_only_critical_in_rule_severity_message_shape(self):
        result = self._run([_finding("name_not_banned", Severity.CRITICAL), _finding("polish", Severity.OPTIONAL)])
        assert result == [{"rule": "name_not_banned", "severity": "CRITICAL", "message": "name_not_banned failed"}]

    def test_no_critical_findings_is_empty_list(self):
        assert self._run([_finding("polish", Severity.OPTIONAL)]) == []

    def test_gate_failure_is_unavailable_not_empty(self):
        assert self._run(boom=RuntimeError("db down")) is None

    def test_missing_assistant_is_unavailable(self):
        assert self._run([], assistant=None) is None


class TestForcePublishRecordsBypassed:
    def _force(self, service, findings):
        captured = {}

        def fake_decide(**kwargs):
            extras = kwargs["extras"]
            extras.pre_cas_hook(kwargs["assistant_id"])
            captured["extras"] = extras
            captured["decision"] = kwargs["decision"]
            return MagicMock()

        with (
            patch.object(service, "decide", side_effect=fake_decide),
            patch.object(MarketplaceQueueService, "_critical_gate_findings", return_value=findings),
        ):
            service.force_publish("a-1", "admin", override_notice="because")
        return captured

    def test_critical_findings_land_in_result_extra(self, service):
        findings = [{"rule": "r", "severity": "CRITICAL", "message": "m"}]
        captured = self._force(service, findings)

        assert captured["decision"] is ReviewDecision.FORCE_PUBLISH
        assert captured["extras"].result_extra == {"bypassed_gate_findings": findings}

    def test_no_findings_is_explicit_empty_list(self, service):
        captured = self._force(service, [])
        assert captured["extras"].result_extra == {"bypassed_gate_findings": []}

    def test_unavailable_gates_do_not_block_and_are_flagged(self, service):
        captured = self._force(service, None)
        assert captured["extras"].result_extra == {
            "bypassed_gate_findings": [],
            "bypassed_gate_findings_unavailable": True,
        }

    def test_findings_reach_the_stored_result_body(self):
        body = MarketplaceQueueService._result_body(
            entity_kind=qs.EntityKind.ASSISTANT,
            entity_id="a-1",
            updated={"marketplace_status": "APPROVED"},
            override_notice="because",
            stamp_submitted=False,
            extra={"bypassed_gate_findings": [{"rule": "r", "severity": "CRITICAL", "message": "m"}]},
        )
        assert body["override_notice"] == "because"
        assert body["bypassed_gate_findings"][0]["rule"] == "r"


class TestLatestForcePublish:
    def _lookup(self, service, row):
        session = MagicMock()
        session.exec.return_value.mappings.return_value.first.return_value = row

        @contextmanager
        def fake_session():
            yield session

        with patch.object(qs, "get_session", fake_session):
            return service.latest_force_publish("a-1", 5), session

    def test_returns_notice_actor_and_findings(self, service):
        row = {
            "override_notice": "because",
            "decided_by_email": "admin@x",
            "result": {"bypassed_gate_findings": [{"rule": "r", "severity": "CRITICAL", "message": "m"}]},
        }
        found, session = self._lookup(service, row)

        assert found == {
            "override_notice": "because",
            "overridden_by": "admin@x",
            "bypassed_gate_findings": [{"rule": "r", "severity": "CRITICAL", "message": "m"}],
            "bypassed_gate_findings_unavailable": False,
        }
        assert session.exec.call_args.kwargs["params"]["version"] == 5

    def test_none_when_current_version_did_not_come_from_force_publish(self, service):
        found, _ = self._lookup(service, None)
        assert found is None

    def test_legacy_row_without_findings_reads_as_empty(self, service):
        found, _ = self._lookup(service, {"override_notice": "n", "decided_by_email": "a", "result": {}})
        assert found["bypassed_gate_findings"] == []


class TestReviewOverride:
    def _assistant(self, status="APPROVED"):
        return SimpleNamespace(id="a-1", marketplace_status=status, marketplace_version=5)

    def test_not_approved_has_no_override(self):
        with patch.object(mm_router._queue_service, "latest_force_publish") as lookup:
            assert mm_router._review_override(self._assistant("PENDING_REVIEW")) == {}
        lookup.assert_not_called()

    def test_approved_by_force_publish_is_exposed_with_notice(self):
        record = {
            "override_notice": "because",
            "overridden_by": "admin@x",
            "bypassed_gate_findings": [{"rule": "r", "severity": "CRITICAL", "message": "m"}],
            "bypassed_gate_findings_unavailable": False,
        }
        with patch.object(mm_router._queue_service, "latest_force_publish", return_value=record):
            override = mm_router._review_override(self._assistant())

        assert override["override_notice"] == "because"
        assert override["overridden_by"] == "admin@x"
        assert override["bypassed_gate_findings"][0].rule == "r"

    def test_approved_by_normal_approve_has_no_override(self):
        with patch.object(mm_router._queue_service, "latest_force_publish", return_value=None):
            assert mm_router._review_override(self._assistant()) == {}

    def test_lookup_failure_degrades_to_no_override(self):
        with patch.object(mm_router._queue_service, "latest_force_publish", side_effect=RuntimeError("db")):
            assert mm_router._review_override(self._assistant()) == {}


def test_force_publish_response_carries_bypassed_findings_and_defaults_to_empty():
    base = {"ok": True, "entity_kind": "ASSISTANT", "entity_id": "a-1", "status": "APPROVED", "override_notice": "n"}

    assert ForcePublishResponse(**base).bypassed_gate_findings == []
    with_findings = ForcePublishResponse(
        **base, bypassed_gate_findings=[{"rule": "r", "severity": "CRITICAL", "message": "m"}]
    )
    assert with_findings.bypassed_gate_findings[0].rule == "r"
