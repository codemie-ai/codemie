---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 8bfc686ef99812ccebf15d8389d4e2ada09aea1c
---

# Code review — 2026-11-02-epmcdme-13944-stt-litellm

**Decision:** approve (check round, confidence final low · check high · follow-up low)
**Reviewed head:** `8bfc686ef` · **Rounds:** final → check → check → check → follow-up final

**Closed:** 8 fixed · 0 moot · 1 decided

The branch was rebased onto `main` after the check rounds approved it at commit `da25e80fb`. That commit no longer exists in the branch history. Its approved content is unchanged by the rebase.

CR-001 resolved: the unused extra-body path is removed from the LiteLLM STT client, and the test now runs the real header setup and asserts `x-litellm-customer-id`. CR-002 resolved: Phoenix usage is written as `llm.token_count.prompt`, `llm.token_count.completion` and `llm.token_count.total`, with a test. CR-003 resolved: a dedicated `UnknownSttModelError` is the only error mapped to a 400. CR-004 decided: the trace name stays `Audio Transcription` and the Jira acceptance criterion was updated to match. CR-005 resolved: a new `_narrow_patterns()` helper drops a bare `*` pattern from a model's `stt_config` header lists. A broad pattern can no longer bypass the shared deny-lists. Two new tests cover it. CR-006 resolved: the type annotations are complete, including `get_model_type`. CR-007 resolved: an unconfigured STT model returns a 503. CR-008 resolved: the arguments passed to the LiteLLM factory are asserted.

CR-009 resolved: `main` had gained a spend-attribution header on its embedding client after this branch diverged. After the rebase onto `main`, the CI review bot flagged that the STT LiteLLM client built no such header. `get_litellm_stt_client` now keeps the resolved budget category and adds the same spend metadata header as the embedding client, with a test. A follow-up final round reviewed this fix on its own. Its edge-case and standards lenses both passed with 0 findings in the changed files. That round also flagged three issues in `config/llms/llm-dial-config.yaml` and `docker-compose.yml`. Both files carry local, uncommitted changes that are not part of this merge request, so those three findings do not apply here.

## Open — 0

## Coverage

The final round graded acceptance and standards. The check rounds and the follow-up final round each re-verified their own findings only, so Open and Closed above are current.
