# ClusterSnap
**Cloud-Agnostic Kubernetes Backup Tool**

> Clustersnap is a lightweight, production-grade, cloud-agnostic backup tool that safely secures Kubernetes volumes into remote storage like Azure Blob Storage or AWS S3.


* **Export** Kubernetes data to remote storage.
* **Import** backed-up data to Kubernetes.
* **Backups Isolation:** Each Kubernetes namespace is backed up into a dedicated space.
* **Ensure data Integrity:** Scale-down running workload, backup their volumes and restore the workload.
* **Cloud-Native & Minimalist:** Light footprint, easy to install, and fully customizable from standard Kubernetes manifests without third-party CRD dependencies.


## Requirements
* Remote storage (Azure Blob Storage, S3 etc...), with credentials
* Working Kubernetes cluster


## Installation
* Install CLI
  * Create venv
    ```
    python -m .venv
    source .venv/bin/activate
    ```

  * Install dependencies
    ```
    pip install --upgrade pip
    pip install -r requirements.txt
    ```

* Create required Kubernetes objects
  * `pullImage` secret
    ```
    kubectl create secret docker-registry registry-auth-clustersnap \
      --docker-server=ghcr.io \
      --docker-username=GITHUB_ACCOUNT \
      --docker-password=GITHUB_PAT \
      --docker-email=GITHUB_ACCOUNT
    ```

  * `config` Secret
    * edit `src/templates/backup.secret.yaml` with your own config
    * apply
      ```
      NAMESPACE=YOUR_NAMESPACE envsubst < templates/backup.secret.yaml | kubectl apply -f -
      ```

  * `backup-map` ConfigMap
    > Note: this ConfigMap is used to orchestrate the backup. \
    > You can manually edit it to customize the backup behaviors, the command below is just a quick way to create it. \
    > You can also read it with `python -m src.master -c KUBECTL_CONTEXT -gm`
    ```
    python -m src.master -c KUBECTL_CONTEXT -rm
    ```
    

## Usage
> Ensure required secrets & configmap are created
### Kubernetes
* Create pod (& required resources such as RBAC etc...)
```
NAMESPACE=YOUR_NAMESPACE envsubst < tests/resources_test.yaml | kubectl apply -f -
```
* Wait for it to end...

### Local CLI
* Run cluster-wide backup
```
python -m src.master -c KUBECTL_CONTEXT
```

* Run namespaced backup
```
python -m src.master -c KUBECTL_CONTEXT -n NAMESPACE
```

* Display help
```
python -m src.master --help
```


## Developpers
### Architecture
* `master.py`   => entrypoint of the master pod (orchestrate backup jobs, scaling replicas etc...)
* `exporter.py` => entrypoint of the exporter pod (do the backup = dump persisted storage & sync it to remote storage)
* `importer.py` => entrypoint of the importer pod (import backups from remote storage to Kubernetes)


### Workflows
#### Export
tofill

#### Import
tofill

## License
This project is licensed under the MIT License. See the [LICENSE file](https://raw.githubusercontent.com/Cosmo-Tech/clustersnap/refs/heads/main/LICENSE.md) for details.
