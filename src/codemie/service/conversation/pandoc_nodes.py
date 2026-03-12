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

"""
Pandoc-compliant Document Tree Serializer

This module provides a node-based (tree) structure for building and serializing
Pandoc Markdown documents programmatically.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


# ============================================================================
# Base Node Classes
# ============================================================================


class Node(ABC):
    """Abstract base class for all document nodes."""

    @abstractmethod
    def serialize(self, context: 'SerializationContext') -> str:
        """Serialize the node to Pandoc Markdown."""
        pass

    def __str__(self) -> str:
        """Convenience method for serialization with default context."""
        return self.serialize(SerializationContext())


@dataclass
class SerializationContext:
    """Context for tracking serialization state."""

    indent_level: int = 0
    indent_str: str = "    "  # 4 spaces
    in_list: bool = False
    blank_line_before: bool = True
    output_format: str | None = None  # Target output format (pdf, docx, html, etc.)

    def indent(self) -> str:
        """Get current indentation string."""
        return self.indent_str * self.indent_level

    def with_indent(self, delta: int = 1) -> 'SerializationContext':
        """Create new context with modified indent level."""
        return SerializationContext(
            indent_level=self.indent_level + delta,
            indent_str=self.indent_str,
            in_list=self.in_list,
            blank_line_before=self.blank_line_before,
            output_format=self.output_format,
        )


# ============================================================================
# Document Root
# ============================================================================


@dataclass
class Document(Node):
    """Root document node containing metadata and content."""

    children: list[Node] = field(default_factory=list)

    def add(self, node: Node) -> 'Document':
        """Add a child node to the document."""
        self.children.append(node)
        return self

    def serialize(self, context: SerializationContext | None = None) -> str:
        if context is None:
            context = SerializationContext()

        parts = []

        # Serialize all children
        for i, child in enumerate(self.children):
            serialized = child.serialize(context)
            parts.append(serialized)

            # Add blank line between block elements (if not already present)
            if i < len(self.children) - 1 and not serialized.endswith("\n\n"):
                parts.append("")

        return "\n".join(parts)


# ============================================================================
# Block Elements
# ============================================================================


class HeadingLevel(Enum):
    """Heading levels 1-6."""

    H1 = 1
    H2 = 2
    H3 = 3
    H4 = 4
    H5 = 5
    H6 = 6


@dataclass
class Heading(Node):
    """Heading node."""

    text: str
    level: HeadingLevel

    def serialize(self, context: SerializationContext) -> str:
        prefix = "#" * self.level.value
        return f"{prefix} {self.text}"


@dataclass
class Paragraph(Node):
    """Text paragraph."""

    content: str

    def serialize(self, context: SerializationContext) -> str:
        return self.content


@dataclass
class CodeBlock(Node):
    """Fenced code block with optional language."""

    code: str
    language: str | None = None
    fence_char: str = "```"

    def serialize(self, context: SerializationContext) -> str:
        lang_str = self.language if self.language else ""
        code_lines = self.code.rstrip("\n")
        return f"{self.fence_char}{lang_str}\n{code_lines}\n{self.fence_char}"


@dataclass
class HorizontalRule(Node):
    """Horizontal rule/divider."""

    char: str = "-"  # Can be '-', '_', or '*'
    length: int = 3

    def serialize(self, context: SerializationContext) -> str:
        return self.char * self.length


@dataclass
class PageBreak(Node):
    """
    Format-specific page break using raw blocks.
    Supports multiple output formats via Pandoc raw_attribute extension.
    """

    formats: list[str] = field(default_factory=lambda: ["latex", "openxml", "html"])

    # Format-specific page break content
    _PAGE_BREAKS = {
        # For HTML output, use CSS page break
        "html": '<div style="page-break-after: always;"></div>',
        # For LaTeX PDF engines (pdflatex, xelatex, lualatex)
        "latex": r"\newpage",
        # For DOCX/PPTX (OpenXML)
        "openxml": '<w:p>\n  <w:r>\n    <w:br w:type="page"/>\n  </w:r>\n</w:p>',
        # Other formats
        "html5": '<div style="page-break-after: always;"></div>',
        "opendocument": '<text:p text:style-name="PageBreak"/>',
        "rtf": r"\page",
        "context": r"\page",
        "ms": ".bp",  # roff ms
        "beamer": r"\framebreak",  # For Beamer slides
    }

    def serialize(self, context: SerializationContext) -> str:
        """
        Serialize page breaks for all specified formats.
        Uses raw_attribute extension syntax: ```{=format}

        If context has output_format set, only emit that format.
        Otherwise emit all specified formats.
        """
        # If context specifies a format, use only that one
        formats_to_use = [self._map_output_format(context.output_format)] if context.output_format else self.formats

        parts = []

        for fmt in formats_to_use:
            if fmt in self._PAGE_BREAKS:
                content = self._PAGE_BREAKS[fmt]
                # Use raw block syntax with format specifier
                parts.append(f"```{{={fmt}}}\n{content}\n```")

        # Join with blank lines between different format blocks
        return "\n\n".join(parts)

    def _map_output_format(self, output_format: str) -> str:
        """Map output format to Pandoc format name.

        Note: PDF uses 'latex' because pdflatex is a LaTeX renderer.
        """
        format_map = {
            "pdf": "latex",  # pdflatex uses LaTeX for PDF
            "docx": "openxml",
            "html": "html",
            "pptx": "openxml",
        }
        return format_map.get(output_format.lower(), "html")


@dataclass
class RawContent(Node):
    """Raw markdown content."""

    content: str

    def serialize(self, context: SerializationContext) -> str:
        return self.content
