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

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.skill_event import SkillEvent, SkillEventRequest
from codemie.rest_api.routers.skill_events import (
    get_all_skills_stats,
    get_skill_aggregated_stats,
    get_skill_event_log,
    record_skill_event,
    router,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


# ---------------------------------------------------------------------------
# HTTP-level test client
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(router)


@_test_app.exception_handler(ExtendedHTTPException)
async def _ext_http_exc_handler(request: Request, exc: ExtendedHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.code,
        content={"error": {"message": exc.message, "details": exc.details, "help": exc.help}},
    )


_http_client = TestClient(_test_app, raise_server_exceptions=False)

_AUTH_USER = User(id="http-user-1", username="http-user", email="http@example.com")
_AUTH_HEADERS = {"user-id": "http-user-1"}


@pytest.fixture(autouse=False)
def _override_auth():
    _test_app.dependency_overrides[authenticate] = lambda: _AUTH_USER
    yield
    _test_app.dependency_overrides.clear()


def _user() -> User:
    return User(id="user-1", username="user", email="user@example.com")


def _request() -> SkillEventRequest:
    return SkillEventRequest(
        session_id="session-1",
        command="add",
        status="completed",
        skill_slug="code-review",
    )


def test_record_skill_event_returns_persisted_event_id() -> None:
    event = SkillEvent(
        id="event-1",
        user_id="user-1",
        session_id="session-1",
        command="add",
        status="completed",
    )

    with patch("codemie.rest_api.routers.skill_events.skill_event_service.record", return_value=event) as record:
        response = record_skill_event(
            request=_request(),
            user=_user(),
            x_codemie_cli="codemie-cli/1.0.0",
            x_codemie_client="cli",
        )

    assert response.id == "event-1"
    assert response.success is True
    assert response.message == "Skill event 'add/completed' recorded"
    record.assert_called_once()
    assert record.call_args.kwargs["x_codemie_cli"] == "codemie-cli/1.0.0"
    assert record.call_args.kwargs["x_codemie_client"] == "cli"


def test_record_skill_event_preserves_service_http_errors() -> None:
    original = ExtendedHTTPException(code=409, message="duplicate event")

    with patch("codemie.rest_api.routers.skill_events.skill_event_service.record", side_effect=original):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            record_skill_event(request=_request(), user=_user())

    assert exc_info.value is original


def test_record_skill_event_wraps_unexpected_service_errors() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.record",
        side_effect=RuntimeError("database unavailable"),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            record_skill_event(request=_request(), user=_user())

    assert exc_info.value.code == 500
    assert exc_info.value.message == "Failed to record skill event"
    assert "command=add status=completed" in exc_info.value.details


def test_record_skill_event_header_overrides_decoded() -> None:
    """A percent-encoded X-CodeMie-Repository/-Branch header decodes and overrides the body;
    X-CodeMie-Project is passed to the service exactly as sent."""
    event = SkillEvent(id="event-1", user_id="user-1", session_id="session-1", command="add", status="completed")
    request = SkillEventRequest(
        session_id="session-1",
        command="add",
        status="completed",
        skill_slug="code-review",
        repository="body/repo",
        branch="body-branch",
        project="body-project",
    )

    with patch("codemie.rest_api.routers.skill_events.skill_event_service.record", return_value=event) as record:
        record_skill_event(
            request=request,
            user=_user(),
            x_codemie_repository="clients/my%20repo",
            x_codemie_branch="feature/%D1%82%D0%B5%D1%81%D1%82",
            x_codemie_project="my%20project",
        )

    assert record.call_args.kwargs["x_codemie_repository"] == "clients/my repo"
    assert record.call_args.kwargs["x_codemie_branch"] == "feature/тест"
    assert record.call_args.kwargs["x_codemie_project"] == "my%20project"


def test_record_skill_event_whitespace_only_header_falls_back_to_body() -> None:
    """CR-002: a repository/branch header that decodes to whitespace-only must not override a
    legitimate body-supplied value. Project is not decoded, so "%20" reaches the service as sent."""
    event = SkillEvent(id="event-1", user_id="user-1", session_id="session-1", command="add", status="completed")
    request = SkillEventRequest(
        session_id="session-1",
        command="add",
        status="completed",
        skill_slug="code-review",
        repository="body/repo",
        branch="body-branch",
        project="body-project",
    )

    with patch("codemie.rest_api.routers.skill_events.skill_event_service.record", return_value=event) as record:
        record_skill_event(
            request=request,
            user=_user(),
            x_codemie_repository="%20",
            x_codemie_branch="%20",
            x_codemie_project="%20",
        )

    assert record.call_args.kwargs["x_codemie_repository"] is None
    assert record.call_args.kwargs["x_codemie_branch"] is None
    assert record.call_args.kwargs["x_codemie_project"] == "%20"


def test_record_skill_event_without_headers_uses_body_fields() -> None:
    """Missing attribution headers leave the body's repository/branch/project untouched."""
    event = SkillEvent(id="event-1", user_id="user-1", session_id="session-1", command="add", status="completed")
    request = _request()

    with patch("codemie.rest_api.routers.skill_events.skill_event_service.record", return_value=event) as record:
        record_skill_event(request=request, user=_user())

    assert record.call_args.kwargs.get("x_codemie_repository") is None
    assert record.call_args.kwargs.get("x_codemie_branch") is None
    assert record.call_args.kwargs.get("x_codemie_project") is None


# ---------------------------------------------------------------------------
# GET /v1/skills/events — raw chronological event log
# ---------------------------------------------------------------------------


def _skill_event(*, command: str = "add") -> SkillEvent:
    return SkillEvent(
        id="event-1",
        user_id="user-1",
        session_id="session-1",
        command=command,
        status="completed",
        skill_slug="code-review",
        source="marketplace",
        target_agents=["agent-a"],
        created_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
    )


def test_get_skill_event_log_returns_paginated_raw_events() -> None:
    event = _skill_event()

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([event], 1),
    ):
        response = get_skill_event_log(limit=100, offset=0, user=_user())

    assert response.pagination.total == 1
    assert response.pagination.per_page == 100
    assert response.pagination.page == 0
    assert len(response.data) == 1
    item = response.data[0]
    assert item.skill_slug == "code-review"
    assert item.command == "add"
    assert item.user_id == "user-1"
    assert item.source == "marketplace"
    assert item.target_agents == ["agent-a"]
    assert item.date == datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def test_get_skill_event_log_pagination_metadata() -> None:
    events = [_skill_event(command="add"), _skill_event(command="remove")]

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=(events, 50),
    ):
        response = get_skill_event_log(limit=10, offset=10, user=_user())

    assert response.pagination.total == 50
    assert response.pagination.per_page == 10
    assert response.pagination.page == 1
    assert response.pagination.pages == 5
    assert len(response.data) == 2


def test_get_skill_event_log_null_target_agents_normalised_to_empty_list() -> None:
    event = _skill_event()
    object.__setattr__(event, "target_agents", None)

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([event], 1),
    ):
        response = get_skill_event_log(limit=100, offset=0, user=_user())

    assert response.data[0].target_agents == []


# ---------------------------------------------------------------------------
# GET /v1/skills/events/stats — per-skill aggregated list
# ---------------------------------------------------------------------------

_STATS_ITEM = {
    "skill_slug": "code-review",
    "installs": 3,
    "removals": 1,
    "by_agent": {"agent-a": 3},
    "by_source": {"github.com/org": 3},
}


def test_get_all_skills_stats_happy_path_returns_paginated_aggregated_response() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=([_STATS_ITEM], 1),
    ):
        response = get_all_skills_stats(limit=100, offset=0, user=_user())

    assert response.pagination.total == 1
    assert response.pagination.per_page == 100
    assert response.pagination.page == 0
    assert len(response.data) == 1
    item = response.data[0]
    assert item.skill_slug == "code-review"
    assert item.installs == 3
    assert item.removals == 1
    assert item.by_agent == {"agent-a": 3}
    assert item.by_source == {"github.com/org": 3}


def test_get_all_skills_stats_pagination_metadata() -> None:
    rows = [
        {"skill_slug": "alpha", "installs": 2, "removals": 1, "by_agent": {}, "by_source": {}},
        {"skill_slug": "beta", "installs": 5, "removals": 0, "by_agent": {}, "by_source": {}},
    ]

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=(rows, 50),
    ):
        response = get_all_skills_stats(limit=10, offset=10, user=_user())

    assert response.pagination.total == 50
    assert response.pagination.per_page == 10
    assert response.pagination.page == 1
    assert response.pagination.pages == 5
    assert len(response.data) == 2


def test_get_all_skills_stats_empty_result() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=([], 0),
    ):
        response = get_all_skills_stats(limit=100, offset=0, user=_user())

    assert response.pagination.total == 0
    assert response.data == []


# ---------------------------------------------------------------------------
# GET /v1/skills/{slug}/stats
# ---------------------------------------------------------------------------


def test_get_skill_aggregated_stats_happy_path() -> None:
    stats = {"installs": 5, "removals": 2, "by_agent": {"agent-a": 3, "agent-b": 2}, "by_source": {"github.com/org": 5}}

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        return_value=stats,
    ):
        response = get_skill_aggregated_stats(skill_slug="code-review", user=_user())

    assert response.installs == 5
    assert response.removals == 2
    assert response.by_agent == {"agent-a": 3, "agent-b": 2}
    assert response.by_source == {"github.com/org": 5}


def test_get_skill_aggregated_stats_raises_404_when_skill_not_found() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        return_value=None,
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_skill_aggregated_stats(skill_slug="unknown-skill", user=_user())

    assert exc_info.value.code == 404
    assert exc_info.value.message == "Skill not found"


# ---------------------------------------------------------------------------
# HTTP routing tests (TestClient) — verify the actual registered URL paths
# ---------------------------------------------------------------------------


def test_http_get_events_stats_route_returns_200(_override_auth) -> None:
    """GET /v1/skills/events/stats must resolve to the aggregated-stats handler."""
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=([], 0),
    ):
        resp = _http_client.get("/v1/skills/events/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "pagination" in body


def test_http_get_events_stats_route_returns_401_without_auth() -> None:
    """GET /v1/skills/events/stats must reject unauthenticated requests."""
    resp = _http_client.get("/v1/skills/events/stats")
    assert resp.status_code == 401


def test_http_get_skill_aggregated_stats_route_returns_200(_override_auth) -> None:
    """GET /v1/skills/events/{slug}/stats must resolve to the aggregated-stats handler."""
    stats = {"installs": 3, "removals": 1, "by_agent": {"agent-a": 3}}
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        return_value=stats,
    ):
        resp = _http_client.get("/v1/skills/events/code-review/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["installs"] == 3
    assert body["removals"] == 1
    assert body["by_agent"] == {"agent-a": 3}


def test_http_get_skill_aggregated_stats_route_returns_404_for_unknown_slug(_override_auth) -> None:
    """GET /v1/skills/events/{slug}/stats must return 404 when the slug has no events."""
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        return_value=None,
    ):
        resp = _http_client.get("/v1/skills/events/nonexistent/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 404


def test_http_events_stats_route_not_shadowed_by_slug_route(_override_auth) -> None:
    """/v1/skills/events/stats must match the all-skills handler, not /{slug}/stats with slug='events'."""
    with (
        patch(
            "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
            return_value=([], 0),
        ) as mock_all,
        patch(
            "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        ) as mock_single,
    ):
        resp = _http_client.get("/v1/skills/events/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    mock_all.assert_called_once()
    mock_single.assert_not_called()


def test_http_get_events_stats_pagination_params_are_forwarded(_override_auth) -> None:
    """Query params limit and offset must be forwarded to the service."""
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=([], 0),
    ) as mock_get:
        _http_client.get("/v1/skills/events/stats?limit=5&offset=10", headers=_AUTH_HEADERS)

    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["limit"] == 5
    assert call_kwargs["offset"] == 10


def test_http_get_events_stats_returns_aggregated_shape(_override_auth) -> None:
    """Response items must contain skill_slug, installs, removals, by_agent, by_source — not raw event fields."""
    rows = [
        {
            "skill_slug": "my-skill",
            "installs": 4,
            "removals": 1,
            "by_agent": {"agent-x": 4},
            "by_source": {"github.com/org": 4},
        }
    ]
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=(rows, 1),
    ):
        resp = _http_client.get("/v1/skills/events/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["skill_slug"] == "my-skill"
    assert item["installs"] == 4
    assert item["removals"] == 1
    assert item["by_agent"] == {"agent-x": 4}
    assert item["by_source"] == {"github.com/org": 4}
    assert "command" not in item
    assert "date" not in item
    assert "user_id" not in item


# ---------------------------------------------------------------------------
# HTTP routing tests — GET /v1/skills/events (raw event log)
# ---------------------------------------------------------------------------


def test_http_get_events_log_route_returns_200(_override_auth) -> None:
    """GET /v1/skills/events must return paginated raw events."""
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([], 0),
    ):
        resp = _http_client.get("/v1/skills/events", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "pagination" in body


def test_http_get_events_log_route_returns_401_without_auth() -> None:
    """GET /v1/skills/events must reject unauthenticated requests."""
    resp = _http_client.get("/v1/skills/events")
    assert resp.status_code == 401


def test_http_get_events_log_returns_raw_event_shape(_override_auth) -> None:
    """Response items must contain raw event fields, not aggregated counts."""
    event = _skill_event()
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([event], 1),
    ):
        resp = _http_client.get("/v1/skills/events", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["skill_slug"] == "code-review"
    assert item["command"] == "add"
    assert item["user_id"] == "user-1"
    assert item["source"] == "marketplace"
    assert item["target_agents"] == ["agent-a"]
    assert "installs" not in item
    assert "removals" not in item


def test_http_get_events_log_pagination_params_forwarded(_override_auth) -> None:
    """limit and offset query params must reach the service."""
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([], 0),
    ) as mock_get:
        _http_client.get("/v1/skills/events?limit=5&offset=10", headers=_AUTH_HEADERS)

    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["limit"] == 5
    assert call_kwargs["offset"] == 10


def test_http_events_log_and_events_stats_routes_are_independent(_override_auth) -> None:
    """GET /v1/skills/events and GET /v1/skills/events/stats must route to different handlers."""
    with (
        patch(
            "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
            return_value=([], 0),
        ) as mock_log,
        patch(
            "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
            return_value=([], 0),
        ) as mock_stats,
    ):
        _http_client.get("/v1/skills/events", headers=_AUTH_HEADERS)
        _http_client.get("/v1/skills/events/stats", headers=_AUTH_HEADERS)

    mock_log.assert_called_once()
    mock_stats.assert_called_once()


# ---------------------------------------------------------------------------
# user_email field — GET /v1/skills/events
# ---------------------------------------------------------------------------


def test_get_skill_event_log_includes_user_email() -> None:
    event = _skill_event()
    object.__setattr__(event, "user_email", "user@example.com")

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([event], 1),
    ):
        response = get_skill_event_log(limit=100, offset=0, user=_user())

    assert response.data[0].user_email == "user@example.com"


def test_get_skill_event_log_user_email_is_none_when_absent() -> None:
    event = _skill_event()
    object.__setattr__(event, "user_email", None)

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([event], 1),
    ):
        response = get_skill_event_log(limit=100, offset=0, user=_user())

    assert response.data[0].user_email is None


def test_http_get_events_log_includes_user_email(_override_auth) -> None:
    event = _skill_event()
    object.__setattr__(event, "user_email", "user@example.com")
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([event], 1),
    ):
        resp = _http_client.get("/v1/skills/events", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["user_email"] == "user@example.com"


# ---------------------------------------------------------------------------
# Access control — GET /v1/skills/events (router forwards user to service)
# ---------------------------------------------------------------------------


def test_get_skill_event_log_forwards_admin_user_to_service() -> None:
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([], 0),
    ) as mock_get:
        get_skill_event_log(limit=100, offset=0, user=admin)

    assert mock_get.call_args.kwargs["user"] is admin


def test_get_skill_event_log_forwards_regular_user_to_service() -> None:
    user = _user()

    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        return_value=([], 0),
    ) as mock_get:
        get_skill_event_log(limit=100, offset=0, user=user)

    assert mock_get.call_args.kwargs["user"] is user


# ---------------------------------------------------------------------------
# by_source field — GET /v1/skills/events/stats
# ---------------------------------------------------------------------------


def test_get_all_skills_stats_includes_by_source() -> None:
    stats_item = {
        "skill_slug": "code-review",
        "installs": 3,
        "removals": 1,
        "by_agent": {"agent-a": 3},
        "by_source": {"github.com/org": 2, "unknown": 1},
    }
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=([stats_item], 1),
    ):
        response = get_all_skills_stats(limit=100, offset=0, user=_user())

    assert response.data[0].by_source == {"github.com/org": 2, "unknown": 1}


def test_http_get_events_stats_includes_by_source(_override_auth) -> None:
    rows = [
        {
            "skill_slug": "my-skill",
            "installs": 4,
            "removals": 1,
            "by_agent": {"agent-x": 4},
            "by_source": {"github.com/org": 4},
        }
    ]
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        return_value=(rows, 1),
    ):
        resp = _http_client.get("/v1/skills/events/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["by_source"] == {"github.com/org": 4}


# ---------------------------------------------------------------------------
# by_source field — GET /v1/skills/events/{slug}/stats
# ---------------------------------------------------------------------------


def test_get_skill_aggregated_stats_includes_by_source() -> None:
    stats = {
        "installs": 5,
        "removals": 2,
        "by_agent": {"agent-a": 3, "agent-b": 2},
        "by_source": {"github.com/org": 5},
    }
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        return_value=stats,
    ):
        response = get_skill_aggregated_stats(skill_slug="code-review", user=_user())

    assert response.by_source == {"github.com/org": 5}


def test_http_get_skill_aggregated_stats_includes_by_source(_override_auth) -> None:
    stats = {"installs": 3, "removals": 1, "by_agent": {"agent-a": 3}, "by_source": {"github.com/org": 3}}
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        return_value=stats,
    ):
        resp = _http_client.get("/v1/skills/events/code-review/stats", headers=_AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["by_source"] == {"github.com/org": 3}


# ---------------------------------------------------------------------------
# Exception-path coverage — wrapping and re-raise for error handlers
# ---------------------------------------------------------------------------


def test_get_skill_event_log_preserves_service_http_errors() -> None:
    original = ExtendedHTTPException(code=429, message="rate limited")
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        side_effect=original,
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_skill_event_log(limit=100, offset=0, user=_user())
    assert exc_info.value is original


def test_get_skill_event_log_wraps_unexpected_errors() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_event_log",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_skill_event_log(limit=100, offset=0, user=_user())
    assert exc_info.value.code == 500
    assert exc_info.value.message == "Failed to retrieve skill event log"


def test_get_all_skills_stats_preserves_service_http_errors() -> None:
    original = ExtendedHTTPException(code=503, message="service unavailable")
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        side_effect=original,
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_all_skills_stats(limit=100, offset=0, user=_user())
    assert exc_info.value is original


def test_get_all_skills_stats_wraps_unexpected_errors() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_all_skills_stats",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_all_skills_stats(limit=100, offset=0, user=_user())
    assert exc_info.value.code == 500
    assert exc_info.value.message == "Failed to retrieve skill stats"


def test_get_skill_aggregated_stats_wraps_unexpected_errors() -> None:
    with patch(
        "codemie.rest_api.routers.skill_events.skill_event_service.get_skill_stats",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_skill_aggregated_stats(skill_slug="code-review", user=_user())
    assert exc_info.value.code == 500
    assert exc_info.value.message == "Failed to retrieve skill stats"
