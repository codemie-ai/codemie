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

from abc import ABC, abstractmethod
from typing import Any

from codemie_tools.base.file_object import FileObject


class DirectoryObject:
    """
    A representation of a directory object.

    Attributes:
        name (str): The name of the directory.
        owner (str): The owner of the directory.
        path (str): The path where the directory is located.
    """

    def __init__(self, name: str, owner: str, path: str = ""):
        self.name = name
        self.owner = owner
        self.path = path

    def get_path(self):
        return self.path

    def __repr__(self):
        return f"<Directory: name={self.name}, owner={self.owner}, path={self.path}>"


class FileRepository(ABC):
    """
    An abstract base class for repository services. This class defines the interface
    for repository operations such as writing and reading files.

    Subclasses should implement the methods defined here to interact with specific
    storage mechanisms (e.g., file system, cloud storage).
    """

    @abstractmethod
    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        pass

    @abstractmethod
    def read_file(self, file_name: str, owner: str, mime_type: str = None) -> FileObject:
        """
        Reads data from a file in the repository.

        Parameters:
            file_name (str): Name of the file in the repository.
            owner (str): User identifier.
            mime_type (str): MimeType of the file in the repository.

        Returns:
            FileObject: An object with details of the read file.
        """
        pass

    @abstractmethod
    def create_directory(self, name: str, owner: str) -> DirectoryObject:
        """
        Creates a new directory in the repository.

        Parameters:
            name (str): The name of the new directory.
            owner (str): User identifier for the owner of the directory.

        Returns:
            DirectoryObject: An object representing the newly created directory.
        """
        pass
