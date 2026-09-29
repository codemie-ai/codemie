# Technical Research

**Task**: file download auth idor security share conversation
**Generated**: 2026-08-20T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

IDOR — Cross-User File Disclosure via forgeable file-download URLs. The endpoint GET /v1/files/{file_name} has no authentication dependency unlike every other endpoint and performs no ownership check. The file_name path parameter is a base64-encoded [mime_type, owner, name] triplet produced by StringSerializer — a non-cryptographic, unsigned, fully forgeable encoding. The handler trusts the client-supplied owner value and reads the file straight from storage with no verification. Fix required: (1) enforce authorization on GET /v1/files/{file_name} so a requester may retrieve a file only if they own it OR the file belongs to a conversation they own OR the conversation was explicitly shared with them; (2) make file-download URLs unforgeable (e.g. HMAC-signed token or equivalent); (3) preserve public Share Chat rendering for actually-shared conversations without requiring login, scoped only to files belonging to that shared conversation. Affected areas: file-download endpoint, FileObject.to_encoded_url/from_encoded_url, StringSerializer, and the public share conversation flow /v1/share/conversations/{token}.

---

## 2. Codebase Findings

### Existing Implementations

**File download endpoint (the vulnerable handler):**
- `/Users/kyrylo_korotych/codemie/src/codemie/rest_api/routers/files.py` — `GET /v1/files/{file_name}` handler (`read_file`, ~line 195). The router is declared with `dependencies=[]` and the handler carries no `Depends(authenticate)`. Every other handler in this file (`POST /files/`, `POST /files/bulk`, `POST /files/diagram/mermaid`) has explicit `Depends(authenticate)`.

**File encoding layer:**
- `/Users/kyrylo_korotych/codemie/src/codemie_tools/base/file_object.py` — `FileObject` pydantic model with `to_encoded_url()` (line ~147) and `from_encoded_url()` (line ~156). `to_encoded_url` delegates to `StringSerializer.serialize([self.mime_type, self.owner, self.name])`. `from_encoded_url` delegates to `StringSerializer.deserialize` and unpacks the `(mime_type, owner, name)` triplet with no signature check.
- `/Users/kyrylo_korotych/codemie/src/codemie_tools/base/string_serializer.py` — `StringSerializer` custom class. Encoding format: each string gets a length prefix (`{len}~{word}`), then the result is `base64.b64encode`d. No HMAC, no secret, no expiry. Also supports a legacy `_`-split fallback (line ~62) for backward compatibility. Fully reversible by any caller — the attack surface for token forgery.

**File service:**
- `/Users/kyrylo_korotych/codemie/src/codemie/service/file_service/file_service.py` — `FileService.get_file_object(file_name)` (line ~22). Decodes the token via `FileObject.from_encoded_url(file_name)`, then calls `file_repo.read_file(file_name=file_object.name, owner=file_object.owner, mime_type=file_object.mime_type)`. The `owner` is taken from the attacker-controlled token, not from the authenticated session.

**File storage repositories:**
- `/Users/kyrylo_korotych/codemie/src/codemie/repository/base_file_repository.py` — abstract `FileRepository` base class.
- Concrete implementations selected by `FileRepositoryFactory` in `/Users/kyrylo_korotych/codemie/src/codemie/repository/repository_factory.py` based on `config.FILES_STORAGE_TYPE`: `filesystem` (local disk), `aws` (S3 via boto3), `azure` (Azure Blob Storage), `gcp` (GCP Cloud Storage). All use `{owner}/{filename}` as the path/key layout.

**Share conversation flow:**
- `/Users/kyrylo_korotych/codemie/src/codemie/rest_api/routers/share.py` — `POST /v1/share/conversations` (line ~39) and `GET /v1/share/conversations/{token}` (line ~65). Both endpoints currently require `Depends(authenticate)` — there is no public/unauthenticated share rendering path in the current codebase, despite the task description mentioning one.
- `/Users/kyrylo_korotych/codemie/src/codemie/service/share_conversation_service.py` — `ShareConversationService`. `get_shared_conversation(token, user)` fetches `SharedConversation` by `share_token`, then loads the full `Conversation` including its `history` (which contains `file_names` — encoded file URL lists per message).
- `/Users/kyrylo_korotych/codemie/src/codemie/rest_api/models/share/shared_conversation.py` — `SharedConversation` model (table `shared_conversations`): fields `share_id`, `conversation_id` (indexed), `shared_by_user_id` (indexed), `share_token` (12-char random alphanumeric via `secrets.choice`, indexed), `access_count`, `created_at`. The `share_token` is cryptographically random but not a signed JWT — it is an opaque lookup key with no expiry.

**Conversation and ownership model:**
- `/Users/kyrylo_korotych/codemie/src/codemie/rest_api/models/conversation.py` — `Conversation` (table `conversations`): `user_id` (indexed owner), `history: List[GeneratedMessage]`. `GeneratedMessage.file_names: Optional[List[str]]` stores the encoded file URLs of files attached to messages. `Conversation.is_owned_by(user)` returns `self.user_id == user.id`. `Conversation.is_shared_with(user)` returns `False` — a stub not yet implemented. This is the linkage required to verify a file belongs to a conversation: the file's encoded URL can be checked against `history[*].file_names`.

**Ownership / Ability framework:**
- `/Users/kyrylo_korotych/codemie/src/codemie/core/ability.py` — `Ability`, `Action`, `Role`, `Owned` pattern. `PERMISSIONS` registry covers `Conversation` (READ: `[SHARED_WITH, OWNED_BY]`), `Assistant`, `WorkflowConfig`, etc. There is no `FileObject` or file resource entry in `PERMISSIONS`. File access bypasses the Ability system entirely.

**Authentication infrastructure:**
- `/Users/kyrylo_korotych/codemie/src/codemie/rest_api/security/authentication.py` — `authenticate` FastAPI dependency (line ~95). Handles JWT/IdP flows, internal loopback bind-key (HMAC-SHA256 with nonce and 30-second timestamp window via `sign_internal_request` / `_verify_internal_request`), and stores the resolved `User` in `request.state.user`. The `hmac` stdlib module is already imported here. The internal bind key is an ephemeral `secrets.token_hex(32)` generated at process startup — not configurable.

**HMAC reference implementation:**
- `/Users/kyrylo_korotych/codemie/src/codemie/triggers/bindings/github_webhook_security.py` — production-quality HMAC-SHA256 verification using `hmac.compare_digest`. This is the established pattern for constant-time HMAC comparison in the codebase.

**Config:**
- `/Users/kyrylo_korotych/codemie/src/codemie/configs/config.py` — single `Config(BaseSettings)` class. Contains `MCP_AUTH_HMAC_SECRET: str = ""` (validated to be ≥ 32 bytes when `MCP_AUTH_ENABLED=True`) and `FILES_STORAGE_TYPE`. There is no `FILE_DOWNLOAD_HMAC_SECRET` or equivalent for signed file tokens.

**Callers of `to_encoded_url` / `from_encoded_url` (full call surface affected by token format change):**
- `/Users/kyrylo_korotych/codemie/src/codemie_tools/data_management/file_system/generate_image_tool.py`
- `/Users/kyrylo_korotych/codemie/src/codemie_tools/data_management/code_executor/file_export_service.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/datasource/loader/binary/image_loader.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/core/utils.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/workflows/nodes/agent_node.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/workflows/workflow.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/agents/assistant_agent.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/agents/langgraph_agent.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/service/mcp/toolkit.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/service/workflow_service.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/service/agent_workspace_service.py`
- `/Users/kyrylo_korotych/codemie/src/codemie/service/file_service/image_service.py`

Any change to the token format (e.g. appending an HMAC) will affect all 12 call sites, plus all stored file URL values in existing conversation histories in the database/Elasticsearch index.

### Architecture and Layers Affected

| Layer | Component | File |
|---|---|---|
| API Router | `GET /v1/files/{file_name}` handler | `src/codemie/rest_api/routers/files.py` |
| API Router | Share conversation endpoints | `src/codemie/rest_api/routers/share.py` |
| Service | `FileService.get_file_object` | `src/codemie/service/file_service/file_service.py` |
| Service | `ShareConversationService` | `src/codemie/service/share_conversation_service.py` |
| Domain Model / Token Encoding | `FileObject.to_encoded_url` / `from_encoded_url` | `src/codemie_tools/base/file_object.py` |
| Domain Model / Token Encoding | `StringSerializer.serialize` / `deserialize` | `src/codemie_tools/base/string_serializer.py` |
| Domain Model / Ownership | `Conversation.is_shared_with` (stub) | `src/codemie/rest_api/models/conversation.py` |
| Domain Model / Sharing | `SharedConversation` | `src/codemie/rest_api/models/share/shared_conversation.py` |
| Repository / Storage | `FileRepository` + concrete backends | `src/codemie/repository/base_file_repository.py` + factory |
| Security / Auth | `authenticate` dependency | `src/codemie/rest_api/security/authentication.py` |
| Security / Ownership | `Ability` / `Owned` framework | `src/codemie/core/ability.py` |
| Configuration | `Config(BaseSettings)` | `src/codemie/configs/config.py` |

### Integration Points

**Internal cross-module dependencies:**
- `codemie_tools.base.file_object` → `codemie_tools.base.string_serializer` (encoding)
- `codemie.rest_api.routers.files` → `codemie.service.file_service.file_service` → `codemie.repository` (storage) + `codemie_tools.base.file_object`
- `codemie.rest_api.routers.share` → `codemie.service.share_conversation_service` → `codemie.rest_api.models.conversation` + `codemie.rest_api.models.share.shared_conversation`
- All agent/workflow/service files that call `to_encoded_url` / `from_encoded_url` (12 callers listed above)
- `codemie.core.ability` ← referenced by share router and conversation router but not by the file router

**External service integration points:**
- AWS S3 (`boto3`), Azure Blob Storage (`azure-storage-blob`), GCP Cloud Storage (`google-cloud-storage`) — all accessed through the file repository abstraction. No changes to these adapters are expected unless the `owner` path segment is altered.
- PostgreSQL / SQLModel — `Conversation` and `SharedConversation` models use SQLModel for ORM. A `Conversation.is_shared_with` implementation may need a JOIN or subquery against `shared_conversations`.
- Elasticsearch — `SharedConversation.get_by_fields` queries the `codemie_shared_conversations` index by `share_token.keyword`.

### Patterns and Conventions

- **Auth pattern:** Every authenticated endpoint uses `dependencies=[Depends(authenticate)]` on the route decorator plus `user: User = Depends(authenticate)` in the function signature when the user object is needed. This is the mandatory pattern per guides and all existing POST endpoints in `files.py`.
- **Ownership pattern:** `Ability(user).can(Action.READ, resource)` with the resource implementing `is_owned_by`, `is_managed_by`, `is_shared_with`. Currently no `FileObject` entry in `PERMISSIONS`.
- **Access-denied pattern:** `raise_access_denied("view")` (from `codemie.rest_api.exceptions`) — used in conversation, share, and other routers.
- **HMAC pattern:** `hmac.new(key, msg, hashlib.sha256).hexdigest()` with `hmac.compare_digest` for constant-time comparison — established in both `authentication.py` (internal bind key) and `github_webhook_security.py` (webhook verification).
- **Config secrets pattern:** New secrets are added as `FieldName: str = ""` on `Config(BaseSettings)`, optionally validated in a `@model_validator` — mirrors `MCP_AUTH_HMAC_SECRET`.
- **Token generation pattern:** Opaque random tokens use `secrets.choice` or `secrets.token_hex` — established in `SharedConversation.generate_share_token()` and `authentication.py`.
- **FastAPI app global auth:** There is no global authentication middleware. Auth is always per-endpoint. Missing `Depends(authenticate)` on any route means it is publicly accessible.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/kyrylo_korotych/codemie/.ai-run/guides/development/security-patterns.md` — documents the `authenticate` dependency as the mandatory auth entry point; prohibits inline auth header parsing; specifies fail-closed input validation.
- `/Users/kyrylo_korotych/codemie/.ai-run/guides/api/rest-api-patterns.md` — documents the FastAPI route + dependency pattern including `dependencies=[Depends(authenticate)]`.
- `/Users/kyrylo_korotych/codemie/.ai-run/guides/api/endpoint-conventions.md` — route and response conventions.
- `/Users/kyrylo_korotych/codemie/.ai-run/guides/architecture/layered-architecture.md` — records that HTTP concerns including auth enforcement belong in routers, not in services or repositories.
- `/Users/kyrylo_korotych/codemie/.ai-run/guides/architecture/project-structure.md` — package boundary definitions.

### Architectural Decisions

- The guide `layered-architecture.md` places auth enforcement strictly in the router layer — the `GET /v1/files/{file_name}` omission is a deviation, not a deliberate architectural decision.
- No formal ADR documents or `DECISION:`/`ADR:` inline markers were found for file access, URL signing, or the share conversation flow. The IDOR was not a known deferred decision — it is an oversight.
- The internal bind-key HMAC pattern in `authentication.py` was introduced as a service-to-service auth mechanism; the `MCP_AUTH_HMAC_SECRET` was introduced for enterprise MCP auth. Neither was designed for file token signing, but both establish the HMAC-SHA256 signing pattern the fix should follow.

### Derived Conventions

- New config variables follow the `UPPER_SNAKE_CASE: type = default` pattern on `Config`. For a signing secret, the pattern is `FILE_DOWNLOAD_HMAC_SECRET: str = ""` with a `@model_validator` that enforces a minimum length when the feature is enabled.
- Token expiry, if added, would be encoded as an integer field inside the signed payload (similar to the 30-second window used for bind-key nonces in `authentication.py`).
- The `Ability` ownership check for files should follow the same three-method `Owned` protocol (`is_owned_by`, `is_managed_by`, `is_shared_with`) to remain consistent with existing resources. Alternatively, an inline check comparing `file_object.owner == user.id` (equivalent to `is_owned_by`) plus a conversation-membership check is acceptable given the current absence of a `FileObject` `Ability` entry.

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/kyrylo_korotych/codemie/tests/codemie/rest_api/routers/test_files.py` — 15+ tests covering the GET endpoint. `test_read_file_success` (line ~63) sends `GET /v1/files/test.txt` with **no auth** and asserts HTTP 200 — this directly confirms the unauthenticated access path and will break as a positive signal when auth is added. Tests for `Content-Disposition`, XSS hardening (SVG, XML, XHTML), MIME types, UUID prefix stripping, and Cyrillic filenames exist. Write endpoint tests use the `authenticated_user` fixture correctly.
- `/Users/kyrylo_korotych/codemie/tests/codemie/rest_api/routers/test_share.py` — covers `POST /v1/share/conversations` (success, 404, no-permission, service-error) and `GET /v1/share/conversations/{token}` (success, not-found, idempotent share creation).
- `/Users/kyrylo_korotych/codemie/tests/codemie_tools/base/test_file_object.py` — unit tests for `FileObject`, `MimeType`, `normalise_mime`, `to_encoded_url`, and `from_encoded_url`.
- `/Users/kyrylo_korotych/codemie/tests/codemie_tools/base/test_string_serializer.py` — unit tests for `StringSerializer` encode/decode.
- `/Users/kyrylo_korotych/codemie/tests/codemie/rest_api/security/test_authentication.py` — covers `authenticate`, `sign_internal_request`, `_verify_internal_request`.
- `/Users/kyrylo_korotych/codemie/tests/codemie/triggers/bindings/test_github_webhook_security.py` — HMAC verification pattern tests (reference for new signed URL tests).

### Testing Framework and Patterns

- **Framework:** pytest with `pytest-anyio` (async tests via `@pytest.mark.anyio`), `pytest-mock` (`mocker` fixture), `pytest-env`.
- **Config:** `pytest.ini` — `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`, `ENV=local`.
- **Conftest:** `tests/conftest.py` mocks Postgres engine and stubs native packages. `tests/codemie/rest_api/routers/conftest.py` disables rate limiter and patches `starlette.datastructures.State.__getattr__` to return a dummy UUID (allowing `authenticate()` to not crash in test apps).
- **Auth override pattern:**
  ```python
  app.dependency_overrides[authenticate] = lambda: mock_user
  ```
  Used for authenticated route tests in both `test_files.py` and `test_share.py`.
- **Mock strategy:** `mocker.patch(...)` on `FileRepositoryFactory.get_current_repository` and `FileObject.from_encoded_url`; repository `read_file`/`write_file` mocked as `mocker.Mock()`.
- **HTTP client:** `AsyncClient(transport=ASGITransport(app=app))` for async; `fastapi.testclient.TestClient` for sync.

### Coverage Gaps

1. **No 401/403 assertion for unauthenticated `GET /v1/files/{file_name}`** — `test_read_file_success` currently expects HTTP 200 without credentials. This test must be updated once auth is enforced; a new test asserting 401/403 on unauthenticated requests must be added.
2. **No cross-user IDOR test** — no test constructs a URL with `owner=user_B` while authenticated as `user_A` and asserts HTTP 403. This must be added.
3. **No HMAC signature validation tests for file tokens** — once HMAC signing is implemented, tests are needed for: valid signature accepted; tampered `owner` rejected; tampered `name` rejected; expired/replayed token rejected (if TTL is added). Reference pattern: `test_github_webhook_security.py`.
4. **No share-context file access test** — no test validates that a viewer of a shared conversation can retrieve the files embedded in it (the exemption path for shared conversation file access). This must be added once the exemption mechanism is designed.
5. **No unauthenticated rejection test for `GET /v1/share/conversations/{token}`** — analogous to the mermaid endpoint's `test_create_mermaid_diagram_unauthenticated`, this test is absent.
6. **`test_file_object.py` and `test_string_serializer.py`** — if HMAC signing moves into `to_encoded_url`/`from_encoded_url`, unit tests for HMAC generation, valid verification, wrong-secret rejection, and legacy unsigned URL rejection are needed.

---

## 5. Configuration and Environment

### Environment Variables

**Existing auth/secret variables in `Config(BaseSettings)`:**
- `JWT_ALGORITHM`, `JWT_EXPIRATION_HOURS`, `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH`, `JWT_ISSUER` — RS256 JWT for local IdP
- `JWKS_VALIDATION_ENABLED`, `JWKS_TRUSTED_ISSUERS`, `JWKS_CACHE_TTL_SECONDS`, `JWKS_HTTP_TIMEOUT_SECONDS`, `JWKS_LEEWAY_SECONDS` — JWKS validation for inbound bearer tokens
- `AUTH_COOKIE_NAME`, `AUTH_COOKIE_HTTPONLY`, `AUTH_COOKIE_SECURE` (default `False` — must be `True` in production), `AUTH_COOKIE_SAMESITE`, `AUTH_COOKIE_PATH`
- `MCP_AUTH_HMAC_SECRET: str = ""` — existing HMAC secret (validated ≥ 32 bytes when `MCP_AUTH_ENABLED=True`); not provisioned in docker-compose or Helm values
- `MCP_AUTH_ENABLED: bool = False`
- `FILES_STORAGE_TYPE` — `filesystem` | `aws` | `azure` | `gcp`
- `FILES_STORAGE_DIR` — local filesystem base path

**Variable needed but not yet present:**
- `FILE_DOWNLOAD_HMAC_SECRET: str = ""` — signing key for file download tokens. Must be added to `Config`, `docker-compose.yml`, and `deploy-templates/values.yaml`.

### Configuration Files

- `/Users/kyrylo_korotych/codemie/src/codemie/configs/config.py` — primary settings class; `sensitive_keywords` masking covers any field ending in `key`, `password`, `secret`, `token`.
- `/Users/kyrylo_korotych/codemie/.env.example` — local dev template; does not yet document `FILE_DOWNLOAD_HMAC_SECRET`.
- `/Users/kyrylo_korotych/codemie/tests/.env.test` — test environment values.
- `/Users/kyrylo_korotych/codemie/docker-compose.yml` — `codemie` service only declares `ELASTIC_URL`, `PG_URL`, `KEYCLOAK_LOGOUT_URL`, `GOOGLE_APPLICATION_CREDENTIALS`. No HMAC secrets present.
- `/Users/kyrylo_korotych/codemie/deploy-templates/values.yaml` — Helm chart; secrets injected via `secretKeyRef`. No entry for file-download signing secret. A new k8s Secret object and `secretKeyRef` entry must be added before deployment.

### Feature Flags and Deployment Concerns

- `ENABLE_USER_MANAGEMENT: bool = False` — selects user provider backend (no direct impact on this fix).
- `MCP_AUTH_ENABLED: bool = False` — gates MCP auth HMAC validation; not directly related, but `MCP_AUTH_HMAC_SECRET` is the model to follow.
- No existing feature flag for file-download authentication. Because the fix is a security patch, it should not be placed behind a flag — it must be unconditionally enforced once deployed.
- **Deployment gate:** `FILE_DOWNLOAD_HMAC_SECRET` must be provisioned in Kubernetes before the fix goes live, or the server must refuse to start when the secret is absent (via `@model_validator`). A missing secret in production would either break file download entirely or silently bypass signing — either outcome is unacceptable.
- **Rolling upgrade concern:** Existing file URLs stored in `Conversation.history[*].file_names` are unsigned tokens. If `from_encoded_url` begins rejecting unsigned tokens immediately, all previously stored file links break. A migration window or dual-accept policy (accept unsigned tokens with ownership check, reject only forged ones) may be required.
- `AUTH_COOKIE_SECURE: bool = False` in `Config` default — must be confirmed as `True` in production. If cookies are sent over HTTP, the share context exemption could be intercepted.

---

## 6. Risk Indicators

- **Critical — No authentication on `GET /v1/files/{file_name}`:** The handler at `src/codemie/rest_api/routers/files.py` (~line 203) has zero auth. Any HTTP client can download any file by constructing a valid base64 token. This is the primary IDOR.
- **Critical — Forgeable token in `StringSerializer`:** `src/codemie_tools/base/string_serializer.py` uses pure base64 with no MAC. An attacker who knows the encoding format (public by inspection of any intercepted URL) can craft tokens for arbitrary owner/filename pairs.
- **Critical — `FileService` trusts token-supplied owner:** `src/codemie/service/file_service/file_service.py` (~line 34) passes `file_object.owner` from the decoded token directly to `file_repo.read_file` — the authenticated user's identity is never consulted.
- **High — `Conversation.is_shared_with` is a stub returning `False`:** `src/codemie/rest_api/models/conversation.py` (~line 633). The requirement to allow file access "if the conversation was explicitly shared with them" cannot be implemented without first implementing this method. This is a prerequisite dependency.
- **High — No `FileObject` entry in `Ability.PERMISSIONS`:** `src/codemie/core/ability.py`. File access bypasses the project's ownership framework entirely. Either the framework must be extended, or an inline ownership check must be consistently applied.
- **High — Share conversation GET endpoint already requires auth:** `src/codemie/rest_api/routers/share.py` (~line 65). The task description mentions "public Share Chat rendering for actually-shared conversations without requiring login" — but this does not currently exist. If truly required, it represents a new public endpoint surface that must be carefully scoped (only file URLs belonging to the shared conversation, not arbitrary files).
- **High — Broad call surface for `to_encoded_url` / `from_encoded_url`:** 12 call sites across agents, workflows, services, and tools. Any change to the token format (e.g., appending an HMAC) must be backward-compatible during rollout, or all 12 call sites must be updated atomically.
- **High — Stored unsigned tokens in existing conversation history:** `GeneratedMessage.file_names` in Postgres/Elasticsearch contains plain (unsigned) encoded URLs for all historical conversations. After the fix, these tokens will fail HMAC verification unless a migration or dual-accept window is implemented.
- **Medium — `FILE_DOWNLOAD_HMAC_SECRET` not provisioned in deployment manifests:** `docker-compose.yml` and `deploy-templates/values.yaml` lack this secret. Must be added before deployment; if absent, file download signing either cannot be initialized or will silently be skipped.
- **Medium — `AUTH_COOKIE_SECURE: bool = False` default in `Config`:** If the share exemption path is added as a cookie-based or session-based flow over HTTP in any environment, the session token could be intercepted.
- **Low — No `ADR:` or `DECISION:` marker explaining why `GET /v1/files/` was left unauthenticated:** The oversight is undocumented. The fix should add an inline comment or changelog entry to prevent regression.
- **Low — Legacy `_`-split fallback in `StringSerializer.deserialize`:** An additional token parsing path that could accept unexpected crafted inputs if not also validated.
- **Low — Test `test_read_file_success` will break when auth is enforced:** This is expected and marks correct regression coverage, but must be explicitly updated in the fix.

---

## 7. Summary for Complexity Assessment

This task touches five architectural layers simultaneously: API Router (adding `authenticate` dependency and an ownership/share-context authorization check to `GET /v1/files/{file_name}`), Domain Model and Token Encoding (adding HMAC signing to `FileObject.to_encoded_url` / `from_encoded_url` and updating `StringSerializer` or wrapping it), Service Layer (`FileService.get_file_object` must validate ownership after decoding), and Configuration (new `FILE_DOWNLOAD_HMAC_SECRET` env var added to `Config`, `docker-compose.yml`, and Helm values). The share conversation flow adds a fifth surface: either implementing the stub `Conversation.is_shared_with` or designing a dedicated public endpoint. The file change surface is moderate-to-large: the core fix involves 5–7 files (`files.py`, `file_object.py`, `string_serializer.py`, `file_service.py`, `ability.py` or equivalent ownership logic, `config.py`), but the cascading change to the token format potentially implicates 12 additional call sites plus database/ES migration concerns for stored unsigned URLs.

The task introduces partial technical novelty: the project has established HMAC-SHA256 signing patterns (internal bind key in `authentication.py`, webhook verification in `github_webhook_security.py`) that can be directly reused. The `Ability`/`Owned` ownership framework is mature and consistently applied across other resources — the novelty is extending it to cover file access, or implementing an equivalent inline check. The `Conversation.is_shared_with` stub is an unimplemented prerequisite that must be resolved to fully satisfy requirement (1). The public share rendering requirement in (3) is a design question: the current share GET endpoint requires authentication, meaning "public/unauthenticated" rendering does not exist and would require a new endpoint surface — this is the highest-ambiguity item and warrants scoping clarification before implementation.

Test coverage for the affected areas is mixed. The `GET /v1/files/` endpoint has 15+ existing tests covering response formatting and MIME handling, but none asserting auth enforcement — `test_read_file_success` is effectively a regression test for the vulnerability. The share endpoints have reasonable happy-path and error-path coverage but no auth-rejection tests. The `StringSerializer` and `FileObject` encoding layers have unit tests that will need extension for HMAC signing. Key risk factors for complexity scoring: (a) the broad `to_encoded_url` call surface and stored-token migration concern elevate implementation risk above what the router-layer fix alone would suggest; (b) the `Conversation.is_shared_with` stub is a prerequisite blocker; (c) the "public Share Chat" requirement may require a net-new public endpoint design, which is out-of-scope without further product clarification.
