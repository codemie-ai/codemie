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

"""N1 consent requests on the four ingest routes through the real ConsentGate and ingestor (EPMCDME-16849 item 3).

Only the database is faked: the recording connection (ingestor) and the World fixture (project lookups)."""

# ruff: noqa: F811  (the `world` fixture is imported from the destination tests)

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as ingest_router
import codemie.rest_api.routers.cli_analytics_ingest_route as route_module
from codemie.configs import config
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.rest_api.security.authentication import authenticate
from tests.codemie.repository.cli_analytics.postgres.test_ingestor import FakeEngine
from tests.codemie.rest_api.routers.test_cli_analytics_evidence_routing import (
    ROUTES,
    SID,
    _evidence_headers,
    _payload,
    _reason,
)
from tests.codemie.service.analytics.test_cli_analytics_destination import make_user, world  # noqa: F401

CONTRACT = "X-CodeMie-Publication-Contract"
DESTINATION = "X-CodeMie-Destination"
BOUND_PROJECT = "X-CodeMie-Bound-Project"
ECHO = (CONTRACT, DESTINATION, BOUND_PROJECT)
CONSENT = {CONTRACT: "1", "X-CodeMie-Work-Consent": "explicit-folder", "X-CodeMie-Project": "lINKED"}


@dataclass
class Env:
    client: TestClient
    engine: FakeEngine
    world: object

    def post(self, route: str, headers: dict | None = None):
        path, body, content_type = _payload(route, SID)
        return self.client.post(path, content=body, headers={"Content-Type": content_type, **(headers or {})})

    def bound(self, **row):
        self.engine.conn.bindings = [
            {
                "session_id": SID,
                "owner_email": "u@example.com",
                "project_name": "Linked",
                "subscription_type": "corporate",
                "project_origin": "linked_folder",
                "route_note": "",
                "legacy_project": None,
                "contract_version": "1",
                "work_consent": "explicit-folder",
                **row,
            }
        ]


@pytest.fixture
def env(world):
    engine = FakeEngine()
    storage = CliAnalyticsStorage(reader=None, ingestor=PostgresTelemetryIngestor(engine))  # type: ignore[arg-type]
    user = make_user()

    def authenticate_as(request: Request):
        request.state.user = user
        return user

    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = authenticate_as
    customer_config = MagicMock()
    customer_config.is_feature_enabled.return_value = True
    customer_config.get_feature_setting.side_effect = lambda _f, key, default=None: (
        "Acme" if key == "corporateOrganizations" else default
    )
    with (
        patch.object(ingest_router, "get_cli_analytics_storage", return_value=storage),
        patch.object(ingest_router, "customer_config", customer_config),
        patch.object(route_module, "customer_config", customer_config),
        patch.object(config, "CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED", True),
        patch.object(config, "ENABLE_USER_MANAGEMENT", True),
    ):
        yield Env(TestClient(app), engine, world)


def _headers(**extra):
    return {**_evidence_headers(), **CONSENT, **extra}


@pytest.mark.parametrize("route", ROUTES)
def test_an_accepted_consent_request_binds_the_canonical_project_and_echoes_it(env, route):
    response = env.post(route, _headers())

    assert response.status_code == 200
    assert (response.headers[CONTRACT], response.headers[DESTINATION], response.headers[BOUND_PROJECT]) == (
        "1",
        "project",
        "Linked",
    )
    ((sessions, owner, kind, origin, _note, projects, version, consent, _funding),) = env.engine.conn.sql(
        "INSERT INTO session_dims"
    )
    assert (sessions, kind, origin, projects, version, consent) == (
        [SID],
        "corporate",
        "linked_folder",
        ["Linked"],
        "1",
        "explicit-folder",
    )


@pytest.mark.parametrize("route", ROUTES)
def test_a_personal_class_session_with_consent_is_stored_under_the_project_not_privately(env, route):
    headers = _headers(**{"X-CodeMie-Organization": "Elsewhere", "X-CodeMie-Subscription-Type": "personal"})

    response = env.post(route, headers)

    assert response.status_code == 200 and response.headers[DESTINATION] == "project"
    ((_s, _o, kind, origin, _n, projects, *_),) = env.engine.conn.sql("INSERT INTO session_dims")
    assert (kind, origin, projects) == ("personal", "linked_folder", ["Linked"])


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("change", "status", "reason", "disposition"),
    [
        ("missing", 503, "project_unavailable", "hold"),
        ("disabled", 503, "project_analytics_not_enabled", "hold"),
        ("personal", 422, "consent_personal_project", "final"),
    ],
)
def test_a_held_or_rejected_request_has_the_body_and_no_echo_and_stores_nothing(
    env, route, change, status, reason, disposition
):
    from tests.codemie.service.analytics.test_cli_analytics_destination import PERSONAL, app

    if change == "missing":
        env.world.remove("Linked")
    elif change == "disabled":
        env.world.projects["linked"].cli_analytics_enabled = False
    else:
        env.world.projects["linked"] = app("Linked", project_type=PERSONAL, created_by="u-1")

    response = env.post(route, _headers())

    assert response.status_code == status
    assert response.json() == {"reason": reason, "disposition": disposition}
    assert not any(h in response.headers for h in ECHO)
    assert env.engine.conn.sql("INSERT INTO") == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("stored", "header", "reason"),
    [
        ({"work_consent": None}, "Linked", "session_contract_conflict"),
        ({"contract_version": None, "work_consent": None}, "Linked", "session_contract_conflict"),
        ({}, "Other", "session_project_conflict"),
    ],
)
def test_a_conflict_discard_is_a_200_without_echo_and_writes_nothing(env, route, stored, header, reason):
    env.bound(**stored)

    response = env.post(route, _headers(**{"X-CodeMie-Project": header}))

    assert _reason(response, route) == reason
    assert not any(h in response.headers for h in ECHO)
    assert env.engine.conn.sql("INSERT INTO") == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_bound_destination_that_went_away_is_a_final_discard_without_echo(env, route):
    env.bound()
    env.world.remove("Linked")

    response = env.post(route, _headers())

    assert _reason(response, route) == "session_project_unavailable"
    assert not any(h in response.headers for h in ECHO)


@pytest.mark.parametrize("route", ROUTES)
def test_a_bound_consent_session_is_accepted_without_rebinding_and_echoed(env, route):
    env.bound()

    response = env.post(route, _headers())

    assert response.status_code == 200 and response.headers[BOUND_PROJECT] == "Linked"
    assert env.engine.conn.sql("INSERT INTO session_dims") == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_contract_request_without_consent_is_echoed_with_its_private_destination(env, route):
    headers = _evidence_headers(**{"X-CodeMie-Organization": "Elsewhere", "X-CodeMie-Subscription-Type": "personal"})

    response = env.post(route, {**headers, CONTRACT: "1"})

    assert response.status_code == 200
    assert (response.headers[CONTRACT], response.headers[DESTINATION]) == ("1", "personal")
    assert BOUND_PROJECT not in response.headers


@pytest.mark.parametrize("route", ROUTES)
def test_a_legacy_evidence_request_has_no_echo_headers(env, route):
    response = env.post(route, _evidence_headers())

    assert response.status_code == 200
    assert not any(h in response.headers for h in ECHO)
