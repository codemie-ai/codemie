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

"""Cached sections built from the analysis, Elasticsearch request bodies and the partitioned fetch."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import elastic_transport
import pytest
from elasticsearch import ApiError

from codemie.service.usage_attribution import fetch as fetch_module
from codemie.service.usage_attribution import parsing, queries
from codemie.service.usage_attribution.fetch import FetchError, Fetcher, Spec
from codemie.service.usage_attribution.sections import build_sections, shard_of
from tests.codemie.service.usage_attribution.builders import app_db, inputs, person, run, settings_with, tool, value

S, E = datetime(2026, 9, 13, tzinfo=timezone.utc), datetime(2026, 10, 13, tzinfo=timezone.utc)


def sample():
    persons = {
        "u1": person("u1@x.com", {"acme/core": (100, 0, 0, 0), "": (20, 0, 0, 0)}, projects={"u1@x.com": (120, 0, 0)}),
        "u2": person("u2@x.com", {"acme/core": (100, 0, 0, 0)}),
        "u3": person(
            "u3@x.com", {"acme/core": (100, 0, 0, 0), "codemie-katas/k": (50, 0, 50, 0)}, projects={"proj": (50, 0, 50)}
        ),
    }
    return inputs(
        persons,
        values={"acme/core": value(300, people=3, branches={"ACME-1": 300})},
        tools=[tool("u1", "acme/core", sessions=6, edit=5, days=6)],
        web_proj={"proj": {"spend": 40.0, "people": 1.0, "types": {"conversation_assistant_usage": 40.0}}},
        web_people={"u3": {"name": "u3@x.com", "spend": 40.0, "proj": {"proj": 40.0}, "fbp": {}}},
        shared_cli={
            "proj": {
                "spend": 50.0,
                "cb": 0.0,
                "sh": 50.0,
                "fb": 0.0,
                "people_n": 1.0,
                "repos": {"codemie-katas/k": [50.0, 50.0]},
                "people": {"u3": [50.0, "u3@x.com"]},
                "clients": {"codemie-claude": [50.0, 4.0]},
                "models": {"m": 50.0},
            }
        },  # fmt: skip
        app_db=app_db(
            shared=["proj"], people=[["u1", "", "Ann", "Lee", "Poland", "Kraków", "Engineering", "Java", "u1@x.com"]]
        ),
    )


def sample_with_charged_back_person():
    """sample() plus u9: their acme/core work (7 days) is charged back to a project; their desktop work has no
    repository and is allocated to Production by their own classified work, paid from their personal budget."""
    data = sample()
    data["persons"]["u9"] = person("u9@x.com", {"acme/core": (60, 60, 0, 0), "": (40, 0, 0, 0)})
    data["tool"].append(tool("u9", "acme/core", sessions=6, edit=5, days=7))
    return data


class TestSections:
    def test_summary_is_consistent(self):
        secs = build_sections(run(sample()))
        tiles = secs["summary"]["tiles"]
        assert tiles["cli"] == pytest.approx(370)
        assert tiles["prod"] + tiles["exp"] + tiles["uncl"] == pytest.approx(tiles["cli"])
        assert sum(x["value"] for x in secs["summary"]["pie"]) == pytest.approx(tiles["total"])
        assert sum(r["spend"] for r in secs["summary"]["categories"]) == pytest.approx(tiles["cli"])
        assert tiles["ncb"] == pytest.approx(tiles["ncb_cli_shared"] + tiles["ncb_cli_personal"] + tiles["ncb_web"])

    def test_pie_has_five_slices_and_unclassified_without_likely_production_is_other(self):
        pie = build_sections(run(sample()))["summary"]["pie"]
        assert [(x["id"], x["label"]) for x in pie] == [
            ("platform", "Web Platform"),
            ("production", "CLI Production"),
            ("likely_production", "CLI Likely production"),
            ("experiments", "CLI Experiments"),
            ("unclassified", "CLI Unclassified, other"),
        ]

    def test_shared_projects(self):
        secs = build_sections(run(sample()))
        [row] = secs["shared"]
        assert row["name"] == "proj"
        assert row["web_sh"] == pytest.approx(40)  # no platform budget
        assert row["cli_exp"] == pytest.approx(50)  # the kata is learning: not a chargeback candidate
        assert row["ncb"] == pytest.approx(40)
        assert row["via"] == ["web", "cli"]
        detail = secs["sdetail"][shard_of("proj")]["proj"]
        assert detail["row"]["name"] == "proj"
        assert detail["detail"]["repos"][0]["category"] == "learning"
        assert detail["detail"]["web_people"][0]["uid"] == "u3"

    def test_people_and_details(self):
        secs = build_sections(run(sample()))
        rows = {r["uid"]: r for r in secs["people"]}
        assert rows["u1"]["name"] == "Ann Lee"
        assert (rows["u1"]["city"], rows["u1"]["country"], rows["u1"]["job"]) == ("Kraków", "Poland", "Engineering")
        assert rows["u3"]["platform"] == pytest.approx(40)
        assert rows["u3"]["in_shared_project"] is True
        assert rows["u1"]["prod_ncb"] > 0
        detail = secs["pdetail"][shard_of("u1")]["u1"]["detail"]
        assert {r["category"] for r in detail["repos"]} >= {"production"}
        assert any(r.get("reason") == "no_repository" for r in detail["reasons"]) or detail["allocated"]

    def test_people_rows_split_spend_by_who_pays(self):
        rows = {r["uid"]: r for r in build_sections(run(sample()))["people"]}
        for r in rows.values():
            parts = r["charged_back"] + r["prod_ncb"] + r["personal_learning"] + r["personal_other"]
            assert r["total"] == pytest.approx(parts, abs=0.02), r["uid"]
        assert rows["u3"]["personal_learning"] == pytest.approx(
            50.0
        )  # codemie-katas/k: a learning source, paid in proj
        assert rows["u3"]["personal_other"] == pytest.approx(40.0)  # Web Platform in proj, which has no platform budget
        assert rows["u1"]["charged_back"] == 0.0

    def test_production_days_take_the_most_active_production_repository(self):
        rows = {r["uid"]: r for r in build_sections(run(sample()))["people"]}
        assert rows["u1"]["production_days"] == 6  # tool("u1", "acme/core", days=6)
        assert rows["u2"]["production_days"] == 0  # no tool evidence

    def test_days_on_charged_back_work_and_allocated_production_are_not_production_days(self):
        row = {r["uid"]: r for r in build_sections(run(sample_with_charged_back_person()))["people"]}["u9"]
        assert row["charged_back"] == pytest.approx(60.0)
        assert row["prod_ncb"] == pytest.approx(40.0)  # allocated from the desktop work, paid personally
        assert row["production_days"] == 0  # the 7 days on acme/core were paid by a project

    def test_production_days_count_the_share_paid_personally(self):
        # u8 works on acme/core on 8 days; a project pays 75% of it: 2 of the 8 days are paid personally.
        data = sample()
        data["persons"]["u8"] = person("u8@x.com", {"acme/core": (100, 75, 0, 0)})
        data["tool"].append(tool("u8", "acme/core", sessions=6, edit=5, days=8))
        row = {r["uid"]: r for r in build_sections(run(data))["people"]}["u8"]
        assert row["prod_ncb"] == pytest.approx(25.0)
        assert row["production_days"] == 2

    def test_person_popup_tells_repositories_new_since_the_evidence_from_the_limits(self):
        # Evidence reused from earlier in the hour: acme/old is among its repository values, beyond the top
        # repositories, and so is $3 beyond u1's top repositories in the range; acme/new was first used since.
        evidence = {"u1": person("u1@x.com", {"acme/core": (100,)})}
        money = {"u1": person("u1@x.com", {"acme/new": (10,), "acme/old": (7,)}, extra=(3, 0, 0, 0))}
        values = {"acme/core": value(100), "acme/old": value(20)}
        an = run(
            inputs(money, ev_persons=evidence, values=values, days=1), settings_with(allocateByPerson__enabled=False)
        )
        reasons = build_sections(an)["pdetail"][shard_of("u1")]["u1"]["detail"]["reasons"]
        assert [r["text"] for r in reasons] == [
            "$10 outside the analysis limits (beyond the top 10 repositories)",
            "$10 on repositories first used after the evidence was read: no evidence yet",
        ]

    def test_groups_hold_single_repositories_with_production_in_personal_projects(self):
        secs = build_sections(run(sample()))
        [group] = secs["groups"]
        assert group["repositories"] == 1 and group["ncb"] == pytest.approx(300)
        assert "search" in group
        detail = secs["gdetail"][shard_of(group["id"])][group["id"]]
        assert "search" not in detail["row"]
        assert detail["detail"]["repos"][0]["variants"][0]["value"] == "acme/core"

    def test_breakdown_by_profile(self):
        secs = build_sections(run(sample()))
        countries = {r["label"]: r["spend"] for r in secs["breakdown"]["country"]["production"]}
        assert countries["Poland"] > 0
        assert "(unknown)" in countries


class TestQueries:
    def test_who_pays_filters_are_exclusive(self):
        filters = queries.who_pays_filters(["p1", "p2"])
        assert set(filters) == {"charged_back", "shared_no_budget", "personal", "no_customer"}
        assert filters["charged_back"]["bool"]["must_not"] == [queries.has_fb()]
        assert {"terms": {"attributes.project.keyword": ["p1", "p2"]}} in filters["personal"]["bool"]["filter"][1][
            "bool"
        ]["must_not"]

    def test_persons_body_reads_the_fallback_project(self):
        aggs = queries.persons_body(S, E, ["p"], (0, 1), 100, 10)["aggs"]["users"]["aggs"]
        assert aggs["fallback"]["filter"] == queries.has_fb()
        assert aggs["fallback"]["aggs"]["t"]["terms"]["field"] == "attributes.budget_fallback_from.keyword"
        assert aggs["fallback"]["aggs"]["t"]["terms"]["size"] == 3
        assert "spend" in aggs["fallback"]["aggs"]["t"]["aggs"]

    def test_parse_persons_keeps_fallback_projects(self):
        bucket = {"key": "u1", "spend": {"value": 30.0}, "repos": {"buckets": []}, "projects": {"buckets": []},
                  "fallback": {"t": {"buckets": [{"key": "nwnd-pay", "spend": {"value": 12.5}}]}}}  # fmt: skip
        assert parsing.parse_persons([bucket])["u1"]["fallback"] == {"nwnd-pay": 12.5}
        assert parsing.parse_persons([{**bucket, "fallback": {"t": {"buckets": []}}}])["u1"]["fallback"] == {}
        without = {k: v for k, v in bucket.items() if k != "fallback"}  # inputs read before the field existed
        assert parsing.parse_persons([without])["u1"]["fallback"] == {}

    def test_paid_personally_fallback_excludes_default_project_charges(self):
        """EPMCDME-16625: a fallback charged to the default project is charged back there, not paid personally."""
        fb = queries.has_fb()
        assert {"exists": {"field": "attributes.budget_fallback_from.keyword"}} in fb["bool"]["filter"]
        # Records written before the kind existed have no kind and still count as paid personally.
        assert fb["bool"]["must_not"] == [{"term": {"attributes.budget_fallback_kind.keyword": "default_project"}}]
        assert queries.no_fb() == {"bool": {"must_not": [fb]}}

    def test_project_popup_counts_only_personal_fallbacks_of_the_project(self):
        aggs = queries.project_web_detail_body(S, E, "team-a")["aggs"]
        assert aggs["f"]["filter"] == {
            "bool": {"filter": [queries.has_fb(), {"term": {"attributes.budget_fallback_from.keyword": "team-a"}}]}
        }

    def test_cache_namespace_is_bumped_for_the_fallback_kind(self):
        from codemie.service.usage_attribution.constants import CACHE_NAMESPACE

        assert CACHE_NAMESPACE == "codemie:ua:v3"

    def test_no_shared_projects(self):
        assert queries.shared_clause([]) == {"match_none": {}}
        assert queries.personal_clause([]) == {"match_all": {}}

    def test_partitioned_terms(self):
        body = queries.persons_body(S, E, ["p"], (2, 7), 500, 10)
        terms = body["aggs"]["users"]["terms"]
        assert terms["include"] == {"partition": 2, "num_partitions": 7}
        assert terms["size"] == 500
        assert body["aggs"]["users"]["aggs"]["repos"]["terms"]["size"] == 10
        assert body["query"]["bool"]["filter"][0] == {
            "range": {"@timestamp": {"gte": "2026-09-13T00:00:00Z", "lt": "2026-10-13T00:00:00Z"}}
        }

    def test_records_without_a_repository_count_as_no_repository(self):
        # Without "missing", records that have no repository field fall into no bucket, and their spend would count as
        # outside the limits instead of no repository (the empty value).
        body = queries.persons_body(S, E, ["p"], None, 500, 10)
        assert body["aggs"]["users"]["aggs"]["repos"]["terms"]["missing"] == ""

    def test_tool_evidence_counts_days_with_a_runtime_field(self):
        body = queries.tools_body(S, E, None, 100, 10)
        assert "ua_day" in body["runtime_mappings"]

    def test_parse_totals(self):
        aggs = {
            "source": {
                "buckets": {
                    "cli": {"spend": {"value": 10}, "who_pays": {"buckets": {"charged_back": {"spend": {"value": 4}}}}},
                    "other_proxy": {"spend": {"value": 1}},
                    "web_assistant": {"spend": {"value": 2}},
                }
            }
        }
        out = parsing.parse_totals(aggs)
        assert out["cli"]["spend"] == 10 and out["cli"]["charged_back"] == 4
        assert out["other_proxy"] == 1 and out["web"]["assistant"] == 2


class FakeClient:
    """Answers persons partitions; the first round overflows to force a retry with twice as many partitions."""

    def __init__(self, overflow_rounds: int = 1, error: str | None = None):
        self.calls = []
        self.overflow_rounds = overflow_rounds
        self.error = error

    def search(self, index, body, request_timeout, filter_path):
        self.calls.append(body)
        if self.error:
            raise RuntimeError(self.error)
        terms = body["aggs"]["users"]["terms"]
        part = terms.get("include", {"partition": 0, "num_partitions": 1})
        overflow = 5 if part["num_partitions"] < 2 * self.overflow_rounds and self.overflow_rounds else 0
        bucket = {
            "key": f"u{part['partition']}-{part['num_partitions']}",
            "spend": {"value": 1},
            "repos": {"buckets": []},
        }
        return {"took": 3, "aggregations": {"users": {"buckets": [bucket], "sum_other_doc_count": overflow}}}


class TestFetcher:
    def spec(self, est=100, per_item=10):
        return Spec(
            "persons",
            "A2",
            lambda part, size: queries.persons_body(S, E, [], part, size, 10),
            parse=lambda b, acc: parsing.parse_persons(b, acc),
            new_acc=dict,
            est=est,
            per_item=per_item,
            path=("users",),
        )

    def test_plan_partitions_from_the_bucket_budget(self):
        fetcher = Fetcher(FakeClient(0), "idx", 4, 0.5, 2000, 30)
        sp = self.spec(est=1000, per_item=10)
        fetcher.plan([sp])
        assert sp.parts == 10  # 10,000 buckets / 1,000 per request

    def test_overflow_retries_with_twice_as_many_partitions(self):
        client = FakeClient(overflow_rounds=1)
        fetcher = Fetcher(client, "idx", 2, 0.6, 65536, 30)
        sp = self.spec()
        fetcher.execute([sp])
        assert sp.parts == 2
        assert set(sp.result) == {"u0-2", "u1-2"}  # the overflowing round is discarded
        assert sp.stats["requests"] == 3

    def test_errors_fail_the_fetch(self):
        fetcher = Fetcher(FakeClient(0, error="boom"), "idx", 2, 0.6, 65536, 30)
        with pytest.raises(FetchError, match="boom"):
            fetcher.execute([self.spec()])

    def test_too_many_buckets_retries(self):
        class TooMany(FakeClient):
            def search(self, index, body, request_timeout, filter_path):
                part = body["aggs"]["users"]["terms"].get("include", {"num_partitions": 1})
                if part["num_partitions"] < 2:
                    self.calls.append(body)
                    raise RuntimeError("too_many_buckets_exception")
                return super().search(index, body, request_timeout, filter_path)

        fetcher = Fetcher(TooMany(0), "idx", 2, 0.6, 65536, 30)
        sp = self.spec()
        fetcher.execute([sp])
        assert sp.parts == 2 and len(sp.result) == 2

    @pytest.mark.parametrize(
        "failure",
        [
            elastic_transport.ConnectionError("Connection reset by peer"),
            elastic_transport.TlsError("TLS error caused by: connection reset"),
            "shards",
            "status-503",
        ],
    )
    def test_transient_errors_are_retried(self, monkeypatch, failure):
        monkeypatch.setattr(fetch_module.time, "sleep", lambda _s: None)

        class Flaky(FakeClient):
            def __init__(self):
                super().__init__(0)
                self.failures = 2

            def search(self, index, body, request_timeout, filter_path):
                if self.failures:
                    self.failures -= 1
                    self.calls.append(body)
                    if failure == "shards":
                        return {"took": 1, "_shards": {"failed": 1}, "aggregations": {}}
                    if failure == "status-503":
                        raise ApiError("unavailable", meta=SimpleNamespace(status=503), body={})
                    raise failure
                return super().search(index, body, request_timeout, filter_path)

        client = Flaky()
        fetcher = Fetcher(client, "idx", 1, 0.6, 65536, 30)
        sp = self.spec()
        fetcher.execute([sp])
        assert set(sp.result) == {"u0-1"}
        assert len(client.calls) == 3
        assert fetcher.requests == 3

    def test_retries_are_bounded_and_timeouts_are_not_retried(self, monkeypatch):
        monkeypatch.setattr(fetch_module.time, "sleep", lambda _s: None)

        class Down(FakeClient):
            def search(self, index, body, request_timeout, filter_path):
                self.calls.append(body)
                raise elastic_transport.ConnectionError("Connection refused")

        down = Down(0)
        with pytest.raises(FetchError, match="ConnectionError"):
            Fetcher(down, "idx", 1, 0.6, 65536, 30).execute([self.spec()])
        assert len(down.calls) == 4  # the first attempt and three retries

        class Slow(FakeClient):
            def search(self, index, body, request_timeout, filter_path):
                self.calls.append(body)
                raise elastic_transport.ConnectionTimeout("read timed out")

        slow = Slow(0)
        with pytest.raises(FetchError, match="ConnectionTimeout"):
            Fetcher(slow, "idx", 1, 0.6, 65536, 30).execute([self.spec()])
        assert len(slow.calls) == 1

    def test_is_transient(self):
        assert fetch_module.is_transient(ApiError("unavailable", meta=SimpleNamespace(status=503), body={}))
        # Refused under load (a circuit breaker): sending it again would only add load.
        assert not fetch_module.is_transient(ApiError("circuit", meta=SimpleNamespace(status=429), body={}))
        assert not fetch_module.is_transient(ApiError("bad request", meta=SimpleNamespace(status=400), body={}))
        assert not fetch_module.is_transient(RuntimeError("too_many_buckets_exception"))

    def test_too_many_buckets_in_a_real_error_body(self, monkeypatch):
        """Elasticsearch reports it as search_phase_execution_exception, with the type in the root causes."""
        monkeypatch.setattr(fetch_module.time, "sleep", lambda _s: None)
        error_body = {
            "error": {
                "root_cause": [{"type": "too_many_buckets_exception", "reason": "Trying to create too many buckets"}],
                "type": "search_phase_execution_exception",
                "reason": "all shards failed",
                "caused_by": {"type": "too_many_buckets_exception"},
            },
            "status": 400,
        }

        class TooMany(FakeClient):
            def search(self, index, body, request_timeout, filter_path):
                part = body["aggs"]["users"]["terms"].get("include", {"num_partitions": 1})
                if part["num_partitions"] < 2:
                    self.calls.append(body)
                    raise ApiError(
                        "search_phase_execution_exception", meta=SimpleNamespace(status=400), body=error_body
                    )
                return super().search(index, body, request_timeout, filter_path)

        client = TooMany(0)
        sp = self.spec()
        Fetcher(client, "idx", 2, 0.6, 65536, 30).execute([sp])
        assert sp.parts == 2 and len(sp.result) == 2
        assert len(client.calls) == 3  # one refused request (not retried as transient), then two partitions

    def test_too_many_buckets_in_failed_shards(self):
        class PartlyFailed(FakeClient):
            def search(self, index, body, request_timeout, filter_path):
                part = body["aggs"]["users"]["terms"].get("include", {"num_partitions": 1})
                if part["num_partitions"] < 2:
                    self.calls.append(body)
                    reason = {"type": "too_many_buckets_exception", "reason": "Trying to create too many buckets"}
                    return {"took": 1, "_shards": {"failed": 1, "failures": [{"reason": reason}]}, "aggregations": {}}
                return super().search(index, body, request_timeout, filter_path)

        sp = self.spec()
        Fetcher(PartlyFailed(0), "idx", 2, 0.6, 65536, 30).execute([sp])
        assert sp.parts == 2 and len(sp.result) == 2

    def test_error_types(self):
        assert fetch_module.error_types(None) == set()
        assert fetch_module.error_types({"error": {"type": "a", "caused_by": {"type": "b", "reason": "text"}}}) == {
            "a",
            "b",
        }
