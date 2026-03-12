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

    def test_get_enabled_components(self):
        with patch("codemie.configs.customer_config.Path.read_text") as mock_read_text:
            mock_read_text.return_value = yaml.dump(self.valid_yaml)
            config = CustomerConfig()
            enabled_components = config.get_enabled_components()

            self.assertEqual(len(enabled_components), 1)
            self.assertEqual(enabled_components[0].id, "component1")
            self.assertTrue(enabled_components[0].settings.enabled)

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
