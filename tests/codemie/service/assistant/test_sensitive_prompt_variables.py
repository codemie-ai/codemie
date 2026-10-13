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

"""Characterization tests for the sensitive prompt variable helpers: encrypt, restore and mask."""

from collections import UserDict
from unittest.mock import call, patch

import pytest

from codemie.rest_api.models.assistant import Assistant, AssistantRequest, PromptVariable
from codemie.service.assistant.sensitive_prompt_variables import (
    encrypt_prompt_variables_in_request,
    encrypt_sensitive_prompt_variables,
    get_masked_prompt_variables,
    mask_sensitive_prompt_variables,
    restore_existing_encrypted_values,
)

MASK = "********"


def _variable(key: str, default_value: str, is_sensitive: bool = True) -> PromptVariable:
    return PromptVariable(key=key, default_value=default_value, is_sensitive=is_sensitive)


def _assistant(variables) -> Assistant:
    assistant = Assistant(name="assistant", description="d", system_prompt="p", project="proj")
    # Assigned after construction: the constructor validates and rejects None, an attribute assignment does not.
    assistant.prompt_variables = variables
    return assistant


def _request(variables) -> AssistantRequest:
    return AssistantRequest(
        name="assistant", description="d", system_prompt="p", llm_model_type="gpt-4", prompt_variables=variables
    )


def _values_by_key(holder) -> dict[str, str]:
    return {var.key: var.default_value for var in holder.prompt_variables}


@pytest.fixture
def encryption_factory():
    with patch("codemie.service.encryption.encryption_factory.EncryptionFactory") as factory_cls:
        yield factory_cls


@pytest.fixture
def encryption_service(encryption_factory):
    service = encryption_factory.return_value.get_current_encryption_service.return_value
    service.encrypt.side_effect = lambda value: f"enc:{value}"
    return service


# Both encrypt helpers are twins: one takes the Assistant, the other the AssistantRequest.
both_encrypt_variants = pytest.mark.parametrize(
    ("encrypt", "make_holder"),
    [
        pytest.param(encrypt_sensitive_prompt_variables, _assistant, id="assistant"),
        pytest.param(encrypt_prompt_variables_in_request, _request, id="request"),
    ],
)


@both_encrypt_variants
def test_encrypt_encrypts_only_sensitive_non_empty_non_mask_values_once_each(
    encrypt, make_holder, encryption_factory, encryption_service
):
    holder = make_holder(
        [
            _variable("api_key", "s3cret"),
            _variable("empty_secret", ""),
            _variable("already_masked", MASK),
            _variable("plain", "visible", is_sensitive=False),
            _variable("token", "t0ken"),
        ]
    )

    encrypt(holder)

    assert _values_by_key(holder) == {
        "api_key": "enc:s3cret",
        "empty_secret": "",
        "already_masked": MASK,
        "plain": "visible",
        "token": "enc:t0ken",
    }
    assert encryption_service.encrypt.call_args_list == [call("s3cret"), call("t0ken")]
    encryption_factory.assert_called_once_with()
    encryption_factory.return_value.get_current_encryption_service.assert_called_once_with()


@both_encrypt_variants
@pytest.mark.parametrize("no_variables", [[], None], ids=["empty_list", "none"])
def test_encrypt_returns_without_resolving_the_encryption_factory_when_no_variables(
    encrypt, make_holder, no_variables, encryption_factory
):
    holder = make_holder(no_variables)

    encrypt(holder)

    assert holder.prompt_variables == no_variables
    encryption_factory.assert_not_called()


@both_encrypt_variants
def test_encrypt_propagates_the_provider_error_unwrapped(encrypt, make_holder, encryption_service):
    provider_error = RuntimeError("kms unavailable")
    encryption_service.encrypt.side_effect = provider_error
    holder = make_holder([_variable("api_key", "s3cret")])

    with pytest.raises(RuntimeError) as raised:
        encrypt(holder)

    assert raised.value is provider_error


@both_encrypt_variants
def test_encrypt_propagates_the_error_raised_while_resolving_the_encryption_service(
    encrypt, make_holder, encryption_factory
):
    resolve_error = ValueError("Unsupported encryption service type")
    encryption_factory.return_value.get_current_encryption_service.side_effect = resolve_error
    holder = make_holder([_variable("api_key", "s3cret")])

    with pytest.raises(ValueError) as raised:
        encrypt(holder)

    assert raised.value is resolve_error


def test_restore_swaps_the_mask_for_the_stored_value_matched_by_key():
    request = _request(
        [
            _variable("api_key", MASK),
            _variable("token", "new-t0ken"),
            _variable("password", MASK),
        ]
    )
    stored = _assistant(
        [
            _variable("password", "enc:stored-password"),
            _variable("token", "enc:stored-token"),
            _variable("api_key", "enc:stored-api-key"),
        ]
    )

    restore_existing_encrypted_values(request, stored)

    assert _values_by_key(request) == {
        "api_key": "enc:stored-api-key",
        "token": "new-t0ken",
        "password": "enc:stored-password",
    }


def test_restore_leaves_a_mask_in_a_variable_that_is_not_sensitive():
    request = _request([_variable("plain", MASK, is_sensitive=False)])
    stored = _assistant([_variable("plain", "stored")])

    restore_existing_encrypted_values(request, stored)

    assert _values_by_key(request) == {"plain": MASK}


@pytest.mark.parametrize("no_variables", [[], None], ids=["empty_list", "none"])
def test_restore_returns_when_the_request_has_no_variables(no_variables):
    request = _request(no_variables)
    stored = _assistant([_variable("api_key", "enc:stored")])

    restore_existing_encrypted_values(request, stored)

    assert request.prompt_variables == no_variables


@pytest.mark.parametrize("no_variables", [[], None], ids=["empty_list", "none"])
def test_restore_returns_when_the_stored_assistant_has_no_variables(no_variables):
    request = _request([_variable("api_key", MASK)])
    stored = _assistant(no_variables)

    restore_existing_encrypted_values(request, stored)

    assert _values_by_key(request) == {"api_key": MASK}


def test_restore_keeps_the_mask_when_the_stored_value_is_empty():
    request = _request([_variable("api_key", MASK)])
    stored = _assistant([_variable("api_key", "")])

    restore_existing_encrypted_values(request, stored)

    assert _values_by_key(request) == {"api_key": MASK}


def test_restore_copies_the_stored_value_although_the_stored_variable_is_not_sensitive_pinned_quirk():
    request = _request([_variable("api_key", MASK)])
    stored = _assistant([_variable("api_key", "stored-plain", is_sensitive=False)])

    restore_existing_encrypted_values(request, stored)

    assert _values_by_key(request) == {"api_key": "stored-plain"}


def test_restore_leaves_the_literal_mask_when_no_stored_variable_has_the_key_pinned_quirk():
    request = _request([_variable("new_secret", MASK)])
    stored = _assistant([_variable("other_key", "enc:stored")])

    restore_existing_encrypted_values(request, stored)

    assert _values_by_key(request) == {"new_secret": MASK}


@pytest.mark.parametrize("no_variables", [[], None], ids=["empty_list", "none"])
def test_get_masked_returns_an_empty_list_when_no_variables(no_variables):
    assert get_masked_prompt_variables(no_variables) == []


def test_get_masked_returns_dicts_with_sensitive_non_empty_defaults_masked_and_leaves_the_models_untouched():
    variables = [
        _variable("api_key", "s3cret"),
        _variable("empty_secret", ""),
        _variable("plain", "visible", is_sensitive=False),
    ]

    masked = get_masked_prompt_variables(variables)

    assert masked is not variables
    assert masked == [
        {"key": "api_key", "description": None, "default_value": MASK, "is_sensitive": True},
        {"key": "empty_secret", "description": None, "default_value": "", "is_sensitive": True},
        {"key": "plain", "description": None, "default_value": "visible", "is_sensitive": False},
    ]
    assert [var.default_value for var in variables] == ["s3cret", "", "visible"]


def test_get_masked_copies_a_userdict_input_instead_of_mutating_it():
    source = UserDict({"key": "api_key", "default_value": "s3cret", "is_sensitive": True})

    (masked,) = get_masked_prompt_variables([source])

    assert type(masked) is dict
    assert masked == {"key": "api_key", "default_value": MASK, "is_sensitive": True}
    assert source["default_value"] == "s3cret"


def test_get_masked_mutates_a_plain_dict_input_in_place_pinned_quirk():
    source = {"key": "api_key", "default_value": "s3cret", "is_sensitive": True}

    (masked,) = get_masked_prompt_variables([source])

    assert masked is source
    assert source["default_value"] == MASK


def test_mask_in_place_changes_only_the_default_of_sensitive_non_empty_variables():
    variables = [
        _variable("api_key", "s3cret"),
        _variable("empty_secret", ""),
        _variable("plain", "visible", is_sensitive=False),
    ]
    assistant = _assistant(variables)

    result = mask_sensitive_prompt_variables(assistant)

    assert result is None
    assert assistant.prompt_variables == [
        _variable("api_key", MASK),
        _variable("empty_secret", ""),
        _variable("plain", "visible", is_sensitive=False),
    ]
    assert all(masked is original for masked, original in zip(assistant.prompt_variables, variables))


@pytest.mark.parametrize("no_variables", [[], None], ids=["empty_list", "none"])
def test_mask_in_place_returns_when_no_variables(no_variables):
    assistant = _assistant(no_variables)

    mask_sensitive_prompt_variables(assistant)

    assert assistant.prompt_variables == no_variables
