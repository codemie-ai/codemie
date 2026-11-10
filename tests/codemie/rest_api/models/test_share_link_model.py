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

"""The common share-link record: one table for every link-sharing resource type, beside ``permissions``."""

from sqlalchemy.dialects import postgresql

from codemie.rest_api.models.permission import Permission, ResourceType, ShareLink


def test_share_links_is_one_common_table_with_the_permissions_resource_columns():
    table = ShareLink.__table__
    assert table.name == "share_links"
    assert set(table.c.keys()) == {
        "id",
        "resource_type",
        "resource_id",
        "token",
        "token_sha256",
        "created_by",
        "created_at",
        "revoked_at",
        "revoked_by",
    }
    resource_type = table.c.resource_type.type
    assert resource_type.enum_class is ResourceType
    assert (resource_type.name, resource_type.schema) == (
        Permission.__table__.c.resource_type.type.name,
        Permission.__table__.c.resource_type.type.schema,
    )
    assert type(table.c.resource_id.type) is type(Permission.__table__.c.resource_id.type)


def test_share_links_reference_no_resource_table():
    assert not ShareLink.__table__.foreign_keys


def test_share_link_token_digest_is_unique_and_revocation_columns_are_nullable():
    columns = ShareLink.__table__.c
    assert columns.token_sha256.unique
    assert not columns.token.nullable and not columns.created_by.nullable
    assert not columns.resource_type.nullable and not columns.resource_id.nullable
    assert columns.revoked_at.nullable and columns.revoked_by.nullable


def test_share_link_allows_one_live_link_per_resource():
    live = {index.name: index for index in ShareLink.__table__.indexes}["uq_share_links_live"]
    assert live.unique
    assert [column.name for column in live.columns] == ["resource_type", "resource_id"]
    predicate = live.dialect_options["postgresql"]["where"].compile(dialect=postgresql.dialect())
    assert str(predicate) == "revoked_at IS NULL"


def test_permission_source_link_id_references_share_links_with_restrict():
    (foreign_key,) = Permission.__table__.c.source_link_id.foreign_keys
    assert foreign_key.target_fullname == "share_links.id"
    # Deleting a link row never removes grants silently: link grants are removed explicitly, with audit.
    assert foreign_key.ondelete == "RESTRICT"
    assert foreign_key.name == "permissions_source_link_id_fkey"
