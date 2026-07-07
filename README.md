# ClusterSnap - Cloud-Agnostic Kubernetes Backup Tool

> ClusterSnap runs as a Kubernetes CronJob, zips PVC data from a shared volume,
> uploads the archive to any cloud storage backend (AWS S3, MinIO, SeaweedFS, or
> Azure Blob Storage), and pushes structured telemetry to Grafana Loki and
> Prometheus - all from a single, provider-agnostic codebase.


- 🗜️ **Single ZIP archive** per backup run using Python's built-in `zipfile`
- ☁️ **Provider-agnostic** storage layer - swap S3 for Azure with one env var
- 📋 **JSON-structured logs** parsed natively by Grafana Loki
- 🛡️ **ZIP path-traversal protection** during restore
- 🔒 **Dual Azure auth** - shared-key or service-principal
- 📊 **Prometheus metrics** pushed to Pushgateway after every run


## Installation
### Install from sources
* create venv
  ```
  python -m .venv
  source .venv/bin/activate
  ```

* install dependencies
  ```
  pip install --upgrade pip
  pip install -r requirements.txt
  ```

* create `.env` file
  * AWS/S3
    ```
    # Core
    MODE=export
    NAMESPACE=tenant-sphinx
    PROVIDER=s3
    BUCKET=velero
    BACKUP_DIR=/tmp/clustersnap
    REMOTE_PREFIX=clustersnap/backups
    LOG_LEVEL=INFO

    # AWS S3 / MinIO / SeaweedFS
    AWS_ACCESS_KEY_ID=minioadmin
    AWS_SECRET_ACCESS_KEY="minio&é&é&"
    AWS_REGION=us-east-1
    # Remove or leave blank for real AWS S3; set to MinIO / SeaweedFS address otherwise:
    S3_ENDPOINT_URL=http://localhost:9000  minio-api.local

    # Observability
    PUSHGATEWAY_URL=http://localhost:9091  
    ```

  * Azure Blob Storage (shared-key)
    ```
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

  * Azure Blob Storage (service-principal)
    ```
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

## Install locally with Docker
* build image
  ```
  docker build . --tag clustersnap:local
  ```

* run image
  ```
  docker run clustersnap:local
  ```

## Install on Kubernetes (pod)
* create secret to pull image
  ```
  kubectl create secret docker-registry registry-auth-clustersnap \
    --docker-server=ghcr.io \
    --docker-username=GITHUB_ACCOUNT \
    --docker-password=GITHUB_PAT \
    --docker-email=GITHUB_ACCOUNT
  ```
* create pod
  ```
  kubectl apply -f tests/pod.yaml -n NAMESPACE
  ```


# Usage

tofill


## Developpers

### Architecture

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

### Workflow of export
```
CronJob trigger
     │
     ▼
main.py --mode export
     │
     ├─► config.py          - validate all env vars (fail fast)
     ├─► storage/<provider> - health check (HeadBucket / GetContainerProperties)
     ├─► exporter.py        - zip /data/src-disks → /tmp/clustersnap/<ns>/<ts>.zip
     ├─► storage.upload()   - stream ZIP to bucket
     ├─► metrics.py         - record success / duration / size
     └─► metrics.push()     - push to Prometheus Pushgateway
```

### Workflow of import
tofill

## License
This project is licensed under the MIT License. See the [LICENSE file](https://raw.githubusercontent.com/Cosmo-Tech/clustersnap/refs/heads/main/LICENSE.md) for details.
