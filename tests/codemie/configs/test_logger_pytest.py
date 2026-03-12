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
