# Technical Research

**Task**: hosted-applications validator renderer capture worker storage deployment
**Generated**: 2026-11-12 (sliced from the parent analysis of 2026-11-10)
**Research path**: filesystem (sliced, not re-run)

---

## 1. Original Context

Child "Capture preview in the validation pass" of EPMCDME-17070 (automatic preview cover for hosted applications). Branch EPMCDME-17070_automatic-preview-cover, from origin/EPMCDME-15659; MR target EPMCDME-15659.

Scope: add a sandboxed headless renderer process to the validator pod and image. Capture one raster per version from the files in the validator workspace during the validation pass. Pull it back through the validator client and store it under the validation attempt prefix, with a pointer from the version. A capture failure or timeout yields no preview and never changes the verdict. Rejected or failed versions keep no preview. Record the new server-side capture decision in `architecture.md` and update the runbook.

The child story's acceptance criteria AC1–AC9 and decisions D1–D6 are in `story.md` in this folder.

---

## 2. Codebase Findings

Terminology: current code uses **version** throughout (`HostedApplicationVersions`, `selected_version_id`, `active_version_id`, `retire_version`).

### Existing Implementations
- `src/codemie/hosted_apps_validator/` — `server.py` (Starlette routes `/livez`, `/health`, `/reservations`, `/validate`, `/results/{key}/files/{index}`, `/results/{key}` DELETE; capacity 2), `sandbox.py` ("No application code is evaluated"), `isolation.py` (Landlock, seccomp, rlimits; fail closed), `scanner.py` (clamd).
- `src/codemie/service/hosted_applications/validator_client.py` — `ready`, `reserve`, `inspect`, `file_chunks` (pulls result files by index).
- `src/codemie/service/hosted_applications/worker.py` — `ValidationWorker`: `claim()` (FOR UPDATE SKIP LOCKED, 5 candidates, fair share per quota holder), 30 s DB lease renewed every 10 s (`heartbeat`), job `budget` from `validation_budget`. `_process` streams the original to the validator, writes `report.json`, `assets/<path>` and `manifest.json` through `storage.writer(prefix, byte_limit, object_limit, lease)`. `accept()` sets `validation_state='accepted'`, `accepted_attempt_id`, `manifest_key`, `entrypoint_path`, `site_root`, `routing_mode`; sets `app.selected_version_id` when it was `None`; adds a `version.accept` audit. `fail()` marks `rejected`/`failed` and `delete_requested` on the output attempt.
- `src/codemie/core/hosted_applications/manifest.py` — manifest v1: `{site_root, entrypoint, routing_mode, files: {path: {key, size, sha256, mime, document}}}`; key = `<attempt prefix>/assets/<path>`.
- `src/codemie/service/hosted_applications/retention.py` — `retire_version` sets every non-deleted attempt of the version to `delete_requested` and queues a `cleanup_upload` job.
- `src/codemie/service/hosted_applications/runtime.py` — `VALIDATION_WORKERS = 2`, `maintain()` (cleanup, purge, retention, retry, gc) and the storage sweep.
- Raster checks shared by covers and thumbnails: `binary.image()` structure check and header dimensions (1..2048 per side).

### Architecture and Layers Affected
- Validator: `server.py` capture step, a new renderer module with its own sandbox profile, `isolation.py`.
- Service: `validator_client.py` (pull the raster), `worker.py` (`_process` / `accept`: write the raster, set the pointer).
- Core / models: `manifest.py` or the version model (pointer), plus an Alembic migration if a column is chosen (hosted chain head `f8d24a6c0b17`).
- Deployment: `hosted-applications/Dockerfile.validator`, `requirements-validator.txt`, validator resources in `hosted-applications/kubernetes.yaml` and chart values.
- Docs: `docs/hosted-applications/architecture.md` (new decision), `hosted-applications/README.md` runbook (§3, §7, §10, §13).

### Integration Points
- Worker → validator over HTTP with `HOSTED_APPS_VALIDATOR_KEY`; the validator has no DB or storage credentials.
- Storage writes go to fresh prefixes `a-<app hex>/<app hex>/<version>/<attempt>/...`; ledger row in `hosted_application_storage_attempts` (`kind IN ('upload','validation')`).
- Jobs table `kind IN ('validate','purge','retention','cleanup_upload')`.
- The content gateway serves app files with `CONTENT_CSP` and injects `WRAPPER_SCRIPT`; the renderer works from raw files, so fidelity differs.
- No headless browser exists anywhere: no Playwright, Chromium, Puppeteer or Selenium dependency.

### Patterns and Conventions
- Storage I/O never under DB locks; every write fenced by the lease token and `writer_fence`; quota changes under the creator's `UserState` row lock.
- Errors are `HostedApplicationError(code, message, status)`; logs carry class names only, never exception text.
- API and validator images are built from the same commit; the policy digest (`POLICY_VERSION = "static-v4"`) must match.

---

## 3. Documentation Findings

- `docs/hosted-applications/architecture.md` — D1 "Static artifacts only; apps execute in the viewer's browser"; intro "There is no server-side rendering"; D5 validator and clamd in two private pods with NetworkPolicy, no credentials.
- `core/hosted_applications/policy.py` — `POLICY_DEFINITION["execution"] = {"server": False, ...}`; any change needs a new `POLICY_VERSION`.
- `hosted-applications/README.md` — runbook §1 Topology, §3 Configuration (validator environment), §7 Validator and scanner, §10 Rollout, §13 Residual risks.
- Derived conventions: new columns appear in both the SQLModel and the hosted migration (`test_schema_parity.py`); deployment names and topology match across chart, `kubernetes.yaml` and the runbook (`test_deployment_manifests.py`).

---

## 4. Testing Landscape

- `tests/codemie/core/hosted_applications/test_validator_server.py`, `test_parser_sandbox.py`, `test_deployment_manifests.py`.
- `tests/codemie/service/hosted_applications/test_worker.py` (accept, adapters, malware, lease expiry, retryable codes, fair share), `test_retention.py`, `test_storage_contract.py`, `test_schema_parity.py`.
- `conftest.py`: real PostgreSQL; `processing` fixture returns `ValidationWorker` with `HostedFileStorage(tmp_path)` and the `TrustedValidatorSeam` fake validator.
- Gaps: no test or seam for any renderer or capture step.

---

## 5. Configuration and Environment

- Validator env: `VALIDATOR_BIND_HOST`, `VALIDATOR_PORT` (8091), `VALIDATOR_WORKSPACE`, `VALIDATOR_SERVICE_KEY`, `CLAMD_HOST`, `CLAMD_PORT`, `CLAMD_CONFIGURATION`.
- `hosted-applications/kubernetes.yaml` — validator Deployment (1 replica, Recreate, UID 65532, read-only root, caps dropped, no SA token, `emptyDir /work` 2Gi, 250m/512Mi to 1 CPU/2Gi), Service, NetworkPolicy (ingress only from API pods on 8091; egress DNS + clamd 3310).
- `hosted-applications/Dockerfile.validator` — Python 3.12 Alpine by digest; installs only hash-pinned `requirements-validator.txt` (7 packages); copies only `core/hosted_applications` and `hosted_apps_validator`.
- Chart: `deploy-templates/templates/hosted-apps/` and values `features.tools.hosted-apps`.
- `.gitlab-ci.yml` has no validator image build.

---

## 6. Risk Indicators

- The renderer executes untrusted uploaded code inside the validator pod. `isolation.py` is built for a parser that executes nothing, so the renderer needs its own sandbox profile (no network, no credentials, read only the version's files, rlimits).
- Alpine has no official Playwright build; a headless browser CLI from the distribution is the likely path. The image grows a lot.
- Validator memory and CPU limits and its 2-job capacity need review; the capture adds load per version.
- Capture must not couple to the verdict (AC2) and must fit inside the validation budget and lease (AC3).
- Reversing architecture D1 needs a new recorded decision; the policy digest must stay unchanged (AC6).
- The raster must pass the same header checks as other covers.

---

## 7. Summary for Complexity Assessment

The change touches the validator (a new sandboxed renderer process and a capture step in `server.py`), the validator client, the worker's `_process` and `accept`, the version pointer (manifest field or column plus migration), the validator image, its resources in `kubernetes.yaml` and the chart, the runbook and the architecture document. Novelty is high: no headless browser and no server-side rendering exist, and the renderer runs untrusted code. The pod posture, the lease, the attempt ledger, retention and quota are reused, so there is no new pod, job kind or attempt kind.

---

## 8. External References

None.

---

## 9. Product-owner Decisions (2026-11-10)

- D5: The renderer runs in the existing validator pod, as a separate process next to the validator. The validator process keeps executing nothing; `execution.server: False` and `POLICY_VERSION` stay unchanged.
- D6: Capture runs in the validation pass, from the files in `/work`. The worker pulls the raster back through `file_chunks` and writes it with the validation output. Capture failure or timeout yields no preview and never changes the verdict. Rejected or failed versions drop it. No file push, no new job kind, no second lease.
- D7: No new pod type, Deployment, Service, NetworkPolicy or image. `test_deployment_manifests.py` keeps the same topology.
- D8: The raster is stored under the validation attempt prefix, so the attempt ledger, retention, cleanup, the storage sweep and quota cover it. The pointer (manifest field or version column) is left to the spec.
