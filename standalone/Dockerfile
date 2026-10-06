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

# ─── Stage 1: Build the React frontend ───────────────────────────────────────
FROM node:20-alpine AS frontend-builder

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

# VITE_IS_ENTERPRISE_EDITION must be false for the OSS build.
# It is baked into the JS bundle at build time and cannot be changed at runtime.
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
ENV VITE_IS_ENTERPRISE_EDITION=false \
    VITE_SUFFIX='' \
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
FROM python:3.12-slim AS backend-builder

WORKDIR /app/backend

# nemo-switchyard builds a native Rust/Python extension during Poetry install.
# Keep its compiler toolchain in the builder stage so it is not copied into the
# final runtime image.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# hadolint ignore=DL3013
RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock ./

# --no-root: skip installing the codemie package itself (source-layout).
# The source is copied into the final stage and PYTHONPATH fills the gap.
# virtualenvs.in-project produces /app/backend/.venv for easy COPY.
RUN poetry config virtualenvs.in-project true && \
    poetry install --without dev --no-root

# ─── Stage 3: Final runtime image ────────────────────────────────────────────
FROM python:3.12-slim AS final

# Install nginx (process manager + static server) and curl (healthcheck probe).
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Frontend static assets
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html

# OSS nginx config (adds /api/ proxy block; does not modify frontend/nginx.conf)
COPY standalone/docker/nginx.conf /etc/nginx/sites-enabled/default

# Python venv with all production dependencies
COPY --from=backend-builder /app/backend/.venv /app/backend/.venv

# Backend application source and config directory.
# config.py resolves paths relative to Path(__file__).parents[3] == /app/backend/,
# so both src/ and config/ must be present under /app/backend/.
COPY src    /app/backend/src
COPY config /app/backend/config

# Pristine copy of the bundled customer config, kept alongside the live one. The entrypoint
# restores customer-config.yaml from this pristine image-bundled copy on every start before optionally
# re-applying an external override, so removing the external file and restarting the same
# (not recreated) container correctly falls back to the bundled config instead of leaving a
# stale external copy in place.
COPY config/customer/customer-config.yaml /app/backend/config/customer/.customer-config.bundled.yaml

# Entrypoint: generates config.js, validates env, starts uvicorn, execs nginx.
COPY standalone/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# PYTHONPATH makes the source-layout codemie package importable from the venv.
# backend/pyproject.toml: packages = [{ include = "codemie", from = "src" }]
ENV PYTHONPATH=/app/backend/src

# Backend deployment defaults (all overridable at docker run time via -e).
ENV FRONTEND_URL=http://localhost:8080
ENV ENV=development
ENV IDP_PROVIDER=local
# Rate-limit for login endpoint — allow enough requests for CI/CD tooling
# and parallel test workers. Can be overridden at container run time.
ENV RATE_LIMIT_LOGIN=100/15minutes

EXPOSE 8080

# Probes the full nginx→uvicorn chain, not just the nginx stub /healthcheck.
# start-period=90s allows time for Alembic migrations to complete on first run.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://localhost:8080/api/v1/healthcheck || exit 1

ENTRYPOINT ["/entrypoint.sh"]
