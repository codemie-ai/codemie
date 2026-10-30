---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 786a2357fa41188d0f0552d2da51efb5e26025c1
---

# Code review — 2026-10-29-epmcdme-16528-backend-pasted-content-first-prompt

**Decision:** approve (final round, confidence high)  
**Reviewed head:** `786a235` · **Rounds:** final

All four lenses and standards ran; no blocking findings survived. 14 candidates were dismissed: the trace_id/sid search KeyError is unreachable (trace_id is set to sid), ASCII-vs-Unicode whitespace and unterminated-tag swallowing are spec-acknowledged or implausible edge cases, and the raw-prefix sentinels deliberately read the unmodified prompt (documented). No defer items.

## Open — 0

## Coverage

| Acceptance criterion | Status | Notes |
|---|---|---|
| Approach 1: first_prompt derived from cleaned text in the rollup SQL | pass | rollups.py _clean_prompt/_CLEAN_PROMPT; pattern in vocabulary.PASTED_CONTENT_TAG_SQL |
| Approach 1: sentinel tests apply to the cleaned text | pass | Exact and trimmed-prefix sentinels read the cleaned prompt; the two Claude-injected raw prefixes deliberately read the prompt as sent (commented in code, recorded in guide). |
| Approach 1: prompt cleaning to empty does not qualify; nullif merge guard | pass | _PROMPT_OK requires cleaned <> ''; _earliest_merged guards first_prompt |
| Approach 2: strip_pasted_content applied to every served first_prompt | pass | list, detail, search and worst_session_prompt |
| Approach 3: live-PG parity test over the corpus | pass | test_live_first_prompt.py parity test, 9 cases |
| Approach 4: session_dims.session_title via schema-only revision a1c1a0000007 | pass | single head; latest-value mechanism, trim, 200 cut, blank never picked |
| Approach 5: served prompt is session_title else cleaned first prompt; REST unchanged | pass | _served_prompt; no model changes |
| Approach 6: search matches served prompt, cleaned first prompt, repository, branch | pass | first_prompt_by_session side dict, not added to response |
| Approach 7: prompt_original ignored | pass | no src reference |
| AC 1-14: readable titles, no markup, typed unchanged, empty skipped, named sessions, parity, tests | pass | covered by live, handler, migration, rollup and reader tests |
| AC 15: cli-analytics-events.md and cli-analytics-storage.md updated | pass | guides updated |
| Non-goals respected: no data-repair UPDATE, no REST change, no classification change | pass | revision is a single ADD COLUMN |

Standards: commit-format pass · code-quality pass · security pass
