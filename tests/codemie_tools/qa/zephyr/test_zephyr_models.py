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

from codemie_tools.qa.zephyr.models import ZephyrConfig


def test_zephyr_url_defaults_to_empty_when_unconfigured():
    """url defaults to empty string when no tool_defaults configured; app enforces required at use time."""
    config = ZephyrConfig(token="mytoken")
    assert config.url == ""


def test_zephyr_url_placeholder_in_schema():
    schema = ZephyrConfig.model_json_schema()
    assert (
        schema["properties"]["url"].get("placeholder")
        == "URL, e.g. https://prod-api.zephyr4jiracloud.com/v2 or https://api.zephyrscale.smartbear.com/v2"
    )


def test_zephyr_valid_config():
    config = ZephyrConfig(url="https://api.zephyrscale.smartbear.com/v2", token="mytoken")
    assert config.url == "https://api.zephyrscale.smartbear.com/v2"
