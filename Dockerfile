# Argument are replaced in Github workflows
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

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .


# -----------------------------------------------------------------------------
# Runtime
FROM ${RUNTIME_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY *.md /app
COPY pyproject.toml /app
COPY src /app/src
COPY templates /app/templates

CMD ["bkup", "export"]
