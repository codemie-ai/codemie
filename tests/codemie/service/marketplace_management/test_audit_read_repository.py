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

"""Unit tests for the `audit_read_repository` module.

Specifically the `CleanupOrderBy` enum extension and `_CLEANUP_ORDER_BY` dict
entries added for column-header sorting (EPMCDME-15333).
"""

from __future__ import annotations

import pytest

from codemie.service.marketplace_management.audit_read_repository import _CLEANUP_ORDER_BY
from codemie.service.marketplace_management.models import CleanupOrderBy


@pytest.mark.parametrize(
    "value,expected_fragment",
    [
        ("users_asc", "users"),
        ("likes_asc", "likes"),
        ("name_desc", "name"),
    ],
)
def test_cleanup_order_by_new_values(value: str, expected_fragment: str) -> None:
    """New enum values map to SQL ORDER BY clauses that reference the expected column."""
    order_by = CleanupOrderBy(value)
    clause = str(_CLEANUP_ORDER_BY[order_by])
    assert expected_fragment in clause.lower()


def test_cleanup_filters_scope_to_assistants_that_are_public_now() -> None:
    """Withdraw, reject, re-submit and rollback all end with `is_global` false, so that is what the queue keys on."""
    from codemie.service.marketplace_management.audit_read_repository import _cleanup_filters

    where, _ = _cleanup_filters(
        bucket=None,
        reason=None,
        tier=None,
        verdict=None,
        mcp=None,
        category=None,
        cluster_id=None,
        q=None,
        rule=None,
    )

    assert "a.is_global IS TRUE" in where
    for status in ("WITHDRAWN", "REJECTED", "PENDING_REVIEW", "DRAFT"):
        assert status not in where, f"the cleanup scope must not enumerate {status}"
