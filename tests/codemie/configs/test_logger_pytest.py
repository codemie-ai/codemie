# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import threading
import uuid

import pytest

from codemie.configs.logger import set_logging_info, logging_uuid, logging_user_id, logging_conversation_id


EXAMPLE_UUID = uuid.uuid4()


@pytest.mark.parametrize(
    "input_logger_data, expected_logger_data",
    (
        ((EXAMPLE_UUID, "example_user_id", "conv_1"), (EXAMPLE_UUID, "example_user_id", "conv_1")),
        ((None, None, None), ("-", "-", "-")),
        ((), ("-", "-", "-")),
    ),
    ids=("all_values_set", "nullable_values_passed", "no_values_passed"),
)
def test_set_logging_info(input_logger_data: tuple, expected_logger_data: tuple) -> None:
    attr_names = ("uuid", "user_id", "conversation_id")
    attributes = {attr_name: attr_val for attr_val, attr_name in zip(input_logger_data, attr_names)}

    set_logging_info(**attributes)

    assert logging_uuid.get() == expected_logger_data[0]
    assert logging_user_id.get() == expected_logger_data[1]
    assert logging_conversation_id.get() == expected_logger_data[2]


def test_copy_and_restore_logging_context() -> None:
    """
    copy_logging_context() captures current values;
    restore_logging_context() writes them back in a new context.
    """
    from codemie.configs.logger import (
        copy_logging_context,
        restore_logging_context,
        logging_uuid,
        logging_user_id,
        logging_conversation_id,
        current_user_email,
    )

    set_logging_info(
        uuid="snap-uuid",
        user_id="snap-user",
        conversation_id="snap-conv",
        user_email="snap@example.com",
    )
    snapshot = copy_logging_context()

    # Simulate context reset (new task)
    set_logging_info()  # resets to defaults

    # Restore from snapshot
    restore_logging_context(snapshot)

    assert logging_uuid.get() == "snap-uuid"
    assert logging_user_id.get() == "snap-user"
    assert logging_conversation_id.get() == "snap-conv"
    assert current_user_email.get() == "snap@example.com"


def test_logging_context_propagates_to_new_thread() -> None:
    """Snapshot/restore carries correlation fields into a forked thread (hedged path pattern)."""
    from codemie.configs.logger import (
        copy_logging_context,
        restore_logging_context,
        current_user_email,
    )

    set_logging_info(
        uuid="test-uuid",
        user_id="user-abc",
        conversation_id="conv-xyz",
        user_email="user@example.com",
    )
    snapshot = copy_logging_context()
    captured: dict[str, str] = {}

    def worker() -> None:
        restore_logging_context(snapshot)
        captured["uuid"] = logging_uuid.get()
        captured["user_id"] = logging_user_id.get()
        captured["conversation_id"] = logging_conversation_id.get()
        captured["user_email"] = current_user_email.get()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert captured == {
        "uuid": "test-uuid",
        "user_id": "user-abc",
        "conversation_id": "conv-xyz",
        "user_email": "user@example.com",
    }
