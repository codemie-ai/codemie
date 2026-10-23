# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

install:
	poetry install

install-enterprise:
	poetry install -E enterprise --sync

install-oss:
	poetry install --sync

install-hooks:
	poetry run pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push

build:
	poetry build

test:
	poetry run pytest tests/

# The live CLI Analytics SQL tests (test_live_*.py) skip without TEST_LOCAL_PG_URL; this runs them on throwaway
# PostgreSQL 17 and 14 containers on loopback, removed afterwards, never on the docker-compose database; a skipped
# test fails it.
LIVE_PG_PASSWORD ?= password
test-cli-analytics-live:
	@trap 'docker rm -f codemie-cli-analytics-live-pg17 codemie-cli-analytics-live-pg14 >/dev/null 2>&1' EXIT INT TERM; \
	for v in 17 14; do \
	  name=codemie-cli-analytics-live-pg$$v; port=$$((55400 + v)); \
	  docker rm -f $$name >/dev/null 2>&1; \
	  docker run -d --rm --name $$name -e POSTGRES_PASSWORD=$(LIVE_PG_PASSWORD) -p 127.0.0.1:$$port:5432 postgres:$$v >/dev/null || exit 1; \
	  for i in $$(seq 60); do docker exec $$name pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 1; done; \
	  docker exec $$name pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 || { echo "postgres:$$v not ready in 60 s" >&2; exit 1; }; \
	  out=$$(env -u CLI_ANALYTICS_PG_URL TEST_LOCAL_PG_URL=postgresql://postgres:$(LIVE_PG_PASSWORD)@127.0.0.1:$$port/postgres \
	    poetry run pytest -q tests/codemie/repository/cli_analytics/postgres -k live -rs 2>&1); rc=$$?; echo "$$out"; \
	  docker rm -f $$name >/dev/null; [ $$rc -eq 0 ] || exit $$rc; \
	  if echo "$$out" | grep -q skipped; then echo "live tests skipped: the database was not reached" >&2; exit 1; fi; \
	done

ruff:
	poetry run ruff format
	poetry run ruff check --fix
	poetry run ruff check

ruff-format:
	poetry run ruff format

ruff-fix:
	poetry run ruff check --fix

license:
	poetry run python scripts/license_headers/check_license_headers.py --fix $(FILE)
	poetry run python scripts/license_headers/check_license_headers.py --check $(FILE)

license-check:
	poetry run python scripts/license_headers/check_license_headers.py --check --quiet $(FILE)

license-fix:
	poetry run python scripts/license_headers/check_license_headers.py --fix $(FILE)

gitleaks:
	docker run --rm -v "./:/workspace" ghcr.io/gitleaks/gitleaks:v8.30.1 \
	    dir --no-banner --verbose --redact --config=/workspace/.gitleaks.toml /workspace

verify: ruff license gitleaks test

coverage:
	poetry run pytest tests/ -W ignore::DeprecationWarning --cov --cov-report=html

import-katas:
	@echo "Importing AI Katas from GitHub..."
	poetry run import-katas

sonar-local:
	@node scripts/sonar/run-local-sonar.js

run:
	poetry run uvicorn

test-harness:
	uvx codemie-test-harness --sanity-api
