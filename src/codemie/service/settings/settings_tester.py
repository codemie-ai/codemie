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

from typing import Tuple

from codemie_tools.cloud.aws.models import AWSConfig
from codemie_tools.cloud.aws.tools import GenericAWSTool
from codemie_tools.cloud.azure.models import AzureConfig
from codemie_tools.cloud.azure.tools import GenericAzureTool
from codemie_tools.cloud.gcp.models import GCPConfig
from codemie_tools.cloud.gcp.tools import GenericGCPTool
from codemie_tools.cloud.kubernetes.models import KubernetesConfig
from codemie_tools.cloud.kubernetes.tools import GenericKubernetesTool
from codemie_tools.code.models import SonarConfig
from codemie_tools.code.sonar.tools import SonarTool
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
from codemie_tools.git.toolkit import GitToolkit
from codemie_tools.itsm.servicenow.models import ServiceNowConfig
from codemie_tools.itsm.servicenow.tools import ServiceNowTableTool
from codemie_tools.notification.email.models import EmailToolConfig
from codemie_tools.notification.email.tools import EmailTool
from codemie_tools.qa.zephyr.models import ZephyrConfig
from codemie_tools.qa.zephyr.tools import ZephyrGenericTool
from codemie_tools.qa.zephyr_squad.models import ZephyrSquadConfig
from codemie_tools.qa.zephyr_squad.tools import ZephyrSquadGenericTool
from codemie_tools.qa.xray.models import XrayConfig
from codemie_tools.qa.xray.tools import XrayGetTestsTool
from codemie_tools.report_portal.models import ReportPortalConfig
from codemie_tools.report_portal.tools import GetAllLaunchesTool

from codemie.core.models import CodeRepoType
from codemie.rest_api.models.settings import Settings, TestSettingRequest
from codemie_tools.base.models import CredentialTypes
from codemie.service.settings.settings import SettingsService


class SettingsTesterHandlerNotFound(Exception):
    pass


class SettingsTester(SettingsService):
    def __init__(self, request: TestSettingRequest):
        self.credential_type = request.credential_type

        if request.setting_id:
            setting = Settings.get_by_id(request.setting_id)
            credential_values = setting.credential_values
        else:
            credential_values = []

        credential_values = self._overwrite_credential_values(
            credential_values, self._filter_masked_values(request.credential_values)
        )
        credential_values = self._decrypt_fields(credential_values)

        self.credential_values = self._normalize_credential_values(credential_values)

    def test(self) -> Tuple[bool, str]:
        handler = self.handlers.get(self.credential_type)

        if not handler:
            raise SettingsTesterHandlerNotFound(f"Unsupported setting type: {self.credential_type}")

        return handler(self)

    @property
    def handlers(self):
        return {
            CredentialTypes.JIRA: SettingsTester._test_jira,
            CredentialTypes.CONFLUENCE: SettingsTester._test_confluence,
            CredentialTypes.KUBERNETES: SettingsTester._test_kubernetes,
            CredentialTypes.AWS: SettingsTester._test_aws,
            CredentialTypes.GCP: SettingsTester._test_gcp,
            CredentialTypes.AZURE: SettingsTester._test_azure,
            CredentialTypes.EMAIL: SettingsTester._test_email,
            CredentialTypes.ZEPHYR_SCALE: SettingsTester._test_zephyr,
            CredentialTypes.ZEPHYR_SQUAD: SettingsTester._test_zephyr_squad,
            CredentialTypes.XRAY: SettingsTester._test_xray,
            CredentialTypes.SERVICENOW: SettingsTester._test_snow,
            CredentialTypes.GIT: SettingsTester._test_git,
            CredentialTypes.SONAR: SettingsTester._test_sonar,
            CredentialTypes.REPORT_PORTAL: SettingsTester._test_report_portal,
        }

    def _test_snow(self) -> Tuple[bool, str]:
        return ServiceNowTableTool(config=ServiceNowConfig(**self.credential_values)).healthcheck()

    def _test_jira(self) -> Tuple[bool, str]:
        return GenericJiraIssueTool(config=JiraConfig(**self.credential_values)).healthcheck()

    def _test_confluence(self) -> Tuple[bool, str]:
        return GenericConfluenceTool(config=ConfluenceConfig(**self.credential_values)).healthcheck()

    def _test_kubernetes(self) -> Tuple[bool, str]:
        return GenericKubernetesTool(config=KubernetesConfig(**self.credential_values)).healthcheck()

    def _test_aws(self) -> Tuple[bool, str]:
        return GenericAWSTool(config=AWSConfig(**self.credential_values)).healthcheck()

    def _test_gcp(self) -> Tuple[bool, str]:
        return GenericGCPTool(config=GCPConfig(**self.credential_values)).healthcheck()

    def _test_azure(self) -> Tuple[bool, str]:
        return GenericAzureTool(config=AzureConfig(**self.credential_values)).healthcheck()

    def _test_email(self) -> Tuple[bool, str]:
        return EmailTool(config=EmailToolConfig(**self.credential_values)).healthcheck()

    def _test_zephyr(self) -> Tuple[bool, str]:
        return ZephyrGenericTool(config=ZephyrConfig(**self.credential_values)).healthcheck()

    def _test_zephyr_squad(self) -> Tuple[bool, str]:
        return ZephyrSquadGenericTool(config=ZephyrSquadConfig(**self.credential_values)).healthcheck()

    def _test_xray(self) -> Tuple[bool, str]:
        return XrayGetTestsTool(config=XrayConfig(**self.credential_values)).healthcheck()

    def _test_git(self) -> Tuple[bool, str]:
        default_branch = "main"
        repo_link = self.credential_values.get("url")
        credentials = self.credential_values
        repo_type = CodeRepoType.from_link(str(repo_link))
        if repo_type == CodeRepoType.UNKNOWN:
            repo_type = CodeRepoType.from_link_probing(str(repo_link))

        return GitToolkit.git_integration_healthcheck(
            configs={
                "base_branch": default_branch,
                "repo_type": repo_type.value,
                "repo_link": repo_link,
                "token": credentials.get("token"),
                "token_name": credentials.get("name"),
            }
        )

    def _test_sonar(self) -> Tuple[bool, str]:
        """Test Sonar integration using SonarTool instance."""
        return SonarTool(config=SonarConfig(**self.credential_values)).healthcheck()

    def _test_report_portal(self) -> Tuple[bool, str]:
        return GetAllLaunchesTool(config=ReportPortalConfig(**self.credential_values)).healthcheck()

    def _normalize_credential_values(self, credential_values: list) -> dict:
        """Normalize credential values to a dictionary"""
        return {item.key: item.value for item in credential_values}

    def _filter_masked_values(self, credential_values: list) -> list:
        """Filter *masked* values from the list of credential values"""
        filtered = filter(lambda item: item.value != self.MASKED_VALUE, credential_values)
        return list(filtered)

    def _overwrite_credential_values(self, credential_values: list, new_credential_values: list) -> dict:
        """Overwrite existing credential values with new ones"""
        for value in new_credential_values:
            try:
                existing_value = next(item for item in credential_values if item.key == value.key)
                existing_value.value = value.value
            except StopIteration:
                credential_values.append(value)

        return credential_values
