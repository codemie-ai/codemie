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

from langchain_core.documents import Document


class DatasourceProcessorCallback:
    def on_start(self):
        """
        Stub method to be overridden by subclasses or instances.
        This method will be called after the data source processing starts.
        """
        pass

    def on_split_documents(self, docs: list[Document]):
        """
        Stub method to be overridden by subclasses or instances.
        This method will be called after the data source is split into documents.
        """
        pass

    def on_complete(self, result):
        """
        Stub method to be overridden by subclasses or instances.
        This method will be called after the data source processing ends.

        :param result: The result of the data source processing
        """
        pass

    def on_error(self, exception: Exception):
        """
        Stub method to be overridden by subclasses or instances.
        This method will be called if an error occurs during the data source processing.

        :param exception: The exception that occurred
        """
        pass
