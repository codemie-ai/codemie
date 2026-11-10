# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.hosted_application_requests import (
    AppCreate,
    AppPatch,
    VersionCreate,
)


def test_plain_metadata_is_normalized_and_trimmed():
    result = AppCreate(name="  Cafe\u0301  ", description="  useful visual  ", project="team-a")
    assert result.name == "Café"
    assert result.description == "useful visual"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "description": "y", "org_id": "other"},
        {"name": "", "description": "y"},
        {"name": "x", "description": " "},
        {"name": "x\u0000", "description": "y"},
        {"name": "x" * 121, "description": "y"},
        {"name": 42, "description": "y"},
    ],
)
def test_metadata_rejects_coercion_escalation_and_bad_text(payload):
    with pytest.raises(ValidationError):
        AppCreate(**payload)


def test_patch_requires_changes_and_disallows_null_name():
    for payload in ({}, {"name": None}, {"audience": "organization"}):
        with pytest.raises(ValidationError):
            AppPatch(**payload)
    with pytest.raises(ValidationError):
        AppPatch(folder_id=None)
    with pytest.raises(ValidationError):
        AppCreate(name="x", description="y", folder_id=None)


def test_version_limits_and_unknown_fields():
    with pytest.raises(ValidationError):
        VersionCreate(filename="dist.zip", artifact_kind="zip", byte_size=True)
    with pytest.raises(ValidationError):
        VersionCreate(filename="../dist.zip", artifact_kind="zip", byte_size=1)
    with pytest.raises(ValidationError):
        VersionCreate(filename="x.html", artifact_kind="html", byte_size=20 * 1024 * 1024 + 1)


def test_descriptions_accept_normal_multiline_text_but_reject_hidden_controls():
    assert (
        AppCreate(name="Visual", description="First paragraph.\n\nSecond paragraph.", project="team-a").description
        == "First paragraph.\n\nSecond paragraph."
    )
    for value in ("contains\x00nul", "contains\u202eoverride"):
        with pytest.raises(ValidationError):
            AppCreate(name="Visual", description=value, project="team-a")


def test_create_takes_shared_and_defaults_it_to_private():
    assert AppCreate(name="Visual", description="Text", project="team-a").shared is False
    assert AppCreate(name="Visual", description="Text", project="team-a", shared=True).shared is True
    for value in ("true", 1, None):
        with pytest.raises(ValidationError):
            AppCreate(name="Visual", description="Text", project="team-a", shared=value)


def test_patch_still_treats_an_omitted_shared_as_no_change():
    assert "shared" not in AppPatch(name="Renamed").model_fields_set
    assert AppPatch(name="Renamed").shared is None


@pytest.mark.parametrize("field", ["name", "description", "project"])
def test_a_null_metadata_field_in_a_patch_is_a_validation_error(field):
    """CR-010: ``"description": null`` must answer 422, never escape the validator as a TypeError (5xx)."""
    with pytest.raises(ValidationError):
        AppPatch.model_validate({field: None})
