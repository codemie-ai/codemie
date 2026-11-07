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

"""Funding first-wins (EPMCDME-16849 item 4): the class x routed x claim table through the real route and ingestor."""

# ruff: noqa: F811  (the `world` fixture is imported from the destination tests)

from __future__ import annotations

import pytest

from codemie.repository.cli_analytics.postgres import ingestor as ingestor_module
from tests.codemie.rest_api.routers.test_cli_analytics_consent_routing import CONSENT, Env, env  # noqa: F401
from tests.codemie.rest_api.routers.test_cli_analytics_evidence_routing import SID, _evidence_headers, _payload
from tests.codemie.service.analytics.test_cli_analytics_destination import world  # noqa: F401

FUNDING = "X-CodeMie-Funding-Route"
ROUTED = {"X-CodeMie-Routed-Via-CodeMie": "true"}


def _headers(claim=None, routed=False, **extra):
    headers = {**_evidence_headers(), **CONSENT, **(ROUTED if routed else {}), **extra}
    if claim is not None:
        headers[FUNDING] = claim
    return headers


def _stored(env: Env) -> str | None:
    ((*_, funding),) = env.engine.conn.sql("INSERT INTO session_dims")
    return funding


@pytest.mark.parametrize(
    ("routed", "claim", "expected"),
    [
        (True, "api_based", "api_based"),
        (True, "subscription", "unknown"),  # a codemie-routed session cannot be a subscription: contradiction
        (True, "unknown", "unknown"),
        (True, "API_BASED ", "api_based"),
        (False, "api_based", "api_based"),
        (False, "subscription", "subscription"),
        (False, "unknown", "unknown"),
        (False, "bogus", "unknown"),
        (False, "", "unknown"),
        (False, None, "unknown"),
        (True, None, "unknown"),
    ],
)
def test_the_first_binding_stores_the_funding_the_table_allows(env, routed, claim, expected):
    response = env.post("logs", _headers(claim, routed))

    assert response.status_code == 200
    assert _stored(env) == expected


def test_a_repeated_funding_header_is_unknown(env):
    path, body, content_type = _payload("logs", SID)
    headers = [("Content-Type", content_type), *_headers().items(), (FUNDING, "subscription"), (FUNDING, "api_based")]

    response = env.client.post(path, content=body, headers=headers)

    assert response.status_code == 200
    assert _stored(env) == "unknown"


def test_a_legacy_request_leaves_funding_null(env):
    response = env.post("logs", _evidence_headers())

    assert response.status_code == 200
    assert _stored(env) is None


def test_a_later_differing_claim_changes_nothing_and_counts_once(env):
    env.bound(funding_route="api_based")
    before = ingestor_module.FUNDING_EVENTS["funding_claim_differs"]

    response = env.post("logs", _headers("subscription"))

    assert response.status_code == 200
    assert env.engine.conn.sql("INSERT INTO session_dims") == []  # a bound row is never updated
    assert ingestor_module.FUNDING_EVENTS["funding_claim_differs"] == before + 1


def test_a_later_equal_claim_is_not_counted_and_funding_is_not_compared(env):
    env.bound(funding_route="subscription")
    before = ingestor_module.FUNDING_EVENTS["funding_claim_differs"]

    same = env.post("logs", _headers("subscription"))

    assert same.status_code == 200 and ingestor_module.FUNDING_EVENTS["funding_claim_differs"] == before
    other = env.post("logs", _headers("api_based"))
    assert other.status_code == 200  # accepted, not a contract conflict
    assert ingestor_module.FUNDING_EVENTS["funding_claim_differs"] == before + 1


# N0: contract 1 without work-consent goes through the same route, ingestor and EvidenceGate (`note_funding` wiring)


def _n0_headers(claim=None, routed=False):
    headers = {**_evidence_headers(), "X-CodeMie-Publication-Contract": "1", **(ROUTED if routed else {})}
    if claim is not None:
        headers[FUNDING] = claim
    return headers


def _n0_stored(env: Env) -> tuple[str | None, str | None]:
    ((*_, version, consent, funding),) = env.engine.conn.sql("INSERT INTO session_dims")
    assert consent is None
    return version, funding


@pytest.mark.parametrize(
    ("routed", "claim", "expected"),
    [(False, "subscription", "subscription"), (False, None, "unknown"), (True, "subscription", "unknown")],
)
def test_n0_first_binding_stores_funding(env, routed, claim, expected):
    response = env.post("logs", _n0_headers(claim, routed))

    assert response.status_code == 200
    assert _n0_stored(env) == ("1", expected)


def test_n0_later_differing_claim_is_counted_once_and_the_binding_is_immutable(env):
    env.bound(work_consent=None, funding_route="api_based")
    before = ingestor_module.FUNDING_EVENTS["funding_claim_differs"]

    response = env.post("logs", _n0_headers("subscription"))

    assert response.status_code == 200
    assert env.engine.conn.sql("INSERT INTO session_dims") == []  # first-wins: the bound row is never rewritten
    assert ingestor_module.FUNDING_EVENTS["funding_claim_differs"] == before + 1


def test_n0_same_claim_is_not_counted_and_a_later_differing_one_is(env):
    env.bound(work_consent=None, funding_route="subscription")
    before = ingestor_module.FUNDING_EVENTS["funding_claim_differs"]

    assert env.post("logs", _n0_headers("subscription")).status_code == 200
    assert ingestor_module.FUNDING_EVENTS["funding_claim_differs"] == before
    assert env.post("logs", _n0_headers("api_based")).status_code == 200
    assert ingestor_module.FUNDING_EVENTS["funding_claim_differs"] == before + 1
    assert env.engine.conn.sql("INSERT INTO session_dims") == []
