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

from codemie.configs.budget_config import BudgetConfig


def test_budget_config_loads_predefined_budgets_from_yaml(tmp_path):
    yaml_file = tmp_path / "budgets-config.yaml"
    yaml_file.write_text(
        """
predefined_budgets:
  - budget_id: cli-budget
    name: CLI Budget
    soft_budget: 10
    max_budget: 100
    budget_duration: 30d
    budget_category: cli
""",
        encoding="utf-8",
    )

    config = BudgetConfig(yaml_file=yaml_file)

    assert len(config.predefined_budgets) == 1
    assert config.predefined_budgets[0].budget_id == "cli-budget"
    assert config.predefined_budgets[0].budget_category == "cli"
