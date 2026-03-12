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

import os
from datetime import datetime
from typing import Any

from github import InputGitTreeElement
from langchain.chains.llm import LLMChain
from langchain_core.documents import Document

from codemie_tools.base.utils import get_encoding

from codemie.configs import config, logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import GitRepo
from codemie.datasource.code.code_summary_datasource_prompt import README_GEN_PROMPT
from codemie.datasource.datasources_config import CODE_CONFIG
from codemie.rest_api.models.index import IndexInfo
from codemie.service.git_api.git_api_service import GitApiService
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.settings.settings import SettingsService


class DocsGenService:
    TOKEN_LIMIT = 70_000

    def __init__(self):
        self.git_actions = []

    @classmethod
    def _get_app_folder(cls, app_id: str):
        app_folder = f"{config.REPOS_LOCAL_DIR}/{app_id}"

        if not os.path.exists(app_folder):
            os.makedirs(app_folder)

        return app_folder

    @classmethod
    def _limit_output_content(cls, output: Any) -> Any:
        encoding = get_encoding(LLMService.BASE_NAME_GPT_41_MINI)
        tokens = encoding.encode(str(output))
        if len(tokens) > cls.TOKEN_LIMIT:
            output = encoding.decode(tokens[: cls.TOKEN_LIMIT])
        return output

    def generate_docs_per_file(self, document: Document, index: IndexInfo):
        app_folder = self._get_app_folder(index.project_name)

        base_path, file_name = os.path.split(document.metadata['file_path'])
        _, chunk_filename = os.path.split(document.metadata['source'])
        chunk_file_path = os.path.join(base_path, chunk_filename)

        doc_file_path = f"{app_folder}/{index.repo_name}/docs/{chunk_file_path}.md"
        _, file_extension = os.path.splitext(file_name)

        if file_extension not in CODE_CONFIG.excluded_extensions.get_full_docs_exclusions():
            directory = os.path.dirname(doc_file_path)
            if not os.path.exists(directory):
                os.makedirs(directory)
            with open(doc_file_path, "w") as f:
                logger.debug(f"Write new documentation for file: {doc_file_path}")
                self.git_actions.append(
                    {
                        'action': 'create',
                        'file_path': f"docs/{chunk_file_path}.md",
                        'content': document.page_content,
                    }
                )
                f.write(document.page_content)
        else:
            logger.debug(f"Skipping file: {document.metadata['file_path']}. No documentation needed for this file")

    def _generate_readme(self, readme_content: str, folder_path: str, llm_name: str, request_uuid: str):
        file_path = os.path.join(folder_path, f"README_{datetime.today().strftime("%Y-%m-%d_%H-%M-%S")}.md")
        llm = get_llm_by_credentials(llm_model=llm_name, request_id=request_uuid)
        chain = LLMChain(llm=llm, prompt=README_GEN_PROMPT)
        logger.debug(f"Generating readme for folder: {folder_path}")

        readme_result = chain.predict(fileName=file_path, fileContents=self._limit_output_content(readme_content))
        self.git_actions.append(
            {
                'action': 'create',
                'file_path': file_path,
                'content': readme_result,
            }
        )

    def recursively_generate_readmes(self, index: IndexInfo, llm_name: str, request_uuid: str):
        app_folder = self._get_app_folder(index.project_name)
        root_path = f"{app_folder}/{index.repo_name}/docs"
        for dirpath, folders, files in reversed(list(os.walk(root_path))):
            if not dirpath.replace(root_path, "/docs")[1:].startswith("."):
                self._generate_readme_for_directory(dirpath, folders, files, root_path, llm_name, request_uuid)

    def _generate_readme_for_directory(self, dirpath, folders, files, root_path, llm_name: str, request_uuid: str):
        relative_path = dirpath.replace(root_path, "/docs")[1:]
        readme_content = self._create_readme_header(relative_path, folders, files)
        content = self._get_content_for_readme(relative_path, folders, files)
        readme_content += f"##Content:\n{content}\n"
        self._generate_readme(readme_content, relative_path, llm_name, request_uuid)

    def _create_readme_header(self, dirpath, folders, files):
        readme_content = f"# {os.path.basename(dirpath)}\n\nThis folder contains:\n"
        readme_content += f"##Subfolders:\n{self._format_list(folders)}\n\n" if folders else ""
        readme_content += f"##Files:\n{self._format_list(files)}\n\n" if files else ""
        return readme_content

    def _format_list(self, items):
        return '\n'.join(items)

    def _get_content_for_readme(self, dirpath, folders, files):
        if files and not folders:
            return self._get_file_contents(dirpath)
        else:
            return self._get_folder_tree_and_contents(dirpath, folders)

    def _get_file_contents(self, dirpath):
        return '\n\n'.join([x['content'][:500] for x in self.git_actions if f'{x["file_path"]}'.startswith(dirpath)])

    def _get_folder_tree_and_contents(self, dirpath, folders):
        content = "Here a folder tree for repo, generate README based on them:\n\n"
        content += self._get_folder_tree(dirpath)
        content += self._get_inner_readme_contents(dirpath, folders)
        return content

    def _get_folder_tree(self, dirpath):
        return '\n'.join((x["file_path"] for x in self.git_actions if f'{x["file_path"]}'.startswith(dirpath)))

    def _get_inner_readme_contents(self, dirpath, folders):
        readme_contents = []
        for folder in folders:
            readme_path = f'{dirpath}/{folder}/README'
            # Find and process matching README contents from git actions
            folder_contents = [
                action['content'][:500]  # Limit content to 500 chars
                for action in self.git_actions
                if action['file_path'].startswith(readme_path)
            ]

            if folder_contents:
                readme_contents.append('\n\n'.join(folder_contents))
        return '\n\n'.join(readme_contents)

    def push_documentation(self, repo: GitRepo, index: IndexInfo):
        branch_name = "generated_docs"
        pr_title = "Generate project documentation for each file"
        pr_body = (
            " In an effort to enhance our code base's readability and maintainability, this PR introduces "
            "comprehensive documentation for every file within our repository. The newly added docs include "
            "relevant information such as purpose, functions, usage examples, and any pertinent notes for "
            "developers. This update ensures that developers, both current and future, can quickly understand "
            "and utilize our code efficiently, thus streamlining collaboration and development efforts. The "
            "documentation was generated adhering to our project's documentation guidelines to maintain "
            "consistency throughout."
        )
        if not repo:
            raise ValueError("Invalid repo instance provided")

        logger.info(f"Pushing documentation for repo: {repo.name}, {repo.get_type()}")

        creds = SettingsService.get_git_creds(
            user_id=index.created_by.id,
            project_name=index.project_name,
            repo_link=repo.link,
            setting_id=repo.setting_id,
        )
        if repo.get_type() == "gitlab":
            self.create_gitlab_pr(repo=repo, creds=creds, branch_name=branch_name, pr_title=pr_title, pr_body=pr_body)
        elif repo.get_type() == "github":
            self.create_github_pr(repo=repo, creds=creds, branch_name=branch_name, pr_title=pr_title, pr_body=pr_body)

    def create_gitlab_pr(self, repo: GitRepo, creds: Any, branch_name: str, pr_title: str, pr_body: str):
        gitlab_api_wrapper = GitApiService.init_gitlab_api_wrapper(
            repo_link=repo.link, base_branch=repo.branch, gitlab_token=creds.token
        )
        branch_creation_message = gitlab_api_wrapper.create_branch(branch_name)
        logger.debug(branch_creation_message)
        data = {'branch': gitlab_api_wrapper.gitlab_branch, 'commit_message': pr_title, 'actions': self.git_actions}
        gitlab_api_wrapper.gitlab_repo_instance.commits.create(data)
        pr_message = gitlab_api_wrapper.create_pull_request(pr_title + "\n" + pr_body)
        logger.debug(pr_message)

    def create_github_pr(self, repo: GitRepo, creds: Any, branch_name: str, pr_title: str, pr_body: str):
        github_api_wrapper = GitApiService.init_github_api_wrapper(
            repo_link=repo.link, base_branch=repo.branch, github_access_token=creds.token
        )
        github_repo = github_api_wrapper.github_repo_instance

        branch_creation_message = github_api_wrapper.create_branch(branch_name)
        logger.debug(branch_creation_message)

        git_tree = []
        for action in self.git_actions:
            blob = github_repo.create_git_blob(content=action['content'], encoding='utf-8')
            file_name = action['file_path'][1:] if action['file_path'].startswith("/") else action['file_path']
            git_tree.append(
                InputGitTreeElement(
                    path=file_name,
                    mode="100644",
                    type="blob",
                    sha=blob.sha,
                )
            )
        new_tree = github_repo.create_git_tree(
            tree=git_tree, base_tree=github_repo.get_git_tree(sha=github_api_wrapper.active_branch)
        )

        commit = github_repo.create_git_commit(
            message=pr_title,
            tree=github_repo.get_git_tree(sha=new_tree.sha),
            parents=[github_repo.get_git_commit(github_repo.get_branch(github_api_wrapper.active_branch).commit.sha)],
        )

        github_repo.get_git_ref(ref=f'heads/{github_api_wrapper.active_branch}').edit(sha=commit.sha)

        pr = github_api_wrapper.github_repo_instance.create_pull(
            title=pr_title,
            body=pr_body,
            head=github_api_wrapper.active_branch,
            base=github_api_wrapper.github_base_branch,
        )
        logger.debug(f"Successfully created PR number {pr.number}")
