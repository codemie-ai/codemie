# EPMCDME-15631: Cost Center filter for the project list API

Source: MR !4482 description and ticket EPMCDME-15631.

## Acceptance criteria

- AC1: The project list `GET` endpoint (`rest_api/routers/projects.py`) accepts a repeated `cost_center_ids` query parameter.
- AC2: Omitted or empty `cost_center_ids` applies no Cost Center filter (existing behavior unchanged).
- AC3: With one or more IDs, only projects assigned to at least one of the selected Cost Centers are returned.
- AC4: The IDs pass through `project_visibility_service.py` unchanged; existing visibility rules (admin/maintainer/auditor) still apply.
- AC5: The Cost Center predicate is applied to both the page query and the count query, so pagination totals match the filtered rows.
- AC6: The filter combines with the existing search and budget filters using AND.
- AC7: No new environment variables or configuration.

## Explicitly out of ticket scope (kept on branch on purpose)

- Commit `84d556949` allows `@` in model identifiers in `project_service.py` (Vertex `name@version` IDs in project `allowed_models`).
