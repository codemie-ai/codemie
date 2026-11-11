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
from pydantic import ValidationError

from codemie_tools.azure_devops.test_plan.models import GetTestCasesInput, GetTestPlanInput, GetTestSuiteInput


@pytest.mark.parametrize(
    "model, required",
    [
        (GetTestPlanInput, {}),
        (GetTestSuiteInput, {"plan_id": 1}),
        (GetTestCasesInput, {"plan_id": 1, "suite_id": 1}),
    ],
    ids=["plans", "suites", "cases"],
)
class TestLimitSchema:
    def test_omitted_limit_is_none(self, model, required):
        assert model(**required).limit is None

    @pytest.mark.parametrize("value", [-1, 1, 1000])
    def test_valid_limit_accepted(self, model, required, value):
        assert model(**required, limit=value).limit == value

    @pytest.mark.parametrize("value", [0, -2])
    def test_invalid_limit_rejected(self, model, required, value):
        with pytest.raises(ValidationError):
            model(**required, limit=value)
