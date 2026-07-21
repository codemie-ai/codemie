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

import unittest
from unittest.mock import patch
from importlib.metadata import PackageNotFoundError

import yaml
from pydantic import ValidationError

from codemie.configs.customer_config import CustomerConfig, Component, ComponentSetting


class TestComponentSetting(unittest.TestCase):
    def test_component_setting_default(self):
        setting = ComponentSetting(enabled=True)
        self.assertTrue(setting.enabled)
        self.assertIsNone(setting.name)
        self.assertIsNone(setting.url)

    def test_component_setting_with_values(self):
        setting = ComponentSetting(enabled=True, name="test", url="http://test.com")
        self.assertTrue(setting.enabled)
        self.assertEqual(setting.name, "test")
        self.assertEqual(setting.url, "http://test.com")

    def test_component_setting_extra_fields(self):
        # Test that extra fields are allowed
        setting = ComponentSetting(enabled=True, extra_field="value")
        self.assertTrue(setting.enabled)
        self.assertEqual(getattr(setting, "extra_field"), "value")


class TestComponent(unittest.TestCase):
    def setUp(self):
        self.valid_settings = ComponentSetting(enabled=True)

    def test_component_default(self):
        component = Component(id="test_id", settings=self.valid_settings)
        self.assertEqual(component.id, "test_id")
        self.assertTrue(component.settings.enabled)

    def test_component_invalid_id(self):
        with self.assertRaises(ValidationError):
            Component(id=None, settings=self.valid_settings)

    def test_component_with_full_settings(self):
        settings = ComponentSetting(enabled=True, name="Test Component", url="http://test.com")
        component = Component(id="test_component", settings=settings)
        self.assertEqual(component.id, "test_component")
        self.assertEqual(component.settings.name, "Test Component")
        self.assertEqual(component.settings.url, "http://test.com")


class TestCustomerConfig(unittest.TestCase):
    def setUp(self):
        self.valid_yaml = {
            'components': [
                {
                    'id': 'component1',
                    'settings': {'enabled': True, 'name': 'Component 1', 'url': 'http://component1.com'},
                },
                {'id': 'component2', 'settings': {'enabled': False, 'name': 'Component 2'}},
            ]
        }

    @patch("codemie.configs.customer_config.Path.read_text")
    def test_load_config_successful(self, mock_read_text):
        mock_read_text.return_value = yaml.dump(self.valid_yaml)
        config = CustomerConfig()
        self.assertEqual(len(config.components), 2)
        self.assertEqual(config.components[0].id, 'component1')
        self.assertTrue(config.components[0].settings.enabled)
        self.assertEqual(config.components[0].settings.name, 'Component 1')
        self.assertEqual(config.components[0].settings.url, 'http://component1.com')

    @patch("codemie.configs.customer_config.Path.read_text")
    def test_load_config_invalid_yaml(self, mock_read_text):
        mock_read_text.return_value = "invalid_yaml: ["
        with self.assertRaises(ValueError) as context:
            CustomerConfig()
        self.assertIn("Error parsing YAML", str(context.exception))

    @patch("codemie.configs.customer_config.Path.read_text")
    def test_load_config_invalid_structure(self, mock_read_text):
        # Test invalid root structure
        mock_read_text.return_value = yaml.dump([1, 2, 3])
        with self.assertRaises(ValueError) as context:
            CustomerConfig()
        self.assertIn("Invalid YAML structure: root must be a dictionary", str(context.exception))

        # Test invalid components structure
        mock_read_text.return_value = yaml.dump({'components': 'not_a_list'})
        with self.assertRaises(ValueError) as context:
            CustomerConfig()
        self.assertIn("Invalid YAML structure: 'components' must be a non-empty list", str(context.exception))

    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_get_enabled_components(self, mock_config, mock_version):
        with patch("codemie.configs.customer_config.Path.read_text") as mock_read_text:
            mock_read_text.return_value = yaml.dump(self.valid_yaml)
            mock_version.return_value = "2.3.23"  # Enterprise package installed
            mock_config.ENABLE_USER_MANAGEMENT = False
            mock_config.IDP_PROVIDER = "local"
            mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

            config = CustomerConfig()
            enabled_components = config.get_enabled_components()

            # 1 YAML component + 1 runtime feature (enterpriseEdition) + 2 runtime features (idpProvider, mcpAuthOrigin)
            self.assertEqual(len(enabled_components), 4)

            yaml_components = [c for c in enabled_components if c.id == "component1"]
            self.assertEqual(len(yaml_components), 1)
            self.assertTrue(yaml_components[0].settings.enabled)

    def test_preconfigured_assistants_default_behavior(self):
        """Test that assistants default to enabled when not configured"""
        with patch("codemie.configs.customer_config.Path.read_text") as mock_read_text:
            mock_read_text.return_value = yaml.dump(self.valid_yaml)
            config = CustomerConfig()

            # Assistant not in config should default to enabled
            self.assertTrue(config.is_assistant_enabled("unconfigured-assistant"))

    def test_preconfigured_assistants_configuration(self):
        """Test preconfigured assistants configuration"""
        yaml_with_assistants = {
            'components': [{'id': 'component1', 'settings': {'enabled': True}}],
            'preconfigured_assistants': [
                {'id': 'assistant1', 'settings': {'enabled': True}},
                {'id': 'assistant2', 'settings': {'enabled': False}},
                {'id': 'assistant3', 'settings': {'enabled': True}},
            ],
        }

        with patch("codemie.configs.customer_config.Path.read_text") as mock_read_text:
            mock_read_text.return_value = yaml.dump(yaml_with_assistants)
            config = CustomerConfig()

            # Test enabled assistants
            self.assertTrue(config.is_assistant_enabled("assistant1"))
            self.assertTrue(config.is_assistant_enabled("assistant3"))

            # Test disabled assistant
            self.assertFalse(config.is_assistant_enabled("assistant2"))

            # Test unconfigured assistant (should default to enabled)
            self.assertTrue(config.is_assistant_enabled("unconfigured"))

    def test_is_feature_enabled(self):
        """Test is_feature_enabled checks feature flags by component id prefix 'features:'"""
        yaml_with_features = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
                {'id': 'features:webSearch', 'settings': {'enabled': True}},
                {'id': 'features:dynamicCodeInterpreter', 'settings': {'enabled': False}},
            ]
        }

        with patch("codemie.configs.customer_config.Path.read_text") as mock_read_text:
            mock_read_text.return_value = yaml.dump(yaml_with_features)
            config = CustomerConfig()

            # Enabled feature returns True
            self.assertTrue(config.is_feature_enabled("webSearch"))

            # Disabled feature returns False
            self.assertFalse(config.is_feature_enabled("dynamicCodeInterpreter"))

            # Unconfigured feature defaults to False (is_component_enabled defaults to False)
            self.assertFalse(config.is_feature_enabled("unknownFeature"))

    def test_get_all_configured_assistant_slugs(self):
        """Test getting all configured assistant slugs"""
        yaml_with_assistants = {
            'components': [{'id': 'component1', 'settings': {'enabled': True}}],
            'preconfigured_assistants': [
                {'id': 'assistant1', 'settings': {'enabled': True}},
                {'id': 'assistant2', 'settings': {'enabled': False}},
                {'id': 'assistant3', 'settings': {'enabled': True}},
            ],
        }

        with patch("codemie.configs.customer_config.Path.read_text") as mock_read_text:
            mock_read_text.return_value = yaml.dump(yaml_with_assistants)
            config = CustomerConfig()

            all_slugs = config.get_all_configured_assistant_slugs()
            expected = ['assistant1', 'assistant2', 'assistant3']
            self.assertEqual(sorted(all_slugs), sorted(expected))


class TestRuntimeFeatures(unittest.TestCase):
    """Test runtime-computed feature flags (enterpriseEdition, userManagement)"""

    def setUp(self):
        self.valid_yaml = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
                {'id': 'features:webSearch', 'settings': {'enabled': True}},
            ]
        }

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_runtime_features_enterprise_installed(self, mock_config, mock_version, mock_read_text):
        """Test runtime features when enterprise package is installed"""
        mock_read_text.return_value = yaml.dump(self.valid_yaml)
        mock_version.return_value = "2.3.23"  # Package exists
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        # Should have 2 YAML components + 4 runtime features (enterpriseEdition, userManagement, idpProvider, mcpAuthOrigin)
        self.assertEqual(len(components), 6)

        # Check enterprise edition is enabled
        enterprise_components = [c for c in components if c.id == "features:enterpriseEdition"]
        self.assertEqual(len(enterprise_components), 1)
        self.assertTrue(enterprise_components[0].settings.enabled)

        # Check user management is enabled
        user_mgmt_components = [c for c in components if c.id == "features:userManagement"]
        self.assertEqual(len(user_mgmt_components), 1)
        self.assertTrue(user_mgmt_components[0].settings.enabled)

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_runtime_features_enterprise_not_installed(self, mock_config, mock_version, mock_read_text):
        """Test runtime features when enterprise package is NOT installed"""
        mock_read_text.return_value = yaml.dump(self.valid_yaml)
        mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        # 2 YAML components + 2 runtime features (idpProvider, mcpAuthOrigin)
        self.assertEqual(len(components), 4)

        enterprise_components = [c for c in components if c.id == "features:enterpriseEdition"]
        self.assertEqual(len(enterprise_components), 0)

        user_mgmt_components = [c for c in components if c.id == "features:userManagement"]
        self.assertEqual(len(user_mgmt_components), 0)

        self.assertFalse(config.is_feature_enabled("enterpriseEdition"))
        self.assertFalse(config.is_feature_enabled("userManagement"))

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_is_feature_enabled_runtime_features(self, mock_config, mock_version, mock_read_text):
        """Test is_feature_enabled works for runtime-computed features"""
        mock_read_text.return_value = yaml.dump(self.valid_yaml)
        mock_version.return_value = "2.3.23"  # Package exists
        mock_config.ENABLE_USER_MANAGEMENT = True

        config = CustomerConfig()

        self.assertTrue(config.is_feature_enabled("enterpriseEdition"))
        self.assertTrue(config.is_feature_enabled("userManagement"))

        self.assertTrue(config.is_feature_enabled("webSearch"))

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_runtime_features_override_yaml(self, mock_config, mock_version, mock_read_text):
        """Test that runtime features override any YAML configuration with same ID"""
        # YAML with runtime feature IDs (should be ignored)
        yaml_with_runtime_ids = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
                {'id': 'features:enterpriseEdition', 'settings': {'enabled': False}},  # Will be ignored
                {'id': 'features:userManagement', 'settings': {'enabled': False}},  # Will be ignored
            ]
        }

        mock_read_text.return_value = yaml.dump(yaml_with_runtime_ids)
        mock_version.return_value = "2.3.23"  # Package exists
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        # Should have 1 YAML component + 4 runtime features (YAML runtime IDs filtered out)
        self.assertEqual(len(components), 5)

        # Runtime features should be True (not False from YAML)
        self.assertTrue(config.is_feature_enabled("enterpriseEdition"))
        self.assertTrue(config.is_feature_enabled("userManagement"))

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_is_component_enabled_runtime_features(self, mock_config, mock_version, mock_read_text):
        """Test is_component_enabled works for runtime features"""
        mock_read_text.return_value = yaml.dump(self.valid_yaml)
        mock_version.return_value = "2.3.23"  # Package exists
        mock_config.ENABLE_USER_MANAGEMENT = False

        config = CustomerConfig()

        # Runtime features via is_component_enabled
        self.assertTrue(config.is_component_enabled("features:enterpriseEdition"))
        self.assertFalse(config.is_component_enabled("features:userManagement"))

        # YAML components should still work
        self.assertTrue(config.is_component_enabled("features:webSearch"))

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_disabled_runtime_features_excluded_like_yaml(self, mock_config, mock_version, mock_read_text):
        """Test that disabled runtime features are excluded from get_enabled_components(), matching YAML behavior"""
        # YAML with one enabled, one disabled component
        yaml_data = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
                {'id': 'component2', 'settings': {'enabled': False}},
            ]
        }
        mock_read_text.return_value = yaml.dump(yaml_data)
        mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        # 1 YAML component + 2 feature components (idpProvider, mcpAuthOrigin)
        self.assertEqual(len(components), 3)

        yaml_components = [c for c in components if c.id == "component1"]
        self.assertEqual(len(yaml_components), 1)
        self.assertEqual(yaml_components[0].id, "component1")

        # Verify disabled YAML component NOT in response
        self.assertFalse(any(c.id == "component2" for c in components))

        # Verify disabled runtime features NOT in response (same behavior as YAML)
        self.assertFalse(any(c.id == "features:enterpriseEdition" for c in components))
        self.assertFalse(any(c.id == "features:userManagement" for c in components))

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_idp_provider_system_component(self, mock_config, mock_version, mock_read_text):
        """Test that IDP provider is exposed as a feature component with correct value"""
        yaml_data = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
            ]
        }
        mock_read_text.return_value = yaml.dump(yaml_data)
        mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "keycloak"
        mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        # Find the IDP provider component
        idp_components = [c for c in components if c.id == "idpProvider"]
        self.assertEqual(len(idp_components), 1)

        idp_component = idp_components[0]
        self.assertTrue(idp_component.settings.enabled)
        self.assertEqual(idp_component.settings.model_dump()["value"], "keycloak")

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_idp_provider_default_value(self, mock_config, mock_version, mock_read_text):
        """Test that IDP provider defaults to 'local' when not set"""
        yaml_data = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
            ]
        }
        mock_read_text.return_value = yaml.dump(yaml_data)
        mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        # Find the IDP provider component
        idp_components = [c for c in components if c.id == "idpProvider"]
        self.assertEqual(len(idp_components), 1)

        idp_component = idp_components[0]
        self.assertTrue(idp_component.settings.enabled)
        self.assertEqual(idp_component.settings.model_dump()["value"], "local")

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_mcp_auth_origin_component(self, mock_config, mock_version, mock_read_text):
        yaml_data = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
            ]
        }
        mock_read_text.return_value = yaml.dump(yaml_data)
        mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "https://codemie.example.com"

        config = CustomerConfig()
        components = config.get_enabled_components()

        mcp_components = [c for c in components if c.id == "mcpAuthOrigin"]
        self.assertEqual(len(mcp_components), 1)

        mcp_component = mcp_components[0]
        self.assertTrue(mcp_component.settings.enabled)
        self.assertEqual(mcp_component.settings.model_dump()["value"], "https://codemie.example.com")

    @patch("codemie.configs.customer_config.Path.read_text")
    @patch("codemie.configs.customer_config.version")
    @patch("codemie.configs.customer_config.config")
    def test_mcp_auth_origin_default_value(self, mock_config, mock_version, mock_read_text):
        yaml_data = {
            'components': [
                {'id': 'component1', 'settings': {'enabled': True}},
            ]
        }
        mock_read_text.return_value = yaml.dump(yaml_data)
        mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "local"
        mock_config.CALLBACK_API_BASE_URL = "http://host.docker.internal:8080"

        config = CustomerConfig()
        components = config.get_enabled_components()

        mcp_components = [c for c in components if c.id == "mcpAuthOrigin"]
        self.assertEqual(len(mcp_components), 1)

        mcp_component = mcp_components[0]
        self.assertTrue(mcp_component.settings.enabled)
        self.assertEqual(mcp_component.settings.model_dump()["value"], "http://host.docker.internal:8080")
