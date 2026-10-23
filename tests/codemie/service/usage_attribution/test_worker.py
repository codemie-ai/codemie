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

"""The worker's reads: which Elasticsearch requests run when evidence of earlier in the hour is reused, and the
responses without document counts."""

from __future__ import annotations

import gzip
import json

import pytest

from codemie.clients.postgres import PostgresClient
from codemie.service.usage_attribution import app_db, worker
from codemie.service.usage_attribution.fetch import Fetcher
from codemie.service.usage_attribution.settings import build_defaults

MONTH = {"from": "2026-09-13T00:00:00Z", "to": "2026-10-13T14:45:00Z", "days": 30.6146}
WEEK = {"from": "2026-10-06T00:00:00Z", "to": "2026-10-13T14:45:00Z", "days": 7.6146}
# The same 30 days computed at the start of the hour.
EARLY_MONTH = {"from": "2026-09-13T00:00:00Z", "to": "2026-10-13T14:00:00Z", "days": 30.5833}
EVIDENCE_SPECS = {"cards", "values", "tool", "sess", "ev_persons"}


def _unzip(blob: bytes) -> dict:
    return json.loads(gzip.decompress(blob))


class _Session:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


# One shared project's platform budget: u1 funded all along, u2 from 10 October (inside every test period).
FUNDING = {
    "assignments": [["proj-a", "b1", "2026-06-01T00:00:00Z", None, True, None]],
    "allocations": [
        ["proj-a", "u1", "2026-06-01T00:00:00Z", None],
        ["proj-a", "u2", "2026-10-10T00:00:00Z", None],
    ],
    "stops": [],
}


@pytest.fixture
def no_db(monkeypatch):
    """No app DB: empty projects, budgets and people, and the funding of one project."""
    monkeypatch.setattr(worker, "_max_buckets", lambda client: 65536)
    monkeypatch.setattr(PostgresClient, "get_engine", classmethod(lambda cls: None))
    monkeypatch.setattr("sqlmodel.Session", _Session)
    static = {"shared": [], "projects": [], "budgets": [], "datasources": [], "members": []}
    monkeypatch.setattr(app_db, "load_static", lambda session, timings: dict(static))
    monkeypatch.setattr(app_db, "load_funding", lambda session, timings: FUNDING)
    monkeypatch.setattr(app_db, "load_people", lambda session, uids, timings, enrichment=True: [])


@pytest.fixture
def executed(monkeypatch, no_db):
    """Fetcher.execute records the specs it runs and answers each with a small result; no ES, no DB."""
    ran: list[str] = []

    def execute(self, specs):
        for sp in specs:
            ran.append(sp.key)
            if sp.key in ("persons", "ev_persons"):
                sp.result = {"u1": {"name": "u1", "spend": 1.0, "pairs": {}}}
            else:
                sp.result = sp.new_acc()

    monkeypatch.setattr(Fetcher, "execute", execute)
    monkeypatch.setattr(worker, "_es_client", lambda concurrency: object())
    return ran


def job(window: dict, evidence_window: dict = MONTH, evidence_blob: bytes | None = None) -> dict:
    return {
        "window": window,
        "evidence_window": evidence_window,
        "settings": build_defaults({}).values,
        "framework_regex": ".*",
        "evidence_blob": evidence_blob,
    }


def test_a_first_read_returns_the_evidence(executed):
    inputs, evidence = worker.fetch_inputs(job(MONTH))
    assert {"cards", "values", "tool", "sess", "persons"} <= set(executed)
    assert "ev_persons" not in executed  # the money window is the evidence window: one people read
    assert "prev_persons" not in executed  # no previous period is read
    assert _unzip(evidence)["ev_persons"] == inputs["persons"]
    assert inputs["meta"]["evidence_reused"] is False


def test_another_period_reads_only_its_own_spend(executed):
    _inputs, evidence = worker.fetch_inputs(job(MONTH))
    executed.clear()
    inputs, again = worker.fetch_inputs(job(WEEK, evidence_blob=evidence))
    assert not EVIDENCE_SPECS & set(executed)
    assert set(executed) == {
        "totals",
        "persons",
        "shared_cli",
        "shared_cli_fb",
        "web_proj",
        "web_fb",
        "web_funding",
        "web_people",
    }
    assert again is None
    assert inputs["ev_persons"] == _unzip(evidence)["ev_persons"]
    assert inputs["meta"]["evidence_reused"] is True


def test_the_evidence_period_itself_reuses_the_people_read(executed):
    _inputs, evidence = worker.fetch_inputs(job(WEEK))
    assert "ev_persons" in executed  # a 7-day first read needs the 30-day people too
    executed.clear()
    inputs, _ = worker.fetch_inputs(job(MONTH, evidence_blob=evidence))
    assert "persons" not in executed  # the 30-day people come from the 7-day read's evidence
    assert inputs["persons"] == _unzip(evidence)["ev_persons"]


def test_a_later_computation_in_the_hour_reuses_the_evidence(executed):
    _inputs, evidence = worker.fetch_inputs(job(EARLY_MONTH, EARLY_MONTH))
    executed.clear()
    inputs, again = worker.fetch_inputs(job(MONTH, MONTH, evidence))
    assert not EVIDENCE_SPECS & set(executed)
    # The evidence ends at the start of the hour, this period 45 minutes later: its people are read for it.
    assert "persons" in executed
    assert again is None
    assert inputs["evidence_window"] == inputs["meta"]["evidence_window"] == EARLY_MONTH
    assert inputs["ev_persons"] == _unzip(evidence)["ev_persons"]
    assert inputs["meta"]["evidence_reused"] is True


# ---------------------------------------------------------------------------------------------------------------------
# Responses without document counts
# ---------------------------------------------------------------------------------------------------------------------
def _answer(aggs: dict) -> dict:
    """Synthetic aggregation results for a request: two buckets per terms level, and the document counts
    Elasticsearch puts on every bucket, filter and terms aggregation."""
    out: dict = {}
    for name, spec in aggs.items():
        sub = spec.get("aggs") or {}
        if "terms" in spec:
            terms = spec["terms"]
            include = terms.get("include")
            part = include.get("partition", 0) if isinstance(include, dict) else 0
            buckets = [{"key": f"{terms['field']}-{part}-{i}", "doc_count": 3 + i, **_answer(sub)} for i in range(2)]
            out[name] = {"doc_count_error_upper_bound": 0, "sum_other_doc_count": 0, "buckets": buckets}
        elif "filter" in spec:
            out[name] = {"doc_count": 2, **_answer(sub)}
        elif "filters" in spec:
            out[name] = {"buckets": {key: {"doc_count": 2, **_answer(sub)} for key in spec["filters"]["filters"]}}
        elif "sum" in spec or "cardinality" in spec:
            out[name] = {"value": 1.5}
        elif "max" in spec or "min" in spec:
            out[name] = {"value": 1.7e12, "value_as_string": "2026-10-10T10:00:00.000Z"}
        else:
            raise AssertionError(f"no synthetic answer for the aggregation {name}: {sorted(spec)}")
    return out


def _without(node, names: set[str]):
    if isinstance(node, dict):
        return {key: _without(value, names) for key, value in node.items() if key not in names}
    if isinstance(node, list):
        return [_without(x, names) for x in node]
    return node


class SyntheticES:
    """Answers every search with synthetic aggregations. With ``filtered``, it applies the exclusions of
    ``filter_path`` as Elasticsearch does."""

    def __init__(self, filtered: bool):
        self.filtered = filtered
        self.filter_paths: list[list[str]] = []

    def search(self, index, body, request_timeout, filter_path):
        self.filter_paths.append(list(filter_path))
        data = {"took": 1, "timed_out": False, "_shards": {"failed": 0}, "aggregations": _answer(body["aggs"])}
        if self.filtered:
            data = _without(data, {p.removeprefix("-**.") for p in filter_path if p.startswith("-**.")})
        return data


def test_responses_leave_out_document_counts_the_analysis_never_reads(monkeypatch, no_db):
    results = []
    for filtered in (False, True):
        client = SyntheticES(filtered)
        monkeypatch.setattr(worker, "_es_client", lambda concurrency, c=client: c)
        inputs, _evidence = worker.fetch_inputs(job(WEEK))
        results.append({key: value for key, value in inputs.items() if key != "meta"})
        assert all({"-**.doc_count", "-**.doc_count_error_upper_bound"} <= set(p) for p in client.filter_paths)
    assert results[0]["persons"] and results[0]["values"] and results[0]["tool"]  # the synthetic answers parsed
    assert results[0] == results[1]


def test_web_funding_is_read_for_the_money_window(monkeypatch, no_db):
    client = SyntheticES(filtered=True)
    bodies: list[dict] = []
    search = client.search

    def record(index, body, request_timeout, filter_path):
        bodies.append(body)
        return search(index, body, request_timeout, filter_path)

    monkeypatch.setattr(client, "search", record)
    monkeypatch.setattr(worker, "_es_client", lambda concurrency: client)
    inputs, _evidence = worker.fetch_inputs(job(WEEK))
    body = next(b for b in bodies if "fp" in b["aggs"] and "fb" not in b["aggs"])
    ranges = body["aggs"]["fg"]["aggs"]["g"]["filters"]["filters"]["g0"]["bool"]["filter"][2]["bool"]["should"]
    # u2's allocation starts on 10 October, inside the week: only that part of its spend is charged back.
    assert ranges == [{"range": {"@timestamp": {"gte": 1791590400000, "lt": 1791902700000, "format": "epoch_millis"}}}]
    assert inputs["web_funding"]  # the synthetic answer parsed
    assert inputs["meta"]["web_funding"] == {"projects": 1, "members_whole_range": 1, "members_part_of_range": 1}
