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
    tofill
    ```

  * Azure Blob Storage (shared-key)
    ```
    tofill
    ```

* run
  ```
  python -m src.main
  ```

<!-- ## Install locally with Docker
* build
  ```
  docker build . --tag clustersnap:local
  ```

* run
  ```
  docker run clustersnap:local
  ``` -->

## Install on Kubernetes (pod)
* create secret to pull image
  ```
  kubectl create secret docker-registry registry-auth-clustersnap \
    --docker-server=ghcr.io \
    --docker-username=GITHUB_ACCOUNT \
    --docker-password=GITHUB_PAT \
    --docker-email=GITHUB_ACCOUNT
  ```
* create secret for remote storage access
  * edit `src/templates/backup.secret.yaml` with your own config
  * apply
    ```
    NAMESPACE=tenant-test0 envsubst < templates/backup.secret.yaml | kubectl apply -f -
    ```
* create pod
  ```
  NAMESPACE=tenant-test0 envsubst < tests/resources_test.yaml | kubectl apply -f -
  ```

## Usage
* local
```
python -m src.main --c CONTEXT -n NAMESPACE
```

* docker/kubernetes
just run the image


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
Clustersnap -> Look for existing Deployments/Statefuleset -> List attached PVC -> List volumes in each PVC
```

### Workflow of import
tofill

## License
This project is licensed under the MIT License. See the [LICENSE file](https://raw.githubusercontent.com/Cosmo-Tech/clustersnap/refs/heads/main/LICENSE.md) for details.
