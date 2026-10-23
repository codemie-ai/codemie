# Deferred from code review — 2026-10-07-epmcdme-15208-clear-temperature (2026-10-07)

- **Anthropic/Vertex ignore features.temperature** — `src/codemie/core/dependecies.py:411-502` — get_vertex_llm and get_anthropic_llm still pass the temperature kwarg without checking `features.temperature`. Pre-existing: task scope names only LiteLLM and direct Bedrock; these siblings were not changed by this work and None is passed as the client default.

_Resolved in MR !4481 review follow-up: Anthropic and Vertex now gate temperature via `_temperature_allowed`; Bedrock also gates `top_p`._
