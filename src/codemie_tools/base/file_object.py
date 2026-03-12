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

import base64
from typing import Any, Optional

import chardet
from pydantic import BaseModel

from codemie_tools.base.string_serializer import StringSerializer

UTF_8 = "utf-8"


class MimeType:
    """A class to represent the MIME type of a file"""

    IMG_PREFIX = 'image'

    CSV_TYPE = 'text/csv'
    PNG_TYPE = 'image/png'
    PDF_TYPE = 'application/pdf'
    SVG_TYPE = 'image/svg+xml'
    PPTX_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    XLS_TYPE = 'application/vnd.ms-excel'
    DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

    def __init__(self, mime_type: str):
        self.mime_type = mime_type

    @property
    def is_image(self) -> bool:
        """Check if the mime type is an image, excluding SVG"""
        return self.mime_type.startswith(self.IMG_PREFIX) and self.mime_type != self.SVG_TYPE

    @property
    def is_csv(self) -> bool:
        """Check if the mime type is a CSV file"""
        return self.mime_type == self.CSV_TYPE

    @property
    def is_png(self) -> bool:
        """Check if the mime type is a PNG image"""
        return self.mime_type == self.PNG_TYPE

    @property
    def is_pdf(self) -> bool:
        """Check if the mime type is a PDF file"""
        return self.mime_type == self.PDF_TYPE

    @property
    def is_pptx(self) -> bool:
        """Check if the mime type is a PPTX (PowerPoint) file"""
        return self.mime_type == self.PPTX_TYPE

    @property
    def is_excel(self) -> bool:
        """Check if the mime type is an Excel file (XLS or XLSX)"""
        return self.mime_type in [self.XLSX_TYPE, self.XLS_TYPE]

    @property
    def is_docx(self) -> bool:
        """Check if the mime type is a DOCX (Word) file"""
        return self.mime_type == self.DOCX_TYPE

    @property
    def is_text_based(self) -> bool:
        """Check if the mime type is text-based"""
        return self.mime_type is not None and self.mime_type.startswith('text')


class FileObject(BaseModel):
    """
    A representation of a file object.

    Attributes:
        name (str): The name of the file.
        mime_type (str): The type of the file.
        path (str): The path where the file is located.
        owner (str): The owner of the file.
        content (Any, optional): The content of the file. Defaults to None.
    """

    name: str
    mime_type: str
    owner: str
    path: Optional[str] = None
    content: Optional[Any] = None

    @staticmethod
    def to_string_content(file_content: str | bytes):
        """
        Converts file content to string representation.

        Parameters:
            file_content (str|bytes): The content to be converted to string.

        Returns:
            str: The content as a string, with encoding detection for bytes input.
        """
        if isinstance(file_content, str):
            return file_content
        else:
            encoding_info = chardet.detect(file_content)
            encoding = encoding_info.get('encoding') if encoding_info and encoding_info.get('encoding') else UTF_8
            try:
                data = file_content.decode(encoding)
            except UnicodeDecodeError:
                data = file_content.decode(UTF_8, errors='replace')
            return data

    def to_encoded_url(self) -> str:
        """
        Generates a base64 encoded URL consisting of the mime_type, owner, and file name.

        Returns:
            str: The base64 encoded URL.
        """
        return StringSerializer.serialize([self.mime_type, self.owner, self.name])

    @staticmethod
    def from_encoded_url(encoded_url: str):
        """
        Creates a FileObject from a base64 encoded URL.

        Parameters:
            encoded_url (str): The base64 encoded URL containing mime_type, owner, and file name.

        Returns:
            FileObject: An instance of FileObject.
        """
        deserialized_data = StringSerializer.deserialize(encoded_url)
        if len(deserialized_data) != 3:
            raise ValueError(
                f"Invalid encoded URL data: {encoded_url}, "
                f"expected 3 values but got {len(deserialized_data)}: {deserialized_data}"
            )

        mime_type, owner, name = deserialized_data
        return FileObject(name=name, mime_type=mime_type, owner=owner)

    def is_image(self) -> bool:
        """
        Checks if the file is an image.

        Returns:
            bool: True if the file is an image, False otherwise.
        """
        return MimeType(self.mime_type).is_image

    def is_text_based(self) -> bool:
        """
        Checks if the file has a text-based MIME type.

        Returns:
            bool: True if the file is text-based, False otherwise.
        """
        return MimeType(self.mime_type).is_text_based

    def base64_content(self) -> str:
        """
        Encodes the content of the file to base64.

        Returns:
            str: The base64 encoded content.
        """
        prefix = "data:" + self.mime_type + ";base64,"
        return prefix + self.to_image_base64()

    def to_image_base64(self):
        """
        Encodes the file content to base64 for image representation.

        Returns:
            str: The base64 encoded content as a string.
        """
        return base64.b64encode(self.content).decode(UTF_8)

    def bytes_content(self) -> Optional[bytes]:
        """
        Returns the content of the file as bytes.

        If the content is already in bytes format, returns it directly.
        Otherwise, encodes the string content to bytes using UTF-8 encoding.
        If content is None, returns None.

        Returns:
            Optional[bytes]: The file content as bytes, or None if content is None.
        """
        if self.content is None:
            return None

        if isinstance(self.content, bytes):
            return self.content

        return self.content.encode(UTF_8, errors='replace')

    def string_content(self) -> Optional[str]:
        """
        Returns the content of the file as a string.

        Uses the to_string_content static method to convert the file content to a string.
        If content is None, returns None.

        Returns:
            Optional[str]: The file content as a string, or None if content is None.
        """
        if self.content is None:
            return None

        return self.to_string_content(self.content)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"<File: name={self.name}, mime_type = {self.mime_type}, owner={self.owner}, path={self.path}>"
