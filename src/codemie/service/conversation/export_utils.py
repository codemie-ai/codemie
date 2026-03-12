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

"""Shared utilities for conversation export services."""

import base64
import re

from codemie.chains.base import Thought
from codemie.core.constants import MermaidContentType
from codemie.service.file_service.file_service import FileService
from codemie.service.file_service.mermaid_service import MermaidService

# We use the string constant but not the class itself
from ...configs import logger


class ExportUtils:
    """Shared utilities for export services."""

    _SANDBOX_FILE_PREFIX = "sandbox:/v1/files/"
    _FILES_PATH_SEPARATOR = "/files/"
    _SANDBOX_RE = re.compile(r'!\[[^\]]*\]\((sandbox:/v1/files/[^)]+)\)')

    # Fixed regex to avoid ReDoS: unroll the loop to prevent backtracking
    # Matches: ![alt](anything/files/filename) where the URL contains /files/
    # Pattern breakdown:
    # - !\[[^\]]*\] matches ![alt text]
    # - \( matches opening parenthesis
    # - ([^)]*) captures the entire URL (greedy match, but [^)] ensures linear time)
    # We validate the /files/ part in Python code instead of regex to avoid backtracking
    _FILES_API_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')

    # Mermaid code block regex (ReDoS-safe)
    # Matches: ```mermaid\n...\n``` or ~~~mermaid\n...\n~~~
    # Uses negative lookahead to prevent backtracking and guarantee linear time complexity
    # Pattern breakdown:
    # - (?:(?!```).)*? matches any character that is NOT followed by ``` (prevents backtracking)
    # - (?:(?!~~~).)*? matches any character that is NOT followed by ~~~ (prevents backtracking)
    _MERMAID_CODE_BLOCK_RE = re.compile(
        r'```mermaid\s*\n((?:(?!```).)*?)\n```|~~~mermaid\s*\n((?:(?!~~~).)*?)\n~~~', re.DOTALL
    )

    @staticmethod
    def replace_sandbox_images(markdown_content: str) -> str:
        """Replace sandbox image references with base64 data URLs.

        This method finds all sandbox:// image references in the markdown
        and replaces them with base64-encoded data URLs that can be embedded
        in exported documents.

        Args:
            markdown_content: Markdown content with sandbox image references

        Returns:
            Markdown with sandbox images replaced by base64 data URLs
        """
        patterns = [
            (ExportUtils._SANDBOX_RE, "HTML"),
            (ExportUtils._FILES_API_RE, "HTML"),
        ]

        for pattern, format_type in patterns:
            matches = pattern.findall(markdown_content)
            for match in matches:
                markdown_content = ExportUtils._process_image_match(markdown_content, match, pattern, format_type)

        return markdown_content

    @staticmethod
    def replace_mermaid_with_images(markdown_content: str) -> str:
        """Replace mermaid code blocks with embedded PNG images.

        This method finds all ```mermaid code blocks in the markdown,
        generates PNG diagrams using MermaidService, and replaces them
        with embedded base64 PNG images.

        Args:
            markdown_content: Markdown content with mermaid code blocks

        Returns:
            Markdown with mermaid code blocks replaced by embedded images
        """

        def replace_mermaid_block(match: re.Match) -> str:
            """Replace a single mermaid code block with an embedded image."""
            # Extract mermaid code from either capture group (backticks or tildes)
            mermaid_code = match.group(1) if match.group(1) else match.group(2)

            if not mermaid_code or not mermaid_code.strip():
                logger.warning("Empty mermaid code block found, skipping replacement")
                return match.group(0)

            try:
                # Generate PNG diagram
                diagram_bytes = MermaidService.draw_mermaid(
                    mermaid_code=mermaid_code.strip(), type=MermaidContentType.PNG
                )

                # Convert to base64
                base64_image = base64.b64encode(diagram_bytes).decode('utf-8')
                data_url = f"data:image/png;base64,{base64_image}"

                # Replace with markdown image
                return f"![Mermaid Diagram]({data_url})"

            except Exception as e:
                logger.warning(f"Failed to generate mermaid diagram: {e}. Keeping original code block.")
                # Keep original code block if generation fails
                return match.group(0)

        # Replace all mermaid code blocks
        return ExportUtils._MERMAID_CODE_BLOCK_RE.sub(replace_mermaid_block, markdown_content)

    @staticmethod
    def _process_image_match(markdown_content: str, match: str, pattern: re.Pattern, format_type: str) -> str:
        """Process a single image match and replace it with base64 data URL.

        Args:
            markdown_content: The markdown content to update
            match: The matched image URL
            pattern: The regex pattern that matched
            format_type: Type of format being processed

        Returns:
            Updated markdown content with image replaced
        """
        try:
            # For _FILES_API_RE, filter for URLs containing /files/
            if pattern == ExportUtils._FILES_API_RE and ExportUtils._FILES_PATH_SEPARATOR not in match:
                return markdown_content

            # Extract file name from the URL pattern
            file_name = ExportUtils._extract_file_name(match)
            if not file_name:
                logger.warning(f"Unable to extract file name from {match}")
                return markdown_content

            # Get the file object and convert to base64 data URL
            data_url = ExportUtils._get_image_data_url(file_name)
            return markdown_content.replace(match, data_url)

        except Exception as e:
            logger.warning(f"Failed to process sandbox {format_type} image {match}: {e}")
            return markdown_content

    @staticmethod
    def _extract_file_name(url: str) -> str | None:
        """Extract file name from URL.

        Args:
            url: The URL to extract file name from

        Returns:
            File name or None if extraction fails
        """
        if url.startswith(ExportUtils._SANDBOX_FILE_PREFIX):
            return url.split(ExportUtils._SANDBOX_FILE_PREFIX)[1]
        if ExportUtils._FILES_PATH_SEPARATOR in url:
            return url.split(ExportUtils._FILES_PATH_SEPARATOR)[1]

        return None

    @staticmethod
    def _get_image_data_url(file_name: str) -> str:
        """Get base64 data URL for image file.

        Args:
            file_name: Name of the file to convert

        Returns:
            Base64 data URL
        """
        file_object = FileService.get_file_object(file_name)
        base64_content = file_object.to_image_base64()

        if base64_content.startswith('data:'):
            return base64_content
        return f"data:{file_object.mime_type};base64,{base64_content}"

    @staticmethod
    def should_include_thought(thought: Thought, export_full_thought: bool = True) -> bool:
        """Determine if a thought should be included in the export.

        When export_full_thought is True, includes all thoughts.
        When export_full_thought is False, only includes thoughts where author_name contains "thoughts"
        (case-insensitive), such as "CodeMie Thoughts", "CODEMIE THOUGHTS", etc.

        Always filters out:
        - Empty thoughts
        - Thoughts with no message or author_type

        Args:
            thought: The thought to evaluate
            export_full_thought: If True, include all thoughts; if False, only include thoughts with
                "thoughts" in author_name

        Returns:
            True if thought should be included, False otherwise
        """
        # Skip if no message
        if not thought.message or not thought.message.strip() or not thought.author_type:
            return False

        # If export_full_thought is True, include all thoughts
        if export_full_thought:
            return True

        # If export_full_thought is False, only include thoughts with author_name matching "thoughts" (case-insensitive)
        return bool(thought.author_name and 'thoughts' in thought.author_name.lower())
