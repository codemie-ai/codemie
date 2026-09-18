# Routing Analytics — CLI ClickHouse Parity

**Deferred from**: EPMCDME-14888
**Status**: Out of scope — implement as a separate Jira ticket
**Depends on**: EPMCDME-14888 Slice 1 (RoutingInfo + ES ingestion fields must land first)

## What needs to be done

The platform analytics (Elasticsearch path) will carry routing dimensions after EPMCDME-14888.
The CLI report uses a separate ClickHouse data path and will not have routing parity until this work is done.

### Files to change

| File | Change |
|---|---|
| `cli_analytics_repository.py` | Add routing aggregation queries (tier distribution, decision-source distribution, routed-vs-total counts, classifier overhead cost) against the ClickHouse `codemie_analytics` table |
| `cli_analytics.py` | Expose routing metrics and savings figures in CLI analytics endpoints |
| ClickHouse write path | Extend the `codemie_analytics` write to carry the same routing dimensions added to ES in Slice 1: `routed_model`, `requested_model`, `tier`, `decision_source`, `confidence`, `classifier_tokens`, `classifier_cost_usd` |

### Acceptance criteria (to inherit)

- [ ] CLI report shows the same routing metrics as the platform UI: routed session count, request count, tier distribution, decision-source distribution, classifier overhead cost
- [ ] Savings figures in the CLI report agree with platform UI values for the same period
- [ ] ClickHouse write path carries all routing dimensions introduced in EPMCDME-14888 Slice 1

### Notes

- `LLM_PROXY_ENABLED=False` deployments will show zero for classifier-related fields on both paths; this is expected behaviour, not a bug
- Pricing lookup for counterfactual savings must use the same model-price registry introduced in EPMCDME-14888 Slice 3; import it rather than duplicating it
- Request-ID correlation between ClickHouse records and LiteLLM spend logs should be verified before implementing savings on this path
