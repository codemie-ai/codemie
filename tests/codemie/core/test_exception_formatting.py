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

from codemie.core.exception_formatting import describe_exception


class TestDescribeException:
    """describe_exception makes swallowed subagent failures readable in logs."""

    def test_describe_exception_model_not_whitelisted(self):
        from codemie.core.exceptions import ModelNotWhitelistedException

        e = ModelNotWhitelistedException("x", "proj", details="d")
        result = describe_exception(e)

        assert "ModelNotWhitelistedException" in result
        assert str(e.message) in result
        assert str(e.code) in result
        assert "details=d" in result

    def test_describe_exception_plain_error(self):
        result = describe_exception(ValueError("boom"))

        assert "ValueError" in result
        assert "boom" in result
