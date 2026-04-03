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

import base64
import mimetypes
import os
import re
import shutil
from typing import Any, Iterator
from urllib.parse import urlunparse, urlparse, quote

from codemie.core.utils import check_file_type
from git import Blob, Repo, Submodule
from git.exc import GitCommandError
from langchain_community.document_loaders import GitLoader
from langchain_core.documents import Document

from codemie.configs import logger
from codemie.core.models import GitRepo
from codemie.datasource.datasources_config import CODE_CONFIG
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes, is_binary_extractable
from codemie.datasource.loader.git_auth_utils import get_github_app_token
from codemie.rest_api.models.settings import Credentials

# List of specific MIME types to exclude
excluded_mime_types = [
    'application/x-dosexec',  # .exe
    'application/java-archive',  # .jar
    'application/zip',  # .zip
    'application/x-rar-compressed',  # .rar
    'application/rtf',  # .rtf
    'application/octet-stream',  # .dll, .so, .lib, etc.
    'application/msword',  # .doc
    'application/x-iso9660-image',  # .iso
    'application/x-tar',  # .tar
    'application/x-7z-compressed',  # .7z
    'application/x-bzip2',  # .bz2
    'application/x-gzip',  # .gz
    'application/x-lzip',  # .lz
    'application/x-xz',  # .xz
    'application/x-msdownload',  # .dll, .exe
    'application/x-debian-package',  # .deb
    'application/vnd.apple.installer+xml',  # .pkg
    'application/x-csh',  # .csh
    'application/x-shockwave-flash',  # .swf
    'application/x-cpio',  # .cpio
    'application/x-sv4cpio',  # .sv4cpio
    'application/x-sv4crc',  # .sv4crc
    'application/x-rpm',  # .rpm
    'application/x-lzh-compressed',  # .lzh
    'application/x-msi',  # .msi
    'application/epub+zip',  # .epub
    'application/x-bittorrent',  # .torrent
    'application/x-xml',  # .xml (if you consider this binary)
    # Legacy formats
    'application/mac-binhex40',  # .hqx
    'application/mac-compactpro',  # .cpt
    'application/x-apple-diskimage',  # .dmg
    'application/x-stuffit',  # .sit
    'application/x-stuffitx',  # .sitx
    'application/x-gtar',  # .gtar
    'application/x-ustar',  # .ustar
    'application/x-dvi',  # .dvi
    'application/x-latex',  # .latex
    # Add more specific MIME types as needed
]


def _build_clone_url(creds, repo):
    if not creds:
        return repo.link

    # Get token: either PAT or generate GitHub App token
    if hasattr(creds, 'is_github_app') and creds.is_github_app:
        token = get_github_app_token(creds.app_id, creds.private_key, creds.installation_id)
        token_name = "x-access-token"
    elif creds.token:
        token = creds.token
        token_name = creds.token_name or "oauth2"
    else:
        return repo.link

    parsed_url = urlparse(repo.link)
    auth_part = f"{quote(token_name, safe='')}:{quote(token)}"

    if "@" in parsed_url.netloc:
        new_netloc = f"{auth_part}@{parsed_url.netloc.split('@')[1]}"
    else:
        new_netloc = f"{auth_part}@{parsed_url.netloc}"

    return urlunparse(parsed_url._replace(netloc=new_netloc))


def _build_auth_header(creds):
    """
    Build HTTP Basic Authorization header for git clone.
    Returns git config option string for http.extraHeader or None if no credentials.

    Example: 'http.extraHeader=Authorization: Basic <base64_token>'
    """
    if not creds:
        return None

    # Get token: either PAT or generate GitHub App token
    if hasattr(creds, 'is_github_app') and creds.is_github_app:
        token = get_github_app_token(creds.app_id, creds.private_key, creds.installation_id)
    elif creds.token:
        token = creds.token
    else:
        return None

    # Encode ":token" in base64 for Basic auth (empty username, token as password)
    auth_string = f":{token}"
    b64_token = base64.b64encode(auth_string.encode()).decode()

    return f"http.extraHeader=Authorization: Basic {b64_token}"


def _sanitize_git_command(command: str | list) -> str:
    """
    Sanitize git command to mask authorization tokens in error messages.

    Masks Base64 tokens in http.extraHeader Authorization headers to prevent
    credential exposure in logs and error messages.

    Args:
        command: Git command as string or list of arguments

    Returns:
        Sanitized command string with masked credentials
    """
    # Convert to string if it's a list
    cmd_str = ' '.join(command) if isinstance(command, list) else str(command)

    # Mask Authorization: Basic <base64_token> in http.extraHeader
    # Pattern matches: http.extraHeader=Authorization: Basic <base64_string>
    pattern = r'(http\.extraHeader=Authorization:\s*Basic\s+)([A-Za-z0-9+/]+=*)'
    sanitized = re.sub(pattern, r'\1' + '*' * 10, cmd_str)

    return sanitized


class GitBatchLoader(GitLoader, BaseDatasourceLoader):
    FILTERED_DOCUMENTS_KEY = 'filtered_documents'
    TOTAL_SIZE_KB_KEY = 'total_size_kb'
    AVERAGE_FILE_SIZE_KEY = 'average_file_size_bytes'
    UNIQUE_EXTENSIONS_KEY = 'unique_extensions'

    def __init__(self, *args, **kwargs):
        # Extract auth_header and request_uuid before passing to parent
        self.auth_header = kwargs.pop('auth_header', None)
        self.request_uuid: str | None = kwargs.pop('request_uuid', None)
        super().__init__(*args, **kwargs)
        self.repo = None

    @classmethod
    def create_loader(cls, repo: GitRepo, creds: Credentials, request_uuid: str | None = None):
        repo_local_path = repo.get_repo_local_file_path()
        clone_url = _build_clone_url(creds, repo)

        # Build auth header if use_header_auth is enabled
        auth_header = None
        if creds and getattr(creds, 'use_header_auth', False):
            auth_header = _build_auth_header(creds)

        return cls(
            clone_url=clone_url,
            repo_path=repo_local_path,
            branch=repo.branch,
            auth_header=auth_header,
            request_uuid=request_uuid,
            file_filter=lambda file_path: check_file_type(
                file_name=file_path,
                files_filter=repo.files_filter,
                repo_local_path=repo_local_path,
                excluded_files=CODE_CONFIG.excluded_extensions.get_full_code_exclusions(),
            ),
        )

    def _init_repo(self) -> None:
        if not os.path.exists(self.repo_path) and self.clone_url is None:
            raise ValueError(f"Path {self.repo_path} does not exist")

        if self.clone_url:
            shutil.rmtree(self.repo_path, ignore_errors=True)

            # Build multi_options with depth and filter
            multi_options = ['--depth=1', '--filter=blob:none']

            # Add auth header if provided
            allow_unsafe = False
            if self.auth_header:
                # Wrap the entire config value in quotes to handle spaces
                quoted_header = f'"{self.auth_header}"'
                multi_options.extend(['-c', quoted_header])
                allow_unsafe = True  # Required for -c flag in GitPython
                logger.debug(f"Using header-based authentication for git clone. Repo={self.repo_path}")

            try:
                self.repo = Repo.clone_from(
                    self.clone_url,
                    self.repo_path,
                    progress=self._print_progress,
                    branch=self.branch,
                    multi_options=multi_options,
                    allow_unsafe_options=allow_unsafe,
                )
            except GitCommandError as e:
                # Sanitize command line to mask authorization tokens
                sanitized_command = _sanitize_git_command(e.command)
                # Create new error with sanitized command
                sanitized_error = GitCommandError(sanitized_command, e.status, e.stderr, e.stdout)
                logger.error(f"Git clone failed for repo {self.repo_path}. Error: {sanitized_error}")
                raise sanitized_error from e
        else:
            self.repo = Repo(self.repo_path)

    def fetch_remote_stats(self) -> dict[str, Any]:
        """
        Get the number of files in the repository.
        """
        if not self.repo:
            self._init_repo()
        files = []
        unique_extensions = set()
        total_size = 0
        file_sizes = []
        skipped_files_count = 0
        filtered_items = []
        processed_count = 0
        last_log_time = 0

        for item in self.repo.tree().traverse():
            processed_count += 1

            # Log progress every 1000 files or every 5 seconds (whichever comes first)
            import time

            current_time = time.time()
            if processed_count % 1000 == 0 or (current_time - last_log_time) >= 5:
                logger.info(
                    f"Scanning repository progress: {processed_count} items processed, "
                    f"{len(files)} files matched, {skipped_files_count} skipped"
                )
                last_log_time = current_time

            if self._should_skip_item(item):
                if GitBatchLoader._is_blob(item):
                    skipped_files_count += 1
                    if self._is_filtered(item):
                        filtered_items.append(item.path)
                continue
            file_size = item.size
            total_size += file_size
            file_sizes.append(file_size)
            unique_extensions.add(os.path.splitext(item.path)[1])
            files.append(item)

        logger.info(
            f"Repository scan completed: {processed_count} total items, "
            f"{len(files)} files matched, {skipped_files_count} skipped"
        )
        average_file_size = total_size / len(files) if files else 0
        return {
            self.DOCUMENTS_COUNT_KEY: len(files),
            self.TOTAL_DOCUMENTS_KEY: skipped_files_count + len(files),
            self.SKIPPED_DOCUMENTS_KEY: skipped_files_count,
            self.FILTERED_DOCUMENTS_KEY: filtered_items,
            self.TOTAL_SIZE_KB_KEY: total_size / 1024,
            self.AVERAGE_FILE_SIZE_KEY: average_file_size,
            self.UNIQUE_EXTENSIONS_KEY: sorted(unique_extensions),
        }

    def lazy_load(self) -> Iterator[Document]:
        """
        Process repo files in batches and yield a list of documents.
        """
        if not self.repo:
            self._init_repo()
        for item in self.repo.tree().traverse():
            if self._should_skip_item(item):
                continue
            yield from self._process_file(item, os.path.join(self.repo_path, item.path))

    def _should_skip_item(self, item: Any):
        if isinstance(item, Submodule):
            logger.debug(f"Skip submodule item. File={item.path}")
            return True
        file_path = os.path.join(self.repo_path, item.path)
        if os.path.islink(file_path):
            logger.debug(f"File is a symlink. File={item.path}")
            return True
        if not GitBatchLoader._is_blob(item):
            logger.debug(f"File is not a blob. File={item.path}")
            return True
        if self._is_unsupported_mime_type(item.path):
            logger.debug(f"File mime_type is not supported. File={item.path}")
            return True
        # uses filter to skip files
        if self._is_filtered(item):
            logger.debug(f"Skip file due to file filter. File={file_path}")
            return True
        return False

    def _is_filtered(self, item: Any):
        return self.file_filter and not self.file_filter(os.path.join(self.repo_path, item.path))

    @staticmethod
    def _is_blob(item: Any):
        return isinstance(item, Blob)

    @classmethod
    def _is_unsupported_mime_type(cls, item_path):
        """
        Determines if a file is binary based on its MIME type and extension, or by reading its content.
        If binary or couldn't be read, file should be skipped from processing

        :param item_path: Path to the file
        :return: True if the file is binary, False otherwise
        """
        if is_binary_extractable(item_path):
            return False  # always allow binary-extractable formats
        mime_type, _ = mimetypes.guess_type(item_path, strict=False)
        return mime_type in excluded_mime_types or (
            mime_type and mime_type.startswith(('image', 'video', 'audio', 'application/vnd', 'application/x-font'))
        )

    def _process_file(self, item, file_path: str) -> list[Document]:
        """
        Load file by file_path and decode its contents forming Document instances.
        Binary files (PDF, DOCX, XLSX, PPTX, MSG, images) are routed through
        extract_documents_from_bytes(); text files use UTF-8 decoding.
        """
        rel_file_path = os.path.relpath(file_path, self.repo_path)
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            if is_binary_extractable(item.name):
                return self._process_binary_file(content, rel_file_path, item.name)
            ext = os.path.splitext(item.name)[1].lower()
            text_content = self._decode_content(content, file_path)
            if text_content is None:
                return []
            metadata = {
                "source": rel_file_path,
                "file_path": rel_file_path,
                "file_name": item.name,
                "file_type": ext,
            }
            return [Document(page_content=text_content, metadata=metadata)]
        except (FileNotFoundError, IsADirectoryError):
            logger.error(f"Error reading file {file_path}", exc_info=True)
            return []

    def _process_binary_file(self, content: bytes, rel_file_path: str, file_name: str) -> list[Document]:
        """
        Extract documents from binary file bytes using extract_documents_from_bytes.
        """
        try:
            documents = extract_documents_from_bytes(
                file_bytes=content,
                file_name=file_name,
                request_uuid=self.request_uuid,
            )
            for doc in documents:
                doc.metadata["source"] = rel_file_path
                doc.metadata["file_path"] = rel_file_path
                doc.metadata["file_name"] = file_name
                doc.metadata["file_type"] = os.path.splitext(file_name)[1].lower()
            logger.debug(f"Extracted {len(documents)} document(s) from binary file {rel_file_path}")
            return documents
        except Exception as e:
            logger.warning(
                f"Failed to extract binary file {rel_file_path} ({type(e).__name__}): {e}",
                exc_info=True,
            )
            return []

    @classmethod
    def _decode_content(cls, content: bytes, file_path: str) -> str | None:
        try:
            # If file has non-utf characters, escaping them.
            return content.decode("utf-8", errors="backslashreplace")
        except UnicodeDecodeError:
            logger.error(f"Decoding error for: {file_path}. Trying with 'latin-1' encoding.", exc_info=True)
            try:
                return content.decode("latin-1")
            except UnicodeDecodeError:
                logger.error(f"Decoding error for: {file_path} with 'latin-1' encoding.", exc_info=True)
                return None

    def _print_progress(self, *args):
        has_valid_args = all((args[1], args[2])) and float(args[2]) != 0
        if has_valid_args:
            percentage = round(float(args[1]) / float(args[2]) * 100)
            logger.debug(f"Pulling repo {self.repo_path}. Branch={self.branch}. Progress={percentage}% ... {args}")
