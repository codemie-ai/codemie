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

from unittest import mock

import pytest

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import Tool, CodeMieToolConfig, ToolKit
from codemie_tools.base.toolkit_provider import (
    get_available_toolkits,
    get_available_toolkits_info,
    get_tool,
    get_tools,
    get_toolkit,
    get_available_tools_configs,
    get_available_tools_configs_info,
    is_toolkit_class,
    is_config_class,
    _find_toolkits,
    _find_tool_configs,
)


@pytest.fixture(autouse=True)
def clear_lru_caches():
    """Clear all LRU caches before each test to ensure test isolation."""
    from codemie_tools.base.toolkit_provider import (
        get_available_toolkits,
        get_available_toolkits_info,
        get_tool,
        get_tools,
        get_toolkit,
        get_available_tools_configs,
        get_available_tools_configs_info,
    )

    get_available_toolkits.cache_clear()
    get_available_toolkits_info.cache_clear()
    get_tool.cache_clear()
    get_tools.cache_clear()
    get_toolkit.cache_clear()
    get_available_tools_configs.cache_clear()
    get_available_tools_configs_info.cache_clear()
    yield


@pytest.fixture
def mock_toolkit_class():
    """Create a mock toolkit class for testing."""

    class MockToolkit(DiscoverableToolkit):
        @classmethod
        def get_definition(cls):
            return ToolKit(
                toolkit="mock_toolkit",
                tools=[
                    Tool(name="mock_tool_1", user_description="Mock tool 1"),
                    Tool(name="mock_tool_2", user_description="Mock tool 2"),
                ],
            )

    return MockToolkit


@pytest.fixture
def mock_toolkit_class_no_definition():
    """Create a mock toolkit class that returns None from get_definition."""

    class MockToolkitNoDefinition(DiscoverableToolkit):
        @classmethod
        def get_definition(cls):
            return None

    return MockToolkitNoDefinition


@pytest.fixture
def mock_toolkit_class_error():
    """Create a mock toolkit class that raises an exception from get_definition."""

    class MockToolkitError(DiscoverableToolkit):
        @classmethod
        def get_definition(cls):
            raise ValueError("Test error")

    return MockToolkitError


@pytest.fixture
def mock_config_class():
    """Create a mock config class for testing."""

    class MockConfig(CodeMieToolConfig):
        pass

    return MockConfig


def test_is_toolkit_class(mock_toolkit_class):
    """Test the is_toolkit_class function."""
    # Test with a valid toolkit class
    assert is_toolkit_class(mock_toolkit_class) is True

    # Test with DiscoverableToolkit itself
    assert is_toolkit_class(DiscoverableToolkit) is False

    # Test with non-toolkit class
    assert is_toolkit_class(object) is False

    # Test with non-class objects
    assert is_toolkit_class("not a class") is False
    assert is_toolkit_class(None) is False


def test_is_config_class(mock_config_class):
    """Test the is_config_class function."""
    # Test with a valid config class
    assert is_config_class(mock_config_class) is True

    # Test with CodeMieToolConfig itself
    assert is_config_class(CodeMieToolConfig) is False

    # Test with non-config class
    assert is_config_class(object) is False

    # Test with non-class objects
    assert is_config_class("not a class") is False
    assert is_config_class(None) is False


@mock.patch('codemie_tools.base.toolkit_provider._find_toolkits')
def test_get_available_toolkits(mock_find_toolkits, mock_toolkit_class):
    """Test that get_available_toolkits returns the expected toolkits."""
    # Set up the mock to return our test toolkit
    mock_find_toolkits.return_value = [mock_toolkit_class]

    # Call the function
    toolkits = get_available_toolkits()

    # Verify the result
    assert len(toolkits) == 1
    assert toolkits[0] == mock_toolkit_class

    # Verify the mock was called
    mock_find_toolkits.assert_called_once()

    # Call again to test caching
    toolkits_cached = get_available_toolkits()

    # Verify the result is the same
    assert toolkits_cached == toolkits

    # Verify the mock was not called again (due to caching)
    assert mock_find_toolkits.call_count == 1


@mock.patch('codemie_tools.base.toolkit_provider._find_tool_configs')
def test_get_available_tools_configs(mock_find_tool_configs, mock_config_class):
    """Test that get_available_tools_configs returns the expected configs."""
    # Set up the mock to return our test config
    mock_find_tool_configs.return_value = [mock_config_class]

    # Call the function
    configs = get_available_tools_configs()

    # Verify the result
    assert len(configs) == 1
    assert configs[0] == mock_config_class

    # Verify the mock was called
    mock_find_tool_configs.assert_called_once()

    # Call again to test caching
    configs_cached = get_available_tools_configs()

    # Verify the result is the same
    assert configs_cached == configs

    # Verify the mock was not called again (due to caching)
    assert mock_find_tool_configs.call_count == 1


@mock.patch('codemie_tools.base.toolkit_provider.get_available_tools_configs')
def test_get_available_tools_configs_info(mock_get_available_tools_configs, mock_config_class):
    """Test that get_available_tools_configs_info returns a list of dictionaries with tool config information."""
    # Set up the mock to return our test config class
    mock_get_available_tools_configs.return_value = [mock_config_class]

    # Call the function
    configs_info = get_available_tools_configs_info()

    # Verify the result
    assert len(configs_info) == 1

    # Check that the config info has the expected structure
    config_item = configs_info[0]
    assert isinstance(config_item, dict)

    # Get the config name and data
    config_name, config_data = next(iter(config_item.items()))

    # Check that the config name is lowercase
    assert config_name == 'mockconfig'

    # Check that the class field is present and correct
    assert 'class' in config_data
    assert config_data['class'].endswith('MockConfig')

    # Call again to test caching
    configs_info_cached = get_available_tools_configs_info()

    # Verify the result is the same
    assert configs_info_cached == configs_info

    # Verify the mock was not called again (due to caching)
    assert mock_get_available_tools_configs.call_count == 1


@mock.patch('codemie_tools.base.toolkit_provider.get_available_toolkits')
def test_get_available_toolkits_info(
    mock_get_available_toolkits, mock_toolkit_class, mock_toolkit_class_no_definition, mock_toolkit_class_error
):
    """Test that get_available_toolkits_info returns a list of dictionaries with toolkit information."""
    # Set up the mock to return our test toolkits
    mock_get_available_toolkits.return_value = [
        mock_toolkit_class,
        mock_toolkit_class_no_definition,
        mock_toolkit_class_error,
    ]

    # Call the function
    toolkits_info = get_available_toolkits_info()

    # Verify the result
    assert len(toolkits_info) == 1  # Only one toolkit has a valid definition

    # Check that the toolkit info has the expected structure
    toolkit_info = toolkits_info[0]
    assert isinstance(toolkit_info, dict)
    assert toolkit_info['toolkit'] == "mock_toolkit"
    assert len(toolkit_info['tools']) == 2
    assert 'class_name' in toolkit_info

    # Check that the tools have the expected structure
    for tool in toolkit_info['tools']:
        assert 'name' in tool
        assert tool['name'] in ["mock_tool_1", "mock_tool_2"]
        assert 'user_description' in tool


@mock.patch('codemie_tools.base.toolkit_provider.get_available_toolkits')
def test_get_tools(
    mock_get_available_toolkits, mock_toolkit_class, mock_toolkit_class_no_definition, mock_toolkit_class_error
):
    """Test that get_tools returns all tools from all available toolkits."""
    # Set up the mock to return our test toolkits
    mock_get_available_toolkits.return_value = [
        mock_toolkit_class,
        mock_toolkit_class_no_definition,
        mock_toolkit_class_error,
    ]

    # Call the function
    tools = get_tools()

    # Verify the result
    assert len(tools) == 2  # Only the valid toolkit contributes tools
    assert tools[0].name == "mock_tool_1"
    assert tools[1].name == "mock_tool_2"

    # Call again to test caching
    tools_cached = get_tools()

    # Verify the result is the same
    assert tools_cached == tools

    # Verify the mock was not called again (due to caching)
    assert mock_get_available_toolkits.call_count == 1


def test_get_tool():
    """Test that get_tool returns the correct tool by name."""
    # Create test tools
    test_tools = [
        Tool(name="test_tool_1", user_description="Test tool 1"),
        Tool(name="test_tool_2", user_description="Test tool 2"),
    ]

    # Use a context manager to patch get_tools for this specific test
    with mock.patch('codemie_tools.base.toolkit_provider.get_tools', return_value=test_tools) as mock_get_tools:
        # Test getting an existing tool
        tool = get_tool("test_tool_1")
        assert tool is not None
        assert tool.name == "test_tool_1"

        # Test getting a non-existent tool with raise_error=False (default)
        non_existent_tool = get_tool("non_existent_tool")
        assert non_existent_tool is None

        # Test getting a non-existent tool with raise_error=True
        with pytest.raises(ValueError, match="No tool found with name: non_existent_tool"):
            get_tool("non_existent_tool", raise_error=True)

        # Verify the mock was called
        assert mock_get_tools.call_count > 0


def test_get_toolkit(mock_toolkit_class):
    """Test that get_toolkit returns the correct toolkit by name."""
    # Use a context manager to patch get_available_toolkits for this specific test
    with mock.patch(
        'codemie_tools.base.toolkit_provider.get_available_toolkits', return_value=[mock_toolkit_class]
    ) as mock_get_available_toolkits:
        # Test getting an existing toolkit
        toolkit = get_toolkit("mock_toolkit")
        assert toolkit == mock_toolkit_class

        # Test getting a non-existent toolkit
        with pytest.raises(ValueError, match="No toolkit found with name: non_existent_toolkit"):
            get_toolkit("non_existent_toolkit", raise_error=True)

        # Verify the mock was called
        assert mock_get_available_toolkits.call_count > 0


@mock.patch('importlib.import_module')
@mock.patch('pkgutil.iter_modules')
@mock.patch('inspect.getmembers')
@mock.patch('codemie_tools.base.toolkit_provider.logger')
def test_find_toolkits(mock_logger, mock_getmembers, mock_iter_modules, mock_import_module, mock_toolkit_class):
    """Test that _find_toolkits correctly finds toolkit classes."""
    # Set up the mock module
    mock_module = mock.MagicMock()
    mock_module.__path__ = ['/mock/path']
    mock_import_module.return_value = mock_module

    # Set up the mock to return our test toolkit class
    mock_module.__name__ = 'mock_module'

    # Mock the inspect.getmembers function to return our test toolkit
    mock_getmembers.return_value = [('MockToolkit', mock_toolkit_class)]

    # No submodules
    mock_iter_modules.return_value = []

    # Call the function
    toolkits = _find_toolkits()

    # Verify the result
    assert len(toolkits) == 1
    assert toolkits[0] == mock_toolkit_class


@mock.patch('importlib.import_module')
@mock.patch('pkgutil.iter_modules')
@mock.patch('inspect.getmembers')
@mock.patch('codemie_tools.base.toolkit_provider.logger')
def test_find_tool_configs(mock_logger, mock_getmembers, mock_iter_modules, mock_import_module, mock_config_class):
    """Test that _find_tool_configs correctly finds config classes."""
    # Set up the mock module
    mock_module = mock.MagicMock()
    mock_module.__path__ = ['/mock/path']
    mock_import_module.return_value = mock_module

    # Set up the mock to return our test config class
    mock_module.__name__ = 'mock_module'

    # Mock the inspect.getmembers function to return our test config
    mock_getmembers.return_value = [('MockConfig', mock_config_class)]

    # No submodules
    mock_iter_modules.return_value = []

    # Call the function
    configs = _find_tool_configs()

    # Verify the result
    assert len(configs) == 1
    assert configs[0] == mock_config_class


@mock.patch('importlib.import_module')
@mock.patch('codemie_tools.base.toolkit_provider.logger')
def test_find_toolkits_import_error(mock_logger, mock_import_module):
    """Test that _find_toolkits handles ImportError gracefully."""
    # Set up the mock to raise ImportError
    mock_import_module.side_effect = ImportError("Test import error")

    # Call the function
    toolkits = _find_toolkits()

    # Verify the result
    assert len(toolkits) == 0


def test_get_tools_with_error_in_toolkit(mock_toolkit_class_error):
    """Test that get_tools handles errors in toolkit.get_definition() gracefully."""
    # Use a context manager to patch get_available_toolkits and logger.error
    with (
        mock.patch(
            'codemie_tools.base.toolkit_provider.get_available_toolkits', return_value=[mock_toolkit_class_error]
        ),
        mock.patch('codemie_tools.base.toolkit_provider.logger.error') as mock_logger_error,
    ):
        # Call the function
        tools = get_tools()

        # Verify the result - should be empty since the toolkit raises an error
        assert len(tools) == 0

        # Verify that the error was logged
        assert mock_logger_error.called


def test_integration_get_available_toolkits():
    """Integration test for get_available_toolkits."""
    # This is an integration test that uses the actual implementation
    toolkits = get_available_toolkits()

    # Verify that we got some toolkits
    assert len(toolkits) > 0

    # Verify that all items are BaseToolkit subclasses
    for toolkit in toolkits:
        assert issubclass(toolkit, DiscoverableToolkit)
        assert toolkit != DiscoverableToolkit


def test_integration_get_available_tools_configs_info():
    """Integration test for get_available_tools_configs_info."""
    # This is an integration test that uses the actual implementation
    configs_info = get_available_tools_configs_info()

    # Verify that we got some config info
    assert len(configs_info) >= 0  # May be 0 if no configs are defined

    # Verify that all items are dictionaries with a single key-value pair
    for config_item in configs_info:
        assert isinstance(config_item, dict)
        assert len(config_item) == 1  # Each item should have exactly one key-value pair

        # Get the config name and data
        config_name, config_data = next(iter(config_item.items()))

        # Check that the config name is a string
        assert isinstance(config_name, str)

        # Check that the config data is a dictionary with the class field
        assert isinstance(config_data, dict)
        assert 'class' in config_data


def test_scan_recursively_with_seen_module():
    """Test that _scan_recursively skips modules that have already been seen."""
    # Create test data
    seen_modules = {'already_seen_module'}
    seen_toolkit_ids = set()
    toolkits = []

    # Call the function with a module that's already in seen_modules
    from codemie_tools.base.toolkit_provider import _scan_recursively

    _scan_recursively('already_seen_module', seen_modules, seen_toolkit_ids, toolkits)

    # Verify that seen_modules hasn't changed (the function returned early)
    assert seen_modules == {'already_seen_module'}
    assert len(toolkits) == 0


def test_scan_config_recursively_with_seen_module():
    """Test that _scan_config_recursively skips modules that have already been seen."""
    # Create test data
    seen_modules = {'already_seen_module'}
    seen_config_ids = set()
    configs = []

    # Call the function with a module that's already in seen_modules
    from codemie_tools.base.toolkit_provider import _scan_config_recursively

    _scan_config_recursively('already_seen_module', seen_modules, seen_config_ids, configs)

    # Verify that seen_modules hasn't changed (the function returned early)
    assert seen_modules == {'already_seen_module'}
    assert len(configs) == 0


def test_integration_get_tools():
    """Integration test for get_tools."""
    # This is an integration test that uses the actual implementation
    tools = get_tools()

    # Verify that we got some tools
    assert len(tools) > 0

    # Verify that all items are Tool instances
    for tool in tools:
        assert isinstance(tool, Tool)
        assert tool.name  # Name should not be empty


@mock.patch('codemie_tools.base.toolkit_provider.logger')
def test_scan_submodules_generic(mock_logger):
    """Test the _scan_submodules_generic function."""
    # Create a mock module with __path__ attribute
    mock_module = mock.MagicMock()
    mock_module.__path__ = ['/mock/path']
    mock_module.__name__ = 'mock_module'

    # Create mock scan function
    mock_scan_func = mock.MagicMock()

    # Create mock pkgutil.iter_modules
    with mock.patch('pkgutil.iter_modules', return_value=[(None, 'mock_submodule', None)]) as mock_iter_modules:
        # Call the function
        from codemie_tools.base.toolkit_provider import _scan_submodules_generic

        _scan_submodules_generic(mock_module, set(), set(), [], mock_scan_func)

        # Verify that iter_modules was called with the module's __path__
        mock_iter_modules.assert_called_once_with(mock_module.__path__, mock_module.__name__ + '.')

        # Verify that the scan function was called with the submodule name
        mock_scan_func.assert_called_once_with('mock_submodule', set(), set(), [])


def test_integration_get_tool():
    """Integration test for get_tool."""
    # Get all tools
    all_tools = get_tools()

    # Make sure we have tools to test with
    assert len(all_tools) > 0

    # Get the first tool's name
    first_tool_name = all_tools[0].name

    # Get the tool by name
    tool = get_tool(first_tool_name, raise_error=True)

    # Check that we got the right tool
    assert tool.name == first_tool_name

    # Test with a non-existent tool name
    with pytest.raises(ValueError):
        get_tool('non_existent_tool_name_12345', raise_error=True)

    # Test with raise_error=False - should return None
    non_existent_tool = get_tool('non_existent_tool_name_12345')
    assert non_existent_tool is None
