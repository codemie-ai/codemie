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

from codemie_tools.base.file_object import FileObject

from codemie.repository.repository_factory import FileRepositoryFactory


class FileService:
    @classmethod
    def get_file_object(cls, file_name: str) -> FileObject:
        """
        Load a FileObject with content from encoded file name.

        Args:
            file_name: Base64 encoded file URL containing mime_type, owner, and name

        Returns:
            FileObject with content loaded from repository
        """
        file_object = FileObject.from_encoded_url(file_name)
        file_repo = FileRepositoryFactory().get_current_repository()
        return file_repo.read_file(file_name=file_object.name, owner=file_object.owner, mime_type=file_object.mime_type)

    @classmethod
    def get_image_base64(cls, file_name: str):
        return cls.get_file_object(file_name).to_image_base64()
