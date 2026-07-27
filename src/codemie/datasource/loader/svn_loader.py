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

import mimetypes
import os
from typing import Any, Iterator

from langchain_core.documents import Document

from codemie.configs import logger
from codemie.core.models import SVNRepo
from codemie.core.utils import check_file_type
from codemie.datasource.datasources_config import CODE_CONFIG, SVN_CONFIG
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes, is_binary_extractable
from codemie.datasource.loader.svn_client import SVNClientError, SvnClient
from codemie.rest_api.models.settings import SVNCredentials

# Reuse the same MIME exclusion list as the Git loader
from codemie.datasource.loader.git_loader import excluded_mime_types


def _build_branch_url(base_url: str, branch: str) -> str:
    """Construct the full SVN branch URL.

    trunk → <base>/trunk
    branches/feature-x → <base>/branches/feature-x
    any other value → <base>/<branch>
    """
    base_url = base_url.strip().rstrip("/")
    branch = branch.strip().strip("/")
    return f"{base_url}/{branch}"


class SVNBatchLoader(BaseDatasourceLoader):
    FILTERED_DOCUMENTS_KEY = "filtered_documents"
    TOTAL_SIZE_KB_KEY = "total_size_kb"
    HEAD_REVISION_KEY = "head_revision"

    KIND_KEY = "kind"
    KIND_DIR = "dir"
    KIND_FILE = "file"
    SIZE_KEY = "size"

    def __init__(
        self,
        svn_repo: SVNRepo,
        creds: SVNCredentials,
        request_uuid: str | None = None,
        datasource_id: str = "",
    ):
        self._repo = svn_repo
        self._creds = creds
        self._request_uuid = request_uuid
        self._datasource_id = datasource_id
        self._branch_url = _build_branch_url(svn_repo.link, svn_repo.branch)
        self._skipped_count = 0
        self._failed_count = 0

    @classmethod
    def create_loader(
        cls,
        svn_repo: SVNRepo,
        creds: SVNCredentials,
        request_uuid: str | None = None,
        datasource_id: str = "",
    ) -> "SVNBatchLoader":
        return cls(
            svn_repo=svn_repo,
            creds=creds,
            request_uuid=request_uuid,
            datasource_id=datasource_id,
        )

    @classmethod
    def test_connection(cls, url: str, branch: str, creds: SVNCredentials) -> dict[str, Any]:
        """Verify connectivity using the svn CLI and return the HEAD revision."""
        cls._ensure_svn_available()
        branch_url = _build_branch_url(url.rstrip("/"), branch.strip("/"))
        client = SvnClient(branch_url, creds)
        head_revision = client.get_latest_revnum()
        return {cls.HEAD_REVISION_KEY: head_revision}

    def fetch_remote_stats(self) -> dict[str, Any]:
        """Verify connectivity using the svn CLI and return HEAD revision."""
        self._ensure_svn_available()
        client = SvnClient(self._branch_url, self._creds)
        head_revision = client.get_latest_revnum()
        logger.info(f"SVN HEAD revision for {self._branch_url}: {head_revision}")
        return {
            self.DOCUMENTS_COUNT_KEY: 0,
            self.HEAD_REVISION_KEY: head_revision,
        }

    def get_load_stats(self) -> dict[str, Any]:
        return {
            self.SKIPPED_DOCUMENTS_KEY: self._skipped_count,
            self.FAILED_DOCUMENTS_KEY: self._failed_count,
        }

    def lazy_load(self) -> Iterator[Document]:
        """Fetch SVN repository contents via svn CLI and yield one Document per file."""
        self._ensure_svn_available()
        client = SvnClient(self._branch_url, self._creds)
        revision = client.get_latest_revnum()
        yield from self._walk_remote(client, "", revision, "")

    def _walk_remote(
        self,
        client: SvnClient,
        remote_path: str,
        revision: int,
        rel_prefix: str,
    ) -> Iterator[Document]:
        dirents = client.get_dir(remote_path, revision)
        for name, dirent in dirents.items():
            kind = dirent.get(self.KIND_KEY)
            child_remote = f"{remote_path}/{name}" if remote_path else name
            child_rel = f"{rel_prefix}/{name}" if rel_prefix else name
            if kind == self.KIND_DIR:
                yield from self._walk_remote(client, child_remote, revision, child_rel)

            elif kind == self.KIND_FILE:
                size_kb = (dirent.get(self.SIZE_KEY) or 0) / 1024

                if not self._should_skip(child_rel, size_kb):
                    yield from self._fetch_and_process(client, child_remote, revision, child_rel, name)

    def _fetch_and_process(
        self,
        client: SvnClient,
        remote_path: str,
        revision: int,
        rel_path: str,
        fname: str,
    ) -> list[Document]:
        try:
            content = client.get_file(remote_path, revision)
            return self._process_content(content, rel_path, fname)
        except Exception:
            logger.error(f"Error fetching SVN file {remote_path}", exc_info=True)
            self._failed_count += 1
            return []

    def _should_skip(self, rel_path: str, size_kb: float) -> bool:
        if size_kb > SVN_CONFIG.max_file_size_kb:
            logger.debug(f"Skip oversized file ({size_kb:.0f} KB): {rel_path}")
            self._skipped_count += 1
            return True

        if self._is_unsupported_mime_type(rel_path):
            logger.debug(f"Skip unsupported MIME type: {rel_path}")
            self._skipped_count += 1
            return True

        if not check_file_type(
            file_name=rel_path,
            files_filter=self._repo.files_filter,
            repo_local_path="",
            excluded_files=CODE_CONFIG.excluded_extensions.get_full_code_exclusions(),
        ):
            logger.debug(f"Skip filtered file: {rel_path}")
            self._skipped_count += 1
            return True

        return False

    @staticmethod
    def _is_unsupported_mime_type(path: str) -> bool:
        if is_binary_extractable(path):
            return False
        mime_type, _ = mimetypes.guess_type(path, strict=False)
        return mime_type in excluded_mime_types or (
            mime_type
            and mime_type.startswith(
                ("image/", "video/", "audio/", "font/", "model/", "application/vnd", "application/x-font")
            )
        )

    def _process_content(self, content: bytes, rel_path: str, fname: str) -> list[Document]:
        if is_binary_extractable(fname):
            return self._process_binary_file(content, rel_path, fname)

        ext = os.path.splitext(fname)[1].lower()
        text = self._decode_content(content, rel_path)
        if text is None:
            return []

        metadata = {
            "source": rel_path,
            "file_path": rel_path,
            "file_name": fname,
            "file_type": ext,
        }
        return [Document(page_content=text, metadata=metadata)]

    def _process_binary_file(self, content: bytes, rel_path: str, fname: str) -> list[Document]:
        try:
            docs = extract_documents_from_bytes(
                file_bytes=content,
                file_name=fname,
                request_uuid=self._request_uuid,
                datasource_id=self._datasource_id,
            )
            for doc in docs:
                doc.metadata["source"] = rel_path
                doc.metadata["file_path"] = rel_path
                doc.metadata["file_name"] = fname
                doc.metadata["file_type"] = os.path.splitext(fname)[1].lower()
            return docs
        except Exception as exc:
            logger.warning(
                f"Failed to extract binary SVN file {rel_path} ({type(exc).__name__}): {exc}",
                exc_info=True,
            )
            return []

    @staticmethod
    def _decode_content(content: bytes, path: str) -> str | None:
        try:
            return content.decode("utf-8", errors="backslashreplace")
        except UnicodeDecodeError:
            logger.error(f"UTF-8 decode error for {path}, retrying with latin-1", exc_info=True)
            try:
                return content.decode("latin-1")
            except UnicodeDecodeError:
                logger.error(f"latin-1 decode error for {path}", exc_info=True)
                return None

    @staticmethod
    def _ensure_svn_available() -> None:
        """Raise SVNClientError if the svn CLI is not available."""
        if not SvnClient.svn_is_available():
            raise SVNClientError("svn CLI is not installed or not on PATH")
