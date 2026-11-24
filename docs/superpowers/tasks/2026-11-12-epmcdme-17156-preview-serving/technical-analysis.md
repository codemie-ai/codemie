# Technical Research

**Task**: hosted-applications thumbnail cover order
**Generated**: 2026-11-12 (sliced from the parent analysis of 2026-11-10)
**Research path**: filesystem (sliced, not re-run)

---

## 1. Original Context

Child "Serve preview from /thumbnail" of EPMCDME-17070 (automatic preview cover for hosted applications). Branch EPMCDME-17070_automatic-preview-cover, from origin/EPMCDME-15659; MR target EPMCDME-15659. Depends on the sibling child "Capture preview in the validation pass", which stores one raster per accepted version under the validation attempt prefix and adds a pointer from the version (manifest field or version column, chosen in that child's spec).

Scope: extend the `HostedThumbnailService` cover order to form cover, bundle cover image, automatic preview, initials. Use the active version for viewers and the selected version for managers. Never serve a preview for rejected or failed versions. Update the "Card covers" section of `architecture.md`.

The child story's acceptance criteria AC1–AC9 and decisions D1–D4 are in `story.md` in this folder.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/hosted_applications/thumbnails.py` — `HostedThumbnailService.get(identity, app_id) -> Cover | None` (`Cover(body, mime, etag)`). Order today: (1) row in `hosted_application_covers`, (2) bundled raster from the accepted manifest, else `None`. Version chosen: `selected_version_id` for managers (`manageable`), `active_version_id` for viewers (`readable`), via `catalog._eligible_version`. A manager with no eligible version still gets the form cover; a viewer gets 404.
- Bundled raster rule: `_candidates(manifest)` scans `manifest['files']`; suffix in `RASTER`, `asset['mime']` must match, `0 < size <= MAX_THUMBNAIL_BYTES` (1 MiB); stem rank by `THUMBNAIL_PRIORITIES`; top 5. `_fits`: `binary.image()` plus header dimensions 1..2048 per side, area <= 1 MiP. Bytes read with `storage.read_small(key, maximum=1 MiB)` and re-verified against size and SHA-256 (`CONTENT_UNAVAILABLE` 503 on mismatch). ETag = `"<sha256 hex>"`. Manifest read through `ImmutableContentCache.shared(storage)`.
- `src/codemie/service/hosted_applications/covers.py` — form cover `put_cover` / `delete_cover`; `_covered()` gives `has_cover` for list pages.
- `src/codemie/rest_api/models/hosted_application_cover.py` — `HostedApplicationCovers`, one row per application (the form cover; must not hold the preview).
- `src/codemie/rest_api/routers/hosted_applications.py:324` — `GET /hosted-applications/{app_id}/thumbnail`: 204 when `None`; else `private, no-cache`, strong ETag, 304 on `If-None-Match`, CSP `default-src 'none'; sandbox`, `nosniff`.
- Catalog `_eligible_versions` gates servability (accepted job, stored attempts, permitted policy, security clear).

### Architecture and Layers Affected
- Service: `thumbnails.py` (cover order, reading the preview through the version pointer).
- REST router: unchanged contract.
- Possibly `covers.py` `_covered()` if list pages should report a preview; the UI already calls `/thumbnail`.

### Integration Points
- Reads the raster that the sibling child writes, through its pointer, with `storage.read_small`.

### Patterns and Conventions
- Authorization runs before every thumbnail response; only immutable manifest data is cached.
- Raster checks are header-only and shared by covers and thumbnails.

---

## 3. Documentation Findings

- `docs/hosted-applications/architecture.md` — "Card covers" lists the 3-step order and says "Cards never render the application itself, and the gateway has no static-preview mode".

---

## 4. Testing Landscape

- `tests/codemie/service/hosted_applications/test_thumbnail.py` (7 tests: access, oversized/active formats, integrity, owner-private candidate, cache, ETag route, missing cover).
- `test_covers.py` (17 tests, including form cover wins over bundled raster).
- `test_catalog.py`.
- Gap: no test for a cover order with more than two sources.

---

## 5. Configuration and Environment

- No new settings. Feature flag `features:hostedApplications` / `FEATURE_HOSTED_APPLICATIONS`.

---

## 6. Risk Indicators

- The preview sits between the bundled raster and `None`, applies to the manager's selected version before activation, and never appears for rejected or failed versions.
- The stored raster must pass the same integrity and header checks as a bundled raster before it is served.
- The ETag must change when the version (and so the preview) changes.

---

## 7. Summary for Complexity Assessment

A narrow service-layer change: one more source in `HostedThumbnailService.get`, read through the pointer the sibling child adds, with the same checks, caching and ETag as the bundled raster. The route contract does not change, there is no UI change, and no migration is expected in this child. Tests extend `test_thumbnail.py` and `test_covers.py`. One documentation update in `architecture.md`.

---

## 8. External References

None.

---

## 9. Product-owner Decisions (2026-11-10)

- D3: Cover order: 1) form cover, 2) bundle cover image, 3) automatic preview, 4) initials.
- D8: The raster lives under the validation attempt prefix; the version pointer is defined by the sibling child.
