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

"""Tests for pandoc_nodes module."""

from codemie.service.conversation.pandoc_nodes import (
    CodeBlock,
    Document,
    Heading,
    HeadingLevel,
    HorizontalRule,
    PageBreak,
    Paragraph,
    RawContent,
    SerializationContext,
)


# ============================================================================
# Tests for SerializationContext
# ============================================================================


class TestSerializationContext:
    """Test suite for SerializationContext class."""

    def test_default_values(self):
        """Test default context values."""
        context = SerializationContext()

        assert context.indent_level == 0
        assert context.indent_str == "    "
        assert context.in_list is False
        assert context.blank_line_before is True
        assert context.output_format is None

    def test_custom_values(self):
        """Test context with custom values."""
        context = SerializationContext(
            indent_level=2,
            indent_str="  ",
            in_list=True,
            blank_line_before=False,
            output_format="pdf",
        )

        assert context.indent_level == 2
        assert context.indent_str == "  "
        assert context.in_list is True
        assert context.blank_line_before is False
        assert context.output_format == "pdf"

    def test_indent_method(self):
        """Test indent method returns correct string."""
        context = SerializationContext(indent_level=0)
        assert context.indent() == ""

        context = SerializationContext(indent_level=1)
        assert context.indent() == "    "

        context = SerializationContext(indent_level=3)
        assert context.indent() == "            "

    def test_indent_with_custom_indent_str(self):
        """Test indent method with custom indent string."""
        context = SerializationContext(indent_level=2, indent_str="  ")
        assert context.indent() == "    "

    def test_with_indent_method(self):
        """Test with_indent method creates new context with modified indent level."""
        context = SerializationContext(indent_level=0)
        new_context = context.with_indent(1)

        assert new_context.indent_level == 1
        assert context.indent_level == 0  # Original unchanged

    def test_with_indent_preserves_other_properties(self):
        """Test with_indent preserves other context properties."""
        context = SerializationContext(
            indent_level=0,
            indent_str="  ",
            in_list=True,
            blank_line_before=False,
            output_format="docx",
        )
        new_context = context.with_indent(2)

        assert new_context.indent_level == 2
        assert new_context.indent_str == "  "
        assert new_context.in_list is True
        assert new_context.blank_line_before is False
        assert new_context.output_format == "docx"


# ============================================================================
# Tests for Document
# ============================================================================


class TestDocument:
    """Test suite for Document class."""

    def test_empty_document(self):
        """Test empty document serialization."""
        doc = Document()
        result = doc.serialize()

        assert result == ""

    def test_document_with_single_node(self):
        """Test document with single node."""
        doc = Document()
        doc.add(Paragraph(content="Hello"))

        result = doc.serialize()
        assert result == "Hello"

    def test_document_with_multiple_nodes(self):
        """Test document with multiple nodes adds blank lines."""
        doc = Document()
        doc.add(Paragraph(content="First"))
        doc.add(Paragraph(content="Second"))

        result = doc.serialize()
        assert result == "First\n\nSecond"

    def test_document_add_returns_self(self):
        """Test add method returns self for chaining."""
        doc = Document()
        result = doc.add(Paragraph(content="Test"))

        assert result is doc

    def test_document_with_context(self):
        """Test document serialization with context."""
        doc = Document()
        doc.add(Paragraph(content="Test"))

        context = SerializationContext(output_format="pdf")
        result = doc.serialize(context)

        assert result == "Test"

    def test_document_no_extra_blank_lines(self):
        """Test document doesn't add extra blank lines when already present."""
        doc = Document()
        doc.add(RawContent("First\n\n"))
        doc.add(Paragraph(content="Second"))

        result = doc.serialize()
        assert result == "First\n\n\nSecond"

    def test_document_str_method(self):
        """Test __str__ method uses default context."""
        doc = Document()
        doc.add(Paragraph(content="Test"))

        result = str(doc)
        assert result == "Test"


# ============================================================================
# Tests for Heading
# ============================================================================


class TestHeading:
    """Test suite for Heading class."""

    def test_heading_level_1(self):
        """Test H1 heading serialization."""
        heading = Heading(text="Title", level=HeadingLevel.H1)
        result = heading.serialize(SerializationContext())

        assert result == "# Title"

    def test_heading_level_2(self):
        """Test H2 heading serialization."""
        heading = Heading(text="Subtitle", level=HeadingLevel.H2)
        result = heading.serialize(SerializationContext())

        assert result == "## Subtitle"

    def test_heading_level_6(self):
        """Test H6 heading serialization."""
        heading = Heading(text="Smallest", level=HeadingLevel.H6)
        result = heading.serialize(SerializationContext())

        assert result == "###### Smallest"

    def test_heading_with_special_characters(self):
        """Test heading with special characters."""
        heading = Heading(text="Title: With Special! @#$", level=HeadingLevel.H1)
        result = heading.serialize(SerializationContext())

        assert result == "# Title: With Special! @#$"

    def test_heading_enum_values(self):
        """Test HeadingLevel enum values."""
        assert HeadingLevel.H1.value == 1
        assert HeadingLevel.H2.value == 2
        assert HeadingLevel.H3.value == 3
        assert HeadingLevel.H4.value == 4
        assert HeadingLevel.H5.value == 5
        assert HeadingLevel.H6.value == 6


# ============================================================================
# Tests for Paragraph
# ============================================================================


class TestParagraph:
    """Test suite for Paragraph class."""

    def test_paragraph_with_string_content(self):
        """Test paragraph with string content."""
        para = Paragraph(content="This is a paragraph.")
        result = para.serialize(SerializationContext())

        assert result == "This is a paragraph."

    def test_paragraph_empty_string(self):
        """Test paragraph with empty string."""
        para = Paragraph(content="")
        result = para.serialize(SerializationContext())

        assert result == ""


# ============================================================================
# Tests for CodeBlock
# ============================================================================


class TestCodeBlock:
    """Test suite for CodeBlock class."""

    def test_code_block_with_language(self):
        """Test code block with language specified."""
        code = CodeBlock(code="print('hello')", language="python")
        result = code.serialize(SerializationContext())

        assert result == "```python\nprint('hello')\n```"

    def test_code_block_without_language(self):
        """Test code block without language."""
        code = CodeBlock(code="generic code")
        result = code.serialize(SerializationContext())

        assert result == "```\ngeneric code\n```"

    def test_code_block_multiline(self):
        """Test code block with multiple lines."""
        code = CodeBlock(code="line1\nline2\nline3", language="python")
        result = code.serialize(SerializationContext())

        assert result == "```python\nline1\nline2\nline3\n```"

    def test_code_block_strips_trailing_newlines(self):
        """Test code block strips trailing newlines from code."""
        code = CodeBlock(code="code\n\n\n", language="python")
        result = code.serialize(SerializationContext())

        assert result == "```python\ncode\n```"

    def test_code_block_custom_fence_char(self):
        """Test code block with custom fence character."""
        code = CodeBlock(code="code", language="python", fence_char="~~~")
        result = code.serialize(SerializationContext())

        assert result == "~~~python\ncode\n~~~"


# ============================================================================
# Tests for HorizontalRule
# ============================================================================


class TestHorizontalRule:
    """Test suite for HorizontalRule class."""

    def test_horizontal_rule_default(self):
        """Test horizontal rule with default settings."""
        rule = HorizontalRule()
        result = rule.serialize(SerializationContext())

        assert result == "---"

    def test_horizontal_rule_custom_char(self):
        """Test horizontal rule with custom character."""
        rule = HorizontalRule(char="*")
        result = rule.serialize(SerializationContext())

        assert result == "***"

    def test_horizontal_rule_custom_length(self):
        """Test horizontal rule with custom length."""
        rule = HorizontalRule(length=5)
        result = rule.serialize(SerializationContext())

        assert result == "-----"

    def test_horizontal_rule_underscore(self):
        """Test horizontal rule with underscore."""
        rule = HorizontalRule(char="_", length=3)
        result = rule.serialize(SerializationContext())

        assert result == "___"


# ============================================================================
# Tests for PageBreak
# ============================================================================


class TestPageBreak:
    """Test suite for PageBreak class."""

    def test_page_break_default_formats(self):
        """Test page break with default formats."""
        page_break = PageBreak()
        result = page_break.serialize(SerializationContext())

        assert "```{=latex}" in result
        assert r"\newpage" in result
        assert "```{=openxml}" in result
        assert "```{=html}" in result

    def test_page_break_with_context_output_format_pdf(self):
        """Test page break with PDF output format."""
        page_break = PageBreak()
        context = SerializationContext(output_format="pdf")
        result = page_break.serialize(context)

        # PDF maps to latex (pdflatex)
        assert "```{=latex}" in result
        assert r'\newpage' in result  # LaTeX page break command
        # Should not include other formats
        assert result.count("```") == 2

    def test_page_break_with_context_output_format_docx(self):
        """Test page break with DOCX output format."""
        page_break = PageBreak()
        context = SerializationContext(output_format="docx")
        result = page_break.serialize(context)

        # DOCX maps to openxml
        assert "```{=openxml}" in result
        assert 'w:br w:type="page"' in result
        # Should not include other formats
        assert result.count("```") == 2

    def test_page_break_custom_formats(self):
        """Test page break with custom formats."""
        page_break = PageBreak(formats=["latex", "html"])
        result = page_break.serialize(SerializationContext())

        assert "```{=latex}" in result
        assert "```{=html}" in result
        # Should not include openxml
        assert "```{=openxml}" not in result

    def test_page_break_map_output_format_pptx(self):
        """Test page break format mapping for PPTX."""
        page_break = PageBreak()
        result = page_break._map_output_format("pptx")

        assert result == "openxml"

    def test_page_break_map_output_format_unknown(self):
        """Test page break format mapping for unknown format."""
        page_break = PageBreak()
        result = page_break._map_output_format("unknown")

        assert result == "html"

    def test_page_break_multiple_formats_separated(self):
        """Test page breaks for multiple formats are separated by blank lines."""
        page_break = PageBreak(formats=["latex", "html"])
        result = page_break.serialize(SerializationContext())

        # Should have blank line separator
        assert "\n\n" in result


# ============================================================================
# Tests for RawContent
# ============================================================================


class TestRawContent:
    """Test suite for RawContent class."""

    def test_raw_content_simple(self):
        """Test raw content serialization."""
        raw = RawContent(content="Raw markdown **content**")
        result = raw.serialize(SerializationContext())

        assert result == "Raw markdown **content**"

    def test_raw_content_with_special_syntax(self):
        """Test raw content with special markdown syntax."""
        raw = RawContent(content="# Heading\n\n- List item\n- Another item")
        result = raw.serialize(SerializationContext())

        assert result == "# Heading\n\n- List item\n- Another item"


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for complex document structures."""

    def test_complex_document_structure(self):
        """Test complex document with multiple element types."""
        doc = Document()
        doc.add(Heading(text="Main Title", level=HeadingLevel.H1))
        doc.add(Paragraph(content="Introduction paragraph."))

        doc.add(Heading(text="Section 1", level=HeadingLevel.H2))
        doc.add(Paragraph(content="Section content."))

        result = doc.serialize()

        assert "# Main Title" in result
        assert "## Section 1" in result

    def test_document_with_page_breaks(self):
        """Test document with page breaks between sections."""
        doc = Document()
        doc.add(Heading(text="Page 1", level=HeadingLevel.H1))
        doc.add(Paragraph(content="Content on page 1"))
        doc.add(PageBreak())
        doc.add(Heading(text="Page 2", level=HeadingLevel.H1))
        doc.add(Paragraph(content="Content on page 2"))

        result = doc.serialize()

        assert "# Page 1" in result
        assert "# Page 2" in result
        assert "```{=latex}" in result or "```{=openxml}" in result or "```{=html}" in result
