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

from unittest.mock import patch

import pytest

from codemie.configs.customer_config import CustomerConfig
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.budget.feature_gate import ensure_budget_management_enabled


def test_ensure_budget_management_enabled_raises_when_disabled():
    # `customer_config` is a pydantic model instance; patch the class method rather than the
    # instance attribute (pydantic's __setattr__/__delattr__ rejects ad hoc instance patching).
    with patch.object(CustomerConfig, "is_feature_enabled", return_value=False):
        with pytest.raises(ExtendedHTTPException) as exc:
            ensure_budget_management_enabled()
        assert exc.value.code == 403


def test_ensure_budget_management_enabled_passes_when_enabled():
    with patch.object(CustomerConfig, "is_feature_enabled", return_value=True):
        ensure_budget_management_enabled()  # does not raise
