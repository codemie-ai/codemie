---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: e47c3870a8e790948c3de87feb70076e672e55b6
---

# Code review — 2026-11-12-epmcdme-17155-preview-capture

**Decision:** approve (check round, confidence final low · check medium)  
**Reviewed head:** `e47c387` · **Rounds:** final → check

**Closed:** 13 fixed · 0 moot · 3 decided

All 16 prior blocking findings are resolved: 13 code findings checked in the current source and 3 decisions (CR-002, CR-003, CR-010) backed by id-mapped outcome and rationale records; none unresolved or superseded. Confidence is medium because CR-009 relies on the crashpad handler exiting by itself after a hard-limit kill (stated in a comment, not tested) and the CR-015 Docker sandbox run is recorded only in implementation-notes.local.md and was not re-verified. business_review and standards_review are carried forward from the final round unchanged, so their CR-002/CR-010 partial rows reflect the pre-fix state.

## Open — 0

## Decided — 3

- **CR-002** · Renderer Landlock allowlist wider than spec §3 — `src/codemie/hosted_apps_validator/isolation.py:42` — **Accept the measured set and try to narrow /proc: amend spec §3, story AC5 and architecture.md D7 to list the measured read set, the musl loader execute right and the PR_SET_DUMPABLE compensating control; try /proc/self and keep all of /proc with the reason if Chromium does not start.** Tried a /proc/self Landlock rule with the Dockerfile.validator image (localhost/codemie-hosted-validator:task9): Chromium closed its CDP pipe within 0.3 s and the probe failed, because Landlock binds the rule to the renderer's own /proc/<pid> and each Chromium process reads its own PID directory. All of /proc is kept; spec §3, story AC5, D7 and runbook §7 now list the accepted set, the loader and crashpad execute rights and the PR_SET_DUMPABLE control.
- **CR-003** · No signal scoping on Landlock ABI 3–5 kernels — `src/codemie/hosted_apps_validator/isolation.py:94` — **Accept and record the risk: capture stays on for Landlock ABI 3 to 5; record it in spec §3 and architecture.md D7 with node kernel versions unknown; add a runbook note to check the node kernel (6.12 or later) and log the Landlock ABI in the self-test.** Spec §3, D7 and runbook §7 and §13 record the accepted risk and that the kernel versions are not known. The renderer probe now reports landlock_abi, and require_renderer logs 'Renderer self-test passed (chromium=..., landlock_abi=...)'; test_renderer_self_test_returns_the_chromium_version and the Docker probe test prove it.
- **CR-010** · Hard and soft limits use different clocks — `src/codemie/hosted_apps_validator/server.py:698` — **Count both limits from renderer start, as spec §1 says; cut the lock wait so lock wait + 20 s + margin stays within the worker's 25 s CAPTURE_REQUEST_SECONDS; update plan.md Task 6, not the spec.** contract.CAPTURE_LOCK_WAIT_SECONDS = 3 bounds the lock wait; preview_capture._render sets the hard deadline at renderer spawn; preview.CAPTURE_REQUEST_SECONDS = 3 + 20 + 2 = 25. plan.md Task 6 updated. test_a_capture_that_waited_for_the_renderer_still_gets_its_whole_hard_limit was RED before and GREEN after.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| §1 Capture only after inspect succeeds and configuration() returns a choice; verdict already fixed | pass | worker.py calls _capture_preview on the chosen-configuration branch, before _write_output |
| §1 No capture for rejected, failed or awaiting_configuration versions; the configure pass captures | pass | capture only on the chosen-configuration path; rejected and awaiting-configuration tests |
| §1 Worker sends site_root, entrypoint, routing_mode to a new validator route; validator requires an HTML entrypoint | pass | POST /results/{key}/preview; _capture_job requires text/html entrypoint |
| §1 Soft 15 s and hard 20 s limits, both counted from capture start including browser start | partial (final round) | server counts hard limit from request arrival, renderer counts soft from its own start; a queued capture is killed before its soft deadline (CR-010) |
| §1 Capture runs only when job budget and result lifetime leave at least the hard limit | pass | worker left < CAPTURE_REQUEST_SECONDS returns None; server checks workspace.expires - started |
| §1 At most one renderer in the pod; a capture that cannot start before its deadline yields no preview | pass | capture_lock with wait_for until the soft deadline |
| §1 Worker pulls the picture over the existing chunked file path | pass | file_chunks with index len(files) |
| §2 Renderer is a child process; validator runs no application code; POLICY_VERSION unchanged | pass | _render spawns the renderer module with start_new_session; policy.py not changed |
| §2 CDP pipe; synthetic origin; requests answered from version files with resolve_asset, same MIME and CONTENT_CSP; others fail | pass | renderer.py ORIGIN, route() and Fetch.failRequest; route tests |
| §2 Fixed minimal Chromium flag set and a fresh profile in scratch under /work | pass | FLAGS matches the spec list; profile removed with scratch |
| §2 Viewport 1280x800; picture when quiet or at the soft deadline | pass | PREVIEW_VIEWPORT; _Page.quiet with settle time; pump loop until soft |
| §2 At the hard limit the browser is killed and there is no preview | pass | Browser deadline and server killpg; hung page and hung renderer tests |
| §2 Output is one JPEG, at most 1 MiB and 2048 px per side | pass | encode and describe bound size and sides; _kept_preview re-checks |
| §2 A single-colour picture counts as renders nothing | pass | _uniform on every encoded picture; single-colour test |
| §3 seccomp refuses socket() for AF_INET, AF_INET6, AF_PACKET; only AF_UNIX and pipes | pass | _renderer_seccomp allows AF_UNIX only; Docker isolation test asserts EPERM |
| §3 Landlock: read only version files and browser install; write one scratch dir; execute only browser binaries | partial (final round) | also read on /usr/lib, /usr/share/fonts, /proc, /dev/null and execute on the musl loader (CR-002) |
| §3 Empty environment: no VALIDATOR_SERVICE_KEY and no TLS material | pass | _scrub_environment; CHILD_ENVIRONMENT holds PYTHONPATH only |
| §3 rlimits on CPU, open files, file size and core dumps; group killed at the hard limit | pass | RLIMIT_CPU, NOFILE, FSIZE, CORE; _kill_group |
| §3 Only the picture leaves the renderer; logs carry class names, never page text | pass | class-name logging; regex-bounded renderer errors; one stdout JSON line |
| §3 Startup self-test; failure disables capture, validation continues; /health and log report capture off | pass | require_renderer and configure_capture; /health capture on/off |
| §4 Worker buffers and checks size, digest, image() and dimensions before it writes; failing picture dropped | pass | _capture_preview checks; PREVIEW_FAULTS test |
| §4 Picture written as <attempt prefix>/preview.jpg via the lease-fenced writer, before manifest.json, outside assets/ | pass | _write_output writer.put('preview.jpg') before manifest.json |
| §4 Picture without byte or object room is dropped, never VALIDATION_LIMIT_EXCEEDED | pass | room check drops the preview first; byte and object room tests |
| §4 Any capture or pull error gives no preview key; report, verdict and audit byte-identical | pass | _capture_preview catches Exception; verdict/report/audit test |
| §4 Retire, purge, cleanup, sweep and quota cover the attempt prefix | pass | accept adds preview_size; retire test |
| §4 Manifest stays version 1 with one optional preview key; no column or migration | pass | build_manifest(preview=None), _validate_preview; no migration |
| §5 Dockerfile.validator installs Chromium from Alpine with an explicit pin | pass | chromium=156.0.8078.79-r0 |
| §5 Image label org.codemie.chromium-version with the pinned version | pass | LABEL present; deployment test |
| §5 Renderer self-test logs the Chromium version at startup | pass | configure_capture log line |
| §5 No new pod, Deployment, Service, NetworkPolicy or image | pass | only the validator memory limit changes |
| §5 No CI job added | pass | .gitlab-ci.yml not changed |
| §5 Validator memory limit 2Gi to 3Gi in kubernetes.yaml and chart; CPU unchanged | pass | both files 3Gi, cpu 1 |
| §5 Setting VALIDATOR_PREVIEW_CAPTURE (default true) switches capture off | pass | capture_setting; setting tests |
| §5 architecture.md decision D7; intro and D1 point to D7 | pass | intro, D1 and D7 hunks |
| §5 Runbook §1 topology shows the renderer process in the same pods | pass | README §1 |
| §5 Runbook §3 documents the new setting | pass | README §3 table row |
| §5 Runbook §7 renderer sandbox, self-test, logs and Chromium update rule | pass | README §7 bullets |
| §5 Runbook §10 rollout: preview manifests, same-commit images, old-API refusal window, rollback | pass | README §10 item 6 |
| §5 Runbook §13 residual risks | pass | README §13 Chromium bullet |
| AC1 Accepted version with a rendering start page has manifest.preview to a stored JPEG within bounds | pass | test_accepted_version_has_preview; Docker static page test |
| AC2 Crash, timeout, blank, failed check or no room gives no preview key; verdict, report and audit identical | pass | PREVIEW_FAULTS, room tests, verdict/report/audit test |
| AC3 Quiet page when quiet; never-quiet at soft deadline; hung browser killed at hard limit; job within budget | partial (final round) | a capture that waited >5 s on capture_lock is killed before its soft deadline (CR-010) |
| AC4 Rejected, failed and awaiting-configuration versions keep no picture | pass | capture only on the chosen-configuration path |
| AC5 Tests show no IP socket, no reads outside the allowlist, no validator key; failed self-test disables capture | pending-stage-7 | Docker isolation tests skip unless HOSTED_TEST_SANDBOX_IMAGE is set; self-test-off tests run without Docker |
| AC6 POLICY_VERSION and POLICY_DEFINITION unchanged; application code runs only in the renderer | pass | policy.py untouched |
| AC7 Retire and purge delete preview.jpg; its bytes count in the attempt ledger and quota | pass | retire test; purge relies on existing prefix deletion |
| AC8 Deployment diff changes only the validator image and memory limit; chart, kubernetes.yaml, runbook agree | pass | values.yaml, kubernetes.yaml, Dockerfile.validator, README agree |
| AC9 architecture.md has D7; D1 and the intro refer to it | pass | architecture.md hunks |
| AC10 parse_manifest accepts v1 with or without a valid preview and refuses invalid or unknown keys | pass | _validate_preview, _BASE_KEYS; parametrized bad-preview tests |
| AC11 Spike first: AF_INET EPERM, timings at 1 CPU, static/React/WebGL pages; plan records values | pass | plan.md Task 1 spike result; README §7 measured limits |
| Non-goal: no showing of the picture and no /thumbnail cover order change | pass | no serving change |
| Non-goal: no API, UI, report or audit field about capture status | pass | capture state only in validator /health and logs |
| Non-goal: no change to validation policy, POLICY_VERSION, verdict rules or output reservation formula | pass | reservation computation untouched |
| Non-goal: no network access for the rendered page, including the gateway WRAPPER_SCRIPT | pass | route() serves raw files; no-wrapper test |
| Non-goal: no new pods, Deployments, Services, NetworkPolicies, images, job kinds or attempt kinds | pass | no new job or attempt kind |
| AC1 One picture of the start page stored with the accepted version's output, within 1 MiB and 2048 px | pass | preview.jpg under the attempt prefix |
| AC2 Capture failure leaves verdict and report as without previews, and no picture is stored | pass | verdict/report/audit test and PREVIEW_FAULTS |
| AC3 Slow or endless start page stops at its own time limit; validation pass finishes inside its budget | partial (final round) | queued capture cut at request+20 s before its soft-deadline picture (CR-010); capture can also spend the write budget (CR-013) |
| AC4 Rejected or failed version keeps no picture | pass | capture after the verdict is fixed; rejected-version test |
| AC5 Network, credential or out-of-version file access during capture fails; only the picture leaves | partial (final round) | network and credentials denied; reads of /usr/lib, fonts and all of /proc succeed (CR-002) |
| AC6 Validator executes no application code; policy and version unchanged | pass | renderer child process; policy.py untouched |
| AC7 Retire or purge deletes the picture; its bytes count toward quota | pass | retire ledger test |
| AC8 No new pod type, Deployment, Service or NetworkPolicy; chart, kubernetes.yaml and runbook agree | pass | values.yaml and kubernetes.yaml 3Gi; README §1 and §7 |
| AC9 A recorded decision states the validator pod captures one picture per version in a sandboxed renderer | pass | architecture.md D7 |

Standards: commit-format pass · code-quality partial (final round) · security pass
