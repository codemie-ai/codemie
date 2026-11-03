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

"""Person view: the delivery framework of a recent session is classified from the names stored in Postgres, else from
the Elasticsearch skill keys."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError
from codemie.repository.cli_analytics.scope import allowed
from codemie.service.analytics.delivery_framework import classify_delivery_framework
from codemie.service.usage_attribution import handler as h
from codemie.service.usage_attribution import postgres_sources

ADMIN = SimpleNamespace(id="admin", is_admin=True, is_maintainer=False, is_auditor=False)
STLC = "STLC Factory (QA factory)"


class FakeReader:
    def __init__(self, names=None, error=None, delay=0.0):
        self.names = names or {}
        self.error = error
        self.delay = delay
        self.calls = []

    async def get_skill_names_by_session(self, session_ids, *, scope):
        assert allowed(scope, "N", "someone@x.com", "work-project")
        assert not allowed(scope, "P", "someone@x.com", "personal-project")
        self.calls.append(list(session_ids))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return [{"session_id": s, "skill_names": n} for s, n in self.names.items()]


async def framework(reader, es_skills, sid="s1"):
    extra = {sid: {"prompts": 1, "skills": es_skills}} if es_skills is not None else {}
    names = await postgres_sources.skill_names_by_session(reader, [sid])
    merged = postgres_sources.postgres_names_win(extra, names)
    bucket = {"key": sid, "doc_count": 1}
    return h.UsageAttributionHandler._person_recent_sessions([bucket], {}, merged, classify_delivery_framework)[0][
        "framework"
    ]


def label(fw):
    return getattr(fw, "label", fw)


@pytest.mark.asyncio
async def test_postgres_only_names_classify():
    reader = FakeReader({"s1": ["stlc-factory:suite-health"]})
    assert label(await framework(reader, [])) == STLC
    assert reader.calls == [["s1"]]


@pytest.mark.asyncio
async def test_es_only_keys_still_classify():
    assert label(await framework(FakeReader(), ["stlc-factory:suite-health"])) == STLC


@pytest.mark.asyncio
async def test_postgres_names_win_over_installed_es_skills():
    # ES holds the skills INSTALLED in the working directory (an SDLC skill); Postgres holds the STLC skill used.
    reader = FakeReader({"s1": ["stlc-factory:suite-health"]})
    assert label(await framework(reader, ["sdlc-factory:complexity-assessor"])) == STLC


@pytest.mark.asyncio
async def test_a_known_session_that_used_nothing_is_labelled_from_postgres_alone():
    # Postgres knows the session and it used no skill: the ES keys (skills merely installed in the working directory)
    # must not label a plain chat as a factory.
    reader = FakeReader({"s1": []})
    assert await postgres_sources.skill_names_by_session(reader, ["s1"]) == {"s1": []}
    assert label(await framework(reader, ["stlc-factory:suite-health"])) == label(classify_delivery_framework([]))
    assert label(await framework(reader, ["stlc-factory:suite-health"])) != STLC


@pytest.mark.asyncio
async def test_missing_postgres_row_keeps_es_label():
    assert label(await framework(FakeReader({"other": ["x"]}), ["stlc-factory:suite-health"])) == STLC


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("boom"), CliAnalyticsStorageConfigError("off")])
async def test_reader_error_falls_back_to_es_keys(error):
    assert label(await framework(FakeReader(error=error), ["stlc-factory:suite-health"])) == STLC


@pytest.mark.asyncio
async def test_a_slow_read_falls_back_to_es_keys_after_the_timeout(monkeypatch):
    monkeypatch.setattr(postgres_sources, "PG_SKILL_NAMES_TIMEOUT_S", 0.05)
    reader = FakeReader({"s1": ["sdlc-factory:complexity-assessor"]}, delay=5.0)
    assert await postgres_sources.skill_names_by_session(reader, ["s1"]) == {}
    assert label(await framework(reader, ["stlc-factory:suite-health"])) == STLC  # the ES keys alone classify


@pytest.mark.asyncio
async def test_no_reader_means_es_only(monkeypatch):
    monkeypatch.setattr(postgres_sources, "reader_or_none", lambda: None)
    extra = {"s1": {"skills": ["a"]}}
    names = await postgres_sources.skill_names_by_session(None, ["s1"])
    assert names == {}
    assert postgres_sources.postgres_names_win(extra, names) == extra


@pytest.mark.asyncio
async def test_no_session_ids_run_no_read():
    reader = FakeReader({"s1": ["x"]})
    assert await postgres_sources.skill_names_by_session(reader, []) == {}
    assert reader.calls == []


class FlagState:
    feature = False
    storage_calls = 0


@pytest.fixture
def feature_flag(monkeypatch):
    """`cliAnalytics` is off (set `feature_flag.feature = True` to turn it on); the storage hands out a FakeReader."""
    from codemie.configs.customer_config import CustomerConfig
    from codemie.repository.cli_analytics import factory

    state = FlagState()
    state.reader = FakeReader({"s1": ["stlc-factory:suite-health"]})
    monkeypatch.setattr(
        CustomerConfig, "is_feature_enabled", lambda self, name: state.feature and name == "cliAnalytics"
    )

    def storage():
        state.storage_calls += 1
        return SimpleNamespace(reader=state.reader)

    monkeypatch.setattr(factory, "get_cli_analytics_storage", storage)
    return state


def test_reader_or_none_is_none_when_the_feature_is_off(feature_flag):
    assert postgres_sources.reader_or_none() is None
    assert feature_flag.storage_calls == 0


def test_reader_or_none_is_the_storage_reader_when_the_feature_is_on(feature_flag):
    feature_flag.feature = True
    assert postgres_sources.reader_or_none() is feature_flag.reader


def test_reader_or_none_is_none_when_the_storage_cannot_be_built(feature_flag, monkeypatch):
    from codemie.repository.cli_analytics import factory

    def refuse():
        raise CliAnalyticsStorageConfigError("refused")

    feature_flag.feature = True
    monkeypatch.setattr(factory, "get_cli_analytics_storage", refuse)
    assert postgres_sources.reader_or_none() is None


def test_a_broken_storage_configuration_is_logged_at_warning_with_the_exception_type_only(feature_flag, monkeypatch):
    from codemie.repository.cli_analytics import factory

    class Log:
        text = ""

        def warning(self, msg, *args, **_kwargs):
            self.text += msg % args if args else msg

    def refuse():
        raise CliAnalyticsStorageConfigError("postgres://user:secret-password@db/x")

    log = Log()
    feature_flag.feature = True
    monkeypatch.setattr(factory, "get_cli_analytics_storage", refuse)
    monkeypatch.setattr(postgres_sources, "logger", log)
    assert postgres_sources.reader_or_none() is None
    assert "CliAnalyticsStorageConfigError" in log.text
    assert "secret-password" not in log.text


@pytest.mark.asyncio
async def test_a_failing_feature_check_degrades_the_person_view_to_the_es_keys(feature_flag, monkeypatch):
    from codemie.configs.customer_config import CustomerConfig

    def broken(self, name):
        raise RuntimeError("customer config unreadable")

    monkeypatch.setattr(CustomerConfig, "is_feature_enabled", broken)
    assert await postgres_sources.skill_names_by_session(None, ["s1"]) == {}


@pytest.mark.asyncio
async def test_person_view_reads_postgres_names_only_when_the_feature_is_on(feature_flag):
    assert await postgres_sources.skill_names_by_session(None, ["s1"]) == {}
    assert feature_flag.reader.calls == []
    feature_flag.feature = True
    assert await postgres_sources.skill_names_by_session(None, ["s1"]) == {"s1": ["stlc-factory:suite-health"]}


class FakeEs:
    def __init__(self, extra_buckets=None):
        self.extra_buckets = extra_buckets or []
        self.bodies = []

    async def execute_aggregation_query(self, body):
        self.bodies.append(body)
        aggs = body.get("aggs", {})
        if "recent" in aggs:
            return {"aggregations": {"recent": {"t": {"buckets": [{"key": "s1", "doc_count": 2}]}}}}
        if "s" in aggs:
            return {"aggregations": {"s": {"buckets": self.extra_buckets}}}
        return {"aggregations": {}}


@pytest.mark.asyncio
async def test_person_live_labels_recent_sessions_from_postgres_names(monkeypatch):
    from codemie.repository import metrics_elastic_repository as mer
    from codemie.service.usage_attribution import app_db
    from codemie.service.usage_attribution.analysis import resolve_range

    es = FakeEs()

    async def facts():
        return {"shared": []}

    monkeypatch.setattr(mer, "MetricsElasticRepository", lambda: es)
    monkeypatch.setattr(app_db, "project_facts", facts)
    reader = FakeReader({"s1": ["stlc-factory:suite-health"]})
    handler = h.UsageAttributionHandler(ADMIN, service=object(), reader=reader)
    rng = resolve_range("last_7_days", None, None, 90, 30)
    out = await handler._person_live(rng, SimpleNamespace(values={}), "u1", {})
    assert label(out["recent"][0]["framework"]) == STLC
    assert reader.calls == [["s1"]]


@pytest.mark.asyncio
async def test_person_session_extras_reads_prompts_and_skills_of_recent_sessions():
    from codemie.service.usage_attribution.analysis import resolve_range

    es = FakeEs([{"key": "s1", "prompts": {"value": 4}, "skills": {"buckets": [{"key": "a"}]}}])
    rng = resolve_range("last_7_days", None, None, 90, 30)
    assert await h.UsageAttributionHandler._person_session_extras(es, rng, []) == {}
    assert es.bodies == []
    extra = await h.UsageAttributionHandler._person_session_extras(es, rng, [{"key": "s1"}])
    assert extra == {"s1": {"prompts": 4, "skills": ["a"]}}


def test_sessions_extra_body_filters_on_the_given_sessions():
    from datetime import datetime, timezone

    from codemie.service.usage_attribution import queries

    body = queries.sessions_extra_body(
        datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 2, tzinfo=timezone.utc), ["s1", "s2"]
    )
    assert "s1" in str(body["query"]) and "s2" in str(body["query"])
    assert body["aggs"]["s"]["terms"]["size"] == 2
