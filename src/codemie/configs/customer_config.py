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

import logging
import yaml
from pathlib import Path
from typing import List, Optional, Dict
from importlib.metadata import version, PackageNotFoundError
from pydantic import BaseModel, Field, ConfigDict, field_validator
from codemie.configs.config import config


CONFIG_IDS = {
    "enterpriseEdition": "features:enterpriseEdition",
    "userManagement": "features:userManagement",
    "idpProvider": "idpProvider",
    "mcpAuthOrigin": "mcpAuthOrigin",
}


class ComponentSetting(BaseModel):
    enabled: bool = Field()
    availableForExternal: bool = Field(default=True)
    name: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    created_by: Optional[str] = Field(default=None)
    icon_url: Optional[str] = Field(default=None)

    model_config = ConfigDict(extra="allow")


class AssistantSetting(BaseModel):
    enabled: bool = Field()
    index_name: Optional[str] = Field(default=None)

    model_config = ConfigDict(extra="allow")


class PreconfiguredAssistant(BaseModel):
    id: str = Field()
    settings: AssistantSetting
    project: Optional[str] = Field(default=None)

    @field_validator('id')
    def validate_id(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Assistant ID must be a non-empty string")
        return v


class Component(BaseModel):
    id: str = Field()
    settings: ComponentSetting

    @staticmethod
    @field_validator('id')
    def validate_id(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Component ID must be a non-empty string")
        return v


class CustomerConfig(BaseModel):
    components: List[Component] = Field(default_factory=list)
    preconfigured_assistants: List[PreconfiguredAssistant] = Field(default_factory=list)
    config_path: Path = Field(default=Path(f'{config.CUSTOMER_CONFIG_DIR}/customer-config.yaml'))

    @staticmethod
    @field_validator("components")
    def _validate_components(cls, v: List[Component]) -> List[Component]:
        if not v:
            raise FileNotFoundError(f"Customer config file not found at: {v}")
        return v

    def model_post_init(self, _) -> None:
        self._load_config()

    def _read_config_file(self) -> str:
        return self.config_path.read_text()

    def _load_config(self) -> None:
        try:
            config_data = yaml.safe_load(self._read_config_file())
            if not isinstance(config_data, dict):
                raise ValueError("Invalid YAML structure: root must be a dictionary")

            components_data = config_data.get('components', [])
            if not isinstance(components_data, list) or not components_data:
                raise ValueError("Invalid YAML structure: 'components' must be a non-empty list")

            self.components = [
                Component(id=comp_data['id'], settings=ComponentSetting(**comp_data['settings']))
                for comp_data in components_data
            ]

            # Load preconfigured assistants configuration
            preconfigured_assistants_data = config_data.get('preconfigured_assistants', [])
            if isinstance(preconfigured_assistants_data, list):
                self.preconfigured_assistants = [
                    PreconfiguredAssistant(
                        id=assistant_data['id'], settings=AssistantSetting(**assistant_data['settings'])
                    )
                    for assistant_data in preconfigured_assistants_data
                ]
            else:
                self.preconfigured_assistants = []

            logging.debug(f"Successfully loaded {len(self.components)} components")
        except yaml.YAMLError as exc:
            raise ValueError(f"Error parsing YAML configuration: {exc}")
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid component configuration: {exc}")
        except Exception as exc:
            raise ValueError(f"Error processing configuration: {exc}")

    def _get_runtime_config(self) -> List[Component]:
        """Generate runtime-computed configuration components.

        These components are determined by actual system state rather than YAML configuration.
        Includes both boolean feature flags and string config values.
        """
        runtime_config = []

        # Enterprise Edition - automatic detection via package presence
        try:
            version("codemie-enterprise")
            is_enterprise = True
        except PackageNotFoundError:
            is_enterprise = False

        runtime_config.append(
            Component(
                id=CONFIG_IDS["enterpriseEdition"],
                settings=ComponentSetting(enabled=is_enterprise),
            )
        )

        runtime_config.append(
            Component(
                id=CONFIG_IDS["userManagement"],
                settings=ComponentSetting(enabled=config.ENABLE_USER_MANAGEMENT),
            )
        )

        runtime_config.append(
            Component(
                id=CONFIG_IDS["idpProvider"],
                settings=ComponentSetting(enabled=True, value=config.IDP_PROVIDER),
            )
        )

        runtime_config.append(
            Component(
                id=CONFIG_IDS["mcpAuthOrigin"],
                settings=ComponentSetting(enabled=True, value=config.CALLBACK_API_BASE_URL),
            )
        )

        return runtime_config

    def get_enabled_components(self) -> List[Component]:
        """Get enabled components including runtime-computed config"""
        runtime_config_ids = set(CONFIG_IDS.values())

        # Get YAML-based components
        yaml_components = [
            component
            for component in self.components
            if component.settings.enabled and component.id not in runtime_config_ids
        ]

        # Add runtime config
        runtime_config = [c for c in self._get_runtime_config() if c.settings.enabled]

        return yaml_components + runtime_config

    def is_assistant_enabled(self, assistant_slug: str) -> bool:
        """
        Check if a preconfigured assistant is enabled.
        If the assistant is not in the configuration, it defaults to True (enabled).
        """
        return next(
            (
                assistant.settings.enabled
                for assistant in self.preconfigured_assistants
                if assistant.id == assistant_slug
            ),
            True,
        )

    def get_all_configured_assistant_slugs(self) -> List[str]:
        """
        Get all assistant slugs that are configured (both enabled and disabled).
        """
        return [assistant.id for assistant in self.preconfigured_assistants]

    def has_assistant_config(self, assistant_slug: str) -> bool:
        return any(assistant.id == assistant_slug for assistant in self.preconfigured_assistants)

    def get_assistant_config(self, assistant_slug: str) -> Optional[Dict]:
        return next(
            (
                assistant.settings.model_dump()
                for assistant in self.preconfigured_assistants
                if assistant.id == assistant_slug
            ),
            None,
        )

    def get_assistant_target_project(self, assistant_slug: str) -> Optional[str]:
        return next(
            (assistant.project for assistant in self.preconfigured_assistants if assistant.id == assistant_slug),
            None,
        )

    def is_component_enabled(self, component_id: str) -> bool:
        """
        Check if a component is enabled (includes runtime-computed config).
        If the component is not in the configuration, it defaults to False (disabled).
        """
        # Check runtime config first
        runtime_config_ids = set(CONFIG_IDS.values())
        if component_id in runtime_config_ids:
            runtime_components = {c.id: c for c in self._get_runtime_config()}
            if component_id in runtime_components:
                return runtime_components[component_id].settings.enabled

        # Fall back to YAML components
        return next(
            (component.settings.enabled for component in self.components if component.id == component_id),
            False,
        )

    def is_feature_enabled(self, feature_key: str) -> bool:
        """
        Check if a feature is enabled.
        If the feature is not in the configuration, it defaults to True (enabled).

        Args:
            feature_key: The feature key (e.g., 'webSearch', 'dynamicCodeInterpreter')

        Returns:
            True if feature is enabled, False otherwise
        """
        component_id = f"features:{feature_key}"
        return self.is_component_enabled(component_id)

    def get_feature_setting(self, feature_key: str, setting_name: str, default=None):
        """Read an extra setting from a ``features:<feature_key>`` component's settings.

        ``ComponentSetting`` allows extra fields, so features can carry structured config
        (e.g. an interactive-elements ``catalog``). Returns ``default`` when absent.
        """
        component_id = f"features:{feature_key}"
        component = next((c for c in self.components if c.id == component_id), None)
        if component is None:
            return default
        return getattr(component.settings, setting_name, default)


customer_config = CustomerConfig()
