# ClusterSnap
**Cloud-Agnostic Kubernetes Backup Tool**

> Clustersnap is a lightweight, production-grade, cloud-agnostic backup tool that safely secures Kubernetes volumes into remote storage like Azure Blob Storage or AWS S3.


* :minidisc: **Export** Kubernetes data to remote storage.
* :recycle: **Import** backed-up data to Kubernetes.
* :package: **Backups Isolation:** Each Kubernetes namespace is backed up into a dedicated space.
* :closed_lock_with_key: **Ensure data Integrity:** Scale-down running workload, backup their volumes and restore the workload.
* :pinching_hand: **Cloud-Native & Minimalist:** Light footprint, easy to install, and fully customizable from standard Kubernetes manifests without third-party CRD dependencies.


## Requirements
* Remote storage (Azure Blob Storage, S3 etc...), with credentials
* Working Kubernetes cluster


## Installation
> [!CAUTION]
> Clusternap is based on **scaling down** Kubernetes **pods** to orchestrate a clean backup plan. \
> You should probably try it on a test environment first.

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

* Create Kubernetes objects
  * [optional] `pullImage` secret
    > To get the image from this repository
    ```
    kubectl create secret docker-registry clustersnap-registry-auth \
      --docker-server=ghcr.io \
      --docker-username=GITHUB_ACCOUNT \
      --docker-password=GITHUB_PAT \
      --docker-email=GITHUB_ACCOUNT
    ```

  * `config` Secret
    * Edit `src/templates/config.secret.yaml` with your own config
    * Apply
      ```
      NAMESPACE=YOUR_NAMESPACE envsubst < templates/backup.secret.yaml | kubectl apply -f -
      ```

  * `backup-map` ConfigMap
    > Note: this ConfigMap is used to orchestrate the backup. \
    > You can manually edit it to customize the backup behaviors, the command below is just a quick way to create it. \
    > If it already exists, you can read it with `python -m src.master -c KUBECTL_CONTEXT -gm` or `kubectl get cm clustersnap-backup-map`
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


### The `backup-map`
Clustersnap relies on a ConfigMap called the `backup-map` to orchestrate its backups plans.

The `backup-map` is really simple:
* consists of listing workloads (Deployments/StatefulSets) and their associated PVC if any.
* must be generated before running Clustersnap jobs.
* can be manually modified (and it's even recommanded to fit with your needs)
* stored in namespace of the `Clusternap master pod` (or default namespace if Clustersnap is not running in the cluster)


#### Example
```
clustersnap-backup-map.yaml: |
  cluster: example
  namespaces:
  - name: namespace1
    workloads: []
    claims: []
  - name: namespace2
    workloads: []
    claims: []
  - name: namespace3
    workloads:
    - resource: Deployment/workload1
      replicas: 1
    - resource: StatefulSet/workload2
      replicas: 9
    - resource: StatefulSet/postgresql
      replicas: 1
    claims:
    - name: pvc-workload1
      backup-type: volume
      attachment-node: node-example
      mounts:
      - container: example
        path: /data
        resource: Deployment/example1

    - name: pvc-workload2
      backup-type: volume
      attachment-node: node-example
      mounts:
      - container: example
        path: /data
        resource: StatefulSet/example2

    - name: pvc-postgresql
      backup-type: pg_dump
      attachment-node: node-example
      mounts:
      - container: postgresql
        path: /data
        resource: StatefulSet/postgresql
```


#### Tweaks
Everything in the backup-map can be tweaked, but here is a short description of the most useful keys

| key                       | value         | info
|---------------------------|---------------|------
| workloads[]               | *[]*          | Nothing to backup
| claim[]                   | *[]*          | Nothing to backup
| workloads[].replicas      | *integer*     | Clustersnap must scale replicas=0 to perform the backups. This value is used to restore the right replicas quantity
| claims[].attachment-node  | *node-name*   | Useful when data are stored on specifics nodes (and not on a remote storage), jobs must run on same nodes as PVC
| claims[].mounts[].path    | *path*        | Mount point where to look for data to backup
| claims[].backup-type      | **volume**    | The backup job will create a `.tar.gz containing data of the mounted paths` found in the PVC
| claims[].backup-type      | **pg_dump**   | The backup job will use `pg_dump to create a .sql` dump of all the databases found in the PVC


### Workflows
#### Volumes
##### Export
tofill

##### Import
tofill


#### PostgreSQL
* Run a Kubernetes job based on the original PostgreSQL image (to get same version)
##### Export
* Run pg_dump

##### Import
tofill


## License
This project is licensed under the MIT License. See the [LICENSE file](https://raw.githubusercontent.com/Cosmo-Tech/clustersnap/main/LICENSE.md) for details.

<br>
<br>
<br>

Made with :heart: by Cosmo Tech DevOps team
