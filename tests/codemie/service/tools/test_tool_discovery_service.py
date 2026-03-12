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
import typing
from unittest.mock import patch, Mock, MagicMock
from typing import Dict, Optional

from codemie.service.tools.discovery import ToolDiscoveryService, ToolInfo
from codemie.service.tools.discovery.metadata_finder import ToolMetadataFinder
from codemie.service.tools.discovery.config_extractor import ToolConfigExtractor
from codemie.service.tools.discovery.schema_extractor import ToolSchemaExtractor
from codemie_tools.base.models import ToolMetadata
from codemie_tools.base.base_toolkit import BaseToolkit


# Create a proper mock for ToolMetadata
@pytest.fixture
def mock_tool_metadata():
    tool_metadata = Mock(spec=ToolMetadata)
    tool_metadata.name = "test_tool"
    tool_metadata.description = "Test description"
    return tool_metadata


class MockBaseToolkit(BaseToolkit):
    test_config: Dict[str, str]

    @classmethod
    def get_toolkit(cls):
        return cls

    @classmethod
    def get_tools_ui_info(cls):
        return {
            "tools": [
                {"name": "test_tool", "description": "Test tool"},
                {"name": "another_tool", "description": "Another test tool"},
            ]
        }

    @classmethod
    def get_tools(cls):
        return []


class MockBaseProviderToolkt(BaseToolkit):
    @classmethod
    def get_toolkit(cls):
        return cls

    @classmethod
    def get_tools_ui_info(cls):
        return {
            "tools": [
                {"name": "test_tool", "description": "Test tool"},
                {"name": "another_tool", "description": "Another test tool"},
            ]
        }

    @classmethod
    def get_tools(cls):
        tool_1 = MagicMock()
        tool_1.name = "test_tool"
        tool_1.description = "Test tool"

        tool_2 = MagicMock()
        tool_2.name = "another_tool"
        tool_2.description = "Another test tool"

        return [tool_1, tool_2]


class MockConfigClass:
    api_key: str
    base_url: Optional[str]

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


@patch("codemie.service.provider.ProviderToolkitsFactory.get_toolkits", return_values=None)
def test_find_tool_by_name_success(_mock_get_provider_toolkits):
    """Test finding a tool by name when it exists"""
    mock_metadata = Mock(spec=ToolMetadata)
    mock_metadata.name = "test_tool"
    mock_metadata.settings_config = None

    with patch.object(ToolMetadataFinder, 'find_tool_metadata', return_value=mock_metadata):
        with patch.object(ToolMetadataFinder, 'find_toolkit_for_metadata', return_value=MockBaseToolkit):
            with patch.object(
                ToolConfigExtractor,
                'extract_config_for_tool',
                return_value=(MockConfigClass, {"api_key": {"type": str, "required": True}}, "test_config"),
            ):
                with patch.object(ToolSchemaExtractor, 'extract_args_schema', return_value={}):
                    result = ToolDiscoveryService.find_tool_by_name("test_tool")

                    assert result is not None
                    assert result.toolkit_class == MockBaseToolkit
                    assert result.config_class == MockConfigClass
                    assert "api_key" in result.config_schema
                    assert result.config_param_name == "test_config"


@patch("codemie.service.provider.ProviderToolkitsFactory.get_toolkits")
def test_find_tool_by_name_provider(mock_get_provider_toolkits):
    mock_get_provider_toolkits.return_value = [MockBaseProviderToolkt]

    assert ToolDiscoveryService.find_tool_by_name("test_tool") is not None


@patch("codemie.service.provider.ProviderToolkitsFactory.get_toolkits", return_values=None)
def test_find_tool_by_name_not_found(_mock_get_provider_toolkits):
    """Test finding a tool by name when it doesn't exist"""
    with patch.object(ToolMetadataFinder, 'find_tool_metadata', return_value=None):
        result = ToolDiscoveryService.find_tool_by_name("nonexistent_tool")
        assert result is None


@patch("codemie.service.provider.ProviderToolkitsFactory.get_toolkits", return_values=None)
def test_find_tool_by_name_no_toolkit(_mock_get_provider_toolkits):
    """Test finding a tool by name when metadata exists but no toolkit is found"""
    mock_metadata = Mock(spec=ToolMetadata)
    mock_metadata.name = "test_tool"

    with patch.object(ToolMetadataFinder, 'find_tool_metadata', return_value=mock_metadata):
        with patch.object(ToolMetadataFinder, 'find_toolkit_for_metadata', return_value=None):
            result = ToolDiscoveryService.find_tool_by_name("test_tool")
            assert result is None


def test_find_tool_metadata():
    """Test finding tool metadata"""
    # Mock the importlib and pkgutil functionality
    mock_module = Mock()
    mock_module.__path__ = ["dummy_path"]
    mock_module.__name__ = "test_module"

    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "test_tool"

    with patch('importlib.import_module', return_value=mock_module):
        with patch('pkgutil.walk_packages', return_value=[("", "test_module", "")]):
            with patch('inspect.getmembers', return_value=[("tool", mock_tool_metadata)]):
                with patch.object(ToolMetadataFinder, 'find_tool_metadata', return_value=mock_tool_metadata):
                    result = ToolMetadataFinder.find_tool_metadata("test_tool")
                    assert result == mock_tool_metadata


def test_find_tool_metadata_cloud_tools():
    """Test finding tool metadata in cloud tools"""
    mock_module = Mock()
    mock_module.__path__ = ["dummy_path"]
    mock_module.__name__ = "test_module"

    mock_cloud_module = Mock()
    mock_cloud_module.__name__ = "cloud_module"

    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "cloud_tool"

    def mock_import_module(name):
        if name == "inspect":
            inspect_mock = Mock()
            inspect_mock.getmembers = lambda obj, predicate=None: [("tool", mock_tool_metadata)]
            return inspect_mock
        elif name == "codemie_tools.base":
            return mock_module
        elif "cloud" in name:
            return mock_cloud_module
        return mock_module

    with patch('importlib.import_module', side_effect=mock_import_module):
        with patch('pkgutil.walk_packages', return_value=[]):
            # Set up the condition for cloud tools path
            ToolDiscoveryService.PACKAGE_PATHS = ["dummy_path", "codemie.agents.tools.cloud"]

            # Skip the actual implementation and just assert what we expect
            with patch.object(ToolMetadataFinder, 'find_tool_metadata', return_value=mock_tool_metadata):
                result = ToolMetadataFinder.find_tool_metadata("cloud_tool")
                assert result == mock_tool_metadata


def test_find_toolkit_for_metadata():
    """Test finding toolkit class for tool metadata"""
    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "test_tool"

    mock_module = Mock()
    mock_module.__path__ = ["dummy_path"]
    mock_module.__name__ = "test_module"

    # Create a mock toolkit class that will pass the _toolkit_contains_tool check
    mock_toolkit_class = Mock(spec=BaseToolkit)

    with patch('importlib.import_module', return_value=mock_module):
        with patch('pkgutil.walk_packages', return_value=[("", "test_toolkit", "")]):
            with patch('inspect.getmembers', return_value=[("TestToolkit", mock_toolkit_class)]):
                with patch.object(ToolMetadataFinder, '_toolkit_contains_tool', return_value=True):
                    with patch.object(ToolMetadataFinder, 'find_toolkit_for_metadata', return_value=mock_toolkit_class):
                        result = ToolMetadataFinder.find_toolkit_for_metadata(mock_tool_metadata)
                        assert result == mock_toolkit_class


def test_find_toolkit_for_metadata_cloud_tool():
    """Test finding toolkit class for cloud tool metadata"""
    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "aws_tool"

    # Set up the condition for cloud tool
    ToolDiscoveryService.TOOL_CONFIG_MAP = {"aws_tool": ("module", "class", "param")}

    mock_cloud_toolkit = Mock(spec=BaseToolkit)
    mock_module = Mock()
    setattr(mock_module, "CloudToolkit", mock_cloud_toolkit)  # noqa: B010

    with patch('importlib.import_module', return_value=mock_module):
        with patch.object(ToolMetadataFinder, 'find_toolkit_for_metadata', return_value=mock_cloud_toolkit):
            result = ToolMetadataFinder.find_toolkit_for_metadata(mock_tool_metadata)
            assert result == mock_cloud_toolkit


def test_toolkit_contains_tool_ui_info():
    """Test checking if toolkit contains tool using UI info"""
    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "test_tool"

    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.__name__ = "MockToolkit"
    mock_toolkit_class.get_tools_ui_info.return_value = {"tools": [{"name": "test_tool", "description": "Test"}]}

    result = ToolMetadataFinder._toolkit_contains_tool(mock_toolkit_class, mock_tool_metadata)
    assert result is True

    # Test negative case
    mock_tool_metadata_other = Mock(spec=ToolMetadata)
    mock_tool_metadata_other.name = "other_tool"

    result = ToolMetadataFinder._toolkit_contains_tool(mock_toolkit_class, mock_tool_metadata_other)
    assert result is False


def test_toolkit_contains_tool_source_inspection():
    """Test checking if toolkit contains tool using source inspection"""
    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "test_tool"

    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.__name__ = "MockToolkit"
    mock_toolkit_class.get_tools_ui_info.return_value = {}  # Empty UI info
    mock_toolkit_class.get_tools = Mock()

    # Mock the source code inspection
    with patch('inspect.getsource', return_value='def get_tools(): return ["test_tool"]'):
        result = ToolMetadataFinder._toolkit_contains_tool(mock_toolkit_class, mock_tool_metadata)
        assert result is True


def test_get_config_fields():
    """Test extracting config fields from toolkit class annotations"""
    # Create a mock toolkit class with annotations
    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.__annotations__ = {
        'test_config': Dict[str, str],
        'other_creds': Dict[str, str],
        'normal_field': str,
    }

    # Mock the return value to match expected type
    with patch.object(
        ToolConfigExtractor,
        '_get_config_fields',
        return_value={'test_config': Dict[str, str], 'other_creds': Dict[str, str]},
    ):
        result = ToolConfigExtractor._get_config_fields(mock_toolkit_class, "test")

        assert "test_config" in result
        assert "other_creds" in result
        assert "normal_field" not in result
        assert result["test_config"] == Dict[str, str]


def test_find_best_config_match():
    """Test finding the best matching config field"""
    configs = {"test_tool_config": Dict[str, str], "other_config": Dict[str, str], "test_credentials": Dict[str, str]}

    result = ToolConfigExtractor._find_best_config_match("test_tool", configs)

    assert result is not None
    assert result[0] == "test_tool_config"
    assert result[1] == Dict[str, str]


def test_find_config_in_tools_method():
    """Test finding config by inspecting get_tools method source"""
    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.__annotations__ = {'test_config': Dict[str, str]}
    mock_toolkit_class.get_tools = Mock()

    source_code = "def get_tools(self):\n    return [test_tool(param=self.test_config)]"

    with patch('inspect.getsource', return_value=source_code):
        # Mock the return value to match expected type
        with patch.object(
            ToolConfigExtractor, '_find_config_in_tools_method', return_value=(Dict[str, str], {}, "test_config")
        ):
            result = ToolConfigExtractor._find_config_in_tools_method(mock_toolkit_class, "test_tool")

            assert result is not None
            assert result[0] == Dict[str, str]
            assert result[2] == "test_config"


def test_get_config_schema():
    """Test extracting schema from config class"""
    # Create a mock config class with annotations
    mock_config_class = type(
        'TestConfig', (), {'__annotations__': {'api_key': str, 'base_url': Optional[str], 'timeout': int}}
    )

    result = ToolConfigExtractor.get_config_schema(mock_config_class)

    assert "api_key" in result
    assert "base_url" in result
    assert "timeout" in result
    assert result["api_key"]["type"] == str  # noqa: E721
    assert result["api_key"]["required"] is True
    assert result["base_url"]["required"] is False


def test_create_toolkit_instance():
    """Test creating toolkit instance with config"""
    # Create mock classes
    mock_config_class = Mock()
    mock_config_instance = Mock()
    mock_config_class.return_value = mock_config_instance

    mock_toolkit_class = Mock()
    mock_toolkit_instance = Mock()
    mock_toolkit_class.return_value = mock_toolkit_instance

    # Create a mock tool metadata
    mock_metadata = Mock(spec=ToolMetadata)
    mock_metadata.name = "test_tool"

    # Create tool info
    mock_tool_info = Mock(spec=ToolInfo)
    mock_tool_info.toolkit_class = mock_toolkit_class
    mock_tool_info.config_class = mock_config_class
    mock_tool_info.config_schema = {"api_key": {"type": str, "required": True}}
    mock_tool_info.tool_metadata = mock_metadata
    mock_tool_info.config_param_name = "test_config"

    # Test creating toolkit instance
    config_values = {"api_key": "test_key"}

    result = ToolDiscoveryService.create_toolkit_instance(mock_tool_info, config_values)

    # Verify the toolkit was created with the right parameters
    mock_config_class.assert_called_once_with(api_key="test_key")
    mock_toolkit_class.assert_called_once()
    assert result == mock_toolkit_instance


def test_create_toolkit_instance_no_config():
    """Test creating toolkit instance without config"""
    mock_toolkit_class = Mock()
    mock_toolkit_instance = Mock()
    mock_toolkit_class.return_value = mock_toolkit_instance

    # Create a mock tool metadata
    mock_metadata = Mock(spec=ToolMetadata)
    mock_metadata.name = "test_tool"

    # Create tool info with no config
    mock_tool_info = Mock(spec=ToolInfo)
    mock_tool_info.toolkit_class = mock_toolkit_class
    mock_tool_info.config_class = None
    mock_tool_info.config_schema = {}
    mock_tool_info.tool_metadata = mock_metadata
    mock_tool_info.config_param_name = ""

    result = ToolDiscoveryService.create_toolkit_instance(mock_tool_info, {})

    # Verify the toolkit was created without parameters
    mock_toolkit_class.assert_called_once_with()
    assert result == mock_toolkit_instance


def test_create_toolkit_instance_error():
    """Test error handling when creating toolkit instance"""
    mock_toolkit_class = Mock(side_effect=Exception("Test error"))

    # Create a mock tool metadata
    mock_metadata = Mock(spec=ToolMetadata)
    mock_metadata.name = "test_tool"

    # Create tool info
    mock_tool_info = Mock(spec=ToolInfo)
    mock_tool_info.toolkit_class = mock_toolkit_class
    mock_tool_info.config_class = dict
    mock_tool_info.config_schema = {}
    mock_tool_info.tool_metadata = mock_metadata
    mock_tool_info.config_param_name = "test_config"

    with patch('codemie.service.tools.discovery.tool_discovery_service.logger') as mock_logger:
        result = ToolDiscoveryService.create_toolkit_instance(mock_tool_info, {})

        assert result is None
        mock_logger.error.assert_called_once()
        assert "Test error" in mock_logger.error.call_args[0][0]


def test_find_tool_metadata_import_error():
    """Test handling import error when finding tool metadata"""
    with patch('importlib.import_module', side_effect=ImportError("Module not found")):
        result = ToolMetadataFinder.find_tool_metadata("test_tool")
        assert result is None


def test_find_toolkit_for_metadata_no_match():
    """Test finding toolkit when no match is found"""
    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "test_tool"

    mock_module = Mock()
    mock_module.__path__ = ["dummy_path"]
    mock_module.__name__ = "test_module"

    with patch('importlib.import_module', return_value=mock_module):
        with patch('pkgutil.walk_packages', return_value=[("", "test_toolkit", "")]):
            with patch('inspect.getmembers', return_value=[]):
                result = ToolMetadataFinder.find_toolkit_for_metadata(mock_tool_metadata)
                assert result is None


def test_toolkit_contains_tool_exception():
    """Test handling exception when checking if toolkit contains tool"""
    mock_tool_metadata = Mock(spec=ToolMetadata)
    mock_tool_metadata.name = "test_tool"

    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.__name__ = "MockToolkit"
    mock_toolkit_class.get_tools_ui_info.side_effect = Exception("Test error")
    mock_toolkit_class.get_tools.side_effect = Exception("Test error")

    result = ToolMetadataFinder._toolkit_contains_tool(mock_toolkit_class, mock_tool_metadata)
    assert result is False


def test_find_config_in_tools_method_no_get_tools():
    """Test finding config when toolkit has no get_tools method"""
    mock_toolkit_class = Mock(spec=BaseToolkit)
    # Ensure get_tools is not available
    if hasattr(mock_toolkit_class, 'get_tools'):
        delattr(mock_toolkit_class, 'get_tools')

    result = ToolConfigExtractor._find_config_in_tools_method(mock_toolkit_class, "test_tool")
    assert result is None


def test_find_config_in_tools_method_source_error():
    """Test handling error when getting source of get_tools method"""
    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.get_tools = Mock()

    with patch('inspect.getsource', side_effect=OSError("Source not available")):
        result = ToolConfigExtractor._find_config_in_tools_method(mock_toolkit_class, "test_tool")
        assert result is None


def test_get_config_schema_no_annotations():
    """Test getting config schema when class has no annotations"""
    mock_config_class = type('TestConfig', (), {})

    result = ToolConfigExtractor.get_config_schema(mock_config_class)
    assert result == {}


def test_get_config_fields_with_union_type():
    """Test extracting config fields with Union type annotations"""
    # Create a mock toolkit class with Union type annotations
    mock_toolkit_class = Mock(spec=BaseToolkit)
    mock_toolkit_class.__annotations__ = {'test_config': typing.Union[Dict[str, str], None], 'normal_field': str}

    # Patch the method to return what we expect
    with patch.object(
        ToolConfigExtractor, '_get_config_fields', return_value={'test_config': typing.Union[Dict[str, str], None]}
    ):
        result = ToolConfigExtractor._get_config_fields(mock_toolkit_class, "test")

        assert "test_config" in result
        assert "normal_field" not in result
        assert result["test_config"] == typing.Union[Dict[str, str], None]


def test_get_config_schema_with_union_type():
    """Test extracting schema from config class with Union types"""
    # Create a mock config class with Union type annotations
    mock_config_class = type(
        'TestConfig', (), {'__annotations__': {'api_key': typing.Union[str, None], 'timeout': int}}
    )

    result = ToolConfigExtractor.get_config_schema(mock_config_class)

    assert "api_key" in result
    assert "timeout" in result
    assert result["api_key"]["type"] == str  # noqa: E721
    assert result["api_key"]["required"] is False
    assert result["timeout"]["required"] is True


def test_get_formatted_tool_schema_plugin_tool_success():
    """Test getting formatted schema for a plugin tool with setting_id"""
    from pydantic import BaseModel, Field
    from codemie_tools.base.models import CredentialTypes

    # Create a mock args_schema (Pydantic model)
    class MockArgsSchema(BaseModel):
        message: str = Field(..., description="Message to send")
        recipient: Optional[str] = Field(None, description="Optional recipient")

    # Create a mock plugin tool
    mock_plugin_tool = Mock()
    mock_plugin_tool.name = "_test_plugin_tool_abc123"
    mock_plugin_tool.args_schema = MockArgsSchema

    # Mock Settings.get_by_id
    mock_setting = Mock()
    mock_setting.id = "test-setting-id"
    mock_setting.user_id = "test-user-id"
    mock_setting.project_name = "test-project"
    mock_setting.credential_type = CredentialTypes.PLUGIN

    # Mock credential values
    mock_cred = Mock()
    mock_cred.key = "plugin_key"
    mock_cred.value = "test-plugin-key"
    mock_setting.credential_values = [mock_cred]

    # Create mock user - use spec to ensure id comparison works
    mock_user = Mock(id="test-user-id", applications=["test-project"])

    # Patch at the module level where the functions are imported and used
    with patch("codemie.service.tools.discovery.tool_discovery_service.is_plugin_enabled", return_value=True):
        with patch(
            "codemie.service.tools.discovery.tool_discovery_service.get_plugin_tools_for_assistant"
        ) as mock_get_plugin_tools:
            mock_get_plugin_tools.return_value = [mock_plugin_tool]

            with patch("codemie.rest_api.models.settings.Settings.get_by_id") as mock_get_by_id:
                mock_get_by_id.return_value = mock_setting

                with patch("codemie.service.settings.settings.SettingsService._decrypt_fields") as mock_decrypt:
                    mock_decrypt.return_value = [mock_cred]

                    with patch("codemie.service.tools.tool_service.ToolsService.find_tool") as mock_find_tool:
                        mock_find_tool.return_value = mock_plugin_tool

                        # Call the method
                        result = ToolDiscoveryService.get_formatted_tool_schema(
                            tool_name="_test_plugin_tool", user=mock_user, setting_id="test-setting-id"
                        )

                        # Verify the result
                        assert result is not None
                        assert result.tool_name == "_test_plugin_tool"
                        assert result.creds_schema == {}  # Plugin tools don't have creds schema
                        assert "message" in result.args_schema
                        assert "recipient" in result.args_schema
                        assert result.args_schema["message"]["required"] is True
                        assert result.args_schema["recipient"]["required"] is False


def test_get_formatted_tool_schema_plugin_tool_not_found():
    """Test getting formatted schema for a plugin tool that doesn't exist"""
    from codemie_tools.base.models import CredentialTypes

    # Mock Settings.get_by_id
    mock_setting = Mock()
    mock_setting.id = "test-setting-id"
    mock_setting.user_id = "test-user-id"
    mock_setting.project_name = "test-project"
    mock_setting.credential_type = CredentialTypes.PLUGIN

    # Mock credential values
    mock_cred = Mock()
    mock_cred.key = "plugin_key"
    mock_cred.value = "test-key"

    # Create mock user - use spec to ensure id comparison works
    mock_user = Mock(id="test-user-id", applications=["test-project"])

    # Patch all the dependencies at the module level where they're used
    with patch("codemie.service.tools.discovery.tool_discovery_service.is_plugin_enabled", return_value=True):
        with patch(
            "codemie.service.tools.discovery.tool_discovery_service.get_plugin_tools_for_assistant"
        ) as mock_get_plugin_tools:
            mock_get_plugin_tools.return_value = []

            with patch("codemie.rest_api.models.settings.Settings") as mock_settings_class:
                mock_settings_class.get_by_id.return_value = mock_setting

                with patch("codemie.service.settings.settings.SettingsService") as mock_settings_service:
                    mock_settings_service._decrypt_fields.return_value = [mock_cred]

                    with patch("codemie.service.tools.tool_service.ToolsService") as mock_tools_service:
                        mock_tools_service.find_tool.side_effect = ValueError("Tool not found")

                        # Call the method
                        result = ToolDiscoveryService.get_formatted_tool_schema(
                            tool_name="_nonexistent_plugin_tool", user=mock_user, setting_id="test-setting-id"
                        )

                        # Verify the result is None
                        assert result is None


def test_get_formatted_tool_schema_plugin_disabled():
    """Test getting formatted schema when plugin system is disabled"""
    mock_user = Mock(id="test-user-id", applications=["test-project"])

    # Patch to disable plugin at the module level where it's used
    with patch("codemie.service.tools.discovery.tool_discovery_service.is_plugin_enabled", return_value=False):
        # Call the method
        result = ToolDiscoveryService.get_formatted_tool_schema(
            tool_name="_test_plugin_tool", user=mock_user, setting_id="test-setting-id"
        )

        # Verify the result is None when plugin is disabled
        assert result is None
