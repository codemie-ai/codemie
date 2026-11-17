# Spec: Workflow icon upload (EPMCDME-15605)

## Problem

Workflows accept only `icon_url`, a free-form string. Users must host an image elsewhere and keep that link alive. Assistants already let users upload an icon. Workflows should get the same upload option, while keeping the URL option and YAML compatibility.

## Approach

Reuse the existing file storage (`FileRepository`, as used by `/v1/files/`) for the bytes. Keep the stored result in the existing `icon_url` field: the upload produces a file URL, and saving the workflow writes that URL into `icon_url`. No new column and no schema change.

Two things differ from the generic file endpoint:

1. **Validation.** A workflow icon upload endpoint enforces an allowlist of image types and a size cap. The generic `/v1/files/` endpoint has only the global 100 MB cap and no type rule. Assistant icon behaviour is unchanged.
2. **Read access.** Workflow icons are stored under a workflow-owned namespace. A read is allowed when the requested icon URL is the `icon_url` of a workflow the requesting user can view. Everything else keeps the current owner-only rule.

Ruled out:
- An inline data URL in `icon_url`. It bloats the workflows table and the YAML history.
- Owner-scoped storage. Other viewers would get 404 on the icon.
- A new `icon_file` column. It would duplicate `icon_url` and require a migration-like display change in every consumer.
- Deleting replaced or removed files. The owner has chosen to leave them orphaned.

## Assumed icon limits (to be confirmed)

The `codemie-ui` values for assistant icons could not be read from this repository. The following are assumptions, not verified values:

- Allowed types: PNG, JPEG, WebP, GIF (`image/png`, `image/jpeg`, `image/webp`, `image/gif`).
- Maximum size: 2 MB.

The reviewer must confirm these against `codemie-ui` before merge. Any mismatch is a one-constant change.

## Requirements

**Upload (backend)**
- New authenticated endpoint on the workflow router that accepts one image file and returns a file URL. It rejects files whose type is not in the allowlist, or whose size exceeds the cap, with an `ExtendedHTTPException` 400 whose message names the reason (type or size).
- Type is checked from the declared content type and the file extension, and the bytes are sniffed to match the declared type.
- The file is written to the workflow icon namespace through `FileRepository`, with a unique name.

**Save (backend)**
- `CreateWorkflowRequest` and `UpdateWorkflowRequest` keep `icon_url` as-is. The uploaded URL arrives there.
- When `icon_url` points into the workflow icon namespace, the save must confirm the referenced file exists in that namespace. Arbitrary private file URLs must not be accepted as icons, so a user cannot expose their own files through a workflow they can edit.
- Non-namespace URLs are accepted unchanged, as today.
- Only users with write access to the workflow can change `icon_url`. This reuses the existing router check at `workflow.py:662`; no new authorization path is added.

**Read (backend)**
- The file-download authorization in `can_download` gains one rule: a workflow icon-namespace file is readable by any user who can view a workflow whose `icon_url` equals that file's URL. Owner-only behaviour for all other files is unchanged.

**Frontend (codemie-ui, outside this repo)**
- Create and edit form: an upload control next to the existing URL input. The control validates type and size client-side with the same limits, shows a preview, and offers replace and remove. Choosing a file uploads it and sets `icon_url` to the returned URL on save. Removing clears `icon_url`.
- Display locations (workflow list, workflow page, conversations, user preferences) already read `icon_url`. They need no change beyond what is already rendered.

**YAML**
- `icon_url` in workflow YAML keeps working as before. No new YAML key is introduced.

**Existing data**
- Workflows with an existing `icon_url` are untouched. No migration.

## Acceptance criteria

1. A user can upload an image as a workflow icon during create and edit, and sees a preview before saving.
2. Allowed types and the size cap match the assistant icon values. Invalid files are rejected with a message that names the reason.
3. After save, the uploaded icon displays wherever the workflow icon appears: list, page, conversations, user preferences.
4. A user can replace or remove an uploaded icon.
5. A user can still set a workflow icon by URL.
6. Existing `icon_url` workflows and YAML `icon_url` keep working with no migration.
7. Only users who can edit a workflow can change its icon.
8. Assistant icon upload and read behaviour is unchanged.
9. Icon cropping and editing are not part of this change.
10. A non-viewer cannot read a workflow icon file, and a file outside the icon namespace cannot be set as a workflow icon.

## Non-goals

- Icon cropping, editing, or resizing tools.
- Migrating existing `icon_url` values or re-hosting external URLs.
- Changing assistant icon upload, storage, or access.
- Deleting old or orphaned icon files on replace or remove.
- SVG or other image types beyond the allowlist.
- Multiple icons per workflow, or any new YAML key.
- Changing the generic `/v1/files/` endpoint behaviour.

## Test expectations

- Router: upload accepts an allowed type, rejects a disallowed type and an oversize file with the right message, and requires authentication.
- Save: a namespace `icon_url` that does not exist is rejected; a non-namespace URL is accepted unchanged; update without write access is denied.
- Read: viewer of the workflow can read its icon; a user with no view access gets 404; an owner-only file is still owner-only.
- Existing workflow tests (`tests/codemie/rest_api/routers/test_workflow.py`, `tests/codemie/service/test_workflow_service.py`) remain green.

## Open risks

- The icon limits (types and 2 MB cap) are assumptions. Reviewer must confirm against `codemie-ui`.
- The `can_download` rule requires a lookup of workflows by `icon_url`. This needs an indexed column or an equivalent query. Performance to be checked against the workflows table.
- Orphaned files accumulate on replace and remove. Accepted by the owner.
- Upload before save creates a file that may never be referenced if the user abandons the form. Accepted.
- Frontend display and form behaviour cannot be verified from this repository.
