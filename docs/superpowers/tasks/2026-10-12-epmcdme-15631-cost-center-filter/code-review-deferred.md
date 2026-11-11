# Deferred from code review — 2026-10-12-epmcdme-15631-cost-center-filter (2026-10-12 UTC)

- **Trailing newline accepted in model identifiers** — `src/codemie/service/project/project_service.py:59` — A model identifier ending in a newline passes the `$`-anchored pattern and will not match the intended identifier. Pre-existing: the ticket spec explicitly keeps the separate `@` model-identifier change on the branch outside this ticket's scope.
- **Malformed @ model identifiers accepted** — `src/codemie/service/project/project_service.py:59` — The character-class-only pattern accepts `@` at an edge or repeated, allowing degenerate identifiers. Pre-existing: the ticket spec explicitly keeps the separate `@` model-identifier change on the branch outside this ticket's scope.
