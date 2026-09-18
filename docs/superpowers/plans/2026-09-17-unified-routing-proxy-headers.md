# Unified Routing Proxy Headers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit one `x-codemie-routing-*` header vocabulary to proxy clients regardless of which mechanism routed the request, per `docs/superpowers/specs/2026-09-17-unified-routing-proxy-headers-design.md`. Remove `RoutingInfo.meta` (now fully redundant with typed fields) and `SwitchyardMeta` along with it; rename `LiteLLMRouterMeta` → `LiteLLMRouterHeaders` to reflect its actual (parse-only) role.

**Architecture:** A single canonical codec serializes `RoutingInfo`'s typed fields directly to `x-codemie-routing-*` headers — used for both Switchyard (decide()-time `RoutingInfo`) and LiteLLM (built by parsing its raw response headers, same as backend analytics already does). Raw `x-litellm-router-*` headers stop reaching the client.

**Tech Stack:** Python 3.12, Pydantic v2 (`RoutingInfo`), dataclasses, pytest + pytest-asyncio, poetry.

---

## Task 1: Remove `RoutingInfo.meta`

**Files:**
- Modify: `src/codemie/core/routing_info.py`
- Modify: `tests/enterprise/test_routing_info.py`

- [ ] **Step 1: Write the failing test (guard against regression)**

Add to `tests/enterprise/test_routing_info.py`, anywhere after the imports:

```python
def test_routing_info_has_no_meta_field():
    assert not hasattr(RoutingInfo(), "meta")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/enterprise/test_routing_info.py -k has_no_meta -v`
Expected: FAIL (the field still exists).

- [ ] **Step 3: Remove the field and its two usages in `src/codemie/core/routing_info.py`**

Remove `meta: dict[str, str] = Field(default_factory=dict)` from the `RoutingInfo` class body.

`is_empty()` needs no change — it never checked `self.meta` (that omission is exactly what
`test_is_empty_ignores_meta`, deleted in Step 4, documented: `meta` was deliberately excluded
from the emptiness check even before this removal).

In `merged_over()`, remove the line:
```python
            meta={**base.meta, **self.meta},
```

Also remove the now-unused `Field` import if nothing else in the file uses it — check: `Field` is still used for other fields with `Field(default_factory=...)`? Grep the file; if `meta` was the only `Field(default_factory=dict)` user and no other field uses `Field(...)`, remove `Field` from the `pydantic` import line. If any other field still uses `Field(...)`, leave the import.

Update the class docstring: remove the paragraph describing `meta` as "an opaque, mechanism-specific passthrough bag" (it references `SwitchyardRouter`/`apply_router_routing` stashing headers there — no longer true).

- [ ] **Step 4: Remove the meta-specific tests in `tests/enterprise/test_routing_info.py`**

Delete `test_merged_over_unions_meta_with_self_winning_on_collision` (currently constructs `RoutingInfo(meta={...})` and asserts the union — the field no longer exists).

Delete `test_is_empty_ignores_meta` (its whole premise — meta being excluded from the emptiness check — no longer applies since there is no meta).

Do **not** touch `_FakeRouterMeta`/`test_header_codec_*` tests below them — those exercise the generic `RoutingHeaderCodec` mixin directly via a standalone test dataclass, entirely unrelated to `RoutingInfo.meta`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/test_routing_info.py -v`
Expected: PASS for every remaining test in the file.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/core/routing_info.py tests/enterprise/test_routing_info.py
git commit -m "EPMCDME-14888: Remove RoutingInfo.meta (redundant with typed fields)"
```

## Context

`RoutingInfo.meta` (`dict[str, str]`) was an "opaque per-router passthrough bag" — the only field that let a router smuggle arbitrary extra header data past the canonical typed fields. Every key it ever carried (via `SwitchyardMeta`) already has a 1:1 typed `RoutingInfo` field (`tier`, `decision_source`, `routing_source`, `router_type`, `classifier_model`, classifier token/cost fields) — confirmed by direct field-by-field audit. Once client-facing headers are built directly from those typed fields (Task 3), `meta` carries nothing unique. This is Task 1 of 8; later tasks remove `meta`'s only producers (`Router.build_routing_meta()`, `SwitchyardMeta`) and its only consumer (`_routing_info_to_headers()`'s old `dict(info.meta)` line).

---

## Task 2: Remove `Router.build_routing_meta()`

**Files:**
- Modify: `src/codemie/core/router.py`
- Modify: `tests/codemie/core/test_router.py`

- [ ] **Step 1: Write the failing tests**

Replace `test_router_default_routing_info_combines_canonical_fields_and_meta` (currently tests `build_routing_meta()`'s override hook, which is being removed) in `tests/codemie/core/test_router.py` with:

```python
def test_router_default_routing_info_combines_canonical_fields():
    """routing_info() is the decision-only counterpart to extract() (no response needed) —
    used wherever a RoutingDecision is already in hand (RouterChatModel, apply_router_routing,
    TokensCalculationCallback)."""
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="capable",
        decision_source="llm_classifier",
        routing_family="stub",
        classifier=ClassifierCall(cost_usd=0.001),
    )
    info = _StubRouter().routing_info(decision)
    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == 0.001
    assert info.routing_family == "stub"
```

Replace `test_router_default_routing_info_classifier_cost_none_when_no_classifier`:

```python
def test_router_default_routing_info_classifier_cost_none_when_no_classifier():
    decision = RoutingDecision(
        model="claude-4-5-haiku", tier="capable", decision_source="heuristic", routing_family="stub"
    )
    info = _StubRouter().routing_info(decision)
    assert info.classifier_cost_usd is None
    assert info.routing_family == "stub"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/core/test_router.py -v`
Expected: FAIL — `info.meta` no longer exists is not the failure mode here since these rewritten tests don't reference `.meta` at all; instead they should currently PASS against the *old* code (the assertions they keep are already true). This step is a sanity check only — if they already pass, proceed directly to Step 3's removal and re-verify in Step 4 that nothing regresses.

- [ ] **Step 3: Remove `build_routing_meta()` from `src/codemie/core/router.py`**

Remove the `build_routing_meta()` method entirely from the `Router` ABC:

```python
    def build_routing_meta(self, decision: RoutingDecision) -> Mapping[str, str]:
        """Proxy-path only: this router's own opaque contribution to RoutingInfo.meta (see
        that field's docstring — only the producing router may populate or interpret these
        keys; generic callers must treat the result as an opaque bag to forward, never branch
        on it). Default: nothing beyond RoutingInfo's own canonical fields (routed_model,
        classifier_cost_usd) already covers. Override where a router's wire format carries
        more per-call detail than those two fields (see SwitchyardRouter, the only router
        whose decide() returns a populated RoutingDecision today)."""
        return {}
```

Update `routing_info()`'s default implementation to drop the `meta=` line:

```python
    def routing_info(self, decision: RoutingDecision) -> RoutingInfo:
        """Canonical RoutingInfo built directly from a decision — no response needed, so it's
        usable synchronously the moment decide() returns, unlike extract() (which needs a
        response object and, for SwitchyardRouter, needs RouterChatModel to have already
        stamped it — see extract()'s docstring). Prefer this over extract() at every call site
        that already has a RoutingDecision in hand (RouterChatModel._agenerate,
        apply_router_routing, TokensCalculationCallback when a decision was stashed); fall back
        to extract() only where no decision was ever available synchronously.

        ``routing_family`` is read from the decision, not from ``self.routing_family`` —
        the decision is the single source of truth once one exists (see RoutingDecision's own
        docstring); ``self.routing_family`` exists only for routers whose decide() can return
        None (LiteLLMRouter), which never reach this method with a real decision anyway."""
        return RoutingInfo(
            routed_model=decision.model,
            classifier_cost_usd=decision.classifier.cost_usd if decision.classifier else None,
            routing_family=decision.routing_family,
        )
```

If `Mapping` (from `collections.abc`) was imported only for `build_routing_meta()`'s signature, check remaining usages before removing the import — `CallContext`/other signatures in this file also use `Mapping`, so it almost certainly stays; verify with a grep before touching the import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/core/test_router.py -v`
Expected: PASS for every test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/router.py tests/codemie/core/test_router.py
git commit -m "EPMCDME-14888: Remove Router.build_routing_meta (RoutingInfo.meta's only producer)"
```

## Context

This is Task 2 of 8. `Router.build_routing_meta()` existed solely to populate `RoutingInfo.meta` (removed in Task 1). With that field gone, the hook has no remaining purpose. `SwitchyardRouter`'s override of this same method is removed in Task 4, once the new canonical codec (Task 3) no longer needs it either.

---

## Task 3: New canonical `RoutingInfo → x-codemie-routing-*` codec

**Files:**
- Modify: `src/codemie/enterprise/switchyard/proxy.py`
- Modify: `tests/enterprise/switchyard/test_proxy.py`

This task rewrites `_routing_info_to_headers()` to serialize `RoutingInfo`'s typed fields
directly (the same header *names* `SwitchyardMeta` already used, plus one new one for
`routing_family`), rather than reading the now-removed `.meta`. It does **not** yet touch
`SwitchyardRouter`/`build_switchyard_routing_meta` (Task 4) or `proxy_router.py`'s three call
sites (Task 6) — this task only fixes the codec function itself and its own direct tests.

- [ ] **Step 1: Write the failing tests**

Replace `test_switchyard_headers_injected_into_response_headers` and add new tests, in
`tests/enterprise/switchyard/test_proxy.py` (find and replace that existing test, wherever it
currently is in this file — it was last confirmed content unaffected by `.meta` removal, but
extend it):

```python
def test_routing_info_to_headers_serialises_typed_fields():
    from codemie.core.routing_info import RoutingInfo
    from codemie.enterprise.switchyard.proxy import _routing_info_to_headers

    info = RoutingInfo(
        routed_model="claude-4-5-haiku",
        requested_model="claude-4-6-sonnet",
        tier="simple",
        decision_source="llm-classifier",
        routing_source="judge",
        router_type="composite",
        routing_family="switchyard",
        classifier_model="gpt-5.6-luna",
        classifier_input_tokens=120,
        classifier_output_tokens=40,
        classifier_cached_tokens=15,
        classifier_cache_creation_tokens=5,
        classifier_cost_usd=0.0009,
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routed-model"] == "claude-4-5-haiku"
    assert headers["x-codemie-requested-model"] == "claude-4-6-sonnet"
    assert headers["x-codemie-routing-tier"] == "simple"
    assert headers["x-codemie-routing-decision-source"] == "llm-classifier"
    assert headers["x-codemie-routing-source"] == "judge"
    assert headers["x-codemie-routing-router-type"] == "composite"
    assert headers["x-codemie-routing-family"] == "switchyard"
    assert headers["x-codemie-routing-classifier-model"] == "gpt-5.6-luna"
    assert headers["x-codemie-routing-classifier-input-tokens"] == "120"
    assert headers["x-codemie-routing-classifier-output-tokens"] == "40"
    assert headers["x-codemie-routing-classifier-cached-tokens"] == "15"
    assert headers["x-codemie-routing-classifier-cache-creation-tokens"] == "5"
    assert headers["x-codemie-routing-classifier-cost-usd"] == "0.0009"


def test_routing_info_to_headers_omits_none_fields():
    from codemie.core.routing_info import RoutingInfo
    from codemie.enterprise.switchyard.proxy import _routing_info_to_headers

    headers = _routing_info_to_headers(RoutingInfo())
    assert headers == {}


def test_routing_info_to_headers_works_for_a_litellm_shaped_routing_info():
    """The same codec must serialise a RoutingInfo built from LiteLLM's own headers
    identically to one built from a Switchyard decision — no field is Switchyard-specific."""
    from codemie.core.routing_info import RoutingInfo
    from codemie.enterprise.switchyard.proxy import _routing_info_to_headers

    info = RoutingInfo(
        routed_model="claude-sonnet-5",
        tier="complex",
        decision_source="llm-classifier",
        routing_family="litellm",
        router_type="complexity",
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routed-model"] == "claude-sonnet-5"
    assert headers["x-codemie-routing-tier"] == "complex"
    assert headers["x-codemie-routing-decision-source"] == "llm-classifier"
    assert headers["x-codemie-routing-family"] == "litellm"
    assert headers["x-codemie-routing-router-type"] == "complexity"
```

Keep the existing `test_routing_info_to_headers_forwards_meta_and_canonical_fields_win_on_collision`
test **removed** — its entire premise (a `.meta` dict colliding with canonical fields) no longer
applies now that there is no `.meta` to collide with; the new tests above cover the codec's real
behavior instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/switchyard/test_proxy.py -k routing_info_to_headers -v`
Expected: FAIL — most of the new fields (`decision_source`, `routing_source`, `router_type`,
`routing_family`, `classifier_model`, classifier token fields) are silently dropped by the
current `dict(info.meta)`-based implementation (which no longer even has a `.meta` to read after
Task 1 — this will actually raise `AttributeError` at this point, since Task 1 already removed
the field but this codec hasn't been updated yet).

- [ ] **Step 3: Rewrite `_routing_info_to_headers()` in `src/codemie/enterprise/switchyard/proxy.py`**

Add the import and new mapping constant near the top of the file (after the existing imports):

```python
from codemie.core.routing_info import encode_header_value

if TYPE_CHECKING:
    from codemie.core.routing_info import RoutingInfo


_ROUTING_HEADER_FIELDS: dict[str, str] = {
    "requested_model": "x-codemie-requested-model",
    "tier": "x-codemie-routing-tier",
    "decision_source": "x-codemie-routing-decision-source",
    "routing_source": "x-codemie-routing-source",
    "router_type": "x-codemie-routing-router-type",
    "routing_family": "x-codemie-routing-family",
    "classifier_model": "x-codemie-routing-classifier-model",
    "classifier_input_tokens": "x-codemie-routing-classifier-input-tokens",
    "classifier_output_tokens": "x-codemie-routing-classifier-output-tokens",
    "classifier_cached_tokens": "x-codemie-routing-classifier-cached-tokens",
    "classifier_cache_creation_tokens": "x-codemie-routing-classifier-cache-creation-tokens",
}
```

(`encode_header_value` already exists in `core/routing_info.py` — it percent-encodes non-ASCII
so header values stay valid latin-1; reuse it rather than re-implementing. `TYPE_CHECKING` import
of `RoutingInfo` already exists in this file — keep it, just add the runtime `encode_header_value`
import alongside the existing `from codemie.core.router import RoutingDecision` line, not inside
the `if TYPE_CHECKING:` block, since it's called at runtime.)

Replace the `_routing_info_to_headers()` function body (currently reads `dict(info.meta)`):

```python
def _routing_info_to_headers(info: RoutingInfo) -> dict[str, str]:
    """Build the canonical x-codemie-routing-* header set directly from RoutingInfo's typed
    fields — one vocabulary regardless of which Router produced the info (Switchyard decided
    synchronously, or LiteLLM's own external auto-router, parsed back from its response
    headers via routing_info_from_headers()). routed_model/classifier_cost_usd get their own
    headers unconditionally when present — they're RoutingInfo's two always-canonical fields
    (see that field's docstring)."""
    headers: dict[str, str] = {}
    for field_name, header_name in _ROUTING_HEADER_FIELDS.items():
        value = getattr(info, field_name)
        if value is not None:
            headers[header_name] = encode_header_value(str(value))
    if info.routed_model is not None:
        headers["x-codemie-routed-model"] = encode_header_value(info.routed_model)
    if info.classifier_cost_usd is not None:
        headers["x-codemie-routing-classifier-cost-usd"] = f"{info.classifier_cost_usd:.6g}"
    return headers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/switchyard/test_proxy.py -v`
Expected: PASS for every test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/enterprise/switchyard/proxy.py tests/enterprise/switchyard/test_proxy.py
git commit -m "EPMCDME-14888: Build x-codemie-routing-* headers from RoutingInfo's typed fields"
```

## Context

This is Task 3 of 8 — the new shared codec that both Switchyard and (starting in Task 6) LiteLLM
will use to build the client-facing header set. It deliberately does not yet touch anything that
*calls* `apply_router_routing()`/`SwitchyardRouter` (Task 4) or `proxy_router.py` (Task 6) — this
task's own test file (`test_proxy.py`) still has one other test,
`test_apply_router_routing_rewrites_body_when_router_decides`, that constructs its expected
`RoutingInfo` using `build_switchyard_routing_meta(...).to_headers()` — that test is fixed in
Task 4, once `build_switchyard_routing_meta` is gone and `SwitchyardRouter.routing_info()` sets
every field directly.

---

## Task 4: Remove `SwitchyardMeta`; `SwitchyardRouter` builds `RoutingInfo` directly

**Files:**
- Modify: `src/codemie/enterprise/switchyard/router.py`
- Delete: `src/codemie/enterprise/switchyard/routing_meta.py`
- Delete: `tests/enterprise/switchyard/test_routing_meta.py`
- Modify: `tests/enterprise/switchyard/test_router.py`
- Modify: `tests/enterprise/switchyard/test_proxy.py`

- [ ] **Step 1: Write the failing tests**

In `tests/enterprise/switchyard/test_router.py`, remove the `build_switchyard_routing_meta`
import from the top-of-file import line:

```python
from codemie.enterprise.switchyard.router import SwitchyardRouter, build_switchyard_routing_meta
```
becomes:
```python
from codemie.enterprise.switchyard.router import SwitchyardRouter
```

Delete `test_build_switchyard_routing_meta_maps_decision_fields_to_headers` and
`test_build_switchyard_routing_meta_router_type_is_stage_in_signal_mode` entirely (their subject,
`build_switchyard_routing_meta`, is being removed — equivalent header-building coverage now lives
in `test_proxy.py`, Task 3).

Delete `test_build_routing_meta_returns_full_header_set` entirely (`SwitchyardRouter.build_routing_meta`
is being removed).

Replace `test_routing_info_populates_typed_routing_dimensions` (drop the `.meta` assertion and the
docstring's now-stale "not only inside the opaque meta header bag" framing):

```python
def test_routing_info_populates_typed_routing_dimensions():
    """The typed routing dimensions consumed by routing analytics (requested_model, tier,
    classifier token counts) must be on RoutingInfo itself — routing_call_usage reads the
    typed fields directly, and the client-facing headers (see enterprise/switchyard/proxy.py)
    are built from these same typed fields, not a separate passthrough bag."""
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="llm_classifier",
        routing_family="switchyard",
        classifier=ClassifierCall(
            model="gpt-5.6-luna", input_tokens=120, output_tokens=40, cached_tokens=15, cost_usd=0.0009
        ),
    )

    info = router.routing_info(decision)

    assert info.routed_model == "claude-4-5-haiku"
    assert info.requested_model == "claude-4-6-sonnet"
    assert info.tier == "simple"
    assert info.routing_tier_raw == RoutingTier.EFFICIENT
    assert info.classifier_input_tokens == 120
    assert info.classifier_output_tokens == 40
    assert info.classifier_cached_tokens == 15
    assert info.classifier_cost_usd == 0.0009
    assert info.decision_source == "llm-classifier"
    assert info.routing_family == "switchyard"
    assert info.router_type == "stage"  # _make_engine defaults to signal mode
    assert info.classifier_model == "gpt-5.6-luna"
```

Leave `test_routing_info_without_classifier_leaves_token_fields_none` as-is — it does not
reference `.meta` or `build_switchyard_routing_meta` at all.

In `tests/enterprise/switchyard/test_proxy.py`, replace
`test_apply_router_routing_rewrites_body_when_router_decides` (currently builds its expected
`RoutingInfo` via `build_switchyard_routing_meta(...).to_headers()`):

```python
@pytest.mark.asyncio
async def test_apply_router_routing_rewrites_body_when_router_decides():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="llm_classifier",
        routing_family="switchyard",
        classifier=ClassifierCall(cached_tokens=30, cache_creation_tokens=10),
    )
    router = MagicMock()
    router.decide = AsyncMock(return_value=decision)
    request_body = {"model": "claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal", "messages": []}
    body_bytes = json.dumps(request_body).encode("utf-8")

    with patch("codemie.service.llm_service.router_factory.create_router", return_value=router):
        # Exercise the REAL SwitchyardRouter.routing_info() formula via a real engine double,
        # rather than a hand-built expected RoutingInfo — build_switchyard_routing_meta no
        # longer exists, so there's no shortcut object to construct the expectation from.
        from types import SimpleNamespace

        real_engine = SimpleNamespace(
            capable_model="claude-4-6-sonnet",
            efficient_model="claude-4-5-haiku",
            routing_mode="signal",
        )
        from codemie.enterprise.switchyard.router import SwitchyardRouter

        router.routing_info = MagicMock(side_effect=SwitchyardRouter(real_engine).routing_info)

        new_bytes, new_body, returned_decision, routing_info = await apply_router_routing(
            endpoint="v1/messages",
            router_name="claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal",
            request_body=request_body,
            body_bytes=body_bytes,
        )

    assert json.loads(new_bytes)["model"] == "claude-4-5-haiku"
    assert new_body is not None
    assert new_body["model"] == "claude-4-5-haiku"
    assert returned_decision is decision
    assert routing_info is not None
    assert routing_info.routed_model == "claude-4-5-haiku"
    assert routing_info.tier == "simple"
    assert routing_info.requested_model == "claude-4-6-sonnet"
    assert routing_info.classifier_cached_tokens == 30
    assert routing_info.classifier_cache_creation_tokens == 10
    assert routing_info.decision_source == "llm-classifier"
    assert routing_info.router_type == "stage"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/switchyard/test_router.py tests/enterprise/switchyard/test_proxy.py -v`
Expected: FAIL — `SwitchyardRouter.routing_info()` still calls the soon-to-be-removed
`build_routing_meta()`/sets `meta=`, which no longer exists after Task 1/2 (this will currently
raise `AttributeError`/`TypeError` — that's expected, confirming the test exercises real code, not
a stale mock).

- [ ] **Step 3: Rewrite `src/codemie/enterprise/switchyard/router.py`**

Remove the `SwitchyardMeta` import and `build_switchyard_routing_meta()` function entirely
(the whole function, currently near the top of the file, right after the imports).

Remove the `normalize_decision_source`/`normalize_routing_tier` import's dependency on
`SwitchyardMeta` — those two normalize helpers stay (still needed), only the
`from codemie.enterprise.switchyard.routing_meta import SwitchyardMeta` import line is deleted.

Replace the `SwitchyardRouter` class:

```python
class SwitchyardRouter(Router):
    name = "switchyard"
    routing_family = "switchyard"

    def __init__(self, engine: "ProxySwitchyardRouter") -> None:
        self._engine = engine

    async def decide(self, messages: list[dict[str, object]]) -> "RoutingDecision | None":
        return await self._engine.pick_model(messages)

    def candidate_models(self) -> Sequence[str]:
        return (self._engine.capable_model_deployment_name, self._engine.efficient_model_deployment_name)

    def routing_info(self, decision: "RoutingDecision") -> RoutingInfo:
        """Override Router default to populate every typed routing dimension directly — no
        intermediate wire object. ``requested_model`` comes from the engine's capable-model
        name (the tier that would have served absent downgrade) — not part of RoutingDecision,
        which is router-agnostic. Classifier fields come from the ClassifierCall nested on the
        decision. ``decision_source``/``routing_family`` are read straight off the decision —
        it is the single source of truth for both (see RoutingDecision's own docstring).
        ``router_type`` ("stage"/"composite") comes from the engine's own routing_mode config."""
        c = decision.classifier
        return RoutingInfo(
            routed_model=decision.model,
            classifier_cost_usd=c.cost_usd if c else None,
            requested_model=self._engine.capable_model,
            tier=normalize_routing_tier(decision.tier),
            routing_tier_raw=decision.tier,
            decision_source=normalize_decision_source(decision.decision_source),
            routing_source="judge" if c else "stage_router",
            routing_family=decision.routing_family,
            routing_cost_known=True,
            classifier_model=c.model if c else None,
            router_type="composite" if self._engine.routing_mode == "classifier" else "stage",
            classifier_input_tokens=c.input_tokens if c else None,
            classifier_output_tokens=c.output_tokens if c else None,
            classifier_cached_tokens=c.cached_tokens if c else None,
            classifier_cache_creation_tokens=c.cache_creation_tokens if c else None,
        )

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        """Read the canonical RoutingInfo that RouterChatModel._agenerate stamped onto the
        response after this router's own decide() ran — the only channel that's actually
        populated for the agent path (SwitchyardRouter.build_chat_model is the Router
        default, which always wraps in RouterChatModel)."""
        return RoutingInfo.from_response(response)

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        decision = ctx.decision
        if decision is None or decision.classifier is None:
            return None
        c = decision.classifier
        return ClassifierUsage(
            provider=self.name,
            input_tokens=max(0, c.input_tokens),
            output_tokens=max(0, c.output_tokens),
            cost_usd=c.cost_usd,
            model=c.model,
        )
```

Note what's gone versus the previous version: no `build_routing_meta()` override, no
`meta=dict(...)` in `routing_info()`, and `routing_info()` no longer calls
`self.build_routing_meta(decision)` at all — every field it needs is set directly.

- [ ] **Step 4: Delete `src/codemie/enterprise/switchyard/routing_meta.py` and `tests/enterprise/switchyard/test_routing_meta.py`**

```bash
git rm src/codemie/enterprise/switchyard/routing_meta.py tests/enterprise/switchyard/test_routing_meta.py
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/switchyard/ -v`
Expected: PASS for every test under `tests/enterprise/switchyard/` (the deleted test file's tests
are gone, not failing).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/enterprise/switchyard/router.py tests/enterprise/switchyard/test_router.py tests/enterprise/switchyard/test_proxy.py
git commit -m "EPMCDME-14888: Remove SwitchyardMeta; SwitchyardRouter builds RoutingInfo directly"
```

## Context

This is Task 4 of 8. `SwitchyardMeta`/`build_switchyard_routing_meta()` had exactly one real
production caller (`SwitchyardRouter.build_routing_meta()`, removed in this same task) — dead
code once that caller is gone, per the design doc's production-usage audit. `SwitchyardRouter`
now builds a fully-populated `RoutingInfo` in one place, with the same values it always computed
(no behavior change to any typed field — `tier`, `decision_source`, `router_type`, etc. are
computed exactly as before), just without a parallel `.meta` dict alongside it.

---

## Task 5: Rename `LiteLLMRouterMeta` → `LiteLLMRouterHeaders`, trim to parsing-only

**Files:**
- Rename: `src/codemie/enterprise/litellm/litellm_router_meta.py` → `src/codemie/enterprise/litellm/litellm_router_headers.py`
- Modify: `src/codemie/enterprise/litellm/router.py`
- Modify: `tests/enterprise/litellm/test_routing_headers.py`

- [ ] **Step 1: Write the failing tests**

In `tests/enterprise/litellm/test_routing_headers.py`, update the import:

```python
from codemie.enterprise.litellm.litellm_router_meta import (
    LITELLM_ROUTER_FIELD_TO_HEADER,
    LiteLLMRouterMeta,
)
```
becomes:
```python
from codemie.enterprise.litellm.litellm_router_headers import (
    LITELLM_ROUTER_FIELD_TO_HEADER,
    LiteLLMRouterHeaders,
)
```

Rename every `LiteLLMRouterMeta` reference in this file to `LiteLLMRouterHeaders` — that's
`test_litellm_router_meta_from_headers_reads_known_fields`,
`test_litellm_router_meta_from_headers_returns_empty_for_unknown`,
`test_litellm_router_meta_from_headers_invalid_values_become_none`, and
`test_field_to_header_keys_match_dataclass_fields` (rename the class usage inside; the test
function name itself can stay, it's not `LiteLLMRouterMeta`-specific in its own name).

Delete `test_litellm_router_meta_to_headers_serialises_non_none_fields` and
`test_litellm_router_meta_from_routing_decision` entirely — they test `.to_headers()`/
`.from_routing_decision()`, both being removed (zero production callers, confirmed by audit in
the design doc).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/litellm/test_routing_headers.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` (the renamed module/class don't exist yet).

- [ ] **Step 3: Rename the file and trim the class**

```bash
git mv src/codemie/enterprise/litellm/litellm_router_meta.py src/codemie/enterprise/litellm/litellm_router_headers.py
```

Replace the entire contents of the renamed file:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import dataclasses
from typing import ClassVar, Final

from codemie.core.routing_info import RoutingHeaderCodec

LITELLM_ROUTER_FIELD_TO_HEADER: Final[dict[str, str]] = {
    "tier": "x-litellm-router-tier",
    "cause": "x-litellm-router-cause",
    "score": "x-litellm-router-score",
    "routed_model": "x-litellm-router-routed-model",
    "classifier_model": "x-litellm-router-classifier-model",
    "router_model_name": "x-litellm-router-model-name",
    "router_type": "x-litellm-router-type",
    "signals": "x-litellm-router-signals",
    "escalated": "x-litellm-router-escalated",
    "escalation_keyword": "x-litellm-router-escalation-keyword",
    "classifier_prompt_tokens": "x-litellm-classifier-prompt-tokens",
    "classifier_completion_tokens": "x-litellm-classifier-completion-tokens",
    "classifier_total_tokens": "x-litellm-classifier-total-tokens",
    "classifier_cost_usd": "x-litellm-classifier-cost",
}

LITELLM_ROUTER_HEADERS: Final[frozenset[str]] = frozenset(LITELLM_ROUTER_FIELD_TO_HEADER.values())

_INT_FIELDS: Final[frozenset[str]] = frozenset(
    {"classifier_prompt_tokens", "classifier_completion_tokens", "classifier_total_tokens"}
)
_FLOAT_FIELDS: Final[frozenset[str]] = frozenset({"score", "classifier_cost_usd"})


@dataclasses.dataclass
class LiteLLMRouterHeaders(RoutingHeaderCodec):
    """Typed parser for LiteLLM's own external x-litellm-router-*/x-litellm-classifier-*
    response headers — mirrors that foreign wire vocabulary exactly (field names like ``cause``,
    ``router_model_name``, ``score`` are LiteLLM's own, not ours). Used exclusively by
    enterprise/litellm/router.py's routing_info_from_headers()/extract_classifier_usage() to
    translate this wire shape into RoutingInfo's own domain fields (different names on purpose:
    cause->decision_source, router_model_name->requested_model, score->confidence/router_score).
    Read-only in practice: this codebase never builds outgoing x-litellm-router-* headers (those
    are emitted by the external LiteLLM proxy fork's own callback code), so only from_headers()
    (inherited from RoutingHeaderCodec) is exercised in production."""

    FIELD_TO_HEADER: ClassVar[dict[str, str]] = LITELLM_ROUTER_FIELD_TO_HEADER
    INT_FIELDS: ClassVar[frozenset[str]] = _INT_FIELDS
    FLOAT_FIELDS: ClassVar[frozenset[str]] = _FLOAT_FIELDS

    tier: str | None = None
    cause: str | None = None
    score: float | None = None
    routed_model: str | None = None
    classifier_model: str | None = None
    router_model_name: str | None = None
    router_type: str | None = None
    signals: str | None = None
    escalated: str | None = None
    escalation_keyword: str | None = None
    classifier_prompt_tokens: int | None = None
    classifier_completion_tokens: int | None = None
    classifier_total_tokens: int | None = None
    classifier_cost_usd: float | None = None
```

(`from_routing_decision()` and its `json`/`Mapping`/`cast` imports are dropped entirely — zero
production callers, confirmed by repo-wide grep before this plan was written.)

- [ ] **Step 4: Update `src/codemie/enterprise/litellm/router.py`**

Replace both local imports (there are two, at the top of `extract()`/`routing_info_from_headers()`
and inside `extract_classifier_usage()`):

```python
    from codemie.enterprise.litellm.litellm_router_meta import LiteLLMRouterMeta
```
becomes (both occurrences):
```python
    from codemie.enterprise.litellm.litellm_router_headers import LiteLLMRouterHeaders
```

Rename every `LiteLLMRouterMeta.from_headers(...)` call to `LiteLLMRouterHeaders.from_headers(...)`
(two call sites: inside `routing_info_from_headers()` and inside `extract_classifier_usage()`).
The local variable name `meta` at each call site can stay as-is (it's a local variable, not the
class name) — only the class reference itself changes.

Update this file's own module docstring line that mentions `LiteLLMRouterMeta`:
```
LiteLLMRouterMeta/_iter_header_maps — this is enterprise/litellm logic, not core.
```
becomes:
```
LiteLLMRouterHeaders/_iter_header_maps — this is enterprise/litellm logic, not core.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/litellm/test_routing_headers.py -v`
Expected: PASS for every test in the file.

Also run: `poetry run pytest tests/enterprise/litellm/ -q` to catch any other file that imported
the old module/class name and wasn't yet updated (there should be none besides `proxy_router.py`,
handled in Task 6 — if this run surfaces an import error elsewhere, note it and fix it here, since
Task 6 assumes the rename is already fully complete by this point).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/enterprise/litellm/litellm_router_headers.py src/codemie/enterprise/litellm/router.py tests/enterprise/litellm/test_routing_headers.py
git rm src/codemie/enterprise/litellm/litellm_router_meta.py 2>/dev/null || true
git commit -m "EPMCDME-14888: Rename LiteLLMRouterMeta to LiteLLMRouterHeaders, trim to parsing-only"
```

(The `git rm ... || true` is a safety net in case `git mv` in Step 3 already staged the deletion
side of the rename — avoids a spurious error if there's nothing left to remove.)

## Context

This is Task 5 of 8. `LiteLLMRouterMeta`'s name suggested a connection to `RoutingInfo`/"meta"
that doesn't actually exist — it is a typed parser for LiteLLM's own external header vocabulary,
used only by the translation function that turns that foreign shape into `RoutingInfo`'s domain
fields. Renaming it — and dropping the two methods (`to_headers()`, `from_routing_decision()`)
that have zero production callers — makes its actual, narrower role explicit. `proxy_router.py`
still imports `LITELLM_ROUTER_HEADERS` from this module (Task 6 updates that import path) —
that constant's name is unaffected by this rename, it was already correctly named.

---

## Task 6: Unify the three `proxy_router.py` call sites

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py`
- Modify: `tests/enterprise/litellm/test_proxy_router.py`

- [ ] **Step 1: Write the failing tests**

Replace `test_router_headers_pass_through_filter` (currently asserts raw `x-litellm-router-*`
headers pass through — this behavior is being removed) in `tests/enterprise/litellm/test_proxy_router.py`:

```python
    def test_router_headers_no_longer_pass_through_filter(self):
        """Raw x-litellm-router-* headers are no longer forwarded to clients at all — the
        canonical x-codemie-routing-* vocabulary replaces them entirely (see
        docs/superpowers/specs/2026-09-17-unified-routing-proxy-headers-design.md)."""
        from codemie.enterprise.litellm.proxy_router import (
            LITELLM_FORWARDED_HEADERS,
            PROXY_RESPONSE_HOP_BY_HOP_HEADERS,
            _should_forward_response_header,
        )

        downstream_headers = httpx.Headers(
            {
                "content-type": "application/json",
                "x-litellm-router-tier": "COMPLEX",
                "x-litellm-router-cause": "llm_classifier",
                "x-litellm-router-score": "0.75",
                "x-litellm-router-routed-model": "claude-sonnet-5",
                "x-litellm-router-classifier-model": "claude-haiku-4-5-20251001",
                "x-litellm-router-model-name": "claude-only-simple-no-aff",
                "x-litellm-router-type": "complexity",
                "x-litellm-router-signals": '["llm-classifier:COMPLEX"]',
                "x-litellm-model-name": "claude-sonnet-5",
                "x-litellm-response-cost": "0.0002244",
            }
        )

        response_headers = {k: v for k, v in downstream_headers.items() if _should_forward_response_header(k)}

        for header in (
            "x-litellm-router-tier",
            "x-litellm-router-cause",
            "x-litellm-router-score",
            "x-litellm-router-routed-model",
            "x-litellm-router-classifier-model",
            "x-litellm-router-model-name",
            "x-litellm-router-type",
            "x-litellm-router-signals",
        ):
            assert header not in response_headers
        # x-litellm-model-name is NOT a router header — LITELLM_FORWARDED_HEADERS, unaffected.
        assert response_headers["x-litellm-model-name"] == "claude-sonnet-5"
        assert response_headers["content-type"] == "application/json"
        assert "x-litellm-response-cost" not in response_headers
```

Replace `test_non_router_litellm_headers_still_stripped` similarly — read its current body first
(it constructs a second `downstream_headers` set with non-router `x-litellm-*` headers, e.g.
`x-litellm-call-id`, `x-litellm-version`) and update only its two `from ... import LITELLM_ROUTER_HEADERS`
lines to drop that now-unused import if the test body itself doesn't otherwise reference
`LITELLM_ROUTER_HEADERS` directly — check its body; if it only imports the name without using it
after this task's `_should_forward_response_header` becomes the thing under test, remove the
unused import. This test's own assertions (non-router `x-litellm-*` headers are stripped) are
unaffected by this task and should keep passing unchanged.

Add a new test verifying the "always determine, always emit" behavior at the response-assembly
level:

```python
    def test_response_headers_carry_canonical_routing_when_only_litellm_decided(self):
        """Even when Switchyard never ran (routing_info is None) and only LiteLLM's own
        auto-router decided, the client must still receive x-codemie-routing-* headers, built by
        parsing LiteLLM's raw response headers — not the raw x-litellm-router-* vocabulary."""
        from codemie.enterprise.litellm.proxy_router import _should_forward_response_header
        from codemie.enterprise.litellm.router import routing_info_from_headers
        from codemie.enterprise.switchyard.proxy import _routing_info_to_headers

        downstream_headers = httpx.Headers(
            {
                "content-type": "application/json",
                "x-litellm-router-tier": "COMPLEX",
                "x-litellm-router-cause": "llm_classifier",
                "x-litellm-router-routed-model": "claude-sonnet-5",
            }
        )
        response_headers = {k: v for k, v in downstream_headers.items() if _should_forward_response_header(k)}
        client_routing = routing_info_from_headers(downstream_headers)
        response_headers.update(_routing_info_to_headers(client_routing))

        assert response_headers["x-codemie-routing-tier"] == "complex"
        assert response_headers["x-codemie-routing-decision-source"] == "llm-classifier"
        assert response_headers["x-codemie-routed-model"] == "claude-sonnet-5"
        assert response_headers["x-codemie-routing-family"] == "litellm"
        assert "x-litellm-router-tier" not in response_headers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/litellm/test_proxy_router.py -k "router_headers or litellm_decided" -v`
Expected: FAIL — the exposure policy still allowlists `LITELLM_ROUTER_HEADERS`, and
`_proxy_to_llm_proxy()` still gates the canonical headers on `routing_info is not None`.

- [ ] **Step 3: Update `src/codemie/enterprise/litellm/proxy_router.py`**

Update the import path (from Task 5's rename) — replace:
```python
from codemie.enterprise.litellm.litellm_router_meta import LITELLM_ROUTER_HEADERS
```
with:
```python
from codemie.enterprise.litellm.litellm_router_headers import LITELLM_ROUTER_HEADERS
```

Update `_HEADER_EXPOSURE_POLICIES` (remove `LITELLM_ROUTER_HEADERS` from the exposed set, keep
`LITELLM_FORWARDED_HEADERS`):

```python
_HEADER_EXPOSURE_POLICIES: tuple[_HeaderExposurePolicy, ...] = (
    _HeaderExposurePolicy(prefix="x-litellm-", exposed=LITELLM_FORWARDED_HEADERS),
)
```

Since `LITELLM_ROUTER_HEADERS` is no longer used in the exposure policy, check whether it's still
imported/used anywhere else in this file — it is not (confirmed: the only use was in the
exposure policy tuple) — remove the now-dead import line entirely:
```python
from codemie.enterprise.litellm.litellm_router_headers import LITELLM_ROUTER_HEADERS
```
(Delete this line rather than leaving an unused import — `ruff` will flag it otherwise.)

In `_proxy_to_llm_proxy()`, replace the response-header assembly block:

```python
    response_headers = {k: v for k, v in downstream_response.headers.items() if _should_forward_response_header(k)}
    if downstream_response.headers.get("x-litellm-cache-hit", "").lower() == "true":
        response_headers[CODEMIE_CACHE_HIT_HEADER] = "true"
    if routing_info is not None:
        response_headers.update(_routing_info_to_headers(routing_info))
```

with:

```python
    response_headers = {k: v for k, v in downstream_response.headers.items() if _should_forward_response_header(k)}
    if downstream_response.headers.get("x-litellm-cache-hit", "").lower() == "true":
        response_headers[CODEMIE_CACHE_HIT_HEADER] = "true"
    # Always determine a canonical RoutingInfo, regardless of which mechanism routed: Switchyard
    # decided synchronously (routing_info already set), or only LiteLLM's own auto-router did —
    # in which case parse it back from the raw (unfiltered) downstream headers, same as
    # _finalize_stream_usage_tracking already does for the ES analytics event. Either way, emit
    # the one x-codemie-routing-* vocabulary — never the raw x-litellm-router-* one (already
    # stripped above by _should_forward_response_header).
    client_routing = routing_info if routing_info is not None else routing_info_from_headers(downstream_response.headers)
    response_headers.update(_routing_info_to_headers(client_routing))
```

`routing_info_from_headers` is already imported at the top of this file (confirm the import line
`from codemie.enterprise.litellm.router import routing_info_from_headers` is present — it already
is, used elsewhere in `_finalize_stream_usage_tracking`).

In `_streaming_response_with_usage_tracking()`, replace:

```python
        chunk_source = (
            with_routing_metadata_stream(downstream_response.aiter_raw(), _routing_info_to_headers(routing_info))
            if routing_info
            else downstream_response.aiter_raw()
        )
```

with:

```python
        client_routing = routing_info if routing_info is not None else routing_info_from_headers(downstream_response.headers)
        chunk_source = with_routing_metadata_stream(
            downstream_response.aiter_raw(), _routing_info_to_headers(client_routing)
        )
```

(An empty `client_routing` produces an empty header dict from `_routing_info_to_headers()`, which
`with_routing_metadata_stream`/`_inject_routing_into_message_start` already treat as a no-op
`event_data["message"].update({})` — no separate `is_empty()` branch needed; always wrapping the
stream is correct and simpler than conditionally choosing between two code paths.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/litellm/test_proxy_router.py -v`
Expected: PASS for every test in the file — including
`test_streaming_cache_hit_does_not_queue_usage_tracking`/`test_streaming_cache_miss_queues_usage_tracking`
(Task-6-unrelated tests that call `_streaming_response_with_usage_tracking` without a
`routing_info` argument and with no `x-litellm-router-*` headers on their mock response — these
should keep passing unchanged, since `client_routing` resolves to an empty `RoutingInfo` and the
stream wrapper is a no-op for them, functionally identical to before).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/enterprise/litellm/proxy_router.py tests/enterprise/litellm/test_proxy_router.py
git commit -m "EPMCDME-14888: Emit canonical routing headers to clients regardless of decider"
```

## Context

This is Task 6 of 8 — the actual client-facing behavior change the whole plan exists to deliver.
Both call sites that build what the client actually sees move from "only when Switchyard decided
synchronously" to "always determine a canonical RoutingInfo, from whichever source is available."
The exposure-policy change (`LITELLM_ROUTER_HEADERS` removed from the allowlist) is what actually
stops raw `x-litellm-router-*` headers from reaching the client — the header-assembly change alone
would just add the new headers *alongside* the old ones, not replace them.

---

## Task 7: Re-scope the architecture isolation guard

**Files:**
- Modify: `tests/architecture/test_routing_meta_isolation.py`

- [ ] **Step 1: Update the guard**

Replace the `_GUARDED_NAMES` dict (currently guards both `SWITCHYARD_FIELD_TO_HEADER` and
`LITELLM_ROUTER_FIELD_TO_HEADER`):

```python
_GUARDED_NAMES: dict[str, Path] = {
    "LITELLM_ROUTER_FIELD_TO_HEADER": _SRC_ROOT / "enterprise" / "litellm" / "litellm_router_headers.py",
}
```

Rewrite the module docstring — it currently frames this guard as protecting
`RoutingInfo.meta`'s opaqueness invariant, which no longer exists:

```python
"""Architecture guard: LiteLLM's own external routing-header vocabulary must stay isolated.

``LITELLM_ROUTER_FIELD_TO_HEADER`` (enterprise/litellm/litellm_router_headers.py) mirrors
LiteLLM's own external x-litellm-router-*/x-litellm-classifier-* wire vocabulary (field names
like ``cause``, ``router_model_name``, ``score`` are LiteLLM's own, not ours) — a foreign
contract this codebase reads but does not define. ``LiteLLMRouterHeaders.from_headers()`` (the
only production consumer of this mapping) translates that wire shape into RoutingInfo's own
canonical domain fields (``decision_source``, ``requested_model``, ``confidence``/``router_score``,
...) inside enterprise/litellm/router.py's ``routing_info_from_headers()``. Nothing outside that
one translation function needs to know LiteLLM's raw field names at all — every other consumer
(callbacks, RouterChatModel, the proxy's own client-facing header codec in
enterprise/switchyard/proxy.py) works exclusively off the already-translated RoutingInfo. This
test makes "some other module starts reading LiteLLM's raw wire vocabulary directly, instead of
going through the one translator" a CI failure instead of a silent architectural drift.

Deliberately NOT guarded: ``LITELLM_ROUTER_HEADERS`` (the plain frozenset of header-name
strings, with no field-name association) — historically used as a header-forwarding allowlist
in proxy_router.py; as of the routing-header-unification change that allowlist usage was
removed (raw x-litellm-router-* headers are no longer forwarded to clients at all), but the
frozenset itself may still have other legitimate, non-interpretive uses (testing header-name set
membership, never what a specific header *means*) — that's not "branching on wire-vocabulary
meaning" in the sense this guard cares about.
"""
```

Update the `_from_imported_names`/`test_header_name_mapping_not_imported_outside_owning_module`
function bodies — no change needed, they're already generic over `_GUARDED_NAMES`.

- [ ] **Step 2: Run tests to verify they pass**

Run: `poetry run pytest tests/architecture/test_routing_meta_isolation.py -v`
Expected: PASS. `enterprise/litellm/router.py` only ever imports the `LiteLLMRouterHeaders`
class itself (confirmed by repo-wide grep before this plan was written) — never
`LITELLM_ROUTER_FIELD_TO_HEADER` directly, since the class owns that mapping as a `ClassVar`.
If this test unexpectedly fails, something now imports the raw mapping directly instead of going
through `LiteLLMRouterHeaders` — investigate and fix that import, don't weaken the guard.

- [ ] **Step 3: Commit**

```bash
git add tests/architecture/test_routing_meta_isolation.py
git commit -m "EPMCDME-14888: Re-scope routing-meta isolation guard to LiteLLM's wire vocabulary"
```

## Context

This is Task 7 of 8. The guard's original justification (protecting `RoutingInfo.meta`'s
opaqueness) is gone along with `.meta` itself (Task 1) and `SwitchyardMeta` (Task 4) — but the
underlying isolation principle for LiteLLM's *foreign* wire vocabulary is still real and worth
enforcing, just re-justified. This task doesn't change the guard's mechanism, only which name(s)
it watches and why.

---

## Task 8: Full suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `poetry run pytest tests/ -q`
Expected: the failure count matches the pre-existing baseline established before this plan
(confirm by comparing against the count from the immediately-preceding commit on this branch —
at the time this plan was written, that baseline was 213 pre-existing, unrelated failures). Any
NEW failure beyond that baseline must be investigated and fixed before proceeding — do not treat
a changed failure count as "probably fine."

- [ ] **Step 2: Confirm no `SwitchyardMeta`/`LiteLLMRouterMeta`/`RoutingInfo.meta`/`build_routing_meta` references remain**

Run:
```bash
grep -rn "SwitchyardMeta\|LiteLLMRouterMeta\|build_routing_meta\|build_switchyard_routing_meta" --include='*.py' src/ tests/
grep -rn "\.meta\b" --include='*.py' src/codemie/core/routing_info.py src/codemie/enterprise/switchyard/ src/codemie/enterprise/litellm/
```
Expected: no matches (the first command finds zero references anywhere; the second finds none on
`RoutingInfo` specifically — a stray unrelated `.meta` on some other, unrelated object is fine and
out of scope, use judgment reading any hits).

- [ ] **Step 3: Run lint**

Run: `make ruff`
Expected: PASS, no new findings — pay particular attention to unused-import warnings on
`src/codemie/enterprise/litellm/proxy_router.py` (the removed `LITELLM_ROUTER_HEADERS` import)
and `src/codemie/core/routing_info.py` (the possibly-removed `Field` import from Task 1).

- [ ] **Step 4: Re-read the design doc's field-summary table and confirm every row is implemented**

Check off against `docs/superpowers/specs/2026-09-17-unified-routing-proxy-headers-design.md`'s
final table — one row per file changed, confirm each matches what actually landed.

- [ ] **Step 5: Final commit (only if Steps 1-4 required any fix-up)**

```bash
git status
# If clean, nothing to commit.
# If a fix was needed:
git add -A
git commit -m "EPMCDME-14888: Fix up unified routing header verification findings"
```
