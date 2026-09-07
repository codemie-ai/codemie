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


class InvalidCustomFieldException(Exception):
    ERROR_MSG = (
        "Unknown or inaccessible Jira custom field(s): {fields}. Check field IDs/names and integration permissions."
    )

    def __init__(self, fields: list[str], *args, **kwargs):
        self.fields = fields
        super().__init__(self.ERROR_MSG.format(fields=", ".join(fields)), *args, **kwargs)


class AmbiguousCustomFieldException(InvalidCustomFieldException):
    """Several Jira fields share the configured display name, so only the field ID identifies one."""

    ERROR_MSG = (
        "Ambiguous Jira custom field name(s): {fields}. Several fields share each name; "
        "use the field ID (e.g. customfield_10001) instead."
    )


class EmptyResultException(Exception):
    ERROR_MSG = "Based on {} expression empty result returned."

    def __init__(self, expression_type: str, *args, **kwargs):
        super().__init__(self.ERROR_MSG.format(expression_type), *args, **kwargs)


class SkippedFileException(Exception):
    """Raised when a file is intentionally skipped during indexing (e.g. exceeds size limit)."""

    def __init__(self, file_name: str, reason: str, *args, **kwargs):
        self.file_name = file_name
        self.reason = reason
        super().__init__(f"Skipped {file_name}: {reason}", *args, **kwargs)


class UnreadableWorkbookError(Exception):
    """Raised when a workbook (e.g. XLSB/XLSX) cannot be parsed by the extraction engine.

    Distinct from a plain ``ValueError`` ("unsupported file type") so that a corrupt or
    encrypted workbook is accounted for as a *failure* rather than silently skipped. The
    calamine engine raises the same generic error for corrupt and for
    password-protected files, so the hint covers both causes.
    """

    ERROR_MSG = (
        "Could not read workbook '{file_name}': {details}. "
        "The file may be corrupt, encrypted or password-protected, "
        "or not a valid Excel workbook."
    )

    def __init__(self, file_name: str = "", details: str = "", *args, **kwargs):
        self.file_name = file_name
        self.details = details
        super().__init__(self.ERROR_MSG.format(file_name=file_name, details=details), *args, **kwargs)

    def __reduce__(self):
        # Reconstruct cleanly across the file process pool (ProcessPoolExecutor) boundary
        # without re-wrapping the already-formatted message.
        return (self.__class__, (self.file_name, self.details))


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
