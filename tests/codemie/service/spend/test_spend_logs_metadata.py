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

"""The header that carries correlation, identity, dimension and origin to LiteLLM.

The rules under test are the ones whose violation is **silent**: a malformed value discards all seven
keys at once with no error on either side, a partial identity triple produces a well-formed row that
disappears from every category budget, and an origin outside the vocabulary produces one that
disappears from every traffic class.
"""

import json
import uuid
from unittest.mock import patch

import pytest

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend import spend_logs_metadata
from codemie.service.spend.spend_logs_metadata import (
    SPEND_LOGS_METADATA_HEADER,
    build_spend_logs_metadata,
    spend_logs_metadata_header,
)
from codemie.service.spend.traffic_class import TrafficOrigin

USER = "aaaaaaaa-1111-1111-1111-111111111111"
ALL_SIX = {
    "request_id": "req-1",
    "run_id": "run-1",
    "user_id": USER,
    "category": BudgetCategory.PLATFORM.value,
    "project": "acme",
    "workflow_id": "wf-1",
}


def payload_of(header: dict[str, str]) -> dict:
    return json.loads(header[SPEND_LOGS_METADATA_HEADER])


class TestTheValueIsJsonAndSurvivesTheParse:
    def test_all_six_keys_are_carried(self):
        assert payload_of(spend_logs_metadata_header(**ALL_SIX)) == {
            "codemie_request_id": "req-1",
            "codemie_run_id": "run-1",
            "codemie_user_id": USER,
            "codemie_category": "platform",
            "codemie_project": "acme",
            "codemie_workflow_id": "wf-1",
        }

    def test_every_value_is_a_flat_string(self):
        """Every target column is text, and nothing downstream would reject a nested value."""
        assert all(isinstance(v, str) for v in payload_of(spend_logs_metadata_header(**ALL_SIX)).values())

    def test_a_non_ascii_project_is_escaped_rather_than_sent_raw(self):
        """Header values must be latin-1/ASCII, and the project name is free text."""
        raw = spend_logs_metadata_header(**{**ALL_SIX, "project": "prójekt-中文"})[SPEND_LOGS_METADATA_HEADER]
        raw.encode("ascii")  # would raise if ensure_ascii were turned off
        assert payload_of({SPEND_LOGS_METADATA_HEADER: raw})["codemie_project"] == "prójekt-中文"

    def test_nothing_to_send_yields_no_header_rather_than_an_empty_object(self):
        """An empty object is well-formed and carries no facts - indistinguishable from a drop."""
        assert spend_logs_metadata_header() == {}


class TestTheIdentityTripleIsAllOrNothing:
    """A partial tuple is refused as a whole downstream, so it is never sent in part."""

    @pytest.mark.parametrize(
        "broken",
        [
            {"user_id": "not-a-uuid"},
            {"user_id": None},
            {"category": None},
            {"category": "no-such-category"},
        ],
    )
    def test_a_broken_triple_sends_no_identity_key_at_all(self, broken):
        payload = build_spend_logs_metadata(**{**ALL_SIX, **broken})
        assert "codemie_user_id" not in payload
        assert "codemie_category" not in payload
        assert "codemie_project" not in payload

    def test_the_correlation_ids_still_travel_when_identity_fails(self):
        """Different groups, different failure semantics - one failing must not silence the other."""
        payload = build_spend_logs_metadata(**{**ALL_SIX, "user_id": "not-a-uuid"})
        assert payload["codemie_request_id"] == "req-1"
        assert payload["codemie_run_id"] == "run-1"

    def test_an_absent_project_means_principal_form_and_does_not_fail_the_triple(self):
        payload = build_spend_logs_metadata(**{**ALL_SIX, "project": None})
        assert payload["codemie_user_id"] == USER
        assert payload["codemie_category"] == "platform"
        assert "codemie_project" not in payload

    @pytest.mark.parametrize("category", [c.value for c in BudgetCategory])
    def test_every_closed_set_member_is_accepted(self, category):
        assert build_spend_logs_metadata(**{**ALL_SIX, "category": category})["codemie_category"] == category


class TestTheDimensionKeyIsOutsideThatRule:
    """The asymmetry: absence is NORMAL for a dimension key and a DEFECT for an identity key."""

    def test_an_absent_workflow_id_leaves_identity_untouched(self):
        """An ordinary chat call has no workflow id, and must not lose its identity for it.

        Were completeness tested over every key present in the object, every chat call would fail,
        fall to the fallback parser, and collapse project attribution platform-wide.
        """
        payload = build_spend_logs_metadata(**{**ALL_SIX, "workflow_id": None})
        assert "codemie_workflow_id" not in payload
        assert payload["codemie_user_id"] == USER
        assert payload["codemie_category"] == "platform"
        assert payload["codemie_project"] == "acme"

    def test_only_its_own_key_is_affected_by_an_empty_dimension_value(self):
        payload = build_spend_logs_metadata(**{**ALL_SIX, "workflow_id": ""})
        assert "codemie_workflow_id" not in payload
        assert len(payload) == 5


class TestTheOriginKeyIsValidatedOnItsOwn:
    """The fourth group: absence is normal, and an unknown word is dropped rather than carried."""

    @pytest.mark.parametrize("origin", [o.value for o in TrafficOrigin])
    def test_every_vocabulary_member_is_carried(self, origin):
        assert build_spend_logs_metadata(**ALL_SIX, origin=origin)["codemie_origin"] == origin

    def test_an_absent_origin_sends_no_key(self):
        """Most rows before the sending release have none, and a NULL is a known hole."""
        assert "codemie_origin" not in build_spend_logs_metadata(**ALL_SIX)

    @pytest.mark.parametrize("unknown", ["no-such-origin", "CONVERSATION", " conversation"])
    def test_a_word_outside_the_vocabulary_is_dropped_and_costs_nothing_else(self, unknown):
        """A value no traffic class claims would fall out of the split while looking well-formed."""
        payload = build_spend_logs_metadata(**ALL_SIX, origin=unknown)
        assert "codemie_origin" not in payload
        assert payload == build_spend_logs_metadata(**ALL_SIX)

    def test_the_drop_is_reported(self):
        """Silent is the one thing it must not be - the sender is the only place that knows."""
        with patch.object(spend_logs_metadata, "logger") as log:
            build_spend_logs_metadata(**ALL_SIX, origin="no-such-origin")
        assert log.debug.called

    def test_an_unknown_origin_does_not_disturb_the_identity_triple(self):
        payload = build_spend_logs_metadata(**ALL_SIX, origin="no-such-origin")
        assert payload["codemie_user_id"] == USER
        assert payload["codemie_category"] == "platform"
        assert payload["codemie_project"] == "acme"

    def test_a_valid_origin_does_not_rescue_a_broken_identity_triple(self):
        """The groups are independent in both directions."""
        payload = build_spend_logs_metadata(
            **{**ALL_SIX, "user_id": "not-a-uuid"}, origin=TrafficOrigin.CONVERSATION.value
        )
        assert payload["codemie_origin"] == "conversation"
        assert "codemie_user_id" not in payload
        assert "codemie_category" not in payload

    def test_the_origin_travels_in_the_header_as_a_seventh_key(self):
        payload = payload_of(spend_logs_metadata_header(**ALL_SIX, origin=TrafficOrigin.CLI.value))
        assert len(payload) == 7
        assert payload["codemie_origin"] == "cli"


class TestTheHeaderStaysSmall:
    """AD-21 (amendment 2026-09-07): the header's size is measured, not extrapolated, and this pins it.

    The limits that matter are 8 KiB per header line (nginx, llhttp) and 16 KiB per header block
    (h11). A seven-key header with a 50-character project name and the longest closed-vocabulary
    values measures well under 1 KiB, so an eighth key or a longer value that pushes past this is
    noticed here, not as a mass NULL population in the ledger.
    """

    def test_the_seven_key_header_stays_under_one_kib(self):
        header = spend_logs_metadata_header(
            request_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            user_id=USER,
            category=max((c.value for c in BudgetCategory), key=len),
            project="p" * 50,
            workflow_id=str(uuid.uuid4()),
            origin=max((o.value for o in TrafficOrigin), key=len),
        )
        assert len(payload_of(header)) == 7

        wire_line = f"{SPEND_LOGS_METADATA_HEADER}: {header[SPEND_LOGS_METADATA_HEADER]}\r\n".encode("ascii")
        assert len(wire_line) < 1024
