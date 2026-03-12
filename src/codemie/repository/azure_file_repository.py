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

from typing import Any
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings
from codemie_tools.base.file_object import FileObject

from .base_file_repository import FileRepository, DirectoryObject
from codemie.configs.logger import logger


class AzureFileRepository(FileRepository):
    def __init__(self, connection_string: str = None, storage_account_name: str = None):
        if connection_string:
            self.blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        else:
            account_url = f"https://{storage_account_name}.blob.core.windows.net"
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=DefaultAzureCredential(),
            )
        logger.debug("AzureFileRepository instantiated")

    def _get_container(self, owner: str) -> ContainerClient:
        logger.debug(f"Getting container client for owner: {owner}")
        container_client = self.blob_service_client.get_container_client(owner)
        try:
            container_client.get_container_properties()
            logger.debug(f"Container {owner} accessed successfully")
        except Exception:
            logger.debug(f"Container {owner} does not exist. Creating new container.")
            container_client = self.blob_service_client.create_container(owner)
            logger.debug(f"Container {owner} created successfully")
        return container_client

    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        logger.debug(f"Writing file {name} to container {owner} with MIME type {mime_type}")
        container_client = self._get_container(owner)
        blob_client = container_client.get_blob_client(name)
        content_settings = ContentSettings(content_type=mime_type)
        try:
            blob_client.upload_blob(content, content_settings=content_settings, overwrite=True)
            logger.debug(f"File {name} uploaded successfully in container {owner}")
        except Exception as e:
            logger.error(f"Failed to upload file {name} in container {owner}: {e}")
        return FileObject(name=name, mime_type=mime_type, owner=owner, content=content)

    def read_file(self, file_name: str, owner: str, mime_type: str = None, path: str = None) -> FileObject:
        logger.debug(f"Reading file {file_name} from container {owner}")
        container_client = self._get_container(owner)
        blob_client = container_client.get_blob_client(file_name)
        content = blob_client.download_blob().readall()
        if not mime_type:
            mime_type = blob_client.get_blob_properties().content_settings.content_type
        logger.debug(f"File {file_name} read successfully from container {owner}")
        return FileObject(name=file_name, owner=owner, content=content, mime_type=mime_type)

    def create_directory(self, name: str, owner: str) -> DirectoryObject:
        logger.debug(f"Creating directory {name} in container {owner}")
        container_client = self._get_container(owner)
        blob_client = container_client.get_blob_client(f"{name}/")
        blob_client.upload_blob(
            "", content_settings=ContentSettings(content_type="application/x-directory"), overwrite=True
        )
        logger.debug(f"Directory {name} created successfully in container {owner}")
        return DirectoryObject(name=name, owner=owner)
