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
from .models import AzureDevOpsGitConfig

AZURE_DEVOPS_GIT_TOOL = ToolMetadata(
    name="azure_devops_git",
    description="""
        Advanced Azure DevOps Git REST API client tool that provides comprehensive access to Azure DevOps Git repositories.

        Documentation: https://learn.microsoft.com/en-us/rest/api/azure/devops/?view=azure-devops-rest-7.2

        IMPORTANT: Azure DevOps Structure
        - Organization: Top-level container (e.g., "EPMC-EASE")
        - Project: Container for repositories, pipelines, etc. (e.g., "DefaultProject", "MyTeam")
        - Repository: Git repository within a project (e.g., "git-version-experiments")

        HOW TO FIND PROJECT NAME:
        1. First, list all projects in the organization:
           {"query": {"method": "GET", "url": "/_apis/projects", "method_arguments": {}}}
           Note: This uses /_apis/projects (not /_apis/git/)

        2. Then list repositories in a specific project:
           {"query": {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {"project": "ProjectName"}}}

        3. Or list ALL repositories across all projects (no project parameter):
           {"query": {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {}}}
           This will show repository.project.name for each repository

        INPUT FORMAT:
        The tool accepts a `query` parameter containing a JSON with the following structure:
        {"query":
            {
                "method": "GET|POST|PUT|DELETE|PATCH",
                "url": "/_apis/git/...",
                "method_arguments": {object with request data} or [list for bulk endpoints],
                "custom_headers": {optional dictionary of additional HTTP headers}
            }
        }

        REQUIREMENTS:
        - `method`: HTTP method (GET, POST, PUT, DELETE, PATCH)
        - `url`: Must start with "/_apis/git/" for Git operations (Azure DevOps Git API endpoint - relative path)
        - `method_arguments`: Object containing request parameters or body data; for some endpoints (e.g., /refs bulk updates) this MUST be a LIST `[...]`
        - `custom_headers`: Optional dictionary for additional headers (authorization headers are protected)
        - The entire query must be valid JSON that passes json.loads() validation
        - NOTE: When method_arguments is an OBJECT (not a list), placeholders such as {repositoryId}, {prId}, and {threadId} in the URL are replaced from method_arguments`. The corresponding key is then removed from the arguments to avoid duplication.
        - NOTE: When method_arguments is a LIST, placeholders like {repositoryId} in the URL will NOT be replaced automatically. Put the repository ID directly into the URL string.
        - **BEST PRACTICE:** To ensure reliability, **always embed all IDs (`repositoryId`, `prId`, `threadId`, etc.) directly into the `url` string** and remove them from `method_arguments`. This applies to all requests (GET, POST, etc.).

        PROJECT PARAMETER:
        - For most Git operations, you need to specify the PROJECT name (not repository name!)
        - Project can be specified in method_arguments: {"project": "ProjectName", ...}
        - If unsure about project name, first list all repositories without project parameter

        API VERSION:
        - Azure DevOps REST API uses api-version parameter, defaults to configured version (7.1-preview.1)
        - For specific API version requirements, include it in the method_arguments: {"api-version": "7.0"} (or "7.1")

        FEATURES:
        - Automatic request parameter handling (GET uses query params, others use request body)
        - Built-in authentication using configured Azure DevOps Personal Access Token
        - Custom header support for specialized API calls
        - Structured response with full context for AI agent processing
        - Comprehensive error handling and validation
        - URL placeholder replacement (e.g., {repositoryId}, {prId}, {threadId}) when method_arguments is an OBJECT
        - For bulk endpoints requiring LIST bodies (e.g., /refs), placeholders are NOT replaced; IDs must be embedded directly in the URL.
        - Project can be specified in method_arguments to override default

        RESPONSE FORMAT:
        Returns an AzureDevOpsGitOutput object containing:
        {
            "success": true/false,           # Whether request was successful
            "status_code": 200,               # HTTP status code
            "method": "GET",                  # HTTP method used
            "url": "https://...",             # Full URL that was called
            "data": {...} or [...] or "...",  # Response body (JSON object/array or text)
            "error": null or "error message"  # Error message if failed
        }

        This structured format provides complete visibility into the HTTP transaction for AI agent analysis.

        SECURITY:
        - Authorization headers are automatically managed and cannot be overridden via custom_headers
        - Personal Access Token (PAT) is securely transmitted via Basic Authentication header

        COMMON WORKFLOWS (SEQUENCES):
        - **Create a branch:** `GET /refs` (to get source commit ID) -> `POST /refs` (to create new branch with the ID). [7, 8]
        - **Edit a file:** `GET /refs` (to get latest commit ID) -> `GET /items` (to read current content) -> `POST /pushes` (to commit the new full content). [9, 10, 11]
        - **Create a Pull Request:** `POST /pullrequests` (a single-step action if the source branch exists). [12]
        - **Review a Pull Request:** `GET /pullRequests/{prId}/iterations` -> `GET /iterations/{id}/changes` -> `GET /items` (for file content) -> `POST /pullRequests/{prId}/threads` (to post comments). [13, 14, 15, 16]

        COMMON PITFALLS (quick notes to reduce retries):
        - **400 Bad Request errors:** Often caused by failed URL placeholder substitution. **Always embed IDs like `repositoryId`, `prId`, and `threadId` directly in the URL string.** [8, 15, 16, 17]
        - **Invalid data types:** Ensure arguments match the API's expected type. For example, when creating a PR thread, use `status: 1` (integer) for "active", not the string `"active"`. [15, 17]
        - 405 Method Not Allowed on POST /pushes: ensure body shape matches Azure DevOps spec (refUpdates + commits) and include correct api-version (e.g., "7.1").
        - Bulk endpoints with LIST bodies (/refs): repositoryId MUST be embedded directly in the URL; {repositoryId} placeholder will not be substituted for lists.
        - Editing text files via /pushes: use newContent.contentType="rawtext". For binary content, use "base64encoded" and provide base64 string.
        - Reading content from a specific branch: use versionDescriptor.version=<branch_name> and includeContent=true.
        - PR comments/threads can be added only to active PRs. If PR is completed/abandoned, create a new PR or reopen before posting threads. [15, 17]
        - Propagation delays: after creating branches or pushing commits, wait a few seconds before subsequent operations (refs/content/PR creation).
        - Finding active PR by source branch: use GET /pullrequests with searchCriteria.status="active" and searchCriteria.sourceRefName="refs/heads/<branch>". [19]
        - Inline PR comment on a specific line: use threadContext with filePath and rightFileStart/rightFileEnd to attach comments to the diff/file line. [15]
        - Append to a text file via /pushes: the API does not support "append" directly. First GET current content via /items (includeContent=true), then POST /pushes with the entire updated content in newContent.content.

        EXAMPLES:

        1. Find the right project for your repository:
        {"query": {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {}}}
        Then look for your repository name and note its "project": {"name": "..."} field

        2. List repositories in a specific project:
        For GET requests, "project" is a string used in url or query params for filtering.
        {"query": {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {"project": "ProjectName"}}}
        Response: {"success": true, "status_code": 200, "method": "GET", "url": "https://dev.azure.com/org/_apis/git/repositories", "data": {"value": [...]}, "error": null}

        3. Get a specific repository:
        Using placeholder (shows parameter name) or alternatively embed ID directly in the URL (best practice).
        Option1: {"query": {"method": "GET", "url": "/_apis/git/repositories/{repositoryId}", "method_arguments": {"repositoryId": "repo-guid"}}}
        Option2: {"query": {"method": "GET", "url": "/_apis/git/repositories/repo-guid", "method_arguments": {}}}
        Response: {"success": true, "status_code": 200, "method": "GET", "url": "https://dev.azure.com/org/_apis/git/repositories/repo-guid", "data": {...}, "error": null}

        4. Create a repository:
        For POST requests, the API might require "project" as an object in the body.
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories",
                "method_arguments": {
                    "name": "NewRepo",
                    "project": {"id": "ProjectId"}
                }
            }
        }
        Response: {"success": true, "status_code": 201, "method": "POST", "url": "https://dev.azure.com/org/_apis/git/repositories", "data": {...}, "error": null}

        5. Get repository branches:
        Using placeholder or embed ID directly in the URL
        {"query": {"method": "GET", "url": "/_apis/git/repositories/{repositoryId}/refs", "method_arguments": {"repositoryId": "repo-guid", "filter": "heads/"}}}
        {"query": {"method": "GET", "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/refs", "method_arguments": {"filter": "heads/"}}}


        6. Find active PRs by source branch:
        {"query":
            {
                "method": "GET",
                "url": "/_apis/git/repositories/{repositoryId}/pullrequests",
                "method_arguments": {
                    "repositoryId": "repo-guid",
                    "searchCriteria.status": "active",
                    "searchCriteria.sourceRefName": "refs/heads/test-broken-develop",
                    "api-version": "7.1"
                }
            }
        }

        7. Get commit details:
        Using placeholders or embed IDs directly
        {"query": {"method": "GET", "url": "/_apis/git/repositories/{repositoryId}/commits/{commitId}", "method_arguments": {"repositoryId": "repo-guid", "commitId": "commit-sha"}}}
        {"query": {"method": "GET", "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/commits/892ca5bd6a5f36867d2f67639cfac2c987abb9b3", "method_arguments": {}}}

        // --- WORKFLOW: CREATE A BRANCH (MULTI-STEP) ---
        8. First, get the commit ID of the source branch (e.g., 'develop'):
        The 'objectId' from the response is the commit ID you need for the next step.
        {"query": {"method": "GET", "url": "/_apis/git/repositories/{repositoryId}/refs", "method_arguments": {"repositoryId": "6f742f70-9e29-49df-ad29-fee16a566063", "filter": "heads/develop"}}}
        {"query": {"method": "GET", "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/refs", "method_arguments": {"filter": "heads/develop"}}}

        9. Then, create the new branch from that commit ID:
        IMPORTANT: This API requires a LIST `[...]` as the request body.
        The `{repositoryId}` placeholder will NOT work if `method_arguments` is a list.
        Therefore, the repository ID must be embedded directly in the `url` string.
        {"query": {
            "method": "POST",
            "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/refs",
            "method_arguments": [
                {
                    "name": "refs/heads/new-feature-branch", // Full name of the new branch.
                    "oldObjectId": "0000000000000000000000000000000000000000", // Must be all zeros to create.
                    "newObjectId": "892ca5bd6a5f36867d2f67639cfac2c987abb9b3" // The commit ID from the previous step.
                }
            ]
        }}

        // --- WORKFLOW: EDIT A FILE (MULTI-STEP) ---
        10. First, get the latest commit ID of the branch ('oldObjectId'):
        {"query": {"method": "GET", "url": "/_apis/git/repositories/repo-guid/refs", "method_arguments": {"filter": "heads/my-feature-branch"}}}

        11. Second, read the current content of the file:
        {"query":
            {
                "method": "GET",
                "url": "/_apis/git/repositories/repo-guid/items",
                "method_arguments": {
                    "path": "/README.md",
                    "versionDescriptor.version": "my-feature-branch",
                    "includeContent": "true",
                    "api-version": "7.1"
                }
            }
        }

        12. Finally, push the updated content to the file:
        Note: <CURRENT_CONTENT> must be replaced with the content from step 9.
        Note: <OLD_COMMIT_ID> must be the 'objectId' from step 8 (fetched via GET /refs for the branch).
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/repo-guid/pushes",
                "method_arguments": {
                    "refUpdates": [{"name": "refs/heads/my-feature-branch", "oldObjectId": "<OLD_COMMIT_ID>"}],
                    "commits": [{
                        "comment": "Append a line to README.md",
                        "changes": [{
                            "changeType": "edit",
                            "item": { "path": "/README.md" },
                            "newContent": { "content": "<CURRENT_CONTENT>\\nAppended line.", "contentType": "rawtext" }
                        }]
                    }],
                    "api-version": "7.1"
                }
            }
        }

        // --- WORKFLOW: CREATE A PULL REQUEST ---
        13. Create a pull request:
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/{repositoryId}/pullrequests",
                "method_arguments": {
                    "repositoryId": "repo-id",
                    "sourceRefName": "refs/heads/feature-branch",
                    "targetRefName": "refs/heads/main",
                    "title": "Feature implementation",
                    "description": "Adding new feature X",
                    "reviewers": [ { "id": "user-guid" } ],
                    "isDraft": true
                }
            }
        }
        Option 2 (best practice):
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/pullrequests",
                "method_arguments": {
                    "sourceRefName": "refs/heads/feature-branch",
                    "targetRefName": "refs/heads/main",
                    "title": "Feature implementation",
                    "description": "Adding new feature X"
                }
            }
        }

        // --- WORKFLOW: PULL REQUEST REVIEW (MULTI-STEP) ---
        14. Get PR iterations to find the latest one: [2, 9]
        {"query": {"method": "GET", "url": "/_apis/git/repositories/7ec3871a-46be-4132-9864-96301215006f/pullRequests/6577/iterations", "method_arguments": {"project": "EPM-EASE", "api-version": "7.1"}}}

        15. Get changes in the latest iteration (e.g., iteration 14): [3]
        {"query": {"method": "GET", "url": "/_apis/git/repositories/7ec3871a-46be-4132-9864-96301215006f/pullRequests/6577/iterations/14/changes", "method_arguments": {"project": "EPM-EASE", "api-version": "7.1"}}}

        16. Add an inline PR comment on a specific line: [1]
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/repo-guid/pullRequests/6446/threads",
                "method_arguments": {
                    "comments": [{"content": "This is an inline comment."}],
                    "status": 1, // Use integer 1 for 'active'
                    "threadContext": {
                        "filePath": "/README.md",
                        "rightFileStart": { "line": 10, "offset": 1 },
                        "rightFileEnd": { "line": 10, "offset": 15 }
                    }
                }
            }
        }


        17. Add a general PR comment thread (not on a specific line):
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/{repositoryId}/pullRequests/{prId}/threads",
                "method_arguments": {
                    "project": "ProjectName",
                    "comments": [ { "content": "This is a test comment." } ],
                    "status": 1  // Use integer 1 for 'active'
                }
            }
        }

        18. Reply to a PR comment thread: [25]
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/repo-guid/pullRequests/6446/threads/44077/comments",
                "method_arguments": { "content": "This is a reply." }
            }
        }

        // --- OTHER USEFUL EXAMPLES ---
        19. Get file content with custom headers (e.g., to get raw binary):
        {"query":
            {
                "method": "GET",
                "url": "/_apis/git/repositories/{repositoryId}/items",
                "method_arguments": {
                    "repositoryId": "repo-id",
                    "path": "/path/to/file.txt"
                },
                "custom_headers": {"Accept": "application/octet-stream"}
            }
        }

        20. Re-open an abandoned Pull Request: [7, 14]
        To change a PR's status, use PATCH. Set status to "active" to re-open, or "abandoned" to close without merging.
        {"query":
            {
                "method": "PATCH",
                "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/pullrequests/6501",
                "method_arguments": {
                    "status": "active"
                }
            }
        }
        Response: {"success": true, "status_code": 200, "data": {"pullRequestId": 6501, "status": "active", ...}, "error": null}

        21. Delete a branch: [2, 4, 5, 6]
        First, get the branch's current commit ID (oldObjectId). Then, POST to the /refs endpoint with a zeroed-out `newObjectId`.
        This requires a LIST `[...]` body, so the repository ID must be in the URL.
        {"query":
            {
                "method": "POST",
                "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/refs",
                "method_arguments": [
                    {
                        "name": "refs/heads/test-codemie-branch-to-delete",
                        "oldObjectId": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2", // Current commit SHA of the branch
                        "newObjectId": "0000000000000000000000000000000000000000"  // Must be all zeros to delete
                    }
                ]
            }
        }
        Response: {"success": true, "status_code": 200, "data": {"value": [{"success": true, ...}]}, "error": null}

        22. Delete a PR comment: [1, 3]
        {"query":
            {
                "method": "DELETE",
                "url": "/_apis/git/repositories/6f742f70-9e29-49df-ad29-fee16a566063/pullRequests/6495/threads/43267/comments/1",
                "method_arguments": {"api-version": "7.1"}
            }
        }
        Response: {"success": true, "status_code": 200, "data": "", "error": null}

        23. Example of error response:
        {"query": {"method": "GET", "url": "/_apis/git/repositories/invalid-id", "method_arguments": {}}}
        Response: {"success": false, "status_code": 404, "method": "GET", "url": "https://dev.azure.com/org/_apis/git/repositories/invalid-id", "data": {"message": "Repository not found"}, "error": "HTTP 404: Not Found - Repository not found"}
        """,
    label="Azure DevOps Git",
    user_description="""
        Provides comprehensive access to the Azure DevOps Git REST API with structured response formatting for optimal AI agent processing. This tool enables the AI assistant to perform any Azure DevOps Git operation available through the REST API.

        Key Capabilities:
        - Repository management (create, read, update, delete)
        - Branch and ref operations
        - Commit history and details
        - Pull request creation and management
        - File content retrieval and manipulation
        - Tag management
        - Policy configuration
        - Import repository operations
        - Structured response with full HTTP context

        Setup Requirements:
        1. Azure DevOps Organization URL (e.g., https://dev.azure.com/organization)
        2. Azure DevOps Personal Access Token with Git permissions

        Response Features:
        - Structured response object with success/failure indication
        - Complete HTTP transaction details including status codes
        - Full response body content (JSON or text)
        - Error messages for failed requests
        - Automatic handling of different request types (GET vs POST/PUT/DELETE)

        The tool returns a structured response object that includes:
        - success: boolean indicating if the request succeeded
        - status_code: HTTP status code for understanding the result
        - method: HTTP method used for the request
        - url: Full URL that was called
        - data: The actual response data from Azure DevOps
        - error: Error message if the request failed

        This structured format ensures the AI agent has full context about each API operation, making it easier to understand results and handle errors appropriately.

        Use this tool when you need direct access to Azure DevOps Git repositories that may not be covered by other specialized Azure DevOps tools.
        """.strip(),
    settings_config=True,
    config_class=AzureDevOpsGitConfig,
)
