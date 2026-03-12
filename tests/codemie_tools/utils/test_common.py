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

from unittest.mock import MagicMock

from pydantic import BaseModel, ValidationError

from codemie_tools.base.utils import humanize_error


class TestModel(BaseModel):
    name: str
    age: int
    gender: str


def test_regular_error():
    try:
        raise ValueError("This is a regular error")
    except Exception as e:
        assert humanize_error(e) == "This is a regular error"


def test_pydantic_validation_error():
    try:
        TestModel(name="John", age="twenty")
    except ValidationError as e:
        expected = "Age: input should be a valid integer, unable to parse string as an integer, gender: field required"
        assert humanize_error(e) == expected


def test_pydantic_validation_error_ex():
    e = MagicMock(spec=ValidationError)
    e.__str__.return_value = "Invalid value: 'value'"
    e.errors.return_value = None
    assert humanize_error(e) == "Invalid value: 'value'"
