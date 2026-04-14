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

from pathlib import Path

from pydantic_settings import BaseSettings, YamlConfigSettingsSource

from codemie.configs.config import PredefinedBudgetConfig, config
from codemie.configs.logger import logger


class BudgetYamlSettings(BaseSettings):
    yaml_file: Path

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,  # type: ignore[override]
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return init_settings, YamlConfigSettingsSource(cls, init_settings.init_kwargs["yaml_file"])


class BudgetConfig(BudgetYamlSettings):
    predefined_budgets: list[PredefinedBudgetConfig]


budget_config = BudgetConfig(yaml_file=config.BUDGETS_CONFIG_DIR / "budgets-config.yaml")

logger.info(
    f"BudgetConfig initiated. Config={budget_config.yaml_file}. "
    f"PredefinedBudgets={budget_config.predefined_budgets}"
)
