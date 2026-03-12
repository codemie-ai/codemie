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

import re
from typing import Any, Dict, List, Tuple

from googleapiclient.discovery import build


class AssistantKBGoogleDocToJsonParser:
    """
    This is temporary pipeline we use to parse Google Doc with Assistant KB to JSON.
    Final solution idea is that we will have a complete knowledge management system with a database
    and a web interface.

    For now we agreed with client to use Google Doc as a source of truth for Assistant KB.
    """

    reference_regex: str = r"(\d+(\.\d+)*)\."
    chapter_title_regex: str = r"(\d+\.\d+.\d+)\."
    prompt_instruction_splitter: str = "Prompt Instruction:"

    def __init__(self, document_id: str) -> None:
        self.document_id: str = document_id

    def is_title(self, text: str, style: str) -> bool:
        return bool("heading" in style and re.match(self.reference_regex, text))

    def is_chapter_title(self, text: str, style: str) -> bool:
        """
        Returns true, if element is a chapter title (3rd level),
        e.g 1.1.1. Some title
        """
        if "heading" not in style:
            return False

        return bool(re.match(self.chapter_title_regex, text))

    def is_header(self, text: str, style: str) -> bool:
        return bool("heading" in style) and (
            bool(re.match(r"^\d+\.\s", text)) or bool(re.match(r"^\d+\.\d+\.\s", text))
        )

    def get_document(self, service, document_id: str) -> Dict[str, Any]:
        return service.documents().get(documentId=document_id).execute()

    def get_elements(self, document: Dict[str, Any]) -> List[Dict[str, Any]]:
        return document.get("body", {}).get("content", [])

    def get_element_text(self, element: Dict[str, Any]) -> str:
        text = ""
        for el in element.get("paragraph", {}).get("elements", []):
            text += el.get("textRun", {}).get("content", "")
        text = text.replace("\v", "")
        return text

    def get_element_style(self, element: Dict[str, Any]) -> str:
        return element.get("paragraph", {}).get("paragraphStyle", {}).get("namedStyleType", "").lower()

    def get_articles(self, elements: List[Dict[str, Any]]) -> List[Dict[Any, Any]]:
        titles = []  # Changed from previous_title to titles
        content = reference = ""
        articles = []

        for element in elements:
            if "paragraph" in element:
                text = self.get_element_text(element)
                style = self.get_element_style(element)

                # Check for new title and update titles list
                if self.is_title(text, style):
                    if self.is_header(text, style):
                        continue

                    # Check if content exists and a new title arrived
                    if content.strip("\n") and titles:
                        # Save articles for each title
                        for previous_title in titles:
                            (
                                content_text,
                                instructions,
                            ) = self.split_content_instructions(content)
                            content_text = content_text.strip("\n").strip()
                            match = re.search(self.reference_regex, previous_title)
                            if match:
                                reference = match.group()
                                if len(reference) == 4:
                                    reference = reference.rstrip(".") + ".0"
                            if content_text:
                                articles.append(
                                    {
                                        "title": re.sub(
                                            self.reference_regex,
                                            "",
                                            previous_title,
                                        ).strip(),
                                        "content": content_text,
                                        "instructions": instructions,
                                        "reference": reference,
                                    }
                                )
                        # Clear titles and content for the next block
                        titles.clear()
                        content = reference = ""
                    titles.append(text)

                # Check if content starts (empty line or not a title)
                elif not self.is_title(text, style):
                    # Accumulate content
                    content += text.strip("\n") + "\n"

        # Process remaining content and title after the loop
        if content.strip("\n") and titles:
            for previous_title in titles:
                (
                    content_text,
                    instructions,
                ) = self.split_content_instructions(content)
                content_text = content_text.strip("\n").strip()
                match = re.search(self.reference_regex, previous_title)
                if match:
                    reference = match.group()
                    if len(reference) == 4:
                        reference = reference.rstrip(".") + ".0"
                if content_text:
                    articles.append(
                        {
                            "title": re.sub(
                                self.reference_regex,
                                "",
                                previous_title,
                            ).strip(),
                            "content": content_text,
                            "instructions": instructions,
                            "reference": reference,
                        }
                    )
        return articles

    def split_content_instructions(self, content: str) -> tuple[str, str]:
        """
        Splits actual content and `Prompt Instruction: <xx>`
        """
        splitted = content.split(self.prompt_instruction_splitter)
        content = splitted[0]
        instructions = "".join(splitted[1:])
        instructions = instructions.strip("\n").strip()

        return content, instructions

    def get_titles(self, elements: List) -> List[str]:
        """
        Returns 2-nd level titles from document.
        """
        titles = []

        for element in elements:
            if "paragraph" not in element:
                continue

            text = self.get_element_text(element)
            style = self.get_element_style(element)

            if self.is_chapter_title(text, style):
                text = text.replace("\n", ";")
                titles.append(text)

        return titles

    def parse_doc(self) -> Tuple[List, str]:
        service = build("docs", "v1")

        document = self.get_document(service, self.document_id)
        elements = self.get_elements(document)
        articles = self.get_articles(elements)
        chapters = self.get_titles(elements)

        document_id: str = document.get("documentId", "")

        return articles, chapters, document_id
