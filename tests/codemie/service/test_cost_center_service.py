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

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.cost_center_service import CostCenterService


class TestCostCenterService:
    @pytest.mark.parametrize(
        "name",
        [
            "eng-123",
            "aa-bb2",
            "1a-2b",
        ],
    )
    def test_validate_name_accepts_single_separator_pattern(self, name: str):
        assert CostCenterService.validate_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "eng--123",
            "eng-123-extra",
            "eng_123",
            "-eng123",
            "eng123-",
        ],
    )
    def test_validate_name_rejects_invalid_separator_pattern(self, name: str):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.validate_name(name)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Invalid cost center name"
