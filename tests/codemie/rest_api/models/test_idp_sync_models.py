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

import pytest
from pydantic import ValidationError

from codemie.rest_api.models import idp_sync
from codemie.rest_api.models.idp_sync import IdpAssignRequest, IdpUserUpsertRequest

_VALID = {
    "id": "8f14e45f-ceea-467a-9575-1c5e1f2b3d4a",
    "email": "jane@example.com",
    "username": "jane",
    "name": "Jane",
    "is_admin": False,
}


def test_valid_upsert_request():
    assert IdpUserUpsertRequest(**_VALID).id == _VALID["id"]


@pytest.mark.parametrize("bad_id", ["", "not-a-uuid", "123"])
def test_malformed_id_is_rejected(bad_id):
    with pytest.raises(ValidationError):
        IdpUserUpsertRequest(**{**_VALID, "id": bad_id})


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_project_name_is_rejected(name):
    with pytest.raises(ValidationError):
        IdpAssignRequest(project_name=name)


def test_assign_defaults_to_non_admin():
    assert IdpAssignRequest(project_name="p").is_project_admin is False


@pytest.mark.parametrize(
    "model",
    [
        idp_sync.IdpUserUpsertRequest,
        idp_sync.IdpAssignRequest,
        idp_sync.IdpMembership,
        idp_sync.IdpUserResponse,
        idp_sync.IdpUserPage,
    ],
)
def test_every_field_has_description(model):
    missing = [name for name, field in model.model_fields.items() if not field.description]
    assert missing == []


def test_every_request_and_scalar_field_has_examples():
    for model in (idp_sync.IdpUserUpsertRequest, idp_sync.IdpAssignRequest, idp_sync.IdpMembership):
        assert [n for n, f in model.model_fields.items() if not f.examples] == []


def test_is_admin_is_described_as_advisory():
    assert "advisory" in idp_sync.IdpUserUpsertRequest.model_fields["is_admin"].description.lower()


def test_picture_of_max_length_is_accepted():
    req = IdpUserUpsertRequest(**{**_VALID, "picture": "p" * 1024})
    assert len(req.picture) == 1024


def test_picture_over_max_length_is_rejected():
    with pytest.raises(ValidationError):
        IdpUserUpsertRequest(**{**_VALID, "picture": "p" * 1025})


def test_request_without_picture_is_valid():
    assert IdpUserUpsertRequest(**_VALID).picture is None


def test_response_has_described_picture_and_maintainer_fields():
    fields = idp_sync.IdpUserResponse.model_fields
    assert fields["picture"].description
    assert fields["is_maintainer"].description
    assert fields["is_maintainer"].default is False
    assert fields["picture"].default is None


def test_picture_max_length_matches_migration():
    import re
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[4] / "src/external/alembic/versions/299a10eb05a0_add_user_management_tables.py"
    )
    match = re.search(r"sa\.Column\(\s*['\"]picture['\"]\s*,\s*sa\.String\((\d+)\)", path.read_text())
    assert match is not None
    assert int(match.group(1)) == idp_sync.PICTURE_MAX_LENGTH


def test_role_flags_are_optional_and_default_to_not_sent():
    req = IdpUserUpsertRequest(**_VALID)

    assert req.is_maintainer is None and req.is_auditor is None
    assert "is_maintainer" not in req.model_dump(exclude_unset=True)


def test_a_maintainer_must_be_an_admin():
    with pytest.raises(ValidationError, match="is_maintainer requires is_admin"):
        IdpUserUpsertRequest(**{**_VALID, "is_admin": False, "is_maintainer": True})

    assert IdpUserUpsertRequest(**{**_VALID, "is_admin": True, "is_maintainer": True}).is_maintainer is True


@pytest.mark.parametrize("is_admin", [True, False])
def test_removing_the_maintainer_role_is_valid_whatever_the_admin_flag(is_admin):
    assert IdpUserUpsertRequest(**{**_VALID, "is_admin": is_admin, "is_maintainer": False}).is_maintainer is False


def test_an_auditor_needs_no_other_role():
    assert IdpUserUpsertRequest(**{**_VALID, "is_auditor": True}).is_auditor is True


def test_response_reports_the_auditor_flag_and_defaults_to_false():
    response = idp_sync.IdpUserResponse(
        id=_VALID["id"], is_active=True, email="a@b.c", username="a", name="A", is_admin=False, memberships=[]
    )

    assert response.is_auditor is False and response.is_maintainer is False


@pytest.mark.parametrize("user_type", ["regular", "external", None])
def test_user_type_accepts_regular_and_external_or_nothing(user_type):
    request = IdpUserUpsertRequest(**{**_VALID, "user_type": user_type})
    assert request.user_type == user_type


@pytest.mark.parametrize("user_type", ["service_account", "admin", ""])
def test_user_type_refuses_anything_else(user_type):
    with pytest.raises(ValidationError):
        IdpUserUpsertRequest(**{**_VALID, "user_type": user_type})
