# syntax=docker/dockerfile:1
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
#
# Main build context is the codemie (backend) repository root. The frontend is
# supplied as the named build context "frontend"
# (docker/podman build --build-context frontend=<path-to-codemie-ui> ...).

# Same tag as the backend Dockerfile (PYTHON_VERSION), so both images get the same base-image fixes.
ARG PYTHON_VERSION=3.12.12

# ─── Stage 1: Build the React frontend ───────────────────────────────────────
# Same Node/Alpine line as codemie-ui/multistage.Dockerfile.
FROM node:20-alpine3.23 AS frontend-builder

WORKDIR /app/frontend

# Only the files genuinely required by `npm run build` (tsc --noEmit && vite
# build) are ever copied from the "frontend" context — never a blanket copy.
# This is deliberate: the codemie-ui repo's own .dockerignore excludes
# node_modules/dist_keycloak/.env but not .git or other local files, and that
# repo must not be modified from here to compensate. Excluded on purpose:
# test/lint/format config (vitest*, .eslintrc.cjs, .prettierrc.json) and
# Keycloak-theme-only assets (.keycloakify/, index-keycloak.html) — none are
# read by the production `build` script.
COPY --from=frontend package.json package-lock.json ./
RUN npm ci

COPY --from=frontend index.html vite.config.ts tsconfig.json tsconfig.node.json \
     postcss.config.js tailwind.config.ts ./
COPY --from=frontend src    ./src
# public/ is copied by Vite into dist/ as-is; site.webmanifest icons resolve
# to /assets/... at runtime, so it is required for feature parity, not just build success.
COPY --from=frontend public ./public
# These build-time defaults replace the frontend's own .env, which is never
# copied into the build context or image. VITE_SUFFIX must be empty so
# index.html's "%VITE_SUFFIX%/config.js" placeholder resolves to "/config.js",
# matching where the entrypoint actually writes the runtime config file.
ENV VITE_SUFFIX='' \
    VITE_ONBOARDING_ASSISTANT_SLUG=codemie-onboarding \
    VITE_FEEDBACK_ASSISTANT_SLUG=codemie-feedback \
    VITE_CHATBOT_ASSISTANT_SLUG=ai-run-chatbot \
    VITE_PROMPT_ENGINEER_SLUG=prompt-engineer
RUN npm run build
# Fail fast if any %VITE_*% placeholder (e.g. %VITE_SUFFIX%) was left
# unresolved in the built HTML — that breaks config.js loading at runtime.
RUN if grep -Eq '%VITE_[A-Z_]+%' dist/index.html; then \
        echo "ERROR: unresolved %VITE_*% placeholder in dist/index.html:" >&2; \
        grep -Eo '%VITE_[A-Z_]+%' dist/index.html >&2; \
        exit 1; \
    fi

# ─── Stage 2: Install Python production dependencies ─────────────────────────
# The shared builder image already has Poetry, a compiler and the venv at /venv
# (POETRY_VIRTUALENVS_CREATE=false), so dependencies install straight into it.
FROM codemie/codemie-base-python:${PYTHON_VERSION}-debian-builder AS backend-builder

WORKDIR /app

COPY pyproject.toml poetry.lock ./

# --no-root: skip installing the codemie package itself (source-layout).
# The source is copied into the final stage and PYTHONPATH fills the gap.
RUN poetry install --without dev --no-root

# ─── Stage 3: Final runtime image ────────────────────────────────────────────
FROM codemie/codemie-base-python:${PYTHON_VERSION}-debian-runtime AS final

# The base image runs as the unprivileged "codemie" user; packages are installed as root and the
# user is restored below. curl (HEALTHCHECK, entrypoint probe) and git (GitPython loaders) are
# already in the base image. nginx runs as codemie too: the "user" directive is dropped, and the
# pid file and the log/temp directories are moved to places that user can write.
USER root
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i -e '/^user /d' -e 's|^pid .*|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf \
    && chown -R codemie:codemie /var/log/nginx /var/lib/nginx /usr/share/nginx/html

# Frontend static assets (the html directory was chowned above: the entrypoint writes config.js there)
COPY --from=frontend-builder --chown=codemie:codemie /app/frontend/dist /usr/share/nginx/html

# OSS nginx config (adds /api/ proxy block; does not modify frontend/nginx.conf)
COPY standalone/docker/nginx.conf /etc/nginx/sites-enabled/default

# Python venv with all production dependencies
COPY --from=backend-builder --chown=codemie:codemie /venv /venv

# Backend application source and config directory.
# config.py resolves paths relative to Path(__file__).parents[3] == /app/backend/,
# so both src/ and config/ must be present under /app/backend/.
COPY --chown=codemie:codemie src    /app/backend/src
COPY --chown=codemie:codemie config /app/backend/config

# Pristine copy of the bundled customer config, kept alongside the live one. The entrypoint
# restores customer-config.yaml from this pristine image-bundled copy on every start before optionally
# re-applying an external override, so removing the external file and restarting the same
# (not recreated) container correctly falls back to the bundled config instead of leaving a
# stale external copy in place.
COPY --chown=codemie:codemie config/customer/customer-config.yaml /app/backend/config/customer/.customer-config.bundled.yaml

# Entrypoint: generates config.js, validates env, starts uvicorn, execs nginx.
COPY --chmod=755 standalone/docker/entrypoint.sh /entrypoint.sh

# PYTHONPATH makes the source-layout codemie package importable from the venv.
# backend/pyproject.toml: packages = [{ include = "codemie", from = "src" }]
ENV PYTHONPATH=/app/backend/src

# Pre-create the writable data directories so new named volumes inherit codemie ownership.
USER codemie
RUN mkdir -p /app/codemie-storage /app/codemie-repos

EXPOSE 8080

# Probes the full nginx→uvicorn chain, not just the nginx stub /healthcheck.
# start-period=90s allows time for Alembic migrations to complete on first run.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://localhost:8080/api/v1/healthcheck || exit 1

ENTRYPOINT ["/entrypoint.sh"]
