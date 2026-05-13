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
from unittest.mock import MagicMock, patch

from codemie.repository.skill_event_repository import SQLSkillEventRepository
from codemie.rest_api.models.skill_event import SkillEvent


def _repo() -> SQLSkillEventRepository:
    return SQLSkillEventRepository()


def _event(**kwargs) -> SkillEvent:
    defaults: dict = {"user_id": "user-1", "session_id": "session-1", "command": "add", "status": "completed"}
    defaults.update(kwargs)
    return SkillEvent(**defaults)


@patch("codemie.repository.skill_event_repository.Session")
def test_insert_persists_event_and_returns_it(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    event = _event()

    result = _repo().insert(event)

    mock_session.add.assert_called_once_with(event)
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(event)
    assert result is event


@patch("codemie.repository.skill_event_repository.Session")
def test_find_by_id_returns_event_when_found(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    event = _event()
    mock_session.get.return_value = event

    result = _repo().find_by_id("event-1")

    mock_session.get.assert_called_once_with(SkillEvent, "event-1")
    assert result is event


@patch("codemie.repository.skill_event_repository.Session")
def test_find_by_id_returns_none_when_not_found(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = None

    assert _repo().find_by_id("missing") is None


@patch("codemie.repository.skill_event_repository.Session")
def test_get_events_returns_rows_and_total_count(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    event = _event(skill_slug="code-review")
    count_result = MagicMock()
    count_result.one.return_value = 1
    data_result = MagicMock()
    data_result.all.return_value = [event]
    mock_session.exec.side_effect = [count_result, data_result]

    rows, total = _repo().get_events(user_id=None, from_dt=None, to_dt=None, limit=100, offset=0)

    assert total == 1
    assert rows == [event]


@patch("codemie.repository.skill_event_repository.Session")
def test_get_events_with_filters_returns_empty_when_no_match(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    count_result = MagicMock()
    count_result.one.return_value = 0
    data_result = MagicMock()
    data_result.all.return_value = []
    mock_session.exec.side_effect = [count_result, data_result]

    rows, total = _repo().get_events(
        user_id="user-1",
        from_dt=datetime(2026, 1, 1, tzinfo=UTC),
        to_dt=datetime(2026, 6, 1, tzinfo=UTC),
        limit=10,
        offset=5,
    )

    assert total == 0
    assert rows == []


@patch("codemie.repository.skill_event_repository.Session")
def test_get_skill_aggregated_stats_returns_none_for_unknown_skill(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    assert _repo().get_skill_aggregated_stats(skill_slug="nonexistent") is None


@patch("codemie.repository.skill_event_repository.Session")
def test_get_skill_aggregated_stats_returns_install_removal_breakdown(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [
        ("add", "codex", "github.com/org", 3),
        ("add", None, None, 1),
        ("remove", None, None, 2),
    ]

    result = _repo().get_skill_aggregated_stats(skill_slug="code-review")

    assert result is not None
    assert result["installs"] == 4
    assert result["removals"] == 2
    assert result["by_agent"]["codex"] == 3
    assert result["by_agent"]["unknown"] == 1
    assert result["by_source"]["github.com/org"] == 3
    assert result["by_source"]["unknown"] == 1


@patch("codemie.repository.skill_event_repository.Session")
def test_get_all_skills_aggregated_stats_returns_empty_when_no_slugs(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    count_result = MagicMock()
    count_result.one.return_value = 0
    slugs_result = MagicMock()
    slugs_result.all.return_value = []
    mock_session.exec.side_effect = [count_result, slugs_result]

    items, total = _repo().get_all_skills_aggregated_stats(user_id=None, from_dt=None, to_dt=None, limit=100, offset=0)

    assert items == []
    assert total == 0


@patch("codemie.repository.skill_event_repository.Session")
def test_get_all_skills_aggregated_stats_returns_per_skill_breakdown(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    count_result = MagicMock()
    count_result.one.return_value = 1
    slugs_result = MagicMock()
    slugs_result.all.return_value = ["code-review"]
    rows_result = MagicMock()
    rows_result.all.return_value = [
        ("code-review", "add", "agent-a", "github.com/org", 2),
        ("code-review", "remove", None, None, 1),
    ]
    mock_session.exec.side_effect = [count_result, slugs_result, rows_result]

    items, total = _repo().get_all_skills_aggregated_stats(
        user_id="user-1", from_dt=None, to_dt=None, limit=100, offset=0
    )

    assert total == 1
    assert len(items) == 1
    item = items[0]
    assert item["skill_slug"] == "code-review"
    assert item["installs"] == 2
    assert item["removals"] == 1
    assert item["by_agent"]["agent-a"] == 2
    assert item["by_source"]["github.com/org"] == 2


@patch("codemie.repository.skill_event_repository.Session")
def test_get_all_skills_aggregated_stats_slug_query_orders_by_install_count_desc(mock_session_cls) -> None:
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    count_result = MagicMock()
    count_result.one.return_value = 0
    slugs_result = MagicMock()
    slugs_result.all.return_value = []
    mock_session.exec.side_effect = [count_result, slugs_result]

    _repo().get_all_skills_aggregated_stats(user_id=None, from_dt=None, to_dt=None, limit=10, offset=0)

    slug_stmt = mock_session.exec.call_args_list[1][0][0]
    compiled = str(slug_stmt.compile()).lower()
    assert "sum(" in compiled
    assert "order by" in compiled
    assert "desc" in compiled
    # Secondary tie-breaker must also be present (stable pagination)
    assert "skill_slug" in compiled


@patch("codemie.repository.skill_event_repository.Session")
def test_get_all_skills_aggregated_stats_returns_results_in_install_count_order(mock_session_cls) -> None:
    """Skills with more installs must appear before skills with fewer installs."""
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session

    count_result = MagicMock()
    count_result.one.return_value = 2
    # Simulate DB returning slugs already ordered by installs DESC (popular-skill first)
    slugs_result = MagicMock()
    slugs_result.all.return_value = ["popular-skill", "rare-skill"]
    rows_result = MagicMock()
    rows_result.all.return_value = [
        ("popular-skill", "add", "agent-a", None, 10),
        ("rare-skill", "add", "agent-b", None, 1),
    ]
    mock_session.exec.side_effect = [count_result, slugs_result, rows_result]

    items, total = _repo().get_all_skills_aggregated_stats(user_id=None, from_dt=None, to_dt=None, limit=10, offset=0)

    assert total == 2
    assert items[0]["skill_slug"] == "popular-skill"
    assert items[0]["installs"] == 10
    assert items[1]["skill_slug"] == "rare-skill"
    assert items[1]["installs"] == 1
