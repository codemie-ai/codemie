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

"""File with FileDatasourceLoader logic."""

import collections
import logging
from typing import Any, Union, List, Optional

from codemie_tools.base.file_object import FileObject
from langchain_core.documents import Document

from codemie.datasource.exceptions import (
    AllFilesTimedOutError,
    FileParsingTimeoutError,
    SkippedFileException,
    UnreadableWorkbookError,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.configs import config
from codemie.datasource.loader.file_processor_pool import maybe_pool_submit

logger = logging.getLogger(__name__)


class FilesDatasourceLoader(BaseDatasourceLoader):
    """
    FilesDatasourceLoader class to load data from supported file types.

    Supports various document formats including:
    - Text files (TXT, XML, YAML, YML, JSON) - handled directly
    - PDF files - using PDFPlumberLoader
    - Office documents (DOCX, PPTX, XLSX) - using specialized loaders
    - Web content (HTML)
    - E-books (EPUB)
    - Notebooks (IPYNB)
    - Email (MSG)
    - Archives (ZIP)
    - Media files (images, audio)

    Attributes:
        total_count_of_documents (int): Total count of documents to be loaded.
        file_repo (FileRepository): Repository for file operations.
        files_paths (list): List of file paths to be loaded.
        request_uuid (str): Request ID for tracking LLM usage.
    """

    def __init__(
        self,
        total_count_of_documents: int,
        files_paths: List[collections.namedtuple],
        csv_separator: str,
        request_uuid: Optional[str] = None,
        include_email_attachments: bool = True,
        datasource_id: str = "",
    ):
        """
        Initialize the FilesDatasourceLoader.

        Args:
            total_count_of_documents (int): Total count of documents to be loaded.
            files_paths (list): List of file paths to be loaded.
            csv_separator (str): CSV delimiter.
            request_uuid (str, optional): Request UUID for tracking LLM usage.
            include_email_attachments (bool): Extract attachments from EML/MSG files.
            datasource_id (str): Datasource identifier used to scope file storage for image loaders.
        """
        self.total_count_of_documents = total_count_of_documents
        self.file_repo = FileRepositoryFactory.get_current_repository()
        self.files_paths = files_paths
        self.request_uuid = request_uuid
        self._csv_separator = csv_separator
        self._include_email_attachments = include_email_attachments
        self._skipped_count = 0
        self._timed_out_count = 0
        self.datasource_id = datasource_id

    def fetch_remote_stats(self) -> dict[str, Any]:
        """
        Fetch the remote statistics.

        Returns:
            dict: The remote statistics with the document count.
        """
        return {
            self.DOCUMENTS_COUNT_KEY: self.total_count_of_documents,
            self.TOTAL_DOCUMENTS_KEY: self.total_count_of_documents,
        }

    def lazy_load(self) -> Union[Document, List[Document]]:
        """
        This method loads the whole Document of a given file.
        Depending on the file type a different loader is used.

        Yields:
            Union[Document, List[Document]]: Yields documents loaded from the files.
        """
        if config.ENABLE_FILE_MULTIPROCESSING:
            yield from self._load_docs_parallel()
        else:
            for file_data in self.files_paths:
                file = self.file_repo.read_file(file_data.name, file_data.owner)
                yield self._lazy_load_documents(file)

    def _load_docs_parallel(self):
        produced_documents = False
        for file_data in self.files_paths:
            file = self.file_repo.read_file(file_data.name, file_data.owner)
            try:
                docs = maybe_pool_submit(
                    extract_documents_from_bytes,
                    file.bytes_content(),
                    file.name,
                    self.request_uuid,
                    self._csv_separator,
                    self._include_email_attachments,
                    datasource_id=self.datasource_id,
                )
                if not docs:
                    self._skipped_count += 1
                else:
                    produced_documents = True
                yield docs
            except SkippedFileException:
                self._skipped_count += 1
                yield []
            except FileParsingTimeoutError as e:
                self._timed_out_count += 1
                logger.error(f"Failed to extract documents from file {file_data.name}: {e}")
            except UnreadableWorkbookError:
                # A corrupt/encrypted workbook is a genuine failure, not a skip. Propagate it
                # (like the serial path) so the actionable error surfaces instead of the file
                # being silently dropped with only a log line.
                raise
            except Exception as e:
                logger.error(f"Failed to extract documents from file {file_data.name}: {e}")

        if self._timed_out_count and not produced_documents:
            raise AllFilesTimedOutError(self._timed_out_count)

    def _lazy_load_documents(self, file: FileObject) -> List[Document]:
        """
        Lazy load documents from the file based on the file extension.

        Args:
            file (FileObject): The file object.

        Returns:
            List[Document]: A list of documents loaded from the file.
        """
        try:
            docs = extract_documents_from_bytes(
                file_bytes=file.bytes_content(),
                file_name=file.name,
                request_uuid=self.request_uuid,
                csv_separator=self._csv_separator,
                include_email_attachments=self._include_email_attachments,
                datasource_id=self.datasource_id,
            )
            if not docs:
                self._skipped_count += 1
            return docs
        except SkippedFileException:
            self._skipped_count += 1
            return []

    def get_load_stats(self) -> dict[str, int]:
        return {self.SKIPPED_DOCUMENTS_KEY: self._skipped_count, self.FAILED_DOCUMENTS_KEY: self._timed_out_count}
