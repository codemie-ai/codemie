# FE API Contract — EPMCDME-14336: Optional Project Description

**Backend ticket:** [EPMCDME-14336](https://jiraeu.epam.com/browse/EPMCDME-14336)
**Backend branch:** `EPMCDME-14336_optional-project-description`
**Status:** proposed — pending backend implementation
**Applies to:** `POST /v1/projects`, `PATCH /v1/projects/{projectName}` and their response models

---

## Summary

The project `description` field becomes optional in both create and edit flows. It can also be cleared after being set. The 500-character upper bound is unchanged.

---

## POST `/v1/projects` — Create

### Request

All fields shown; only `name` is required.

```json
{
  "name": "my-project",
  "display_name": "My Project",
  "description": "Optional text",
  "cost_center_id": null
}
```

Acceptable variants for `description` — **all produce `null` server-side**:

- Field omitted
- `"description": null`
- `"description": ""`
- `"description": "   "` (whitespace-only)

### Rejected

- `description` longer than **500 chars** → `400` / `422`.

### Response schema change

`description` is now `string | null` (was `string`).

```json
{
  "id": "…",
  "name": "my-project",
  "display_name": "My Project",
  "description": null,
  "cost_center_id": null,
  "…": "…"
}
```

---

## PATCH `/v1/projects/{projectName}` — Update

### Update behavior for `description`

| FE sends | Effect |
|---|---|
| `description` omitted, `clear_description` omitted/false | Description unchanged |
| `"description": "New text"` | Description set to `"New text"` |
| `"description": ""` or `"description": "   "` | Description cleared (stored as `null`) |
| `"clear_description": true` (no `description`) | Description cleared (stored as `null`) |
| `"description": "text"` + `"clear_description": true` | **422** (mutually exclusive) |
| `"description": null` (explicit JSON null, no clear flag) | Treated as omitted → description unchanged. Use `clear_description: true` (or empty string) to clear explicitly. |

Other update fields are unchanged.

### Response

`description` returned as `string | null`. The previous coercion to `""` when the value was unset is removed.

---

## Recommended FE usage

- **Clearing a description**: prefer `clear_description: true` when the user explicitly wipes the field — intent is unambiguous in logs and requests.
- Empty string also works if the form already binds `""` to an empty input.
- Do **not** rely on explicit JSON `null` for `description` on PATCH — Pydantic treats it as "field omitted" and it will not clear.
- Add **null handling** wherever the FE reads `project.description` (previously guaranteed non-null on create response).
- **Details page**: render a "no value" placeholder when `description` is `null` (ticket AC).
- **Edit modal (`ProjectModal`)**: remove the "required" constraint from Description in both create and edit modes.

---

## OpenAPI

FastAPI regenerates `/openapi.json` from the Pydantic models at startup — there is no committed schema file. After backend merge, the spec will show:

- `POST /v1/projects` request `description`: `anyOf: [{type: "string"}, {type: "null"}]`
- Both create and update responses' `description`: `anyOf: [{type: "string"}, {type: "null"}]`
- `PATCH /v1/projects/{name}` request: new `clear_description: boolean` (default `false`)

Pull the updated spec from a running backend (`GET /openapi.json`) to refresh generated SDK clients.

---

## Backwards compatibility

- **Non-breaking for FE clients that always send a non-empty `description` on create** — those requests continue to succeed unchanged.
- **Breaking for readers that assume `description` is always a string** — the response type widens to `string | null`.
- **New capability**: cleaning up a previously-set description now works; previously it was impossible.

---

## Coordination checklist for FE

- [ ] Remove `required` from the Description field in `ProjectModal` (create + edit).
- [ ] Wire the Edit modal's Description input to send `clear_description: true` (or `""`) when the user empties it.
- [ ] Render a "no value" placeholder on the project details page when `description` is `null`.
- [ ] Ensure all reads of `project.description` handle `null`.
- [ ] Regenerate OpenAPI-derived types/clients after backend merge.

---

*Source of truth for backend behavior: `docs/superpowers/tasks/2026-08-26-optional-project-description/spec.md`.*
