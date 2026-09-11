# Git FAQ Retrieval — Decision Record

**Story:** EPMCDME-11696 (Git FAQ datasource) · FU-1 (retrieval strategy for large articles)
**Date:** 2026-09-01 · **Status:** Decided — Option A is the plan of record

---

## 1. Where we are

The Git FAQ datasource is complete end-to-end: repo clone → markdown parsing (title / instructions /
reference) → FU-2 flexible file filtering (`faq_path` + gitignore-style `files_filter`) → indexing →
retrieval via the `search_kb` tool. Webhook/cron reindex, edit semantics, SDK (Python/Node/MCP) and
UI are all in place.

The selected retrieval model for large articles is **Option A: chunked hybrid retrieval using the
Confluence processing model**. It was implemented and verified against the local stack (ES + Azure
embeddings). The remaining options are retained for decision history and possible future evolution;
they are not the implementation contract.

## 2. Retrieval strategies that already exist in this codebase

The team does not need to invent anything — the repo ships six distinct retrieval patterns today:

| # | Strategy | Where | Mechanics |
|---|---|---|---|
| 1 | **Chunked hybrid + RRF** | `service/search_and_rerank/kb.py` (Confluence, Jira, SharePoint, files, and now Git FAQ) | kNN vectors + LLM source routing over aggregated sources + BM25 `match_phrase`, fused client-side with RRF (k=60) |
| 2 | **Whole-document LLM routing** | `agents/tools/kb/search_kb.py:303` (Google Docs FAQ) | TOC stored in ES `_meta` → routing LLM picks articles by reference → whole article returned |
| 3 | **LLM relevance filter post-retrieval** | `agents/tools/code/base_tools.py:66` (code search `*_v2` tools) | Retrieval first, then a batched LLM filters chunks by relevance (`with_structured_output`) |
| 4 | **Index-time contextual enrichment** | `datasource/code/code_summary_datasource_processor.py:83` (chunk-summary) | LLM-generated summary prepended to each chunk at index time (Anthropic-contextual-retrieval-like) |
| 5 | **Navigate + fetch (agentic, no vectors)** | `agents/tools/code/` toolkit (repo tree + read file) | LLM lists/prunes the file tree, then fetches whole files by path; pure keyword/phrase ES |
| 6 | **Delegated external KB** | `service/aws_bedrock/...` (Bedrock) | Indexing and retrieval fully owned by the provider |

Not present anywhere in the repo (for completeness): cross-encoder/hosted rerankers, parent-child
(small-to-big) retrieval, HyDE, query expansion, GraphRAG, sparse vectors/ELSER, ES-native RRF.

## 3. Candidate strategies for Git FAQ

### Option A — Chunked hybrid (Confluence model) · **IMPLEMENTED**

Confluence's exact pipeline: `MarkdownHeaderTextSplitter` (H1–H3) → window-join sections (3/1,
headers inlined) → size split (2000 tok / 30 overlap) → embeddings → `SearchAndRerankKB`
(kNN + BM25 + RRF + LLM source routing + coverage notices).

- **Pros:** maximal reuse (one retrieval path for all KB types); per-section granularity solves
  large-article blowout; LLM source routing preserved (Confluence-style, over chunk metadata);
  embeddings make synonym-style questions work.
- **Cons:** embedding cost per chunk; answer quality depends on chunk boundaries; whole-article
  overview questions get section fragments (mitigated by window join + coverage notices).
- **Status:** live-verified: 2 articles → 3 chunks, rollback question retrieved the Rollbacks
  section, small articles returned whole, delete/reindex lifecycle clean.

### Option B — Two-stage: route, then section-slice

Stage 1 unchanged from the original FAQ design: TOC → routing LLM → article selection.
Stage 2: articles under a token threshold returned whole; large ones sliced to relevant sections
(lexical or hybrid, scoped to the selected article).

- **Pros:** exact-article precision of whole-doc routing; no embeddings needed if stage 2 is
  lexical; normal articles behave exactly as the original demoed design.
- **Cons:** two retrieval systems to maintain; TOC prompt size grows with article count; routing
  misses at article level are unrecoverable at section level.
- **Migration from A:** moderate — parser/loader/filter/SDK/FE all carry over; needs the TOC
  machinery re-wired for FAQ (`BaseLLMRoutingDatasourceProcessor` still exists, Google uses it) plus
  a scoped section-search path.

### Option C — Section-level TOC routing

Extend the TOC to H2 sections; the routing LLM picks sections directly; no embeddings.

- **Pros:** single representation; explainable routing.
- **Cons:** TOC grows ~10× (routing accuracy/latency/cost degrade); loses whole-article answers;
  every article edit reshapes the TOC.
- **Migration from A:** large — new TOC format, new prompt, retrieval rewrite.

### Option D — A + LLM relevance filter (layered)

Keep A, add the code-search pattern: after RRF, an LLM filters the top chunks before they reach the
conversation (`_filter_documents_by_relevance`, batched).

- **Pros:** cheap add-on on top of A; improves precision in noisy corpora; proven in code search v2.
- **Cons:** +1 LLM call per search (latency/cost); recall risk if the filter is over-aggressive.
- **Migration from A:** small — one flag on the search path.

### Option E — A + index-time contextual enrichment

Prepend an LLM-generated article/section context line to each chunk at index time (the
chunk-summary pattern), on top of the current `Article title:/Source:` prefix.

- **Pros:** best expected retrieval quality for terse sections whose meaning depends on the
  article; the FAQ parser's frontmatter (`instructions`) already gives a manual version of this.
- **Cons:** LLM cost per chunk per reindex; index bloat; reindex required to benefit.
- **Migration from A:** small-medium — one processor hook; cost scales with corpus.

### Option F — Navigate + fetch (agentic)

No embeddings: expose the article list (titles/references) + a fetch-article tool; let the agent
pick and read articles like the code toolkit does with files.

- **Pros:** zero embedding cost; agent sees whole articles (no chunking artifacts); trivially
  explainable.
- **Cons:** highest per-query token cost (agent loops); slowest; no semantic matching; unbounded
  output for large articles returns (the original problem).
- **Migration from A:** orthogonal — could complement A (an explicit "list FAQ articles" tool)
  rather than replace it.

## 4. Comparison matrix

| | Retrieval quality (large articles) | Query cost/latency | Index cost | Ops complexity | Distance from A |
|---|---|---|---|---|---|
| **A (implemented)** | High | Low-med (embeddings + routing LLM) | Med (embed per chunk) | Low (shared with Confluence) | — |
| B | High (article-precise) | Low + routing LLM | None (lexical) or med | Med (two systems) | Moderate |
| C | Medium (TOC-dependent) | Med (big routing prompt) | None | Med | Large |
| D | Highest precision | Med-high (+filter LLM) | Med | Low | Small |
| E | Highest recall on terse sections | Low-med | High (LLM per chunk) | Low | Small-med |
| F | Medium (agent-dependent) | High (multi-step) | None | Low | Orthogonal |

## 5. Decision

1. **Option A is the plan of record.** It is the team lead's stated direction (Confluence-basis
   markdown parsing), it is the same retrieval path as every other KB datasource, and it is already
  verified working. Git FAQ therefore uses `knowledge_base_git_faq`, embedded H1-H3 section chunks,
  3/1 window joining, and the standard `SearchAndRerankKB` path.
2. **Treat D and E as incremental dials on A**, decision-deferrable: they can be added later
   behind flags if retrieval quality in real corpora demands it — no architectural commitment today.
3. **Options B and C are rejected for this story.** Either would create a second FAQ-only retrieval
  system based on TOC routing. Reconsidering one requires a new architecture decision rather than a
  documentation-only change.

## 6. Demo evidence available today

- Large multi-section article indexed into window-joined chunks with `title`/`source`/`header`/
  `chunk_num`/`reference` metadata; ES mapping with `text` + `vector`.
- "Roll back a failed deployment" → hybrid retrieval returned the Rollbacks section (LLM source
  routing selected the right article); small article returned whole for its question.
- Folder exclusion (`!faq-folder/troubleshooting`) — files under the excluded folder never enter
  the index (FU-2), verified via `metadata.source` inspection.
- Delete/reindex lifecycle leaves no orphaned ES indexes; webhook push → reindex fires.
