---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: a1b4643ef3fc796b671583e869835e26dc870569
---

# Code review — 2026-11-12-epmcdme-17156-preview-serving

**Decision:** approve (final round, confidence high)  
**Reviewed head:** `a1b4643` · **Rounds:** final

Both compact-profile lenses ran with parseable output: edge-case reported no findings and acceptance passed AC1-AC9 and the three plan criteria; the ruff and pytest gate is pending-stage-7. Standards audit was intentionally omitted by the compact profile (no coverage gap); blind and verification-gap lenses are not part of this profile. 0 deferred, 0 dismissed, no decision_needed items.

## Open — 0

## Coverage

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1: version with stored preview and no form/bundle cover shows the preview | pass | thumbnails.py:183-188 serves manifest['preview'] after the candidate loop; test_preview_serves_when_no_cover_or_bundled_raster. |
| AC2: each card loads one ordinary image, no application launched | pass | Preview read via storage.read_small in _verified; route unchanged; no gateway or launch path touched. |
| AC3: form cover first, then bundle cover image, not the preview | pass | Uploaded cover returns before manifest read; candidate loop precedes preview branch; test_bundled_raster_wins_over_preview. |
| AC4: viewers see the active version's preview, managers the selected one, also before activation | pass | Version choice at thumbnails.py:156-158 reused; test_viewer_gets_active_and_manager_selected_preview. |
| AC5: no stored preview behaves as today | pass | parse_manifest sets preview=None when key absent (manifest.py:156-159); branch skipped, get returns None. |
| AC6: rejected or failed version never serves a preview | pass | catalog._eligible_version filters before any manifest read; test_rejected_version_never_serves_preview. |
| AC7: form cover overrides preview; removal restores bundle image or preview | pass | test_form_cover_wins_over_preview_and_removal_restores_it covers put then remove; covers.py unchanged. |
| AC8: unreadable viewer gets today's answer and no preview bytes | pass | Access checks at thumbnails.py:147-155 raise before manifest read; test_unreadable_viewer_reads_no_preview_bytes. |
| AC9: architecture document lists the four-step order | pass | architecture.md lists form cover, bundled raster, automatic preview, initials. |
| Plan: served preview passes size, digest and header checks; digest mismatch gives 503 CONTENT_UNAVAILABLE | pass | Reuses _verified and _fits(body, '.jpg'); test_preview_integrity_is_rechecked and area-limit test. |
| Plan: ETag is the preview sha256 and changes with a new version's preview | pass | Cover etag built from preview['sha256']; test asserts a different etag across versions. |
| Plan: route, headers and has_cover unchanged; no migration or backfill | pass | Diff touches only thumbnails.py, tests and architecture.md. |
| Quality gate: make ruff and pytest for test_thumbnail.py and test_covers.py pass | pending-stage-7 | Lint and test execution are Stage 7 evidence. |

Standards: commit-format na · code-quality na · security na
