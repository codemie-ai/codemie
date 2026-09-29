# EPMCDME-12708 — Approved design (Section 2) and recorded decisions

Status: **implemented**. Supersedes all earlier drafts of Section 2 (editions 1–6) and the
Story 2 capability-token design. D-1 and D-3 were revised during implementation — see the
**Implementation addendum** at the end. Every claim below was verified against the source at
the file:line references given.

## Story sequencing

Stories 1 and 3 ship as **one deployable unit**. There is no intermediate state where
auth enforcement is live without the share grant — that would 404 every attachment in
every shared conversation. Story 2 is reduced to a recorded residual risk (see D-1).

## Authorization rules

```python
_workspace_repo = AgentWorkspaceRepository()  # module-level; tests patch
                                              # codemie.rest_api.routers.files._workspace_repo


def _authorize_file_access(
    file_object: FileObject,
    user: User,
    share_token: str | None,
    file_name_param: str,
    workspace_repo: AgentWorkspaceRepository,
) -> None:
    owner = file_object.owner

    # Rule A: requester owns the file directly.
    if owner == user.id:
        return

    # Rule B: workflow schema only. Scoped to the workflows/ prefix because the same
    # owner namespace also holds memory-profiling snapshots (see D-3).
    bucket = config.CODEMIE_STORAGE_BUCKET_NAME
    if bucket and owner == bucket and file_object.name.startswith("workflows/"):
        return

    # Rule C: MCP screenshot — any authenticated user. Recorded residual risk, see D-1.
    if owner == MCP_IMAGES_SUBDIR:
        return

    # Rule D: workspace-prefixed blob, requester owns the workspace.
    # Falls through on miss so Rule E can still grant share recipients.
    if owner.startswith("workspace-"):
        workspace_id = owner.removeprefix("workspace-")
        if workspace_repo.get_by_id_for_user(workspace_id, user.id):
            return

    # Rule E: share grant.
    if share_token:
        shared = SharedConversation.get_by_fields({"share_token.keyword": share_token})
        if shared:
            conversation = Conversation.find_by_id(shared.conversation_id)
            if conversation:
                in_file_names = any(
                    file_name_param == fn
                    for msg in (conversation.history or [])
                    for fn in (getattr(msg, "file_names", None) or [])
                )
                if in_file_names:
                    return
                # Blob identity, not owner prefix: covers both workspace write paths —
                # content-written (blob_owner = workspace-{id}) and reference-written
                # (blob_owner = the original owner, e.g. the sharer's user id).
                ws = workspace_repo.get_by_conversation_for_user(
                    shared.conversation_id, shared.shared_by_user_id
                )
                if ws and workspace_repo.find_by_blob(
                    ws.id, owner, file_object.name, file_object.mime_type
                ):
                    return

    _raise_file_not_found()
```

Evidence for each rule:

- Rule A — uploads, mermaid, generated images and code-executor exports all write with
  `owner=user.id` (`routers/files.py:267,313,350`, `generate_image_tool.py:59-63`,
  `file_export_service.py:116-121`).
- Rule B — `workflow_service.py:547-555` writes `workflows/{id}.svg` under
  `config.CODEMIE_STORAGE_BUCKET_NAME` and stores the encoded URL as
  `WorkflowConfig.schema_url`.
- Rule C — `mcp/toolkit.py:368` writes with `owner = MCP_IMAGES_SUBDIR`
  (`repository_factory.py:83`); names are `uuid4().hex` (`toolkit.py:372`).
- Rule D — `agent_workspace_service.py:66` → `:454` → `:686` build
  `blob_owner = f"workspace-{workspace.id}"` from the workspace **PK**, not the
  conversation id. The matching lookup is `get_by_id_for_user`
  (`agent_workspace_repository.py:27`), not `get_by_conversation_for_user` (`:34`).
- Rule E — `share.py:65` + `share_conversation_service.py:78-131` let any authenticated
  holder of the token read the conversation, whose `file_names`
  (`models/conversation.py:65,127`) hold bare encoded tokens (`core/utils.py:550-560`).
  `history` is `List[GeneratedMessage]` via `PydanticListType`
  (`models/conversation.py:235-237`), so attribute access with an `isinstance`-style
  guard is required — never `dict.get`.
  `shared_by_user_id` is set at `share_conversation_service.py:57`.

Namespaces deliberately **out of scope**: `datasource-{id}`
(`datasource_file_storage.py:26`) and `skill-{id}` (`skill_file_storage.py:38`). Neither
reaches `to_encoded_url` on an HTTP path; datasource images are read server-side only via
`FileService.get_image_base64` (`agents/tools/kb/search_kb.py:190`).

## Handler

```python
@router.get(
    "/files/{file_name}",
    dependencies=[Depends(authenticate)],          # matches files.py:246 sibling convention
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Malformed file token"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentication required"},
        status.HTTP_404_NOT_FOUND: {"description": "File not found"},
    },
)
def read_file(                                     # sync, NOT async — see D-6
    file_name: str,
    share_token: str | None = Query(default=None),
    user: User = Depends(authenticate),
) -> Any:
    try:
        file_object = FileObject.from_encoded_url(file_name)
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid file token",
            details="The file token could not be decoded.",
            help="Verify the file URL is complete and unmodified.",
        )

    # Authorization runs BEFORE the storage try block. ExtendedHTTPException subclasses
    # Exception (core/exceptions.py:28); inside the try, the bare except would swallow
    # 404 denials and return 500, breaking the 404-parity guarantee.
    _authorize_file_access(file_object, user, share_token, file_name, _workspace_repo)

    try:
        # RETAINED VERBATIM from files.py:213-229 — MIME dispatch, _strip_uuid_prefix,
        # normalise_mime, READ_FILE_MIME_TYPE_HANDLERS, INLINE_SAFE_MIME_PREFIXES,
        # _safe_disposition, X-Content-Type-Options. No behavioural change.
        ...
    except FileNotFoundError:
        _raise_file_not_found()
    except Exception:
        logger.exception(f"Unexpected error reading file: file_name={file_name!r}, user_id={user.id}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            details="An unexpected error occurred while processing the request.",
            help="Please try again later. If the problem persists, contact support.",
        )
```

Add `Query` to the `from fastapi import ...` line (`files.py:22`).

`FileObject.from_encoded_url` raises `ValueError` for every malformed input: bad base64
returns `[]` from `StringSerializer.deserialize` (`string_serializer.py:53`) and fails the
length check (`file_object.py:168-172`); a `UnicodeDecodeError` from `.decode(UTF_8)` is
itself a `ValueError` subclass. One `except ValueError` covers both.

### 404 parity

```python
def _raise_file_not_found() -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="File not found",
        details="The requested file could not be found.",
        help="Please verify the file name and try again.",
    )
```

Both the authorization denial and the `FileNotFoundError` branch call this, so the two
responses are byte-identical and reveal no existence oracle. No `file_name` in the body.
The existing 404 branch (`files.py:230-236`) is replaced.

Accepted residual: denial short-circuits before the storage read, a genuine 404 after, so
a timing difference remains. UUIDv4 owners make timing-based enumeration infeasible.

Also fixed in the same rewrite: the current 500 branch (`files.py:238-243`) interpolates
`str(e)` into the response body, which `.ai-run/guides/development/security-patterns.md`
prohibits and `security/authentication.py:159-160` already avoids.

### Service layer

`FileService.load_content(file_object: FileObject) -> FileObject` — new method taking an
already-decoded object, so the router decodes exactly once. `FileService.get_file_object`
and `get_image_base64` keep their current signatures: they are called server-side with no
user context (`core/utils.py:552`, `search_kb.py:190`). **No authorization moves into
FileService.** Enforcement is router-only, per
`.ai-run/guides/architecture/layered-architecture.md`.

### New repository method

```python
def find_by_blob(
    self,
    workspace_id: str,
    blob_owner: str,
    blob_name: str,
    mime_type: str | None = None,
) -> Optional[AgentWorkspaceFile]:
    with Session(AgentWorkspaceFile.get_engine()) as session:
        conditions = [
            AgentWorkspaceFile.workspace_id == workspace_id,
            AgentWorkspaceFile.blob_owner == blob_owner,
            AgentWorkspaceFile.blob_name == blob_name,
            # Deliberately no deleted_at filter — see D-4.
        ]
        if mime_type is not None:
            conditions.append(AgentWorkspaceFile.mime_type == mime_type)
        return session.exec(select(AgentWorkspaceFile).where(*conditions)).first()
```

`mime_type` is part of the match key, consistent with `_blob_identity_key`
(`agent_workspace_service.py:165,185`). Both sides originate from the same encoded token,
so `normalise_mime` is applied on neither side and the comparison stays exact.
`(blob_owner, blob_name)` is unindexed, but the query is anchored on the indexed
`workspace_id` (`models/agent_workspace.py:39`), bounding the scan to one workspace. No
new index.

### Share token delivery

`ShareConversationService.get_shared_conversation()` adds `"share_token": share_token` to
its existing response dict. `file_names` is **not** rewritten — those entries are read as
bare tokens by `_process_file_names_to_objects` (`core/utils.py:550`) and by export
(`service/conversation/export_utils.py:168`), so mutating them would corrupt fork,
continue and export flows.

Frontend coordination required (not optional): in shared-conversation context the
frontend appends `?share_token={token}` to both `file_names` URLs and `sandbox:/v1/files/`
URLs found in message text (prefix `SANDBOX_FILE_PREFIX`,
`agent_workspace_service.py:47`; pattern `SANDBOX_FILE_RE`, `agents/tools/agent.py:36`).

Accepted: the token appears in nginx access logs and `Referer` headers. Lower severity
than the original IDOR, but real. If PRE-2 below resolves to fetch+blob rendering, move to
an `X-Share-Token` request header instead.

### Complexity

`_authorize_file_access` sits under ruff's `max-complexity = 16` (`pyproject.toml:237`) at
five rules. A sixth rule requires extracting `_check_share_grant(...)` first. `B904` and
`G004` are in the ignore list (`pyproject.toml:218-219`), so bare `raise` inside `except`
and f-string logging both match house style.

---

## Recorded decisions

**D-1 — Rule C stays allow-all for authenticated users. Residual risk accepted.**
The proposal to tighten Rule C to a workspace blob-identity check was rejected because the
ownership record it depends on does not reliably exist. `assistant_service.py:575` selects
the agent, `ENABLE_LANGGRAPH_AITOOLS_AGENT` defaults to `True` (`config.py:566`), so the
default chat path is `LangGraphAgent` — and `langgraph_agent.py:657-663` passes
`response=output` where `output = response.generated`, a plain string.
`_extract_sandbox_urls` (`agents/tools/agent.py:148-150`) then only regex-matches the final
assistant text. MCP screenshots are produced inside tool content
(`mcp/toolkit.py:396-398`), so they are registered only on the two `AIToolsAgent` call
sites that pass the full executor dict (`assistant_agent.py:425,458`, which have
`return_intermediate_steps=True` at `:273`). Tightening Rule C would 404 the screenshot's
own author on the default path.
Residual: an authenticated user who independently obtains an MCP screenshot URL can fetch
it. Names carry 122 bits of randomness, so enumeration is infeasible; this is
security-by-unguessable-name for cross-user access, not an enumerable IDOR.
Follow-up (separate ticket, not a blocker): register MCP screenshots at write time in
`_save_screenshot_to_storage` (`mcp/toolkit.py:376`), then tighten Rule C.

**D-2 — AC #2 is satisfied by authorization, with one named exception.**
AC #2 asks that forged URLs be rejected. Post-fix, tampering with `owner` or fabricating a
URL from a known `(user_id, filename)` pair reaches the authorization check and is rejected
with 404; signing would reject it before decode instead of after, which is not observably
different to the caller. Cryptographic token signing therefore adds no practical security
once ownership is enforced.
Named exception: a fabricated `owner=CODEMIE_STORAGE_BUCKET_NAME`,
`name=workflows/{known-id}.svg` URL is served to any authenticated user by Rule B. Workflow
schema SVGs are deliberately authenticated-but-not-owner-scoped. AC #2 holds for all
user-owned and workspace-scoped content; this one namespace is an accepted exception.

**D-3 — Rule B is scoped to the `workflows/` prefix, allow-list not deny-list.**
`CODEMIE_STORAGE_BUCKET_NAME` has two writers: `workflow_service.py:550` and
`monitoring/memory_profiling_service.py:217`, the latter writing
`{MEMORY_PROFILING_SNAPSHOT_PREFIX}/snapshot_{ts}_{hostname}_{uuid[:8]}.json.gz`
(`:387-390`, default prefix `memory_snapshots`, `config.py:715`) — serialized process state
that can contain prompt text, tokens and credentials, under names with only 32 bits of
UUID plus a to-the-second timestamp and a pod name. An allow-list on `workflows/` denies
these by default and makes any future third writer of this namespace deny-by-default too.

**D-4 — `find_by_blob` includes soft-deleted rows.**
The grant is "this blob was part of the shared conversation", which soft-deleting the file
from a workspace does not revoke. `get_file` exposes an `include_deleted` option
(`agent_workspace_repository.py:55`); this query simply omits the filter. If a future
requirement needs deletion to revoke share access, add `revoked_at` then rather than
speculatively now.

**D-5 — No admin or support bypass.**
Rules A–E contain no admin branch, so administrators are subject to the same checks. This
is deliberate least privilege for an IDOR fix. Memory-profiling snapshots are unreachable
through this endpoint for every principal, admins included.

**D-6 — `read_file` stays synchronous.**
`FileService` performs blocking S3/GCS/Azure I/O through the repository abstraction.
FastAPI runs a sync endpoint in the threadpool and supports an async dependency on it;
`async def` would move blocking blob reads onto the event loop.

**D-7 — AC #3 is Interpretation A. No outbound question.**
AC #3 ("public Share chat rendering continues to work ... without requiring login") is a
non-regression clause describing behaviour that does not exist: `share.py:65` has carried
`Depends(authenticate)` since before the ticket was filed, so there is nothing to preserve.
The ticket also cites the edge proxy blocking unauthenticated requests as a *mitigating
control* in its own Preconditions, which contradicts "renders without login" — and under
Interpretation B the fix would have to remove the control the ticket relies on. The
substantive half of AC #3 — "only for files belonging to the shared conversation, not
arbitrary files" — is implemented by Rule E.
If anonymous share viewing is later confirmed as a real requirement, it is a separate story
requiring optional authentication on `read_file`, re-evaluation of Rules A–D against a
null user, an ingress change to permit anonymous `/v1/files/` and `/v1/share/` traffic, and
a security sign-off.

**D-8 — Historical data.**
Workspace registration postdates much of the stored conversation history, so Rule E's
`find_by_blob` will not match blobs from conversations shared before that feature existed.
Effect is limited to share recipients (owners are unaffected via Rule A), and no backfill
is planned. Rule C is unaffected because D-1 leaves it allow-all.

---

## Open pre-conditions — must be answered before deploy, not before coding

**PRE-1 — Ingress.** What are the actual annotations on `/code-assistant-api/(.*)` in the
target environment? The chart default (`deploy-templates/values.yaml:252-267`) carries no
oauth2-proxy annotations, but overlays routinely replace them, and the codebase shows a
proxy/BFF is present in real deployments: `jwks_validating.py:21,49` injects
`x-auth-request-access-token` for the enterprise IDP, `provider_api_client.py:31-38`
documents BFF header forwarding, and `values.yaml:351` shows the pattern on another
ingress. If oauth2-proxy injects `Authorization`, `<img src>` requests are covered
regardless of user provider.

**PRE-2 — Rendering.** Are chat files rendered via `<img src>` / `<iframe>` (cannot carry a
custom header — needs a cookie or proxy injection) or via fetch + blob URL (can set
`Authorization`)? `JwksValidatingIdp._extract_bearer` (`jwks_validating.py:124`) is
header-only; the persistent provider also accepts the `codemie_access_token` cookie
(`user_providers/persistent.py:56-71`, `config.py:245`).

**PRE-3 — URL construction.** Does the frontend concatenate the token into the path raw, or
URL-encode the segment (`?` → `%3F`)? The answer decides query-string versus
`X-Share-Token` header transport for the share grant.

Record the answers and their source in this directory when they arrive.

---

## Test plan

Retrofit the 18 existing unauthenticated `GET /v1/files/...` tests in
`tests/codemie/rest_api/routers/test_files.py` (lines 86, 107, 757, 775, 790, 805, 934,
954, 966, 978, 991, 1004, 1017, 1030, 1043, 1058, 1076, 1096) with auth headers **and**
fixtures whose mocked `FileObject.owner` matches the authenticated test user — otherwise
they 404 instead of 200.

New cases:

1. 401 — no authentication.
2. 400 — malformed token.
3. 404 — wrong owner, no `share_token`.
4. 200 — own file (Rule A).
5. 200 — workflow schema `workflows/{id}.svg` (Rule B).
6. 404 — `memory_snapshots/snapshot_*.json.gz` for any authenticated user (pins D-3).
7. 200 — MCP image for any authenticated user, comment naming D-1's accepted trade-off.
8. 200 — workspace file, requester owns the workspace (Rule D).
9. 200 — share grant via `in_file_names`, user-uploaded file (Rule E).
10. 200 — share grant via `find_by_blob`, file whose `blob_owner` is the **sharer's user
    id**, registered by reference and referenced only in message text as
    `sandbox:/v1/files/...`.
11. 200 — share grant for a `workspace-`-prefixed blob where the requester is not the
    workspace owner: Rule D miss → Rule E hit.
12. 404 — `workspace-`-prefixed blob, non-owner, no `share_token`.
13. 404-parity — denial and genuine-missing return byte-identical JSON bodies.
14. 404 not 500 — a wrong-owner request returns 404, proving authorization runs outside
    the storage try block.
15. `file_names` integrity — after `get_shared_conversation`, the stored conversation's
    `file_names` entries are still bare tokens.

## Files touched

- `src/codemie/rest_api/routers/files.py` — auth dependency, `share_token` param,
  `_authorize_file_access`, `_raise_file_not_found`, 400 branch, 500 body fix, `Query`
  import, `_workspace_repo`.
- `src/codemie/service/file_service/file_service.py` — add `load_content(file_object)`.
- `src/codemie/repository/agent_workspace_repository.py` — add `find_by_blob`.
- `src/codemie/service/share_conversation_service.py` — add `share_token` to the response
  dict.
- `tests/codemie/rest_api/routers/test_files.py` — retrofit plus the 15 new cases.

No config keys. No new modules. No token-format change. No migration.


---

# Implementation addendum

Three changes were made after the design was approved. Anonymous share viewing
(AC #3 Interpretation B) was dropped from scope entirely: it requires taking
`/v1/files/` and `/v1/share/` out from behind oauth2-proxy, which is an edge-config
change outside this repository. It is filed as a separate feature, not a defect fix.

## A-1 supersedes D-1 — MCP screenshots are now user-owned

`_save_screenshot_to_storage` (`service/mcp/toolkit.py`) takes an `owner` argument, threaded
from `MCPTool._post_process_output_content` via `MCPExecutionContext.user_id`
(`service/mcp/models.py:47`). New screenshots are written under the invoking user's id and are
therefore covered by Rule A. `MCP_IMAGES_SUBDIR` remains only as the fallback when no execution
context is present.

This closes D-1's residual for all new content without depending on the workspace harvest,
which D-1 showed to be unreliable on the default `LangGraphAgent` path.

Rule C is kept for blobs already stored under the shared namespace. Their names are
`uuid4().hex` and denying them would break images in existing conversations, so the legacy
path stays open and decays as history ages out. Recorded, not accidental.

## A-2 supersedes D-3 — Rule B checks Ability, not just the path prefix

Rule B resolves `workflows/{id}.svg` to its `WorkflowConfig` and requires
`Ability(user).can(Action.READ, workflow_config)`. The prefix check remains as the first gate,
so memory-profiling snapshots under the same owner are still denied, and a workflow schema is
now readable only by someone who may read the workflow itself. `_can_read_workflow_schema`
fails closed: a missing workflow or any lookup error denies rather than raising.

With A-1 and A-2 in place, AC #2 is satisfied without signing tokens and without any migration:
a forged URL reaches the authorization check and is rejected there. D-2's named exception moved
rather than disappeared: `workflows/` is now owner-scoped through `Ability`, but **Rule C still
grants every authenticated user any blob under `MCP_IMAGES_SUBDIR`** — see A-4 for the residual and
what would let it be removed. A forged *name* is rejected separately (A-5), since ownership alone
does not constrain the name half of the token.

## A-3 — the share grant is returned as a separate map, not by rewriting the conversation

`GET /v1/share/conversations/{token}` returns an extra `shared_file_urls` field: a map from every
encoded file token the conversation refers to, to the same token carrying `?share_token=...`. Both
carriers are covered — `file_names` entries and inline `sandbox:/v1/files/<token>` URLs found in
message text.

The conversation itself is returned **untouched**. An earlier revision rewrote `file_names` and
message text in a presentation copy; the sanity harness rejected it
(`test_share_conversation_with_file_attachment` asserts that `fileNames` still contains the exact
token the upload returned), which showed the rewrite was breaking a contract real consumers depend
on, not just a UI detail. `_process_file_names_to_objects` (`core/utils.py:550`) and the export
flow decode those entries as bare tokens too.

A share recipient renders files through the map and falls back to the bare token when a reference
is absent from it. The map costs no payload duplication: it carries tokens, not message bodies.

Frontend consequence: `codemie-ui` must read `shared_file_urls` on the shared conversation page
instead of building file URLs from `file_names` directly. The `stripFileTokenQuery` helper added
there stays useful — the mapped value carries a query string, and `createFileMetadata` still has to
decode only the part before `?`.

## Test coverage added

- Rule B: granted when Ability allows READ; denied when it does not; denied when the workflow row
  is gone.
- MCP ownership: owner is the execution-context user id; falls back to `MCP_IMAGES_SUBDIR` with no
  context and with a context carrying no user id.
- Share rewriting: `file_names` and inline sandbox URLs carry the token in the returned copy while
  the source message objects stay unchanged; a message with no file references is passed through
  unchanged.


## A-4 — Rule C is a recorded residual, not a closed hole

New screenshots are user-owned (A-1), but `_save_screenshot_to_storage` still falls back to
`MCP_IMAGES_SUBDIR` when no invoking user is known, and `user_id` is optional all the way up
(`service/mcp/toolkit_service.py:188`). So the shared namespace is not purely historical, and
Rule C's blanket grant does not automatically decay.

Accepted for now: exposure needs the blob name, which is `uuid4().hex` (122 bits), and denying the
namespace would break images in existing conversations. The unattributed-write path now logs a
warning, so whether it still fires in practice is observable instead of assumed. Rule C can be
dropped once that log stays silent and the stored history has aged out — that is the concrete exit
condition, and it belongs in a follow-up ticket rather than this change.

## A-5 — the name half of the token is validated, not just the owner

Authorization keys on `file_object.owner`, but `FileSystemRepository` joins owner and name, and its
`_resolve_safe_path` only prevented escaping the storage **root**. A token with the requester's own
owner and a name of `../<victim>/secret.pdf` therefore passed Rule A and resolved into another
owner's directory — the same cross-user read the ticket is about, moved from the owner field to the
name field. Cloud backends were unaffected: S3/GCS/Azure keys are literal strings.

Two layers now close it. `_resolve_safe_path` resolves against `<root>/<owner>` and refuses anything
that leaves it, which protects every caller rather than only this endpoint; subdirectory names such
as `workflows/{id}.svg` and `memory_snapshots/...` still resolve normally. `_points_outside_owner`
rejects such a token in the router before any rule runs, so the answer stays the shared 404 instead
of surfacing the storage error as a 500.

## A-6 — authorization fails closed

Rules B, D and E call into Postgres and Elasticsearch, and they run before the storage `try` block.
An exception from any of them previously escaped as a 500, which both broke the documented
fail-closed contract and reintroduced an existence oracle — 500 for "exists but the lookup broke"
against 404 for "denied". The authorization call is now wrapped: `ExtendedHTTPException` propagates
unchanged, anything else is logged and denied with the shared 404.

## A-7 — Rule E accepts exactly what the share response grants

`_build_shared_file_urls` issues grants for both carriers: `file_names` entries and inline
`sandbox:/v1/files/<token>` references in message text. Rule E originally matched only `file_names`,
so a recipient could receive a granted URL for an inline-referenced file and still be denied — the
workspace fallback covers that case only when the blob was registered, which D-1 shows is unreliable
on the default agent route. `_message_refers_to_file` now checks both carriers, so the authorizing
side and the granting side agree by construction.
