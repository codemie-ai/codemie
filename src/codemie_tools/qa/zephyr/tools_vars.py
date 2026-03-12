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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.qa.zephyr.models import ZephyrConfig

ZEPHYR_TOOL = ToolMetadata(
    name="ZephyrScale",
    description="""
    Zephyr Tool that provides access to the zephyr-python-api package, enabling interaction with Zephyr test cases,
    cycles or executions etc.
    You must provide the following args: entity_str, method_str and body.
    1. 'entity_str': ZephyrScale entity requested by user. It must be accessible from ZephyrScale().api
    2. 'method_str': Zephyr method that is accessible from that entity. E.g. method 'get_test_cases' is accessible from ZephyrScale().api.test_cases.
        It can be equal to 'dir' so that you can list all available methods in requested entity.
    3. 'body': (Optional) Valid JSON object with parameters that must be passed to method.
    If some required information is not provided by user, try find by querying API, if not found ask user.
    If method you requested is not exists, try to execute tool with method 'dir' to get list of available methods.
    """.strip(),
    label="Zephyr Scale",
    user_description="""
    Provides access to the zephyr-python-api package, enabling interaction with Zephyr test cases, cycles or executions.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the Zephyr Scale integration)
    2. Url (base url of the Zephyr Scale)
    3. Token (API access token)
    Usage Note:
    Use this tool when you need to manage Zephyr test cases, cycles or executions.
    """.strip(),
    settings_config=True,
    config_class=ZephyrConfig,
)
