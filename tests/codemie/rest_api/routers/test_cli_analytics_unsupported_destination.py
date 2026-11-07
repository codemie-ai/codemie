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

"""A new-contract destination whose canonical name cannot be echoed as a header (latin-1) is held before any write
(503 `destination_unsupported`), through N1 (ConsentGate) and N0 (EvidenceGate: derived, bound and legacy anchor)."""

# ruff: noqa: F811  (the fixtures are imported from the destination tests)

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.responses import Response

from codemie.rest_api.security.authentication import authenticate

from codemie.repository.cli_analytics.postgres.session_binding import SessionBinding
from codemie.service.analytics.cli_analytics_project_gate import _check_echoable
from tests.codemie.rest_api.routers.test_cli_analytics_consent_routing import (  # noqa: F401
    BOUND_PROJECT,
    CONSENT,
    CONTRACT,
    DESTINATION,
    ECHO,
    Env,
    env,
)
from tests.codemie.rest_api.routers.test_cli_analytics_evidence_routing import _evidence_headers
from tests.codemie.service.analytics.test_cli_analytics_destination import app, world  # noqa: F401

CYRILLIC = "Проект"
LATIN1 = "Café"
HELD = {"reason": "destination_unsupported", "disposition": "hold"}
N0 = {CONTRACT: "1", "X-CodeMie-Project": "Linked", "X-CodeMie-Project-Source": "linked_folder"}


def _rename(env: Env, name: str) -> None:
    """The project the header "Linked" names has this canonical name, which the fixture user may access."""
    user = env.client.app.dependency_overrides[authenticate](SimpleNamespace(state=SimpleNamespace()))
    user.project_names.append(name)
    env.world.projects["linked"] = env.world.projects[name.lower()] = app(name)


def _held(response, env: Env) -> None:
    assert response.status_code == 503 and response.json() == HELD
    assert CYRILLIC not in response.text
    assert not any(h in response.headers for h in ECHO)
    assert env.engine.conn.sql("INSERT INTO") == []  # no session binding, no rows


@pytest.mark.parametrize("route", ["logs", "metrics"])
@pytest.mark.parametrize("gate", ["n1", "n0"])
def test_an_unsupported_new_destination_is_held_before_any_write(env, route, gate):
    _rename(env, CYRILLIC)
    headers = {**_evidence_headers(), **(CONSENT if gate == "n1" else N0)}

    _held(env.post(route, headers), env)


def test_an_already_bound_n0_unsupported_session_is_held_and_its_history_kept(env):
    # N1 cannot reach this: its header must equal the bound name, so a non-latin-1 name is a conflict first.
    env.bound(project_name=CYRILLIC, work_consent=None)
    _rename(env, CYRILLIC)
    before = list(env.engine.conn.bindings)

    _held(env.post("logs", {**_evidence_headers(), **N0}), env)
    assert env.engine.conn.bindings == before


def test_an_n0_legacy_anchor_with_an_unsupported_name_is_held(env):
    _rename(env, CYRILLIC)
    env.bound(owner_email=None, project_name=None, contract_version=None, work_consent=None, legacy_project=CYRILLIC)

    _held(env.post("logs", {**_evidence_headers(), CONTRACT: "1"}), env)


@pytest.mark.parametrize("gate", ["n1", "n0"])
def test_an_ascii_destination_is_stored_and_echoed(env, gate):
    headers = {**_evidence_headers(), **(CONSENT if gate == "n1" else N0)}

    response = env.post("logs", headers)

    assert response.status_code == 200
    assert (response.headers[DESTINATION], response.headers[BOUND_PROJECT]) == ("project", "Linked")
    assert len(env.engine.conn.sql("INSERT INTO session_dims")) == 1


def test_a_latin1_name_is_representable_and_really_encodes_as_a_response_header():
    binding = SessionBinding(LATIN1, "corporate", "linked_folder", contract_version="1")

    assert _check_echoable(binding) is binding
    assert Response(headers={BOUND_PROJECT: LATIN1}).headers[BOUND_PROJECT.lower()] == LATIN1  # no UnicodeEncodeError


@pytest.mark.parametrize("name", ["Bad\r\nName", "Bad\x7fName", "Bad\x01Name", "Bad\x85Name"])
def test_a_name_with_control_characters_is_held_though_it_is_latin1(env, name):
    name.encode("latin-1")  # representable, but not in the CLI's printable header-value ranges
    _rename(env, name)

    _held(env.post("logs", {**_evidence_headers(), **N0}), env)
