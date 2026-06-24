# Spec: AgentCore Vendor Installations

**Branch:** `EPMCDME-12240_agentcore`  
**Date:** 2026-05-22

---

## Goal

Extend the vendor layer with a generic "installation" concept — a Postgres-backed record that tracks whether a vendor entity (initially: AgentCore runtime endpoints) has been installed as a CodeMie resource. The installation record caches AWS metadata so the frontend can render full endpoint details without a live AWS call.

---

## Scope

- New Postgres table: `vendor_entity_installation`
- New repository: `VendorInstallationRepository`
- New service methods on `BedrockAgentCoreRuntimeService` for installation CRUD
- New routes in `vendor.py` under `/installations` sub-resource (agentcore-guarded)
- Rename `list_importable_entities_for_main_entity` → `list_installable_entities` on `BedrockAgentCoreRuntimeService`
- Existing `GET /v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints` is **unchanged**

Out of scope: assistant creation on install, UI changes, other vendor entity types.

---

## Data Model

### `vendor_entity_installation` (Postgres / SQLModel)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Stable CodeMie identifier |
| `setting_id` | VARCHAR | AWS integration UUID |
| `vendor` | VARCHAR | e.g. `"aws"` |
| `entity_type` | VARCHAR | e.g. `"agentcore-runtimes"` |
| `entity_id` | VARCHAR | Runtime ID |
| `sub_entity_id` | VARCHAR | Endpoint name (e.g. `"DEFAULT"`) |
| `install_state` | VARCHAR | `not_installed \| installed \| version_drift \| deleted_on_aws` |
| `installed_resource_id` | UUID? | CodeMie Assistant UUID when installed |
| `installed_version` | VARCHAR? | AWS `liveVersion` at install time |
| `metadata` | JSONB | Cached AWS endpoint fields (name, status, liveVersion, targetVersion, ARNs, timestamps, etc.) |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

**Unique constraint:** `(setting_id, vendor, entity_type, entity_id, sub_entity_id)`

---

## API

All routes live in `vendor.py` under the existing `APIRouter`. Routes are guarded to `entity == Entities.AWS_AGENTCORE_RUNTIMES`.

### GET `/v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints/installations`

**Behaviour:**
1. Fetch the AWS endpoint list for `runtimeId` using `setting_id` from query params and the authenticated user's credentials.
2. Upsert a `vendor_entity_installation` row for each AWS endpoint not yet in Postgres — `install_state: not_installed`, `metadata` populated from AWS response.
3. Return all Postgres rows for `(setting_id, vendor, entity_type, runtimeId)`.

**Query params:** `setting_id` (required)

**Response:**
```json
[
  {
    "id": "<uuid>",
    "sub_entity_id": "DEFAULT",
    "install_state": "not_installed",
    "installed_resource_id": null,
    "installed_version": null,
    "metadata": {
      "name": "DEFAULT",
      "status": "PREPARED",
      "liveVersion": "2",
      "targetVersion": null,
      "agentRuntimeEndpointArn": "arn:...",
      "agentRuntimeArn": "arn:...",
      "createdAt": "...",
      "updatedAt": "..."
    }
  }
]
```

---

### POST `/v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints/installations`

Creates an installation record for a specific endpoint.

**Body:**
```json
{ "setting_id": "<uuid>", "endpoint_name": "DEFAULT" }
```

**Behaviour:** Fetch AWS endpoint detail → create row with `metadata` populated, `install_state: installed`.

---

### PUT `/v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints/installations/{id}`

Re-syncs an existing installation record with AWS.

**Query params:** `setting_id` (required)

**Behaviour:** Re-fetch AWS endpoint detail → refresh `metadata` → recompute `install_state` (compare `liveVersion` vs `installed_version`).

---

### DELETE `/v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints/installations/{id}`

Removes the installation record.

**Query params:** `setting_id` (required)

---

## Service Layer

### `VendorInstallationRepository` (`src/codemie/repository/vendor_installation.py`)

- `upsert(record)` — insert or update on unique constraint
- `get_by_entity(setting_id, vendor, entity_type, entity_id)` → list
- `get_by_id(id)` → single record
- `delete(id)`

### `BedrockAgentCoreRuntimeService` additions

- `list_installable_entities(...)` — renamed from `list_importable_entities_for_main_entity`, behaviour unchanged
- `list_installations(user, setting_id, runtime_id, page, per_page)` — GET handler: fetch AWS, upsert missing rows, return Postgres rows
- `create_installation(user, setting_id, runtime_id, endpoint_name)` — POST handler
- `update_installation(user, setting_id, runtime_id, installation_id)` — PUT handler
- `delete_installation(user, setting_id, installation_id)` — DELETE handler

### `BaseBedrockService` (no changes yet)

Installation methods are agentcore-specific for now. Generalisation to `BaseBedrockService` is deferred until a second entity type needs it.

---

## `install_state` Logic

| State | Condition |
|---|---|
| `not_installed` | Row exists, `installed_resource_id` is null |
| `installed` | `installed_resource_id` set, `installed_version == metadata.liveVersion` |
| `version_drift` | `installed_resource_id` set, `installed_version != metadata.liveVersion` |
| `deleted_on_aws` | Row exists but endpoint no longer appears in AWS list |

---

## Error Handling

- User lacks access to `setting_id` → 403 via existing `get_setting_for_user`
- `setting_id` not found → 404
- AWS call fails → 502 via `aws_service_exception_handler`
- Installation record not found by `id` → 404

---

## Migration

New Alembic migration: `add_vendor_entity_installation_table`.
