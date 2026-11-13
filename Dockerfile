# -----------------------------------------------------------------------------
# Arguments are replaced in Github workflows
#  - BUILDER_IMAGE must have pip
#  - RUNTIME_IMAGE can be python only (without pip nor bash etc..)
ARG BUILDER_IMAGE=python:3.15-alpine
ARG RUNTIME_IMAGE=python:3.15-alpine


# -----------------------------------------------------------------------------
# Builder with pip
FROM ${BUILDER_IMAGE} AS builder

WORKDIR /app
COPY *.md /app
COPY pyproject.toml /app
COPY src /app/src
COPY templates /app/templates

RUN python -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /app/venv/bin/pip install --no-cache-dir .


# -----------------------------------------------------------------------------
# Runtime
FROM ${RUNTIME_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/venv/bin:$PATH"

WORKDIR /app
COPY --from=builder /app/venv /app/venv
COPY *.md /app
COPY pyproject.toml /app
COPY src /app/src
COPY templates /app/templates

CMD ["bkup", "export"]
