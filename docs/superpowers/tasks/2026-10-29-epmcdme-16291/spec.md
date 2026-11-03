# EPMCDME-16291 acceptance criteria (from the Jira ticket and Klim Starykau's 2026-10-27 update)

After EPMCDME-15542 all CLI Analytics money is catalog-derived; Bedrock-style model ids must resolve for every money value and public model name.

- AC1: `anthropic.claude-haiku-4-5-20251001-v1:0`, `converse/global.anthropic.…-v1:0`, `converse/us.anthropic.…-v1:0`, `converse/eu.anthropic.…-v1:0` resolve to catalog `claude-haiku-4-5-20251001` and are priced at its catalog rates in every money value (cost, cache-read cost, bloat, events, dispatches, aggregates).
- AC2: at the ticket rates (input 1e-6, output 5e-6, cache read 1e-7), 796 input + 23 output tokens cost 0.000911.
- AC3: session cache_read_cost_usd for the historical fixture = 80,579 x 1e-7 + 212,878 x 5.5e-7 = 0.1251408.
- AC4: session details (`model_name`, header) and events show the normalised model name, de-duplicated, as Tools -> Tokens by model does.
- AC5: a model that cannot be priced is listed in `metadata.unpriced_models`, never silently counted as 0; no guessed or default price.
- AC6: catalog model ids (e.g. `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`) are unaffected; exact catalog base_name/deployment_name matches, date/version distinctions and ambiguity are respected.
- AC7: ingestion identity/dedup and stored raw evidence are unchanged.
- AC8: verify a newly captured real mixed-model session through ingestion, storage, rollup and Session API, using independent arithmetic over its captured counters and platform catalog rates. The complete 2026-11-03 Haiku Bedrock + Sonnet capture totals 0.2898198 COST and 0.0088458 CACHE-READ COST. Per user direction, this own-session evidence replaces replay of the unavailable historical QA export; it does not claim to reproduce that session's 0.1016444.
