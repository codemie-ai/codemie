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

from enum import Enum
from pydantic import BaseModel
from codemie.configs import config, logger
from codemie.repository.aws_file_repository import AWSFileRepository
from codemie.repository.azure_file_repository import AzureFileRepository
from codemie.repository.gcp_file_repository import GCPFileRepository
from codemie.repository.file_system_repository import FileSystemRepository


class FileStorageType(Enum):
    GCP = 'gcp'
    AWS = 'aws'
    FILE_SYSTEM = 'filesystem'
    AZURE = 'azure'


class FileRepositoryFactory(BaseModel):
    @classmethod
    def get_current_repository(cls):
        """Determines the current storage type based on the application's configuration
        and returns an instance of the appropriate file repository."""
        get_current_storage_type = cls.get_current_storage_type()
        return cls._get_repository(get_current_storage_type)

    @classmethod
    def _get_repository(cls, storage_type: FileStorageType):
        """Private method to instantiate and return a file repository object based on
        the specified storage type.

        :param storage_type: An instance of `FileStorageType` indicating the desired storage type.
        :return: An instance of a file repository corresponding to the provided `storage_type`.
        :raises UnsupportedFileSystemException: If an unsupported storage type (e.g., `AWS`) is provided.
        """
        logger.debug(f"Creating file repository for storage type: {storage_type.value}")
        if storage_type == FileStorageType.GCP:
            return GCPFileRepository()
        elif storage_type == FileStorageType.AZURE:
            return AzureFileRepository(
                connection_string=config.AZURE_STORAGE_CONNECTION_STRING,
                storage_account_name=config.AZURE_STORAGE_ACCOUNT_NAME,
            )
        elif storage_type == FileStorageType.FILE_SYSTEM:
            return FileSystemRepository()
        elif storage_type == FileStorageType.AWS:
            return AWSFileRepository(region_name=config.AWS_S3_REGION, root_bucket=config.AWS_S3_BUCKET_NAME)

    @classmethod
    def get_current_storage_type(cls):
        """Determines the current storage type based on the application's configuration.

        :return: An instance of `FileStorageType` indicating the configured file storage type.
        Defaults to `FileSystem` if no storage type is specified in the configuration.
        """
        return FileStorageType(config.FILES_STORAGE_TYPE) if config.FILES_STORAGE_TYPE else FileStorageType.FILE_SYSTEM
