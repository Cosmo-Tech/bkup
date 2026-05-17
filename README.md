# ClusterSnap — Cloud-Agnostic Kubernetes Backup Tool

> **A production-grade Python replacement for legacy Bash backup scripts.**
> ClusterSnap runs as a Kubernetes CronJob, zips PVC data from a shared volume,
> uploads the archive to any cloud storage backend (AWS S3, MinIO, SeaweedFS, or
> Azure Blob Storage), and pushes structured telemetry to Grafana Loki and
> Prometheus — all from a single, provider-agnostic codebase.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Local Development & Installation](#4-local-development--installation)
5. [Local Testing Guide](#5-local-testing-guide)
6. [Containerization & Docker Testing](#6-containerization--docker-testing)
7. [Kubernetes & Helm Deployment](#7-kubernetes--helm-deployment)
8. [Observability Reference](#8-observability-reference)
9. [Environment Variable Reference](#9-environment-variable-reference)

---

## 1. Project Overview

ClusterSnap replaces a suite of ad-hoc Bash scripts (`export.sh`, `read.sh`,
`migrate_secrets-postgresql.sh`) with a resilient, observable, and
**multi-cloud** Python tool.

| Dimension | Legacy Bash | ClusterSnap |
|-----------|-------------|-------------|
| Language | Shell scripts | Python 3.11+ (fully typed) |
| Cloud support | Single-target | S3 · MinIO · SeaweedFS · Azure Blob |
| Observability | `echo` statements | Structured JSON logs (Loki) + Prometheus metrics |
| Error handling | `exit 1` on first failure | Explicit `try/except` with cleanup in `finally` |
| Configuration | Hard-coded variables | Pydantic-validated environment variables |
| Packaging | Raw scripts on node | Docker image deployed via Helm CronJob |
| Security | Plain-text secrets in scripts | Kubernetes `Secret` → env vars; supports SP auth |

### Key Features

- 🗜️ **Single ZIP archive** per backup run using Python's built-in `zipfile`
- ☁️ **Provider-agnostic** storage layer — swap S3 for Azure with one env var
- 📊 **Prometheus metrics** pushed to Pushgateway after every run
- 📋 **JSON-structured logs** parsed natively by Grafana Loki
- 🛡️ **ZIP path-traversal protection** during restore
- 🔒 **Dual Azure auth** — shared-key or service-principal

---

## 2. Architecture

```
clustersnap/
├── src/
│   ├── main.py               # CLI entrypoint (--mode export | import)
│   ├── config.py             # Pydantic v2 environment validation
│   ├── storage/
│   │   ├── base.py           # Abstract BaseStorageProvider
│   │   ├── s3.py             # AWS S3 / MinIO / SeaweedFS
│   │   └── azure.py          # Azure Blob Storage (shared-key + SP)
│   ├── backup/
│   │   ├── exporter.py       # Zip → Upload → Cleanup
│   │   └── importer.py       # Download → Validate → Extract
│   └── telemetry/
│       ├── logger.py         # JSON formatter for Grafana Loki
│       └── metrics.py        # Prometheus counters, histograms, gauges
├── deploy/
│   └── clustersnap/          # Helm chart
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── configmap.yaml
│           ├── secret.yaml
│           └── cronjob.yaml
├── Dockerfile
├── requirements.txt
└── README.md
```

**Runtime flow (export)**

```
CronJob trigger
     │
     ▼
main.py --mode export
     │
     ├─► config.py          — validate all env vars (fail fast)
     ├─► storage/<provider> — health check (HeadBucket / GetContainerProperties)
     ├─► exporter.py        — zip /data/src-disks → /tmp/clustersnap/<ns>/<ts>.zip
     ├─► storage.upload()   — stream ZIP to bucket
     ├─► metrics.py         — record success / duration / size
     └─► metrics.push()     — push to Prometheus Pushgateway
```

---

## 3. Prerequisites

Install the following tools before proceeding:

| Tool | Minimum version | Purpose |
|------|----------------|---------|
| **Python** | 3.11 | Runtime |
| **pip** | 23.0 | Package installation |
| **Docker** | 24.0 | Build & run the container image |
| **kubectl** | 1.28 | Interact with Kubernetes clusters |
| **Helm** | 3.14 | Package and deploy the Helm chart |
| **MinIO Client (`mc`)** | `RELEASE.2024` | Local S3-compatible object storage testing |
| **Azure CLI (`az`)** | 2.59 | Azure credential setup (optional) |

> **MinIO quick-start (Docker)**
> ```bash
> docker run -d --name minio \
>   -p 9000:9000 -p 9001:9001 \
>   -e MINIO_ROOT_USER=minioadmin \
>   -e MINIO_ROOT_PASSWORD=minioadmin \
>   quay.io/minio/minio server /data --console-address ":9001"
> ```
>
> **Azurite quick-start (Docker — Azure local emulator)**
> ```bash
> docker run -d --name azurite \
>   -p 10000:10000 \
>   mcr.microsoft.com/azure-storage/azurite \
>   azurite-blob --blobHost 0.0.0.0
> ```

---

## 4. Local Development & Installation

### Step 1 — Create and activate a virtual environment

```bash
cd clustersnap/

python3 -m venv .venv
source .venv/bin/activate
```

Confirm the correct interpreter is active:

```bash
python --version
# Python 3.11.x
which python
# /path/to/clustersnap/.venv/bin/python
```

### Step 2 — Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Key packages installed:

| Package | Version constraint | Role |
|---------|--------------------|------|
| `pydantic` | `>=2.13,<3.0` | Environment variable validation |
| `boto3` | `>=1.43,<2.0` | AWS S3 / MinIO / SeaweedFS client |
| `azure-storage-blob` | `>=12.28,<13.0` | Azure Blob Storage client |
| `azure-identity` | `>=1.25,<2.0` | Azure service-principal auth |
| `prometheus-client` | `>=0.24,<1.0` | Metrics collection + Pushgateway push |

### Step 3 — Configure environment variables

Create a `.env` file at `clustersnap/` (**never commit this file**):

```bash
cp .env.example .env   # if the example file exists, otherwise create it:
touch .env
```

#### `.env` template — AWS S3 / MinIO

```dotenv
# Core
MODE=export
NAMESPACE=tenant-alpha
PROVIDER=s3
BUCKET=clustersnap-backups
BACKUP_DIR=/tmp/clustersnap
REMOTE_PREFIX=clustersnap/backups
LOG_LEVEL=INFO

# AWS S3 / MinIO / SeaweedFS
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_REGION=us-east-1
# Remove or leave blank for real AWS S3; set to MinIO / SeaweedFS address otherwise:
S3_ENDPOINT_URL=http://localhost:9000

# Observability
PUSHGATEWAY_URL=http://localhost:9091
```

#### `.env` template — Azure Blob Storage (shared-key)

```dotenv
# Core
MODE=export
NAMESPACE=tenant-alpha
PROVIDER=azure
BUCKET=clustersnap-backups
BACKUP_DIR=/tmp/clustersnap
REMOTE_PREFIX=clustersnap/backups
LOG_LEVEL=INFO

# Azure Blob (shared-key)
AZURE_STORAGE_ACCOUNT=mystorageaccount
AZURE_STORAGE_KEY=<your-base64-account-key>

# Observability
PUSHGATEWAY_URL=http://localhost:9091
```

#### `.env` template — Azure Blob Storage (service-principal)

```dotenv
# Core
MODE=export
NAMESPACE=tenant-alpha
PROVIDER=azure
BUCKET=clustersnap-backups
BACKUP_DIR=/tmp/clustersnap
REMOTE_PREFIX=clustersnap/backups
LOG_LEVEL=INFO

# Azure Blob (service-principal)
AZURE_STORAGE_ACCOUNT=mystorageaccount
AZURE_CLIENT_ID=<app-registration-client-id>
AZURE_CLIENT_SECRET=<app-registration-client-secret>
AZURE_TENANT_ID=<azure-tenant-id>

# Observability
PUSHGATEWAY_URL=http://localhost:9091
```

> **Tip:** Add `.env` to your `.gitignore`:
> ```bash
> echo ".env" >> .gitignore
> ```

---

## 5. Local Testing Guide

All commands below assume you are inside `clustersnap/` with the virtual
environment activated and a `.env` file populated as shown in §4.

### 5.1 — Prepare test data

Create a local directory that simulates the Kubernetes volume mount:

```bash
mkdir -p /tmp/test-src-disks/pvc-seaweedfs
mkdir -p /tmp/test-src-disks/pvc-argo
mkdir -p /tmp/test-src-disks/pvc-cosmotechapi

# Populate with dummy files
echo "seaweedfs data" > /tmp/test-src-disks/pvc-seaweedfs/data.db
echo "argo workflow data" > /tmp/test-src-disks/pvc-argo/argo.db
echo "cosmotech api data" > /tmp/test-src-disks/pvc-cosmotechapi/api.db
```

### 5.2 — Create the MinIO bucket

```bash
# Configure the MinIO client alias
mc alias set local http://localhost:9000 minioadmin minioadmin

# Create the target bucket
mc mb local/clustersnap-backups

# Confirm the bucket exists
mc ls local/
```

### 5.3 — Test Export (Backup)

```bash
export $(cat .env | xargs) && \
  BACKUP_DIR=/tmp/clustersnap \
  python src/main.py --mode export
```

To override the source volume directory (pointing to your test data instead of
the default `/data/src-disks`), set the env var before running:

```bash
export $(cat .env | xargs) && \
  BACKUP_DIR=/tmp/clustersnap \
  python - <<'EOF'
import sys
sys.path.insert(0, "src")
from pathlib import Path
from config import load_config
from storage.s3 import S3StorageProvider
from backup.exporter import Exporter

cfg = load_config()
storage = S3StorageProvider(
    bucket=cfg.bucket,
    access_key_id=cfg.s3.access_key_id,
    secret_access_key=cfg.s3.secret_access_key,
    region=cfg.s3.region,
    endpoint_url=cfg.s3.endpoint_url,
)
Exporter(cfg, storage, source_root=Path("/tmp/test-src-disks")).run()
EOF
```

**Expected JSON log output:**

```json
{"timestamp": "2026-05-17T12:00:00.000000Z", "level": "INFO", "logger": "clustersnap.backup.exporter", "message": "Export workflow started", "extra": {"namespace": "tenant-alpha", "source_root": "/tmp/test-src-disks"}}
{"timestamp": "2026-05-17T12:00:00.120000Z", "level": "INFO", "logger": "clustersnap.backup.exporter", "message": "Zip archive creation started", "extra": {"source_root": "/tmp/test-src-disks", "archive": "/tmp/clustersnap/tenant-alpha/tenant-alpha-2026-05-17T12-00-00Z.zip"}}
{"timestamp": "2026-05-17T12:00:00.250000Z", "level": "INFO", "logger": "clustersnap.storage.s3", "message": "S3 upload succeeded", "extra": {"remote_path": "clustersnap/backups/tenant-alpha/tenant-alpha-2026-05-17T12-00-00Z.zip", "bucket": "clustersnap-backups", "size_bytes": 1048576}}
{"timestamp": "2026-05-17T12:00:00.310000Z", "level": "INFO", "logger": "clustersnap.backup.exporter", "message": "Export workflow completed successfully", "extra": {"namespace": "tenant-alpha", "duration_seconds": 0.312}}
```

**Verify the upload on MinIO:**

```bash
mc ls local/clustersnap-backups/clustersnap/backups/tenant-alpha/
```

### 5.4 — Test Import (Restore)

Set the mode to `import` and pass the remote key of the archive to restore:

```bash
export $(cat .env | xargs) && \
  MODE=import \
  python src/main.py \
    --mode import \
    --remote-key "clustersnap/backups/tenant-alpha/tenant-alpha-2026-05-17T12-00-00Z.zip"
```

> Replace the `--remote-key` value with the actual key listed by `mc ls` in the
> previous step.

**Verify the restored files:**

```bash
ls -lhR /data/src-disks/
# or, if testing locally:
ls -lhR /tmp/clustersnap/tenant-alpha/
```

### 5.5 — Verify Telemetry

#### JSON logs

ClusterSnap writes all logs to **stdout** as single-line JSON. Pipe through `jq`
for a readable local view:

```bash
export $(cat .env | xargs) && \
  python src/main.py --mode export 2>&1 | jq '.'
```

Filter for errors only:

```bash
export $(cat .env | xargs) && \
  python src/main.py --mode export 2>&1 | jq 'select(.level == "ERROR")'
```

#### Prometheus metrics

Start a local Pushgateway container:

```bash
docker run -d --name pushgateway \
  -p 9091:9091 \
  prom/pushgateway:latest
```

Run an export — metrics are pushed automatically at the end of the workflow.
Then query the Pushgateway API to confirm receipt:

```bash
curl -s http://localhost:9091/metrics | grep backup_
```

**Expected output:**

```
# HELP backup_success_total Total number of backup runs that completed successfully.
# TYPE backup_success_total counter
backup_success_total{job="clustersnap",namespace="tenant-alpha"} 1.0

# HELP backup_duration_seconds End-to-end wall-clock duration of the full backup workflow.
# TYPE backup_duration_seconds histogram
backup_duration_seconds_bucket{job="clustersnap",namespace="tenant-alpha",le="1.0"} 1.0
...

# HELP backup_size_bytes Size in bytes of the ZIP archive produced during the last successful backup run.
# TYPE backup_size_bytes gauge
backup_size_bytes{job="clustersnap",namespace="tenant-alpha"} 1048576.0

# HELP last_backup_timestamp Unix epoch timestamp of the last successful backup.
# TYPE last_backup_timestamp gauge
last_backup_timestamp{job="clustersnap",namespace="tenant-alpha"} 1.747483200e+09
```

You can also browse the Pushgateway web UI at **http://localhost:9091**.

---

## 6. Containerization & Docker Testing

### 6.1 — Build the Docker image

From the `clustersnap/` directory (where `Dockerfile` lives):

```bash
docker build -t clustersnap:local .
```

Verify the image was created:

```bash
docker images clustersnap
```

### 6.2 — Run the container locally (export mode)

Mount the local test-data directory to simulate the Kubernetes volume mount and
pass credentials via `--env-file`:

```bash
docker run --rm \
  --env-file .env \
  --network host \
  -v /tmp/test-src-disks:/data/src-disks:ro \
  -v /tmp/clustersnap:/tmp/clustersnap \
  clustersnap:local \
  python src/main.py --mode export
```

| Flag | Purpose |
|------|---------|
| `--env-file .env` | Injects all credentials and config without baking them into the image |
| `--network host` | Allows the container to reach `localhost:9000` (MinIO) and `localhost:9091` (Pushgateway) |
| `-v /tmp/test-src-disks:/data/src-disks:ro` | Simulates a PVC mounted read-only inside the pod |
| `-v /tmp/clustersnap:/tmp/clustersnap` | Persists the staging ZIP on the host for inspection |

### 6.3 — Run the container locally (import mode)

```bash
docker run --rm \
  --env-file .env \
  --network host \
  -v /tmp/restore-target:/data/src-disks \
  clustersnap:local \
  python src/main.py --mode import \
    --remote-key "clustersnap/backups/tenant-alpha/tenant-alpha-2026-05-17T12-00-00Z.zip"
```

### 6.4 — Inspect logs from a running container

```bash
docker logs -f <container-id> | jq '.'
```

---

## 7. Kubernetes & Helm Deployment

### 7.1 — Lint the Helm chart

```bash
helm lint deploy/clustersnap/
```

A passing lint produces:

```
==> Linting deploy/clustersnap/
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```

### 7.2 — Render templates locally (dry-run)

#### With AWS / MinIO credentials

```bash
helm template clustersnap deploy/clustersnap/ \
  --set config.namespace=tenant-alpha \
  --set config.provider=s3 \
  --set config.bucket=clustersnap-backups \
  --set credentials.s3.accessKeyId=minioadmin \
  --set credentials.s3.secretAccessKey=minioadmin \
  --set credentials.s3.endpointUrl=http://minio:9000 \
  --set cronjob.schedule="0 2 * * *" \
  | kubectl apply --dry-run=client -f -
```

#### With Azure (service-principal) credentials

```bash
helm template clustersnap deploy/clustersnap/ \
  --set config.namespace=tenant-alpha \
  --set config.provider=azure \
  --set config.bucket=clustersnap-backups \
  --set credentials.azure.storageAccount=mystorageaccount \
  --set credentials.azure.clientId=<client-id> \
  --set credentials.azure.clientSecret=<client-secret> \
  --set credentials.azure.tenantId=<tenant-id> \
  --set cronjob.schedule="0 2 * * *" \
  | kubectl apply --dry-run=client -f -
```

### 7.3 — Install the chart

Create a `my-values.yaml` (never commit real secrets — use `kubectl create secret`
or an external secrets operator in production):

```yaml
# my-values.yaml
config:
  namespace: tenant-alpha
  provider: s3
  bucket: clustersnap-backups
  backupDir: /tmp/clustersnap
  remotePrefix: clustersnap/backups
  logLevel: INFO
  pushgatewayUrl: http://prometheus-pushgateway:9091

cronjob:
  schedule: "0 2 * * *"   # every day at 02:00 UTC
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1

credentials:
  s3:
    accessKeyId: minioadmin
    secretAccessKey: minioadmin
    region: us-east-1
    endpointUrl: http://minio:9000

image:
  repository: clustersnap
  tag: latest
  pullPolicy: IfNotPresent

volumeMount:
  mountPath: /data/src-disks
  claimName: my-pvc
```

```bash
helm upgrade --install clustersnap deploy/clustersnap/ \
  --namespace tenant-alpha \
  --create-namespace \
  -f my-values.yaml
```

### 7.4 — Verify the CronJob was deployed

```bash
kubectl -n tenant-alpha get cronjob
kubectl -n tenant-alpha describe cronjob clustersnap
```

### 7.5 — Trigger the CronJob manually for testing

```bash
kubectl -n tenant-alpha create job clustersnap-manual-test \
  --from=cronjob/clustersnap
```

Monitor the job in real time:

```bash
# Watch the pod come up
kubectl -n tenant-alpha get pods -w -l job-name=clustersnap-manual-test

# Stream structured JSON logs (pipe through jq for readability)
kubectl -n tenant-alpha logs -f \
  -l job-name=clustersnap-manual-test \
  --all-containers | jq '.'
```

Check the final job status:

```bash
kubectl -n tenant-alpha get job clustersnap-manual-test
```

Expected output when successful:

```
NAME                       COMPLETIONS   DURATION   AGE
clustersnap-manual-test    1/1           18s        2m
```

### 7.6 — Uninstall

```bash
helm uninstall clustersnap --namespace tenant-alpha
```

---

## 8. Observability Reference

### Prometheus metrics

| Metric name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `backup_success_total` | Counter | `namespace` | Incremented once per successful backup run |
| `backup_failure_total` | Counter | `namespace` | Incremented once per failed backup run |
| `backup_duration_seconds` | Histogram | `namespace` | End-to-end wall-clock time (zip + upload + cleanup) |
| `backup_size_bytes` | Gauge | `namespace` | Compressed ZIP archive size in bytes |
| `last_backup_timestamp` | Gauge | `namespace` | Unix epoch of the last successful backup |

**Recommended Prometheus alert rules:**

```yaml
# Alert if no successful backup in the last 25 hours
- alert: ClusterSnapBackupStale
  expr: time() - last_backup_timestamp{job="clustersnap"} > 90000
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "No successful backup in >25h for namespace {{ $labels.namespace }}"

# Alert on any backup failure
- alert: ClusterSnapBackupFailed
  expr: increase(backup_failure_total{job="clustersnap"}[1h]) > 0
  labels:
    severity: critical
  annotations:
    summary: "Backup failure detected for namespace {{ $labels.namespace }}"
```

### Grafana Loki log queries

```logql
# All ClusterSnap logs for a namespace
{job="clustersnap"} | json | namespace="tenant-alpha"

# Errors only
{job="clustersnap"} | json | level="ERROR"

# Upload events with size
{job="clustersnap"} | json | message=~"upload.*"
```

---

## 9. Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MODE` | ✅ | — | `export` or `import` |
| `NAMESPACE` | ✅ | — | Kubernetes namespace being backed up |
| `PROVIDER` | ✅ | — | `s3` or `azure` |
| `BUCKET` | ✅ | — | Bucket (S3) or container (Azure) name |
| `BACKUP_DIR` | ❌ | `/tmp/clustersnap` | Local staging directory for ZIP files |
| `REMOTE_PREFIX` | ❌ | `clustersnap/backups` | Key prefix inside the bucket |
| `LOG_LEVEL` | ❌ | `INFO` | `DEBUG` · `INFO` · `WARNING` · `ERROR` |
| `PUSHGATEWAY_URL` | ❌ | `http://prometheus-pushgateway:9091` | Prometheus Pushgateway endpoint |
| `KUBE_CONTEXT` | ❌ | `<current-context>` | kubectl context name. Omit when running in-cluster (CronJob) |
| `AWS_ACCESS_KEY_ID` | S3 only ✅ | — | AWS / MinIO / SeaweedFS access key |
| `AWS_SECRET_ACCESS_KEY` | S3 only ✅ | — | AWS / MinIO / SeaweedFS secret key |
| `AWS_REGION` | S3 only ❌ | `us-east-1` | AWS region (ignored by MinIO/SeaweedFS) |
| `S3_ENDPOINT_URL` | S3 only ❌ | — | Override endpoint for MinIO / SeaweedFS |
| `AZURE_STORAGE_ACCOUNT` | Azure only ✅ | — | Azure storage account name |
| `AZURE_STORAGE_KEY` | Azure shared-key ✅ | — | Account shared key (mutually exclusive with SP) |
| `AZURE_CLIENT_ID` | Azure SP ✅ | — | Service-principal application ID |
| `AZURE_CLIENT_SECRET` | Azure SP ✅ | — | Service-principal client secret |
| `AZURE_TENANT_ID` | Azure SP ✅ | — | Azure Active Directory tenant ID |

---

<div align="center">

**ClusterSnap** — [ClusterSnap](https://github.com/bmadcode) ·
Python3 · Kubernetes · Helm · Prometheus · Grafana

</div>
