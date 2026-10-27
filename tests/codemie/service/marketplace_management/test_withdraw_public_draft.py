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

"""Assistants that were public before the migration are DRAFT with `is_global = true`.

They must stay unpublishable (WITHDRAW) and reviewable (entity detail), while a
private DRAFT keeps conflicting / 404-ing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from codemie.rest_api.routers import marketplace_management as mm
from codemie.service.marketplace_management.queue_service import (
    _CAS_SQL,
    TRANSITIONS,
    ReviewDecision,
)


def test_withdraw_accepts_draft_only_while_public():
    rule = TRANSITIONS[ReviewDecision.WITHDRAW]

    assert "DRAFT" not in rule.allowed_from
    assert rule.allowed_from_if_global == ("DRAFT",)


def test_no_other_transition_widens_its_sources_for_public_rows():
    widened = {d for d, r in TRANSITIONS.items() if r.allowed_from_if_global}

    assert widened == {ReviewDecision.WITHDRAW}


def test_cas_statement_gates_the_extra_sources_on_is_global():
    sql = str(_CAS_SQL)

    assert "is_global AND marketplace_status = ANY(CAST(:allowed_from_if_global" in sql


@pytest.mark.parametrize(
    "status, is_global, found",
    [
        ("DRAFT", True, True),
        ("DRAFT", False, False),
        ("APPROVED", False, True),
    ],
)
def test_entity_loader_treats_public_draft_as_engaged(status, is_global, found):
    assistant = MagicMock(marketplace_status=status, is_global=is_global)

    with patch.object(mm.Assistant, "get_by_id", return_value=assistant):
        if found:
            assert mm._load_assistant_or_404("a1") is assistant
        else:
            with pytest.raises(HTTPException) as exc:
                mm._load_assistant_or_404("a1")
            assert exc.value.status_code == 404
