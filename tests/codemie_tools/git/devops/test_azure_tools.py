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

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from azure.devops.v7_0.git.models import (
    GitBranchStats,
    GitCommitRef,
    GitPullRequest,
    GitItem,
    GitPullRequestCommentThread,
    Comment,
    IdentityRef,
)

from codemie_tools.git.azure_devops.client import AzureDevOpsCredentials
from codemie_tools.git.azure_devops.tools import (
    CommentOnPullRequestTool,
    ListBranchesTool,
    SetActiveBranchTool,
    ListFilesTool,
    ListOpenPullRequestsTool,
    CreateBranchTool,
    ReadFileTool,
    CreateFileTool,
    UpdateFileTool,
    CreatePullRequestTool,
)


class TestListBranchesTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = ListBranchesTool(credentials=self.credentials)
        self.tool.client = MagicMock()

    def test_execute_with_branches(self):
        # Mock branch data
        branch1 = GitBranchStats(name="main")
        branch2 = GitBranchStats(name="develop")
        self.tool.client.client.get_branches.return_value = [branch1, branch2]

        result = self.tool.execute()
        expected = "Found 2 branches in the repository:\nmain\ndevelop"
        self.assertEqual(result, expected)

    def test_execute_no_branches(self):
        self.tool.client.client.get_branches.return_value = []
        result = self.tool.execute()
        self.assertEqual(result, "No branches found in the repository")

    def test_execute_error(self):
        self.tool.client.client.get_branches.side_effect = Exception("API Error")
        result = self.tool.execute()
        self.assertTrue(result.startswith("Error during attempt to fetch the list of branches"))


class TestSetActiveBranchTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = SetActiveBranchTool(credentials=self.credentials)
        self.tool.client = MagicMock()

    def test_execute_branch_exists(self):
        branch = GitBranchStats(name="feature")
        self.tool.client.client.get_branches.return_value = [branch]
        result = self.tool.execute("feature")
        self.assertEqual(result, "Switched to branch `feature`")
        self.assertEqual(self.tool.client.active_branch, "feature")

    def test_execute_branch_not_exists(self):
        self.tool.client.client.get_branches.return_value = []
        result = self.tool.execute("nonexistent")
        self.assertIn("does not exist", result)


class TestListFilesTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = ListFilesTool(credentials=self.credentials)
        self.tool.client = MagicMock()
        self.tool.client.active_branch = "main"

    def test_execute_with_files(self):
        item1 = GitItem(path="file1.txt", git_object_type="blob")
        item2 = GitItem(path="dir/file2.txt", git_object_type="blob")
        self.tool.client.client.get_items.return_value = [item1, item2]

        result = self.tool.execute("test_dir")
        expected = str(["file1.txt", "dir/file2.txt"])
        self.assertEqual(result, expected)

    def test_execute_error(self):
        self.tool.client.client.get_items.side_effect = Exception("API Error")
        result = self.tool.execute("test_dir")
        self.assertTrue(result.startswith("Failed to fetch files from directory"))


class TestListOpenPullRequestsTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = ListOpenPullRequestsTool(credentials=self.credentials)
        self.tool.client = MagicMock()

    def test_execute_with_prs(self):
        pr = GitPullRequest(pull_request_id=1, title="Test PR", status="active")
        self.tool.client.client.get_pull_requests.return_value = [pr]

        # Mock PR details
        thread = GitPullRequestCommentThread(
            comments=[
                Comment(
                    id=1,
                    content="Test comment",
                    author=IdentityRef(display_name="Test User"),
                    published_date=datetime.now(),
                )
            ],
            status="active",
        )
        self.tool.client.client.get_threads.return_value = [thread]
        self.tool.client.client.get_pull_request_commits.return_value = [
            GitCommitRef(commit_id="123", comment="Test commit")
        ]

        result = self.tool.execute()
        self.assertIn("Found 1 open pull requests", result)

    def test_execute_no_prs(self):
        self.tool.client.client.get_pull_requests.return_value = []
        result = self.tool.execute()
        self.assertEqual(result, "No open pull requests available")


class TestCreateBranchTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = CreateBranchTool(credentials=self.credentials)
        self.tool.client = MagicMock()

    def test_execute_success(self):
        branch = GitBranchStats(commit=GitCommitRef(commit_id="123abc"))
        self.tool.client.branch_exists.return_value = False
        self.tool.client.client.get_branch.return_value = branch

        result = self.tool.execute("new-branch")
        self.assertIn("created successfully", result)

    def test_execute_branch_exists(self):
        self.tool.client.branch_exists.return_value = True
        result = self.tool.execute("existing-branch")
        self.assertIn("already exists", result)

    def test_execute_branch_with_spaces(self):
        result = self.tool.execute("branch with spaces")
        self.assertIn("contains spaces", result)


class TestReadFileTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = ReadFileTool(credentials=self.credentials)
        self.tool.client = MagicMock()
        self.tool.client.active_branch = "main"

    def test_execute_success(self):
        self.tool.client.client.get_item_text.return_value = [b"test content"]
        result = self.tool.execute("test.txt")
        self.assertEqual(result, "test content")

    def test_execute_file_not_found(self):
        self.tool.client.client.get_item_text.side_effect = Exception("Not found")
        result = self.tool.execute("nonexistent.txt")
        self.assertIn("File not found", result)


class TestUpdateFileTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = UpdateFileTool(credentials=self.credentials)
        self.tool.client = MagicMock()
        self.tool.client.base_branch = "main"
        self.tool.client.active_branch = "feature"

    def test_execute_protected_branch(self):
        self.tool.client.active_branch = "main"
        result = self.tool.execute("main", "test.txt", "old->new")
        self.assertIn("protected", result)


class TestCreateFileTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = CreateFileTool(credentials=self.credentials)
        self.tool.client = MagicMock()
        self.tool.client.base_branch = "main"

    def test_execute_success(self):
        branch = GitBranchStats(commit=GitCommitRef(commit_id="123abc"))
        self.tool.client.client.get_branch.return_value = branch
        self.tool.client.client.get_item.side_effect = Exception("Not found")

        result = self.tool.execute("test.txt", "new content", "feature")
        self.assertIn("Created file", result)

    def test_execute_file_exists(self):
        self.tool.client.client.get_item.return_value = GitItem()
        result = self.tool.execute("test.txt", "new content", "feature")
        self.assertIn("already exists", result)


class TestCreatePullRequestTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = CreatePullRequestTool(credentials=self.credentials)
        self.tool.client = MagicMock()
        self.tool.client.active_branch = "feature"

    def test_execute_success(self):
        pr_response = GitPullRequest(pull_request_id=1)
        self.tool.client.client.create_pull_request.return_value = pr_response

        result = self.tool.execute("Test PR", "PR description", "main")
        self.assertIn("Successfully created PR with ID 1", result)

    def test_execute_same_branch(self):
        result = self.tool.execute("Test PR", "PR description", "feature")
        self.assertIn("Cannot create a pull request", result)


class TestCommentOnPullRequestTool(unittest.TestCase):
    def setUp(self):
        self.credentials = AzureDevOpsCredentials(
            organization_url="https://dev.azure.com/test-org",
            project="test-project",
            repository_id="test-repo-id",
            token="test-token",
            base_branch="main",
        )
        self.tool = CommentOnPullRequestTool(credentials=self.credentials)
        self.tool.client = MagicMock()

    @patch("codemie_tools.git.azure_devops.client.AzureDevOpsClient")
    def test_execute_success(self, mock_azure_devops_client):
        # If code tries to instantiate the client, gets the mock instead
        mocked_client_instance = MagicMock()
        mocked_client_instance.repository_id = "repo1"
        mocked_client_instance.project = "test-project"
        mocked_client_instance.client.create_thread = MagicMock()
        mock_azure_devops_client.return_value = mocked_client_instance

        self.tool.client = mocked_client_instance

        pr_number = 123
        file_path = "src/file.py"
        comment = "Nice change!"
        line_number = 42

        result = self.tool.execute(pr_number=pr_number, file_path=file_path, comment=comment, line_number=line_number)
        self.assertIn("Commented on pull request", result)
        mocked_client_instance.client.create_thread.assert_called_once()

        called_args, called_kwargs = mocked_client_instance.client.create_thread.call_args

        comment_thread = called_args[0]
        self.assertEqual(len(comment_thread.comments), 1)
        self.assertEqual(comment_thread.comments[0].content, comment)
        self.assertEqual(comment_thread.comments[0].comment_type, "text")
        self.assertEqual(comment_thread.status, "active")

        ctx = comment_thread.thread_context
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.file_path, "/" + file_path if not file_path.startswith("/") else file_path)
        self.assertEqual(ctx.right_file_start.line, line_number)
        self.assertEqual(ctx.right_file_end.line, line_number + 1)

        self.assertEqual(called_kwargs["repository_id"], "repo1")
        self.assertEqual(called_kwargs["pull_request_id"], pr_number)
        self.assertEqual(called_kwargs["project"], "test-project")

    @patch("codemie_tools.git.azure_devops.client.AzureDevOpsClient")
    def test_execute_empty_file_path_and_no_line_number(self, mock_azure_devops_client):
        mocked_client_instance = MagicMock()
        mocked_client_instance.repository_id = "repo1"
        mocked_client_instance.project = "test-project"
        mocked_client_instance.client.create_thread = MagicMock()
        mock_azure_devops_client.return_value = mocked_client_instance

        self.tool.client = mocked_client_instance

        pr_number = 123
        file_path = ""
        comment = "No file path and no line number"
        line_number = None

        result = self.tool.execute(pr_number=pr_number, file_path=file_path, comment=comment, line_number=line_number)
        self.assertIn("Commented on pull request", result)
        mocked_client_instance.client.create_thread.assert_called_once()

        called_args, called_kwargs = mocked_client_instance.client.create_thread.call_args

        comment_thread = called_args[0]
        self.assertEqual(len(comment_thread.comments), 1)
        self.assertEqual(comment_thread.comments[0].content, comment)
        self.assertEqual(comment_thread.comments[0].comment_type, "text")
        self.assertEqual(comment_thread.status, "active")

        ctx = comment_thread.thread_context
        self.assertIsNone(ctx)

        self.assertEqual(called_kwargs["repository_id"], "repo1")
        self.assertEqual(called_kwargs["pull_request_id"], pr_number)
        self.assertEqual(called_kwargs["project"], "test-project")
