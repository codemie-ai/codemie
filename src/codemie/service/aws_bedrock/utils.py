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

import random
import time
from typing import Any, List, Optional
from elasticsearch import NotFoundError
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from codemie.rest_api.models.settings import AWSCredentials, SettingType, Settings, SettingsBase
from codemie_tools.base.models import CredentialTypes
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.exceptions import (
    AwsCredentialsNotFoundException,
    SettingAWSCredentialTypeRequired,
    SettingAccessDeniedException,
    SettingIdRequiredException,
    SettingNotFoundException,
)
from codemie.service.settings.settings import SettingsService
from codemie.configs import logger

MAX_RETRIES = 3
BASE_DELAY = 0.5  # in seconds, higher than this and we experience api timeouts

CONFIGURATION_INVALID_EXCEPTIONS = [
    "UnrecognizedClientException",
    "InvalidUserID.NotFound",
    "SignatureDoesNotMatch",
    "InvalidAccessKeyId",
    "IncompleteSignature",
    "AuthFailure",
    "Could not connect to the endpoint URL",
]


def get_setting_for_user(user: User, setting_id: str) -> SettingsBase:
    """
    Retrieves the AWS settings for a given user and setting ID, ensuring the user has access.

    Args:
        user (User): The user requesting the settings.
        setting_id (str): The ID of the settings to retrieve.

    Returns:
        SettingsBase: The settings object for the given ID.

    Raises:
        SettingNotFoundException: If the settings are not found.
        SettingAccessDeniedException: If the user does not have access to the settings.
    """
    try:
        setting: SettingsBase = Settings.get_by_id(id_=setting_id)  # type: ignore
    except (NotFoundError, KeyError):
        raise SettingNotFoundException(str(setting_id))

    if setting.credential_type != CredentialTypes.AWS:
        raise SettingAWSCredentialTypeRequired(str(setting_id))

    if user.is_admin:
        return setting

    # Check if this is a user setting owned by the requesting user
    if setting.setting_type == SettingType.USER and setting.user_id == user.id:
        return setting

    # Check if this is a project setting that the user has access to
    if setting.setting_type == SettingType.PROJECT:
        accessible_projects = set(user.admin_project_names or []).union(set(user.project_names or []))
        if setting.project_name in accessible_projects:
            return setting

    raise SettingAccessDeniedException(user.id, str(setting_id), setting.project_name)


def get_all_settings_for_user(user: User) -> List[SettingsBase]:
    """
    Retrieves all AWS settings (both user and project) that the user has access to.

    Args:
        user (User): The user requesting the settings.

    Returns:
        List[SettingsBase]: List of settings the user has access to.
    """
    all_settings = []

    user_settings = SettingsService.get_settings(
        user_id=user.id,
        settings_type=SettingType.USER,
        credential_type=CredentialTypes.AWS,
    )
    all_settings.extend(user_settings)

    if user.is_admin:
        project_settings = SettingsService.get_all_settings(
            settings_type=SettingType.PROJECT,
            credential_type=CredentialTypes.AWS,
        )
        all_settings.extend(project_settings)
    else:
        project_names: List[str] = list(set(user.admin_project_names or []).union(set(user.project_names or [])))
        if project_names:
            project_settings = SettingsService.get_settings(
                project_names=project_names,
                settings_type=SettingType.PROJECT,
                credential_type=CredentialTypes.AWS,
            )
            all_settings.extend(project_settings)

    return all_settings


def get_setting_aws_credentials(setting_id: str | None) -> AWSCredentials:
    """
    Retrieves AWS credentials for a given user and setting ID.

    Args:
        setting_id (str | None): The ID of the settings/integration.

    Returns:
        AWSCredentials: The AWS credentials for the user and setting.

    Raises:
        SettingIdRequiredException: If the setting ID is not provided.
        AwsCredentialsNotFoundException: If AWS credentials are not found for the given setting.
    """
    if not setting_id:
        raise SettingIdRequiredException

    aws_creds = SettingsService.get_aws_creds(integration_id=setting_id)

    if not aws_creds:
        raise AwsCredentialsNotFoundException(setting_id)

    return aws_creds


def get_aws_client_for_service(
    service: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: Optional[str] = None,
    max_retry_attempts: Optional[int] = None,
):
    """
    Creates and returns a boto3 client for the specified AWS service.

    Args:
        service (str): The AWS service name (e.g., 'bedrock', 's3').
        region (str): The AWS region.
        access_key_id (str): The AWS access key ID.
        secret_access_key (str): The AWS secret access key.
        session_token (Optional[str]): The AWS session token, if using temporary credentials.
        max_retry_attempts (Optional[int]): Maximum retry attempts for boto3 client.
                                     If None, uses default boto3 retry behavior.

    Returns:
        boto3.client: The AWS service client.
    """
    # Build config parameters dynamically
    config_params: dict = {
        "region_name": region,
    }

    if max_retry_attempts is not None:
        config_params["retries"] = {
            "max_attempts": max_retry_attempts,
            "mode": "standard",
        }

    client_config = Config(**config_params)
    api_instance = boto3.client(
        service,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        aws_session_token=session_token,
        config=client_config,
    )
    return api_instance


def handle_aws_call(func, *args, **kwargs):
    """
    Executes an AWS SDK call with exponential backoff for throttling errors.

    Args:
        func: The function to call.
        *args: Positional arguments for the function.
        **kwargs: Keyword arguments for the function.

    Returns:
        Any: The result of the AWS SDK call.

    Raises:
        ClientError: If an AWS client error occurs after all retries.
        Exception: For any other unexpected errors.
    """

    last_exception = None

    # 4 attempts in total (MAX_RETRIES + 1 initial call)
    for attempt in range(MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            last_exception = e

            # Handle non-throttling errors immediately
            throttling_errors = [
                "ThrottlingException",
                "TooManyRequestsException",
                "RequestLimitExceeded",
                "Throttling",
            ]
            if error_code not in throttling_errors:
                logger.error(f"AWS ClientError (non-throttling): {e}")
                raise e

            # Handle max retries exceeded
            if attempt >= MAX_RETRIES:
                logger.error(f"AWS throttling exceeded max retries ({MAX_RETRIES}): {error_code}")
                raise e

            # Calculate delay and retry
            base_delay = BASE_DELAY * (2**attempt)
            jitter = random.uniform(0, base_delay * 0.5)  # 0 to 50% of base delay
            delay = base_delay + jitter

            logger.warning(
                f"AWS throttling detected (attempt {attempt + 1}/{MAX_RETRIES + 1}), "
                f"retrying in {delay:.2f}s: {error_code}"
            )
            time.sleep(delay)
            continue

        except Exception as e:
            logger.error(f"Unexpected error when calling AWS: {e}")
            raise

    # This should never be reached, but handle it just in case (correct function return in all cases)
    if last_exception:
        raise last_exception
    else:
        raise RuntimeError("Unexpected condition: no exception but function didn't return")


def call_bedrock_listing_api(
    service_name: str,
    api_method_name: str,
    response_key: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: Optional[str] = None,
    page: int = 0,
    per_page: int = 10,
    next_token: Optional[str] = None,
    max_retry_attempts: Optional[int] = None,
    **api_params: Any,
) -> tuple[List[dict], Optional[str]]:
    """
    Generic function to call any AWS Bedrock listing API with pagination support.

    Args:
        service_name (str): AWS service name (e.g., "bedrock-agent", "bedrock")
        api_method_name (str): The boto3 API method name (e.g., "list_agents", "list_knowledge_bases")
        response_key (str): The key in the response containing the data (e.g., "agentSummaries", etc.)
        region (str): The AWS region for the request
        access_key_id (str): The AWS access key ID
        secret_access_key (str): The AWS secret access key
        session_token (Optional[str]): The AWS session token, if using temporary credentials
        page (int): The page number for pagination (0-based)
        per_page (int): The number of items per page
        next_token (Optional[str]): Token for pagination
        max_retry_attempts (Optional[int]): Maximum number of retry attempts
        **api_params: Additional parameters specific to the API method

    Returns:
        tuple[List[dict], Optional[str]]: Tuple of (data list, next_token)
    """

    def _func(client):
        # Base parameters for pagination
        params: dict = {"maxResults": min(per_page, 1000)}

        # Add any API-specific parameters
        params.update(api_params)

        if next_token:
            # When nextToken is provided, use it directly
            params["nextToken"] = next_token
        else:
            # Use pagination logic for page-based requests
            params["maxResults"] = min((page + 1) * per_page, 1000)

        # Dynamically call the API method
        api_method = getattr(client, api_method_name)
        response = api_method(**params)

        data = response.get(response_key, [])
        response_next_token = response.get("nextToken")

        if next_token:
            # When using nextToken, return data as-is
            return data, response_next_token
        else:
            # Apply pagination by slicing for page-based requests
            start = page * per_page
            end = start + per_page
            return data[start:end], response_next_token

    client = get_aws_client_for_service(
        service_name,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        max_retry_attempts=max_retry_attempts,
    )

    return handle_aws_call(_func, client)
