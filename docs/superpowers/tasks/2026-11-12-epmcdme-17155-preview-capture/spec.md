# Capture preview in the validation pass — Spec

**Ticket**: EPMCDME-17155 (child of EPMCDME-17070) · **Requirements**: `story.md` (AC1–AC9, D1–D7) · **Size**: XL, kept whole (story D7)

## Context

Every version passes through the validator pod before it can run. This change adds one picture of the start page per accepted version. A separate sandboxed renderer process in the same pod takes the picture, from the files the validator already holds. The sibling child EPMCDME-17156 shows the picture from `/thumbnail`. It finds the picture through the manifest pointer defined here. (Superseded by the MR !4837 review (AR-1); see the §4 note.)

## Behavior

### 1. When the capture runs

- The worker asks for a capture only after `inspect` succeeds and `configuration()` returns a choice (`worker.py:375-399`). The verdict is already fixed at that point, so the capture cannot change it (AC2).
- No capture runs for rejected uploads, for failed jobs, or for `awaiting_configuration`. The configure action queues a new validation pass; that pass captures.
- The worker sends the chosen `site_root`, `entrypoint` and `routing_mode` to a new validator route on the existing result key. The validator checks that the entrypoint is an HTML file in that result.
- The capture has two time limits, both counted from capture start, including browser start. The soft deadline is 15 s; section 2 says what happens at it. The hard limit is 20 s; it applies only when the browser hangs. These are starting values, and the timing spike (AC11) sets the final ones.
- The capture runs only when the job budget and the result lifetime (`contract.py:37-51`, `RESULT_GRACE_SECONDS`) leave at least the hard limit. Otherwise there is no preview (AC3).
- At most one renderer runs in the pod at a time. A capture that cannot start before its deadline yields no preview.
- The worker pulls the picture back over the existing chunked file path of the validator client (`file_chunks`).

### 2. The renderer process

- The validator starts the renderer as a child process, like the parser. The validator process itself still executes no application code. `POLICY_VERSION` and `execution.server: False` stay unchanged (AC6).
- The renderer drives headless Chromium over `--remote-debugging-pipe`. It loads the entrypoint from a synthetic HTTPS origin that does not resolve. It answers every request to that origin from the version's files, with the same resolution as `resolve_asset` (`manifest.py:148-168`), the same MIME types and `CONTENT_CSP`. It fails every other request.
- Chromium starts with a fixed, minimal set of flags: `--headless`, `--remote-debugging-pipe`, `--no-sandbox` (section 3 is the boundary), `--no-zygote`, `--disable-gpu`, `--disable-extensions`, `--disable-dev-shm-usage`, `--no-first-run`, `--disable-background-networking`, `--disable-component-update`, `--disable-sync`, `--mute-audio`, `--js-flags=--max-old-space-size=512` (a V8 heap cap per page, so one page cannot use up the memory of the shared validator container), and `--user-data-dir` pointing to a fresh, empty profile directory in the job's scratch directory under `/work`. The renderer deletes the profile with the scratch directory.
- Viewport 1280×800. The renderer takes the picture at the first of these two moments:
  - Normal path: the page is quiet. That is, the load event has fired, no request is pending, and a short settle time has passed.
  - Soft deadline (15 s): the renderer takes the picture of what is on screen, even if the page is not quiet yet.
- At the hard limit (20 s) the renderer kills the browser and there is no preview.
- Output is one JPEG, at most 1 MiB and 2048 px per side.
- A picture with every pixel the same color counts as "renders nothing": no preview (AC2). This check applies to pictures from the soft deadline too.

### 3. The renderer sandbox (AC5)

A new profile next to `enforce_parser_sandbox` (`isolation.py:108-123`) applies to the whole renderer process tree, and fails closed:

- No IP sockets: seccomp refuses `socket()` for `AF_INET`, `AF_INET6` and `AF_PACKET`. Only `AF_UNIX` and pipes stay.
- Landlock: read the version's files, the browser install and the measured set that Chromium 156 needs to start: `/usr/lib`, `/usr/share/fonts`, `/proc` and `/dev/null`. Write one private scratch directory under `/work`. Execute only Chromium, its crashpad handler and the musl dynamic loader.
  - Decision CR-002 (2026-11-12): this measured set is accepted. A `/proc/self` rule was tried, and Chromium does not start: Landlock binds the rule to the renderer's own `/proc/<pid>`, and each Chromium process reads its own PID directory. Chromium 156 starts its crashpad handler even with `--disable-crash-reporter`, and stops when it cannot run it.
  - The loader execute right lets the renderer run any readable ELF file through the loader, a file it writes to scratch included. Such a program still has no IP sockets (seccomp), and Landlock still limits its files.
  - Compensating control: the renderer runs as the validator's UID and can read all of `/proc`. The validator therefore sets `PR_SET_DUMPABLE` to 0 for itself at start, so its `environ`, `mem` and `fd` are unreadable to the renderer. If the kernel refuses, capture stays off.
- Signal scoping: on Landlock ABI 6 or later (Linux 6.12 or later) the renderer also cannot signal processes outside its sandbox or connect to their abstract UNIX sockets.
  - Decision CR-003 (2026-11-12): on ABI 3 to 5, capture stays on without this scoping. This is an accepted risk. After a browser escape on such a node, the page can signal the validator, which runs as the same UID, and stop every validation in the pod.
  - The node kernel versions in use are not known. The self-test logs the node's Landlock ABI, and the runbook tells operators to check the node kernel.
- Empty environment: no `VALIDATOR_SERVICE_KEY` and no TLS material.
- rlimits on CPU time, open files, file size and core dumps. The process group is killed at the hard limit.
- Only the picture leaves the renderer. Logs carry class names, never page text.

At startup the validator runs a self-test of this profile, in the same way as `require_isolation` (`server.py:121`). On failure the validator disables capture and keeps validating. `/health` and the log report that capture is off.

### 4. Storage and the pointer

> **Superseded by the MR !4837 review (AR-1).** The pointer is not a manifest key. Three nullable columns of the version row (`preview_key`, `preview_size`, `preview_sha256`) hold it, and the worker sets them in the same transaction as `manifest_digest`. The manifest reader ignores unknown top-level keys. The text below records the first design.

- The worker buffers the picture (at most 1 MiB). It checks the size, the digest from the validator, `binary.image()` and the dimensions (1..2048 per side) *before* it writes. A picture that fails a check is dropped, and nothing is written.
- The worker writes the picture as `<attempt prefix>/preview.jpg` through the same lease-fenced writer, before `manifest.json`. It is outside `assets/`, so the content gateway never serves it.
- If the picture does not fit the output reservation (`byte_upper_bound`, `object_limit`), the worker drops it. It never raises `VALIDATION_LIMIT_EXCEEDED` for it (AC2).
- Any capture or pull error gives no `preview` key and a log line with the class name. The report, the verdict and the audit stay byte-identical (AC2).
- Retire, purge, cleanup, the storage sweep and quota already cover the attempt prefix (AC7). A failed `accept` already marks the attempt `delete_requested` (AC4).

The manifest stays at version 1 and gets one optional key, `preview`. The worker writes it only when it stores a picture. An absent key means "no preview", so manifests without a picture stay byte-for-byte as today. `parse_manifest` keeps the exact-key rule for every other key and allows only this one optional key. No column and no migration. (Decision, user: an optional key in version 1, over a manifest version 2; both have the same rollout risk, because today's parser refuses any unknown key.)

```json
"version": 1,
"preview": {"key": "<prefix>/preview.jpg", "size": 1..1048576, "sha256": "<64 hex>", "mime": "image/jpeg", "width": 1..2048, "height": 1..2048}
```

### 5. Deployment and documents (AC8, AC9)

- `Dockerfile.validator` installs Chromium from the Alpine distribution with an explicit pin: `apk add --no-cache chromium=<version>-r<n>`. Rejected alternatives:
  - The unpinned latest package from Alpine. It is not reproducible, and it breaks the image's rule that nothing is resolved at build time.
  - Chrome for Testing `chrome-headless-shell`. It needs glibc, so the base image would move off Alpine.
- The image has the label `org.codemie.chromium-version`, with the pinned package version as its value.
- The renderer self-test logs the Chromium version at startup.
- There is no new pod, Deployment, Service, NetworkPolicy or image.
- This change adds no CI job. This repository does not build the validator image: `.gitlab-ci.yml` has only MR guards. An outside image pipeline, which is not visible here, builds the image from `Dockerfile.validator` at the API commit (runbook §7).
- The validator memory limit goes from 2Gi to 3Gi, in `kubernetes.yaml` and in the chart values. CPU stays the same. `test_deployment_manifests.py` keeps the same topology.
- New validator setting: `VALIDATOR_PREVIEW_CAPTURE` (default `true`). It switches capture off without a code change.
- `architecture.md`: new decision **D7**, "The validator pod captures one picture per version in a sandboxed renderer". It records why, the rejected alternatives (browser-side capture, a renderer pod) and the cost. Amend the "no server-side rendering" intro and D1 to point to D7.
- Runbook (`hosted-applications/README.md`):
  - §1 topology (renderer process, same pods).
  - §3 the new setting.
  - §7 renderer sandbox, self-test and logs. §7 also gets a Chromium update rule:
    - Rebuild the validator image and bump the Chromium pin at least every 4 weeks.
    - Rebuild and bump at once when Chromium has a security release.
    - When Alpine drops the pinned version, the image build fails. That failure is the signal to bump the pin.
  - §10 rollout (manifests with a `preview` key, same-commit images; an API pod older than this change refuses a manifest with a `preview` key, so a rolling update has a short window of unavailable content and a rollback makes newer versions unavailable). Rollout precondition: the outside image pipeline must build `Dockerfile.validator` at the API commit and push it with the API image tag. Otherwise Chromium bumps reach production only through manual rebuilds. (Superseded by the MR !4837 review (AR-1); see the §4 note.)
  - §13 residual risks.

## Acceptance criteria

- AC1: An accepted version whose start page renders has `manifest.preview` pointing to a stored JPEG within 1 MiB and 2048 px. (Superseded by the MR !4837 review (AR-1); see the §4 note.)
- AC2: A crash, timeout, blank picture, failed check or missing reservation room gives a manifest with no `preview` key. The verdict, `report.json` and the audit are identical to a run without capture. (Superseded by the MR !4837 review (AR-1); see the §4 note.)
- AC3: A page that is quiet gets its picture as soon as it is quiet. A page that never gets quiet gets its picture at the soft deadline (15 s), unless that picture is blank. A hung browser is killed at the hard limit (20 s), with no preview. The job finishes within `validation_budget`. No capture starts when the time left is shorter than the hard limit.
- AC4: Rejected, failed and awaiting-configuration versions keep no picture.
- AC5: Tests show that the renderer cannot open an IP socket, read files outside its allowlist, or see the validator key. A failed self-test disables capture.
- AC6: `POLICY_VERSION` and `POLICY_DEFINITION` are unchanged. Application code runs only in the renderer process.
- AC7: Retire and purge delete `preview.jpg`. Its bytes count in the attempt ledger and in quota.
- AC8: The deployment diff changes only the validator image and its memory limit. The chart, `kubernetes.yaml` and the runbook agree.
- AC9: `architecture.md` has decision D7, and D1 and the intro refer to it.
- AC10: `parse_manifest` accepts today's version 1 manifests unchanged (no `preview` key gives `None`), and version 1 manifests with a valid `preview` object. It refuses a `preview` that is `null`, outside the prefix, out of bounds, or has an extra or a missing key, and it still refuses every other unknown key. (Superseded by the MR !4837 review (AR-1); see the §4 note.)
- AC11: The plan starts with one spike, before the renderer is built. The spike confirms that Chromium works when `socket(AF_INET)` returns `EPERM`. It also measures browser start time and capture time under the real validator pod limit (1 CPU) while ClamAV scans. It uses three pages: a static page, a typical React or Vue build, and a WebGL page. The spike result sets the soft deadline and the hard limit; 15 s and 20 s are the starting values. The plan records the measured values and the limits chosen from them.

## Non-goals

- Showing the picture, or changing the `/thumbnail` cover order (EPMCDME-17156).
- Interactive or live previews; more than one picture per version.
- Backfill for versions validated before this change.
- Changes to manual cover upload, replace or remove.
- Any API field, UI, report field or audit field about capture status.
- New pods, Deployments, Services, NetworkPolicies, images, job kinds or attempt kinds.
- Changes to the validation policy, `POLICY_VERSION`, the verdict rules or the output reservation formula.
- Network access for the rendered page, including the gateway's `WRAPPER_SCRIPT`.

## Left to the builder

The exact settle delay. The rlimit values. The name of the validator route. Whether the asset transfer overlaps the capture. The test seam in `TrustedValidatorSeam`. A skip marker for renderer tests when Chromium is not installed locally.
