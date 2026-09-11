# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Git loader for the FAQ datasource (``knowledge_base_git_faq``).

Clones the repository exactly like the Git code datasource (``GitBatchLoader``)
and walks every ``*.md`` file in the repo, narrowed only by the optional
``files_filter`` (same gitignore-style mechanism the Git code datasource uses,
see ``check_file_type``), parsing each match into a FAQ article (see
``faq_markdown_parser``). One file = one document; section chunking happens
in the processor, not here.

The factory takes primitives (link/branch) instead of a ``GitRepo`` row
because FAQ config lives on ``IndexInfo``.
"""

import os
import shutil
from dataclasses import asdict
from types import SimpleNamespace
from typing import Iterator, Optional

from codemie.configs import config, logger
from codemie.core.utils import check_file_type
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.faq_markdown_parser import FaqParseError, parse_faq_markdown
from codemie.datasource.loader.git_loader import (
    GitBatchLoader,
    _build_auth_header,
    _build_clone_url,
)
from codemie.rest_api.models.settings import Credentials
from git import Blob
from langchain_core.documents import Document


class GitFaqLoader(GitBatchLoader, BaseDatasourceLoader):
    """Clones a Git repo and yields one parsed FAQ article per ``.md`` file in the repo."""

    def __init__(
        self,
        *,
        repo_path: str,
        clone_url: Optional[str],
        branch: str,
        files_filter: Optional[str] = None,
        request_uuid: Optional[str] = None,
        datasource_id: str = "",
        auth_header: Optional[str] = None,
    ):
        self.files_filter = (files_filter or "").strip()
        self._skipped_files: list[str] = []
        super().__init__(
            repo_path=repo_path,
            clone_url=clone_url,
            branch=branch,
            file_filter=self._is_faq_file,
            auth_header=auth_header,
            request_uuid=request_uuid,
            datasource_id=datasource_id,
        )

    @classmethod
    def create_loader(
        cls,
        *,
        project_name: str,
        datasource_name: str,
        repo_link: str,
        branch: str,
        files_filter: Optional[str] = None,
        creds: Optional[Credentials],
        request_uuid: Optional[str] = None,
        datasource_id: str = "",
    ) -> "GitFaqLoader":
        repo_path = os.path.join(config.REPOS_LOCAL_DIR, project_name, datasource_name)
        os.makedirs(repo_path, exist_ok=True)
        clone_url = _build_clone_url(creds, SimpleNamespace(link=repo_link))

        auth_header = None
        if creds and getattr(creds, "use_header_auth", False):
            auth_header = _build_auth_header(creds)

        return cls(
            repo_path=repo_path,
            clone_url=clone_url,
            branch=branch,
            files_filter=files_filter,
            auth_header=auth_header,
            request_uuid=request_uuid,
            datasource_id=datasource_id,
        )

    def _is_faq_file(self, file_path: str) -> bool:
        """File-filter hook (receives an absolute path): keep only ``.md`` files matching ``files_filter``.

        Same gitignore-style include/exclude mechanism as the Git code datasource
        (``check_file_type``); an empty filter matches every ``.md`` file in the repo.
        """
        rel_path = os.path.relpath(file_path, self.repo_path).replace(os.sep, "/")
        if not rel_path.endswith(".md"):
            return False

        return check_file_type(
            file_name=rel_path,
            files_filter=self.files_filter,
            repo_local_path="",
            excluded_files=[],
        )

    def lazy_load(self) -> Iterator[Document]:
        """
        Yield one parsed FAQ article per matching file.
        """
        if not self.repo:
            self._init_repo()
        for item in self.repo.tree().traverse():
            if self._should_skip_item(item):
                continue
            yield from self._process_faq_file(item, os.path.join(self.repo_path, item.path))

    def get_load_stats(self) -> dict:
        """Post-load stats: how many FAQ files were actually skipped (parse/empty/decode)."""
        return {
            BaseDatasourceLoader.SKIPPED_DOCUMENTS_KEY: len(self._skipped_files),
            BaseDatasourceLoader.FAILED_DOCUMENTS_KEY: 0,
        }

    def fetch_remote_stats(self) -> dict:
        """
        Count the FAQ files expected to be indexed (no content reads).
        """
        if not self.repo:
            self._init_repo()
        total_documents = 0
        for item in self.repo.tree().traverse():
            if not isinstance(item, Blob):
                continue
            if self._is_faq_file(os.path.join(self.repo_path, item.path)):
                total_documents += 1

        # NOTE: pre-count is an upper bound — files that fail parsing are only
        # known during the actual load and are reported via get_load_stats().
        return {
            BaseDatasourceLoader.DOCUMENTS_COUNT_KEY: total_documents,
            BaseDatasourceLoader.TOTAL_DOCUMENTS_KEY: total_documents,
            BaseDatasourceLoader.SKIPPED_DOCUMENTS_KEY: 0,
            BaseDatasourceLoader.FAILED_DOCUMENTS_KEY: 0,
        }

    def _process_faq_file(self, item, file_path: str) -> list[Document]:
        """
        Read one ``.md`` file and parse it into a single FAQ-article document.
        Parse failures skip the file with a warning; they never abort the run.
        """
        rel_file_path = os.path.relpath(file_path, self.repo_path).replace(os.sep, "/")
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            text_content = self._decode_content(content, file_path)
            if text_content is None:
                logger.error(f"Could not decode FAQ file {rel_file_path}")
                self._skipped_files.append(rel_file_path)
                return []
            article = parse_faq_markdown(rel_path=rel_file_path, raw=text_content)
        except FaqParseError as e:
            logger.warning(f"Skipping FAQ file with invalid structure {rel_file_path}: {e}")
            self._skipped_files.append(rel_file_path)
            return []
        except (FileNotFoundError, IsADirectoryError):
            logger.error(f"Error reading file {file_path}", exc_info=True)
            return []

        if not article.content.strip():
            logger.warning(f"Skipping empty FAQ article {rel_file_path}")
            self._skipped_files.append(rel_file_path)
            return []

        metadata = {
            **asdict(article),
            "source": rel_file_path,
            "file_path": rel_file_path,
            "file_name": item.name,
            "file_type": ".md",
        }
        return [Document(page_content=article.content, metadata=metadata)]


def cleanup_local_repo(project_name: str, datasource_name: str) -> None:
    """Remove the local FAQ clone directory (used on datasource deletion)."""
    repo_path = os.path.join(config.REPOS_LOCAL_DIR, project_name, datasource_name)
    shutil.rmtree(repo_path, ignore_errors=True)
