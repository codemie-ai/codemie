# Capture preview in the validation pass — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Each accepted version gets one JPEG of its start page, under its validation attempt prefix, with a `manifest.preview` pointer. (Superseded by the MR !4837 review (AR-1).)
**Architecture:** After `inspect`, the worker asks the validator for a capture. The validator runs a sandboxed renderer child that drives Alpine Chromium over `--remote-debugging-pipe`. The worker pulls the JPEG through `file_chunks`, checks it and writes `preview.jpg` before `manifest.json` (an optional `preview` key in manifest version 1). (Superseded by the MR !4837 review (AR-1).)
**Spec:** `docs/superpowers/tasks/2026-11-12-epmcdme-17155-preview-capture/spec.md` (read it with this plan).
Commit per task using the repository's existing convention (`EPMCDME-17155: <Description>`, see `.ai-run/guides/standards/git-workflow.md`).

## Global Constraints

- `POLICY_VERSION = "static-v4"` and `POLICY_DEFINITION` stay unchanged. Do not edit `core/hosted_applications/policy.py`.
- No new pod, Deployment, Service, NetworkPolicy, image, job kind, attempt kind, column or migration.
- No new Python dependency: the renderer speaks CDP with the standard library. `requirements-validator.txt` stays unchanged.
- Capture never changes the verdict, `report.json` or the audit. Every capture or pull error gives a manifest with no `preview` key. (Superseded by the MR !4837 review (AR-1).)
- Logs carry class names only, never exception text or page text.
- Starting limits: soft deadline 15 s, hard limit 20 s, JPEG at most 1 MiB and 2048 px per side, viewport 1280×800. Task 1 sets the final limits.
- Real-sandbox and Chromium tests follow `test_parser_sandbox.py:33-37`: skip unless `HOSTED_TEST_SANDBOX_IMAGE` is set.

## Review Focus

1. Mixed rollout: an old API pod reads a manifest with a `preview` key and refuses it (today's parser refuses any unknown key). Task 10 documents same-commit rollout; Task 2 tests that today's manifests still parse. (Superseded by the MR !4837 review (AR-1).)
2. A version with 5000 files has no object room for `preview.jpg` (`object_limit` is 5002). Task 8 tests that the preview is dropped and the version is accepted.
3. A capture that ends after the job budget would fail the job. Task 8 tests that the worker skips capture when less than the hard limit is left.
4. A page requests `https://example.com/x.js` or a WebSocket. Task 4 tests that the request fails; Task 3 tests that IP sockets fail.
5. Both validator slots ask for a capture at once. Task 6 tests that the second waits for the single renderer and gets `null` when the wait passes `CAPTURE_LOCK_WAIT_SECONDS` (3 s).

---

### Task 1: Chromium spike (AC11)

**Files:** none committed except the `## Spike result` section of this plan.
Test-first: no — a measurement spike; it produces numbers, not code.

- [ ] Build a local image from `hosted-applications/Dockerfile.validator` plus `apk add chromium` from the base image's Alpine 3.24 repository. Record the exact `<version>-r<n>`.
- [ ] Run `/usr/lib/chromium/chromium` with the spec §2 flags under `docker run --cpus=1 --memory=3g --read-only --cap-drop=ALL --user=65532:65532` and a seccomp profile that returns `EPERM` for `socket`/`socketpair` unless the family is `AF_UNIX`. Confirm that a page loads and a JPEG is taken.
- [ ] Measure browser start, time to quiet and screenshot time, 5 runs each, for a static page, a Vite React or Vue build and a WebGL page. Run a large ZIP validation through the same validator at the same time, so clamd scans and the parser runs.
- [ ] Record the minimum Landlock read set (fonts, `/proc` paths, libraries), the needed non-secret environment values and the needed `RLIMIT_NOFILE`.
- [ ] Choose the limits. Let q = p95 of start plus time to quiet for the static and the framework pages. Soft = max(15, ceil(1.5 × q)). Hard = soft + max(5, ceil(2 × p95 screenshot time)). Record whether WebGL renders with `--disable-gpu`.
- [ ] Stop and report to the caller when Chromium fails under the socket filter, or when soft would be more than 30 s.

## Spike result

Measured 2026-11-12 by Task 1. Chromium loads the page and takes a JPEG under the socket filter, so no stop condition applies.

- **Pin:** `chromium=156.0.8078.79-r0` (Alpine 3.24.2 main, on the validator base image). It pulls `font-opensans` as its only font.
- **Host caveat:** arm64 Podman VM (libkrun, kernel 7.1). Production may be amd64. Re-check the timings there before release.
- **Sandbox:** `--cpus=1 --memory=3g --read-only --cap-drop=ALL --user=65532:65532`, only `/work` writable (tmpfs, no `/tmp`). A seccomp profile returns `EPERM` for `socket`/`socketpair` unless the family is `AF_UNIX`. Landlock and `RLIMIT_NOFILE` are applied in the child before exec. Spec §2 flags, viewport 1280x800, pages served from `https://app.preview.invalid` by `Fetch.fulfillRequest`. Every other request gets `Fetch.failRequest`.
- **Load:** all runs share one pod cgroup (`cpu.max` = 1 CPU) with the validator. The validator validates a 55 MB ZIP (1801 files), with clamd in its own container. A CPU-bound Python loop in the validator container uses the free CPU.

| Page (5 runs, under load) | Start median / p95 | Time to quiet median / p95 | Screenshot median / p95 |
|---|---|---|---|
| Static (HTML, CSS, SVG, one external image blocked) | 0.81 / 1.10 s | 0.73 / 0.75 s | 0.023 / 0.026 s |
| Vite 5 React build (143 kB JS) | 0.78 / 0.79 s | 0.83 / 0.84 s | 0.056 / 0.059 s |
| WebGL page | 0.78 / 0.88 s | 0.74 / 0.81 s | 0.019 / 0.022 s |

"Start" is from process spawn until the page target is attached and its domains are enabled. "Time to quiet" includes the 500 ms settle. p95 of 5 runs is the maximum.

- **q** = p95 of start + time to quiet (static and Vite) = 1.80 s. **Soft = max(15, ceil(1.5 × 1.80)) = 15 s. Hard = 15 + max(5, ceil(2 × 0.059)) = 20 s.**
- **Landlock read set (minimum, found by elimination):** read `/usr/lib`, `/usr/share/fonts`, `/proc`, `/dev/null`. Execute (and read) `/usr/lib/chromium/chromium`, `/usr/lib/chromium/chrome_crashpad_handler`, and the musl loader (`/lib/ld-musl-aarch64.so.1`; `/lib/ld-musl-x86_64.so.1` on amd64). All ABI 3 rights except execute on the per-run directory. Without `/proc`, crashpad fails on `/proc/self/fd` and the GPU process aborts. A `/proc/self` rule does not help, because the children have other PIDs. `/etc`, `/etc/fonts`, `/sys`, `/var`, `/lib` and the rest of `/usr/share` are not needed.
- **Environment:** `TMPDIR=<per-run directory under /work>` only. Without it, ProcessSingleton cannot make its socket directory and Chromium exits. `HOME` is not needed. With an empty environment, crashpad also fails (`--database is required`).
- **RLIMIT_NOFILE:** peak 129-132 open descriptors per process, 9 processes. 140 works and 128 fails (`EMFILE`). Choose **512**.
- **WebGL with `--disable-gpu`:** does not render. `getContext('webgl')` returns null, because the Alpine package ships no SwiftShader. The page still loads and a JPEG is taken (the canvas is blank).
- **Deviation:** clamd ran from the local amd64 `clamav/clamav` image under emulation, so its scan of the ZIP took longer than the measurement window. The parser stage therefore did not overlap the captures. The CPU-bound loop in the same 1-CPU cgroup replaced it, as the task allows.
- **Concern for Tasks 3 and 5:** a read rule on all of `/proc` lets the renderer read `/proc/<validator pid>/environ`, which holds `VALIDATOR_SERVICE_KEY` (same UID). The validator must hide it, for example with `PR_SET_DUMPABLE=0` on the server process, or by starting the renderer with a separate PID namespace or UID.

---

### Task 2: Optional preview pointer in manifest version 1 (AC10)

**Files:** Modify `src/codemie/core/hosted_applications/manifest.py:46-145`; Test `tests/codemie/core/hosted_applications/test_manifest.py`.
**Produces:** `build_manifest(..., preview: dict | None = None)` keeps `"version": 1` and emits `"preview"` only when `preview` is not `None`. `parse_manifest` returns a dict that always has `"preview"` (`None` when the key is absent). (Superseded by the MR !4837 review (AR-1).)
Test-first: yes — `test_parse_manifest_preview_key` fails because today's parser refuses the extra `preview` key. (Superseded by the MR !4837 review (AR-1).)

- [ ] Tests: a manifest without `preview` parses unchanged and gives `preview is None`; `build_manifest` without a preview gives the same bytes as today. A manifest with a valid preview parses. Each of these raises: `preview: null`; key not `<prefix>/preview.jpg`; size 0 or 1048577; sha256 not 64 lower hex; mime not `image/jpeg`; width or height 0 or 2049; an extra or a missing key inside `preview`; any other unknown top-level key; `version` other than 1. (Superseded by the MR !4837 review (AR-1).)
- [ ] Implement: allow the base key set, or the base key set plus `preview`. Validate the preview in a new `_validate_preview(item, prefix)`.

### Task 3: Renderer sandbox profile (AC5)

**Files:** Modify `src/codemie/hosted_apps_validator/isolation.py` (next to `enforce_parser_sandbox`, `:108-128`); Test `tests/codemie/core/hosted_applications/test_renderer_sandbox.py`.
**Produces:** `enforce_renderer_sandbox(files: Path, scratch: Path, browser: Path) -> None`. It raises `HostedApplicationError("VALIDATION_UNAVAILABLE", ...)` on any failure.
Test-first: yes — the real-Linux test fails because the function does not exist.

- [ ] Tests, run in Docker like `test_parser_sandbox.py:34` (raise `--pids-limit` for Chromium): `socket(AF_INET)`, `AF_INET6` and `AF_PACKET` give `EPERM`; `AF_UNIX` works. Reading a file outside the allowlist fails. Writing outside `scratch` fails. `/bin/sh` does not execute. `VALIDATOR_SERVICE_KEY` set in the parent is absent in the child.
- [ ] Implement: clear the environment (add only Task 1's non-secret values). Set rlimits CPU, NOFILE (Task 1 value), FSIZE and CORE 0; no `RLIMIT_AS`, because Chromium reserves large address space. Landlock: read `files`, the browser install and Task 1's read set; read and write `scratch`; execute only `browser`. Seccomp: keep the parser's io_uring, `pidfd_getfd` and ptrace denials. Allow process creation. Allow `socket`/`socketpair` only when `args[0]` (offset 16) is `AF_UNIX`; else `EPERM`.

### Task 4: Renderer request routing

**Files:** Create `src/codemie/core/hosted_applications/content_policy.py` (move `CONTENT_CSP` from `src/codemie/hosted_apps_gateway/content.py:47`; the gateway imports it). Create `src/codemie/hosted_apps_validator/renderer.py`. Test `tests/codemie/core/hosted_applications/test_renderer.py`.
**Produces:** `ORIGIN = "https://app.preview.invalid"`. `route(manifest: dict, files: dict[str, Path], url: str) -> tuple[int, dict, Path | None]`.
Test-first: yes — `test_route_serves_origin_files` fails because `renderer.py` does not exist.

- [ ] Tests: an origin URL resolves like `resolve_asset` (`manifest.py:148`) with its MIME and `CONTENT_CSP`. SPA history routing returns the entrypoint. A missing path gives 404. Any other origin, scheme or port gives a failure. `hosted_apps_gateway.content.CONTENT_CSP is content_policy.CONTENT_CSP`. The routed body never contains `WRAPPER_SCRIPT`.
- [ ] Implement `route` with `resolve_asset`. Do not inject `WRAPPER_SCRIPT`.

### Task 5: Renderer capture process

**Files:** Modify `src/codemie/hosted_apps_validator/renderer.py`, `src/codemie/hosted_apps_validator/contract.py`; Test `test_renderer.py`.
**Produces:** `python -m codemie.hosted_apps_validator.renderer capture <job.json> <scratch>` prints `{"size", "sha256", "width", "height"}` and writes `<scratch>/preview.jpg`, or prints `null`. Mode `probe` prints `{check: bool, ..., "chromium": "<version>"}`. In `contract.py`: `CAPTURE_SOFT_SECONDS`, `CAPTURE_HARD_SECONDS` (Task 1 values), `MAX_PREVIEW_BYTES = 1024 * 1024`, `PREVIEW_VIEWPORT = (1280, 800)`.
Test-first: yes — the Docker test `test_capture_static_page` fails because the `capture` mode does not exist.

- [ ] Tests (Docker, Chromium): a static page gives a 1280×800 JPEG within 1 MiB. A single-color page gives `null`. A page that never gets quiet gives a picture at the soft deadline. A page that fetches another origin still renders, and its request fails. `probe` reports every check `True` and a Chromium version.
- [ ] Implement: apply `enforce_renderer_sandbox`, then start Chromium with exactly the spec §2 flags and a fresh `--user-data-dir` in `scratch`. Use `Fetch.enable` for all URLs and answer with `route`. Take the picture when the load event has fired, no request is pending and 500 ms have passed, or at the soft deadline. Encode at quality 80; retry at 60 and 40 when it is over 1 MiB. Check uniform color on every picture, in a separate browser context on `about:blank`, with `createImageBitmap` and `OffscreenCanvas.getImageData`, tolerance ±2 per channel. Delete the profile directory before exit.

### Task 6: Validator capture route and self-test (AC3, AC5, AC6)

**Files:** Modify `src/codemie/hosted_apps_validator/server.py` (`Workspace :68`, `Inspector :178`, `file :408`, `health :434`, routes `:456-466`, startup `:559-569`); Test `test_validator_server.py`.
**Produces:** `POST /results/{key}/preview`, body `{"site_root", "entrypoint", "routing_mode"}`. The answer is `{"preview": null}` or `{"preview": {"index", "size", "sha256", "width", "height"}}`. `index == len(result["files"])`, and `GET /results/{key}/files/{index}` serves that file. `/health` adds `"capture": "on" | "off"`. `require_renderer(root) -> str | None` returns the Chromium version, or `None`.
Test-first: yes — `test_preview_route_returns_null_when_capture_off` fails with 404.

- [ ] Tests (fake renderer command): capture off (`VALIDATOR_PREVIEW_CAPTURE=false` or a failed self-test) gives `null`, and `/health` says `off`. A non-HTML entrypoint gives `null`. A workspace with less than `CAPTURE_HARD_SECONDS` to live gives `null`. A renderer that hangs is killed (process group) at the hard limit and gives `null`. A second capture waits for the single renderer and gives `null` when the wait passes `CAPTURE_LOCK_WAIT_SECONDS` (3 s). A capture that waited still gets its whole hard limit. A good capture is served at the new index with its size.
- [ ] Implement: check the entrypoint with `build_manifest` and a placeholder prefix. Run the renderer with `start_new_session=True` under one `asyncio.Lock`, in a scratch directory inside the result workspace; delete the scratch directory after the capture. Both limits count from renderer start, as spec §1 says: the hard limit from the renderer spawn, the soft deadline from the renderer's own start. The wait for the renderer lock comes before capture start and is at most `CAPTURE_LOCK_WAIT_SECONDS` (3 s). The worker's `CAPTURE_REQUEST_SECONDS` is 3 s + 20 s + 2 s = 25 s, so it covers the wait and the hard limit (decision CR-010, 2026-11-12). The startup self-test runs only when capture is on; log the Chromium version or "capture off".

### Task 7: Validator client capture

**Files:** Modify `src/codemie/service/hosted_applications/validator_client.py`; Test `tests/codemie/core/hosted_applications/test_validator_client.py`.
**Produces:** frozen dataclass `PreviewFile(index: int, size: int, sha256: str, width: int, height: int)`. `async capture(identifier, site_root, entrypoint, routing_mode, *, timeout: float) -> PreviewFile | None`. It never raises.
Test-first: yes — `test_capture_returns_preview_file` fails because `capture` does not exist.

- [ ] Tests (`httpx.MockTransport`): a good answer gives `PreviewFile`. `null`, a bad shape, size over 1 MiB, a non-200 status and a transport error each give `None`. `file_chunks(identifier, p.index, p)` pulls it unchanged.

### Task 8: Worker stores the preview (AC1, AC2, AC3, AC4, AC7)

**Files:** Modify `src/codemie/service/hosted_applications/worker.py:417-441`; Modify `tests/codemie/service/hosted_applications/conftest.py:198-227` (seam: `preview: bytes | None = None`, `capture_calls`); Test `test_worker.py`, `test_retention.py`.
**Consumes:** `PreviewFile`, `capture`, `build_manifest(preview=...)`, `CAPTURE_HARD_SECONDS`, `MAX_PREVIEW_BYTES`, `binary.image`, `thumbnails.dimensions`.
Test-first: yes — `test_accepted_version_has_preview` fails because the manifest has no `preview`. (Superseded by the MR !4837 review (AR-1).)

- [ ] Tests: an accepted version has `preview.jpg` and a matching `preview` key. With and without a preview, the verdict, `report.json` bytes and audit rows are equal. Each of these gives a manifest with no `preview` key and acceptance: capture error, digest mismatch, not a JPEG, wrong dimensions, no byte room, no object room (5000 files). Capture is not called when less than `CAPTURE_HARD_SECONDS` of budget is left, or for rejected or `awaiting_configuration` versions. Retire deletes `preview.jpg`. The ledger and quota count its bytes. (Superseded by the MR !4837 review (AR-1).)
- [ ] Implement `_capture_preview(work, identifier, chosen) -> tuple[bytes, dict] | None`. Budget left = `work["budget"] - (monotonic() - work["lease"].received_at)`. Request timeout = `CAPTURE_HARD_SECONDS + 5`. Buffer at most `MAX_PREVIEW_BYTES`; check the digest, `image(body, ".jpg")` and the dimensions. Call it before any write. If the bytes or `len(files) + 3` objects do not fit, rebuild the manifest without the preview. Write `preview.jpg` after the assets and before `manifest.json`, and check its stored digest. (Superseded by the MR !4837 review (AR-1).)

### Task 9: Validator image and resources (AC8)

**Files:** Modify `hosted-applications/Dockerfile.validator`, `hosted-applications/kubernetes.yaml:105`, `deploy-templates/values.yaml:328`; Test `tests/codemie/core/hosted_applications/test_deployment_manifests.py`.
Test-first: yes — `test_validator_memory_and_chromium_pin` fails because the limit is 2Gi and no pin exists.

- [ ] Tests: the validator memory limit is `3Gi` in both files and CPU stays `"1"`. The Dockerfile has `apk add --no-cache chromium=<pin>`. The label `org.codemie.chromium-version` equals that pin. The topology assertions stay unchanged.
- [ ] Implement with Task 1's pin.

### Task 10: Architecture decision and runbook (AC9)

**Files:** Modify `docs/hosted-applications/architecture.md`, `hosted-applications/README.md` (§1, §3, §7, §10, §13).
Test-first: no — documentation only.

- [ ] Write the content that spec §5 lists: D7 with reasons, rejected alternatives and cost; D1 and the intro point to D7; `VALIDATOR_PREVIEW_CAPTURE`; the renderer sandbox, self-test and logs; the Chromium update rule; the same-commit rollout precondition; Task 1's measured limits.

---

## Negative-constraint pass

- No change to policy, verdict rules or the output reservation formula: Tasks 6 and 8 keep the verdict path. Task 8 drops the preview and never raises `VALIDATION_LIMIT_EXCEEDED`.
- No new pods, images, job kinds, attempt kinds, columns or migrations: Tasks 2, 8 and 9.
- No capture-status field in the API, UI, report or audit: Task 8 keeps the report and audit equal. The `/health` `capture` key is validator-internal; spec §3 requires it.
- No network for the page, no `WRAPPER_SCRIPT`: Tasks 3 and 4.
- The validator process executes no app code: Tasks 5 and 6 (child process).
- No unpinned Chromium, no `chrome-headless-shell`, no CI job: Task 9.
- No backfill, no `/thumbnail` change, no manual cover change: no task touches them. Task 8 only imports `dimensions`.
