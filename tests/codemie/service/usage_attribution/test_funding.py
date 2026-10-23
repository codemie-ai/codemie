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

"""Web Platform funding: when a shared project's platform budget paid for each member, the A9 request and its
parsing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from codemie.service.usage_attribution import funding, parsing, queries
from codemie.service.usage_attribution.funding import RESTORED, STOPPED, to_ms

S, E = datetime(2026, 9, 13, tzinfo=timezone.utc), datetime(2026, 10, 13, tzinfo=timezone.utc)
MID = "2026-09-28T00:00:00Z"
LATE = "2026-10-05T00:00:00Z"
BEFORE = "2026-06-01T00:00:00Z"


def ms(value: str | datetime) -> int:
    return to_ms(value)


def facts(assignments=(), allocations=(), stops=()) -> dict:
    return {"assignments": [list(a) for a in assignments], "allocations": [list(a) for a in allocations], "stops": [list(x) for x in stops]}  # fmt: skip


def assignment(project="p", budget="b1", at=BEFORE, deleted=None, active=True, budget_deleted=None) -> tuple:
    return (project, budget, at, deleted, active, budget_deleted)


def allocation(uid="u1", project="p", at=BEFORE, deleted=None) -> tuple:
    return (project, uid, at, deleted)


class TestIntervals:
    def test_union_merges_touching_and_overlapping(self):
        assert funding.union([(5, 8), (1, 3), (3, 4), (7, 9)]) == [(1, 4), (5, 9)]

    def test_intersect_and_subtract(self):
        assert funding.intersect([(0, 10)], [(2, 4), (8, 12)]) == [(2, 4), (8, 10)]
        assert funding.subtract([(0, 10)], [(2, 4), (8, 12)]) == [(0, 2), (4, 8)]
        assert funding.subtract([(0, 10)], []) == [(0, 10)]


class TestStops:
    def test_a_stopped_budget_without_events_is_stopped_all_along(self):
        assert funding.stopped_intervals([], {"b1": False, "b2": True}) == {"b1": [(0, 2**62)]}

    def test_stop_and_restore_events_give_the_dates(self):
        stops = [["b1", STOPPED, MID], ["b1", RESTORED, LATE]]
        assert funding.stopped_intervals(stops, {"b1": True}) == {"b1": [(ms(MID), ms(LATE))]}

    def test_a_restore_first_means_stopped_before_it(self):
        assert funding.stopped_intervals([["b1", RESTORED, MID]], {"b1": True}) == {"b1": [(0, ms(MID))]}

    def test_stopped_now_after_a_restore_is_stopped_since_the_last_event(self):
        stops = [["b1", STOPPED, MID], ["b1", RESTORED, LATE]]
        # Restored, then stopped again without an event: stopped from the first stop on.
        assert funding.stopped_intervals(stops, {"b1": False}) == {"b1": [(ms(MID), 2**62)]}


class TestFundedIntervals:
    def test_a_member_funded_all_period(self):
        out = funding.funded_intervals(facts([assignment()], [allocation()]), S, E)
        assert out == {"p": {"u1": [[ms(S), ms(E)]]}}

    def test_a_budget_assigned_during_the_period_funds_only_from_then(self):
        out = funding.funded_intervals(facts([assignment(at=MID)], [allocation()]), S, E)
        assert out == {"p": {"u1": [[ms(MID), ms(E)]]}}

    def test_an_allocation_removed_during_the_period_funds_until_then(self):
        out = funding.funded_intervals(facts([assignment()], [allocation(deleted=MID)]), S, E)
        assert out == {"p": {"u1": [[ms(S), ms(MID)]]}}

    def test_a_stop_and_restore_leave_a_gap(self):
        stops = [["b1", STOPPED, MID], ["b1", RESTORED, LATE]]
        out = funding.funded_intervals(facts([assignment()], [allocation()], stops), S, E)
        assert out == {"p": {"u1": [[ms(S), ms(MID)], [ms(LATE), ms(E)]]}}

    def test_nothing_is_funded_by_a_budget_stopped_without_events(self):
        assert funding.funded_intervals(facts([assignment(active=False)], [allocation()]), S, E) == {}

    def test_a_deleted_budget_funds_until_its_deletion(self):
        out = funding.funded_intervals(facts([assignment(budget_deleted=MID)], [allocation()]), S, E)
        assert out == {"p": {"u1": [[ms(S), ms(MID)]]}}

    def test_a_member_without_an_allocation_is_not_funded(self):
        out = funding.funded_intervals(facts([assignment()], [allocation("u1")]), S, E)
        assert "u2" not in out["p"]

    def test_an_allocation_without_a_platform_budget_funds_nothing(self):
        assert funding.funded_intervals(facts([], [allocation()]), S, E) == {}

    def test_an_assignment_removed_before_the_period_funds_nothing(self):
        out = funding.funded_intervals(facts([assignment(deleted="2026-09-01T00:00:00Z")], [allocation()]), S, E)
        assert out == {}

    def test_a_reassigned_budget_continues_the_funding(self):
        rows = [assignment(budget="b1", deleted=MID), assignment(budget="b2", at=MID)]
        out = funding.funded_intervals(facts(rows, [allocation()]), S, E)
        assert out == {"p": {"u1": [[ms(S), ms(E)]]}}

    def test_datetimes_work_like_iso_strings(self):
        row = ("p", "b1", datetime(2026, 9, 28, tzinfo=timezone.utc), None, True, None)
        out = funding.funded_intervals(facts([row], [allocation()]), S, E)
        assert out == {"p": {"u1": [[ms(MID), ms(E)]]}}


class TestPlan:
    def test_members_funded_part_of_the_period_are_grouped_by_their_intervals(self):
        funded = {
            "p": {"u1": [[ms(S), ms(E)]], "u2": [[ms(MID), ms(E)]], "u3": [[ms(MID), ms(E)]]},
            "q": {"u4": [[ms(S), ms(MID)]]},
        }
        fplan = funding.plan(funded, S, E)
        assert fplan["projects"] == ["p", "q"]
        assert fplan["full"] == {"p": ["u1"]}
        assert fplan["groups"] == [["p", ["u2", "u3"], [[ms(MID), ms(E)]]], ["q", ["u4"], [[ms(S), ms(MID)]]]]
        assert funding.summary(fplan) == {"projects": 2, "members_whole_range": 1, "members_part_of_range": 3}


class TestWebFundingRequest:
    def fplan(self) -> dict:
        return funding.plan({"p": {"u1": [[ms(S), ms(E)]], "u2": [[ms(MID), ms(E)]]}}, S, E)

    def test_no_request_without_funded_projects(self):
        assert queries.web_funding_body(S, E, funding.plan({}, S, E)) is None

    def test_the_request_splits_members_funded_part_of_the_period(self):
        body = queries.web_funding_body(S, E, self.fplan())
        assert {"terms": {"attributes.project.keyword": ["p"]}} in body["query"]["bool"]["filter"]
        group = body["aggs"]["fg"]["aggs"]["g"]["filters"]["filters"]["g0"]["bool"]["filter"]
        assert group[0] == {"term": {"attributes.project.keyword": "p"}}
        assert group[1] == {"terms": {"attributes.user_id.keyword": ["u2"]}}
        assert group[2]["bool"]["should"] == [
            {"range": {"@timestamp": {"gte": ms(MID), "lt": ms(E), "format": "epoch_millis"}}}
        ]

    def test_totals_add_the_funding_to_the_web_who_pays_request(self):
        body = queries.web_pay_body(S, E, ["p"], self.fplan())
        assert {"fb", "personal", "shared", "fp", "fg"} == set(body["aggs"])
        assert set(queries.web_pay_body(S, E, ["p"], funding.plan({}, S, E))["aggs"]) == {"fb", "personal", "shared"}

    def test_parsing_charges_back_the_funded_part(self):
        aggs = {
            "fp": {"p": {"buckets": [{"key": "p", "u": {"buckets": [
                {"key": "u1", "spend": {"value": 50}}, {"key": "u2", "spend": {"value": 40}},
                {"key": "u3", "spend": {"value": 7}}]}}]}},
            "fg": {"g": {"buckets": {"g0": {"u": {"buckets": [{"key": "u2", "spend": {"value": 15}}]}}}}},
        }  # fmt: skip
        out = parsing.parse_web_funding(aggs, self.fplan())
        assert out == {"p": {"u1": [50, 50], "u2": [40, 15], "u3": [7, 0.0]}}

    def test_parsing_without_a_response(self):
        assert parsing.parse_web_funding(None, self.fplan()) == {}


@pytest.mark.parametrize("value", [None, ""])
def test_missing_timestamps(value):
    assert to_ms(value) is None
