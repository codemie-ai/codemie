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

from functools import wraps
from fastapi import status
from botocore.exceptions import ClientError
from botocore.exceptions import BotoCoreError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.configs import logger


class SettingNotFoundException(Exception):
    def __init__(self, setting_id: str):
        self.setting_id = setting_id


class SettingAWSCredentialTypeRequired(Exception):
    def __init__(self, setting_id: str):
        self.setting_id = setting_id


class SettingAccessDeniedException(Exception):
    def __init__(self, user_id: str, setting_id: str, project_name: str):
        self.user_id = user_id
        self.setting_id = setting_id
        self.project_name = project_name


class SettingIdRequiredException(Exception):
    pass


class AwsCredentialsNotFoundException(Exception):
    def __init__(self, setting_id: str):
        self.setting_id = setting_id


def _handle_custom_exceptions(e):
    """Handle setting-related exceptions."""
    if isinstance(e, SettingNotFoundException):
        logger.error(f"Setting {e.setting_id} does not exist.")
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Setting not found",
            details=f"Setting {e.setting_id} does not exist.",
            help="Please check the setting ID and try again.",
        ) from e
    elif isinstance(e, SettingAWSCredentialTypeRequired):
        logger.error(f"Setting {e.setting_id} is not of type AWS.")
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Setting is not of type AWS",
            details=f"Setting {e.setting_id} is not of type AWS.",
            help="Please check the setting ID and try again.",
        ) from e
    elif isinstance(e, SettingAccessDeniedException):
        logger.error(f"User {e.user_id} does not have access to setting {e.setting_id} in project {e.project_name}.")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details=(f"User {e.user_id} does not have access to setting {e.setting_id} in project {e.project_name}."),
            help="Ensure that the user has the necessary permissions to access this setting.",
        ) from e
    elif isinstance(e, SettingIdRequiredException):
        logger.error("Setting ID is required to get AWS credentials")
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Setting ID is required",
            details="Setting ID is required to get AWS credentials",
            help="Provide a valid setting ID.",
        ) from e
    elif isinstance(e, AwsCredentialsNotFoundException):
        logger.error(f"Setting {e.setting_id} does not have AWS credentials")
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="AWS credentials not found",
            details=f"Setting {e.setting_id} does not have AWS credentials",
            help="Check your project settings and AWS integration.",
        ) from e


def _handle_client_error(e: ClientError, entity: str, setting_id: str):
    """Handle AWS ClientError exceptions."""
    error_code = e.response.get("Error", {}).get("Code", "AWSClientError")
    error_message = e.response.get("Error", {}).get("Message", str(e))
    logger.error(f"AWS ClientError [{error_code}] while loading {entity} for project {setting_id}: {error_message}")

    if error_code.strip().lower() == "resourcenotfoundexception":
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"AWS Resource not found: {error_code}",
            details=f"Failed to load {entity} for project {setting_id}: {error_message}",
            help="Check your AWS entities, credentials, permissions, and network connectivity. "
            "If the problem persists, consult AWS documentation or contact support.",
        ) from e
    elif error_code == "AccessDeniedException":
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=f"AWS Access Denied: {error_code}",
            details=f"Access denied while loading {entity} for project {setting_id}: {error_message}",
            help="Ensure that your AWS credentials have the necessary permissions to access this resource.",
        ) from e

    raise ExtendedHTTPException(
        code=status.HTTP_502_BAD_GATEWAY,
        message=f"AWS Client Error: {error_code}",
        details=f"Failed to load {entity} for project {setting_id}: {error_message}",
        help="Check your AWS credentials, permissions, and network connectivity. "
        "If the problem persists, consult AWS documentation or contact support.",
    ) from e


def aws_service_exception_handler(entity: str):
    """
    Decorator to handle AWS ClientError and generic exceptions for Bedrock services.
    Usage:
        @aws_service_exception_handler("Bedrock agents")
        def my_service_method(...):
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            setting_id = kwargs.get("setting_id", "unknown")
            try:
                return func(*args, **kwargs)
            except (
                SettingNotFoundException,
                SettingAWSCredentialTypeRequired,
                SettingAccessDeniedException,
                SettingIdRequiredException,
                AwsCredentialsNotFoundException,
            ) as e:
                _handle_custom_exceptions(e)
            except ClientError as e:
                _handle_client_error(e, entity, setting_id)
            except BotoCoreError as e:
                logger.error(f"AWS BotoCoreError while loading {entity} for project {setting_id}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_502_BAD_GATEWAY,
                    message="AWS BotoCore Error",
                    details=f"Failed to load {entity} for project {setting_id}: {str(e)}",
                    help="Check your AWS credentials, permissions, and network connectivity. "
                    "If the problem persists, consult AWS documentation or contact support.",
                ) from e
            except ExtendedHTTPException:
                raise
            except Exception as e:
                logger.error(f"Unexpected error while loading {entity} for project {setting_id}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    message="Unexpected error",
                    details=f"Unexpected error while loading {entity} for project {setting_id}: {e}",
                    help="Please try again later or contact support if the problem persists.",
                ) from e

        return wrapper

    return decorator
