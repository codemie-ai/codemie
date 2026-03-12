# Local Testing Guide

## Quick Summary

Comprehensive guide for testing Codemie locally with API access, database queries, and test execution patterns. Focus on practical commands and database access options.

**Target**: Local development and testing workflows
**Environment**: localhost:8080 (API), localhost:5432 (PostgreSQL), localhost:9200 (Elasticsearch)
**Philosophy**: Test safely, query responsibly, verify thoroughly

## Prerequisites

- Server running: `cd src/ && poetry run uvicorn codemie.rest_api.main:app --reload`
- Docker services: PostgreSQL, Elasticsearch, Kibana (via `docker-compose up -d`)
- Virtual environment activated: `source .venv/bin/activate`

---

## API Testing (Local)

### Authentication Header (Required)

**🚨 CRITICAL**: All local API requests MUST include `user-id` header

```bash
# Template
curl -X 'GET' \
  'http://localhost:8080/v1/ENDPOINT' \
  -H 'accept: application/json' \
  -H 'user-id: dev-codemie-user'
```

### Common API Test Patterns

| Endpoint | Method | Header Required | Example |
|----------|--------|-----------------|---------|
| `/v1/assistants` | GET | `user-id: dev-codemie-user` | List assistants |
| `/v1/conversations` | GET | `user-id: dev-codemie-user` | List conversations |
| `/v1/analytics/adoption-dimensions` | GET | `user-id: dev-codemie-user` | Analytics data |
| `/docs` | GET | No | API documentation |
| `/health` | GET | No | Health check |

### Example: List Assistants

```bash
# Basic request
curl -X 'GET' \
  'http://localhost:8080/v1/assistants?scope=visible_to_user&minimal_response=false&page=0&per_page=12' \
  -H 'accept: application/json' \
  -H 'user-id: dev-codemie-user'
```

**Response**: JSON array of assistants

### Example: Get Analytics Data

```bash
# D4 metrics endpoint
curl -X 'GET' \
  'http://localhost:8080/v1/analytics/d4-metrics?page=0&per_page=20' \
  -H 'accept: application/json' \
  -H 'user-id: dev-codemie-user'
```

### Example: POST Request (Create Assistant)

```bash
# Create assistant
curl -X 'POST' \
  'http://localhost:8080/v1/assistants' \
  -H 'accept: application/json' \
  -H 'user-id: dev-codemie-user' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Test Assistant",
    "description": "Testing assistant creation",
    "project": "codemie"
  }'
```

### Using HTTPie (Alternative)

```bash
# Install: pip install httpie
http GET localhost:8080/v1/assistants \
  user-id:dev-codemie-user \
  scope==visible_to_user \
  page==0 \
  per_page==12
```

### Using Python Requests

```python
import requests

url = "http://localhost:8080/v1/assistants"
headers = {
    "accept": "application/json",
    "user-id": "dev-codemie-user"
}
params = {
    "scope": "visible_to_user",
    "page": 0,
    "per_page": 12
}

response = requests.get(url, headers=headers, params=params)
print(response.json())
```

---

## Database Access (PostgreSQL)

### Connection Details

| Parameter | Value |
|-----------|-------|
| **Host** | localhost |
| **Port** | 5432 |
| **User** | postgres |
| **Password** | password |
| **Database** | postgres |
| **URL** | `postgresql://postgres:password@localhost:5432/postgres` |
| **Container** | postgres |

### Option 1: Docker Exec (psql) - ⭐ RECOMMENDED

**Best for**: Ad-hoc queries, exploration, quick checks

**Pros**: No local tools needed, guaranteed access, isolated environment
**Cons**: Longer command syntax

```bash
# Interactive shell
docker exec -it postgres psql -U postgres -d postgres

# Single query
docker exec -it postgres psql -U postgres -d postgres -c "SELECT COUNT(*) FROM codemie.assistants"

# Query with output format
docker exec -it postgres psql -U postgres -d postgres -c "
SELECT project, COUNT(*) as assistant_count
FROM codemie.assistants
GROUP BY project
ORDER BY assistant_count DESC
LIMIT 5
"

# Export to CSV
docker exec -it postgres psql -U postgres -d postgres -c "
COPY (
  SELECT project, COUNT(*) as count
  FROM codemie.assistants
  GROUP BY project
) TO STDOUT WITH CSV HEADER
" > assistants_by_project.csv
```

**Common psql Commands** (in interactive shell):
```sql
\dt codemie.*           -- List all tables in codemie schema
\d codemie.assistants   -- Describe assistants table
\l                      -- List all databases
\q                      -- Quit
\x                      -- Toggle expanded display
```

### Option 2: Python Script (SQLModel/SQLAlchemy)

**Best for**: Complex queries, data analysis, scripting

**Pros**: Type safety, Python ecosystem, reusable code
**Cons**: Requires project setup

```python
# Run from project root with virtualenv activated
poetry run python -c "
from codemie.rest_api.models.assistant import Assistant
from sqlmodel import Session, select

# READ queries (safe)
with Session(Assistant.get_engine()) as session:
    # Count assistants
    result = session.exec(select(Assistant)).all()
    print(f'Total assistants: {len(result)}')

    # Group by project
    from sqlalchemy import func, text
    query = text('''
        SELECT project, COUNT(*) as count
        FROM codemie.assistants
        WHERE id NOT LIKE 'Virtual%%'
        GROUP BY project
        ORDER BY count DESC
        LIMIT 5
    ''')
    rows = session.exec(query).all()
    for row in rows:
        print(f'{row.project}: {row.count}')
"
```

**Full script example**:

```python
# save as scripts/check_assistants.py
from codemie.rest_api.models.assistant import Assistant
from sqlmodel import Session, select
from sqlalchemy import text

def main():
    with Session(Assistant.get_engine()) as session:
        # Example: Check assistant complexity distribution
        query = text("""
            SELECT
                project,
                COUNT(*) as total,
                COUNT(CASE WHEN jsonb_array_length(COALESCE(toolkits, '[]'::jsonb)) > 0 THEN 1 END) as with_tools,
                COUNT(CASE WHEN jsonb_array_length(COALESCE(context, '[]'::jsonb)) > 0 THEN 1 END) as with_context
            FROM codemie.assistants
            WHERE id NOT LIKE 'Virtual%'
            GROUP BY project
            ORDER BY total DESC
            LIMIT 10
        """)

        print(f"{'Project':<40} | {'Total':>6} | {'Tools':>6} | {'Context':>6}")
        print("-" * 70)

        for row in session.exec(query).all():
            print(f"{row.project:<40} | {row.total:>6} | {row.with_tools:>6} | {row.with_context:>6}")

if __name__ == "__main__":
    main()
```

Run: `poetry run python scripts/check_assistants.py`

### Option 3: psql Command Line (Local Client)

**Best for**: Developers with PostgreSQL installed locally

**Pros**: Full psql features, scriptable
**Cons**: Requires PostgreSQL client installation

```bash
# Interactive
psql -h localhost -p 5432 -U postgres -d postgres

# Single query
psql -h localhost -p 5432 -U postgres -d postgres -c "SELECT COUNT(*) FROM codemie.assistants"

# From file
psql -h localhost -p 5432 -U postgres -d postgres -f query.sql

# Password: password (will be prompted)
```

### Option 4: DBeaver / DataGrip / pgAdmin (GUI)

**Best for**: Visual exploration, schema inspection, complex joins

**Pros**: Visual interface, query builder, data export
**Cons**: Requires tool installation

**Connection settings**:
```
Host: localhost
Port: 5432
Database: postgres
Username: postgres
Password: password
```

---

## Database Query Patterns

### Safe Read Queries (Always Allowed)

```sql
-- Count records
SELECT COUNT(*) FROM codemie.assistants;

-- Group by project
SELECT project, COUNT(*) as count
FROM codemie.assistants
GROUP BY project
ORDER BY count DESC;

-- Join queries
SELECT
    a.project,
    a.name as assistant_name,
    COUNT(c.id) as conversation_count
FROM codemie.assistants a
LEFT JOIN codemie.conversations c ON c.initial_assistant_id = a.id
GROUP BY a.project, a.name
ORDER BY conversation_count DESC
LIMIT 10;

-- Analytics queries (match D4 metrics)
SELECT
    a.project,
    COUNT(DISTINCT a.id) as total_assistants,
    COUNT(CASE WHEN jsonb_array_length(COALESCE(a.toolkits, '[]'::jsonb)) > 0 THEN 1 END) as tool_enabled
FROM codemie.assistants a
WHERE a.id NOT LIKE 'Virtual%'
GROUP BY a.project;
```

### Write Queries (⚠️ REQUIRE APPROVAL)

```sql
-- ⚠️ INSERT - requires user approval
INSERT INTO codemie.assistants (id, name, project, ...)
VALUES ('test-123', 'Test Assistant', 'codemie', ...);

-- ⚠️ UPDATE - requires user approval
UPDATE codemie.assistants
SET name = 'Updated Name'
WHERE id = 'test-123';

-- ⚠️ DELETE - requires user approval
DELETE FROM codemie.assistants
WHERE id = 'test-123';
```

**Rule**: ALWAYS ask user before running INSERT/UPDATE/DELETE queries

---

## Testing Workflows

### Workflow 1: API Endpoint Testing

```bash
# 1. Start server
cd src && poetry run uvicorn codemie.rest_api.main:app --reload

# 2. Test endpoint (new terminal)
curl -X 'GET' \
  'http://localhost:8080/v1/analytics/d4-metrics?page=0&per_page=5' \
  -H 'accept: application/json' \
  -H 'user-id: dev-codemie-user' | jq .

# 3. Verify in database
docker exec -it postgres psql -U postgres -d postgres -c "
SELECT project, COUNT(*) FROM codemie.assistants GROUP BY project LIMIT 5
"

# 4. Check logs
# Server logs appear in terminal where uvicorn is running
```

### Workflow 2: Analytics Query Verification

```bash
# 1. Run analytics endpoint
curl -s http://localhost:8080/v1/analytics/d4-metrics \
  -H 'user-id: dev-codemie-user' > api_result.json

# 2. Run equivalent SQL query
docker exec -it postgres psql -U postgres -d postgres -c "
SELECT
    a.project,
    COUNT(*) as total_assistants
FROM codemie.assistants a
WHERE a.id NOT LIKE 'Virtual%'
GROUP BY a.project
ORDER BY total_assistants DESC
" > sql_result.txt

# 3. Compare results
cat api_result.json | jq '.data.rows[] | {project, total_assistants}'
cat sql_result.txt
```

### Workflow 3: Data Verification Script

```python
# save as scripts/verify_d4_data.py
from codemie.service.analytics.queries.adoption_framework import AdoptionQueryBuilder, FrameworkConfig
from codemie.rest_api.models.assistant import Assistant
from sqlmodel import Session

def verify_d4_metrics():
    """Verify D4 metrics calculation."""
    config = FrameworkConfig()
    builder = AdoptionQueryBuilder(config)

    query, params = builder.build_d4_metrics_query(
        projects=None,
        page=0,
        per_page=5,
    )

    with Session(Assistant.get_engine()) as session:
        result = session.execute(query, params)

        print(f"{'Project':<40} | {'D4 Score':>8} | {'Assistants':>10} | {'Workflows':>9}")
        print("-" * 80)

        for row in result:
            print(
                f"{row.project:<40} | {row.d4_score:>8.1f} | "
                f"{row.total_assistants:>10} | {row.total_workflows:>9}"
            )

if __name__ == "__main__":
    verify_d4_metrics()
```

Run: `poetry run python scripts/verify_d4_data.py`

---

## pytest Testing

**⚠️ IMPORTANT**: Only run tests when user explicitly requests

### Running Tests

```bash
# All tests
poetry run pytest tests/

# Specific test file
poetry run pytest tests/codemie/service/analytics/test_adoption_framework.py

# Specific test function
poetry run pytest tests/codemie/service/analytics/test_adoption_framework.py::test_d4_metrics

# With coverage
poetry run pytest tests/ --cov=codemie --cov-report=html

# Verbose output
poetry run pytest tests/ -v

# Stop on first failure
poetry run pytest tests/ -x

# Show print statements
poetry run pytest tests/ -s
```

### Test Markers

```bash
# Run only unit tests
poetry run pytest tests/ -m unit

# Run only integration tests
poetry run pytest tests/ -m integration

# Skip slow tests
poetry run pytest tests/ -m "not slow"
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `curl: (7) Failed to connect` | Server not running | Start: `cd src && poetry run uvicorn codemie.rest_api.main:app --reload` |
| `401 Unauthorized` | Missing `user-id` header | Add `-H 'user-id: dev-codemie-user'` |
| `psql: command not found` | PostgreSQL client not installed | Use Docker exec (Option 1) |
| `docker exec: Error response` | Container not running | Start: `docker-compose up -d postgres` |
| `Connection refused (5432)` | PostgreSQL not exposed | Check docker-compose.yml ports mapping |
| `Database "postgres" does not exist` | Wrong database name | Use `postgres` (not `codemie`) |
| `FATAL: password authentication failed` | Wrong credentials | Use password: `password` |
| Import errors in Python | Virtualenv not activated | Run `source .venv/bin/activate` |

### Diagnostic Commands

```bash
# Check server running
curl -s http://localhost:8080/health

# Check database connection
docker exec -it postgres pg_isready -U postgres

# Check Docker services
docker-compose ps

# Check database size
docker exec -it postgres psql -U postgres -c "
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'codemie'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10
"

# Check active connections
docker exec -it postgres psql -U postgres -c "
SELECT count(*) as active_connections
FROM pg_stat_activity
WHERE datname = 'postgres'
"
```

---

## Quick Reference

### API Testing Checklist
- [ ] Server running on port 8080
- [ ] `user-id: dev-codemie-user` header included
- [ ] Endpoint path correct (starts with `/v1/`)
- [ ] Response status code 200
- [ ] Response format valid JSON

### Database Query Checklist
- [ ] Using READ-only queries (SELECT)
- [ ] Testing on non-production data
- [ ] Docker container `postgres` running
- [ ] Connection credentials: postgres/password
- [ ] Query includes WHERE clauses to limit data

### Test Execution Checklist
- [ ] User explicitly requested tests
- [ ] Virtual environment activated
- [ ] All dependencies installed (`poetry install`)
- [ ] Database migrations up to date
- [ ] No other tests running concurrently

---

## Related Guides

**Setup**:
- [setup-guide.md](./setup-guide.md) - Initial setup and commands

**Testing**:
- [testing-patterns.md](../testing/testing-patterns.md) - pytest patterns
- [testing-api-patterns.md](../testing/testing-api-patterns.md) - API testing

**Development**:
- [configuration-patterns.md](./configuration-patterns.md) - Environment config
- [logging-patterns.md](./logging-patterns.md) - Debugging logs

**Database**:
- [database-patterns.md](../data/database-patterns.md) - Query patterns
- [repository-patterns.md](../data/repository-patterns.md) - Data access

---
