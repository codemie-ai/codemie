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
from typing import Any, Union, List, Optional

from codemie_tools.base.file_object import FileObject
from langchain_core.documents import Document

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes
from codemie.repository.repository_factory import FileRepositoryFactory


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
    ):
        """
        Initialize the FilesDatasourceLoader.

        Args:
            total_count_of_documents (int): Total count of documents to be loaded.
            files_paths (list): List of file paths to be loaded.
            csv_separator (str): CSV delimiter.
            request_uuid (str, optional): Request UUID for tracking LLM usage.
        """
        self.total_count_of_documents = total_count_of_documents
        self.file_repo = FileRepositoryFactory.get_current_repository()
        self.files_paths = files_paths
        self.request_uuid = request_uuid
        self._csv_separator = csv_separator

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
        for file_data in self.files_paths:
            file = self.file_repo.read_file(file_data.name, file_data.owner)
            yield self._lazy_load_documents(file)

    def _lazy_load_documents(self, file: FileObject) -> List[Document]:
        """
        Lazy load documents from the file based on the file extension.

        Args:
            file (FileObject): The file object.

        Returns:
            List[Document]: A list of documents loaded from the file.
        """
        return extract_documents_from_bytes(
            file_bytes=file.bytes_content(),
            file_name=file.name,
            request_uuid=self.request_uuid,
            csv_separator=self._csv_separator,
        )
