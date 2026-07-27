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


def test_sharepoint_config_is_importable():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    assert SharePointConfig is not None


def test_sharepoint_url_placeholder_in_schema():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    schema = SharePointConfig.model_json_schema()
    assert schema["properties"]["url"].get("placeholder") == "SharePoint URL, e.g. https://yourtenant.sharepoint.com"
    assert schema["properties"]["url"].get("required_at_runtime") is True


def test_sharepoint_credential_type():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig
    from codemie_tools.base.models import CredentialTypes

    config = SharePointConfig(url="https://contoso.sharepoint.com")
    assert config.credential_type == CredentialTypes.SHAREPOINT


def test_sharepoint_url_is_required_at_runtime():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    # default is empty when no tool_defaults configured; app enforces required_at_runtime at use time
    config = SharePointConfig()
    assert config.url == ""
