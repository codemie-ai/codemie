# Serve Preview from /thumbnail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `HostedThumbnailService.get` serves the stored automatic preview as the third cover source.

**Architecture:** EPMCDME-17155 stores one JPEG per accepted version. The manifest points to it in `manifest['preview']`. `parse_manifest` always sets this key, to a dict or `None`. The thumbnail service already reads that manifest for the right version: selected for managers, active for viewers. Rejected and failed versions are filtered out by `catalog._eligible_version`. So the change is one more branch after the bundled-raster loop. It reuses `_verified` and `_fits`.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest-asyncio.

**Spec:** `story.md` in this folder (AC1–AC9, D1–D5) and the inline requirements of EPMCDME-17156.

Commit per task using the repository's existing convention (`EPMCDME-17156: Description`). Lint gate: `make ruff`.

## Acceptance criteria

- [ ] The cover order is: form cover, bundle cover image, automatic preview, then `None` (initials).
- [ ] Viewers get the active version's preview. Managers get the selected version's preview, also before activation.
- [ ] A version without a stored preview behaves as today.
- [ ] A rejected or failed version never serves a preview.
- [ ] A viewer who may not read the application gets today's not-found answer. No preview bytes are read.
- [ ] The served preview passes the bundled raster's size, digest and header checks. A digest mismatch gives `503 CONTENT_UNAVAILABLE`.
- [ ] The ETag is `"<preview sha256>"`, so it changes when a new version brings a different preview.
- [ ] The route, its headers and `has_cover` do not change. There is no migration and no backfill. (Superseded by the MR !4837 review (AR-1).)
- [ ] `architecture.md` "Card covers" lists the four-step order.

## Global Constraints

- Do not change `covers.py` `_covered()`/`has_cover` (D5), the router `thumbnail` endpoint, the UI or any model/migration. (Superseded by the MR !4837 review (AR-1).)
- Size limits for the preview are the existing ones: `MAX_THUMBNAIL_BYTES` (1 MiB), `_fits` (1..2048 px per side, area <= 1024*1024). (Superseded by the MR !4837 review (AR-10).)
- Negative constraints. Each line names what is forbidden and where the plan honors it:
  - Do not change the route contract: Task 1 edits only the service.
  - Do not change `has_cover`, the UI or the schema, and add no migration: Global Constraints. (Superseded by the MR !4837 review (AR-1).)
  - Do not backfill (NG2): versions without a preview behave as today, and Task 1 pins this.
  - Do not launch an application or make a live preview for a card (NG1, AC2): the preview is a stored file read with `read_small`.
  - Do not change manual cover upload (NG3): Task 1 does not touch `covers.py`.
  - Never serve a preview for a rejected or failed version: pinned in Task 1.
  - Never read preview bytes for an unreadable viewer: pinned in Task 1.
  - No task breaks these constraints.

## Review Focus

- A preview whose area is above 1 MiP (2048x2048) passes the worker's checks but must not be served. Pin it in Task 1 (`test_preview_over_the_area_limit_is_not_served`). (Superseded by the MR !4837 review (AR-10).)
- The form cover row is returned before the manifest is read. Keep the preview branch after it, so AC7 holds without a `covers.py` change.
- `validator.preview` on the test seam persists between uploads in one test. The helper must assign it on every call.

---

### Task 1: Preview as the third cover source

**Files:**
- Modify: `src/codemie/service/hosted_applications/thumbnails.py:15-18` (docstring: add the preview), `:177-182` (preview branch after the candidate loop)
- Test: `tests/codemie/service/hosted_applications/test_thumbnail.py`, `tests/codemie/service/hosted_applications/test_covers.py`

**Interfaces:**
- Consumes: `manifest['preview']`, either `{"key", "size", "sha256", "mime": "image/jpeg", "width", "height"}` or `None`. It also uses `HostedThumbnailService._verified(asset)`, `_fits(body, suffix)` and `preview_jpeg(width, height)` from `tests/.../conftest.py:213`.
- Produces: `image_app(..., filename='preview.png', preview: bytes | None = None)` in `test_thumbnail.py`. With `filename=None` the bundle has no raster. The helper sets `processing[4].preview = preview` before `worker.run_one()` on every call.

- [ ] **Step 1: Extend `image_app` and write the failing tests.** In `test_thumbnail.py`:
  - `test_preview_serves_when_no_cover_or_bundled_raster`: `filename=None, preview=preview_jpeg()`. The manager gets `Cover(preview_jpeg(), 'image/jpeg', '"' + sha256(preview_jpeg()).hexdigest() + '"')`. The same app with `preview=None` returns `None` (AC1, AC5).
  - `test_bundled_raster_wins_over_preview`: default `png()` raster plus a preview. The body is `png()` (AC3).
  - `test_viewer_gets_active_and_manager_selected_preview`: before activation, the manager gets preview A and the viewer raises `HostedApplicationError`. After `share_with_project`, the viewer gets A. Upload a second version with `preview_jpeg(80, 60)` and select it. The manager gets B with a different ETag, and the viewer still gets A (AC4).
  - `test_rejected_version_never_serves_preview`: set `validator.rejection = HostedApplicationError("MALWARE_DETECTED", "Rejected")` with a preview set. The manager's call raises `HostedApplicationError`, because the app has no eligible version and no form cover. `read_small` (monkeypatched, as at `:166-172`) never reads a key ending in `/preview.jpg` (AC6).
  - `test_unreadable_viewer_reads_no_preview_bytes`: `identity('viewer')` on an unshared app raises `HostedApplicationError`. No `/preview.jpg` read happens (AC8).
  - `test_preview_integrity_is_rechecked`: overwrite the stored `preview.jpg` (found with `rglob('preview.jpg')`, outside `assets`) with `b'corrupt'`. Expect `HostedApplicationError` with code `CONTENT_UNAVAILABLE` (D4).
  - `test_preview_over_the_area_limit_is_not_served`: `preview_jpeg(2048, 2048)` gives `None`. (Superseded by the MR !4837 review (AR-10).)

  In `test_covers.py` add `test_form_cover_wins_over_preview_and_removal_restores_it`. Use `image_app(..., filename=None, preview=preview_jpeg())`. After `put` the thumbnail is the uploaded cover. After `remove` it is the preview (AC7).
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/hosted_applications/test_thumbnail.py tests/codemie/service/hosted_applications/test_covers.py -v`. Expect the new preview tests to FAIL: they get `None` instead of the JPEG.
- [ ] **Step 3: Implement.** After the candidate loop in `HostedThumbnailService.get`, read `preview = manifest['preview']`. When it is not `None`, call `body = await self._verified(preview)`. When `_fits(body, '.jpg')` holds, return `Cover(body, preview['mime'], '"' + preview['sha256'] + '"')`. Otherwise return `None`. Update the module docstring to list the three sources.
- [ ] **Step 4:** Run the same command. Expect all tests to PASS, old and new.

Test-first: yes — the preview tests in test_thumbnail.py and test_covers.py fail because `get` returns `None` for a bundle that has only a stored preview.

### Task 2: Document the four-step cover order

**Files:**
- Modify: `docs/hosted-applications/architecture.md:487-496`

- [ ] **Step 1:** Insert "3. the automatic preview captured for the version during validation (same size and integrity checks; managers see the selected version's, viewers the active version's);" and renumber initials to 4. Reword `:493` so it says cards still never launch the application: the preview is a picture stored at validation time. In `:505-507`, say that the uploaded cover comes before the bundled raster and the preview.

Test-first: no — documentation only.
