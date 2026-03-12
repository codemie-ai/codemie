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

import mimetypes
import os
from typing import Any

from codemie_tools.base.file_object import MimeType

from codemie.configs import logger, config
from .base_file_repository import FileRepository, FileObject, DirectoryObject

mimetypes.add_type('application/x-yaml', '.yaml')
mimetypes.add_type('application/x-yaml', '.yml')
mimetypes.add_type('text/xml', '.xml')


class FileSystemRepository(FileRepository):
    """
    Implementation of the BaseRepository for working with the local file system.
    """

    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        directory = self.create_directory(owner=owner, name=name)
        file_path = f"{directory.get_path()}/{name}"
        try:
            logger.debug(f"Writing file to {directory.get_path()}. FileName: {name}, MimeType: {mime_type}")
            mode = 'wb' if isinstance(content, bytes) else 'w'
            with open(f"{file_path}", mode) as file:
                file.write(content)
        except Exception as e:
            logger.error(f"Error writing file to {file_path}: {e}")
            raise
        return FileObject(name=name, mime_type=mime_type, path=file_path, owner=owner, content=content)

    def read_file(self, file_name: str, owner: str, mime_type: str = None) -> FileObject:
        """
        Reads data from a file on the local file system.

        Parameters:
            file_name (str): Path of the file in the repository.
            owner (str): Owner of the file in the repository.
            mime_type (str): MimeType of the file in the repository.

        Returns:
            FileObject: An object with details of the read file.
        """
        file_path = f"{config.FILES_STORAGE_DIR}/{owner}/{file_name}"
        logger.debug(f"Reading file from {file_path}. MimeType: {mime_type}")
        try:
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(file_name)
                if mime_type is None:
                    # Default to octet-stream for unknown file types
                    mime_type = 'application/octet-stream'
                elif mime_type == "application/vnd.ms-excel" and file_name.lower().endswith(".csv"):
                    mime_type = MimeType.CSV_TYPE
            # For text mime_type open file for read, for others - for binary read
            read_mode = 'r' if mime_type.startswith('text') else 'rb'
            with open(file_path, read_mode) as file:
                content = file.read()
            file_obj = FileObject(
                name=file_name, mime_type=mime_type, path=os.path.dirname(file_path), content=content, owner=owner
            )
            logger.debug(f"Read file: {file_obj}")
        except Exception as e:
            logger.error(f"Error reading file from {file_name}: {e}")
            raise
        return file_obj

    def create_directory(self, name: str, owner: str) -> DirectoryObject:
        owner_directory_path = os.path.join(config.FILES_STORAGE_DIR, owner)
        directory_path = os.path.join(owner_directory_path, name)
        directory_path_to_create = os.path.dirname(directory_path)
        logger.info(f"Creating directory {directory_path_to_create}")
        if os.path.dirname(directory_path_to_create) and not os.path.exists(directory_path_to_create):
            os.makedirs(directory_path_to_create)
            logger.info(f"Created directory {directory_path_to_create}")
        else:
            logger.info(f"Directory already exists at {directory_path_to_create}")
        return DirectoryObject(name=name, owner=owner, path=owner_directory_path)
