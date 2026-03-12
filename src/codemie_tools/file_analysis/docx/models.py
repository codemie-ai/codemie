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

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field


class QueryType(str, Enum):
    """Enumeration of available query types for DOCX processing."""

    TEXT = "text"
    TEXT_WITH_METADATA = "text_with_metadata"
    TEXT_WITH_IMAGES = "text_with_images"
    STRUCTURE_ONLY = "structure_only"
    IMAGE_EXTRACTION = "image_extraction"
    TABLE_EXTRACTION = "table_extraction"
    SUMMARY = "summary"
    ANALYZE = "analyze"


class AnalysisType(str, Enum):
    """Enumeration of content analysis types."""

    FULL = "full"
    TEXT = "text"
    IMAGES = "images"
    TABLES = "tables"
    STRUCTURE = "structure"


@dataclass
class Position:
    """Represents a position within a document."""

    page: int
    x: float
    y: float


@dataclass
class ImageData:
    """Structure for image data extracted from documents."""

    content: bytes
    format: str
    text_content: Optional[str] = None
    position: Optional[Position] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TableData:
    """Structure for table data extracted from documents."""

    rows: List[List[str]]
    position: Optional[Position] = None
    headers: Optional[List[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HeaderInfo:
    """Information about document headers."""

    level: int
    text: str
    position: Position


@dataclass
class ParagraphInfo:
    """Information about document paragraphs."""

    text: str
    style: str
    position: Position


@dataclass
class SectionInfo:
    """Information about document sections."""

    title: str
    content: List[Any]  # Can contain paragraphs, tables, or other elements
    level: int


@dataclass
class StyleInfo:
    """Information about document styles."""

    name: str
    font: str
    size: int
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = "black"


@dataclass
class FormattingInfo:
    """Information about document formatting."""

    styles: Dict[str, StyleInfo] = field(default_factory=dict)
    page_width: float = 0.0
    page_height: float = 0.0
    margins: Dict[str, float] = field(default_factory=dict)


@dataclass
class DocumentStructure:
    """Structure information about a document."""

    headers: List[HeaderInfo] = field(default_factory=list)
    paragraphs: List[ParagraphInfo] = field(default_factory=list)
    sections: List[SectionInfo] = field(default_factory=list)
    styles: List[StyleInfo] = field(default_factory=list)


@dataclass
class DocumentContent:
    """Comprehensive structure for document content."""

    text: str
    images: List[ImageData] = field(default_factory=list)
    tables: List[TableData] = field(default_factory=list)
    structure: DocumentStructure = field(default_factory=DocumentStructure)
    metadata: Dict[str, Any] = field(default_factory=dict)
    formatting: FormattingInfo = field(default_factory=FormattingInfo)


@dataclass
class TextAnalysis:
    """Results of text analysis."""

    summary: str
    key_topics: List[str]
    sentiment: str
    language: str
    readability_score: float


@dataclass
class ImageAnalysis:
    """Results of image analysis."""

    count: int
    types: List[str]
    described_content: List[str]


@dataclass
class StructureAnalysis:
    """Results of document structure analysis."""

    sections: int
    heading_levels: int
    table_count: int
    complexity_score: float


@dataclass
class AnalysisResult:
    """Comprehensive analysis results."""

    summary: str
    key_topics: List[str] = field(default_factory=list)
    sentiment: str = ""
    language: str = ""
    readability_score: float = 0.0
    image_analysis: Optional[ImageAnalysis] = None
    structure_analysis: Optional[StructureAnalysis] = None
    additional_insights: Dict[str, Any] = field(default_factory=dict)


class DocxToolInput(BaseModel):
    """Input schema for the DocxTool."""

    query: QueryType = Field(
        ...,
        description=(
            "Type of operation to perform on the DOCX document(s):"
            "'text' to extract plain text content, "
            "'text_with_metadata' to extract text with document metadata, "
            "'text_with_images' to extract text including OCR from images, "
            "'structure_only' to extract only document structure, "
            "'image_extraction' to extract images from the document, "
            "'table_extraction' to extract tables from the document, "
            "'summary' to generate a summary of the document, "
            "'analyze' to perform comprehensive document analysis."
        ),
    )
    instructions: Optional[str] = Field(
        None,
        description=(
            "Natural language instructions for document operations. "
            "Used for 'analyze' queries. "
            "For example: 'Analyze the sentiment of this document'."
        ),
    )
    pages: Optional[str] = Field(
        None,
        description=(
            "Specific pages to analyze. Can be a single page number (e.g., '1'), "
            "comma-separated values (e.g., '1,3,5'), a range (e.g., '1-4'), or 'all' for all pages. "
            "If not provided or set to 'all', all pages will be processed."
        ),
    )
