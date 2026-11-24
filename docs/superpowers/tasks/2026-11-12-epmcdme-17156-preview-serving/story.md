# Serve preview from /thumbnail — Story

**Date**: 2026-11-12
**Status**: Approved
**Ticket**: EPMCDME-17156 (https://jiraeu.epam.com/browse/EPMCDME-17156)
**Parent**: ../../story.md
**Feature**: automatic-preview-cover

---

## Context

- `GET /v1/hosted-applications/{id}/thumbnail` serves the card cover as an ordinary image. Today the order is the form cover, then a cover image from the bundle, else no cover (the UI then shows initials).
- Managers get the selected version, also before the first activation. Viewers get the active version. The UI already shows whatever `/thumbnail` returns, so this child needs no UI change.
- The sibling child "Capture preview in the validation pass" stores one picture per accepted version with its validation output, and gives the version a pointer to it.
- The architecture document's "Card covers" section lists the current three-step order and says cards never render the application.

---

## Complexity

**Size**: S (13/36) · **Recommended flow**: sdlc-light

One service changes (`HostedThumbnailService`); it reuses the bundled raster path's checks and ETag, and the route contract and UI stay the same.

---

## Story

**As a** CodeMie user browsing applications, **I want** each card to show a picture of what the application looks like **so that** I can recognise and choose apps without opening them, even when their owner never uploaded a cover.

---

## Background

The sibling child stores a picture for each accepted version, but no card shows it yet. Adding it to the existing `/thumbnail` cover order gives most cards a useful cover. Each card still loads one ordinary image and no application runs for it.

---

## Acceptance Criteria

- [ ] AC1: Given a version with a stored preview and without a form cover or a bundle cover image, when its card is shown in the catalog, then the card shows the preview instead of initials.
- [ ] AC2: Given a catalog page with many such cards, when a user opens or scrolls it, then each card loads one ordinary image and no application is launched or run for the preview.
- [ ] AC3: Given an application with a form cover or a bundle cover image, when its card is shown, then that cover is shown (the form cover first), not the preview.
- [ ] AC4: Given a new version becomes active, when the card is shown again, then viewers see the active version's preview and managers see the selected version's preview, also before the application has an active version.
- [ ] AC5: Given a version with no stored preview (capture failed, or the version was validated before this feature), when its card is shown, then the card shows the bundle cover image if there is one, otherwise initials, as today.
- [ ] AC6: Given a version that was rejected or failed validation, when its card is shown, then no preview is served for it.
- [ ] AC7: Given an application with a preview, when its owner uploads or replaces a cover through the form, then the card shows the uploaded cover; when the owner removes it, then the card shows the bundle cover image if there is one, otherwise the preview.
- [ ] AC8: Given a viewer who may not read the application, when they request its thumbnail, then they get the same answer as today and no preview bytes.
- [ ] AC9: Given the architecture document, when a reader looks up card covers, then it lists the four-step order: form cover, bundle cover image, automatic preview, initials.

---

## Out of Scope

- Capturing or storing the picture (sibling child "Capture preview in the validation pass").
- NG1: Interactive or live previews on cards.
- NG2: Previews for versions validated before this feature (no backfill).
- NG3: Changes to the manual cover upload, replace and remove in the form.
- UI changes; the `/thumbnail` response contract stays the same.

---

## Decisions

- D1: Cover order? → 1) form cover, 2) bundle cover image, 3) automatic preview, 4) initials — ticket (parent D3).
- D2: Which version's preview? → The active version for viewers, the selected version for managers — ticket (parent AC4).
- D3: Does the preview change the `/thumbnail` contract? → No: same route, caching, ETag and security headers; the preview is one more image source — proposed by PO: the UI shows whatever `/thumbnail` returns (parent Context).
- D4: Is the stored preview checked again when served? → Yes, with the same size, structure and integrity checks as a bundle cover image, but without the bundled-cover area limit (the automatic cover is a JPEG up to 1 MiB and 2048 px per side; changed by the MR !4837 review, AR-10) — proposed by PO: `/thumbnail` already re-verifies bundled rasters (technical analysis §2).
- D5: Does `has_cover` on list pages report the preview? → No: `has_cover` keeps meaning the form cover, which drives the form's Remove action; the card already loads `/thumbnail` — proposed by PO: NG3 keeps the form unchanged.

---

## Open Questions

- None

---

## Sources

- Parent story `../../story.md` (AC1–AC7; D1, D3).
- `technical-analysis.md` in this folder (sliced from the parent).
