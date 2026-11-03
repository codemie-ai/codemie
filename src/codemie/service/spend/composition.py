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

"""The ledger's composition root - the one place the coverage values are read.

**Read once, here, and passed down.** No floor is re-read deep in a handler and none is ever
derived from a query. That single read is what makes activation atomic with respect to readers: a
floor becomes visible to every consumer at the same instant, so no two surfaces ever sit on
opposite sides of it.

Registration happens at startup and the provider branch stays in the registry, so core code never
imports the LiteLLM adapter and a consumer cannot tell which ledger answered it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from codemie.configs import config, logger
from codemie.service.spend.coverage import LedgerCoverage

if TYPE_CHECKING:
    # Type-checking only, for the same reason every other import below sits inside a function: the
    # registry and the ledgers import this package's own consumers, and a module-level import here
    # would turn the composition root into a cycle.
    from codemie.service.spend.ledger import SpendLedger
    from codemie.service.spend.routing import LLMCallClass


def _parse_floor(raw: str, name: str) -> datetime | None:
    """Parse a published floor, or None when it is unset.

    An unparseable value is **not** silently treated as unset: unset means "not activated" or
    "rollout has not landed", both defined states, whereas a malformed value is a deployment
    defect. Reading it as unset would answer unavailable everywhere and look like a coverage
    problem rather than a typo, so it is logged loudly and then treated as unset - which is the
    safe direction, because the alternative is serving figures against a floor nobody set.
    """
    if not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        logger.error(f"spend_ledger_read: {name} is not an ISO-8601 timestamp ({raw!r}); treating as unset")
        return None
    if parsed.tzinfo is None:
        logger.error(f"spend_ledger_read: {name} has no timezone ({raw!r}); treating as unset")
        return None
    return parsed.astimezone(timezone.utc)


def load_ledger_coverage() -> LedgerCoverage:
    """Read the two floors and the two bounding parameters, once.

    **The floors and readiness describe `litellm_spend_logs` and nothing else**, so they are honoured only
    where some call class routes to LiteLLM. A deployment with none ignores them: coverage stays unactivated,
    no cutover is registered and no surface splits at `F`, which leaves its figures with the default
    Elasticsearch ledger exactly as before. The two bounding parameters apply everywhere, because the metric
    store resolves ids with them whichever adapter answers.
    """
    from codemie.service.spend.routing import LLMCallClass, uses_litellm

    routed = any(uses_litellm(call_class) for call_class in LLMCallClass)
    coverage = LedgerCoverage(
        coverage_floor=_parse_floor(config.SPEND_LEDGER_COVERAGE_FLOOR, "SPEND_LEDGER_COVERAGE_FLOOR")
        if routed
        else None,
        explicit_identity_floor=_parse_floor(config.SPEND_LEDGER_IDENTITY_FLOOR, "SPEND_LEDGER_IDENTITY_FLOOR")
        if routed
        else None,
        emission_lag=timedelta(hours=config.SPEND_LEDGER_EMISSION_LAG_HOURS),
        max_elasticsearch_ids=config.SPEND_LEDGER_MAX_METRIC_IDS,
        ready=config.SPEND_LEDGER_READY and routed,
    )
    if not routed:
        logger.info(
            "spend_ledger_read: no call class routes to LiteLLM; coverage floors and readiness are ignored "
            "and spend figures are served by the default ledger"
        )
    elif not coverage.activated:
        # Not a warning about a misconfiguration - this is the correct state before a first
        # activation, and the honest answer in it is unavailable rather than zero.
        logger.info("spend_ledger_read: ledger is not activated; every spend figure reports unavailable")
    return coverage


def register_spend_ledgers() -> None:
    """Register one ledger per call class. Called once at startup.

    **The split is derived from the routing predicate, per class, and never from a global "is LiteLLM
    enabled" flag** - that flag is strictly broader than routing and is true in deployments whose
    traffic never reaches LiteLLM, where it would point every consumer at a table no request writes
    to.

    A deployment where every class routes to LiteLLM registers **no** default ledger at all, rather
    than an unscoped one. An unscoped default ledger is the double-count hazard, and there is nothing
    for it to serve here; if the LiteLLM ledger then failed to register, the registry answers
    unavailable, which is honest.

    **That empty case is a branch and not an early return**, which is a distinction about the future
    rather than about today's behaviour: the two are identical while this function ends here, and
    they stop being identical the moment a step is appended - a `return` silently skips it in exactly
    the configuration production runs, so the step would work everywhere it was tested and nowhere it
    mattered. The pre-floor legacy engine was the first such step (see `register_spend_cutover`), and
    it does not depend on the native classes at all.
    """
    from codemie.clients.elasticsearch import ElasticSearchClient
    from codemie.service.spend.default_ledger import DefaultSpendLedger
    from codemie.service.spend.ledger_registry import register_default_ledger
    from codemie.service.spend.routing import LLMCallClass, uses_litellm

    native_classes = frozenset(call_class for call_class in LLMCallClass if not uses_litellm(call_class))
    if native_classes and not ElasticSearchClient.is_configured():
        # A deployment without Elasticsearch (standalone mode, EPMCDME-14347) has no store for the
        # native classes. Their empty slots answer unavailable; a default ledger would answer the
        # same after failing, and log an error and count a ledger failure on every read.
        logger.info(
            "spend_ledger_register: Elasticsearch is not configured; no default ledger registered, so "
            f"{sorted(call_class.value for call_class in native_classes)} report unavailable"
        )
    elif native_classes:
        register_default_ledger(DefaultSpendLedger(native_classes))
        logger.info(
            "spend_ledger_register: default ledger serves "
            f"{sorted(call_class.value for call_class in native_classes)}"
        )
    else:
        logger.info("spend_ledger_register: every call class routes to LiteLLM; no default ledger registered")


def _cutover_slots() -> list[tuple[SpendLedger, list[LLMCallClass]]]:
    """Every call class paired with the ledger the registry gives it - **the empty slot included**.

    **Above-floor scoping and pre-floor scoping answer different questions, and conflating them fails
    in both directions.** Above `F` the question is *which rows would two ledgers both claim*, and there
    a slot nothing registered claims nothing, so skipping it is right. Below `F` the question is *which
    call classes have no engine at all* - and below `F` the ledger table is empty by definition, because
    `F` is the instant it starts holding rows, so there is nothing for a pre-floor engine to double count
    against and every class deserves one however its slot came out above the floor. One engine per
    wrapper reading *every* class would sum each sub-`F` row twice in a mixed configuration; skipping a
    slot holding the noop would leave the classes it owns with no pre-floor engine, dropping their money.

    **The pairing is read from `ledgers_by_call_class()` rather than re-derived from the routing
    predicate here.** The registry already owns the question *which ledger serves this class* - that
    is the whole reason the provider branch exists exactly once - and a second derivation of it in
    this module is how the two drift: a routing rule changed on one side only would scope a pre-floor
    engine to classes its ledger does not serve, which mis-partitions the metric names silently and in
    the direction of a double count.

    **Grouped on object identity, which is the very key `SpendService._distinct_ledgers` de-duplicates
    on.** One group becomes one cutover wrapper, so a single ledger registered in both slots is still
    consulted once; a group per call class would build two wrappers around one ledger and hand the sum
    across wrappers the same rows twice. The noop is a registry singleton, so two empty slots group
    into one wrapper for the same reason and by the same rule.

    **The whole mapping is read before any of it is written back.** `register_spend_cutover` writes to
    the registry this reads, so a slot re-read after an earlier write would hand back the wrapper just
    installed and wrap it a second time - a cutover inside a cutover, with two legacy engines under it.

    **A registry where *every* slot is empty is left alone, and that is a different state rather than
    the same one twice over.** Nothing registered means the deployment has no spend port: every figure
    on both sides of `F` reports unavailable, which is the documented pre-activation answer. This step
    composes a pre-floor engine *under* a ledger a deployment registered; it does not become that
    deployment's only ledger, which is a new answer chosen by the step whose job is to preserve an
    existing one - a surface printing money for every period before the rollout beside `unavailable`
    for the period a reader is actually looking at. The residual is real and is written down rather
    than hidden: a fully-LiteLLM deployment whose enterprise registration failed keeps reporting
    unavailable below `F` too, and that figure is owed to the failed registration, not to the floor.
    """
    from codemie.service.spend.ledger_registry import _NOOP_LEDGER, ledgers_by_call_class

    slots: list[tuple[SpendLedger, list[LLMCallClass]]] = []
    for call_class, ledger in ledgers_by_call_class().items():
        for registered, classes in slots:
            if registered is ledger:
                classes.append(call_class)
                break
        else:
            slots.append((ledger, [call_class]))

    # Identity against the registry's singleton, not `ledger_name == "noop"`: the name is a
    # diagnostic string a future ledger is free to reuse, while the object is the registry's own
    # statement that the slot is empty.
    if all(ledger is _NOOP_LEDGER for ledger, _ in slots):
        return []
    return slots


def register_spend_cutover() -> None:
    """Re-register each ledger wrapped in the pre-floor cutover. Called **last**, after every ledger.

    Below the published floor `F` the spend ledger holds nothing and says so, which is the truth about
    `litellm_spend_logs` and a blank screen for every period the platform had before the rollout. This
    step puts the legacy Elasticsearch engine underneath each registered ledger so those periods keep
    answering, and it does it by composition: `CutoverSpendLedger` is a `SpendLedger` holding two, so
    `get_spend_ledger` still returns exactly one ledger per call class and no consumer, response model
    or screen can tell that anything changed.

    **Off by default, and off means untouched.** With `SPEND_LEDGER_LEGACY_BELOW_FLOOR` false this
    returns before the registry is read, let alone written, so a deployment that has not opted in
    registers precisely what it registered before this function existed. The switch is a return rather
    than a flag threaded into the wrapper on purpose: a cutover constructed with its legacy half
    disabled still replaces the object in the slot, still changes `ledger_name`, and would then have to
    be *reasoned about* to be ruled out instead of simply not being there.

    **An unactivated coverage registers nothing either.** The cutover splits a period at `F`, and an
    unactivated ledger has no `F` to split at - so there is no boundary to serve two engines around,
    and the honest answer stays the one an unactivated ledger already gives.

    **The floor is taken from the registered coverage, never read from config a second time.** Two
    reads can observe the value mid-change and leave two consumers on opposite sides of one floor,
    which is the failure this module's docstring exists to prevent; the read happened once, at startup,
    and `ledger_coverage()` is where its result lives.

    **Ordering is load-bearing.** This wraps whatever ended up in each slot, so it has to run after the
    last registration a deployment makes - including the LiteLLM ledger, which enterprise code registers
    after this module's own. Run in between, it would wrap the default ledger and then watch the
    spend-logs ledger land unwrapped in its slot, leaving every pre-floor figure on the traffic that
    actually matters reporting unavailable: a cutover that covers the half of the deployment nobody was
    worried about.

    **Every call class gets a pre-floor engine, and a slot nothing registered is no exception.** A
    cutover over `legacy + noop` is exactly the right object there: below `F` it answers from
    Elasticsearch, at and above `F` the noop reports unavailable, which is precisely what that slot
    already said. What keeps two engines from both counting a row is their **class scope**, not whether
    the slots above them are occupied - `_cutover_slots` explains why the two are different questions -
    so skipping the empty slot bought nothing and cost every pre-floor chat and CLI-proxy figure in the
    deployment where the LiteLLM ledger failed to construct and `rest_api/main.py` swallowed the error:
    an ordinary startup failure, not a misconfiguration. A registry where *nothing* registered is still
    left alone, for the reason `_cutover_slots` gives. Each slot is written only once its wrapper
    exists, so a construction failure leaves that slot holding the ledger it already held rather than
    nothing - a degraded pre-floor period, never a vanished figure.

    **Each wrapper's legacy engine is scoped to the call classes of the ledger it wraps, and that is
    the same double-count guard `register_spend_ledgers` applies above the floor.** The mixed
    configuration - chat through LiteLLM, embeddings native, one environment variable away - fills both
    slots, so both get a wrapper and the deployment runs two pre-floor engines. `_distinct_ledgers`
    de-duplicates on object identity and the two wrappers are distinct objects, so both are asked about
    the same sub-`F` period and `combine_results` **adds** their answers. Scoped per slot, the two
    engines read disjoint metric-name sets - `legacy_metric_names` turns the class scope into the name
    set, chat's names plus the CLI proxy metric on one side and the datasource metric on the other - so
    that sum counts each pre-floor row exactly once, precisely as the sum across the two above-floor
    ledgers does. An engine scoped to *every* class under each wrapper is what doubles every sub-`F`
    figure, and it doubles it silently: both halves look entirely plausible on their own. That scope is
    also what makes wrapping an empty slot safe: its engine reads its own classes' names and no other
    engine's, so an added wrapper adds the classes nobody was answering for and never a second copy of
    the classes somebody already was.
    """
    if not config.SPEND_LEDGER_LEGACY_BELOW_FLOOR:
        return

    from codemie.service.spend.cutover import CutoverSpendLedger
    from codemie.service.spend.ledger_registry import (
        ledger_coverage,
        register_default_ledger,
        register_spend_logs_ledger,
    )
    from codemie.service.spend.legacy_ledger import LegacySpendLedger
    from codemie.service.spend.routing import uses_litellm

    coverage = ledger_coverage()
    if not coverage.activated:
        logger.info("spend_ledger_register: ledger is not activated; no pre-floor cutover registered")
        return
    floor = coverage.coverage_floor
    assert floor is not None  # implied by `activated`, restated for the type checker

    wrapped: list[str] = []
    for current, call_classes in _cutover_slots():
        # The scope that keeps two wrappers from counting one row twice - see the docstring. Handing
        # `call_classes` on means this engine reads only the metric names those classes emit, which is
        # the guard the ledger it sits under already applies above the floor.
        legacy = LegacySpendLedger(call_classes=call_classes)
        cutover = CutoverSpendLedger(legacy=legacy, current=current, floor=floor)
        # Written to whichever slot each of this ledger's classes actually reads, which is the
        # registry's own rule and not a second routing decision: `get_spend_ledger` sends a class to
        # one slot, so the wrapper has to land in that one. Repeated writes of the same slot are
        # idempotent, and a ledger registered in *both* slots has to be re-registered in both.
        for call_class in call_classes:
            if uses_litellm(call_class):
                register_spend_logs_ledger(cutover)
            else:
                register_default_ledger(cutover)
        scope = ",".join(sorted(call_class.value for call_class in call_classes))
        wrapped.append(f"{current.ledger_name}[{scope}]")

    if wrapped:
        logger.info(
            f"spend_ledger_register: periods below {floor.isoformat()} served by one "
            f"{LegacySpendLedger.ledger_name} engine per slot, each scoped to that slot's own classes; "
            f"above it by {sorted(wrapped)}"
        )
    else:
        logger.info("spend_ledger_register: no ledger registered; no pre-floor cutover registered")
