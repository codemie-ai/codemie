# Unified routing proxy headers, RoutingInfo.meta removal

**Date**: 2026-09-17
**Status**: Approved for planning

## Problem

Today the proxy emits two structurally different HTTP header vocabularies to the client
depending on which mechanism routed a request:

- **Switchyard decided**: client gets `x-codemie-routing-*` headers (our own domain vocabulary,
  built from `SwitchyardMeta`).
- **LiteLLM's own auto-router decided** (Switchyard not involved): client gets `x-litellm-router-*`
  headers, forwarded *verbatim* from the external LiteLLM proxy's own response — an opaque,
  externally-owned vocabulary (`cause`, `router_type="complexity"`, `router_model_name`, ...)
  that this codebase does not control and does not normalize before it reaches the client.

Concretely, in `proxy_router.py`'s `_proxy_to_llm_proxy()`:

```python
response_headers = {k: v for k, v in downstream_response.headers.items() if _should_forward_response_header(k)}
...
if routing_info is not None:
    response_headers.update(_routing_info_to_headers(routing_info))
```

`routing_info` here is the decide()-time value — non-`None` only when Switchyard actually
decided synchronously. LiteLLM never decides synchronously (`Router.decide()` always returns
`None` for it), so this line never fires for a pure-LiteLLM-routed request, and the client is
left reading raw `x-litellm-router-*` headers directly. The same asymmetry exists in the SSE
`message_start` injection in `_streaming_response_with_usage_tracking()`.

Backend-internal analytics already solved an equivalent problem: `_finalize_stream_usage_tracking()`
builds one unified, canonical `RoutingInfo` (`proxy_routing`) by merging Switchyard's decide()-time
info with a normalized parse of LiteLLM's raw headers (`routing_info_from_headers()`). That unified
object is used only for the ES `routing_call_usage` event — it is never surfaced back to the actual
HTTP client.

## Decision

Emit exactly one header vocabulary (`x-codemie-routing-*`) to the client, regardless of which
mechanism routed the request, and stop forwarding raw `x-litellm-router-*` headers entirely. This
is a full replacement, not an additive/dual-emission transition — no external consumer
compatibility constraint was raised for this proxy's own direct clients (unlike the separate CLI
ClickHouse/analytics contract, which this change does not touch).

The determine → append pattern: Switchyard's routing identity is known at request *start*
(decide()-time, before the call). LiteLLM's is only knowable at the *end*, once the downstream
response headers exist. Both converge on the same unified `RoutingInfo`, built the same way
`_finalize_stream_usage_tracking()` already does it — just moved earlier, to before the client
response is assembled, and applied unconditionally instead of only when Switchyard decided.

### RoutingInfo.meta is removed

Auditing every field `SwitchyardMeta` builds shows 1:1 overlap with `RoutingInfo`'s own typed
fields (`routed_model`, `requested_model`, `tier`, `decision_source`, `routing_source`,
`router_type`, `classifier_model`, all `classifier_*_tokens`, `classifier_cost_usd`) — `.meta`
was carrying no information the typed fields didn't already carry. Once client-facing headers
are built directly from `RoutingInfo`'s typed fields (needed anyway, to cover the LiteLLM case
which never populated `.meta`), `.meta` has no remaining job. Removing it also removes
`Router.build_routing_meta()`/`SwitchyardRouter.build_routing_meta()` (its only producers) and
`SwitchyardMeta`/`build_switchyard_routing_meta()` (their only real implementation — confirmed
zero other production callers).

`is_empty()`/`merged_over()` stay exactly as they are for every other field — both are heavily
used across the routing pipeline (agent callbacks, `TokensCalculationCallback`,
`RequestSummaryManager`, `ConversationService`, `RoutingMonitoringService`, `proxy_router.py`).
Only the one `.meta`-specific line in each is deleted.

### New canonical codec

`_routing_info_to_headers()` (`enterprise/switchyard/proxy.py` — kept in place; renaming/moving
this module is out of scope, see Rejected Alternatives) is rewritten to serialize `RoutingInfo`'s
typed fields directly, via an explicit field→header mapping — the same header *names*
`SwitchyardMeta` already used, so no header name changes for existing Switchyard-routed clients,
plus one new field:

| RoutingInfo field | Header |
|---|---|
| `requested_model` | `x-codemie-requested-model` |
| `tier` | `x-codemie-routing-tier` |
| `decision_source` | `x-codemie-routing-decision-source` |
| `routing_source` | `x-codemie-routing-source` |
| `router_type` | `x-codemie-routing-router-type` |
| `routing_family` | `x-codemie-routing-family` (new) |
| `classifier_model` | `x-codemie-routing-classifier-model` |
| `classifier_input_tokens` | `x-codemie-routing-classifier-input-tokens` |
| `classifier_output_tokens` | `x-codemie-routing-classifier-output-tokens` |
| `classifier_cached_tokens` | `x-codemie-routing-classifier-cached-tokens` |
| `classifier_cache_creation_tokens` | `x-codemie-routing-classifier-cache-creation-tokens` |
| `routed_model` | `x-codemie-routed-model` (already special-cased; unchanged) |
| `classifier_cost_usd` | `x-codemie-routing-classifier-cost-usd` (already special-cased; unchanged) |

Deliberately not exposed as headers (scope discipline — these are either not decide()-time data,
or have no cross-mechanism meaning): `routing_tier_raw`, `confidence`, `routing_cost_known`,
`router_score`, `classifier_total_tokens`, all `routed_*` post-call usage fields, the savings
fields (`counterfactual_model`, `original_cost_usd`, `estimated_max_cost_usd`,
`potential_savings_usd` — these are computed *after* the response is fully read, structurally too
late for any header or the `message_start` SSE injection point; explicitly out of scope for this
change, see Rejected Alternatives).

### Three call sites move from "only when Switchyard decided" to "always"

All in `enterprise/litellm/proxy_router.py`:

1. **`_proxy_to_llm_proxy()`'s response header assembly** — build `client_routing` from
   `routing_info` if present, else `routing_info_from_headers(downstream_response.headers)`
   (the raw, unfiltered downstream headers — must run *before* `_should_forward_response_header`
   filtering removes them), then unconditionally `response_headers.update(_routing_info_to_headers(client_routing))`.
2. **`_streaming_response_with_usage_tracking()`'s SSE `message_start` injection** — same
   "always determine, always inject" change; an empty `client_routing` produces an empty header
   dict, which `with_routing_metadata_stream`/`_inject_routing_into_message_start` already treat
   as a harmless no-op, so no explicit `is_empty()` branch is needed.
3. **`_should_forward_response_header()`'s exposure policy** — remove `LITELLM_ROUTER_HEADERS`
   from the `x-litellm-` prefix's `exposed` allowlist (keep `LITELLM_FORWARDED_HEADERS`, which is
   unrelated to routing — just `x-litellm-model-name`). Raw `x-litellm-router-*` headers stop
   reaching the client entirely.

### LiteLLMRouterMeta → LiteLLMRouterHeaders, trimmed

Renamed for clarity — it is not a "meta" object connected to `RoutingInfo`, it is the typed
parser for LiteLLM's own external header vocabulary, used exclusively by
`routing_info_from_headers()`/`extract_classifier_usage()` to translate that foreign vocabulary
into `RoutingInfo`'s domain fields (different field names on purpose: `cause`→`decision_source`,
`router_model_name`→`requested_model`, `score`→`confidence`/`router_score`, plus `signals`
fallback derivation — this is a real translation, not a rename, so the typed intermediate stays).
Audited production usage: only `.from_headers()` is ever called (2 sites, both in
`enterprise/litellm/router.py`). `.to_headers()` and `.from_routing_decision()` have zero
production callers — dropped, along with the unused `json`/`Mapping`/`cast` imports they alone
needed. File renamed `litellm_router_meta.py` → `litellm_router_headers.py`; module-level
constants (`LITELLM_ROUTER_FIELD_TO_HEADER`, `LITELLM_ROUTER_HEADERS`) keep their names — they
were already accurately named regardless of the class rename.

### Architecture guard test updated, not deleted

`tests/architecture/test_routing_meta_isolation.py` currently guards "`SWITCHYARD_FIELD_TO_HEADER`/
`LITELLM_ROUTER_FIELD_TO_HEADER` must only be imported by their owning module" as a proxy for
"nothing outside the owning module may branch on `RoutingInfo.meta`'s per-key contents." Once
`.meta` is gone, that specific rationale disappears, but the underlying isolation rule for
LiteLLM's *wire* vocabulary is still worth keeping — nothing outside `litellm_router_headers.py`
should know that vocabulary exists; everyone else uses `routing_info_from_headers()`'s already-
translated `RoutingInfo`. Re-scoped (not deleted): drop the `SWITCHYARD_FIELD_TO_HEADER` guarded
name (file gone), keep `LITELLM_ROUTER_FIELD_TO_HEADER` guarded against the renamed file, rewrite
the docstring rationale.

## Rejected alternatives

- **Emit both raw LiteLLM headers and the new canonical ones (additive, non-breaking)** —
  rejected per explicit user decision: full replacement, not a transition period. No known
  consumer of this proxy's own headers was flagged as needing backward compatibility (distinct
  from the CLI's separate ClickHouse/TypeScript analytics contract, which this change does not
  touch at all).
- **Also propagate savings fields (`counterfactual_model`, `original_cost_usd`,
  `estimated_max_cost_usd`, `potential_savings_usd`) to the client** — explicitly deferred. They
  are computed in `_finalize_stream_usage_tracking()` *after* the downstream response is fully
  read (real usage tokens are required to re-price against the counterfactual model), which is
  structurally after both the `message_start` SSE injection point and (for streaming responses)
  after headers have already been sent. Emitting these would need a new injection point at
  stream *end* (e.g. `message_stop`) that does not exist today — out of scope for this change.
- **Relocate `enterprise/switchyard/proxy.py` to a mechanism-neutral location** (e.g.
  `core/routing_proxy.py`) now that its codec serves both mechanisms — considered, deferred. The
  module's own docstring already documents "Switchyard-owned only by location, not by content";
  moving it is pure file-shuffling with no behavior change and adds import-churn risk to an
  already large change. Left as a follow-up, not blocking this one.

## Field summary — what changes where

| File | Change |
|---|---|
| `core/routing_info.py` | Remove `meta` field; remove the `.meta`-specific line from `is_empty()` and `merged_over()`. |
| `core/router.py` | Remove `Router.build_routing_meta()`; `Router.routing_info()` default drops `meta=dict(self.build_routing_meta(decision))`. |
| `enterprise/switchyard/router.py` | Remove `SwitchyardRouter.build_routing_meta()` override; `routing_info()` drops its `meta=` line; stop importing `SwitchyardMeta`/`build_switchyard_routing_meta`. |
| `enterprise/switchyard/routing_meta.py` | **Deleted** (dead code once the above lands). |
| `enterprise/switchyard/proxy.py` | `_routing_info_to_headers()` rewritten to serialize `RoutingInfo`'s typed fields via the new mapping table above, instead of reading `.meta`. |
| `enterprise/litellm/litellm_router_meta.py` | **Renamed** to `litellm_router_headers.py`; class renamed `LiteLLMRouterMeta` → `LiteLLMRouterHeaders`; `.to_headers()`/`.from_routing_decision()` dropped. |
| `enterprise/litellm/proxy_router.py` | 3 call sites (response headers, SSE injection, exposure policy) move from "only when Switchyard decided" to "always"; import path updated for the rename. |
| `tests/architecture/test_routing_meta_isolation.py` | Drop `SWITCHYARD_FIELD_TO_HEADER` guard, keep `LITELLM_ROUTER_FIELD_TO_HEADER` guard against the renamed file, rewrite rationale. |
| Various test files | See implementation plan — mechanical updates plus two behavior-flip tests in `test_proxy_router.py` (raw LiteLLM router headers now stripped, not forwarded). |
