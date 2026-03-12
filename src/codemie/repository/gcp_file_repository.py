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

from email.parser import HeaderParser
from typing import Any

from codemie_tools.base.file_object import MimeType
from google.cloud import storage
from .base_file_repository import FileRepository, FileObject, DirectoryObject
from codemie.configs import config, logger


class GCPFileRepository(FileRepository):
    def __init__(self):
        self.client = storage.Client()
        logger.debug("GCPFileRepository instantiated")

    def _get_bucket(self, owner: str):
        bucket = self.client.lookup_bucket(owner)
        if bucket is None:
            logger.debug(f"Bucket {owner} does not exist. Creating new bucket.")
            bucket = self.client.create_bucket(bucket_or_name=owner, location=config.FILES_STORAGE_GCP_REGION)
            logger.debug(f"Bucket {owner} created successfully")
        else:
            logger.debug(f"Bucket {owner} accessed successfully")
        return bucket

    def write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject:
        bucket = self._get_bucket(owner)
        blob = bucket.blob(name)
        blob.upload_from_string(content, content_type=mime_type)
        logger.debug(f"File {name} uploaded successfully in bucket {owner}")
        return FileObject(name=name, mime_type=mime_type, owner=owner, content=content)

    def read_file(self, file_name: str, owner: str, mime_type: str = None, path: str = None) -> FileObject:
        bucket = self._get_bucket(owner)
        blob = bucket.blob(file_name)
        content = blob.download_as_bytes()
        # `content_type` will be available only when the object has been downloaded.
        if not mime_type:
            mime_type = blob.content_type
        if MimeType(mime_type=mime_type).is_text_based:
            msg = HeaderParser().parsestr("Content-Type: " + mime_type)
            params = dict(msg.get_params()[1:])
            charset = params.get("charset", "utf-8")
            try:
                content = content.decode(charset, errors="backslashreplace")
            except LookupError:
                # fallback if charset not recognized
                content = content.decode("utf-8", errors="backslashreplace")

        logger.debug(f"File {file_name} read successfully from bucket {owner}")
        return FileObject(name=file_name, owner=owner, content=content, mime_type=mime_type)

    def create_directory(self, name: str, owner: str) -> DirectoryObject:
        bucket = self._get_bucket(owner)
        blob = bucket.blob(f"{name}/")
        blob.upload_from_string("", content_type="application/x-directory")
        logger.debug(f"Directory {name} created successfully in bucket {owner}")
        return DirectoryObject(name=name, owner=owner)
