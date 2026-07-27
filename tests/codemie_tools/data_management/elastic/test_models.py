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

from codemie_tools.data_management.elastic.models import ElasticConfig


def test_elastic_url_placeholder_default():
    schema = ElasticConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == 'Elastic URL, e.g. "https://localhost:9200"'


def test_elastic_url_default_url_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {"elastic": {"url": "https://elastic.company.com"}})
    assert customer_config.get_tool_default("elastic", "url") == "https://elastic.company.com"


def test_url_default_wired_to_tool_default():
    from codemie_tools.data_management.elastic.models import ElasticConfig
    from codemie.configs.customer_config import customer_config

    expected = customer_config.get_tool_default(ElasticConfig.TOOL_NAME, "url") or ""
    assert ElasticConfig.model_fields["url"].default == expected
