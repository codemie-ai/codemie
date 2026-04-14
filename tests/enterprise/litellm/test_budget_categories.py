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

"""Unit tests for BudgetCategory helpers (QA-T5).

Covers:
  - build_user_id stable suffix construction
"""

from __future__ import annotations


from codemie.enterprise.litellm.budget_categories import (
    BudgetCategory,
    build_user_id,
)


# ---------------------------------------------------------------------------
# build_user_id
# ---------------------------------------------------------------------------


class TestBuildUserId:
    def test_platform_returns_bare_email(self):
        """PLATFORM category has no suffix — user_id equals the email."""
        result = build_user_id("alice@example.com", BudgetCategory.PLATFORM)
        assert result == "alice@example.com"

    def test_cli_appends_stable_suffix(self):
        """CLI category always appends '_codemie_cli'."""
        result = build_user_id("alice@example.com", BudgetCategory.CLI)
        assert result == "alice@example.com_codemie_cli"

    def test_premium_models_appends_stable_suffix(self):
        """PREMIUM_MODELS category always appends '_codemie_premium_models'."""
        result = build_user_id("alice@example.com", BudgetCategory.PREMIUM_MODELS)
        assert result == "alice@example.com_codemie_premium_models"

    def test_suffix_is_independent_of_config(self):
        """The suffix is a stable constant — not read from any config value."""
        result_a = build_user_id("user@corp.com", BudgetCategory.CLI)
        result_b = build_user_id("user@corp.com", BudgetCategory.CLI)
        assert result_a == result_b == "user@corp.com_codemie_cli"

    def test_email_with_dots_preserved(self):
        """Dots and plus signs in the local-part are preserved verbatim."""
        result = build_user_id("user.name+tag@example.com", BudgetCategory.CLI)
        assert result == "user.name+tag@example.com_codemie_cli"
