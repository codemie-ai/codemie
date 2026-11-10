# Hosted applications: operations runbook

Feature ticket EPMCDME-15659. Users upload a single HTML file or a ZIP of a prebuilt static web app,
keep it private, share it, and open it inside CodeMie. The backend stores and serves static bytes
only: it never builds an artifact, never runs uploaded server code and starts no runtime per
application. Uploaded code runs in the viewer's browser on a separate, isolated content origin.

The feature is off by default. One switch turns it on for the UI and the API together: the
customer-config feature `features:hostedApplications` (section 3). While it is off, no hosted
background work runs, the UI never calls the hosted API, and platform sign-in and sign-out are
unchanged.

Architecture and data model: [`docs/hosted-applications/architecture.md`](../../docs/hosted-applications/architecture.md).

Files in this directory:

| File | Purpose |
|---|---|
| `kubernetes.yaml` | The objects next to the API release, with the chart's topology and names: the validator and clamd Deployments, their Services and NetworkPolicies (in `codemie-hosted-apps`) and the wildcard content Ingress. The chart (`features.tools.hosted-apps`) creates the same objects. |
| `Dockerfile.validator` | Validator image (non-root, hash-pinned dependencies). |
| `requirements-validator.txt` | The validator's complete Python dependency set, pinned with SHA-256 hashes. |
| `clamd.conf` | ClamAV daemon configuration (TCP on the clamd pod's network, admitted only from the validator by a NetworkPolicy; Unix socket; policy scan limits), mounted into clamd and the validator. |

## 1. Topology

```
            https://codemie.example.com                 https://v-<version>.apps-content.example.net
            (CodeMie UI, management API)                (one origin per version, wildcard DNS + TLS)
                          |                                              |
             existing API ingress                          wildcard content ingress (section 4)
                          |                                              |
                          +----------------------+-----------------------+
                                                 |  Host preserved
                                                 v
   +----------------------- API pods (every replica, unchanged count) -----------------------+
   |  HostDispatcher (outermost ASGI app)                                                    |
   |    Host in the content namespace -> closed content gateway (never management routes)    |
   |    any other Host (UI, probes by pod IP, in-cluster callers) -> management API          |
   |  validation workers + maintenance loop ----> codemie-hosted-apps-validator:8091         |
   |  HOSTED_APPS_STORAGE_BUCKET <- private S3 or GCS bucket, shared by every replica        |
   +-----------------------------------------------------------------------------------------+
        |                    |                                        |
   PostgreSQL       identity provider / JWKS        +-- namespace codemie-hosted-apps -------+
                                                    |  validator pod (one replica)          |
                                                    |    :8091 (service key only)           |
                                                    |    sandboxed parser child process     |
                                                    |          | only the validator         |
                                                    |          v                            |
                                                    |  clamd pod :3310 (freshclam) ---------+--> signature mirror
                                                    +---------------------------------------+
```

- Standalone viewing uses a trusted `w-<version>.apps-content.example.net` wrapper around the
  matching uploaded `v-` origin. Both use the existing wildcard content Ingress and certificate;
  neither runs management routes. The wrapper has no credentials or uploaded executable content.
- The upstream ClamAV entrypoint waits for `/run/clamav/clamd.sock`. Keep the configured private
  Unix socket even though the validator uses TCP. Removing it causes startup to stall.
- One backend serves both trees. `HostDispatcher` runs before any management middleware and decides
  by the `Host` header only. A Host equal to the content domain or any subdomain of it reaches only
  the closed content tree: a version host on the content port is served, every other content-namespace
  host gets a closed `404`, and while the feature is off or not ready the version hosts get a closed
  `503`. Every other Host reaches the management API unchanged, so kubelet probes addressed by pod IP,
  in-cluster callers such as `codemie:8080` and requests without a Host keep working.
- Forwarding headers (`Forwarded`, `X-Forwarded-Host`, `X-Original-Host`) never route. A request whose
  forwarding headers disagree with Host about the content namespace is refused with a closed `400`.
  On content hosts the only accepted forwarding header is one `X-Forwarded-Host` that repeats Host
  exactly, as ingress controllers send it.
- The validator and clamd run in **two private pods** (Deployments `codemie-hosted-apps-validator` and
  `codemie-hosted-apps-clamd` in the `codemie-hosted-apps` namespace), outside the API pods. A pod is ready
  only when all its containers are, so as sidecars ClamAV's signature load (minutes after every start)
  would keep API pods out of service, and a scanner compromise would share the API pod's network and
  service account. Neither pod has a service-account token; the validator holds no platform secret except
  the validator key. NetworkPolicies admit only API pods to the validator and only validator pods to clamd,
  whose TCP port has no authentication. The chart creates the clamd policy whenever hosted applications are
  enabled; it needs a CNI that enforces NetworkPolicy.
- **Exactly one validator replica.** A validation result lives in the pod that produced it, and the API
  fetches the validated files from that pod after `/validate`; a second replica behind the Service would
  receive some of those calls. While the pod restarts, validation pauses: the API stops claiming
  validation jobs while the validator is unhealthy, and queued uploads continue afterwards.
- Every API replica uses the same private object-storage bucket (`HOSTED_APPS_STORAGE_BUCKET`).

## 2. Prerequisites

| Area | Requirement |
|---|---|
| Content domain | A **separate registrable domain** (for example `apps-content.example.net` when CodeMie runs on `example.com`), never a subdomain of CodeMie, the identity provider or any other first-party site. Startup refuses a content domain that shares a registrable domain with `FRONTEND_URL`, `CALLBACK_API_BASE_URL`, the Keycloak URLs, JWKS issuers or token-exchange URLs. For `oidc` and `entraid-oidc` without JWKS validation, no setting names the identity provider host, so the content domain must also not share a registrable domain with the identity provider. |
| DNS and TLS | Wildcard DNS `*.<content domain>` to the ingress and a valid wildcard certificate for `*.<content domain>` on port 443. |
| Ingress | Section 4. Host preserved, no rewriting of `X-Forwarded-Host`, no caching or authentication on content hosts, large uploads allowed on the management host. |
| Identity | User management (`ENABLE_USER_MANAGEMENT=true`) with any `IDP_PROVIDER`, JWKS validation on or off. Section 5. |
| Storage | A dedicated private bucket on S3 (`FILES_STORAGE_TYPE=aws`) or GCS (`FILES_STORAGE_TYPE=gcp`), used by one deployment only. Azure is not supported yet. Section 6. |
| Validator nodes | The supplied pod uses **Linux amd64** nodes because the pinned upstream scanner image is amd64-only. The Python validator also supports aarch64, but using that architecture requires a separately built and qualified scanner. Nodes need **Landlock ABI 3 or later** (normally Linux 6.2 or later), with the runtime seccomp profile permitting Landlock system calls. The validator refuses to start without confinement (section 7). |
| Database | Alembic revisions `e3b7c1a95d42` and `f8d24a6c0b17` (head), applied through the normal migration step. The first adds the hosted tables to the core schema; the second changes shared platform tables: `HOSTED_APPLICATION` grants in `permissions`, the common `share_links` table, and `grant_source='name'` on existing assistant grants. |
| Validator image | Built from the **same commit** as the API image: both compute the same validation policy digest. |

No Redis or other new infrastructure is needed.

## 3. Configuration

### Turning the feature on

Set `enabled: true` on the `features:hostedApplications` component of the customer config
(`config/customer/customer-config.yaml`, or the customer ConfigMap that replaces it). The environment
variable `FEATURE_HOSTED_APPLICATIONS=true|false` overrides that value, like every `features:*` flag, but
only where the component exists: a customer ConfigMap must include the component. The UI reads the same
flag from `GET /v1/config`, so the UI and the API always agree. The flag is read at startup; restart the
API after changing it.

At startup the API checks the deployment. When the flag is on but the deployment cannot run the feature
safely, the feature stays off and one warning is logged:

```
Hosted applications are enabled but unavailable: <reason>
```

The reason names the failing setting, never a configured value. If the hosted runtime itself fails to
start, the API still starts, logs `Hosted applications failed to start and stay unavailable: <error
class>` at error level and serves the feature as off (`403 FEATURE_DISABLED` on management routes,
closed `503` on content hosts). The UI then shows only Platform apps.

Accounts that cannot use the feature (for example external or service accounts, or requests with no
usable sign-in token) see it as unavailable: capabilities answer `view: false` and hosted routes answer
`403 ACCOUNT_NOT_SUPPORTED`, never a `401` that would send them back to sign-in.

### Settings

All settings are environment variables read by the API; set them through the chart's `extraEnv`.
Secrets come from the platform secret manager (Kubernetes Secrets, External Secrets, Vault), never from
source control or plain values.

| Setting | Example | Notes |
|---|---|---|
| `HOSTED_APPS_CONTENT_DOMAIN` | `apps-content.example.net` | Required. Registrable domain, separate from every first-party domain; versions are served from `v-<version>.<domain>` on port 443. Setting it installs host dispatch even while the feature is off, so content hosts stay closed. Local development uses `apps.localhost:<backend port>` with `ENV=local`. |
| `HOSTED_APPS_CREDENTIAL_SECRET` | secret, at least 32 bytes | Required. HMAC key for content sessions, tickets, cursors and idempotency. Independent from every other secret. Read section 8 before rotating it. |
| `FILES_STORAGE_TYPE` | `aws` or `gcp` | Selects the hosted storage backend: `aws` uses S3, `gcp` uses GCS. `filesystem` uses `HOSTED_APPS_STORAGE_DIR` and is accepted only with `ENV=local` or `ENV=test`; elsewhere the feature stays off. `azure` keeps hosted applications unavailable (`Hosted applications do not support Azure storage yet`). |
| `HOSTED_APPS_STORAGE_BUCKET` | `codemie-hosted-apps` | Required for `aws` and `gcp`. A dedicated bucket, never the files bucket (`AWS_S3_BUCKET_NAME` or the per-owner GCS buckets). The API never creates it. One bucket per deployment (section 6). |
| `HOSTED_APPS_STORAGE_DIR` | `/mnt/hosted-applications` | Only for `FILES_STORAGE_TYPE=filesystem` (local development and tests). Absolute path; it cannot be `FILES_STORAGE_DIR` or inside it. |
| `HOSTED_APPS_STORAGE_S3_GRACE_SECONDS` | `30` (default) | S3 only. Not negative. S3 cleanup waits for the writer lease end plus this grace. Startup refuses S3 when \|S3 Date - database clock\| + \|S3 Date - pod clock\| exceeds it (log code `STORAGE_CLOCK_SKEW`). |
| `HOSTED_APPS_STORAGE_SWEEP_SECONDS` | `3600` (default) | Interval of the `hosted-storage-sweep` task (section 6). |
| `HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS` | `20` (default) | Time budget of one sweep run. |
| `HOSTED_APPS_VALIDATOR_URL` | `http://codemie-hosted-apps-validator.codemie-hosted-apps.svc:8091` | The validator Service of the chart or `kubernetes.yaml`, namespace-qualified because it runs in the hosted applications namespace, not the API's. Default `http://127.0.0.1:8091`, which suits local development only. No path, query or credentials. Plain HTTP stays inside the cluster, like the platform's other internal services; the NetworkPolicy admits only API pods and the key below authenticates the API. Use `https://` when the validator serves TLS. |
| `HOSTED_APPS_VALIDATOR_KEY` | secret, at least 32 characters | Required. Shared with the validator as `VALIDATOR_SERVICE_KEY`. |
| `HOSTED_APPS_QUOTA_BYTES` | `2147483648` (default) | Storage per creator: every retained version, covers and uploads in progress of the applications a user created, whoever uploads them. At least 1 MiB. |

The rest comes from settings the platform already has:

| Value | Source |
|---|---|
| UI origin (content `frame-ancestors`, CSRF checks, launch policy) | The origin of `FRONTEND_URL`, the origin the API already trusts for CORS. |
| Identity | `IDP_PROVIDER`, `ENABLE_USER_MANAGEMENT`, and optionally `JWKS_VALIDATION_ENABLED`, `JWKS_TRUSTED_ISSUERS`. Hosted data is keyed by the CodeMie user id; `JWT_ISSUER` is not read. Section 5. |
| Membership checks and people search | The CodeMie `users` table only. The feature calls no identity provider API. |

### Fixed limits

These values are constants in the code. Changing one is a code change, not a setting.

| Area | Value |
|---|---|
| Uploads | HTML up to 20 MiB; ZIP up to 100 MiB, 500 MiB unpacked, 50 MiB per file, 5,000 files. One upload may take 900 seconds and may stall for 60. |
| Per person | 500 applications (a deleted application stops counting at once), 4 uploads in progress, 2 validations at a time. A version can restart its upload 10 times; an application keeps at most 100 versions of any state. |
| Retention | 10 accepted versions per application, plus the live and selected versions. Rejected, failed or canceled versions 30 days, audit records 180 days; a deleted application's remaining row is removed after 180 days as well. |
| Sessions | Launch ticket 30 seconds, viewing session 15 minutes. When the platform checks token expiry, never longer than 24 hours after the sign-in it came from; otherwise never longer than 24 hours after the last request the platform accepted. |
| Workers | 2 validation workers per API process. Jobs are leased in PostgreSQL, so every replica can run them. A validation's time limit grows with the upload's size and file count. |
| Database | A separate pool of 10 connections per API process on the core database. A request that waits 2 seconds for a connection fails with `503 SERVICE_BUSY`; add the pool to the per-replica connection budget. |
| Identity directory | Membership decisions cached 5 seconds (4,096 per process); 12 concurrent membership lookups and 4 concurrent people searches per process. |
| Content capacity, per process | 64 requests in flight, at most 16 for one viewer; a request waits at most 5 seconds for a slot (then `503`, or `429` for a viewer at their share) and takes at most 120 seconds. |

There are no per-user request rate limits: floods from outside the platform are the edge's job, as for
every other CodeMie route (section 4).

### Validator environment

| Variable | Default | Notes |
|---|---|---|
| `VALIDATOR_SERVICE_KEY` | required | Same value as `HOSTED_APPS_VALIDATOR_KEY`. |
| `VALIDATOR_BIND_HOST` | `127.0.0.1` | `0.0.0.0` in the validator pod, so the Service reaches it. |
| `VALIDATOR_PORT` | `8091` | Listener port. |
| `VALIDATOR_TLS_CERT_FILE`, `VALIDATOR_TLS_KEY_FILE` | unset | Set both to serve HTTPS; the API then needs a certificate it trusts. Setting one without the other refuses to start. |
| `VALIDATOR_WORKSPACE` | `/work` | Scratch volume. The validator works in a private `jobs` directory inside it, recreated empty at every start and checked for owner and mode `0700`. |
| `CLAMD_HOST`, `CLAMD_PORT` | required, `3310` | `codemie-hosted-apps-clamd` and `3310`: the clamd Service in the same namespace. |
| `CLAMD_CONFIGURATION` | required | Path of the same read-only `clamd.conf` mounted into clamd. The validator refuses to scan when the policy limits in it do not match. |

The validator exposes `GET /livez` (unauthenticated, `204`, no body) for probes and keeps the detailed
`GET /health` (scanner version and signature age) behind the service key; the API uses `/health` to
decide whether to claim validation jobs. The image health command is
`python -B -m codemie.hosted_apps_validator.server --probe`.

## 4. Ingress

### Content hosts (wildcard)

Route `*.<content domain>`, and only that, to the API Service with the wildcard certificate.
`kubernetes.yaml` has a controller-neutral Ingress with no annotations. Configure your controller so that
it:

- keeps Host, path and query unchanged;
- sends `X-Forwarded-Host` only as an exact copy of Host, or not at all, and no `Forwarded` or
  `X-Original-Host` header; anything else is refused with a closed `400`;
- allows responses of 130 seconds (the gateway bounds a request to 120) and request bodies of 64 KiB
  (only the small launch form `POST /_platform/launch` is posted here);
- adds no authentication proxy (oauth2-proxy), rewrite, shared cache, CORS, SPA fallback, static-file
  alias, CDN or public bucket for hosted bytes;
- logs no request or response bodies and redacts `Cookie`, `Set-Cookie` and `Authorization`: tickets
  travel in POST bodies and content sessions in cookies.

The community ingress-nginx controller was retired in March 2026 and no longer receives security fixes.
The CodeMie chart's own ingress uses its annotations; whichever maintained controller replaces it for
the platform should serve the content hosts too, with the behavior above.

### Management host (existing API ingress)

- Artifact uploads (`PUT /v1/hosted-applications/{app}/versions/{version}/artifact`) carry up to
  100 MiB: set the request body limit to at least `110m`.
- Set read and send timeouts to at least `900` seconds, the upload time limit, and stream request
  bodies to the backend instead of buffering them on the ingress disk where the controller allows it.
- Other hosted JSON requests are capped at 32 KiB by the API itself, also under a root path such as
  `/code-assistant-api`, and their `408`/`413` refusals carry the normal CORS headers. Card cover
  uploads (`PUT /v1/hosted-applications/{app}/cover`, a raw PNG, JPEG or WebP body) are capped at 1 MiB
  (`413 COVER_TOO_LARGE`).
- Edge rate limits per client for `/v1/hosted-applications*` and the content
  host are recommended where the platform already uses them; the API has none of its own.

### Probes and network

Kubelet HTTP probes reach the API by pod IP, which is outside the content namespace, so the existing
`/v1/healthcheck` startup, liveness and readiness probes keep working with host dispatch enabled. The
validator pod's probes use `GET /livez`. The API pods need egress to the validator Service on port 8091;
the validator pod needs only DNS and clamd on port 3310, and the clamd pod only DNS and HTTPS to the ClamAV
signature mirror (prefer a private mirror).

## 5. Identity

Hosted endpoints require a verified identity. The API records it after the platform has authenticated
the request and loaded the user. Hosted applications trust CodeMie's own authentication and need no
identity provider API.

- Set `ENABLE_USER_MANAGEMENT=true`. Without it, startup refuses the feature with
  `Hosted applications need user management (ENABLE_USER_MANAGEMENT=true)`.
- Every `IDP_PROVIDER` works (`local`, `keycloak`, `oidc`, `entraid-oidc`), with JWKS validation on or off.
- The principal is the CodeMie user id. Hosted tables and sessions are keyed by it, not by the token issuer.
- Access checks read the `users` and `user_projects` tables: a row with `is_active` true,
  `deleted_at` empty and `user_type` `regular`, and the user's project memberships. The feature uses no `KEYCLOAK_ADMIN_*` setting for
  identity or the directory, and has no sharing-audience setting: access follows the platform's projects. `KEYCLOAK_ADMIN_URL`, when set, still counts
  as a first-party host for the content-domain check.

The hosted identity comes from the token that the platform accepted. Only a token whose signature was
verified (`local_jwt`, `jwks`) supplies claims: its `iat`, `exp`, `sid` and `auth_time` bound the viewing
session. A `platform_jwt` token supplies none (see below):

| Provider | Token |
|---|---|
| `local` | The validated local JWT. |
| `keycloak` | The access token in `x-auth-request-access-token`. |
| `oidc`, `entraid-oidc` | The token in `Authorization: Bearer`. |
| Any provider with JWKS validation on | The token that the JWKS validator verified. |

The `app.launch` audit entry records how the token was accepted: `local_jwt`, `jwks` or `platform_jwt`.

Hosted applications check token expiry exactly when the platform does:

- **`local_jwt` and `jwks`.** The platform refuses an expired token, and so do hosted applications. The
  token's `exp` ends the viewing session, and `auth_time` limits it to 24 hours after the sign-in.
- **`platform_jwt`** (`keycloak`, `oidc` and `entraid-oidc` without JWKS validation). The platform accepts
  the forwarded token whatever its `exp`, and oauth2-proxy decides how long the sign-in lasts. The API does
  not verify that token's signature, so hosted applications take **no claim** from it: no `sid`, `iat`,
  `auth_time` or `exp`. A caller who controls the header therefore chooses nothing that revocation relies
  on. The hosted identity binds to the token itself (its digest), starts when the platform accepted the
  request, and each request the platform accepts keeps it valid for another 24 hours. Viewing sessions still
  last 15 minutes, a new one starts only from the CodeMie UI, and sign-out ends every session of the person
  at once through the per-person cutoff, which needs no `sid`.

If the token is missing, is not a JWT, or does not bind, the request fails with `ACCOUNT_NOT_SUPPORTED`.
The API logs `Hosted applications found no usable sign-in token: cause=<cause> IDP_PROVIDER=<provider>`
at most once per 10 minutes per cause per process. The cause is `missing`, `not_jwt`, `expired`,
`future_iat` (`iat` or `auth_time` ahead of the API clock), `bad_sid` or `bad_claims` (these four for a
`local_jwt` or `jwks` token only), or `bad_principal`.

- **Tokens without `sid`.** A viewing session binds to the verified token's `sid`, or to the token digest
  when there is no `sid` or the token was not verified (`platform_jwt`). Keycloak tokens carry `sid`; Entra ID
  access tokens and many OIDC access tokens do not. Without a `sid`, a `local_jwt` or `jwks` viewing session lasts one token lifetime, and an open
  application stops at the `exp` of its first token.
- **No leeway after `exp`.** The JWKS validator accepts a token for up to `JWKS_LEEWAY_SECONDS` after
  its `exp`. Hosted applications refuse it at `exp`. In that window the platform answers and hosted
  endpoints return `ACCOUNT_NOT_SUPPORTED` with the cause `expired`, until the client gets a new token.
- **No JWKS validation.** For `keycloak`, `oidc` and `entraid-oidc` without JWKS validation, the API
  trusts the token that oauth2-proxy forwards without checking the signature again, and therefore uses
  none of its claims (only that it is a JWT, and its digest). This is the platform's documented trust model (`docs/jwks-configuration-reference.md`). The API
  must be reachable only through the ingress. For `local`, the API checks the signature of the local JWT.
- **Content domain.** The content domain must not share a registrable domain with the identity provider.
  Startup does not check this for `oidc` and `entraid-oidc` without JWKS validation.
- **Known gap.** A user who is disabled or deleted only in the identity provider stays active in `users`
  until a CodeMie admin deactivates them. That user cannot sign in to CodeMie again, so cannot start a
  new viewing session. Existing access ends with the current viewing session or at sign-out. Project
  membership still lets that user open applications shared with their projects until a CodeMie admin
  deactivates them or removes the membership.

Only regular accounts use the feature. Access is checked on the database when someone opens, lists or manages an application;
an open viewing session is not re-checked against the directory and ends within 15 minutes, at the
expiry of the sign-in it came from, or at sign-out.

**Issuer changes.** Hosted code does not read `JWT_ISSUER` and puts no requirement on its length. `users.id` is the subject (`sub`) of the person's token, not the issuer, so changing the issuer (a new Keycloak hostname, a Keycloak upgrade) keeps the same users as long as the identity provider keeps issuing the same subject for each person: every application they created and their per-user state stays visible and no migration is needed. A new realm or provider that issues different subjects creates new users, who do not see the old applications.

**Marketplace.** Publishing to the marketplace (`is_global`) lists the application for every eligible internal user in the `users` table (active, not deleted, not external; service accounts included); it is not limited to a CodeMie project, team or tenant. Sharing (`shared`) reaches only the members of the application's project. Several mutually isolated customers in one deployment must not publish to the marketplace.

## 6. Storage

- Use a dedicated private bucket. `FILES_STORAGE_TYPE` selects the backend: `aws` uses S3, `gcp` uses
  GCS. The API takes credentials and region from the files-storage settings: S3 uses `AWS_S3_REGION`
  (then `AWS_DEFAULT_REGION`) and the default credential chain; GCS uses the default credentials.
  A custom S3 endpoint (MinIO, Ceph) is not supported.
- Never share the bucket with generic CodeMie files (`AWS_S3_BUCKET_NAME` or the per-owner GCS
  buckets). The API never creates buckets: create it before the rollout.
- **One bucket per deployment.** Never point two deployments at one bucket. First, both read the same
  `.hosted/storage-id`, bind the same namespace, and each sweep deletes the objects of the other.
  Second, a database restored to an older point turns newer attempts into orphans, and the sweep
  removes them after 1 day (see Backup and restore in section 12).
- Bucket settings: versioning off, no Object Lock, public access blocked. Never expose the bucket
  through a static-file server, and never grant it to the validator pod.
- Lifecycle rules: on S3, abort incomplete multipart uploads after 1 day. On GCS, delete objects under
  `.staging/` and `.fences/` after 1 day.
- IAM, S3: `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`,
  `s3:ListBucketMultipartUploads`, `s3:AbortMultipartUpload` and `s3:ListMultipartUploadParts` on the
  bucket. IAM, GCS: the bucket object role `roles/storage.objectUser` on the bucket. The API needs no
  `storage.buckets.get` and no Token Creator role: it uses no signed URLs and no `signBlob`.
- Reserved keys: `.hosted/storage-id` (the bucket marker, never delete it), `.fences/` and
  `.staging/` (GCS write state). Preserve them. Storage attempts are bound to the marker; an empty or
  different bucket denies reads, writers and deletion proofs instead of releasing quota.
- S3 writes are multipart uploads signed against the database writer lease. Keep the clocks of the pods
  and the database in sync: startup refuses S3 when the clock skew exceeds
  `HOSTED_APPS_STORAGE_S3_GRACE_SECONDS`.
- Cleanup is resumable and bounded. The runtime task `hosted-storage-sweep` runs every
  `HOSTED_APPS_STORAGE_SWEEP_SECONDS`. It deletes objects older than 1 day under prefixes of `deleted`
  attempts or with no attempt row, skips the reserved keys, and logs an ERROR
  `hosted_storage_orphans_deleted` with the key count. One replica walks the bucket per period: each run takes
  the platform's advisory leader lock (`HOSTED_STORAGE_SWEEP_LOCK_ID`, 987654340) and holds one core-pool
  connection for at most `HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS`; the other replicas skip that period.
- The filesystem adapter (`FILES_STORAGE_TYPE=filesystem`) is for local development and tests. It is
  not supported in production. `azure` keeps hosted applications unavailable.
- A slow or unavailable bucket stalls hosted requests, not the rest of the API: the object backends use
  their own executor.
- Card covers set by a manager are not in the bucket: they live in the database table
  `hosted_application_covers`, at most one image of up to 1 MiB per application, so database backups include
  them. Cover bytes count toward the creator's quota: a cover that would exceed it is refused with
  `409 QUOTA_EXCEEDED`, and a replacement is charged only the size difference. Removing it
  refunds its bytes, and deleting the application removes and refunds it with the application's other
  rows.

## 7. Validator and scanner

- Build from the repository root with the same commit as the API image:
  `docker build -f hosted-applications/Dockerfile.validator -t <registry>/codemie-hosted-validator:<commit> .`
  The image uses a digest-pinned Python Alpine base, installs only the hash-pinned
  `requirements-validator.txt` and runs as UID 65532.
- The scanner is the upstream `clamav/clamav` `_base` image (Alpine, amd64, no bundled signatures),
  used without a derived build and pinned by digest in `kubernetes.yaml`. Scan the validator image
  after building and the pinned scanner image at release time; when a scan reports a fixed
  vulnerability, move to a newer upstream digest. To pull from a private registry, copy that digest
  into it unchanged. Publish the validator through the normal image pipeline and replace its manifest
  placeholder with an immutable digest.
- Deploy it with the chart or `kubernetes.yaml`: one replica, `Recreate` strategy, no service-account token, read-only
  root filesystem, all capabilities dropped, no privilege escalation, `RuntimeDefault` seccomp, CPU and
  memory limits, and a size-limited `emptyDir` at `/work`. Never mount user workspaces, cloud
  credentials, the Docker socket or API secrets other than the validator key into it.
- The parser child installs Landlock and seccomp filters and sets resource limits for itself. At start
  the validator runs that sandbox once on a harmless probe and **exits** unless network, reads and writes
  outside its workspace and program execution are all refused. On a node without Landlock ABI 3 the pod
  crash-loops visibly; the API keeps serving and uploads wait. Schedule the pod on qualified nodes.
- clamd runs from the reviewed upstream ClamAV image (1.4 LTS or 1.5), which starts as root to
  prepare its directories and runs clamd and freshclam as its `clamav` user. Mount `clamd.conf`
  read-only into both pods (`kubectl -n codemie-hosted-apps create configmap codemie-hosted-apps-clamd --from-file=clamd.conf=…`;
  the chart creates it). Run clamd with `TZ=UTC` (the chart and `kubernetes.yaml` set it): clamd reports its
  signature date in local time and the validator reads it as UTC, so another time zone makes every scan fail
  as stale or future-dated.
  Its scan limits are part of the immutable validation policy; a missing or different configuration
  makes validation unavailable, never accepted. Signatures must be newer than 48 hours.
- A file that exceeds ClamAV's own scan limits is rejected as too large or complex to scan, never
  reported as malware and never accepted.
- `clamd.conf` binds clamd to `0.0.0.0:3310` in its own pod; only the NetworkPolicy keeps other pods out, so
  never deploy clamd without it. For local development, publish a standalone clamd container's port on host
  loopback only; `TCPAddr` is not part of the policy check. The local validator container needs
  `VALIDATOR_BIND_HOST=0.0.0.0` and `docker run -p 127.0.0.1:18091:8091`, with
  `HOSTED_APPS_VALIDATOR_URL=http://127.0.0.1:18091`.
- Signatures are refreshed by freshclam in the clamd container (or by a controlled private mirror); the
  parser never has network access. Monitor signature age. freshclam checks for new signatures roughly
  every 6 hours (`FRESHCLAM_CHECKS=4`). Separately, the clamd container's liveness probe restarts the
  container if freshclam is not running (a dead or zombie process), not only if clamd itself stops
  responding to `--ping`.
- Test clean, EICAR, timeout, oversized and stale-signature cases after every image change.

## 8. Secrets and rotation

| Secret | Used by | Rotation |
|---|---|---|
| `HOSTED_APPS_CREDENTIAL_SECRET` | API | Maintenance window only (see below). |
| `HOSTED_APPS_VALIDATOR_KEY` / `VALIDATOR_SERVICE_KEY` | API and validator pod | Update the Secret, restart the validator pod, then the API pods; validations in between wait and continue. |

**Known limitation: rotating `HOSTED_APPS_CREDENTIAL_SECRET`.** Every hosted credential digest is an
HMAC under this secret. A new secret invalidates all open content sessions and launch tickets,
pagination cursors and idempotency records (a retry of an in-flight mutation is treated as a new
request). It also changes the binding digest of SID-bound sign-ins: a sign-in that was revoked before
the rotation no longer matches its record and could open hosted content again until its token expires.
Until key versioning exists, rotate like this:

1. Announce a maintenance window and turn the feature off (`FEATURE_HOSTED_APPLICATIONS=false`, then
   restart the API) or disable the content ingress.
2. Deploy the new secret to every replica.
3. Set a global revocation cutoff so that no credential issued before the rotation is accepted:

   ```sql
   BEGIN;
   UPDATE hosted_application_user_state
      SET auth_revoked_before = GREATEST(auth_revoked_before, clock_timestamp() + interval '5 seconds'),
          revision = revision + 1,
          updated_at = clock_timestamp();
   UPDATE hosted_application_view_sessions SET revoked_at = clock_timestamp() WHERE revoked_at IS NULL;
   COMMIT;
   ```

4. Turn the feature back on. Users sign in to CodeMie again before opening hosted applications.

Never log or print any of these secrets. The configuration dump masks settings whose names end in
`SECRET` or `KEY`.

## 9. Sign-out and session revocation

Platform sign-out never depends on hosted revocation. `POST /v1/local-auth/logout` and
`GET /v1/user/log_out` finish the platform sign-out first (token cleanup, cookie deletion, redirect),
then end every hosted session of the signed-out user, on every device, within 2 seconds at most. The
SSO route revokes only for a same-origin or same-site request, the same rule it uses for token cleanup.

This revocation is best effort. Failures are logged as warnings by exception class and counted in
`hosted_applications_logout_revocations`; when it fails (for example during a database outage), open
content sessions end at their own expiry, at most 15 minutes later. A sign-out performed only at the
identity provider, without a back-channel signal, is bounded by token and session expiry in the same way.

## 10. Rollout

1. Apply the Alembic revision with traffic gated. The platform-sharing revision (`f8d24a6c0b17`) adds the
   `permissions.source_link_id` foreign key to `share_links` only when an unqualified `permissions` resolves on
   the migration's `search_path` (`to_regclass('permissions')`); a hosted-only schema without that table skips
   it silently. After the upgrade, check that `permissions_source_link_id_fkey` exists on the platform
   `permissions` table; if it is missing, set the platform schema on the search path and add it by hand.
2. Provision DNS, TLS, the bucket (with its settings, lifecycle rule and IAM role from section 6), the
   Secret, the clamd ConfigMap and the objects in `kubernetes.yaml`; add the hosted settings to the
   chart values.
3. Turn the feature on in one environment; confirm there is no `Hosted applications are enabled but
   unavailable` warning, that the validator pod is ready, and that probes and in-cluster callers still
   reach the API.
4. Decide on audit: grant and share-link changes (share, unshare, link create, revoke and open) are recorded
   only as platform activity events, which are off by default. Set `ACTIVITY_EVENTS_ENABLED=true` where an
   operator must be able to tell who was given access by name or by link; renames, publishes and suspensions
   are in the hosted audit either way.
5. Pilot private uploads, then selected and everyone-with-access publishing.
6. Before production traffic, test with real browsers: embedded viewing with partitioned cookies,
   the "Open in new tab" fallback, sign-out, every HEAD/range/conditional request, and the load
   targets of the deployment.

## 11. Monitoring

The OpenTelemetry meter `codemie-business-metrics` exports fixed-label hosted instruments; labels never
contain users, applications, versions, paths or file names.

| Instrument | Labels | Meaning |
|---|---|---|
| `hosted_applications_content_requests` | `status`, `route`, `result` | Every content response. `result`: `served`, `denied` (401/403), `rejected` (other 4xx), `throttled` (429), `unavailable` (5xx). |
| `hosted_applications_content_denials` | same | Only `denied` and `rejected`. Throttling and outages are never denials. |
| `hosted_applications_content_unavailable` | same | Only `throttled` and `unavailable`. |
| `hosted_applications_content_seconds`, `hosted_applications_content_bytes` | same | Latency and bytes sent. |
| `hosted_applications_directory_seconds` | `result` | Membership lookup latency: `allowed`, `denied`, `unavailable`. |
| `hosted_applications_directory_searches` | `source`, `page`, `outcome` | People searches; never the query text. |
| `hosted_applications_validation_failures` | `kind` | `artifact_rejected` or `service_unavailable`. |
| `hosted_applications_logout_revocations` | `result` | `revoked`, `failed`, `timeout`. |
| `hosted_applications_completed_work`, `hosted_applications_deferred_work`, `hosted_applications_work_seconds` | `operation` | Durable worker steps. |
| `hosted_applications_queue_jobs`, `hosted_applications_oldest_queue_seconds` | `kind`, `state` | Queue depth and age. |
| `hosted_applications_stored_bytes`, `hosted_applications_reserved_bytes`, `hosted_applications_snapshot_age_seconds` | none | Storage accounting and snapshot freshness. |

Unexpected failures on hosted management routes return `503 APPLICATION_UNAVAILABLE` with an
`error_id` and are logged at error level with that id, the route template and the stack trace. Content
host failures (`5xx`) are logged the same way with an `error_id`, without paths. Request bodies and
credentials are never logged.

Initial alert suggestions (tune per deployment): sustained `unavailable` or `throttled` results,
spikes of `denied`, directory `unavailable`, `logout_revocations` with `failed` or `timeout`, queue age
above 5 minutes (for example while the validator pod is down), snapshot age above 120 seconds, growing
reserved bytes, scanner signature age approaching 48 hours, and the clamd container's restart count
together with its `Liveness probe failed: freshclam is not running` event.

## 12. Operations

**Taking an application down.** A platform administrator suspends an application at once, from the
application's actions in the UI or through the API below. There is no list of suspended applications: an
administrator reaches one outside their own projects by its URL (`/apps/<id>/manage`) or id.

**Stopping a leaked share link.** A platform administrator or maintainer revokes the link without suspending
the application: `DELETE /v1/hosted-applications/{id}/share-link`. Everyone who joined through the link loses
access on their next request, the revoke is audited with the administrator as actor, and a later reinstate does
not bring the link back. Suspension sets the application's security state to suspended: viewers lose access on their next request, open viewing sessions end, and managers (the creator, project admins and platform administrators) keep access. Sharing and the marketplace flag are left as they were:

```sh
curl -X POST "$CODEMIE_API/v1/admin/hosted-applications/<application id>/security/suspend" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" -d '{"reason": "Phishing report"}'
```

The application id is the one in its CodeMie link (`/apps/<id>`). Suspending a suspended application
changes nothing. `reinstate` instead of `suspend` clears a suspension (`409 NOT_SUSPENDED` otherwise);
the application returns exactly as it was suspended, with its sharing and marketplace flags, and the
creator cannot reinstate their own application (`INDEPENDENT_REVIEW_REQUIRED`). A reinstate may name an
accepted version to make live. Suspend takes an optional `If-Match` header with the application ETag (a stale value
is refused); reinstate requires it (`428 PRECONDITION_REQUIRED` without it), because it may change the live version.
Read the ETag from `GET /v1/hosted-applications/<id>` first.

**Validation policy.** The validation policy versions under which app versions may be served are a constant in
the code (`core/hosted_applications/policy.py`), so a policy change ships with a release. To stop
serving everything at once in an incident, turn the feature off.

**Failed jobs and cleanup.** Transient validation failures retry at most 3 times with backoff; a
validator that is down or busy does not use up those retries. Hard artifact failures need a corrected
upload. Cleanup proves deletion in the bound bucket before releasing quota; unavailable storage or a
wrong bucket marker keeps deletion pending and charged. Never zero counters or force a purge state by
hand.

**Backup and restore.** Back up the database and the hosted bucket, including `.hosted/storage-id`, as
a pair, with mutations and workers stopped or a consistent snapshot. Use one bucket per deployment: a
restore never reuses a bucket that another deployment writes to. Restore the database to the same
bucket and marker. A database restored to an older point turns newer attempts into orphans, and the
sweep removes them after 1 day. Two deployments on one bucket read the same `.hosted/storage-id`, bind
the same namespace, and each sweep deletes the other's objects. The filesystem adapter is not
supported in production. A restore returns applications,
versions, sharing, marketplace listings and suspensions to the backup point. Keep the feature off after a restore until both
steps below have run, in this order:

1. The revocation cutoff from section 8, so that no restored viewing session or ticket is accepted.
2. The access withdrawal below, so that sharing and marketplace listings removed after the backup cannot
   come back. Creators then share or publish again; administrators re-apply any suspension made after
   the backup.

```sql
BEGIN;
UPDATE hosted_applications
   SET shared = false, is_global = false,
       access_revision = access_revision + 1, revision = revision + 1, updated_at = clock_timestamp()
 WHERE lifecycle_state = 'active';
COMMIT;
```

**Signature updater failure.** The Kubernetes signal to alert on is the clamd container's restart
count, or the `Liveness probe failed: freshclam is not running` event/message. A restart caused by
this probe restarts both clamd and freshclam together (the accepted T14 trade-off: a short scanner
gap, and a start-up race where clamd may load a signature file freshclam is mid-write on); no action
is needed if freshclam recovers automatically (it checks roughly every 6 hours and the probe retries
on its own). The `signatures` volume is a container-restart-surviving (not pod-restart-surviving)
`emptyDir`, so a probe-triggered restart does not clear a mirror cool-down and sends no new downloads
during one. The exact freshclam log wording for an active cool-down is not verified against this
repo's pinned upstream image; confirm the live log text in `kubectl logs` during your own incident
rather than relying on an invented string here. Recovery: do not clear or delete the `signatures`
volume or cool-down state to "fix" a repeated restart before the underlying mirror cool-down or
network block is resolved — doing so forces a full re-download and can trigger the mirror's rate
limit again.

**Incidents.** Turn the feature off or suspend the application. Never weaken the content CSP or
authentication, or make storage public, to restore availability.

## 13. Residual risks

- **Exfiltration by uploaded code.** Content runs with `connect-src 'self'` and a sandboxed CSP, but
  browsers cannot fully prevent WebRTC (STUN/TURN) or DNS-prefetch based egress. A malicious
  application can send out what a viewer types into it. It cannot read CodeMie cookies, tokens or
  storage, which live on another registrable domain.
- **In-frame phishing.** An application can draw a fake CodeMie sign-in form inside the viewer. The
  native viewer frame labels uploaded content and the content domain differs from CodeMie, but users
  can still be deceived. Suspension is the control; teach users that CodeMie never asks for
  credentials inside an application.
- **ClamAV parses hostile input outside the parser sandbox.** It runs in its own pod with no platform
  credentials, no service-account token and NetworkPolicies that admit only the validator to its
  unauthenticated port and block its egress to cluster-internal and metadata addresses. Without an
  enforcing CNI any pod could reach that port (and stop the scanner). Keep its image patched.
- **One validator replica.** Validation pauses while the pod restarts or its node fails; viewing is
  unaffected.
- **Sign-out during an outage.** Revocation is best effort (section 9): an open content session can last
  up to 15 minutes when the revocation write fails.
- **Upstream sign-out.** Without an identity-provider back-channel logout signal, a sign-out performed
  only at the identity provider is bounded by token and session expiry.


### Capacity policy revision

Build API and validator from the same source revision for `static-v4`; mount the reviewed `clamd.conf` with `MaxScanTime 300000` into both scanner and validator. The validator transport allows 310 seconds per scan; the complete job is still capped at 900 seconds and two jobs. Its budget includes scanning the original archive and every extracted file. Previously accepted exact-digest v3 versions continue to be served. Do not bypass `AlertExceedsMax`, stale-definition checks or clean-verdict requirements. Qualify the full 100 MiB uploaded / 500 MiB expanded envelope with two admitted jobs on the actual scanner CPU and filesystem.
