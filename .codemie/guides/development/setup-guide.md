# Command Reference for AI Agents

## Critical Rule

**🚨 ALWAYS activate virtualenv BEFORE any Python command**

```bash
source .venv/bin/activate
```

**If command fails with "command not found"** → virtualenv not activated

## Virtual Environment

| Check | Command | Expected |
|-------|---------|----------|
| **Verify activated** | `which python` | `.venv/bin/python` |
| **Activate** | `source .venv/bin/activate` | Shell prompt shows `(.venv)` |
| **Alternative** | `.venv/bin/ruff check src/` | Use direct path for single commands |

## Command Reference

| Task | Command | When |
|------|---------|------|
| **Activate venv** | `source .venv/bin/activate` | Before ANY Python command |
| **Start server** | `cd src/ && poetry run uvicorn codemie.rest_api.main:app --reload` | Dev server (port 8080) |
| **Lint + fix** | `poetry run ruff check --fix` | Auto-fix violations |
| **Format** | `poetry run ruff format` | Format code |
| **Lint + format** | `make ruff` | Pre-commit |
| **Tests** | `poetry run pytest tests/` | ONLY if user requests |
| **CI verify** | `make verify` | Lint + tests |
| **Install deps** | `poetry install` | After pull/merge |
| **Alembic migrations** | `cd src/ && poetry run alembic upgrade head` | After DB schema changes |

**⚠️ Database Migrations**: DO NOT modify alembic files manually (see `src/external/alembic/README.MD`)

**API Docs**: http://localhost:8080/docs (after server start)

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `command not found: poetry/ruff/pytest` | Venv not activated | `source .venv/bin/activate` |
| `ModuleNotFoundError` | Dependencies missing | `poetry install` |
| Database connection fails | PostgreSQL not running | Verify service running |
| Port 8080 in use | Server already running | `lsof -ti:8080 \| xargs kill -9` |
| Linting fails | Code quality issues | `poetry run ruff check --fix` |
| Import errors after pull | Dependencies changed | `poetry install` |

### Diagnostic Commands

| Check | Command | Expected |
|-------|---------|----------|
| **Venv active** | `which python` | `.venv/bin/python` |
| **Python version** | `python --version` | `Python 3.12.x` |
| **Dependencies OK** | `poetry show \| wc -l` | 100+ packages |
| **DB running** | `psql -h localhost -U postgres -c "SELECT 1;"` | Success |
| **Elasticsearch** | `curl -s http://localhost:9200` | JSON response |
| **Server ready** | `curl -s http://localhost:8080/docs` | HTML response |

## References

**Related Guides**:
- [configuration-patterns.md](./configuration-patterns.md) - Environment variables
- [code-quality.md](../standards/code-quality.md) - Type hints, linting
- [testing-patterns.md](../testing/testing-patterns.md) - pytest usage

---
