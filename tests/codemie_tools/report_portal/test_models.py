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

from codemie_tools.report_portal.models import ReportPortalConfig


def test_reportportal_url_placeholder_default():
    schema = ReportPortalConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "Report Portal URL, e.g. https://reportportal.example.com/"


def test_reportportal_url_default_url_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {"report_portal": {"url": "https://rp.company.com"}})
    assert customer_config.get_tool_default("report_portal", "url") == "https://rp.company.com"


def test_url_default_wired_to_tool_default():
    from codemie_tools.report_portal.models import ReportPortalConfig
    from codemie.configs.customer_config import customer_config

    expected = customer_config.get_tool_default(ReportPortalConfig.TOOL_NAME, "url") or ""
    assert ReportPortalConfig.model_fields["url"].default == expected
