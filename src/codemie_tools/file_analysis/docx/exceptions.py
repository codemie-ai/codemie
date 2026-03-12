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


class DocxProcessingError(Exception):
    """Base exception for DOCX processing errors."""

    pass


class DocumentReadError(DocxProcessingError):
    """Error reading DOCX document."""

    pass


class DocumentWriteError(DocxProcessingError):
    """Error writing DOCX document."""

    pass


class CorruptedDocumentError(DocxProcessingError):
    """Error when document is corrupted."""

    pass


class UnsupportedFormatError(DocxProcessingError):
    """Error when document format is not supported."""

    pass


class AnalysisError(DocxProcessingError):
    """Error during document analysis."""

    pass


class InsufficientContentError(DocxProcessingError):
    """Error when document content is insufficient for analysis."""

    pass


class ExtractionError(DocxProcessingError):
    """Error extracting content from document."""

    pass


class OCRError(DocxProcessingError):
    """Error during OCR processing."""

    pass


class ImageExtractionError(DocxProcessingError):
    """Error extracting images from document."""

    pass


class TableExtractionError(DocxProcessingError):
    """Error extracting tables from document."""

    pass


class InvalidPageSelectionError(DocxProcessingError):
    """Error when page selection format is invalid."""

    pass
