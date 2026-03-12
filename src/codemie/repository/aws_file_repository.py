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

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from typing import Any

from codemie_tools.base.file_object import FileObject

from codemie.configs import config, logger
from codemie.repository.base_file_repository import FileRepository, DirectoryObject


class AWSFileRepository(FileRepository):
    def __init__(self, region_name: str = config.AWS_S3_REGION, root_bucket: str = config.AWS_S3_BUCKET_NAME):
        self.s3_client = boto3.client('s3', region_name=region_name)
        self.root_bucket = root_bucket

        if not self.root_bucket:
            raise ValueError("root_bucket must not be empty")
        logger.debug(f"AWSFileRepository instantiated with root bucket: {self.root_bucket}")

    def _get_full_key(self, name: str, owner: str) -> str:
        return f"{owner}/{name}"

    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        full_key = self._get_full_key(name, owner)
        logger.debug(f"Writing file {full_key} to bucket {self.root_bucket}")
        try:
            self.s3_client.put_object(Bucket=self.root_bucket, Key=full_key, Body=content, ContentType=mime_type)
            logger.debug(f"File {full_key} uploaded successfully in bucket {self.root_bucket}")
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to upload file {full_key} in bucket {self.root_bucket}: {e}")
            raise
        return FileObject(name=name, mime_type=mime_type, owner=owner, content=content)

    def read_file(self, file_name: str, owner: str, mime_type: str = None) -> FileObject:
        full_key = self._get_full_key(file_name, owner)
        logger.debug(f"Reading file {full_key} from bucket {self.root_bucket}")
        try:
            response = self.s3_client.get_object(Bucket=self.root_bucket, Key=full_key)
            content = response['Body'].read()
            if not mime_type:
                mime_type = response['ContentType']
            logger.debug(f"File {full_key} read successfully from bucket {self.root_bucket}")
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to read file {full_key} from bucket {self.root_bucket}: {e}")
            raise
        return FileObject(name=file_name, owner=owner, content=content, mime_type=mime_type)

    def create_directory(self, name: str, owner: str) -> DirectoryObject:
        full_key = self._get_full_key(f"{name}/", owner)
        logger.debug(f"Creating directory {full_key} in bucket {self.root_bucket}")
        try:
            self.s3_client.put_object(Bucket=self.root_bucket, Key=full_key)
            logger.debug(f"Directory {full_key} created successfully in bucket {self.root_bucket}")
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create directory {full_key} in bucket {self.root_bucket}: {e}")
            raise
        return DirectoryObject(name=name, owner=owner)
