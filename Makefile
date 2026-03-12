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

build:
	poetry build

test:
	poetry run pytest tests/

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

verify: ruff license test

coverage:
	poetry run coverage run -m pytest tests/ -W ignore::DeprecationWarning --cov --cov-report=html

import-katas:
	@echo "Importing AI Katas from GitHub..."
	poetry run import-katas

run:
	poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload