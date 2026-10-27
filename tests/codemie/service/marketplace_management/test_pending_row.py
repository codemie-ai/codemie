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

"""`PendingRow` construction from a real Assistant row (F-09).

`GET /entities/{kind}/pending` built each row with
`submitted_by=a.created_by`, but `Assistant.created_by` is a `CreatedByUser`
object and `PendingRow.submitted_by` is an `Optional[str]` — so pydantic
raised a ValidationError and the whole page 500ed as soon as the queue held a
single assistant with a creator (i.e. always).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codemie.core.models import CreatedByUser
from codemie.service.marketplace_management.models import GateResultFilter, PendingOrderBy, PendingRow


def test_pending_row_accepts_a_created_by_user() -> None:
    """The handler's projection of `created_by` must fit the wire model."""
    row = PendingRow(
        assistant_id="x",
        submitted_by=CreatedByUser(id="u1", name="Alice"),
    )

    assert row.submitted_by == "Alice"


def test_pending_row_tolerates_a_creatorless_assistant() -> None:
    """`created_by` is Optional on the Assistant table — None must not raise."""
    row = PendingRow(assistant_id="x", submitted_by=None)

    assert row.submitted_by is None


def test_pending_row_still_rejects_a_nonsense_submitted_by() -> None:
    """Widening the field must not turn it into `Any`."""
    with pytest.raises(ValidationError):
        PendingRow(assistant_id="x", submitted_by=object())


def test_gate_result_filter_members():
    assert GateResultFilter("all_clear") == GateResultFilter.all_clear
    assert GateResultFilter("has_critical") == GateResultFilter.has_critical
    assert GateResultFilter("has_optional_only") == GateResultFilter.has_optional_only


def test_pending_order_by_members():
    assert PendingOrderBy("submitted_asc") == PendingOrderBy.submitted_asc
    assert PendingOrderBy("submitted_desc") == PendingOrderBy.submitted_desc
    assert PendingOrderBy("name_asc") == PendingOrderBy.name_asc
    assert PendingOrderBy.submitted_asc.value == "submitted_asc"
