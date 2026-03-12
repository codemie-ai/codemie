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

from codemie_tools.base.string_serializer import StringSerializer


def test_serialize():
    assert StringSerializer.serialize(["hello", "world"]) == "NX5oZWxsbzV+d29ybGQ="
    assert StringSerializer.serialize([]) == ""
    assert StringSerializer.serialize(["test"]) == "NH50ZXN0"
    assert StringSerializer.serialize(["a", "abc", "defgh"]) == "MX5hM35hYmM1fmRlZmdo"


def test_deserialize_legacy():
    assert StringSerializer.deserialize("aGVsbG9fd29ybGRfdGVzdA==") == ["hello", "world", "test"]


def test_deserialize_success():
    assert StringSerializer.deserialize("NX5oZWxsbzV+d29ybGQ=") == ["hello", "world"]
    assert StringSerializer.deserialize("") == []
    assert StringSerializer.deserialize("NH50ZXN0") == ["test"]
    assert StringSerializer.deserialize("MX5hM35hYmM1fmRlZmdo") == ["a", "abc", "defgh"]
    assert StringSerializer.deserialize("jibberish") == []
