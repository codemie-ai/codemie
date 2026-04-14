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

FROM python:${PYTHON_VERSION}-slim AS builder

# Set environment variables
ENV PYTHONPATH=/app/src \
    POETRY_VERSION=2.3.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV="/venv"

# Add Poetry and venv to PATH
ENV PATH="$POETRY_HOME/bin:$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl=8.14.1-2+deb13u2 \
    libimage-exiftool-perl=13.25+dfsg-1 \
    gcc=4:14.2.0-1 \
    freetds-dev=1.3.17+ds-2+deb13u1 \
    freetds-bin=1.3.17+ds-2+deb13u1 \
    unixodbc=2.3.12-2 \
    unixodbc-dev=2.3.12-2 && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv $VIRTUAL_ENV

# Install Poetry - respects $POETRY_VERSION & $POETRY_HOME
# hadolint ignore=DL4006
RUN curl -sSL https://install.python-poetry.org | python -
RUN poetry self add jaraco.context@6.1.0 wheel@0.46.2

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
    fi

# Copy source code and configuration
COPY ./src /app/src
COPY ./config /app/config
COPY ./google_credentials_sample.json /app/credentials.json
COPY ./README.md /app/README.md
COPY ./pytest.ini /app
COPY ./tests /app/tests

# Production stage
FROM python:${PYTHON_VERSION}-slim AS production

# Set working directory
WORKDIR /app

# Create codemie user
RUN groupadd --gid 1001 codemie && \
    useradd --uid 1001 --gid 1001 --shell /bin/bash --create-home codemie

RUN chown codemie:codemie /app

# Set environment variables for production
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV="/venv" \
    SUFFIX=/ \
    TIKTOKEN_CACHE_DIR="/home/codemie/.cache/tiktoken"

# Add Poetry and venv to PATH
ENV PATH="$POETRY_HOME/bin:$VIRTUAL_ENV/bin:$PATH"

# Install only runtime dependencies
# procps is needed for kill command in "Code Interpreter"
# texlive packages and pandoc are needed for PDF export (pdflatex + pypandoc)
# lmodern and texlive-fonts-recommended are required for Pandoc PDF generation
# hadolint ignore=DL3008
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl=8.14.1-2+deb13u2 \
    freetds-bin=1.3.17+ds-2+deb13u1 \
    unixodbc=2.3.12-2 \
    libpango1.0-dev=1.56.3-1 \
    git=1:2.47.3-0+deb13u1 \
    procps=2:4.0.4-9 \
    libsqlite3-0=3.46.1-7 \
    openssl=3.5.4-1~deb13u2 \
    pandoc \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    lmodern && \
    apt-get purge -y linux-libc-dev && \
    rm -rf /var/lib/apt/lists/*

RUN chmod 555 /bin && chmod 555 /sbin && chmod 555 /usr && chmod 555 /lib && chmod 555 /lib64

USER codemie

COPY --from=builder --chown=codemie:codemie $POETRY_HOME $POETRY_HOME

# Copy virtual environment from builder
COPY --from=builder --chown=codemie:codemie $VIRTUAL_ENV $VIRTUAL_ENV
RUN chmod 555 $VIRTUAL_ENV

# Copy application code
COPY --from=builder --chown=codemie:codemie /app /app

# Download cl100k_base and o200k_base encodings
# hadolint ignore=SC2046
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" && \
    curl -L https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken > \
    $TIKTOKEN_CACHE_DIR/$(python -c "import hashlib; print(hashlib.sha1('https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken'.encode()).hexdigest())") && \
    curl -L https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken > \
    $TIKTOKEN_CACHE_DIR/$(python -c "import hashlib; print(hashlib.sha1('https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'.encode()).hexdigest())")

# Download NLTK packages
RUN poetry run download_nltk_packages

# Expose port
EXPOSE 8080

# Start the application with Poetry
CMD ["sh", "-c", "poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --root-path $SUFFIX"]
