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


"""The routing explanation on the public session list and detail (EPMCDME-16440): the diagnostic `route_note` is
exposed as stored, and `routing_label` is set only for a default-routed session whose folder had no usable link."""

from __future__ import annotations

from unittest.mock import AsyncMock
from datetime import datetime

import pytest

from codemie.rest_api.models import cli_analytics as models
from codemie.service.analytics.handlers.cli_analytics_handler import FOLDER_NOT_LINKED_LABEL, _routing
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from tests.codemie.service.analytics.handlers.test_cli_analytics_class_lists import _Capture, _flat, _flt

FALLBACK_NOTES = (
    "folder_not_linked",
    "linked_project_unavailable",  # the linked folder's project is inaccessible
    "registered_project_inaccessible",
    "registration_ambiguous",
)


def test_the_exact_label_text():
    assert FOLDER_NOT_LINKED_LABEL == "folder not linked to a project"


@pytest.mark.parametrize("note", FALLBACK_NOTES)
def test_a_default_routed_session_gets_the_label_and_keeps_its_note(note):
    assert _routing({"project_origin": "default_project", "route_note": note}) == {
        "route_note": note,
        "routing_label": FOLDER_NOT_LINKED_LABEL,
    }


@pytest.mark.parametrize(
    "row",
    [
        {"project_origin": "default_project"},  # legacy default without a note: nothing is guessed
        {"project_origin": "default_project", "route_note": "  "},
        {"project_origin": "default_project", "route_note": "something_new"},
        {"project_origin": "none", "route_note": "folder_not_linked"},
        {"project_origin": "linked_folder", "route_note": "folder_not_linked"},
        {"project_origin": "legacy_project", "route_note": None},
        {},
    ],
)
def test_no_label_for_other_origins_or_missing_or_unknown_notes(row):
    out = _routing(row)
    assert out["routing_label"] is None
    assert out["route_note"] == ((row.get("route_note") or "").strip() or None)  # served as stored, never invented


@pytest.mark.parametrize("model", [models.LocalAnalyticsSessionRow, models.LocalAnalyticsSessionDetail])
def test_serialized_session_carries_both_fields_and_omits_them_as_null_for_legacy(model):
    base = {
        "trace_id": "t",
        "developer_name": "d",
        "start_time": "2026-09-01T00:00:00",
        "duration_ms": 0,
        "model_name": "m",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0,
        "harness_name": "h",
    }
    fields = model.model_fields
    assert {"route_note", "routing_label"} <= set(fields)
    routed = model.model_validate(
        {**base, **_routing({"project_origin": "default_project", "route_note": "linked_project_unavailable"})}
    )
    dumped = routed.model_dump(mode="json")
    assert (dumped["route_note"], dumped["routing_label"]) == ("linked_project_unavailable", FOLDER_NOT_LINKED_LABEL)
    legacy = model.model_validate({**base, **_routing({})}).model_dump(mode="json")
    assert (legacy["route_note"], legacy["routing_label"]) == (None, None)


@pytest.mark.asyncio
async def test_list_and_detail_sql_project_the_stored_note():
    r = _Capture()
    await r.get_session_detail_meta("sid", scope=EXCLUDE_SCOPE)
    assert "AS route_note" in _flat(r.sql[0])
    await r.get_session_cost_facts(_flt())
    assert "AS route_note" in _flat(r.sql[1]) and "d.route_note" in _flat(r.sql[1])


# ── the real list and detail builders, serialized ────────────────────────────────────────────────────────────────

from tests.codemie.service.analytics.handlers.test_cli_analytics_handler import (  # noqa: E402
    BASE_COST_ROW,
    DETAIL_SCOPE,
    make_detail_handler,
    make_filter,
    make_handler,
)

FALLBACK = {
    "session_id": "fallback",
    "project_origin": "default_project",
    "route_note": "registered_project_inaccessible",
}
LEGACY = {"session_id": "legacy", "project_origin": "default_project", "route_note": None}  # no note stored
LINKED = {"session_id": "linked", "project_origin": "linked_folder", "route_note": None}


@pytest.mark.asyncio
async def test_the_real_session_list_serializes_each_rows_note_and_label():
    rows = [{**BASE_COST_ROW, **r} for r in (FALLBACK, LEGACY, LINKED)]
    handler = make_handler(rows)
    handler._repo.get_skill_names_by_session = AsyncMock(return_value=[])

    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    out = {
        s["trace_id"]: models.LocalAnalyticsSessionRow.model_validate(s).model_dump(mode="json")
        for s in data["sessions"]
    }
    assert len(out) == 3
    pick = lambda sid: (out[sid]["route_note"], out[sid]["routing_label"])  # noqa: E731
    assert pick("fallback") == ("registered_project_inaccessible", "folder not linked to a project")
    assert pick("legacy") == (None, None)
    assert pick("linked") == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (FALLBACK, ("registered_project_inaccessible", "folder not linked to a project")),
        (LEGACY, (None, None)),
        ({"session_id": "legacy-missing"}, (None, None)),  # no dims row: nothing guessed
    ],
    ids=["default-fallback", "default-without-note", "missing-dims"],
)
async def test_the_real_session_detail_serializes_its_note_and_label(meta, expected):
    handler = make_detail_handler({**meta, "started_at": datetime(2026, 1, 1), "duration_ms": 1000})

    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    dumped = models.LocalAnalyticsSessionDetail.model_validate(
        {"trace_id": "s1", "developer_name": "d", "start_time": "2026-01-01T00:00:00", **detail}
    ).model_dump(mode="json")
    assert (dumped["route_note"], dumped["routing_label"]) == expected
