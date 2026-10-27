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

from uuid import uuid4
from codemie.rest_api.models.usage.assistant_clone_event import AssistantCloneEvent, AssistantCloneEventSQL


def test_assistant_clone_event_has_expected_fields():
    # id is assigned lazily by BaseModelWithSQLSupport.save() (base.py:501-502), or explicitly
    # by the repository at insert time — not by bare construction. Matches AssistantUserInterationSQL.
    event = AssistantCloneEvent(id="explicit-id", assistant_id="a1", user_id="u1")
    assert event.assistant_id == "a1"
    assert event.user_id == "u1"
    assert event.id == "explicit-id"
    assert event.created_at is not None


def test_assistant_clone_event_table_args_have_no_unique_constraint():
    from sqlalchemy import UniqueConstraint

    for arg in AssistantCloneEvent.__table_args__:
        assert not isinstance(
            arg, UniqueConstraint
        ), "assistant_clone_event must not have a UniqueConstraint — every clone action must count"


def test_assistant_clone_event_model_dump_has_only_required_fields():
    """
    Regression test for EPMCDME-10889 bug:
    AssistantCloneEventSQL was inheriting from BaseModelWithSQLSupport which adds 'date' and
    'update_date' fields via CommonBaseModel, but the migration only created 4 columns
    (id, assistant_id, user_id, created_at).

    When inserting, SQLModel tried to write to non-existent 'date' and 'update_date' columns,
    causing: psycopg2.errors.UndefinedColumn: column "date" of relation "assistant_clone_event" does not exist

    Fix: AssistantCloneEventSQL now inherits from plain SQLModel, no unwanted fields.
    This test ensures the model.dump() has ONLY the 4 required fields.
    """
    event = AssistantCloneEventSQL(id=str(uuid4()), assistant_id="a1", user_id="u1")
    dumped = event.model_dump()

    expected_keys = {
        "id",
        "assistant_id",
        "user_id",
        "created_at",
        # Added with the clone provenance edge (EXECUTION-PLAN 9.1, F-70):
        # marketplace duplicate detection reads it as ground truth for
        # "this assistant was cloned from that one".
        "clone_assistant_id",
    }
    unwanted_keys = {"date", "update_date"}  # Would come from CommonBaseModel if inheritance was wrong

    assert set(dumped.keys()) == expected_keys, f"Expected {expected_keys}, got {set(dumped.keys())}"
    assert not any(key in dumped for key in unwanted_keys), f"Model should not have {unwanted_keys}"
