---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 45c1d461b91adf8fd07bd25fb8885b46cbf5bc57
---

# Code review — 2026-10-21-datasource-indexing-slot-hang

**Decision:** approve (final round, confidence medium)  
**Reviewer verdict:** request-changes on CR-001; resolved by the user at gate `code-review.final` as approve, with CR-001 accepted as out of scope (Vertex and Bedrock embeddings are non-goals of the approved spec). See `decisions.jsonl`.  
**Reviewed head:** `45c1d46` · **Rounds:** final

Edge-case and acceptance lenses and standards audit ran; all criteria pass (3 pending-stage-7). One finding survives: the Vertex AI embedding branch has no request timeout (reachability only when no LiteLLM proxy embeddings, unconfirmed). 3 filtered as noise: Bedrock already bounded by AWS_BEDROCK_READ_TIMEOUT, per-read timeout and retry multiplication are spec-accepted, timeout<=0 is operator misconfig; 0 deferred.

## Accepted as out of scope — 1

### CR-001 · code · major — Vertex AI embeddings lack request timeout

**Resolution:** accepted by the user as out of scope; Vertex AI and Bedrock embeddings are listed non-goals in the approved spec.

`src/codemie/core/dependecies.py:79` · not re-checked

**Problem:** Only the Azure branch of get_embeddings_model received request_timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS. The GOOGLE_VERTEX_AI branch builds VertexAIEmbeddings with max_retries=10 and no timeout, so a stalled Vertex embedding call has no bound. (The AWS_BEDROCK branch is already bounded by read_timeout=AWS_BEDROCK_READ_TIMEOUT in get_bedrock_runtime_client.)

**Impact:** A hung Vertex embedding request can still hold the datasource indexing slot indefinitely, which is the failure EPMCDME-16237 targets. Reachability unconfirmed: the branch runs only when get_litellm_embedding_model returns no model, so severity is capped at major.

**Recommendation:** Pass the configured request timeout to VertexAIEmbeddings (request_timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS, using the parameter name the installed langchain_google_vertexai supports), with a unit test asserting it, or record an explicit decision that Vertex embeddings are out of scope.

## Coverage

| Acceptance criterion | Status | Notes |
|---|---|---|
| R1. run() gains keyword-only datasource_name; name resolution; SlotAcquired/SlotReleased logging; queue-timeout log; disabled limit no logs | pass | datasource_concurrency_manager.py matches spec; covered by manager tests |
| R2. run_in_background passes datasource_name=git_repo_name to run() | pass | code_datasource_processor.py:175 with test |
| R3. Config.DATASOURCE_REQUEST_TIMEOUT_SECONDS: int = 600 on central Config | pass | config.py:193 default 600; config tests |
| R4. timeout on three wiki session.get calls; failure paths unchanged | pass | azure_devops_wiki_loader.py lines 429, 525, 564; six tests |
| R5. request_timeout in llm_factory params and native AzureOpenAIEmbeddings; max_retries=10 kept | pass | llm_factory.py:374 and dependecies.py:103 |
| R5. Timeout must reach the OpenAI client for LiteLLMAzureOpenAIEmbeddings | pending-stage-7 | test asserts client timeout; needs test execution |
| Non-goals respected | pass | changes confined to spec'd modules and tests |
| AC1-AC7. Slot logging, release on failure, name resolution, queue-timeout name, disabled mode, config default, wiki timeouts | pass | each covered by new unit tests |
| AC8. Both embedding factories set request_timeout and built LiteLLM client carries it | pending-stage-7 | code and tests present; client attribute path needs test run |
| AC9. Existing tests pass; new tests cover manager and timeouts | pending-stage-7 | full-suite result is Stage 7 execution evidence |

Standards: commit-format pass · code-quality pass · security pass
