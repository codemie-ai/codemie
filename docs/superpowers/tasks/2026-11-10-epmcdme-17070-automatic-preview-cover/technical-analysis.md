# Technical Research

**Task**: hosted-applications thumbnail cover validator worker deployment
**Generated**: 2026-11-10
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-17070: Automatic preview cover for hosted applications. Branch EPMCDME-17070_automatic-preview-cover (created from origin/EPMCDME-15659, the unmerged hosted applications epic branch; MR target will be EPMCDME-15659).

Context: Follow-up to EPMCDME-15659 (hosted applications). A card cover today comes from the owner-uploaded cover, otherwise from an image in the bundle named preview, thumbnail, screenshot, cover or poster (PNG/JPEG/WebP, up to 1 MiB), otherwise the generated initials cover. Most uploaded apps have neither, so most cards show initials. The first version of the feature rendered HTML apps live in an iframe on every catalog card. It was removed: each card was a full launch (launch and view-session records plus one authorization transaction per file, competing with real launches for 16 gateway slots per viewer, 64 per process and a 10-connection hosted DB pool) and ran uploaded code just because someone scrolled the catalog. Every version already passes through the validator (ClamAV and a sandboxed parser) before it can run, and the cover is served through GET /v1/hosted-applications/{id}/thumbnail as an ordinary image. Nothing renders application content on the server today; the validator is deliberately built not to execute or render anything. The UI already shows whatever /thumbnail returns, including to managers before the first activation (fixed in codemie-ui !2044), so the preview needs no UI change: for the card it is one more image source behind /thumbnail. It must not be stored as the owner's form cover, which keeps its own place in the order and its Remove action. Proposal: take a screenshot of each version on the backend once, right after it passes validation (see D2), and serve it through the existing /thumbnail as an ordinary image.

Story: As a CodeMie user browsing applications, I want each card to show a picture of what the application looks like so that I can recognise and choose apps without opening them, even when their owner never uploaded a cover.

Acceptance Criteria:
AC1: Given a version without a cover uploaded through the form or a cover image in its bundle, when it passes validation, then its card in the catalog shows a picture of the application's start page instead of initials.
AC2: Given a catalog page with many such cards, when a user opens or scrolls it, then each card loads one ordinary image and no application is launched or run for the preview.
AC3: Given an application with a cover uploaded through the form or a cover image in its bundle, when its card is shown, then that cover is shown (the form cover first), not the automatic preview.
AC4: Given a new version becomes active, when the card is shown again, then the preview reflects the active version for viewers and the selected version for managers, including before the application has an active version, so an owner sees the preview before anyone else does.
AC5: Given the preview cannot be captured for a version (for example the page renders nothing or capture times out), when validation finishes, then the version is accepted or rejected exactly as it would be without previews, and the card falls back to the bundled image or initials; an owner who finds a captured preview unsuitable replaces it by uploading a cover.
AC6: Given a version that fails validation, when validation finishes, then no preview is produced or served for it.
AC7: Given an application with an automatic preview, when its owner uploads or replaces a cover through the form, then the card shows the uploaded cover; when the owner removes it, then the card shows the bundle's cover image if there is one, otherwise the automatic preview.

Out of Scope: NG1 interactive or live previews on cards. NG2 previews for applications already in the catalog (no backfill). NG3 changes to the existing manual cover upload/replace/remove in the form.

Decisions: D1 screenshot taken on the backend once per version when it is checked, served through the existing /thumbnail as an ordinary image (design left to implementation). D2 the capture may run the app's own code, once per version, in a separate isolated renderer (no network, no credentials, no session) after the version is accepted, from its stored files; the validator itself keeps running nothing. D3 cover order: 1) form cover, 2) bundle cover image, 3) automatic preview, 4) initials. D4 no backfill.

---

## 2. Codebase Findings

Terminology: current code uses **version** throughout (`HostedApplicationVersions`, `selected_version_id`, `active_version_id`, `retire_version`). "release" does not appear in hosted code as a domain term.

### Existing Implementations
- `src/codemie/service/hosted_applications/thumbnails.py` — `HostedThumbnailService.get(identity, app_id) -> Cover | None` (`Cover(body, mime, etag)`). Order today: (1) row in `hosted_application_covers`, (2) bundled raster from the accepted manifest, else `None`. Version chosen: `selected_version_id` for managers (`manageable`), `active_version_id` for viewers (`readable`), via `catalog._eligible_version`. A manager with no eligible version still gets the form cover; a viewer gets 404.
- Bundled raster rule: `_candidates(manifest)` scans `manifest['files']`; suffix in `RASTER` (`.png/.jpg/.jpeg/.webp`), `asset['mime']` must match, `0 < size <= MAX_THUMBNAIL_BYTES` (1 MiB); stem rank by `THUMBNAIL_PRIORITIES = ('preview','thumbnail','screenshot','cover','poster')` (exact, or `name-`/`name_` prefix); top 5. `_fits`: `binary.image()` structure check plus header dimensions 1..2048 per side, area <= 1 MiP. Bytes are read with `storage.read_small(asset['key'], maximum=1 MiB)` and re-verified against manifest size and SHA-256 (`CONTENT_UNAVAILABLE` 503 on mismatch). ETag = `"<sha256 hex>"`. Manifest is read through `ImmutableContentCache.shared(storage)`. Nothing is derived or stored: the bundled raster is chosen per request from the manifest.
- `src/codemie/service/hosted_applications/covers.py` — form cover: `put_cover` / `delete_cover` (`CoverServiceMixin`), limits 1 MiB, 2048 px/side, 4 MiP; `sniff()` signature check; bytes stored in PostgreSQL; charged to creator quota (`_charge_cover`); `_covered()` gives `has_cover` for list pages (keys only).
- `src/codemie/rest_api/models/hosted_application_cover.py` — `HostedApplicationCovers` (PK `app_id`, `media_type`, `sha256`, `byte_size`, `width`, `height`, `data` LargeBinary, CHECKs on all bounds, FK CASCADE). One row per application, not per version.
- `src/codemie/rest_api/routers/hosted_applications.py:324` — `GET /hosted-applications/{app_id}/thumbnail`: 204 when `None`; else `private, no-cache`, strong ETag, 304 on `If-None-Match`, CSP `default-src 'none'; sandbox`, `nosniff`.
- `src/codemie/service/hosted_applications/worker.py` — `ValidationWorker`: `claim()` (FOR UPDATE SKIP LOCKED, 5 candidates, fair share per quota holder), 30 s DB lease renewed every 10 s (`heartbeat`), job `budget` from `validation_budget`. `_process` streams the original to the validator, writes `report.json`, `assets/<path>` and `manifest.json` through `storage.writer(prefix, byte_limit, object_limit, lease)`. `accept()` sets `version.validation_state='accepted'`, `accepted_attempt_id`, `manifest_key`, `entrypoint_path`, `site_root`, `routing_mode`; sets `app.selected_version_id` when it was `None`; adds `version.accept` audit. `fail()` marks `rejected`/`failed` and `delete_requested` on the output attempt.
- `src/codemie/core/hosted_applications/manifest.py` — manifest v1: `{site_root, entrypoint, routing_mode, files: {path: {key, size, sha256, mime, document}}}`; key = `<attempt prefix>/assets/<path>`.
- `src/codemie/service/hosted_applications/runtime.py` — `HostedApplicationsRuntime.start()` creates `VALIDATION_WORKERS = 2` `work()` loops, `maintain()` (cleanup, purge, retention, retry, gc every 5 s / 60 s) and the storage sweep. `DB_POOL_SIZE = 10`.
- `src/codemie/service/hosted_applications/retention.py` — `retire_version` sets every non-deleted attempt of the version to `delete_requested` and queues a `cleanup_upload` job.
- `src/codemie/hosted_apps_validator/` — `server.py` (Starlette routes `/livez`, `/health`, `/reservations`, `/validate`, `/results/{key}/files/{index}`, `/results/{key}` DELETE; capacity 2), `sandbox.py` ("No application code is evaluated"), `isolation.py` (Landlock, seccomp, rlimits; fail closed), `scanner.py` (clamd). Client: `service/hosted_applications/validator_client.py` (`ready`, `reserve`, `inspect`, `file_chunks`).

### Architecture and Layers Affected
- REST router: `thumbnail` route (unchanged contract today).
- Service: `thumbnails.py` (cover order), `worker.py` (post-accept hook point), `runtime.py` (background task wiring), `retention.py` / `maintenance.py` (cleanup of stored bytes).
- Repository / storage: `repository/hosted_applications/` (`filesystem.py`, `object_storage.py`, `s3_backend.py`, `gcs_backend.py`, `factory.py`).
- Models / migrations: `rest_api/models/hosted_application.py`, `hosted_application_cover.py`; Alembic `src/external/alembic/versions/e3b7c1a95d42_add_hosted_applications.py` and `f8d24a6c0b17_platform_sharing_grants_and_links.py` (current head of the hosted chain).
- Deployment: `hosted-applications/kubernetes.yaml`, `hosted-applications/Dockerfile.validator`, chart `deploy-templates/templates/hosted-apps/*` and `values.yaml` `features.tools.hosted-apps`.

### Integration Points
- Worker → validator over HTTP with `HOSTED_APPS_VALIDATOR_KEY`; files are pulled back by index (`file_chunks`). The validator has no DB or storage credentials.
- Storage: `FILES_STORAGE_TYPE` `aws` (S3 multipart, presign bound to lease), `gcp` (fence + compose), `filesystem` (local/test only). Writes go to fresh prefixes `a-<app hex>/<app hex>/<version>/<attempt>/...`; ledger row in `hosted_application_storage_attempts` (`kind IN ('upload','validation')`).
- Jobs table `kind IN ('validate','purge','retention','cleanup_upload')`; state CHECK and `passed` constraints tie to `validate`.
- Catalog `_eligible_versions` gates servability (accepted job, stored attempts, permitted policy, security clear).
- Content gateway (`hosted_apps_gateway/content.py`) serves app files with `CONTENT_CSP` (`connect-src 'self'`, `sandbox allow-scripts allow-same-origin`) and injects `WRAPPER_SCRIPT` (`navigation.py`) into HTML documents.
- No headless browser exists anywhere: no Playwright, Chromium, Puppeteer or Selenium dependency. Pillow is in `pyproject.toml` (lines 134, 179) but hosted code never decodes images ("No rendering, decoding or remote fetch").
- Existing isolated-pod precedent: code executor (`deploy-templates/templates/code-executor-*.yaml`: namespace + Role allowing the API service account to create pods/jobs).

### Patterns and Conventions
- Storage I/O never under DB locks; JSON snapshots (`model_dump()`) cross transactions.
- Every write fenced by lease token and `writer_fence`; quota changes under the creator's `UserState` row lock.
- Strong ETag = content SHA-256; authorization runs before every thumbnail response; only immutable manifest data is cached.
- Raster checks are header-only (`binary.image`, `dimensions`), shared by covers and thumbnails.
- Errors are `HostedApplicationError(code, message, status)`; logs carry class names only, never exception text.
- API and validator images built from the same commit; policy digest (`POLICY_VERSION = "static-v4"`) must match.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `docs/hosted-applications/architecture.md` — "Card covers" section lists the 3-step order (form cover, bundled raster, initials) and states: "Cards never render the application itself, and the gateway has no static-preview mode". System context, data model (eight tables), storage and validation capacity sections.
- `hosted-applications/README.md` — runbook: §1 Topology, §3 Configuration (Fixed limits, Validator environment), §6 Storage, §7 Validator and scanner, §10 Rollout, §13 Residual risks.
- `.ai-run/guides/` has no hosted-specific guide; only `integration/cloud-integrations.md` notes the dedicated hosted bucket.

### Architectural Decisions
- architecture.md D1: "Static artifacts only; apps execute in the viewer's browser." Intro: "There is no server-side rendering".
- D5: validator and clamd in two private pods, one replica each, NetworkPolicy; the validator is sandboxed and has no credentials.
- `POLICY_DEFINITION["execution"] = {"server": False, ...}` in `core/hosted_applications/policy.py`; any change to the policy definition needs a new `POLICY_VERSION`.
- Feature flag `features:hostedApplications` / `FEATURE_HOSTED_APPLICATIONS`.

### Derived Conventions
- Every new table/column must appear in both the SQLModel and the hosted migration (`test_schema_parity.py`).
- Deployment names and topology must match across chart, `kubernetes.yaml` and the runbook (`test_deployment_manifests.py`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/hosted_applications/test_thumbnail.py` (7 tests: access, oversized/active formats, integrity, owner-private candidate, cache, ETag route, missing cover).
- `test_covers.py` (17 tests: upload/replace/remove, quota, replay, form cover wins over bundled raster, has_cover batching, purge refund).
- `test_worker.py` (accept, adapters, malware, lease expiry, retryable codes, fair share, eligibility).
- `test_retention.py`, `test_purge.py`, `test_storage_contract.py`, `test_schema_parity.py`, `test_catalog.py`.
- `tests/codemie/core/hosted_applications/test_validator_server.py`, `test_parser_sandbox.py`, `test_deployment_manifests.py`.

### Testing Framework and Patterns
- pytest + pytest-asyncio, xdist (`-n 3`), `pytest.ini` `pythonpath = src`.
- `conftest.py`: real PostgreSQL ("not an in-memory substitute"), migration loaded by `load_migration`; fixtures `catalog`, `processing` (returns `ValidationWorker` with `HostedFileStorage(tmp_path)` and `TrustedValidatorSeam` fake validator), `identity` factory.
- `storage_fakes.py` for object-storage adapters; tests build PNGs inline (`png(width, height)`).

### Coverage Gaps
- No test or seam for any renderer or capture step (none exists).
- No test for a cover order with more than the two current sources.

---

## 5. Configuration and Environment

### Environment Variables
- API: `FEATURE_HOSTED_APPLICATIONS`, `HOSTED_APPS_CONTENT_DOMAIN`, `HOSTED_APPS_CREDENTIAL_SECRET`, `HOSTED_APPS_VALIDATOR_URL`, `HOSTED_APPS_VALIDATOR_KEY` (>= 32 chars), `HOSTED_APPS_STORAGE_BUCKET`, `HOSTED_APPS_STORAGE_DIR`, `HOSTED_APPS_STORAGE_S3_GRACE_SECONDS`, `HOSTED_APPS_STORAGE_SWEEP_SECONDS`, `HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS`, `HOSTED_APPS_QUOTA_BYTES`, `FILES_STORAGE_TYPE`. Validated in `core/hosted_applications/settings.py`.
- Validator: `VALIDATOR_BIND_HOST`, `VALIDATOR_PORT` (8091), `VALIDATOR_WORKSPACE`, `VALIDATOR_SERVICE_KEY`, `CLAMD_HOST`, `CLAMD_PORT`, `CLAMD_CONFIGURATION`.

### Configuration Files
- `hosted-applications/kubernetes.yaml` — validator Deployment (1 replica, Recreate, UID 65532, read-only root, caps dropped, no SA token, `emptyDir /work` 2Gi, 250m/512Mi to 1 CPU/2Gi), Service, NetworkPolicy (ingress only from API pods on 8091; egress DNS + clamd 3310); clamd Deployment/Service/NetworkPolicy (egress 443 to public IPs only); content Ingress.
- `hosted-applications/Dockerfile.validator` — Python 3.12 Alpine by digest; installs only hash-pinned `requirements-validator.txt` (starlette, anyio, idna, typing-extensions, uvicorn, click, h11); copies only `core/hosted_applications` and `hosted_apps_validator`.
- Chart: `deploy-templates/templates/hosted-apps/` (namespace, validator/clamd deployment, service, networkpolicy, `default-deny-networkpolicy.yaml` for Ingress), values `features.tools.hosted-apps` (`enabled: false`, namespace `codemie-hosted-apps`).

### Feature Flags and Deployment Concerns
- `docker-compose.yml` has no validator service; local validator runs via `docker run -p 127.0.0.1:18091:8091` (runbook §7).
- `.gitlab-ci.yml` has no validator image build; the runbook says to publish it "through the normal image pipeline".
- Secrets: `codemie-hosted-applications` Secret (`validator-key`), credential secret from the secret manager.

---

## 6. Risk Indicators

- Speculative: the change reverses a recorded decision. architecture.md D1 and "Card covers" say nothing renders on the server; D5 and `policy.py` `execution.server: False` describe a no-execution pipeline. Docs need an explicit new decision, and the policy digest must stay unchanged if the renderer is outside the validation policy.
- Speculative: no headless browser exists in the repo. A renderer needs a new image (Chromium-class, far heavier than the 7-package validator image), a new Deployment, Service and NetworkPolicy (egress deny-all), and new chart values, matching `test_deployment_manifests.py`.
- Speculative: D2 says "no credentials" but "from its stored files". The renderer cannot read the private bucket without credentials; the worker would need to push files to it, as it pulls files from the validator today.
- Speculative: storage of the screenshot. `HostedApplicationCovers` is per application and is the form cover (must not be reused). A per-version store means either a new column/table plus migration (schema parity test) or an object under the attempt prefix, which interacts with the `kind IN ('upload','validation')` and jobs `kind` CHECKs, retention cleanup, quota and the sweep.
- Speculative: AC5 requires capture failure to leave acceptance unchanged. Capture inside `accept()` or under the validation lease/budget would couple outcomes; a separate post-accept step needs its own retry/lease semantics.
- Speculative: `thumbnails.py` order change. The preview must sit between bundled raster and `None`, apply to the manager's selected version before activation (AC4), and never appear for rejected/failed versions (AC6).
- Speculative: renderer fidelity. Served pages get `CONTENT_CSP` and `WRAPPER_SCRIPT`; rendering from raw files differs. Apps that call `connect-src 'self'` will see no network.
- Speculative: output must pass the same header checks (`binary.image`, 2048 px, 1 MiB) as other covers.
- Validator scaled at 1 replica, 2 concurrent jobs; a renderer adds CPU/memory load per accepted version.

---

## 7. Summary for Complexity Assessment

The change touches the hosted applications service layer (`thumbnails.py` cover order, `worker.py` post-accept step, `runtime.py` task wiring, retention/cleanup), the hosted data model and Alembic chain (the head is `f8d24a6c0b17`), the storage adapters (S3, GCS, filesystem) and the deployment layer (standalone `kubernetes.yaml`, the Helm chart under `deploy-templates/templates/hosted-apps/`, the runbook and architecture doc). Today the thumbnail endpoint picks the form cover from PostgreSQL, or else a bundled raster chosen per request from the accepted manifest. Nothing is derived or stored per version for covers.

The novelty is high. No server-side rendering exists, and no headless browser dependency exists. The architecture doc records "no server-side rendering" as a decision, and the validation policy pins `execution.server: False`. An isolated renderer is a new pod type, with a new image, network policy and capacity profile. The worker's lease/fence model, quota accounting and attempt ledger are strict, and any new stored object must fit them.

Tests are strong around the existing surface: real-PostgreSQL fixtures, a fake validator seam (`TrustedValidatorSeam`), storage contract tests, schema parity and deployment manifest consistency tests. Nothing covers rendering. The main risks are isolating the renderer (no network, no credentials, but it still needs the stored files), keeping capture failure separate from acceptance (AC5), choosing where per-version preview bytes live, and updating the documented decisions.

---

## 8. External References

None named by the task. The task refers to codemie-ui !2044 (UI fix already merged), which is outside this repository and was not read.

---

## 9. Product-owner Decisions (2026-11-10)

These decisions supersede the renderer-service assumptions in Sections 2, 5, 6 and 7.

- D5: The renderer runs in the existing validator pod, as a separate process next to the validator (`hosted_apps_validator/`). It reuses the pod's posture: no credentials, no service-account token, NetworkPolicy with egress DNS + clamd only, read-only root, UID 65532. The validator process keeps executing nothing; `policy.py` `execution.server: False` and `POLICY_VERSION` stay unchanged.
- D6: Capture runs in the validation pass, from the files the validator pod already holds in its `/work` workspace. The worker pulls the raster back the same way it pulls files today (`file_chunks`) and writes it with the validation output. Capture failure or timeout yields "no preview" and never changes the verdict (AC5). Rejected or failed versions drop it (AC6). There is no file push, no new job kind and no second lease.
- D7: No new infrastructure: no new pod type, Deployment, Service, NetworkPolicy or image. Only `Dockerfile.validator` (adds a headless browser), the validator resources in `kubernetes.yaml` and the chart values, and the runbook change. `test_deployment_manifests.py` keeps the same topology.
- D8: The raster is stored under the validation attempt prefix (`a-<app>/<app>/<version>/<attempt>/...`), so the attempt ledger, retention (`retire_version`), cleanup, the storage sweep and quota already cover it. No new attempt `kind` and no new jobs `kind`. How the version points to the raster (a manifest field or a version column) is left to the spec.
- Remaining risks: the validator image grows (Chromium-class browser; Alpine base has no official Playwright build, so the headless browser CLI is the likely path); validator memory and CPU limits and its 2-job capacity need review; the renderer needs its own sandbox profile inside the pod, since `isolation.py` (Landlock, seccomp) is built for a parser that executes nothing; rendering from raw files differs from the served page (`CONTENT_CSP`, `WRAPPER_SCRIPT`); `architecture.md` D1 "no server-side rendering" needs a new recorded decision.
