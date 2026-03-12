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

import logging
import os
from typing import Dict

from langchain_community.utilities.gitlab import GitLabAPIWrapper
from langchain_core.utils import get_from_dict_or_env
from pydantic import model_validator

logger = logging.getLogger(__name__)

ISSUE_NUMBER_MUST_BE_SPECIFIED = "issue_number must be specified and must be an integer."


class CustomGitLabAPIWrapper(GitLabAPIWrapper):
    """
    A custom GitLab API wrapper that extends the Langchain GitLabAPIWrapper class
    gitlab_base_url is added to allow for custom GitLab instances
    """

    gitlab_base_url: str

    @classmethod
    @model_validator(mode="before")
    def validate_environment(cls, values: Dict) -> Dict:
        """Validate that api key and python package exists in environment."""

        gitlab_base_url = get_from_dict_or_env(values, "gitlab_base_url", "GITLAB_BASE_URL")

        gitlab_repository = get_from_dict_or_env(values, "gitlab_repository", "GITLAB_REPOSITORY")

        gitlab_personal_access_token = get_from_dict_or_env(
            values, "gitlab_personal_access_token", "GITLAB_PERSONAL_ACCESS_TOKEN"
        )

        gitlab_branch = get_from_dict_or_env(values, "gitlab_branch", "GITLAB_BRANCH", default="main")
        gitlab_base_branch = get_from_dict_or_env(values, "gitlab_base_branch", "GITLAB_BASE_BRANCH", default="main")

        try:
            import gitlab

        except ImportError:
            raise ImportError("python-gitlab is not installed. " "Please install it with `pip install python-gitlab`")

        g = gitlab.Gitlab(gitlab_base_url, private_token=gitlab_personal_access_token)

        g.auth()

        values["gitlab"] = g
        values["gitlab_repo_instance"] = g.projects.get(gitlab_repository)
        values["gitlab_repository"] = gitlab_repository
        values["gitlab_personal_access_token"] = gitlab_personal_access_token
        values["gitlab_branch"] = gitlab_branch
        values["gitlab_base_branch"] = gitlab_base_branch

        return values

    def create_branch(self, proposed_branch_name: str) -> str:
        """
        Create a new branch, and set it as the active bot branch.
        Equivalent to `git switch -c proposed_branch_name`
        If the proposed branch already exists, we append _v1 then _v2...
        until a unique name is found.

        Returns:
            str: A plaintext success message.
        """
        from gitlab.exceptions import GitlabCreateError

        i = 0
        new_branch_name = proposed_branch_name

        base_branch = self.gitlab_base_branch

        for i in range(1000):
            try:
                self.gitlab_repo_instance.branches.create({'branch': new_branch_name, 'ref': base_branch})

                self.gitlab_branch = new_branch_name
                return f"Branch '{new_branch_name}' " "created successfully, and set as current active branch."
            except GitlabCreateError as e:
                if e.response_code == 400 and "Branch already exists" in e.error_message:
                    i += 1
                    new_branch_name = f"{proposed_branch_name}_v{i}"
                else:
                    # Handle any other exceptions
                    logger.error(f"Failed to create branch. Error: {str(e)}")  # noqa: T201
                    raise GitlabCreateError(
                        "Unable to create branch name from proposed_branch_name: " f"{proposed_branch_name}"
                    )
        return (
            "Unable to create branch. "
            "At least 1000 branches exist with named derived from "
            f"proposed_branch_name: `{proposed_branch_name}`"
        )

    def set_active_branch(self, branch_name: str) -> str:
        """Equivalent to `git checkout branch_name` for this Agent.

        Returns an Error (as a string) if branch doesn't exist.
        """
        curr_branches = [branch.name for branch in self.gitlab_repo_instance.branches.list(all=True)]
        if branch_name in curr_branches:
            self.gitlab_branch = branch_name
            return f"Switched to branch `{branch_name}`"
        else:
            return f"Error {branch_name} does not exist," f"in repo with current branches: {str(curr_branches)}"

    def list_branches_in_repo(self) -> str:
        """
        Fetches a list of all branches in the repository.

        Returns:
            str: A plaintext report containing the names of the branches.
        """
        try:
            branches = [branch.name for branch in self.gitlab_repo_instance.branches.list(all=True)]
            if branches:
                branches_str = "\n".join(branches)
                return f"Found {len(branches)} branches in the repository:" f"\n{branches_str}"
            else:
                return "No branches found in the repository"
        except Exception as e:
            return str(e)

    def replace_file_content(self, file_query: str, commit_message: str = None) -> str:
        """
        Replaces all the file's content with new content.
        Parameters:
            file_query(str): Contains the file path and the file contents after the new line.
                For example:
                test/hello.txt
                Hello Mars!
            commit_message(str): optional commit message
        Ensures an empty newline at the end of the file content if missing.
        Returns:
            A success or failure message
        """
        try:
            first_line, *rest_of_lines = file_query.splitlines()
            file_path = "\n".join([first_line])
            updated_file_content = "\n".join(rest_of_lines)

            # Ensure there's a newline at the end of the file content
            if not updated_file_content.endswith(os.linesep):
                updated_file_content += os.linesep

            commit_message = "Update " + file_path if commit_message is None else commit_message.strip()

            commit = {
                "branch": self.gitlab_branch,
                "commit_message": commit_message,
                "actions": [
                    {
                        "action": "update",
                        "file_path": file_path,
                        "content": updated_file_content,
                    }
                ],
            }

            self.gitlab_repo_instance.commits.create(commit)
            return "Updated file " + file_path
        except Exception as e:
            return "Unable to update file due to error:\n" + str(e)

    def create_file(self, file_query: str, commit_message: str = None) -> str:
        """
        Creates a new file on the gitlab repo
        Parameters:
            file_query(str): a string which contains the file path
            and the file contents. The file path is the first line
            in the string, and the contents are the rest of the string.
            For example, "hello_world.md\n# Hello World!"
            commit_message(str): optional commit message
        Returns:
            str: A success or failure message
        """
        file_path = file_query.split("\n")[0]
        file_contents = file_query[len(file_path) + 2 :]

        commit_message = "Create " + file_path if commit_message is None else commit_message.strip()

        try:
            self.gitlab_repo_instance.files.get(file_path, self.gitlab_branch)
            return f"File already exists at {file_path}. Use update_file instead"
        except Exception:
            data = {
                "branch": self.gitlab_branch,
                "commit_message": commit_message,
                "file_path": file_path,
                "content": file_contents,
            }

            self.gitlab_repo_instance.files.create(data)

            return commit_message

    def delete_file(self, file_path: str, commit_message: str = None) -> str:
        """
        Deletes a file from the repo
        Parameters:
            file_path(str): Where the file is
            commit_message(str): Optional commit message
        Returns:
            str: Success or failure message
        """
        commit_message = "Delete " + file_path if commit_message is None else commit_message.strip()

        try:
            self.gitlab_repo_instance.files.delete(
                file_path=file_path, branch=self.gitlab_branch, commit_message=commit_message
            )
            return "Deleted file " + file_path
        except Exception as e:
            return "Unable to delete file due to error:\n" + str(e)
