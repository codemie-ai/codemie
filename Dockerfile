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

ARG PYTHON_VERSION=3.12.12
ARG INSTALL_ENTERPRISE=true

FROM codemie/codemie-base-python:${PYTHON_VERSION}-debian-builder AS builder

# Set working directory
WORKDIR /app

# Copy dependency files
COPY ./pyproject.toml ./poetry.lock ./

ARG INSTALL_ENTERPRISE
RUN --mount=type=secret,id=google_credentials,dst=/kaniko/google_credentials.json \
    if [ "$INSTALL_ENTERPRISE" = "true" ]; then \
    echo "Installing with enterprise features..."; \
    export GOOGLE_APPLICATION_CREDENTIALS=/kaniko/google_credentials.json; \
    poetry self add keyrings.google-artifactregistry-auth; \
    poetry install --only main -E enterprise --no-root; \
    poetry self remove keyrings.google-artifactregistry-auth; \
    else \
    echo "Installing base dependencies only..."; \
    poetry install --only main --no-root; \
    fi; \
    chmod 555 "$VIRTUAL_ENV"

# Copy source code and configuration
COPY ./src /app/src
COPY ./config /app/config
COPY ./google_credentials_sample.json /app/credentials.json
COPY ./README.md /app/README.md
COPY ./pytest.ini /app

# Production stage
FROM codemie/codemie-base-python:${PYTHON_VERSION}-debian-runtime AS production

# Set working directory
WORKDIR /app

# Copy virtual environment from builder (chmod 555 applied in builder stage, before
# this COPY, so Kaniko preserves the read-only mode without a separate RUN/snapshot)
COPY --from=builder --chown=codemie:codemie $VIRTUAL_ENV $VIRTUAL_ENV

# Copy application code
COPY --from=builder --chown=codemie:codemie /app /app

# Pre-create writable directories so named volumes inherit codemie ownership on first mount
RUN mkdir -p /app/codemie-storage /app/codemie-repos

# Expose port
EXPOSE 8080

# Start the application with Poetry
CMD ["sh", "-c", "poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --root-path $SUFFIX"]

