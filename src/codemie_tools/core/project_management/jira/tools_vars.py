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
from codemie_tools.core.project_management.jira.models import JiraConfig


def get_jira_tool_description(api_version: int = 2):
    if api_version == 2:
        description = (
            "JIRA Tool for Official Atlassian JIRA REST API V2 to call, searching, creating, updating issues, etc."
            "Required args: relative_url, method, params"
            "1. 'method': HTTP method (GET, POST, PUT, DELETE, etc.)"
            "2. 'relative_url': JIRA API URI starting with '/rest/api/2/...' (no query params in URL)"
            "3. 'params': Optional request body/query params as stringified JSON"
        )
    elif api_version == 3:
        description = (
            "JIRA Tool for Official Atlassian JIRA REST API V3 to call, searching, creating, updating issues, etc."
            "Required args: relative_url, method, params"
            "1. 'method': HTTP method (GET, POST, PUT, DELETE, etc.)"
            "2. 'relative_url': JIRA API URI starting with '/rest/api/3/...' (no query params in URL)"
            "3. 'params': Optional request body/query params as stringified JSON"
            "4. IMPORTANT: For issue search you should use /rest/api/3/search/jql, because /rest/api/3/search is deprecated endpoint"
        )
    else:
        raise ValueError(f"Wrong API version, required 2 or 3, given is: {api_version}")

    description += """
    Key behaviors:
    - Get minimum required fields for search/read operations unless user requests more
    - Query API for missing required info, ask user if not found
    - For status updates: get available statuses first, compare with user input
    - File attachments: To attach files to an issue, use POST method with '/rest/api/{version}/issue/{issueIdOrKey}/attachments'
      and include the file name in params as {"file": "filename.ext"} or {"files": ["file1.ext", "file2.ext"]} for multiple files

    JQL status transitions:
    - Basic: status CHANGED TO "Status" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1))
    - Specific transition: status CHANGED FROM "Status1" TO "Status2" BY "user@example.com" (only when user explicitly asks about FROM/TO)
    - DURING period formats:
      * Specific dates with time: DURING ('2025/10/01 00:00', '2025/10/20 23:59')
      * This month: DURING (startOfMonth(), endOfMonth())
      * Last month: DURING (startOfMonth(-1), endOfMonth(-1))
      * This week: DURING (startOfWeek(), endOfWeek())
      * Last week: DURING (startOfWeek(-1), endOfWeek(-1))
      * Last 2 weeks: DURING (startOfWeek(-1), endOfWeek())
      * Last 30 days: DURING (-30d, now())

    JQL for "completed/done/implemented/developed" queries to track work contribution:
    - CRITICAL: Each status change needs its own BY clause. NEVER: (status CHANGED TO "X" OR status CHANGED TO "Y") BY "user"
    - For queries like "tickets completed/implemented/done by user X in period Y", combine THREE approaches:
      1. User closed ticket: (status CHANGED TO "Closed" BY "user@example.com" DURING (period) OR status CHANGED TO "Done" BY "user@example.com" DURING (period))
      2. User WAS assignee: (assignee WAS "user@example.com" AND status IN ("Closed", "Done") AND updated >= relativeDate)
      3. User IS assignee: (assignee = "user@example.com" AND status IN ("Closed", "Done") AND updated >= relativeDate)
    - Example last week: ((status CHANGED TO "Closed" BY "user@example.com" DURING (startOfWeek(-1), endOfWeek(-1)) OR status CHANGED TO "Done" BY "user@example.com" DURING (startOfWeek(-1), endOfWeek(-1))) OR (assignee WAS "user@example.com" AND status IN ("Closed", "Done") AND updated >= -7d) OR (assignee = "user@example.com" AND status IN ("Closed", "Done") AND updated >= -7d))
    - This captures tickets where user: closed it, worked on it (was assignee), or is responsible for it (current assignee)
    - Status names are case-sensitive: use "Closed", "Done" (not "CLOSED", "DONE"). Query project statuses if unsure.
    - For updated field: use relative dates like -7d, -30d (NOT date functions in comparison operators)
    """
    return description


GENERIC_JIRA_TOOL = ToolMetadata(
    name="generic_jira_tool",
    description=get_jira_tool_description(),
    label="Generic Jira",
    user_description="""
    Provides access to the Jira API, enabling interaction with Jira project management and issue tracking features. This tool allows the AI assistant to perform various operations related to issues, projects, and workflows in both Jira Server and Jira Cloud environments.

    Key capabilities:
    - Create, update, search, and manage Jira issues
    - Attach files and documents to issues
    - Manage projects, sprints, and workflows
    - Query issue status, assignees, and metadata

    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the Jira integration)
    2. Jira URL
    3. Username/email for Jira (Required for Jira Cloud)
    4. Token (API token or Personal Access Token)

    Usage Note:
    Use this tool when you need to manage Jira issues, projects, sprints, retrieve information from your Jira environment, or attach files to issues.
    """.strip(),
    settings_config=True,
    config_class=JiraConfig,
)
