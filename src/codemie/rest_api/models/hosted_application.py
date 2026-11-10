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


"""Seven SQLModel entities for static hosted applications (EPMCDME-16001)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column, CheckConstraint, ForeignKeyConstraint, UniqueConstraint, Index, text
from sqlalchemy import Text, String, Integer, BigInteger, Boolean, DateTime, LargeBinary
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import SQLModel, Field

from codemie.core import ability
from codemie.core.hosted_applications import access
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.base import PydanticType

# Shared constraint and index terms; the generated schema matches the migrations character for character.
POSITIVE_REVISION = 'revision > 0'
ID_DESC = 'id DESC'
APPLICATION_ID = 'hosted_applications.id'
VERSION_APP_ID = 'hosted_application_versions.app_id'
VERSION_ID = 'hosted_application_versions.id'
ATTEMPT_APP_ID = 'hosted_application_storage_attempts.app_id'
ATTEMPT_VERSION_ID = 'hosted_application_storage_attempts.version_id'
ATTEMPT_ID = 'hosted_application_storage_attempts.id'


class HostedApplicationUserState(SQLModel, table=True):
    __tablename__ = 'hosted_application_user_state'
    __table_args__ = (
        CheckConstraint('byte_limit >= 0'),
        CheckConstraint('stored_bytes >= 0'),
        CheckConstraint('reserved_bytes >= 0'),
        CheckConstraint(POSITIVE_REVISION),
        CheckConstraint('char_length(principal_id) BETWEEN 1 AND 255'),
    )

    principal_id: str = Field(sa_column=Column(Text, nullable=False, primary_key=True))
    byte_limit: int = Field(
        default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('2147483648'))
    )
    stored_bytes: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('0')))
    reserved_bytes: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('0')))
    auth_revoked_before: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class HostedApplications(SQLModel, ability.Owned, table=True):
    __tablename__ = 'hosted_applications'
    __table_args__ = (
        CheckConstraint("artifact_kind IN ('html', 'zip')"),
        CheckConstraint("security_state IN ('clear', 'suspended')"),
        CheckConstraint("lifecycle_state IN ('active', 'purging', 'purged')"),
        CheckConstraint(POSITIVE_REVISION),
        CheckConstraint('access_revision > 0'),
        CheckConstraint(
            "lifecycle_state = 'purged' OR (name IS NOT NULL AND char_length(name) > 0 AND de"
            'scription IS NOT NULL AND char_length(description) > 0)'
        ),
        CheckConstraint("(lifecycle_state = 'active') = (deleted_at IS NULL)"),
        CheckConstraint(
            "lifecycle_state <> 'purged' OR (purged_at IS NOT NULL AND name IS NULL AND descr"
            'iption IS NULL\n        AND selected_version_id IS NULL AND'
            " active_version_id IS NULL AND search_text = ''\n        AND created_by->>'username' = '' AND"
            " created_by->>'name' = '')"
        ),
        ForeignKeyConstraint(
            ['id', 'selected_version_id'],
            [VERSION_APP_ID, VERSION_ID],
            name='app_selected_version',
            deferrable=True,
            initially="IMMEDIATE",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ['id', 'active_version_id'],
            [VERSION_APP_ID, VERSION_ID],
            name='app_active_version',
            deferrable=True,
            initially="IMMEDIATE",
            use_alter=True,
        ),
        Index('hosted_applications_purged', 'purged_at', postgresql_where=text("lifecycle_state = 'purged'")),
        Index('hosted_applications_project', 'project'),
        Index(
            'hosted_applications_created_by_id',
            text("(created_by->>'id')"),
            text('updated_at DESC'),
            text(ID_DESC),
        ),
        Index('hosted_applications_name', 'name', 'id'),
        Index(
            'hosted_applications_search',
            'search_text',
            postgresql_using='gin',
            postgresql_ops={'search_text': 'gin_trgm_ops'},
        ),
    )

    id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    project: str = Field(sa_column=Column(Text, nullable=False))
    created_by: CreatedByUser = Field(sa_column=Column(PydanticType(CreatedByUser), nullable=False))
    shared: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text('false')))
    is_global: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text('false')))
    name: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    description: str | None = Field(default=None, sa_column=Column(String(2000), nullable=True))
    search_text: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("''")))
    artifact_kind: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    selected_version_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    active_version_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    security_state: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("'clear'")))
    lifecycle_state: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("'active'")))
    revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    access_revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    purged_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))

    def is_owned_by(self, user) -> bool:
        return access.is_owned_by(self.created_by.id if self.created_by else None, user)

    def is_managed_by(self, user) -> bool:
        return access.is_managed_by(self.project, user)

    def is_shared_with(self, user) -> bool:
        return access.is_shared_with(self.project, self.shared, self.is_global, self.security_state, user)

    def is_granted_to(self, user, action, grants) -> bool:
        return access.is_granted(self.security_state, user, self.id, grants, action)


class HostedApplicationVersions(SQLModel, table=True):
    __tablename__ = 'hosted_application_versions'
    __table_args__ = (
        CheckConstraint('sequence > 0'),
        CheckConstraint("artifact_kind IN ('html', 'zip')"),
        CheckConstraint('expected_bytes > 0'),
        CheckConstraint('octet_length(expected_digest) = 32'),
        CheckConstraint('upload_generation > 0'),
        CheckConstraint('upload_revision > 0'),
        CheckConstraint("upload_state IN ('created', 'receiving', 'stored', 'completed', 'canceled', 'expired')"),
        CheckConstraint('octet_length(candidate_manifest_digest) = 32'),
        CheckConstraint('configuration_revision > 0'),
        CheckConstraint(
            "validation_state IN ('uploading','queued','validating','awaiting_configuration',"
            "'accepted','rejected','failed','canceled')"
        ),
        CheckConstraint("security_state IN ('clear', 'blocked')"),
        CheckConstraint('octet_length(artifact_digest) = 32'),
        CheckConstraint('octet_length(manifest_digest) = 32'),
        CheckConstraint("routing_mode IN ('static','spa_history')"),
        CheckConstraint('expanded_bytes BETWEEN 0 AND 524288000'),
        CheckConstraint('file_count BETWEEN 0 AND 5000'),
        CheckConstraint(POSITIVE_REVISION),
        UniqueConstraint('app_id', 'id'),
        UniqueConstraint('app_id', 'sequence'),
        ForeignKeyConstraint(['app_id'], [APPLICATION_ID], ondelete='RESTRICT'),
        CheckConstraint(
            "jsonb_typeof(requested_configuration) = 'object' AND octet_length(requested_conf"
            'iguration::text) <= 4096'
        ),
        CheckConstraint(
            "validation_state <> 'accepted' OR (source_attempt_id IS NOT NULL AND accepted_at"
            'tempt_id IS NOT NULL AND accepted_job_id IS NOT NULL\n        AND artifact_digest'
            ' IS NOT NULL AND manifest_key IS NOT NULL AND manifest_digest IS NOT NULL\n      '
            '  AND site_root IS NOT NULL AND entrypoint_path IS NOT NULL AND routing_mode IS '
            'NOT NULL AND file_count > 0 AND accepted_at IS NOT NULL)'
        ),
        ForeignKeyConstraint(
            ['app_id', 'id', 'source_attempt_id'],
            [
                ATTEMPT_APP_ID,
                ATTEMPT_VERSION_ID,
                ATTEMPT_ID,
            ],
            name='version_source_attempt',
            deferrable=True,
            initially="IMMEDIATE",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ['app_id', 'id', 'accepted_attempt_id'],
            [
                ATTEMPT_APP_ID,
                ATTEMPT_VERSION_ID,
                ATTEMPT_ID,
            ],
            name='version_accepted_attempt',
            deferrable=True,
            initially="IMMEDIATE",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ['app_id', 'id', 'accepted_job_id'],
            [
                'hosted_application_jobs.app_id',
                'hosted_application_jobs.version_id',
                'hosted_application_jobs.id',
            ],
            name='version_accepted_job',
            deferrable=True,
            initially="IMMEDIATE",
            use_alter=True,
        ),
        Index(
            'hosted_application_version_upload_expiry',
            'upload_expires_at',
            postgresql_where=text("validation_state = 'uploading'"),
        ),
    )

    id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    app_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False))
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    original_filename: str = Field(sa_column=Column(String(255), nullable=False))
    artifact_kind: str = Field(sa_column=Column(Text, nullable=False))
    expected_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    expected_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    upload_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, unique=True))
    upload_generation: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    upload_revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    upload_expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    upload_state: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("'created'")))
    source_attempt_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    requested_configuration: Any = Field(
        default=None, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'"))
    )
    candidate_manifest_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    configuration_revision: int = Field(
        default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1'))
    )
    validation_state: str = Field(
        default=None, sa_column=Column(Text, nullable=False, server_default=text("'uploading'"))
    )
    security_state: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("'clear'")))
    accepted_attempt_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    accepted_job_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    artifact_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    manifest_key: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    manifest_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    site_root: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    entrypoint_path: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    routing_mode: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    expanded_bytes: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('0')))
    file_count: int = Field(default=None, sa_column=Column(Integer, nullable=False, server_default=text('0')))
    revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    accepted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class HostedApplicationStorageAttempts(SQLModel, table=True):
    __tablename__ = 'hosted_application_storage_attempts'
    __table_args__ = (
        CheckConstraint("kind IN ('upload','validation')"),
        CheckConstraint("provider IN ('filesystem','aws','gcp')"),
        CheckConstraint('upload_generation > 0'),
        CheckConstraint(POSITIVE_REVISION),
        CheckConstraint("state IN ('planned','writing','stored','delete_requested','uncertain','deleted')"),
        CheckConstraint('byte_upper_bound >= 0'),
        CheckConstraint('object_limit BETWEEN 1 AND 5002'),
        CheckConstraint('held_bytes >= 0'),
        CheckConstraint('confirmed_bytes >= 0 AND confirmed_bytes <= byte_upper_bound'),
        CheckConstraint('octet_length(receipt_digest) = 32'),
        UniqueConstraint('provider', 'namespace', 'prefix'),
        UniqueConstraint('app_id', 'version_id', 'id'),
        ForeignKeyConstraint(['app_id'], [APPLICATION_ID], ondelete='RESTRICT'),
        ForeignKeyConstraint(
            ['app_id', 'version_id'],
            [VERSION_APP_ID, VERSION_ID],
            ondelete='RESTRICT',
        ),
        CheckConstraint("kind <> 'upload' OR upload_generation IS NOT NULL"),
        CheckConstraint("state <> 'stored' OR receipt_digest IS NOT NULL"),
        CheckConstraint(
            "state <> 'deleted' OR (held_bytes = 0 AND confirmed_bytes = 0 AND writer_quiesce"
            'd_at IS NOT NULL\n        AND absence_verified_at IS NOT NULL AND absence_verifie'
            'd_at >= writer_quiesced_at)'
        ),
        Index(
            'hosted_application_attempt_cleanup',
            'state',
            'write_deadline',
            'id',
            postgresql_where=text("state <> 'deleted'"),
        ),
        Index('hosted_application_attempt_owner', 'quota_principal_id', 'app_id', 'version_id'),
        Index('hosted_application_attempt_version', 'version_id'),
    )

    id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    quota_principal_id: str = Field(sa_column=Column(Text, nullable=False))
    app_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False))
    version_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False))
    kind: str = Field(sa_column=Column(Text, nullable=False))
    provider: str = Field(sa_column=Column(Text, nullable=False))
    namespace: str = Field(sa_column=Column(Text, nullable=False))
    prefix: str = Field(sa_column=Column(Text, nullable=False))
    writer_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False))
    writer_fence: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False))
    upload_generation: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    state: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("'planned'")))
    lease_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    write_deadline: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    byte_upper_bound: int = Field(sa_column=Column(BigInteger, nullable=False))
    object_limit: int = Field(sa_column=Column(Integer, nullable=False))
    held_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    confirmed_bytes: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('0')))
    receipt_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    writer_quiesced_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    absence_verified_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class HostedApplicationJobs(SQLModel, table=True):
    __tablename__ = 'hosted_application_jobs'
    __table_args__ = (
        CheckConstraint("kind IN ('validate','purge','retention','cleanup_upload')"),
        CheckConstraint("state IN ('ready','running','passed','completed','rejected','failed','canceled')"),
        CheckConstraint('octet_length(source_digest) = 32'),
        CheckConstraint('octet_length(configuration_digest) = 32'),
        CheckConstraint('octet_length(policy_definition_digest) = 32'),
        CheckConstraint('octet_length(manifest_digest) = 32'),
        CheckConstraint('octet_length(report_digest) = 32'),
        CheckConstraint(POSITIVE_REVISION),
        UniqueConstraint('app_id', 'version_id', 'id'),
        ForeignKeyConstraint(['app_id'], [APPLICATION_ID], ondelete='RESTRICT'),
        ForeignKeyConstraint(
            ['app_id', 'version_id'],
            [VERSION_APP_ID, VERSION_ID],
            ondelete='RESTRICT',
        ),
        ForeignKeyConstraint(
            ['app_id', 'version_id', 'output_attempt_id'],
            [
                ATTEMPT_APP_ID,
                ATTEMPT_VERSION_ID,
                ATTEMPT_ID,
            ],
            ondelete='RESTRICT',
        ),
        ForeignKeyConstraint(
            ['app_id', 'version_id', 'previous_job_id'],
            [
                'hosted_application_jobs.app_id',
                'hosted_application_jobs.version_id',
                'hosted_application_jobs.id',
            ],
            name='job_previous_job',
            deferrable=True,
            initially="IMMEDIATE",
            use_alter=True,
        ),
        CheckConstraint("kind NOT IN ('validate','cleanup_upload') OR version_id IS NOT NULL"),
        CheckConstraint("state <> 'running' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)"),
        CheckConstraint(
            "state <> 'passed' OR (kind = 'validate' AND output_attempt_id IS NOT NULL AND configuration_revision "
            "IS NOT NULL AND source_digest IS NOT NULL AND configuration_digest IS NOT NULL AND policy_version IS NOT "
            "NULL AND policy_definition_digest IS NOT NULL AND manifest_digest IS NOT NULL AND scanner_version IS NOT "
            "NULL AND scanner_signature_version IS NOT NULL AND report_key IS NOT NULL AND report_digest IS NOT NULL "
            "AND completed_at IS NOT NULL)"
        ),
        CheckConstraint(
            "state NOT IN ('passed','completed','rejected','failed','canceled') OR completed_at IS NOT NULL"
        ),
        CheckConstraint("jsonb_typeof(safe_details) = 'object' AND octet_length(safe_details::text) <= 16384"),
        Index('hosted_application_jobs_ready', 'available_at', 'id', postgresql_where=text("state = 'ready'")),
        Index('hosted_application_jobs_leased', 'lease_until', postgresql_where=text("state = 'running'")),
        Index('hosted_application_jobs_history', 'app_id', 'version_id', text('created_at DESC'), text(ID_DESC)),
        Index(
            'hosted_application_job_version_active',
            'version_id',
            'kind',
            unique=True,
            postgresql_where=text("version_id IS NOT NULL AND state IN ('ready','running')"),
        ),
        Index(
            'hosted_application_job_purge_active',
            'app_id',
            unique=True,
            postgresql_where=text("kind = 'purge' AND state IN ('ready','running')"),
        ),
        Index('hosted_application_jobs_failed', 'available_at', postgresql_where=text("state = 'failed'")),
        Index('hosted_application_jobs_version', 'version_id'),
    )

    id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    app_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False))
    version_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    kind: str = Field(sa_column=Column(Text, nullable=False))
    state: str = Field(default=None, sa_column=Column(Text, nullable=False, server_default=text("'ready'")))
    phase: str = Field(default=None, sa_column=Column(String(80), nullable=False, server_default=text("'queued'")))
    output_attempt_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    previous_job_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    configuration_revision: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    source_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    configuration_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    policy_version: str | None = Field(default=None, sa_column=Column(String(80), nullable=True))
    policy_definition_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    manifest_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    scanner_version: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    scanner_signature_version: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    report_key: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    report_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    lease_token: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    lease_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    deadline_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    available_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    safe_details: Any = Field(default=None, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'")))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class HostedApplicationOperations(SQLModel, table=True):
    __tablename__ = 'hosted_application_operations'
    __table_args__ = (
        CheckConstraint("actor_kind IN ('user','administrator','system')"),
        CheckConstraint('octet_length(key_digest) = 32'),
        CheckConstraint('octet_length(request_digest) = 32'),
        CheckConstraint("state IN ('pending','completed')"),
        CheckConstraint('response_status BETWEEN 200 AND 499'),
        ForeignKeyConstraint(['app_id'], [APPLICATION_ID], ondelete='RESTRICT'),
        CheckConstraint("actor_kind = 'system' OR actor_id IS NOT NULL"),
        CheckConstraint(
            'key_digest IS NULL OR (actor_id IS NOT NULL AND request_digest IS NOT NULL AND r'
            'eplay_expires_at IS NOT NULL)'
        ),
        CheckConstraint("state <> 'completed' OR completed_at IS NOT NULL"),
        CheckConstraint('replay_body IS NULL OR octet_length(replay_body::text) <= 65536'),
        CheckConstraint("jsonb_typeof(audit_details) = 'object' AND octet_length(audit_details::text) <= 16384"),
        Index(
            'hosted_application_operation_key',
            'actor_id',
            'action',
            'target_id',
            'key_digest',
            unique=True,
            postgresql_where=text('key_digest IS NOT NULL'),
        ),
        Index(
            'hosted_application_operation_audit',
            'app_id',
            text('created_at DESC'),
            text(ID_DESC),
            postgresql_where=text("state = 'completed'"),
        ),
        Index(
            'hosted_application_operation_expiry', 'replay_expires_at', postgresql_where=text('replay_body IS NOT NULL')
        ),
        Index('hosted_application_operation_created', 'created_at'),
    )

    id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    actor_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    actor_kind: str = Field(sa_column=Column(Text, nullable=False))
    app_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    target_id: str = Field(sa_column=Column(Text, nullable=False))
    action: str = Field(sa_column=Column(String(160), nullable=False))
    key_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    request_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    state: str = Field(sa_column=Column(Text, nullable=False))
    response_status: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    # Python None must persist as SQL NULL: the scrub and expiry predicates test `replay_body IS NOT NULL`.
    replay_body: Any | None = Field(default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True))
    replay_expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    audit_details: Any = Field(default=None, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'")))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class HostedApplicationViewSessions(SQLModel, table=True):
    __tablename__ = 'hosted_application_view_sessions'
    __table_args__ = (
        CheckConstraint("kind IN ('auth_context','ticket','view_session')"),
        CheckConstraint("parent_kind = 'auth_context'"),
        CheckConstraint('octet_length(token_digest) = 32'),
        CheckConstraint('octet_length(auth_binding_digest) = 32'),
        CheckConstraint("purpose IN ('owner_preview','published_view')"),
        CheckConstraint(POSITIVE_REVISION),
        UniqueConstraint('principal_id', 'id', 'kind'),
        ForeignKeyConstraint(['principal_id'], ['hosted_application_user_state.principal_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(
            ['principal_id', 'parent_id', 'parent_kind'],
            [
                'hosted_application_view_sessions.principal_id',
                'hosted_application_view_sessions.id',
                'hosted_application_view_sessions.kind',
            ],
            ondelete='RESTRICT',
        ),
        ForeignKeyConstraint(
            ['app_id', 'version_id'],
            [VERSION_APP_ID, VERSION_ID],
            ondelete='RESTRICT',
        ),
        CheckConstraint('expires_at > created_at'),
        CheckConstraint(
            "(kind = 'auth_context' AND parent_id IS NULL AND app_id IS NULL AND version_id I"
            'S NULL AND token_digest IS NULL\n        AND auth_binding_digest IS NOT NULL AND '
            'credential_issued_at IS NOT NULL AND binding_valid_until IS NOT NULL AND binding'
            "_valid_until >= expires_at)\n        OR (kind <> 'auth_context' AND parent_id IS "
            'NOT NULL AND app_id IS NOT NULL AND version_id IS NOT NULL\n        AND token_dig'
            'est IS NOT NULL AND purpose IS NOT NULL AND access_revision IS NOT NULL)'
        ),
        CheckConstraint(
            "kind <> 'ticket' OR (destination_host IS NOT NULL AND target_path IS NOT NULL AN"
            "D expires_at <= created_at + interval '30 seconds')"
        ),
        CheckConstraint("kind <> 'view_session' OR expires_at <= created_at + interval '15 minutes'"),
        Index(
            'hosted_application_auth_binding',
            'principal_id',
            'auth_binding_digest',
            unique=True,
            postgresql_where=text("kind = 'auth_context'"),
        ),
        Index('hosted_application_credential_expiry', 'expires_at'),
        Index('hosted_application_credential_app', 'app_id', 'version_id'),
        Index('hosted_application_credential_parent', 'parent_id'),
        Index(
            'hosted_application_credential_binding_expiry',
            'binding_valid_until',
            postgresql_where=text("kind = 'auth_context'"),
        ),
    )

    id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    kind: str = Field(sa_column=Column(Text, nullable=False))
    principal_id: str = Field(sa_column=Column(Text, nullable=False))
    parent_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    parent_kind: str = Field(
        default=None, sa_column=Column(Text, nullable=False, server_default=text("'auth_context'"))
    )
    app_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    version_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    token_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True, unique=True))
    auth_binding_digest: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    binding_valid_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    credential_issued_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    purpose: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    destination_host: str | None = Field(default=None, sa_column=Column(String(253), nullable=True))
    target_path: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    access_revision: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    revision: int = Field(default=None, sa_column=Column(BigInteger, nullable=False, server_default=text('1')))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


HOSTED_APP_MODELS = (
    HostedApplicationUserState,
    HostedApplications,
    HostedApplicationVersions,
    HostedApplicationStorageAttempts,
    HostedApplicationJobs,
    HostedApplicationOperations,
    HostedApplicationViewSessions,
)
