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


"""Add hosted applications: user-uploaded static web apps with versions, sharing and covers.

Revision ID: e3b7c1a95d42
Revises: 0ba28b31fa53

Storage attempts accept the filesystem, aws (S3) and gcp (GCS) providers. Every table lives in the core schema next to the platform tables. The validation
policy versions under which app versions may be served are a code constant (codemie.core.hosted_applications.policy),
not database state.
"""

from alembic import op

HOSTED_APPLICATION_DDL = r"""
CREATE TABLE hosted_application_user_state (
    principal_id text PRIMARY KEY,
    byte_limit bigint NOT NULL DEFAULT 2147483648 CHECK (byte_limit >= 0),
    stored_bytes bigint NOT NULL DEFAULT 0 CHECK (stored_bytes >= 0),
    reserved_bytes bigint NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
    auth_revoked_before timestamptz,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL,
    CHECK (char_length(principal_id) BETWEEN 1 AND 255)
);

CREATE TABLE hosted_applications (
    id uuid PRIMARY KEY,
    project text NOT NULL,
    created_by jsonb NOT NULL,
    shared boolean NOT NULL DEFAULT false,
    is_global boolean NOT NULL DEFAULT false,
    name varchar(120),
    description varchar(2000),
    search_text text NOT NULL DEFAULT '',
    artifact_kind text CHECK (artifact_kind IN ('html', 'zip')),
    selected_version_id uuid,
    active_version_id uuid,
    security_state text NOT NULL DEFAULT 'clear' CHECK (security_state IN ('clear', 'suspended')),
    lifecycle_state text NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active', 'purging', 'purged')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    access_revision bigint NOT NULL DEFAULT 1 CHECK (access_revision > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz,
    purged_at timestamptz,
    CHECK (lifecycle_state = 'purged' OR (name IS NOT NULL AND char_length(name) > 0 AND description IS NOT NULL AND char_length(description) > 0)),
    CHECK ((lifecycle_state = 'active') = (deleted_at IS NULL)),
    CHECK (lifecycle_state <> 'purged' OR (purged_at IS NOT NULL AND name IS NULL AND description IS NULL
        AND selected_version_id IS NULL AND active_version_id IS NULL AND search_text = ''
        AND created_by->>'username' = '' AND created_by->>'name' = ''))
);
CREATE INDEX hosted_applications_project ON hosted_applications (project);
CREATE INDEX hosted_applications_created_by_id ON hosted_applications ((created_by->>'id'), updated_at DESC, id DESC);
CREATE INDEX hosted_applications_purged ON hosted_applications (purged_at) WHERE lifecycle_state = 'purged';
CREATE INDEX hosted_applications_name ON hosted_applications (name, id);

CREATE TABLE hosted_application_versions (
    id uuid PRIMARY KEY,
    app_id uuid NOT NULL,
    sequence integer NOT NULL CHECK (sequence > 0),
    original_filename varchar(255) NOT NULL,
    artifact_kind text NOT NULL CHECK (artifact_kind IN ('html', 'zip')),
    expected_bytes bigint NOT NULL CHECK (expected_bytes > 0),
    expected_digest bytea CHECK (octet_length(expected_digest) = 32),
    upload_id uuid NOT NULL UNIQUE,
    upload_generation bigint NOT NULL DEFAULT 1 CHECK (upload_generation > 0),
    upload_revision bigint NOT NULL DEFAULT 1 CHECK (upload_revision > 0),
    upload_expires_at timestamptz NOT NULL,
    upload_state text NOT NULL DEFAULT 'created' CHECK (upload_state IN ('created', 'receiving', 'stored', 'completed', 'canceled', 'expired')),
    source_attempt_id uuid,
    requested_configuration jsonb NOT NULL DEFAULT '{}',
    candidate_manifest_digest bytea CHECK (octet_length(candidate_manifest_digest) = 32),
    configuration_revision bigint NOT NULL DEFAULT 1 CHECK (configuration_revision > 0),
    validation_state text NOT NULL DEFAULT 'uploading' CHECK (validation_state IN ('uploading','queued','validating','awaiting_configuration','accepted','rejected','failed','canceled')),
    security_state text NOT NULL DEFAULT 'clear' CHECK (security_state IN ('clear', 'blocked')),
    accepted_attempt_id uuid,
    accepted_job_id uuid,
    artifact_digest bytea CHECK (octet_length(artifact_digest) = 32),
    manifest_key text,
    manifest_digest bytea CHECK (octet_length(manifest_digest) = 32),
    site_root text,
    entrypoint_path text,
    routing_mode text CHECK (routing_mode IN ('static','spa_history')),
    expanded_bytes bigint NOT NULL DEFAULT 0 CHECK (expanded_bytes BETWEEN 0 AND 524288000),
    file_count integer NOT NULL DEFAULT 0 CHECK (file_count BETWEEN 0 AND 5000),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    accepted_at timestamptz,
    UNIQUE (app_id, id),
    UNIQUE (app_id, sequence),
    FOREIGN KEY (app_id) REFERENCES hosted_applications (id) ON DELETE RESTRICT,
    CHECK (jsonb_typeof(requested_configuration) = 'object' AND octet_length(requested_configuration::text) <= 4096),
    CHECK (validation_state <> 'accepted' OR (source_attempt_id IS NOT NULL AND accepted_attempt_id IS NOT NULL AND accepted_job_id IS NOT NULL
        AND artifact_digest IS NOT NULL AND manifest_key IS NOT NULL AND manifest_digest IS NOT NULL
        AND site_root IS NOT NULL AND entrypoint_path IS NOT NULL AND routing_mode IS NOT NULL AND file_count > 0 AND accepted_at IS NOT NULL))
);

CREATE TABLE hosted_application_storage_attempts (
    id uuid PRIMARY KEY,
    quota_principal_id text NOT NULL,
    app_id uuid NOT NULL,
    version_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('upload','validation')),
    provider text NOT NULL CHECK (provider IN ('filesystem','aws','gcp')),
    namespace text NOT NULL,
    prefix text NOT NULL,
    writer_id uuid NOT NULL,
    writer_fence uuid NOT NULL,
    upload_generation bigint CHECK (upload_generation > 0),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    state text NOT NULL DEFAULT 'planned' CHECK (state IN ('planned','writing','stored','delete_requested','uncertain','deleted')),
    lease_until timestamptz,
    write_deadline timestamptz NOT NULL,
    byte_upper_bound bigint NOT NULL CHECK (byte_upper_bound >= 0),
    object_limit integer NOT NULL CHECK (object_limit BETWEEN 1 AND 5002),
    held_bytes bigint NOT NULL CHECK (held_bytes >= 0),
    confirmed_bytes bigint NOT NULL DEFAULT 0 CHECK (confirmed_bytes >= 0 AND confirmed_bytes <= byte_upper_bound),
    receipt_digest bytea CHECK (octet_length(receipt_digest) = 32),
    writer_quiesced_at timestamptz,
    absence_verified_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (provider, namespace, prefix),
    UNIQUE (app_id, version_id, id),
    FOREIGN KEY (app_id) REFERENCES hosted_applications (id) ON DELETE RESTRICT,
    FOREIGN KEY (app_id, version_id) REFERENCES hosted_application_versions (app_id, id) ON DELETE RESTRICT,
    CHECK (kind <> 'upload' OR upload_generation IS NOT NULL),
    CHECK (state <> 'stored' OR receipt_digest IS NOT NULL),
    CHECK (state <> 'deleted' OR (held_bytes = 0 AND confirmed_bytes = 0 AND writer_quiesced_at IS NOT NULL
        AND absence_verified_at IS NOT NULL AND absence_verified_at >= writer_quiesced_at))
);
CREATE INDEX hosted_application_attempt_cleanup ON hosted_application_storage_attempts (state, write_deadline, id) WHERE state <> 'deleted';
CREATE INDEX hosted_application_attempt_owner ON hosted_application_storage_attempts (quota_principal_id, app_id, version_id);

CREATE TABLE hosted_application_jobs (
    id uuid PRIMARY KEY,
    app_id uuid NOT NULL,
    version_id uuid,
    kind text NOT NULL CHECK (kind IN ('validate','purge','retention','cleanup_upload')),
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready','running','passed','completed','rejected','failed','canceled')),
    phase varchar(80) NOT NULL DEFAULT 'queued',
    output_attempt_id uuid,
    previous_job_id uuid,
    configuration_revision bigint,
    source_digest bytea CHECK (octet_length(source_digest) = 32),
    configuration_digest bytea CHECK (octet_length(configuration_digest) = 32),
    policy_version varchar(80),
    policy_definition_digest bytea CHECK (octet_length(policy_definition_digest) = 32),
    manifest_digest bytea CHECK (octet_length(manifest_digest) = 32),
    scanner_version varchar(120),
    scanner_signature_version varchar(120),
    report_key text,
    report_digest bytea CHECK (octet_length(report_digest) = 32),
    lease_token uuid,
    lease_until timestamptz,
    deadline_at timestamptz,
    available_at timestamptz NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    safe_details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (app_id, version_id, id),
    FOREIGN KEY (app_id) REFERENCES hosted_applications (id) ON DELETE RESTRICT,
    FOREIGN KEY (app_id, version_id) REFERENCES hosted_application_versions (app_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (app_id, version_id, output_attempt_id) REFERENCES hosted_application_storage_attempts (app_id, version_id, id) ON DELETE RESTRICT,
    CONSTRAINT job_previous_job FOREIGN KEY (app_id, version_id, previous_job_id) REFERENCES hosted_application_jobs (app_id, version_id, id) DEFERRABLE INITIALLY IMMEDIATE,
    CHECK (kind NOT IN ('validate','cleanup_upload') OR version_id IS NOT NULL),
    CHECK (state <> 'running' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK (state <> 'passed' OR (kind = 'validate' AND output_attempt_id IS NOT NULL AND configuration_revision IS NOT NULL
        AND source_digest IS NOT NULL AND configuration_digest IS NOT NULL AND policy_version IS NOT NULL
        AND policy_definition_digest IS NOT NULL AND manifest_digest IS NOT NULL
        AND scanner_version IS NOT NULL AND scanner_signature_version IS NOT NULL AND report_key IS NOT NULL AND report_digest IS NOT NULL
        AND completed_at IS NOT NULL)),
    CHECK (state NOT IN ('passed','completed','rejected','failed','canceled') OR completed_at IS NOT NULL),
    CHECK (jsonb_typeof(safe_details) = 'object' AND octet_length(safe_details::text) <= 16384)
);
CREATE INDEX hosted_application_jobs_ready ON hosted_application_jobs (available_at, id) WHERE state = 'ready';
CREATE INDEX hosted_application_jobs_leased ON hosted_application_jobs (lease_until) WHERE state = 'running';
CREATE INDEX hosted_application_jobs_history ON hosted_application_jobs (app_id, version_id, created_at DESC, id DESC);
CREATE UNIQUE INDEX hosted_application_job_version_active ON hosted_application_jobs (version_id, kind) WHERE version_id IS NOT NULL AND state IN ('ready','running');
CREATE UNIQUE INDEX hosted_application_job_purge_active ON hosted_application_jobs (app_id) WHERE kind = 'purge' AND state IN ('ready','running');
ALTER TABLE hosted_application_versions ADD CONSTRAINT version_source_attempt FOREIGN KEY (app_id, id, source_attempt_id)
    REFERENCES hosted_application_storage_attempts (app_id, version_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE hosted_application_versions ADD CONSTRAINT version_accepted_attempt FOREIGN KEY (app_id, id, accepted_attempt_id)
    REFERENCES hosted_application_storage_attempts (app_id, version_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE hosted_application_versions ADD CONSTRAINT version_accepted_job FOREIGN KEY (app_id, id, accepted_job_id)
    REFERENCES hosted_application_jobs (app_id, version_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE hosted_applications ADD CONSTRAINT app_selected_version FOREIGN KEY (id, selected_version_id)
    REFERENCES hosted_application_versions (app_id, id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE hosted_applications ADD CONSTRAINT app_active_version FOREIGN KEY (id, active_version_id)
    REFERENCES hosted_application_versions (app_id, id) DEFERRABLE INITIALLY IMMEDIATE;

CREATE TABLE hosted_application_operations (
    id uuid PRIMARY KEY,
    actor_id text,
    actor_kind text NOT NULL CHECK (actor_kind IN ('user','administrator','system')),
    app_id uuid,
    target_id text NOT NULL,
    action varchar(160) NOT NULL,
    key_digest bytea CHECK (octet_length(key_digest) = 32),
    request_digest bytea CHECK (octet_length(request_digest) = 32),
    state text NOT NULL CHECK (state IN ('pending','completed')),
    response_status integer CHECK (response_status BETWEEN 200 AND 499),
    replay_body jsonb,
    replay_expires_at timestamptz,
    audit_details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    FOREIGN KEY (app_id) REFERENCES hosted_applications (id) ON DELETE RESTRICT,
    CHECK (actor_kind = 'system' OR actor_id IS NOT NULL),
    CHECK (key_digest IS NULL OR (actor_id IS NOT NULL AND request_digest IS NOT NULL AND replay_expires_at IS NOT NULL)),
    CHECK (state <> 'completed' OR completed_at IS NOT NULL),
    CHECK (replay_body IS NULL OR octet_length(replay_body::text) <= 65536),
    CHECK (jsonb_typeof(audit_details) = 'object' AND octet_length(audit_details::text) <= 16384)
);
CREATE UNIQUE INDEX hosted_application_operation_key ON hosted_application_operations (actor_id, action, target_id, key_digest) WHERE key_digest IS NOT NULL;
CREATE INDEX hosted_application_operation_audit ON hosted_application_operations (app_id, created_at DESC, id DESC) WHERE state = 'completed';
CREATE INDEX hosted_application_operation_expiry ON hosted_application_operations (replay_expires_at) WHERE replay_body IS NOT NULL;

CREATE TABLE hosted_application_view_sessions (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('auth_context','ticket','view_session')),
    principal_id text NOT NULL,
    parent_id uuid,
    parent_kind text NOT NULL DEFAULT 'auth_context' CHECK (parent_kind = 'auth_context'),
    app_id uuid,
    version_id uuid,
    token_digest bytea UNIQUE CHECK (octet_length(token_digest) = 32),
    auth_binding_digest bytea CHECK (octet_length(auth_binding_digest) = 32),
    binding_valid_until timestamptz,
    credential_issued_at timestamptz,
    purpose text CHECK (purpose IN ('owner_preview','published_view')),
    destination_host varchar(253),
    target_path text,
    access_revision bigint,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    revoked_at timestamptz,
    UNIQUE (principal_id, id, kind),
    FOREIGN KEY (principal_id) REFERENCES hosted_application_user_state (principal_id) ON DELETE RESTRICT,
    FOREIGN KEY (principal_id, parent_id, parent_kind)
        REFERENCES hosted_application_view_sessions (principal_id, id, kind) ON DELETE RESTRICT,
    FOREIGN KEY (app_id, version_id) REFERENCES hosted_application_versions (app_id, id) ON DELETE RESTRICT,
    CHECK (expires_at > created_at),
    CHECK ((kind = 'auth_context' AND parent_id IS NULL AND app_id IS NULL AND version_id IS NULL AND token_digest IS NULL
        AND auth_binding_digest IS NOT NULL AND credential_issued_at IS NOT NULL AND binding_valid_until IS NOT NULL AND binding_valid_until >= expires_at)
        OR (kind <> 'auth_context' AND parent_id IS NOT NULL AND app_id IS NOT NULL AND version_id IS NOT NULL
        AND token_digest IS NOT NULL AND purpose IS NOT NULL AND access_revision IS NOT NULL)),
    CHECK (kind <> 'ticket' OR (destination_host IS NOT NULL AND target_path IS NOT NULL AND expires_at <= created_at + interval '30 seconds')),
    CHECK (kind <> 'view_session' OR expires_at <= created_at + interval '15 minutes')
);
CREATE UNIQUE INDEX hosted_application_auth_binding ON hosted_application_view_sessions (principal_id, auth_binding_digest) WHERE kind = 'auth_context';
CREATE INDEX hosted_application_credential_expiry ON hosted_application_view_sessions (expires_at);
CREATE INDEX hosted_application_credential_app ON hosted_application_view_sessions (app_id, version_id);
CREATE INDEX hosted_application_credential_parent ON hosted_application_view_sessions (parent_id);

CREATE TABLE hosted_application_covers (
    app_id uuid PRIMARY KEY,
    media_type text NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp')),
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    byte_size integer NOT NULL CHECK (byte_size BETWEEN 1 AND 1048576),
    width integer NOT NULL CHECK (width BETWEEN 1 AND 2048),
    height integer NOT NULL CHECK (height BETWEEN 1 AND 2048),
    data bytea NOT NULL,
    updated_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    FOREIGN KEY (app_id) REFERENCES hosted_applications (id) ON DELETE CASCADE,
    CHECK (octet_length(data) = byte_size),
    CHECK (width * height <= 4194304)
);

-- Background scans: retries, retention, upload expiry, launch audit pruning and credential expiry.
CREATE INDEX hosted_application_jobs_failed ON hosted_application_jobs (available_at) WHERE state = 'failed';
CREATE INDEX hosted_application_jobs_version ON hosted_application_jobs (version_id);
CREATE INDEX hosted_application_attempt_version ON hosted_application_storage_attempts (version_id);
CREATE INDEX hosted_application_version_upload_expiry ON hosted_application_versions (upload_expires_at) WHERE validation_state = 'uploading';
CREATE INDEX hosted_application_operation_created ON hosted_application_operations (created_at);
CREATE INDEX hosted_application_credential_binding_expiry ON hosted_application_view_sessions (binding_valid_until) WHERE kind = 'auth_context';
"""

TABLES = (
    "hosted_application_covers",
    "hosted_application_view_sessions",
    "hosted_application_operations",
    "hosted_application_jobs",
    "hosted_application_storage_attempts",
    "hosted_application_versions",
    "hosted_applications",
    "hosted_application_user_state",
)

revision = "e3b7c1a95d42"
down_revision = "0ba28b31fa53"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for statement in HOSTED_APPLICATION_DDL.split(";"):
        if statement.strip():
            op.execute(statement)
    op.execute("CREATE INDEX hosted_applications_search ON hosted_applications USING gin (search_text gin_trgm_ops)")


def downgrade():
    op.execute(
        "ALTER TABLE hosted_applications DROP CONSTRAINT app_active_version, DROP CONSTRAINT app_selected_version"
    )
    op.execute(
        "ALTER TABLE hosted_application_versions DROP CONSTRAINT version_source_attempt, "
        "DROP CONSTRAINT version_accepted_attempt, DROP CONSTRAINT version_accepted_job"
    )
    for table in TABLES:
        op.drop_table(table)
