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

from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from codemie.rest_api.models.hosted_application import HOSTED_APP_MODELS


def test_seven_entities_register_without_reusing_project_applications():
    names = {model.__table__.name for model in HOSTED_APP_MODELS}
    assert len(names) == 7
    assert "applications" not in names
    assert {"hosted_application_user_state", "hosted_application_view_sessions", "hosted_applications"} <= names


def test_scoped_circular_foreign_keys_are_deferrable():
    constraints = {
        constraint.name: constraint
        for model in HOSTED_APP_MODELS
        for constraint in model.__table__.foreign_key_constraints
    }
    for name in (
        "version_source_attempt",
        "version_accepted_attempt",
        "version_accepted_job",
        "app_active_version",
        "app_selected_version",
        "job_previous_job",
    ):
        assert constraints[name].deferrable
        assert constraints[name].initially == "IMMEDIATE"


def test_postgres_models_retain_acceptance_and_typed_parent_constraints():
    ddl = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=postgresql.dialect())) for model in HOSTED_APP_MODELS
    )
    assert "accepted_job_id IS NOT NULL" in ddl
    assert "parent_kind = 'auth_context'" in ddl
    assert "FOREIGN KEY(principal_id, parent_id, parent_kind)" in ddl
    assert "manifest_digest IS NOT NULL" in ddl
    assert "policy_definition_digest IS NOT NULL" in ddl


def test_replay_body_persists_python_none_as_sql_null():
    from codemie.rest_api.models.hosted_application import HostedApplicationOperations

    column_type = HostedApplicationOperations.__table__.c.replay_body.type
    assert column_type.none_as_null is True
    dialect = postgresql.asyncpg.dialect()
    assert column_type.dialect_impl(dialect).bind_processor(dialect)(None) is None


def test_trigram_search_index_resolves_operator_class_through_search_path():
    from pathlib import Path

    from sqlalchemy.schema import CreateIndex

    from codemie.rest_api.models.hosted_application import HostedApplications

    index = next(item for item in HostedApplications.__table__.indexes if item.name == "hosted_applications_search")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "gin_trgm_ops" in ddl and "public.gin_trgm_ops" not in ddl
    root = Path(__file__).parents[4] / "src/external/alembic/versions"
    migration = (root / "e3b7c1a95d42_add_hosted_applications.py").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm\")" in migration
    assert "public.gin_trgm_ops" not in migration and "WITH SCHEMA" not in migration


def test_access_core_columns_replace_owner_audience_and_grants():
    from codemie.rest_api.models.hosted_application import HostedApplicationStorageAttempts, HostedApplications

    names = {model.__table__.name for model in HOSTED_APP_MODELS}
    assert "application_grants" not in names
    apps = HostedApplications.__table__.c
    for gone in ("owner_id", "audience", "discoverable", "publication_state"):
        assert gone not in apps
    for present in ("project", "created_by", "shared", "is_global", "security_state"):
        assert present in apps
    assert not apps.project.nullable and not apps.created_by.nullable
    assert not apps.shared.nullable and not apps.is_global.nullable
    attempts = HostedApplicationStorageAttempts.__table__.c
    assert "owner_id" not in attempts and not attempts.quota_principal_id.nullable
    index_names = {index.name for index in HostedApplications.__table__.indexes}
    assert "hosted_applications_created_by_id" in index_names and "hosted_applications_owner" not in index_names
    assert "hosted_applications" in {fk.column.table.name for fk in attempts.app_id.foreign_keys}
