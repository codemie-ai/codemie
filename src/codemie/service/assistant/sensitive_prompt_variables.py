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

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant, AssistantRequest, PromptVariable


def encrypt_sensitive_prompt_variables(assistant: Assistant) -> None:
    """
    Encrypt default values for sensitive prompt variables in the assistant definition.
    Modifies the assistant object in place.

    Args:
        assistant: The assistant object to encrypt
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK
    from codemie.service.encryption.encryption_factory import EncryptionFactory

    if not assistant.prompt_variables:
        return

    encryption_service = EncryptionFactory().get_current_encryption_service()

    for var in assistant.prompt_variables:
        if getattr(var, 'is_sensitive', False) and var.default_value and var.default_value != SENSITIVE_VALUE_MASK:
            var.default_value = encryption_service.encrypt(var.default_value)


def encrypt_prompt_variables_in_request(request: AssistantRequest) -> None:
    """
    Encrypt default values for sensitive prompt variables in the request.
    Modifies the request object in place.

    Args:
        request: The assistant request to encrypt
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK
    from codemie.service.encryption.encryption_factory import EncryptionFactory

    if not request.prompt_variables:
        return

    encryption_service = EncryptionFactory().get_current_encryption_service()

    for var in request.prompt_variables:
        if getattr(var, 'is_sensitive', False) and var.default_value and var.default_value != SENSITIVE_VALUE_MASK:
            var.default_value = encryption_service.encrypt(var.default_value)


def restore_existing_encrypted_values(request: AssistantRequest, existing_assistant: Assistant) -> None:
    """
    For sensitive variables whose value in the request equals the masked sentinel,
    restore the already-encrypted value from the existing assistant so it is not overwritten.
    Modifies the request object in place.

    Args:
        request: The incoming update request
        existing_assistant: The current assistant loaded from the database
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK

    if not request.prompt_variables or not existing_assistant.prompt_variables:
        return

    existing_by_key = {var.key: var for var in existing_assistant.prompt_variables}

    for var in request.prompt_variables:
        if getattr(var, 'is_sensitive', False) and var.default_value == SENSITIVE_VALUE_MASK:
            existing_var = existing_by_key.get(var.key)
            if existing_var and existing_var.default_value:
                var.default_value = existing_var.default_value


def get_masked_prompt_variables(prompt_variables: list[PromptVariable | dict[str, object]]) -> list[dict[str, object]]:
    """
    Mask the default_value of sensitive prompt variables.
    Returns a new list with masked values for sensitive variables.

    Args:
        prompt_variables: List of prompt variable objects or dicts

    Returns:
        New list with sensitive values masked using SENSITIVE_VALUE_MASK constant
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK

    if not prompt_variables:
        return []

    masked_variables = []
    for var in prompt_variables:
        # Convert to dict if it's a model object
        if hasattr(var, 'model_dump'):
            var_dict = var.model_dump()
        elif hasattr(var, '__dict__'):
            var_dict = dict(var)
        else:
            var_dict = var

        # Mask the default_value if variable is sensitive
        if var_dict.get('is_sensitive') and var_dict.get('default_value'):
            var_dict['default_value'] = SENSITIVE_VALUE_MASK

        masked_variables.append(var_dict)

    return masked_variables


def mask_sensitive_prompt_variables(assistant: Assistant) -> None:
    """
    Mask default values for sensitive prompt variables in the assistant definition.
    Uses the shared masking function and updates the assistant in place.

    Args:
        assistant: The assistant object to mask
    """
    if not assistant.prompt_variables:
        return

    # Use shared function and update each variable in place
    masked_list = get_masked_prompt_variables(assistant.prompt_variables)
    for i, masked_var in enumerate(masked_list):
        if i < len(assistant.prompt_variables):
            assistant.prompt_variables[i].default_value = masked_var['default_value']
