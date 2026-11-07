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

"""Publication-contract parser and pre-body check on the four ingest routes (EPMCDME-16849)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

import codemie.rest_api.routers.cli_analytics as router_module
import codemie.rest_api.routers.cli_analytics_ingest_route as route_module
from codemie.rest_api.routers.cli_analytics_ingest_route import (
    PublicationContract,
    PublicationContractRejected,
    parse_publication_contract,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

C = "X-CodeMie-Publication-Contract"
W = "X-CodeMie-Work-Consent"
F = "X-CodeMie-Funding-Route"
P = "X-CodeMie-Project"
BASE = "/v1/analytics/cli-analytics"
ROUTES = ["logs", "metrics", "traces", "event-hooks"]


def _h(*pairs: tuple[str, str]) -> Headers:
    return Headers(raw=[(k.lower().encode(), v.encode()) for k, v in pairs])


def _parse(*pairs):
    try:
        return parse_publication_contract(_h(*pairs))
    except PublicationContractRejected as exc:
        return (exc.status_code, exc.reason, exc.disposition)


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([], None),
        ([(P, "x")], None),
        ([(C, "1")], PublicationContract("1", None, None)),
        ([(C, " 1 ")], PublicationContract("1", None, None)),
        ([(C, "1"), (F, "subscription")], PublicationContract("1", None, "subscription")),
        ([(C, "1"), (F, "bogus")], PublicationContract("1", None, None)),
        ([(C, "1"), (W, "explicit-folder"), (P, "proj")], PublicationContract("1", "explicit-folder", None)),
        ([(W, "explicit-folder"), (P, "proj")], (422, "publication_contract_missing", "final")),
        ([(F, "api_based")], (422, "publication_contract_missing", "final")),
        ([(C, "")], (422, "publication_contract_unsupported", "final")),  # present and invalid
        ([(C, "2")], (422, "publication_contract_unsupported", "final")),
        ([(C, "1"), (C, "1")], (422, "publication_contract_unsupported", "final")),
        ([(C, "1"), (W, "yes"), (P, "proj")], (422, "consent_invalid", "final")),
        ([(C, "1"), (W, "")], (422, "consent_invalid", "final")),
        ([(C, "1"), (W, "explicit-folder"), (W, "explicit-folder"), (P, "p")], (422, "consent_invalid", "final")),
        ([(C, "1"), (W, "explicit-folder")], (422, "consent_without_project", "final")),
        ([(C, "1"), (W, "explicit-folder"), (P, "  ")], (422, "consent_without_project", "final")),
    ],
)
def test_parser(pairs, expected):
    assert _parse(*pairs) == expected


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class _Ingestor:
    def __init__(self) -> None:
        self.calls = 0

    async def ingest_otlp(self, *a, **k):
        self.calls += 1
        return MagicMock(body=b"", media_type="application/json")

    async def ingest_hook_events(self, *a, **k):
        self.calls += 1
        return MagicMock(body=b"{}", media_type="application/json")


@pytest.fixture()
def env():
    user = User(id="u", email="dev@example.com", user_type="regular")

    def _auth(request: Request) -> User:
        request.state.user = user
        return user

    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = _auth
    ingestor = _Ingestor()
    storage = MagicMock(ingestor=ingestor)
    cfg = MagicMock(ANALYTICS_INGEST_MAX_BODY_BYTES=2, CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED=True)
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
        patch.object(router_module, "config", cfg),
        patch.object(route_module, "config", cfg),
        patch.object(route_module, "corporate_organizations", return_value=["Acme"]),
    ):
        yield TestClient(app), ingestor, user, cfg


def _post(client, route, headers, body=b"x" * 10):
    ct = "application/x-ndjson" if route == "event-hooks" else "application/x-protobuf"
    return client.post(f"{BASE}/{route}", content=body, headers={"content-type": ct, **headers})


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({W: "explicit-folder", P: "p"}, "publication_contract_missing"),
        ({C: "9"}, "publication_contract_unsupported"),
        ({C: "1", W: "nope"}, "consent_invalid"),
        ({C: "1", W: "explicit-folder"}, "consent_without_project"),
    ],
)
def test_invalid_contract_is_422_before_body_and_size_checks(env, route, headers, reason):
    client, ingestor, _, _ = env
    with patch.object(Request, "body", side_effect=AssertionError("body read")):
        resp = _post(client, route, headers)  # body 10 bytes > cap 2: would be 413
    assert resp.status_code == 422
    assert resp.json() == {"reason": reason, "disposition": "final"}
    assert ingestor.calls == 0


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "headers",
    [
        {C: "1"},
        {C: "1", W: "explicit-folder", P: "p"},
        {C: "1", "X-CodeMie-Session-ID": "  "},
        {C: "1", "X-CodeMie-Organization": "Acme"},  # evidence-like headers but no session
    ],
)
def test_contract_without_session_is_422_never_legacy(env, route, headers):
    client, ingestor, _, cfg = env
    cfg.ANALYTICS_INGEST_MAX_BODY_BYTES = 1000
    with patch.object(Request, "body", side_effect=AssertionError("body read")):
        resp = _post(client, route, headers, body=b'{"a":1}\n')
    assert resp.status_code == 422
    assert resp.json() == {"reason": "publication_contract_requires_session", "disposition": "final"}
    assert "X-CodeMie-Publication-Contract" not in resp.headers
    assert ingestor.calls == 0


@pytest.mark.parametrize("route", ROUTES)
def test_wrong_content_type_still_422_first(env, route):
    client, _, _, _ = env
    resp = client.post(f"{BASE}/{route}", content=b"x", headers={"content-type": "text/plain", C: "7"})
    assert resp.status_code == 422


@pytest.mark.parametrize("route", ROUTES)
def test_switch_off_holds_503(env, route):
    client, ingestor, _, cfg = env
    cfg.CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED = False
    with patch.object(Request, "body", side_effect=AssertionError("body read")):
        resp = _post(client, route, {C: "1"})
    assert resp.status_code == 503
    assert resp.json() == {"reason": "publication_contract_unavailable", "disposition": "hold"}
    assert ingestor.calls == 0


@pytest.mark.parametrize("route", ROUTES)
def test_switch_off_invalid_contract_still_422(env, route):
    client, _, _, cfg = env
    cfg.CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED = False
    assert _post(client, route, {C: "9"}).status_code == 422


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("enabled", [True, False])
def test_legacy_unchanged(env, route, enabled):
    client, ingestor, _, cfg = env
    cfg.CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED = enabled
    assert _post(client, route, {}).status_code == 413  # existing cap guard still answers
    cfg.ANALYTICS_INGEST_MAX_BODY_BYTES = 1000
    assert _post(client, route, {}, body=b'{"a":1}\n').status_code == 200
    assert ingestor.calls == 1


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("user_type", "org_headers", "held"),
    [
        ("external", {}, True),  # unknown
        ("service_account", {"X-CodeMie-Organization": "Other"}, True),  # personal
        ("external", {"X-CodeMie-Organization": "Acme"}, False),  # corporate
        ("external", {"X-CodeMie-Routed-Via-CodeMie": "true"}, False),  # codemie
        ("regular", {}, False),
    ],
)
def test_n0_excluded_user_personal_or_unknown_held(env, route, user_type, org_headers, held):
    client, _, user, cfg = env
    user.user_type = user_type
    cfg.ANALYTICS_INGEST_MAX_BODY_BYTES = 1000
    headers = {C: "1", "X-CodeMie-Session-ID": "s", **org_headers}
    if held:
        with patch.object(Request, "body", side_effect=AssertionError("body read")):
            resp = _post(client, route, headers, body=b'{"a":1}\n')
    else:
        resp = _post(client, route, headers, body=b'{"a":1}\n')
    if held:
        assert resp.status_code == 503
        assert resp.json() == {"reason": "personal_storage_unsupported", "disposition": "hold"}
    else:
        assert resp.status_code != 503 or resp.json().get("reason") != "personal_storage_unsupported"


def test_consent_is_not_held_for_excluded_user(env):
    client, _, user, cfg = env
    user.user_type = "external"
    cfg.ANALYTICS_INGEST_MAX_BODY_BYTES = 1000
    resp = _post(client, "logs", {C: "1", W: "explicit-folder", P: "p", "X-CodeMie-Session-ID": "s"}, body=b"\x00")
    assert resp.status_code != 503 or resp.json().get("reason") != "personal_storage_unsupported"
