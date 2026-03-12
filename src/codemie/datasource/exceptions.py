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


def _format_datasource_name(name: str) -> str:
    """Format datasource name for display. Special case for Xray -> X-ray."""
    if name.lower() == 'xray':
        return 'X-ray'
    return name


class MissingIntegrationException(Exception):
    """Exception raised when expected context is missing."""

    ERROR_MSG = "{} integration is not completed."

    def __init__(self, integration_type: str, *args, **kwargs):
        super().__init__(self.ERROR_MSG.format(_format_datasource_name(integration_type)), *args, **kwargs)


class InvalidQueryException(Exception):
    ERROR_MSG = "The provided {} expression cannot be parsed. {}"

    def __init__(self, expression_type: str, additional_info: str = "", *args, **kwargs):
        formatted_type = _format_datasource_name(expression_type)
        super().__init__(self.ERROR_MSG.format(formatted_type, additional_info), *args, **kwargs)


class UnauthorizedException(Exception):
    ERROR_MSG = "Cannot retrieve data from {}"

    def __init__(self, datasource_type: str = "", *args, **kwargs):
        super().__init__(self.ERROR_MSG.format(_format_datasource_name(datasource_type)), *args, **kwargs)


class ConnectionException(Exception):
    """Exception raised when connection to datasource fails."""

    ERROR_MSG = "Failed to connect to {}: {}"

    def __init__(self, datasource_type: str, error_details: str = "", *args, **kwargs):
        formatted_type = _format_datasource_name(datasource_type)
        super().__init__(self.ERROR_MSG.format(formatted_type, error_details), *args, **kwargs)


class EmptyResultException(Exception):
    ERROR_MSG = "Based on {} expression empty result returned."

    def __init__(self, expression_type: str, *args, **kwargs):
        super().__init__(self.ERROR_MSG.format(expression_type), *args, **kwargs)


class NoChunksImportedException(Exception):
    """Exception raised when datasource processing completes but no chunks were imported."""

    ERROR_MSG = (
        "No chunks were imported for datasource '{datasource_name}'. "
        "All files were skipped or failed to process. "
        "Please check your files and try again."
    )

    def __init__(self, datasource_name: str, processed_documents: int, *args, **kwargs):
        self.datasource_name = datasource_name
        self.processed_documents = processed_documents
        message = self.ERROR_MSG.format(datasource_name=datasource_name)
        super().__init__(message, *args, **kwargs)
