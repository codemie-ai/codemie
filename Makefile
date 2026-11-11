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

# Fails when the Alembic migration history has more than one head (also against what origin/main brings in).
check-alembic:
	python scripts/check_alembic_heads.py --merge-with origin/main

# Fails when poetry.lock does not match pyproject.toml (Poetry's own `poetry check --lock`); fix with `poetry lock`.
check-deps:
	python scripts/check_poetry_lock.py

# Fails on new strict ruff issues (C901,B,SIM,PL,RET,ARG,PTH,UP,S) in the Python files changed since the merge base
# with origin/main; tests are skipped and issues the files already had are not counted.
ruff-changed:
	poetry run python scripts/check_ruff_changed.py --base origin/main

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
	poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload --reload-dir src

test-harness:
	uvx codemie-test-harness --sanity-api
