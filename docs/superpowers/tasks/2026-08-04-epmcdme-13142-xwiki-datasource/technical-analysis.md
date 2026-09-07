# Technical Research — EPMCDME-13142 xWiki datasource

**Task**: datasource indexer xwiki loader
**Generated**: 2026-08-04
**Research path**: filesystem (the `mcp__codegraph__*` tools are not available in this environment — tool-not-found, so the filesystem fallback was used)
**Repo**: `/Users/alex/Projects/Work/codemie-dev/codemie` @ branch `EPMCDME-13142_xwiki-datasource` (clean, HEAD `3e71dc072`)

> **Verification legend used throughout**
> - **[V]** — verified by me directly reading the file at the stated path/line in this run.
> - **[U]** — **unverified**. Delegated to a parallel research thread that did not return before the session limit, or otherwise not read. Treat as a lead, not a fact.
>
> Nothing marked [U] should be relied on for planning without a re-read.

---

## 1. Original Context

The verbatim hand-off briefing for EPMCDME-13142 is reproduced in the run's task context (pre-flight context document, sections 1–7: ticket scope, existing assets, donor template, registration points, live test environment, review gates, constraints). It is not duplicated here; this document only **verifies, corrects and deepens** it.

---

## 2. Codebase Findings

### 2.1 Briefing path/line audit — verified vs stale

All 16 paths named in the briefing exist. Line counts measured with `wc -l`:

| Briefing claim | Reality | Verdict |
|---|---|---|
| `src/codemie/datasource/azure_devops_wiki/azure_devops_wiki_datasource_processor.py` — 177 lines | 177 | **[V] accurate** |
| `src/codemie/datasource/loader/azure_devops_wiki_loader.py` — 780 lines | 780 | **[V] accurate** |
| `src/codemie/datasource/base_datasource_processor.py` — 1056 lines | **1083** | **[V] STALE** — off by 27 lines; the file has grown |
| `src/codemie/datasource/loader/base_datasource_loader.py` — 48 lines, "exactly two abstract methods" | 48 lines, 2 abstract methods + 1 concrete hook | **[V] accurate** |
| `confluence_datasource_processor.py` — 481 lines | 481 | **[V] accurate** |
| `loader/confluence_loader.py` — 148 lines | 148 | **[V] accurate** |
| `codemie_tools/core/project_management/xwiki/tools.py` — 745 lines | 745 | **[V] accurate** |
| `settings.py:219` `XWIKI_FIELDS = {URL: "url", TOKEN: "token", USERNAME: "username"}` | exact match at line 219 | **[V] accurate** |
| `settings.py:224` `XWikiConfig: CredentialTypes.XWIKI` | exact match at line 224 | **[V] accurate** |
| `xwiki/models.py` `XWikiConfig(url, username, token, use_bearer)` | exact match, `models.py:27-48` | **[V] accurate** |
| `xwiki/tools.py:94` `_XWikiBaseTool` | exact match at line 94 | **[V] accurate** |
| `xwiki/utils.py` `build_spaces_path()` | exact match, `utils.py:20-27` | **[V] accurate** |
| `codemie_tools/base/models.py` xwiki enum entry | `models.py:105` `XWIKI = "XWiki"` (a `CredentialTypes` member) | **[V] accurate** |
| `src/external/alembic/versions/3b3358380aa8_add_xwiki_credential_type.py` | file exists | **[V] exists**; contents **[U]** |
| Migration donor `2b461b2f3d10_added_azure_devops_wiki_column_to_the_.py` | **[U]** not read | **[U]** |

**Correction — the briefing under-describes the existing xWiki tool surface.** The briefing lists "ListWikis, ListSpaces, GetSpace, ListPages, ListWikiPages, ListPageChildren, GetPage, tags". The actual `tools.py` contains **24 tool classes** [V], including full CRUD (`CreatePageTool`, `ModifyPageTool`, `DeletePageTool`), comments (`ListPageCommentsTool`, `GetPageCommentTool`, `CreatePageCommentTool`), attachments (`ListPageAttachmentsTool`, `GetPageAttachmentTool`, `CreatePageAttachmentTool`, `DeletePageAttachmentTool`, `ReadPageAttachmentContentTool`), and **search** (`SearchWikiTool`, `SearchSpaceTool`). The presence of `SearchSpaceTool` and `ReadPageAttachmentContentTool` is relevant: attachment reading logic already exists and is out-of-scope for v1, so do not accidentally re-derive it.

**Confirmed — xWiki genuinely does not exist below the tools layer.** `grep -ril "xwiki" src/ --include='*.py'` returns exactly 9 files [V]:

```
src/codemie_tools/base/models.py
src/codemie_tools/core/project_management/xwiki/{models,toolkit,tools_vars,tools,utils}.py
src/codemie/service/settings/settings.py
src/external/alembic/versions/3b3358380aa8_add_xwiki_credential_type.py
src/external/alembic/versions/6cce754bc484_add_google_oauth_credential_type.py
```

Nothing in `src/codemie/datasource/`, `src/codemie/rest_api/`, `src/codemie/service/index/`, `src/codemie/triggers/`, or `src/codemie/core/constants.py`. The briefing's "missing = the whole ticket" holds.

**No reference commit exists.** `git log --diff-filter=A` on the ADO Wiki processor returns `afc61fa12 EPMCDME-9519: Initial commit with all project files` [V] — repo history is squashed, so there is no "how a datasource type was added" diff to copy. The closest usable reference is `3a216a5ab` (`EPMCDME-13690: Add health_check_git ...`), which touches exactly 3 files and shows the health-check registration shape end to end [V].

---

### 2.2 `BaseDatasourceLoader` — abstract contract [V]

`src/codemie/datasource/loader/base_datasource_loader.py` (48 lines, full read).

```python
class BaseDatasourceLoader(ABC):
    DOCUMENTS_COUNT_KEY = 'documents_count_key'
    TOTAL_DOCUMENTS_KEY  = 'total_documents'
    SKIPPED_DOCUMENTS_KEY = 'skipped_documents'
    FAILED_DOCUMENTS_KEY  = 'failed_documents'
    SECTIONS_FAILED_KEY   = 'sections_failed'

    @abstractmethod
    def fetch_remote_stats(self) -> dict[str, Any]: ...
    @abstractmethod
    def lazy_load(self) -> Iterator[Document]: ...
    def get_load_stats(self) -> dict[str, Any] | None:
        return None        # concrete hook, override to report post-load counters
```

Note `DOCUMENTS_COUNT_KEY` is the **literal string `'documents_count_key'`**, not `'documents_count'`.

What the base processor does with each result:

| Method | Consumed at | Effect |
|---|---|---|
| `fetch_remote_stats()` | `base_datasource_processor.py:189-195` | `stats[DOCUMENTS_COUNT_KEY]` becomes `index.start_progress(complete_state=...)` — the denominator of the user-visible progress bar. The whole dict is stored as `processing_info`. |
| `lazy_load()` | `base_datasource_processor.py:699` | Iterated one `Document` at a time; accumulated into `docs_batch` and flushed at `_processing_batch_size`. **Generator, must not materialise everything.** |
| `get_load_stats()` | `base_datasource_processor.py:306-330` (`_persist_load_stats`) | If non-`None`, merged into `index.processing_info`. On incremental reindex, `SKIPPED/FAILED/SECTIONS_FAILED` are **added** to the previous values; everything else is overwritten. |

`AzureDevOpsWikiLoader` redeclares `DOCUMENTS_COUNT_KEY = "documents_count_key"` locally (`azure_devops_wiki_loader.py:54`) — harmless duplication, do not copy it.

---

### 2.3 `BaseDatasourceProcessor` — abstract contract [V]

`src/codemie/datasource/base_datasource_processor.py` (1083 lines, full read).

**Only three members are actually `@abstractmethod`:**

| Member | Line | Signature |
|---|---|---|
| `_index_name` | 108-111 | `@property @abstractmethod def _index_name(self) -> str` |
| `_init_loader` | 464-494 | `def _init_loader(self) -> BaseDatasourceLoader` |
| `_init_index` | 496-512 | `def _init_index(self)` — returns nothing, must set `self.index` |

**Everything else the briefing lists as "must implement" is optional with a working default:**

| Member | Line | Default behaviour |
|---|---|---|
| `_processing_batch_size` | 113-116 | property returning `DEFAULT_PROCESSING_BATCH_SIZE = 50` |
| `_process_chunk(chunk, chunk_metadata, _document) -> Document` | 653-654 | `Document(page_content=chunk, metadata=chunk_metadata)` — passes the whole source metadata through |
| `_get_splitter(cls, document=None)` | 1064-1083 | classmethod; `RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="o200k_base", chunk_size=CODE_CONFIG.chunk_size, chunk_overlap=CODE_CONFIG.chunk_overlap)` — a **code-oriented** default, so a wiki processor should still override it |
| `_cleanup_data` | 445-454 | deletes the whole ES index |
| `_cleanup_data_for_incremental_reindex(docs)` | 456-462 | `pass` — the hook where a real delta reindex would delete superseded docs |
| `_on_process_start` / `_on_process_end` | 514-537 | `pass` |
| `_validate_indexing_result` | 539-564 | raises `NoChunksImportedException` if `index.current__chunks_state == 0` |

**`_check_docs_health()` — THE DISCREPANCY (confirmed).** [V]

The briefing states the processor "must implement ... `_check_docs_health()`". **It is not on the base class at all.** `grep -rn "_check_docs_health" src/` returns exactly four hits:

```
src/codemie/datasource/azure_devops_work_item/azure_devops_work_item_datasource_processor.py:177   def _check_docs_health(self) -> int:
src/codemie/datasource/azure_devops_wiki/azure_devops_wiki_datasource_processor.py:169             def _check_docs_health(self) -> int:
src/codemie/service/index/datasource_health_check_service.py:157                                   documents_count = processor._check_docs_health()
src/codemie/service/index/datasource_health_check_service.py:158-177                               documents_count = processor._check_docs_health()
```

It is a **duck-typed convention invented by the two Azure DevOps processors**, called by name from the health-check service. There is no ABC enforcing it, no type checker will catch a typo, and no other datasource type has one. Consequence for the ticket: implementing `_check_docs_health()` on the xWiki processor is *necessary but not sufficient* — it only does anything if `datasource_health_check_service.py` is also given a `case DatasourceTypes.XWIKI:` arm that calls it.

**Processing flow (batching / chunking) [V]** — `process()` at line 143 is explicitly documented "should not be overridden":

```
process()
 ├─ _init_index()                                   # subclass; creates IndexInfo if absent
 ├─ _setup_processing_context()                     # appends DatasourceMonitoringCallback, LLM ctx
 ├─ index.start_fetching(is_incremental=...)
 ├─ loader = _init_loader()                         # subclass
 ├─ _on_process_start()
 ├─ loader.fetch_remote_stats() -> start_progress(complete_state=stats[DOCUMENTS_COUNT_KEY])
 ├─ _process()
 │   └─ _load_and_process_documents(loader, index, batch_size=_processing_batch_size)   # :656
 │       ├─ store = _get_store_by_index(_index_name, embeddings_model)                  # :690
 │       ├─ store._store._create_index_if_not_exists()                                  # :691
 │       ├─ _reset_chunk_numbering()
 │       └─ for doc in loader.lazy_load():                                              # :699
 │             docs_batch.append(doc); if len >= batch_size -> _process_batch(...)
 │                 └─ _process_batch()                                                   # :749
 │                     ├─ if is_incremental_reindex: _cleanup_data_for_incremental_reindex(docs)
 │                     ├─ _split_documents(docs)                                         # :593
 │                     │     for each doc: _get_splitter(document).split_text(page_content)
 │                     │     chunk_metadata = doc.metadata.copy(); chunk_metadata["chunk_num"] = n
 │                     │     processed = _process_chunk(chunk, chunk_metadata, document)
 │                     │     processed.metadata["chunk_num"] = n     # re-applied by the BASE  :639
 │                     ├─ _apply_guardrails_for_dict(...)  -> may raise GuardrailBlockedException
 │                     └─ ThreadPoolExecutor(STORAGE_CONFIG.indexing_threads_count)
 │                           _process_document(...)  @retry(exponential)                 # :930
 │                             └─ _store_document_chunks -> store.add_documents(...)     # :988
 ├─ _persist_load_stats()  <- loader.get_load_stats()
 ├─ _validate_indexing_result()   -> NoChunksImportedException if 0 chunks
 ├─ index.complete_progress(...)
 └─ _create_or_update_scheduler()  (cron)
```

Two subtleties that matter for xWiki:

1. **`_split_documents` keys on `metadata["file_path"]` falling back to `metadata["source"]`** (`:624`), and chunk-identity numbering keys on `metadata["source"]` (`:630`). Every yielded `Document` must therefore carry a **unique, stable `source`**. Two xWiki pages that collapse to the same `source` string will overwrite each other's chunk identities.
2. **`_process_chunk` is a metadata whitelist.** ADO Wiki rebuilds the metadata dict from scratch, dropping everything not explicitly copied. The base class re-applies only `chunk_num` afterwards (`:637-639`). So `modified` / `version` / `author` will be silently lost unless `_process_chunk` explicitly copies them — this is exactly review gate #2.

**How failure surfaces to the user [V]** — `process()` exception handling at `:224-260`:

| Exception | Handling |
|---|---|
| `IndexDeletedException` | deletes the ES index, notifies callbacks, **returns silently** |
| `GuardrailBlockedException` | deletes ES index, `index.set_error(str(ex))`, returns |
| any other `Exception` | `index.set_error(str(ex))`, notifies callbacks, **re-raises** |

`index.set_error(str(ex))` means **the exception's `str()` is what the user reads in the UI**. That is why the typed exceptions in `src/codemie/datasource/exceptions.py` matter — they carry human-readable templates.

**Typed exceptions available [V]** (`src/codemie/datasource/exceptions.py`, full read):

| Class | Line | Message template |
|---|---|---|
| `MissingIntegrationException(integration_type)` | 23 | `"{} integration is not completed."` |
| `InvalidQueryException(expression_type, additional_info="")` | 32 | `"The provided {} expression cannot be parsed. {}"` |
| `UnauthorizedException(datasource_type="")` | 40 | `"Cannot retrieve data from {}"` |
| `ConnectionException(datasource_type, error_details="")` | 47 | `"Failed to connect to {}: {}"` |
| `EmptyResultException(expression_type)` | 57 | `"Based on {} expression empty result returned."` |
| `SkippedFileException(file_name, reason)` | 64 | `"Skipped {file_name}: {reason}"` |
| `NoChunksImportedException(datasource_name, processed_documents)` | 73 | "No chunks were imported for datasource '{}'. All files were skipped or failed…" |

`ConnectionException` is the right vehicle for the "you typed the wrong base URL (missing/extra `/xwiki` prefix)" message, because it has an `error_details` slot. `UnauthorizedException` is the right vehicle for 401/403.

---

### 2.4 Donor: `AzureDevOpsWikiDatasourceProcessor` — concrete implementations [V]

`src/codemie/datasource/azure_devops_wiki/azure_devops_wiki_datasource_processor.py` (177 lines, full read).

```python
class AzureDevOpsWikiDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_azure_devops_wiki"                       # :38

    def __init__(self, *, datasource_name, user, project_name, credentials,
                 wiki_query, wiki_name=None, description="",
                 project_space_visible=False, index_info=None, callbacks=None,
                 request_uuid=None, guardrail_assignments=None, **kwargs):
        # stores project_name/description/credentials/... then:
        self.setting_id      = kwargs.get('setting_id')                   # :63
        self.embedding_model = kwargs.get('embedding_model')              # :64
        super().__init__(datasource_name=..., user=..., index=index_info,
                         callbacks=..., request_uuid=...,
                         guardrail_assignments=...,
                         cron_expression=kwargs.get('cron_expression'))   # :66-74

    @property
    def _index_name(self) -> str:                                          # :76-78
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}",
                             type=self.INDEX_TYPE).get_identifier()

    @property
    def _processing_batch_size(self) -> int:                               # :80-82
        return AZURE_DEVOPS_WIKI_CONFIG.loader_batch_size

    def _init_index(self):                                                 # :84-100
        if not self.index:
            self.index = IndexInfo.new(
                repo_name=self.datasource_name, full_name=self.datasource_name,
                project_name=self.project_name, description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE, user=self.user,
                azure_devops_wiki=AzureDevOpsWikiIndexInfo(wiki_query=..., wiki_name=...),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id)
        self._assign_and_sync_guardrails()

    def _init_loader(self):                                                # :102-124
        # (vision model wiring — NOT needed for xWiki v1)
        return AzureDevOpsWikiLoader(base_url=..., wiki_query=..., access_token=...,
                                     organization=..., project=..., wiki_identifier=...,
                                     chat_model=chat_model)

    def _process_chunk(self, chunk, chunk_metadata, document) -> Document:  # :126-157
        metadata = {"source":..., "page_id":..., "page_path":..., "wiki_name":...}
        # then conditionally copies content_type + 5 attachment keys
        return Document(page_content=chunk, metadata=metadata)

    @classmethod
    def _get_splitter(cls, document=None) -> RecursiveCharacterTextSplitter:  # :159-167
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=AZURE_DEVOPS_WIKI_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=AZURE_DEVOPS_WIKI_CONFIG.chunk_overlap)

    def _check_docs_health(self) -> int:                                    # :169-177
        try:
            loader = self._init_loader()
            return loader.fetch_remote_stats().get(AzureDevOpsWikiLoader.DOCUMENTS_COUNT_KEY, 0)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise
```

`ConfluenceDatasourceProcessor` uses the identical `_index_name` formula (`confluence_datasource_processor.py:119-120`, `INDEX_TYPE = "knowledge_base_confluence"` at `:72`) [V] — the pattern is stable across types.

**Donor anti-pattern to NOT copy** [V]: `AzureDevOpsWikiLoader.fetch_remote_stats()` (`azure_devops_wiki_loader.py:757-780`) wraps its body in `try/except Exception` and **returns `_create_stats_response(0)` on failure**. Combined with `_check_docs_health()` above, an ADO Wiki health check on a broken wiki reports "0 documents", not an error. Only `_validate_creds()` (called before the `try`) surfaces auth failures. For xWiki, review gate #3 ("surface a clear error, not a traceback") requires the **opposite**: let typed exceptions propagate out of `fetch_remote_stats()`.

**Donor loader structure** [V] (`azure_devops_wiki_loader.py`, 780 lines — structural read plus full read of `:40-266`, `:356-400`, `:745-780`):

```python
class AzureDevOpsWikiLoader(BaseLoader, BaseDatasourceLoader):   # :40  — dual base
    def _create_auth_header(self) -> dict[str, str]              # :125  base64 Basic
    def _validate_creds(self)                                    # :132
        # no token           -> raise MissingIntegrationException("AzureDevOps Wiki")
        # probe call fails   -> raise UnauthorizedException(datasource_type="AzureDevOps Wiki")
    def lazy_load(self) -> Iterator[Document]                    # :145
        self._init_client(); self._validate_creds()
        for page in self._load_wiki_pages_streaming(): yield self._transform_to_doc(page)
    def _load_wiki_pages_streaming(self)                         # :276  paths first, then batches of 100
    def _transform_to_doc(self, page) -> Document                # :368
        metadata = {"source": <absolute page URL>, "page_id", "page_path",
                    "wiki_name", "wiki_id", "order"}
    def fetch_remote_stats(self) -> dict[str, Any]               # :757
```

The `source` metadata is an **absolute, browser-openable URL** (`:384`). xWiki gives this for free: every REST page summary carries `xwikiAbsoluteUrl` (verified live, see §7).

The ConfluenceDatasourceLoader (`loader/confluence_loader.py`, 148 lines, full read) [V] shows the alternative, terser stats shape:

```python
def fetch_remote_stats(self) -> dict[str, Any]:
    response = self.confluence.cql(self.cql, start=0, limit=1)
    if not isinstance(response, dict):
        raise ValueError("Cannot retrieve data with provided configuration")
    pages_count = response['totalSize']
    return {self.DOCUMENTS_COUNT_KEY: pages_count,
            self.TOTAL_DOCUMENTS_KEY: pages_count,
            self.SKIPPED_DOCUMENTS_KEY: 0}
```

Confluence gets a cheap `totalSize` from the API. **xWiki does not** (verified live, §7) — `fetch_remote_stats()` for xWiki must enumerate.

---

### 2.5 Existing xWiki tools layer — what is reusable and what is not [V]

`src/codemie_tools/core/project_management/xwiki/tools.py:94-151`:

```python
class _XWikiBaseTool(CodeMieTool):
    config: XWikiConfig
    tokens_size_limit: int = 20_000

    def _build_auth_headers(self) -> dict:                        # :101
        if self.config.use_bearer:
            return {"Authorization": f"Bearer {self.config.token}"}
        credentials = base64.b64encode(f"{self.config.username or ''}:{self.config.token}".encode()).decode()
        return {"Authorization": f"Basic {credentials}"}

    def _request(self, method, url, params=None, json_body=None) -> tuple[httpx.Response, str]:   # :108
        headers = {**self._build_auth_headers(), "Accept": "application/json"}
        with httpx.Client(timeout=30) as client: ...
        except httpx.RequestError as e: raise ToolException(f"xWiki request failed: {e}")
        return response, response.text          # <-- does NOT raise on 4xx/5xx

    def _format_result(self, method, url, response, text, *, is_markdown=False) -> str:           # :131
        if is_markdown and response.status_code < 300:
            text = markdownify(text, heading_style="ATX")
        return f"HTTP: {method} {url} -> {response.status_code} {response.reason_phrase}\n{text}"

    def _healthcheck(self) -> None:                                # :144
        validate_creds(self.config)
        response = httpx.Client(timeout=10).get(f"{base_url}/rest/wikis", headers=self._build_auth_headers())
        if response.status_code != 200: raise AssertionError(...)
```

**Reuse verdict:**

| Asset | Reuse? | Why |
|---|---|---|
| `XWikiConfig` (models.py:27) | **Yes** — review gate #1 | the credential model |
| `_build_auth_headers()` logic | **Yes** (copy or extract) | the only auth path; ~5 lines |
| `build_spaces_path()` (utils.py:20) | **Yes** | dotted-space → `/spaces/A/spaces/B` |
| `validate_creds()` (utils.py:30) | **Partially** | raises `ToolException`, which is a LangChain tool exception — a datasource loader should raise `MissingIntegrationException` instead so `index.set_error()` renders a sensible message |
| `_request()` | **No** | returns `(response, text)` and **never raises on 4xx/5xx**. A loader that calls it would treat a 403 as success. |
| `_format_result()` | **No** | returns a human string prefixed `"HTTP: GET … -> 200 OK\n"` — not parseable |
| `GetPageTool` for content | **No** | with `is_markdown=True` it runs `markdownify()` over the **raw JSON body** (because `_request` sets `Accept: application/json`), which is nonsense. The briefing's "GetPageTool markdownifies the response body" is literally true but misleading. |
| `_healthcheck()` | **No, as-is** | see §7 — it returns success against a guest-readable wiki even with wrong credentials |

**`XWIKI_FIELDS` omits `use_bearer`** [V] (`settings.py:219`). Compare `CONFLUENCE_FIELDS` (`:217`) which includes `IS_CLOUD: "cloud"`. Consequence: a stored xWiki integration cannot persist `use_bearer`, so a datasource resolving credentials through `SettingsService` will always get `use_bearer=False` (the `XWikiConfig` default) → **Basic auth only**. This aligns with the briefing's "test through Basic", but it is a *structural* limitation, not just a testing convenience.

---

### 2.6 Registration points — concrete shape

#### [V] Verified registration points

**a) `SettingsService.get_xwiki_creds()` — MISSING FROM THE BRIEFING.**
`src/codemie/service/settings/settings.py`. Every datasource type has a credential accessor: `get_jira_creds` (:1127), `get_confluence_creds` (:1145), `get_git_creds` (:1163), `get_svn_creds` (:1186), `get_azure_devops_creds` (:1223), `get_sharepoint_creds` (:1455). **There is no `get_xwiki_creds`.** The shape to add, modelled on `get_confluence_creds` (`:1144-1160`):

```python
@classmethod
def get_xwiki_creds(cls, user_id: str, project_name: str,
                    assistant_id: Optional[str] = None,
                    setting_id: Optional[str] = None,
                    tool_config: Optional[ToolConfig] = None) -> XWikiConfig:
    return cls.get_config(config_class=XWikiConfig, user_id=user_id,
                          project_name=project_name, assistant_id=assistant_id,
                          integration_id=setting_id, tool_config=tool_config)
```

`get_config` (`:707-752`) resolves `config_class → CredentialTypes` via `__CREDENTIAL_CONFIG_TO_TYPE`, where `XWikiConfig: CredentialTypes.XWIKI` already exists (`:224`) — so this is a pure addition with no mapping change.

**b) `settings_request_validator.py` — it is a DENY-LIST, not a "user-visible name" table.** The briefing describes this file as needing "user-visible name". Reading `src/codemie/service/settings/settings_request_validator.py:44-70` shows two frozensets:

```python
UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES = frozenset([          # :44
    "knowledge_base_file", "knowledge_base_sharepoint"])
UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES_HELP_MESSAGE = (...)   # :50

UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES = frozenset([            # :56
    "knowledge_base_file", "knowledge_base_sharepoint", "knowledge_base_xray",
    "knowledge_base_azure_devops_wiki", "knowledge_base_azure_devops_work_item",
    "platform_marketplace_assistant"])
UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES_HELP_MESSAGE = (...)     # :66
```

The correct change is therefore **inverted from the briefing's framing**:
- **Add** `"knowledge_base_xwiki"` to `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` and extend the help string at `:66-70` (xWiki has no webhook path in v1).
- **Do NOT add** it to `UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES` — scheduled reindex is in scope.

Omitting this lets a user attach a webhook trigger to an xWiki datasource that can never fire.

**c) `SettingsTester` — MISSING FROM THE BRIEFING.** `src/codemie/service/settings/settings_tester.py:80-97` is a `credential_type → handler` dict. `CredentialTypes.XWIKI` **has no entry**, so `test()` (`:72-78`) raises `SettingsTesterHandlerNotFound("Unsupported setting type: XWiki")` — the "Test connection" button on an xWiki integration is broken today. The fix is two lines, because `_XWikiBaseTool._healthcheck()` already exists and `CodeMieTool.healthcheck()` (`src/codemie_tools/base/codemie_tool.py:83-88`) wraps it into `Tuple[bool, str]`:

```python
CredentialTypes.XWIKI: SettingsTester._test_xwiki,
...
def _test_xwiki(self) -> Tuple[bool, str]:
    return ListWikisTool(config=XWikiConfig(**self.credential_values)).healthcheck()
```

Whether this is in scope for EPMCDME-13142 is a product call, but the datasource is not usable end-to-end without a way to validate the integration. See the §7 caveat about `_healthcheck` passing with wrong credentials.

**d) ES index name derivation.** `_index_name` resolves through `KnowledgeBase.get_identifier()` (`src/codemie/core/models.py:484-489`), which is:

```python
def get_identifier(self) -> str:
    return sanitize_es_index_name(self.name)          # models.py:488-489

def sanitize_es_index_name(name: str) -> str:         # models.py:467-477
    identifier = name.lower()
    for char in _ES_INDEX_INVALID_CHARS:
        identifier = identifier.replace(char, "_")
    return identifier
```

**The `type` field is passed to `KnowledgeBase(...)` but never used in the identifier.** The physical ES index name is therefore `lower(sanitize("{project_name}-{datasource_name}"))` — `INDEX_TYPE` participates only in `IndexInfo.index_type` (the DB row), not in the ES index. Two datasources of *different types* with the same project + name would share one ES index. This is pre-existing behaviour, identical for Confluence and ADO Wiki, so xWiki should follow it unchanged — but it is worth knowing when writing tests that assert on index names.

**e) `_init_index()` / mapping.** `_init_index()` does **not** create or configure any Elasticsearch mapping. It only constructs the `IndexInfo` DB row. The ES index is created later, inside `_load_and_process_documents`:

```python
store = self._get_store_by_index(self._index_name, embeddings_model)   # :690
store._store._create_index_if_not_exists()                             # :691
```

where `_get_store_by_index` is `get_elasticsearch(index_name, embeddings_model)` (`:1060-1062`, from `codemie.core.dependecies`). Mapping/dims are therefore owned by the shared Elasticsearch vector-store wiring, **not** per datasource type — so the xWiki work needs no ES mapping change. The internals of `get_elasticsearch` are **[U]** (not read).

**f) Makefile / quality gates.** `Makefile` has **zero `.PHONY` declarations** (verified: `grep -c PHONY Makefile` → `0`) and `verify: ruff license gitleaks test` (`Makefile:58`). A file named `LICENSE` exists at repo root. On macOS's case-insensitive filesystem, `make` sees the `license` target as satisfied by the `LICENSE` file and **skips it**. The briefing's warning is confirmed [V]. Run `make license-check` explicitly.

Test command is `make test` → `poetry run pytest tests/` (`Makefile:30-31`). `make test-harness` → `uvx codemie-test-harness --sanity-api` (`Makefile:73-74`).

#### [U] Registration points I did NOT inspect — treat as unverified

> **SUPERSEDED — see §9.** The orchestrator closed every gap in this table by direct read after
> this document was written. Read §9 for the verified shapes; the table below is retained only to
> show what the briefing alone did and did not establish. Where §9 and this table disagree, §9 wins.

These were delegated to parallel research threads that did not return before the session limit. **The briefing's mention counts are the only evidence; the concrete shape is unknown.**

| File | Briefing claim | Status |
|---|---|---|
| `src/external/alembic/versions/2b461b2f3d10_added_azure_devops_wiki_column_to_the_.py` | migration donor | **[U]** not read. Current alembic **head revision is unknown** — must be determined before writing a new migration. |
| `src/external/alembic/versions/3b3358380aa8_add_xwiki_credential_type.py` | credential type migration | **[U]** exists, contents not read |
| `index_info` SQLModel/ORM class | per-type JSONB columns | **[U]** not located. The briefing's live-DB observation (columns `azure_devops_wiki`, `azure_devops_work_item`, `confluence`, `jira`, `sharepoint` exist; `xwiki` does not) is **not independently verified here**. |
| `src/codemie/core/constants.py` (160 lines) — `DatasourceTypes` | add `XWIKI = "xwiki"` | **[U]** enum not read; exact member names/values unverified |
| `src/codemie/service/constants.py` (55 lines) — `FullDatasourceTypes` | add `XWIKI = "knowledge_base_xwiki"` | **[U]** not read. Cross-check: `settings_request_validator.py` and processor `INDEX_TYPE` constants both use the literal `"knowledge_base_<type>"`, which is consistent with the claim. |
| `src/codemie/datasource/datasources_config.py` (192 lines) | add loader config (batch size, chunk size/overlap, max pages) | **[U]** not read. Known from the donor processor: the object must expose `.loader_batch_size`, `.chunk_size`, `.chunk_overlap` [V, by usage at `azure_devops_wiki_datasource_processor.py:82,164,166`]. Its declaration site and env-var wiring are **[U]**. |
| `src/codemie/rest_api/models/index.py` (1798 lines) | `XWikiIndexInfo` + field on `IndexInfo` + validations | **[U]**. Two facts are known: `IndexInfo.new(...)` accepts a per-type kwarg (`azure_devops_wiki=AzureDevOpsWikiIndexInfo(...)`) [V by call site]; and `DatasourceHealthCheckRequest` at ~`:1257` is a flat model where each type adds optional fields (`svn_repo_url`, `svn_branch`, `git_url`) [V from commit `3a216a5ab`]. |
| `src/codemie/rest_api/routers/index.py` (2490 lines) | `POST`/`PUT /v1/index/knowledge_base/xwiki` | **[U]**. The path shape is corroborated from the UI side: `codemie-ui/src/store/dataSources.ts:430` posts to `v1/index/knowledge_base/azure_devops_wiki` [V]. |
| `src/codemie/service/index/datasource_health_check_service.py` (242 lines) | `case DatasourceTypes.XWIKI` + `health_check_xwiki()` | **Partially [V] via commit `3a216a5ab`** — see below. Full file **[U]**. |
| `src/codemie/triggers/actors/datasource.py`, `bindings/cron.py`, `bindings/utils.py`, `trigger_models.py` | scheduled reindex | **[U]** — none read. Whether the cron path calls `reprocess()` or `incremental_reindex()` per type is **unverified**; the briefing's "incremental refresh is free/inherited" claim is therefore **unconfirmed at the trigger layer**. |

**Health-check service — the one shape I do have** [V, from `git show 3a216a5ab`]. That commit is a complete, minimal example of adding one datasource type to the health check:

```python
# datasource_health_check_service.py — inside health_check_datasource()
                case DatasourceTypes.SVN:
                    return cls.health_check_svn(request, user_id)
+               case DatasourceTypes.GIT:
+                   return cls.health_check_git(request, user_id)
                case _:
                    return DatasourceHealthCheckResponse(implemented=False)
+       except ConnectionException as e:
+           return DatasourceHealthCheckResponse(
+               error=ErrorMessage(
+                   message=str(e),
+                   details=f"An error occurred while checking the connection: {str(e)}",
+                   help="Please check the repository URL and credentials, then try again."))
        except MissingIntegrationException as e: ...
```

plus a `@classmethod health_check_<type>(cls, request, user_id)` returning `DatasourceHealthCheckResponse(documents_count=N)` or `DatasourceHealthCheckResponse(error=ErrorMessage(message=..., details=..., help=..., field_error=...))`. The existing handlers already catch `MissingIntegrationException`, `UnauthorizedException`, `InvalidQueryException` and (since that commit) `ConnectionException`. `ErrorMessage` has a `field_error` slot for pointing at a specific form field — the right place for the "wrong base URL / `/xwiki` prefix" hint. That commit touched exactly 3 files (`index.py` +1 line, health check service +36, tests +111) — a realistic size signal.

#### Frontend (`codemie-ui`) — the briefing undercounts [V]

The briefing lists 7 files. `grep -rn "azure_devops_wiki|AzureDevOpsWiki"` in `~/Projects/Work/codemie-dev/codemie-ui/src` returns **11 distinct files**:

```
src/constants/dataSources.ts:30                     AZURE_DEVOPS_WIKI: 'azure_devops_wiki'
src/constants/assistants.ts:81                      AzureDevOpsWiki: 'Azure DevOps Wiki'      <- not in briefing
src/types/entity/dataSource.ts:119,174              per-type payload + IndexInfo field
src/utils/indexing.ts:55                            isAzureDevOpsWikiIndex()                  <- not in briefing
src/pages/dataSources/DataSourceEditPage.tsx:34,60                                            <- not in briefing
src/pages/dataSources/utils/dataSourceUtils.ts:26,78,113,210,216,217
src/pages/dataSources/components/DataSourceDetails.tsx:533,539
src/pages/dataSources/components/DataSourceForm/DataSourceForm.tsx:560                        <- not in briefing
src/pages/dataSources/components/DataSourceForm/IndexTypeField/IndexTypeAzureDevOpsWiki.tsx   <- NEW COMPONENT, not in briefing
src/pages/dataSources/components/DataSourceForm/IndexTypeField/index.ts:16,36                 <- not in briefing
src/pages/dataSources/components/DataSourceForm/hooks/useCreateIndex.ts:139,328,342           <- not in briefing
src/pages/dataSources/components/DataSourceForm/hooks/useEditPopupForm.ts:408,409
src/store/dataSources.ts:416,430                    createKBIndexAzureDevOpsWiki -> POST v1/index/knowledge_base/azure_devops_wiki
src/pages/assistants/components/ToolkitIcon.tsx:45                                            <- not in briefing
src/pages/dataSources/components/__tests__/DataSourceDetails.test.tsx:104
```

Note also `src/components/DataSourceDetails.tsx` from the briefing **does not exist**; the real path is `src/pages/dataSources/components/DataSourceDetails.tsx` [V]. The frontend work includes **writing a new form component** (`IndexTypeXWiki.tsx`, ~126 lines by analogy), which the briefing's "7 small files" framing hides.

---

## 3. Documentation Findings

`.ai-run/guides/` exists and is populated [V]. Directories: `agents/`, `api/`, `architecture/`, `data/`, `development/`, `integration/`, `standards/`, `testing/`, `workflows/`, plus `project.md` and `quality-gates.md`. `integration/request-hedging.md` exists but is absent from the AGENTS.md guide table — minor doc drift, not blocking.

Guides most relevant to this ticket (contents **[U]** except `quality-gates.md`, partially read):
- `.ai-run/guides/architecture/layered-architecture.md`, `service-layer-patterns.md`
- `.ai-run/guides/data/elasticsearch-integration.md`
- `.ai-run/guides/integration/confluence-integration.md` — the closest analogue for a wiki-style datasource
- `.ai-run/guides/development/error-handling.md` — governs the typed-exception choices in §2.3
- `.ai-run/guides/testing/testing-patterns.md`, `testing-service-patterns.md`
- `.ai-run/guides/quality-gates.md` [V, partial]: gate order is ruff → build → license → gitleaks → tests; `make ruff`, `make license-check`/`make license-fix`, `make gitleaks` (Docker-only), `make test`.

**No ADR was found** for datasource-type registration; the "how to add a datasource type" knowledge is implicit in the code and in the briefing. Recording it is arguably part of this ticket's value.

---

## 4. Testing Landscape

### [V] Verified

Test tree for datasources (`find tests -ipath '*datasource*'`):

```
tests/codemie/datasource/test_base_datasource_processor.py
tests/codemie/datasource/test_base_datasource_processor_llm_context.py
tests/codemie/datasource/test_confluence_datasource_processor.py
tests/codemie/datasource/azure_devops_wiki/test_azure_devops_wiki_datasource_processor.py
tests/codemie/datasource/azure_devops_work_item/test_azure_devops_work_item_datasource_processor.py
tests/codemie/datasource/jira/test_jira_datasource_processor.py
tests/codemie/datasource/google_doc/test_google_doc_datasource_processor.py
tests/codemie/datasource/sharepoint/, file/, code/, svn/, loader/, callback/, platform_tests/
tests/codemie/triggers/actors/test_actor_datasource.py
tests/codemie/service/index/test_datasource_health_check_service.py   (from commit 3a216a5ab)
```

**The mirror directory the briefing names already exists**: `tests/codemie_tools/core/project_management/xwiki/` with `conftest.py`, `test_tools.py`, `test_toolkit.py`, `test_models.py`, `test_tools_vars.py`, `test_utils.py` [V]. Its `conftest.py` (full read) defines exactly three fixtures:

```python
@pytest.fixture
def xwiki_config():          # XWikiConfig(url="https://wiki.example.com", token="secret-token", username="testuser")
@pytest.fixture
def xwiki_config_bearer():   # XWikiConfig(url=..., token="bearer-token", use_bearer=True)
@pytest.fixture
def mock_http_response():    # MagicMock with status_code=200, reason="OK", json()->{}
```

HTTP mocking is done with plain `unittest.mock.MagicMock`, not `responses`/`requests_mock`/`respx`. Note the fixture sets `.reason` while `_format_result` reads `.reason_phrase` (httpx) — a detail to check when reusing the fixture.

**New datasource tests should live under `tests/codemie/datasource/xwiki/`** (mirroring `azure_devops_wiki/`), separate from the tools tests. The briefing's "test dir mirrors `tests/codemie_tools/core/project_management/xwiki/`" is ambiguous — that path is the *tools* test dir, which already exists and is not where processor/loader tests belong.

### [U] Unverified

The contents of `test_azure_devops_wiki_datasource_processor.py`, `test_confluence_datasource_processor.py`, `test_base_datasource_processor.py`, `tests/codemie/datasource/loader/**`, the shared `conftest.py` files, the pytest configuration in `pyproject.toml` (markers, asyncio mode, coverage), and the available dev-dependency mocking libraries were **not read** — that thread did not return. What each test mocks (ES client / loader / embeddings / settings) is therefore unknown.

### Coverage gaps this ticket creates

- No test exists for any xWiki *datasource* code (none exists yet) — greenfield.
- No test exists exercising the `_check_docs_health` duck-typed contract generically.
- `tests/codemie/triggers/actors/test_actor_datasource.py` exists but its parametrisation over datasource types is **[U]** — if it enumerates types, adding `XWIKI` may require updating it.

---

## 5. Configuration and Environment

### [V]

- Credential storage: `CredentialTypes.XWIKI = "XWiki"` (`src/codemie_tools/base/models.py:105`), fields `XWIKI_FIELDS = {URL: "url", TOKEN: "token", USERNAME: "username"}` (`settings.py:219`), config mapping `XWikiConfig: CredentialTypes.XWIKI` (`settings.py:224`). **`use_bearer` is not storable** — see §2.5.
- `XWikiConfig` defaults are sourced via `get_tool_default("xwiki", "url"|"url_placeholder"|"use_bearer")` (`xwiki/models.py:32-48`), i.e. tool-defaults config, not env vars directly.
- Commands: `make ruff`, `make license-check`, `make gitleaks`, `make test`, `make verify` (`Makefile:30-58`).

### [U]

`src/codemie/datasource/datasources_config.py` — the declaration of `AZURE_DEVOPS_WIKI_CONFIG` / `CONFLUENCE_CONFIG` / `STORAGE_CONFIG` / `CODE_CONFIG`, their fields, defaults, and env-var names were **not read**. Known only by usage: the new `XWIKI_CONFIG` must expose `.loader_batch_size`, `.chunk_size`, `.chunk_overlap`; `STORAGE_CONFIG` supplies `indexing_threads_count`, `embeddings_max_docs_count`, `indexing_bulk_max_chunk_bytes`, `indexing_max_retries`, `indexing_error_retry_wait_min_seconds`, `indexing_error_retry_wait_max_seconds`, `indexing_heartbeat_interval` [V by usage in `base_datasource_processor.py`].

Deployment manifests / Helm values for a new datasource type: **[U]**.

---

## 6. Risk Indicators

Ordered roughly by likelihood of biting.

1. **`_check_docs_health()` is a phantom contract.** It is not on `BaseDatasourceProcessor` (verified: only 2 concrete processors define it, 2 call sites in `datasource_health_check_service.py:157,177`). Implementing it on the xWiki processor does nothing on its own — the `case DatasourceTypes.XWIKI:` arm in the health-check service is what activates it. A silent no-op health check is the failure mode.

2. **`/spaces/{S}/pages` does NOT include nested-space pages.** Verified live: `GET /rest/wikis/xwiki/spaces/KB/pages?number=1000` returns exactly 6 summaries (`Empty`, `Formatting`, `Onboarding`, `Vacation Policy`, `WebHome`, `Довідка`) and **omits** `KB.Onboarding.Checklist` / `KB.Onboarding.WebHome`, which are only reachable at `/spaces/KB/spaces/Onboarding/pages`. Indexing "a Space" therefore requires recursive descent. Cheapest route: `GET /rest/wikis/xwiki/spaces` returns a **flat** list of all spaces with dotted ids (`xwiki:KB`, `xwiki:KB.Onboarding`, …) that can be prefix-filtered on `KB.` — one call instead of an N-deep walk. A naive single-space implementation will silently drop child spaces and nobody will notice until a user asks about a nested page.

3. **`KB.Onboarding` is simultaneously a page in `KB` and a space whose home is `KB.Onboarding.WebHome`.** Verified live in both listings. A tree walk that treats "has children ⇒ is a space" or "is a space ⇒ not a page" will either duplicate or drop content. Both must be indexed, with distinct `source` values.

4. **There is no total-count field in any xWiki list response.** Verified live: the page-list payload has top-level keys `['links', 'pageSummaries']` only — no `totalSize` equivalent to Confluence's. So `fetch_remote_stats()` cannot be cheap; it must enumerate pages (across all descendant spaces) to produce `DOCUMENTS_COUNT_KEY`. That number drives the progress bar denominator (`base_datasource_processor.py:191-195`), and it is also what `_check_docs_health()` reports to the create-datasource form. Enumerate once and consider caching within the loader instance.

5. **Pagination is offset-based with no end signal.** Verified live: `?number=2&start=0` → `['Empty','Formatting']`; `?number=2&start=4` → `['WebHome','Довідка']`. There is no `next` link and no total, so the loop terminator must be "returned fewer than `number`" (the Confluence loader uses the same heuristic at `confluence_loader.py:146-148`). The existing `ListPagesTool` defaults to `number=50` (`tools.py:222`) and does not paginate at all — a space with >50 pages would be silently truncated if that tool were reused.

6. **Path encoding.** Verified live: `GET .../pages/Vacation Policy` (raw space) fails at the transport layer (curl exit, HTTP `000`); `.../pages/Vacation%20Policy` returns 200. Cyrillic `.../pages/Довідка` returns 200 when properly UTF-8/percent-encoded. `httpx` normalises spaces in a URL path automatically, so the existing tools happen to survive — but a loader that builds paths with `f"{base}/rest/.../pages/{name}"` and passes them to any other client, or that logs/dedupes on the raw string, must encode explicitly (`urllib.parse.quote(name, safe='')`). `build_spaces_path()` (`utils.py:20-27`) does **no** encoding and splits on `.`, so a space name containing a literal dot is inexpressible.

7. **`/rest/wikis` returns HTTP 200 for anonymous AND for wrong credentials** on the local instance. Verified live: `curl -u <user>:<wrong-password> http://localhost:8888/rest/wikis` → `200`; unauthenticated → `200`. xWiki grants guest read by default, so `_XWikiBaseTool._healthcheck()` (`tools.py:144-150`, "status != 200 ⇒ fail") **passes with a wrong password**. A health check built on it will green-light a broken integration; the user then sees an empty or partial index. The health check must probe something the *configured user* specifically can read, or assert on identity, not just on 200.

8. **Per-space access rights mean 401/403 arrives mid-load, not at connect time.** Combined with (7), the auth failure surfaces only when a restricted space is listed. `_XWikiBaseTool._request()` (`tools.py:108-129`) returns `(response, text)` and **never raises on 4xx** — any loader code reusing it would treat a 403 body as page content. The loader must check `response.status_code` and raise `UnauthorizedException("xWiki")` / `ConnectionException("xWiki", details)` so `index.set_error(str(ex))` (`base_datasource_processor.py:257`) renders a readable message. Review gate #3.

9. **Donor `fetch_remote_stats()` swallows all exceptions and reports 0.** `azure_devops_wiki_loader.py:778-780`. Copying the donor verbatim directly violates review gate #3. Let typed exceptions propagate.

10. **Empty content must be filtered by the loader, not the splitter.** Verified live: `KB.Empty` returns `content == ''` (length 0). `_split_documents` (`base_datasource_processor.py:621-645`) will call `split_text("")` → `[]` → the document contributes zero chunks and the `defaultdict` still records the key. Worse, if *every* page were empty, `_validate_indexing_result()` (`:555-564`) raises `NoChunksImportedException` and the whole datasource is marked failed. Skip empty pages in `lazy_load()` and count them under `SKIPPED_DOCUMENTS_KEY` via `get_load_stats()`.

11. **Raw wiki syntax vs rendered content — an explicit, unmade decision.** Verified live: `GET .../pages/Formatting` returns `content` as raw xwiki/2.1 (`'= Heading =\n\nSome **bold** text, a [[link>>Main.WebHome]] and a list:\n* one\n* two\n\n|=Col A|=Col B\n|1|2'`) plus `syntax: 'xwiki/2.1'`, and `renderedContent: None`. Indexing raw markup embeds `[[link>>Main.WebHome]]` and `|=Col A|` noise into the vectors. The alternative rendered endpoint `/bin/get/<Space>/<Page>?xpage=plain` is outside `/rest` and was **not probed in this run [U]** — the briefing reports it returns 200. This choice affects retrieval quality permanently and is cheap to change now, expensive later.

12. **`_process_chunk` is a metadata whitelist — `modified`/`version`/`author` are dropped by default.** Verified: ADO Wiki's `_process_chunk` (`azure_devops_wiki_datasource_processor.py:126-157`) rebuilds `metadata` from scratch; the base class re-applies only `chunk_num` (`:637-639`). The good news is that the fields exist: a page GET returns `modified: 1785836295000` (epoch ms), `version: '1.1'`, `majorVersion`/`minorVersion`, `author`, `creator`, `created`, and `xwikiAbsoluteUrl` — all verified live. But note **page *summaries* in list responses do NOT include `modified` or `content`** (verified: summary keys are `id, fullName, wiki, space, name, title, rawTitle, parent, parentId, version, author, xwikiRelativeUrl, xwikiAbsoluteUrl, translations, syntax`). A per-page GET is mandatory to obtain `modified` — meaning "cheap delta reindex from the list endpoint" is not available; `version` is, however, present in the summary and could serve as a change signal for a future ticket.

13. **`use_bearer` cannot be persisted.** `XWIKI_FIELDS` (`settings.py:219`) omits it, so credentials resolved from settings always fall back to Basic. Bearer additionally requires an xWiki server plugin (per the briefing). Document this; do not build a second auth path (review gate #1).

14. **`SettingsService.get_xwiki_creds()` does not exist** — an unlisted registration point (§2.6a). Without it, `_init_loader()` has no sanctioned way to resolve credentials.

15. **`SettingsTester` has no `CredentialTypes.XWIKI` handler** — "Test connection" on an xWiki integration raises `SettingsTesterHandlerNotFound` today (§2.6c).

16. **Webhook deny-list omission** — if `"knowledge_base_xwiki"` is not added to `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` (`settings_request_validator.py:56`), users can attach webhook triggers that can never fire (§2.6b).

17. **Alembic head is unknown [U].** A migration adding the `xwiki` JSONB column to `index_info` needs a correct `down_revision`. The head revision was not determined in this run. Getting it wrong produces a branched history that fails at deploy, not at test time.

18. **Source-of-truth for `DatasourceTypes` / `FullDatasourceTypes` unread [U].** Exact member naming and value conventions are assumed from the briefing, not verified.

19. **Trigger/cron layer entirely unread [U].** In particular, whether the scheduled path calls `reprocess()` (full) or `incremental_reindex()` per datasource type is unverified, so the briefing's "incremental refresh is free — inherited, no extra code" is **unconfirmed**. `incremental_reindex()` does exist on the base (`base_datasource_processor.py:422-443`) and only sets a flag before calling `process()` [V] — but which path the cron actor selects for a new type is the open question.

20. **`_split_documents` identity depends on a unique `source`** (`:624`, `:630`). If the xWiki loader derives `source` from, say, `space + name` without the wiki prefix, `KB.Onboarding` (page) and `KB.Onboarding.WebHome` (space home) could collide. Use `xwikiAbsoluteUrl` (unique, browser-openable, verified present on every summary) as `source`, mirroring the ADO Wiki donor.

21. **Base-URL shape ambiguity is a real support burden.** The local instance serves REST at `/rest/...` with no `/xwiki` prefix; xwiki.org serves `/xwiki/rest/...`. Code composes `url.rstrip('/') + '/rest/...'`, so both work only if the user typed the matching base URL. Because of risk (7), a wrong prefix produces a *connection* error while wrong credentials produce a *silent success* — two different failure modes needing two different `ErrorMessage.help` strings. `ErrorMessage` supports `field_error` for pointing at the URL field [V from commit `3a216a5ab`].

22. **`make verify` silently skips the license gate on macOS** (no `.PHONY`, `LICENSE` file shadows the `license` target) — verified. Run `make license-check` explicitly before the MR.

23. **Requirements-clarity risk: attachments.** The v1 decision "no attachments" is sound, but `ReadPageAttachmentContentTool`, `ListPageAttachmentsTool` etc. already exist in the tools layer (`tools.py:421-700`) and a page GET returns an `attachments` field. Scope creep is one `if` statement away.

---

## 7. Live-environment verification (performed in this run)

Probes against `http://localhost:8888` with Basic auth as the local admin user. All results below are **[V] — observed directly**.

| Probe | Result |
|---|---|
| `GET /rest/wikis/xwiki/spaces/KB/pages?number=1000` | 6 summaries: `Empty`, `Formatting`, `Onboarding`, `Vacation Policy`, `WebHome`, `Довідка`. Top-level keys `['links','pageSummaries']` — **no total count**. |
| `GET /rest/wikis/xwiki/spaces/KB/spaces/Onboarding/pages` | 2 summaries: `KB.Onboarding.Checklist`, `KB.Onboarding.WebHome` — **not** in the parent listing |
| `GET /rest/wikis/xwiki/spaces?number=100` | flat list of all spaces incl. `xwiki:KB` (home `xwiki:KB.WebHome`) and `xwiki:KB.Onboarding` (home `xwiki:KB.Onboarding.WebHome`), plus 13 `Help.*` spaces, `Main`, `Menu`, `Sandbox`, `XWiki` |
| `GET .../pages/Formatting` field list | `id, fullName, wiki, space, name, title, rawTitle, parent, parentId, version, author, xwikiRelativeUrl, xwikiAbsoluteUrl, translations, syntax, language, majorVersion, minorVersion, hidden, enforceRequiredRights, created, creator, modified, modifier, originalMetadataAuthor, comment, content, clazz, objects, attachments, hierarchy, rights, renderedContent` |
| `content` of `KB.Formatting` | raw `xwiki/2.1`: `= Heading =\n\nSome **bold** text, a [[link>>Main.WebHome]] and a list:\n* one\n* two\n\n\|=Col A\|=Col B\n\|1\|2` |
| `modified` / `created` | `1785836295000` (epoch **milliseconds**) |
| `renderedContent` | `None` — rendering is not available from the REST page resource |
| `GET .../pages/Vacation Policy` (raw space) | transport failure, HTTP `000` |
| `GET .../pages/Vacation%20Policy` | `200` |
| `GET .../pages/Довідка` (UTF-8) | `200` |
| `GET .../pages/Empty` | `content == ''`, length 0 |
| `GET /rest/wikis` unauthenticated | **`200`** |
| `GET /rest/wikis` with a deliberately wrong password | **`200`** |
| `?number=2&start=0` / `?number=2&start=4` | `['Empty','Formatting']` / `['WebHome','Довідка']` — offset pagination works, no `next` link |

Not probed (session ended): `/bin/get/<Space>/<Page>?xpage=plain` rendering, a wrong-base-URL request (`/xwiki/rest/wikis`), a 404 for a nonexistent page, and behaviour on a genuinely access-restricted space. **[U]**

---

## 8. Summary for Complexity Assessment

**Layers touched.** This is a wide-but-shallow change spanning eight backend layers plus the frontend: DB migration (Alembic + the `index_info` ORM model), domain constants (`core/constants.py`, `service/constants.py`), datasource config, a new loader, a new processor, REST models + router, the health-check service, the settings service (credential accessor, tester handler, webhook deny-list), and the trigger/cron bindings. Backend file count is realistically **14–17 files** (2 new: loader ~150–250 lines and processor ~180 lines; 1 new migration; the rest small edits), plus **11–12 frontend files including one new React form component**. The briefing's estimate of ~11 backend registration points and "7 small" frontend files is low on both counts — I found three unlisted backend points (`SettingsService.get_xwiki_creds`, `SettingsTester` handler, and the webhook deny-list, whose required change is the *inverse* of how the briefing describes that file) and five unlisted frontend files.

**Technical novelty.** Low at the framework level, moderate at the data level. The processor is close to a mechanical transcription of the 177-line ADO Wiki donor — only three methods are genuinely abstract (`_index_name`, `_init_loader`, `_init_index`), the rest have working defaults, and the batching/chunking/guardrail/retry/scheduling machinery is entirely inherited from the 1083-line base class. The loader is where the real work is, and it is *not* a transcription: xWiki's REST API differs from every existing donor in three ways that force original design — no total-count field (so `fetch_remote_stats()` must enumerate rather than ask), page summaries that omit both `content` and `modified` (so a second GET per page is mandatory), and a space listing that excludes descendant spaces (so "index a Space" requires recursive descent, with the `KB.Onboarding` page-vs-space collision as a live trap). The existing xWiki tools layer is also less reusable than the briefing implies: `_request()` never raises on 4xx, `_format_result()` returns human strings, and `GetPageTool`'s markdown mode markdownifies raw JSON — only `XWikiConfig`, `_build_auth_headers()` and `build_spaces_path()` should carry over.

**Test coverage posture.** Mixed, leaning thin. The tools layer is well covered (`tests/codemie_tools/core/project_management/xwiki/` has six test files and a conftest with three fixtures, mocking via plain `MagicMock`), and per-type processor tests exist for every comparable datasource, so there is a clear pattern to mirror at `tests/codemie/datasource/xwiki/`. But the *datasource* side of xWiki is entirely greenfield, and I was unable to read the donor processor/loader tests before the session limit — so what they mock (ES client, embeddings, settings) is unverified and TDD will start by reverse-engineering one donor test file. `_check_docs_health()` has no ABC enforcing it, so nothing fails loudly if the health-check arm is forgotten.

**Key risk factors for scoring.** Three stand out. (1) The health-check story is worse than it looks: xWiki grants guest read by default, so `/rest/wikis` returns 200 even with a wrong password — verified live — meaning the naive health check green-lights broken integrations, and per-space 403s only surface mid-load where `_request()` currently swallows them. (2) Unknowns concentrated in exactly the places that fail at deploy rather than at test time: the current Alembic head revision is unverified, and the whole trigger/cron layer is unread, so the briefing's "incremental refresh is free" claim is unconfirmed at the layer that actually selects `reprocess()` vs `incremental_reindex()`. (3) Two content decisions are cheap now and expensive later — whether to index raw `xwiki/2.1` markup or rendered text, and whether `_process_chunk` carries `modified`/`version`/`author` (it drops them by default, since it is a whitelist and the base class re-applies only `chunk_num`); getting the metadata wrong forces a full reindex to fix. Encoding (`%20`, Cyrillic), empty pages that must be skipped before `_validate_indexing_result()` sees zero chunks, and offset pagination with no end signal are all real but well-understood, and all are already covered by the existing fixture pages in the live `KB` space.

---

## 9. Verification addendum — every [U] registration point closed

Added by the orchestrator after the research agent hit its session limit. Everything in this
section was read directly from source in this repo at HEAD `3e71dc072`. **This section supersedes
the `[U]` table in §2.6 and risk items 17, 18 and 19.**

### 9.1 The headline correction — scheduled reindex is a FULL reindex, not incremental

The briefing states: *"Incremental refresh — **Free**, inherited, no extra code."* **That is wrong**,
and it is the most consequential error in the hand-off.

| Call site | What it runs |
|---|---|
| `triggers/actors/datasource.py:425` — `reindex_azure_devops_wiki` | `datasource_concurrency_manager.run(processor.reprocess, ...)` |
| `triggers/actors/datasource.py:215` — Jira | `processor.incremental_reindex` |
| `triggers/actors/datasource.py:551` — Xray | `processor.incremental_reindex` |
| Confluence (`:292`), Google (`:349`), SharePoint (`:498`), code (`:108`), SVN (`:156`) | `processor.reprocess` |

And `reprocess()` (`base_datasource_processor.py:379-397`) is:

```python
self.is_full_reindex = True
self._cleanup_data()      # -> client.indices.delete(index=self._index_name)   :445-454
self.process()
```

So mirroring the ADO Wiki donor gives xWiki a **scheduled cron reindex that deletes the entire ES
index and rebuilds it from scratch**. `incremental_reindex()` is opted into per datasource type at
the actor, and only the two types that also implement `_cleanup_data_for_incremental_reindex`
(Jira `jira_datasource_processor.py:99`, Xray `xray_datasource_processor.py:97`) use it.

**Consequence for the spec.** Review gate #3 asks us to "state incremental refresh behavior
explicitly rather than implementing a parallel path". The honest statement is: *v1 xWiki scheduled
refresh is a full delete-and-rebuild, identical to Azure DevOps Wiki, Confluence, SharePoint and
Google Doc. True incremental refresh is a separate ticket and would require a
`_cleanup_data_for_incremental_reindex()` implementation plus switching the actor to
`incremental_reindex`.* Do not claim incremental refresh works.

### 9.2 Alembic — single head, verified

147 revision files; computing heads with tuple-aware `down_revision` parsing yields **exactly one
head: `t1u2v3w4x5y6`** (`t1u2v3w4x5y6_add_sort_indexes_to_assistants.py`). That is the
`down_revision` for the new migration. (An earlier naive scan suggested 11 heads — that was a
regex artifact from merge migrations whose `down_revision` is a tuple. There is no branched history.)

Donor `2b461b2f3d10_added_azure_devops_wiki_column_to_the_.py`, read in full — the whole migration is:

```python
def upgrade() -> None:
    op.add_column('index_info', sa.Column('azure_devops_wiki',
                  postgresql.JSONB(astext_type=sa.Text()), nullable=True))

def downgrade() -> None:
    op.drop_column('index_info', 'azure_devops_wiki')
```

### 9.3 The two enums — exact current contents

`src/codemie/core/constants.py:91` — note there is **no `SHAREPOINT` member**, so this enum is not
a complete list of datasource types:

```python
class DatasourceTypes(str, Enum):
    GIT = "git"; SVN = "svn"; CONFLUENCE = "confluence"; JIRA = "jira"
    FILE = "file"; JSON = "json"; GOOGLE = "google"
    AZURE_DEVOPS_WIKI = "azure_devops_wiki"; AZURE_DEVOPS_WORK_ITEM = "azure_devops_work_item"
    XRAY = "xray"
```
→ add `XWIKI = "xwiki"`.

`src/codemie/service/constants.py` — likewise partial (no SVN/XRAY/SHAREPOINT members; those are
used as bare string literals elsewhere):

```python
class FullDatasourceTypes(str, Enum):
    GIT = "code"; CONFLUENCE = "knowledge_base_confluence"; JIRA = "knowledge_base_jira"
    FILE = "knowledge_base_file"; GOOGLE = "llm_routing_google"
    AZURE_DEVOPS_WIKI = "knowledge_base_azure_devops_wiki"
    AZURE_DEVOPS_WORK_ITEM = "knowledge_base_azure_devops_work_item"
    PROVIDER = ProviderIndexType.PROVIDER.value
    PLATFORM_ASSISTANT = "platform_marketplace_assistant"
```
→ add `XWIKI = "knowledge_base_xwiki"`.

### 9.4 `datasources_config.py` + a YAML file the briefing never mentions

The config is **YAML-backed**, so there are two files, not one:

- `src/codemie/datasource/datasources_config.py` — needs a new `BaseModel`, a field on
  `LoadersConfig` (`:124-135`), a module-level constant (`:175` area) and a `logger.info` line.
- **`config/datasources/datasources-config.yaml:143`** — a new `xwiki_loader:` block. **This file is
  absent from the briefing's registration table.** `LoadersConfig` has no defaults for these
  fields, so a missing YAML block is a **Pydantic validation error at import time** — the whole app
  fails to boot, not just the xWiki path.

Donor pair:

```python
class AzureDevOpsWikiConfig(BaseModel):        # datasources_config.py:87
    chunk_size: int; chunk_overlap: int; loader_batch_size: int
```
```yaml
  azure_devops_wiki_loader:                    # datasources-config.yaml:143
    chunk_size: 1000
    chunk_overlap: 50
    loader_batch_size: 50
```

`ConfluenceConfig` (`:72`) additionally carries `loader_max_pages` — the right precedent if xWiki
wants a page cap.

> **Naming collision.** `XWikiConfig` is already taken by the credential model in
> `codemie_tools/core/project_management/xwiki/models.py:27`. The datasources_config class must be
> named something else — `XWikiDatasourceConfig` — or the router/processor imports will clash.

### 9.5 `rest_api/models/index.py` — five distinct edits

| Line | Edit |
|---|---|
| `:95` | add `"knowledge_base_xwiki"` to the `IndexTypeByContextTypeMapping.KNOWLEDGE_BASE` tuple |
| `:155` | new `class XWikiIndexInfo(BaseModel)` — donor: `AzureDevOpsWikiIndexInfo(wiki_query: str, wiki_name: Optional[str])` |
| `:285` | new `IndexInfo` column: `xwiki: Optional[XWikiIndexInfo] = SQLField(default=None, sa_column=Column(PydanticType(XWikiIndexInfo)))` |
| `:682` + `:707` | `IndexInfo.new()` — `xwiki = kwargs.get("xwiki")` and pass `xwiki=xwiki` to `cls(...)` |
| `:1441` / `:1622` | `IndexKnowledgeBaseXWikiRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest)` and `UpdateKnowledgeBaseXWikiRequest(CronExpressionValidatorMixin, BaseModel)` |

Both request models carry the same tail: `setting_id`, `embedding_model`, `cron_expression`; the
update variant adds `name`, `project_name`, `new_project_name`, `description`,
`project_space_visible`, `guardrail_assignments`. `SharePointIndexInfo` (`:167`) shows the
`@field_validator` pattern if the space/base-URL field needs validation.

There is also an update-path helper around `:755-766` (`_update_*_fields`) that mutates the JSONB
column and calls `flag_modified(self, '<column>')` — required, or SQLAlchemy will not persist an
in-place edit to the JSONB field.

### 9.6 `rest_api/routers/index.py` — two endpoints

`POST /index/knowledge_base/azure_devops_wiki` at `:1145-1157` and
`PUT /index/knowledge_base/azure_devops_wiki` at `:1695-1750`, plus three import blocks
(`:69`, `:89`, `:104`). The create path ends in
`datasource_processor.schedule(background_tasks, datasource_processor.reprocess)` (`:1365`/`:1427`).
Mirror as `/index/knowledge_base/xwiki`.

### 9.7 `datasource_health_check_service.py` — three edits, shape confirmed

```python
# :49-50  in health_check_datasource()
case DatasourceTypes.XWIKI:
    return cls.health_check_xwiki(request, user_id)

# ~:142  new classmethod, donor at :142-158
@classmethod
def health_check_xwiki(cls, request, user_id):
    creds = SettingsService.get_xwiki_creds(user_id=user_id, project_name=request.project_name)
    processor = XWikiDatasourceProcessor(datasource_name="health_check", user=None, ...)
    return DatasourceHealthCheckResponse(documents_count=processor._check_docs_health())

# :237  get_invalid_field() — return the xWiki form field name (e.g. "space")
```

The wrapping `try` already catches `ConnectionException` (`:59`), `MissingIntegrationException`,
`UnauthorizedException` and `InvalidQueryException`, each mapping to an
`ErrorMessage(message, details, help)` — so raising the right typed exception from the loader is
sufficient; no new handler is needed. `get_invalid_field()` is what populates
`ErrorMessage.field_error`, which is the hook for the "wrong base URL / `/xwiki` prefix" hint
(risk 21).

### 9.8 Triggers — four files, and one that must be left alone

| File | Edit |
|---|---|
| `triggers/trigger_models.py:59` | `class XWikiReindexTask(ReindexTaskPayload)` with `xwiki_index_info: XWikiIndexInfo` |
| `triggers/actors/datasource.py:360-430` | `def reindex_xwiki(payload)` — resolve creds, guard missing index info, build processor, `run(processor.reprocess, ...)` (see §9.1) |
| `triggers/actors/datasource.py:917` | `_RESUME_DISPATCH["knowledge_base_xwiki"] = _resume_xwiki` (+ the `_resume_xwiki` function, donor `_resume_azure_devops` at `:659-664`) — this is the stale-job watchdog; omitting it means a stuck xWiki index is never resumed |
| `triggers/bindings/cron.py:621-636` | `elif index_type_str == FullDatasourceTypes.XWIKI.value:` branch building the payload and `scheduler.add_job(reindex_xwiki, ...)` |
| `triggers/bindings/utils.py:70` | **do NOT add.** That list is `validate_datasource()`, the *webhook* allow-list; xWiki has no webhook path in v1. Consistent with adding `"knowledge_base_xwiki"` to `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` (§2.6b). |

### 9.9 Revised registration inventory

Backend, all verified: **1** new migration · **2** new modules (loader, processor) · **13** edited
files (`core/constants.py`, `service/constants.py`, `datasources_config.py`,
`config/datasources/datasources-config.yaml`, `rest_api/models/index.py`,
`rest_api/routers/index.py`, `service/index/datasource_health_check_service.py`,
`service/settings/settings.py`, `service/settings/settings_tester.py`,
`service/settings/settings_request_validator.py`, `triggers/trigger_models.py`,
`triggers/actors/datasource.py`, `triggers/bindings/cron.py`) = **16 backend files**, plus tests
and **11–12 frontend files**. Three of these — the YAML config block, `get_xwiki_creds`, and the
`_RESUME_DISPATCH` entry — are absent from the briefing and each fails silently or at boot rather
than at test time.
