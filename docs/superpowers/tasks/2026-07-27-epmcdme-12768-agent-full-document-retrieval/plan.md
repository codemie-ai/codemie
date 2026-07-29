# Self-describing retrieval results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a knowledge-base search response state how much of each document it contains, so the model can tell a complete answer from a partial one and knows how to obtain the rest.

**Architecture:** The per-source chunk total is already computed by an Elasticsearch aggregation the search path runs; it is currently discarded after a size check. Carry it out of the search layer and let the tool's response formatter state, per source, how many parts exist and which are present, followed by an instruction to query again for the rest and not to invent it.

**Tech Stack:** Python 3.12, Poetry, pytest, Elasticsearch, LangChain `Document`.

## Global Constraints

- Commit subject format: `EPMCDME-12768: <Short description>` — ticket first, not Conventional Commits.
- Never mention the ticket id, EPAM, or internal URLs inside source or test files.
- Quality gates are Makefile targets: `make ruff`, `make test`.
- License headers stay at the top of every touched file.
- This change is visible to every knowledge-base assistant, not only SharePoint. Wording added to the response is part of the contract with the model and must be unambiguous.

---

## File Structure

- `src/codemie/service/search_and_rerank/kb.py` — stops discarding the per-source chunk total and exposes it alongside the results.
- `src/codemie/agents/tools/kb/search_kb.py` — response formatter states coverage per source and appends the actionable notice.
- `tests/codemie/service/search_and_rerank/` — coverage for the total being carried out.
- `tests/codemie/agents/tools/kb/` — coverage for the formatted response.

No config change, no migration, no API change.

---

### Task 1: Carry the per-source chunk total out of the search layer

**Files:**
- Modify: `src/codemie/service/search_and_rerank/kb.py` (`_fetch_unique_sources`, `execute`)
- Test: `tests/codemie/service/search_and_rerank/test_kb_source_totals.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SearchAndRerankKB.source_chunk_totals: dict[str, int]` — populated during `execute()`, mapping the source value to the number of indexed chunks that source has. Empty when the routing aggregation did not run.

**Test-first: yes — a test asserting the totals map is populated after execute(); it fails because the attribute does not exist.**

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/service/search_and_rerank/test_kb_source_totals.py` with the license header used by its sibling tests, then:

```python
from unittest.mock import MagicMock, patch

from codemie.service.search_and_rerank.kb import SearchAndRerankKB


def _aggregation_response() -> dict:
    return {
        "aggregations": {
            "unique_sources": {
                "buckets": [
                    {
                        "key": "https://host/report.docx",
                        "doc_count": 4,
                        "source_metadata": {
                            "hits": {"hits": [{"_source": {"metadata": {"source": "https://host/report.docx"}}}]}
                        },
                    }
                ]
            }
        }
    }


def test_execute_records_chunk_total_per_source(kb_search):
    with patch.object(type(kb_search), "_knn_vector_search", return_value=[]), \
         patch.object(type(kb_search), "_text_search", return_value=[]), \
         patch("codemie.service.search_and_rerank.kb.ElasticSearchClient") as es:
        es.get_client.return_value.search.return_value = _aggregation_response()
        kb_search.chain = MagicMock()
        kb_search.chain.invoke.return_value = []

        kb_search.execute()

    assert kb_search.source_chunk_totals == {"https://host/report.docx": 4}
```

Add a `kb_search` fixture in the same file constructing `SearchAndRerankKB` with a stub `kb_index`, `query="q"`, `llm_model="gpt-4.1"`, `top_k=10`, `request_id="r"`, mirroring how sibling tests in `tests/codemie/service/search_and_rerank/` build their subjects. Read one of those files first and follow its construction exactly rather than inventing a new shape.

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/codemie/service/search_and_rerank/test_kb_source_totals.py -v`
Expected: FAIL — `AttributeError: 'SearchAndRerankKB' object has no attribute 'source_chunk_totals'`.

- [ ] **Step 3: Initialize the map**

In `SearchAndRerankKB.__post_init__`, after the existing assignments, add:

```python
        # Number of indexed chunks per source, captured from the routing aggregation.
        # Lets the tool response state how much of a document it actually carries.
        self.source_chunk_totals: dict[str, int] = {}
```

- [ ] **Step 4: Record the total instead of discarding it**

In `_fetch_unique_sources`, inside the bucket loop, the count is already computed as `chunks_count`. Record it before the size check so the value is kept for every source the aggregation returned:

```python
        for bucket in results.get("aggregations", {}).get("unique_sources", {}).get("buckets", []):
            chunks_count = bucket["doc_count"]  # Number of chunks for this source
            self.source_chunk_totals[bucket["key"]] = chunks_count
```

Leave the existing `if chunks_count <= self.MAX_CHUNKS_FOR_SINGLE_DOCUMENT:` branch and everything below it untouched.

- [ ] **Step 5: Run the test to verify it passes**

Run: `poetry run pytest tests/codemie/service/search_and_rerank/ -v`
Expected: PASS, including the pre-existing tests in that directory.

- [ ] **Step 6: Lint and commit**

```bash
make ruff
git add src/codemie/service/search_and_rerank/kb.py tests/codemie/service/search_and_rerank/test_kb_source_totals.py
git commit -m "EPMCDME-12768: Record per-source chunk totals during knowledge base search"
```

---

### Task 2: State coverage and the follow-up instruction in the response

**Files:**
- Modify: `src/codemie/agents/tools/kb/search_kb.py` (`format_document`, `format_response`)
- Test: `tests/codemie/agents/tools/kb/test_search_kb_completeness.py` (new)

**Interfaces:**
- Consumes: `SearchAndRerankKB.source_chunk_totals` from Task 1.
- Produces: no new public symbols; the tool's response text gains a coverage line per source and a trailing notice when any source is partial.

**Test-first: yes — tests asserting the response declares partial and complete coverage; they fail because the response contains no coverage statement at all.**

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/agents/tools/kb/test_search_kb_completeness.py` with the license header, then:

```python
from langchain_core.documents import Document


def _doc(source: str, chunk_num: int, text: str) -> Document:
    return Document(page_content=text, metadata={"source": source, "chunk_num": chunk_num})


def test_partial_coverage_is_declared(search_kb_tool):
    source = "https://host/report.docx"
    search_kb_tool.source_chunk_totals = {source: 4}

    result = search_kb_tool.format_response([_doc(source, 1, "part one"), _doc(source, 2, "part two")])

    assert "2 of 4" in result
    assert "narrower" in result.lower()
    assert "do not" in result.lower()


def test_complete_coverage_carries_no_omission_claim(search_kb_tool):
    source = "https://host/report.docx"
    search_kb_tool.source_chunk_totals = {source: 2}

    result = search_kb_tool.format_response([_doc(source, 1, "part one"), _doc(source, 2, "part two")])

    assert "2 of 2" in result
    assert "narrower" not in result.lower()


def test_unknown_total_makes_no_claim(search_kb_tool):
    source = "https://host/report.docx"
    search_kb_tool.source_chunk_totals = {}

    result = search_kb_tool.format_response([_doc(source, 1, "part one")])

    assert " of " not in result.split("**File Content:**")[0]
```

Add a `search_kb_tool` fixture constructing the tool the way existing tests under `tests/codemie/agents/tools/` build tools. Read a sibling test first and follow it; do not invent a construction shape.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/tools/kb/test_search_kb_completeness.py -v`
Expected: FAIL — the formatted response contains no coverage statement, so the `"2 of 4"` assertion fails first.

- [ ] **Step 3: Hold the totals on the tool**

In `SearchKBTool`, add an attribute alongside the existing fields so the formatter can reach the totals:

```python
    source_chunk_totals: dict = Field(default_factory=dict)
```

Populate it where the search is executed, replacing the current direct call with a bound instance:

```python
            search = search_class(
                query=query,
                kb_index=self.index_info,
                llm_model=self.llm_model,
                top_k=10,  # TODO: make it configurable
                request_id=request_id,
            )
            data = search.execute()
            self.source_chunk_totals = getattr(search, "source_chunk_totals", {}) or {}
```

- [ ] **Step 4: State coverage and the notice**

Replace `format_response` with a version that groups the returned documents by source, reports coverage, and appends the notice only when something is missing:

```python
    def format_response(self, documents: list[Document] | tuple[list[Document], list[str]]) -> str:
        docs = documents[0] if isinstance(documents, tuple) else documents
        prefix = str(documents[1]) + "\n" if isinstance(documents, tuple) else ""

        shown: dict[str, int] = {}
        for doc in docs:
            source = doc.metadata.get("source", "")
            shown[source] = shown.get(source, 0) + 1

        coverage_lines = []
        incomplete = False
        for source, count in shown.items():
            total = self.source_chunk_totals.get(source)
            if not total:
                continue
            coverage_lines.append(f"{COVERAGE_KEY}{source}: {count} of {total} parts included")
            if count < total:
                incomplete = True

        body = "\n".join(self.format_document(doc) for doc in docs)
        parts = [prefix + "\n".join(coverage_lines), body] if coverage_lines else [prefix + body]
        if incomplete:
            parts.append(INCOMPLETE_NOTICE)
        return "\n".join(p for p in parts if p)
```

Define the two constants next to the existing key constants in the same module:

```python
COVERAGE_KEY = "**Coverage:** "
INCOMPLETE_NOTICE = (
    "\n###NOTICE###\n"
    "Some parts of the documents above are not included in this result. "
    "To obtain a missing part, call this tool again with a narrower query naming the "
    "section or topic you need. Do not supply the missing content from your own knowledge."
)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/agents/tools/kb/ -v`
Expected: PASS, including any pre-existing tests in that directory.

- [ ] **Step 6: Run the surrounding suites**

Run: `poetry run pytest tests/codemie/agents/ tests/codemie/service/search_and_rerank/ -q`
Expected: PASS. `test_search_kb.py` asserts on the formatted source label; confirm those assertions still hold, and if one breaks because it now sees a coverage line, verify the new output is correct before adjusting the expectation.

- [ ] **Step 7: Lint and commit**

```bash
make ruff
git add src/codemie/agents/tools/kb/search_kb.py tests/codemie/agents/tools/kb/test_search_kb_completeness.py
git commit -m "EPMCDME-12768: State document coverage in knowledge base search results"
```

---

### Task 3: Shorten replayed results at block boundaries

**Files:**
- Modify: `src/codemie/service/conversation/history_projection_service.py` (`_truncate_text`)
- Test: `tests/codemie/service/conversation/test_history_projection_service.py`

**Interfaces:**
- Consumes: the block delimiter emitted by Task 2's formatter.
- Produces: no new public symbols.

**Test-first: yes — a test asserting a shortened replay ends at a block boundary and reports the omission; it fails because truncation currently cuts at an arbitrary offset and appends only a bare marker.**

- [ ] **Step 1: Write the failing test**

Append to `tests/codemie/service/conversation/test_history_projection_service.py`:

```python
def test_truncation_keeps_blocks_whole_and_reports_omission():
    block = "\n###SOURCE DOCUMENT###\n**Source:**s-{n}\n**File Content:** \n" + ("x" * 400) + "\n"
    text = "".join(block.replace("{n}", str(n)) for n in range(1, 6))

    result = ConversationHistoryProjectionService._truncate_text(text, 900)

    assert "x" * 400 in result
    assert not result.rstrip().endswith("x")
    assert "omitted" in result.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/codemie/service/conversation/test_history_projection_service.py::test_truncation_keeps_blocks_whole_and_reports_omission -v`
Expected: FAIL — the text is cut mid-block, so it ends inside the `x` run and the omission wording is absent.

- [ ] **Step 3: Cut at block boundaries**

Replace `_truncate_text` with a version that prefers a block boundary and states what it dropped:

```python
    @classmethod
    def _truncate_text(cls, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text

        head = text[:limit]
        boundary = head.rfind(SOURCE_DOCUMENT_DELIMITER)
        if boundary > 0:
            kept = text[:boundary].rstrip()
            dropped = text.count(SOURCE_DOCUMENT_DELIMITER) - kept.count(SOURCE_DOCUMENT_DELIMITER)
            logger.debug(f"Truncated replay tool content from {len(text)} to {len(kept)} chars at a block boundary")
            return f"{kept}\n...[{dropped} further block(s) omitted; call the tool again to retrieve them]"

        truncated = head.rstrip()
        logger.debug(f"Truncated replay tool content from {len(text)} to {len(truncated)} chars")
        return f"{truncated}\n...[truncated]"
```

Define the delimiter constant near the other module constants:

```python
SOURCE_DOCUMENT_DELIMITER = "###SOURCE DOCUMENT###"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/conversation/ -v`
Expected: PASS, including all pre-existing tests in that file.

- [ ] **Step 5: Lint and commit**

```bash
make ruff
git add src/codemie/service/conversation/history_projection_service.py tests/codemie/service/conversation/test_history_projection_service.py
git commit -m "EPMCDME-12768: Shorten replayed tool results at block boundaries"
```

---

## Self-review

**Spec coverage.** "Response states its own scope" → Tasks 1 and 2. "Reduction happens at block boundaries and says so" → Task 3. "Statement is actionable and forbids invention" → Task 2 Step 4. "Complete responses gain no misleading claims" → Task 2 test 2. "Unaffected tools unchanged" → Task 3 keeps the old behaviour for text without the delimiter.

**Known limitation to disclose in the MR.** The totals come from the routing aggregation, which skips sources whose chunk count exceeds the per-document ceiling. For such sources no coverage line is emitted rather than a wrong one — Task 2 test 3 pins that. Sources routed by exact path match without the aggregation are covered by the same fallback.

**Type consistency.** `source_chunk_totals` is `dict[str, int]` in Task 1 and read as such in Task 2. `_truncate_text` keeps its `(text: str, limit: int) -> str` signature.

**No placeholders.** Every step carries runnable code or an exact command. Two fixtures are deliberately specified as "read a sibling test and follow it" rather than invented, because the construction shape of these subjects must match existing conventions.
