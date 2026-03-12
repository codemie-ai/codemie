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

"""Unified message exporter service with node-based document structure.

This module provides a unified exporter service that refactors and combines:
- AssistantConversationMessageExporter (full conversation export)
- MessageExportService (single message export)

Key features:
- Node-based document structure for Pandoc
- markitdown integration for content parsing
- Support for PDF, DOCX, and PPTX formats
- Full conversation and single message export
"""

import re
import tempfile
from abc import ABC, abstractmethod
from enum import Enum
from typing import Iterator

import pypandoc
from markitdown import MarkItDown

from codemie.core.models import ChatRole
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage, Thought
from codemie.service.conversation.export_utils import ExportUtils
from codemie.service.conversation.pandoc_nodes import (
    CodeBlock,
    Document,
    Heading,
    HeadingLevel,
    HorizontalRule,
    Node,
    PageBreak,
    Paragraph,
    RawContent,
)
from ...configs import logger


# ============================================================================
# Export Configuration
# ============================================================================


class ExportFormat(str, Enum):
    """Enum for conversation export formats"""

    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"

    @property
    def content_type(self) -> str:
        """Get the content type for the export format"""
        content_type_map = {
            ExportFormat.PDF: "application/pdf",
            ExportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ExportFormat.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }

        return content_type_map.get(self, "")


# ============================================================================
# Content Parser with MarkItDown
# ============================================================================


class ContentParser:
    """Parse message content using markitdown for enhanced formatting."""

    def __init__(self):
        """Initialize the content parser with markitdown."""
        self.markitdown = MarkItDown()

    def parse_message_content(self, content: str) -> list[Node]:
        """Parse message content into document nodes.

        Args:
            content: The raw message content

        Returns:
            List of document nodes representing the parsed content
        """
        # For now, parse as raw content and let markitdown handle complex formats
        # In future, we can add more sophisticated parsing logic
        return [RawContent(content)]

    def parse_thought_content(self, thought: Thought) -> list[Node]:
        """Parse thought content into document nodes.

        Args:
            thought: The thought to parse

        Returns:
            List of document nodes representing the parsed thought
        """
        return self.parse_thought_content_from_text(thought.message)

    def parse_thought_content_from_text(self, content: str) -> list[Node]:
        """Parse thought content from text into document nodes.

        Args:
            content: The thought content text to parse

        Returns:
            List of document nodes representing the parsed content
        """
        # Detect code blocks and parse accordingly
        if self._contains_code_block(content):
            return self._parse_code_blocks(content)

        # Otherwise return as raw content
        return [RawContent(content)]

    def _contains_code_block(self, content: str) -> bool:
        """Check if content contains code blocks."""
        return "```" in content or "~~~" in content

    def _parse_code_blocks(self, content: str) -> list[Node]:
        """Parse content with code blocks into separate nodes."""
        nodes = []
        # Simple regex to detect fenced code blocks
        code_block_pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)

        last_end = 0
        for match in code_block_pattern.finditer(content):
            # Add text before code block
            if match.start() > last_end:
                text = content[last_end : match.start()].strip()
                if text:
                    nodes.append(RawContent(text))

            # Add code block
            language = match.group(1) or ""
            code = match.group(2)
            nodes.append(CodeBlock(code=code, language=language))

            last_end = match.end()

        # Add remaining text after last code block
        if last_end < len(content):
            text = content[last_end:].strip()
            if text:
                nodes.append(RawContent(text))

        return nodes if nodes else [RawContent(content)]


# ============================================================================
# Format-Specific Processors
# ============================================================================


class FormatProcessor(ABC):
    """Base class for format-specific document processors."""

    @abstractmethod
    def process_document(self, document: Document) -> Document:
        """Process document for specific format.

        Args:
            document: The document to process

        Returns:
            Processed document
        """
        pass

    @abstractmethod
    def get_pandoc_args(self) -> list[str]:
        """Get format-specific pandoc arguments.

        Returns:
            List of pandoc command-line arguments
        """
        pass


class PDFProcessor(FormatProcessor):
    """Processor for PDF export using pdflatex (LaTeX engine)."""

    def process_document(self, document: Document) -> Document:
        """PDF needs no special processing - standard markdown works well."""
        return document

    def get_pandoc_args(self) -> list[str]:
        """PDF-specific pandoc arguments for pdflatex."""
        return [
            '--pdf-engine=pdflatex',
            '-V',
            'geometry:margin=1in',
            '-V',
            'fontsize=11pt',
            # Load lmodern for modern Latin fonts (required for proper font rendering)
            # Load hyperref and configure it to avoid pzdr font requirement
            # Add fvextra for better code formatting with long lines
            '-V',
            (
                'header-includes='
                '\\usepackage{lmodern}'
                '\\usepackage[T1]{fontenc}'
                '\\usepackage{hyperref}'
                '\\hypersetup{hidelinks,pdfborder={0 0 0},colorlinks=false}'
                '\\usepackage{fvextra}'
                '\\DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\\\\{\\}}'
            ),
            # Use listings package for better code handling and escaping
            '--listings',
        ]


class DOCXProcessor(FormatProcessor):
    """Processor for DOCX (Word) export."""

    def process_document(self, document: Document) -> Document:
        """DOCX needs no special processing - standard markdown works well."""
        return document

    def get_pandoc_args(self) -> list[str]:
        """DOCX-specific pandoc arguments."""
        return []


class PPTXProcessor(FormatProcessor):
    """Processor for PPTX (PowerPoint) export with slide breaks.

    PPTX structure is fundamentally different from PDF/DOCX:
    - Level 1 (#) and Level 2 (##) headers create NEW slides
    - Level 3 (###) headers are content WITHIN a slide
    - Horizontal rules (---) create slide breaks
    - NO page break raw blocks - use HorizontalRule instead
    """

    def process_document(self, document: Document) -> Document:
        """Process document for PPTX by removing PageBreak nodes and using HorizontalRule.

        PPTX doesn't support raw page break blocks - it uses horizontal rules
        and heading levels to determine slide structure.
        """
        processed_children = []
        after_level3 = False

        for _i, child in enumerate(document.children):
            # Skip PageBreak nodes - PPTX doesn't use them
            if isinstance(child, PageBreak):
                continue

            # Add slide breaks between level 3 headings in detailed sections
            if isinstance(child, Heading) and child.level == HeadingLevel.H3:
                if after_level3:
                    processed_children.append(HorizontalRule())
                after_level3 = True

            processed_children.append(child)

        document.children = processed_children
        return document

    def get_pandoc_args(self) -> list[str]:
        """PPTX-specific pandoc arguments."""
        return []


class ProcessorFactory:
    """Factory for creating format-specific processors."""

    @staticmethod
    def get_processor(export_format: ExportFormat) -> FormatProcessor:
        """Get the appropriate processor for the export format.

        Args:
            export_format: The target export format

        Returns:
            Format-specific processor
        """
        processors = {
            ExportFormat.PDF: PDFProcessor(),
            ExportFormat.DOCX: DOCXProcessor(),
            ExportFormat.PPTX: PPTXProcessor(),
        }
        return processors.get(export_format, DOCXProcessor())


# ============================================================================
# Document Builder
# ============================================================================


class DocumentBuilder:
    """Build document structure from conversation messages."""

    def __init__(self, export_format: ExportFormat):
        """Initialize the document builder.

        Args:
            export_format: Target export format
        """
        self.export_format = export_format
        self.parser = ContentParser()

    def build_single_message_document(
        self, conversation_name: str | None, user_message: GeneratedMessage, ai_message: GeneratedMessage
    ) -> Document:
        """Build document for a single message exchange.

        Args:
            conversation_name: Name of the conversation
            user_message: The user's message
            ai_message: The AI's response

        Returns:
            Complete document with single message exchange
        """
        doc = Document()

        # Add conversation title
        if conversation_name:
            doc.add(Heading(text=conversation_name, level=HeadingLevel.H1))

        # Add user message
        doc.add(Heading(text="User", level=HeadingLevel.H2))
        doc.add(Paragraph(content=user_message.message))

        # Add thoughts if available
        if ai_message.thoughts:
            # Add page break before thoughts section
            self._add_thoughts_to_document(doc, ai_message)
            # Add page break after thoughts section
            doc.add(PageBreak())

        return doc

    def build_conversation_document(
        self,
        conversation: Conversation,
        assistant: Assistant | None,
        message_pairs: list[tuple[GeneratedMessage, GeneratedMessage]],
    ) -> Document:
        """Build document for full conversation.

        Args:
            conversation: The conversation to export
            assistant: The assistant whose messages to export
            message_pairs: List of (user_message, ai_message) tuples

        Returns:
            Complete document with all message exchanges
        """
        doc = Document()

        # Add conversation title
        assistant_name = assistant.name if assistant else "Virtual Assistant"
        title = f"{conversation.conversation_name or 'Conversation'} - {assistant_name}"
        doc.add(Heading(text=title, level=HeadingLevel.H1))

        # Add each message pair
        for user_msg, ai_msg in message_pairs:
            self._add_message_pair_to_document(doc, user_msg, ai_msg)

        return doc

    def _add_message_pair_to_document(
        self, doc: Document, user_message: GeneratedMessage, ai_message: GeneratedMessage
    ) -> None:
        """Add a single message exchange to document.

        Args:
            doc: Document to add to
            message_num: Message number (1-indexed)
            user_message: The user's message
            ai_message: The AI's response
        """
        # Add message separator
        doc.add(HorizontalRule())

        # Add user message
        doc.add(Heading(text="User", level=HeadingLevel.H3))
        doc.add(Paragraph(content=user_message.message))

        # Add thoughts if available
        if ai_message.thoughts:
            # Add page break before thoughts section
            self._add_thoughts_to_document(doc, ai_message)

        # Add AI message
        doc.add(Heading(text="CodeMie Assistant", level=HeadingLevel.H3))
        doc.add(Paragraph(content=ai_message.message))

    def _add_thoughts_to_document(self, doc: Document, ai_message: GeneratedMessage) -> None:
        """Add thoughts section to document.

        Args:
            doc: Document to add thoughts to
            ai_message: The AI message with thoughts
        """
        # Make a list of thoughts to include after filtering
        filtered_thoughts = [t for t in ai_message.thoughts if ExportUtils.should_include_thought(t)]

        for step_counter, thought in enumerate(filtered_thoughts, start=1):
            self._add_single_thought(doc, thought, step_counter)

    def _add_single_thought(self, doc: Document, thought: Thought, step_counter: int) -> None:
        """Add a single thought to the document.

        Args:
            doc: Document to add to
            thought: The thought to add
            step_counter: The step number for the thought
        """
        # Add thought header
        thought_title = thought.author_name or f"Step {step_counter}"
        doc.add(Heading(text=thought_title, level=HeadingLevel.H3))

        # Check if thought has input_text
        has_input = thought.input_text and thought.input_text.strip()

        # Add Input section if input_text exists
        if has_input:
            doc.add(Heading(text="Input", level=HeadingLevel.H4))
            doc.add(Paragraph(content=thought.input_text))

        # Add Output section header if there was input
        if has_input and thought.message:
            doc.add(Heading(text="Output", level=HeadingLevel.H4))

        # Add thought content if it exists
        if thought.message:
            self._add_thought_content(doc, thought)

    def _add_thought_content(self, doc: Document, thought: Thought) -> None:
        """Add thought message content to the document.

        Args:
            doc: Document to add to
            thought: The thought with message content
        """
        # Only replace images if this thought has "thoughts" in author_name
        # This ensures images are only embedded in "thoughts", not in "Python Repl Code Interpreter" etc.
        thought_content = self._process_thought_content(thought)

        # Parse and add thought content directly from the processed text
        content_nodes = self.parser.parse_thought_content_from_text(thought_content)
        for node in content_nodes:
            doc.add(node)

    def _process_thought_content(self, thought: Thought) -> str:
        """Process thought content, replacing images and converting mermaid diagrams if appropriate.

        Args:
            thought: The thought to process

        Returns:
            Processed thought content
        """
        if thought.author_name and 'thoughts' in thought.author_name.lower():
            processed = ExportUtils.replace_sandbox_images(thought.message)
            processed = ExportUtils.replace_mermaid_with_images(processed)
            return processed
        return thought.message


# ============================================================================
# Unified Message Exporter
# ============================================================================


class MessageExporter:
    """Unified message exporter with node-based document structure.

    Supports both single message export and full conversation export.
    """

    def __init__(
        self,
        conversation: Conversation,
        export_format: ExportFormat,
        # For single message export
        history_index: int | None = None,
        message_index: int | None = None,
        # For full conversation export
        assistant: Assistant | None = None,
    ):
        """Initialize the exporter.

        Args:
            conversation: The conversation to export
            export_format: Target format (PDF, DOCX, or PPTX)
            history_index: History index for single message export (optional)
            message_index: Message index for single message export (optional)
            assistant: Assistant for full conversation export (optional)
        """
        self.conversation = conversation
        self.export_format = ExportFormat(export_format)
        self.history_index = history_index
        self.message_index = message_index
        self.assistant = assistant

        # Determine export mode
        self.is_single_message = history_index is not None and message_index is not None
        self.is_full_conversation = assistant is not None or conversation

    def run(self) -> Iterator[bytes]:
        """Run the export service.

        Returns:
            Iterator of bytes containing the exported document
        """
        if self.is_single_message:
            return self.export_single_message()
        elif self.is_full_conversation:
            return self._export_full_conversation()
        else:
            raise ValueError("Invalid export configuration: must specify either message indices or assistant")

    @property
    def filename(self) -> str:
        """Generate filename based on export mode.

        Returns:
            Sanitized filename with appropriate naming convention
        """
        if self.is_single_message:
            title = f"{self.conversation.id}_{self.history_index}_{self.message_index}"
            return f"{title}.{self.export_format.value}"
        elif self.is_full_conversation:
            # Get conversation name
            conv_name = self.conversation.conversation_name or self.conversation.id

            # Sanitize conversation name (max 30 chars)
            safe_conv = re.sub(r'[^\w\s-]', '', conv_name).strip()[:30]
            safe_conv = re.sub(r'[-\s]+', '-', safe_conv)

            # Sanitize assistant name (max 20 chars)
            safe_assistant = re.sub(r'[^\w\s-]', '', self.assistant.name).strip()[:20]
            safe_assistant = re.sub(r'[-\s]+', '-', safe_assistant)

            return f"{safe_conv}_{safe_assistant}.{self.export_format.value}"
        else:
            return f"export.{self.export_format.value}"

    @property
    def content_type(self) -> str:
        """Get the content type for the export format.

        Returns:
            MIME type for the export format
        """
        return self.export_format.content_type

    def _build_extra_args(self, export_format: ExportFormat) -> list[str]:
        """Build Pandoc extra arguments with proper metadata and quiet mode.

        Note: This is used for single message export. Full conversation export
        uses PDFProcessor.get_pandoc_args() instead.
        """
        args = ['--standalone', '--quiet']

        # Add format-specific arguments
        if export_format == ExportFormat.PDF:
            args.extend(
                [
                    '--pdf-engine=pdflatex',
                    '-V',
                    'geometry:margin=1in',
                    '-V',
                    'fontsize=11pt',
                    '-V',
                    (
                        'header-includes='
                        '\\usepackage{lmodern}'
                        '\\usepackage[T1]{fontenc}'
                        '\\usepackage{hyperref}'
                        '\\hypersetup{hidelinks,pdfborder={0 0 0},colorlinks=false}'
                        '\\usepackage{fvextra}'
                        '\\DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\\\\{\\}}'
                    ),
                    '--listings',
                ]
            )

        return args

    def export_single_message(self):
        """Export a single message exchange.

        Returns:
            Iterator of bytes containing the exported document
        """
        _, ai_message = self.conversation.find_messages(self.history_index, self.message_index)
        processed_markdown = self._process_markdown(ai_message.message)
        processed_markdown = ExportUtils.replace_sandbox_images(processed_markdown)

        logger.debug(f"Converting message to {self.export_format.value}. Message length: {len(ai_message.message)}")
        # Build extra arguments with proper metadata and quiet mode
        extra_args = self._build_extra_args(self.export_format)

        # Using default delete=True to ensure automatic cleanup
        with tempfile.NamedTemporaryFile(suffix=f'.{self.export_format.value}') as temp_file:
            temp_dir = tempfile.gettempdir()
            try:
                pypandoc.convert_text(
                    source=processed_markdown,
                    to=str(self.export_format.value),
                    format='markdown-yaml_metadata_block',
                    outputfile=temp_file.name,
                    extra_args=extra_args,
                    cworkdir=temp_dir,
                )

                temp_file.flush()
                temp_file.seek(0)

                chunk_size = 4096  # 4KB chunks
                while True:
                    chunk = temp_file.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            except RuntimeError as e:
                logger.error(
                    f"Pandoc PDF conversion failed for message {self.history_index}_{self.message_index}. "
                    f"This may be due to special characters or formatting issues. Error: {e}"
                )
                raise

    def _export_full_conversation(self) -> Iterator[bytes]:
        """Export full conversation for specific assistant.

        Returns:
            Iterator of bytes containing the exported document
        """
        # Filter messages by assistant
        message_pairs = self._filter_assistant_messages()

        if not message_pairs:
            logger.debug(f"No messages found in conversation {self.conversation.id}")

        # Build document
        builder = DocumentBuilder(
            export_format=self.export_format,
        )

        document = builder.build_conversation_document(
            conversation=self.conversation, assistant=self.assistant, message_pairs=message_pairs
        )

        # Export to format
        return self._export_document(document)

    def _filter_assistant_messages(self) -> list[tuple[GeneratedMessage, GeneratedMessage]]:
        """Filter messages where AI response is from this assistant.

        Groups messages by history_index and pairs user messages with AI responses
        from this specific assistant. For each history_index, takes the LATEST
        message (to handle regenerated responses).

        Returns:
            List of (user_message, ai_message) tuples in chronological order
        """
        message_pairs = []
        messages_by_history_index = {}

        # Group messages by history_index
        for message in self.conversation.history:
            if message.history_index is not None:
                if message.history_index not in messages_by_history_index:
                    messages_by_history_index[message.history_index] = []
                messages_by_history_index[message.history_index].append(message)

        # Filter and pair messages - taking the LATEST message for each role per history_index
        for history_index in sorted(messages_by_history_index.keys()):
            messages = messages_by_history_index[history_index]

            # Find LATEST user message in this turn (last occurrence)
            user_messages = [m for m in messages if m.role == ChatRole.USER]
            user_msg = user_messages[-1] if user_messages else None

            # Find LATEST AI message from this specific assistant (last occurrence)
            ai_messages = [m for m in messages if m.role == ChatRole.ASSISTANT]
            ai_msg = ai_messages[-1] if ai_messages else None

            # Only include if both user and AI messages exist
            if user_msg and ai_msg:
                message_pairs.append((user_msg, ai_msg))

        return message_pairs

    def _export_document(self, document: Document) -> Iterator[bytes]:
        """Export document to specified format using pypandoc.

        Args:
            document: The document to export

        Returns:
            Iterator of bytes containing the exported document
        """
        # Get format processor and process document BEFORE serialization
        # This allows format-specific transformations (like removing PageBreaks for PPTX)
        processor = ProcessorFactory.get_processor(self.export_format)
        processed_document = processor.process_document(document)

        # Create serialization context with output format
        from codemie.service.conversation.pandoc_nodes import SerializationContext

        context = SerializationContext(output_format=self.export_format.value)

        # Serialize document to markdown with format context
        markdown = processed_document.serialize(context)

        # Process markdown
        processed_markdown = self._process_markdown(markdown)

        logger.debug(f"Converting to {self.export_format.value}. Markdown length: {len(markdown)} chars")

        # Build pandoc arguments
        extra_args = ['--standalone', '--quiet']
        extra_args.extend(processor.get_pandoc_args())

        # Export using pypandoc
        with tempfile.NamedTemporaryFile(suffix=f'.{self.export_format.value}') as temp_file:
            temp_dir = tempfile.gettempdir()
            try:
                pypandoc.convert_text(
                    source=processed_markdown,
                    to=str(self.export_format.value),
                    format='markdown-yaml_metadata_block',
                    outputfile=temp_file.name,
                    extra_args=extra_args,
                    cworkdir=temp_dir,
                )

                # Read and yield content
                temp_file.flush()
                temp_file.seek(0)

                chunk_size = 4096  # 4KB chunks
                while True:
                    chunk = temp_file.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            except RuntimeError as e:
                logger.error(
                    f"Pandoc PDF conversion failed for conversation {self.conversation.id}. "
                    f"This may be due to special characters or formatting issues. Error: {e}"
                )
                raise

    def _process_markdown(self, markdown: str) -> str:
        """Process markdown: cleanup and mermaid diagram replacement.

        Args:
            markdown: Raw markdown content

        Returns:
            Processed markdown ready for conversion
        """
        # Cleanup
        processed = markdown.replace("~~~markdown", "").replace("~~~", "")

        # Replace mermaid code blocks with embedded images for export
        processed = ExportUtils.replace_mermaid_with_images(processed)

        # Note: Image replacement is now done per-thought in _add_thoughts_to_document
        # to ensure only thoughts with "thoughts" in author_name get embedded images

        # For PDF export with pdflatex, clean and escape content
        if self.export_format == ExportFormat.PDF:
            # Step 1: Remove ANSI and control characters (do this ONCE, first)
            processed = self._remove_ansi_and_control_chars(processed)
            # Step 2: Convert SVG to PNG (preserve visual data)
            processed = self._convert_svg_to_png(processed)
            # Step 3: Escape LaTeX special characters
            processed = self._escape_latex_special_chars(processed)
            # Step 4: Replace Unicode/emoji with ASCII
            processed = self._replace_unicode_chars(processed)

        return processed

    def _remove_ansi_and_control_chars(self, markdown: str) -> str:
        """Remove ANSI escape sequences and control characters.

        This must be done FIRST, before any other processing.

        Args:
            markdown: Markdown content

        Returns:
            Markdown with ANSI and control characters removed
        """
        import re

        # Remove ANSI escape sequences (terminal color codes)
        markdown = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', markdown)
        markdown = re.sub(r'\x1b[()][\w]', '', markdown)

        # Remove control characters except newline (\n), tab (\t), carriage return (\r)
        markdown = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', markdown)

        return markdown

    def _escape_latex_special_chars(self, markdown: str) -> str:
        """Escape special LaTeX characters outside of code blocks.

        IMPORTANT: ANSI removal must be done BEFORE calling this method.

        Args:
            markdown: Markdown content (with ANSI already removed)

        Returns:
            Markdown with escaped LaTeX special characters
        """
        import re

        # Split by code blocks and inline code
        code_block_pattern = r'(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*?`)'
        parts = re.split(code_block_pattern, markdown)

        escaped_parts = []
        for i, part in enumerate(parts):
            # Even indices are outside code blocks, odd indices are code blocks/inline code
            if i % 2 == 0:  # Outside code blocks - need to escape
                # Escape LaTeX special characters in order
                # Order matters to avoid double-escaping

                # 1. Backslash - escape literal \n, \r, \t that appear as text
                # Common in code examples like: error_msg.rstrip("\r\n")
                part = re.sub(r'\\([nrt])(?![a-zA-Z])', r'\\textbackslash{}\1', part)

                # 2. Other backslashes (preserve markdown escapes like \*, \_, etc.)
                part = re.sub(r'\\(?![\\`*_{}[\]()#+\-.!<>|])', r'\\textbackslash{}', part)

                # 3. Dollar signs (math mode delimiter)
                part = part.replace('$', r'\$')

                # 4. Percent (comment character)
                part = part.replace('%', r'\%')

                # 5. Ampersand (table column separator)
                part = part.replace('&', r'\&')

                # 6. Hash (except at line start for markdown headers)
                part = re.sub(r'(?<!^)(?<!\n)#', r'\\#', part, flags=re.MULTILINE)

                # 7. Underscores (preserve markdown emphasis)
                part = re.sub(r'(?<!\w)_(?!\w)', r'\\_', part)

                # 8. Carets (superscript in math mode)
                part = part.replace('^', r'\\textasciicircum{}')

                # 9. Tildes (non-breaking space)
                part = part.replace('~', r'\\textasciitilde{}')

                # 10. Curly braces (grouping)
                part = part.replace('{', r'\{')
                part = part.replace('}', r'\}')

            escaped_parts.append(part)

        return ''.join(escaped_parts)

    def _convert_svg_to_png(self, markdown: str) -> str:
        """Convert SVG images to PNG format for LaTeX compatibility.

        If cairosvg is not available or conversion fails, replaces with placeholder.
        This preserves visual data when possible while ensuring PDF conversion works.

        Args:
            markdown: Markdown content

        Returns:
            Markdown with SVG images converted to PNG or replaced with placeholders
        """
        import re
        from pathlib import Path

        # Check if cairosvg is available
        try:
            import cairosvg
        except ImportError:
            logger.warning(
                "cairosvg not available for SVG conversion. "
                "SVG images will be replaced with placeholders. "
                "Install cairosvg with: poetry add cairosvg"
            )
            # Replace all SVGs with placeholders - using ReDoS-safe regex patterns
            # Pattern: [^][] excludes both ] and [; outside char class, ] needs no escape
            markdown = re.sub(r'!\[([^][]*)](\([^)]+\.svg)\)', r'[Image: \1]', markdown)
            # For HTML img tags - match using bounded repetition with explicit structure
            # Avoid overlapping quantifiers by using specific patterns
            # Pattern matches: <img + attributes + src with .svg + optional trailing attrs + >
            markdown = re.sub(
                r'<img(?:\s+[\w\-:]+(?:=(?:"[^"]{0,200}"|\'[^\']{0,200}\'))?){0,10}\s+'
                r'src=["\']\s*[^"\']{1,500}\.svg\s*["\']'
                r'(?:\s+[\w\-:]+(?:=(?:"[^"]{0,200}"|\'[^\']{0,200}\'))?){0,10}\s*>',
                '[Image]',
                markdown,
            )
            return markdown

        # Pattern: ![alt text](path/to/image.svg)
        # [^][] excludes both ] and [; outside char class, ] needs no escape
        svg_pattern = r'!\[([^][*)]]\(([^)]+\.svg)\)'

        def replace_svg(match):
            alt_text = match.group(1)
            svg_path = match.group(2)

            # Skip URLs
            if svg_path.startswith(('data:', 'https://')):
                return f'[Image: {alt_text}]'

            try:
                svg_file = Path(svg_path)
                if not svg_file.exists():
                    # Try relative to current working directory
                    svg_file = Path.cwd() / svg_path
                    if not svg_file.exists():
                        logger.debug(f"SVG file not found: {svg_path}")
                        return f'[Image: {alt_text}]'

                # Convert to PNG in temp directory
                png_path = Path(tempfile.gettempdir()) / f"{svg_file.stem}.png"
                cairosvg.svg2png(url=str(svg_file), write_to=str(png_path))

                logger.debug(f"Converted SVG to PNG: {svg_file.name}")
                return f'![{alt_text}]({png_path})'

            except Exception as e:
                logger.debug(f"SVG conversion failed for {svg_path}: {e}")
                return f'[Image: {alt_text}]'

        # Replace markdown SVG images
        markdown = re.sub(svg_pattern, replace_svg, markdown)

        # Replace HTML img tags with SVG - match using bounded repetition with explicit structure
        # Avoid overlapping quantifiers by using specific patterns
        # Pattern matches: <img + attributes + src with .svg + optional trailing attrs + >
        markdown = re.sub(
            r'<img(?:\s+[\w\-:]+(?:=(?:"[^"]{0,200}"|\'[^\']{0,200}\'))?){0,10}\s+'
            r'src=["\']\s*[^"\']{1,500}\.svg\s*["\']'
            r'(?:\s+[\w\-:]+(?:=(?:"[^"]{0,200}"|\'[^\']{0,200}\'))?){0,10}\s*>',
            '[Image]',
            markdown,
        )

        return markdown

    def _replace_unicode_chars(self, markdown: str) -> str:
        """Replace Unicode characters (emojis) with text equivalents for pdflatex.

        IMPORTANT: ANSI removal must be done BEFORE calling this method.

        Args:
            markdown: Markdown content (with ANSI already removed)

        Returns:
            Markdown with Unicode characters replaced with ASCII equivalents
        """
        import re

        # Common emoji replacements
        replacements = {
            '✅': '[OK]',
            '❌': '[X]',
            '⚠️': '[WARNING]',
            '⚠': '[WARNING]',
            '✓': '[check]',
            '✗': '[x]',
            '→': '->',
            '←': '<-',
            '↑': '^',
            '↓': 'v',
            '🔴': '[RED]',
            '🟡': '[YELLOW]',
            '🟢': '[GREEN]',
            '⭐': '[*]',
            '💡': '[IDEA]',
            '📝': '[NOTE]',
            '🚀': '[ROCKET]',
            '⏰': '[TIME]',
            '📊': '[CHART]',
            '📈': '[UP]',
            '📉': '[DOWN]',
            '🎯': '[TARGET]',
            '🎉': '[CELEBRATION]',
            '👍': '[THUMBS-UP]',
            '👎': '[THUMBS-DOWN]',
            '🔥': '[FIRE]',
            '❤️': '[HEART]',
            '💯': '[100]',
            '🚨': '[ALERT]',
        }

        # Replace known emojis with ASCII equivalents
        for emoji, replacement in replacements.items():
            markdown = markdown.replace(emoji, replacement)

        # Replace remaining emojis with generic placeholder
        # Emoji ranges: U+1F300-U+1F9FF, U+2600-U+26FF, U+2700-U+27BF
        markdown = re.sub(r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001F000-\U0001FAFF]', '[emoji]', markdown)

        # Keep only ASCII printable (32-126) + whitespace (tab, newline, carriage return)
        # This removes any remaining Unicode that LaTeX can't handle
        markdown = re.sub(r'[^\x09\x0a\x0d\x20-\x7E]+', '', markdown)

        return markdown
