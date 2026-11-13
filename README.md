# bkup
**Cloud-Agnostic & Kubernetes-native backup tool**

**bkup** is a lightweight, production-grade, cloud-agnostic & Kubernetes-native backup tool that safely secures Kubernetes volumes into remote storage like Azure Blob Storage, AWS S3 etc...

* :minidisc: **Export** Kubernetes data to remote storage.
* :recycle: **Import** backed-up data to Kubernetes.
* :package: **Backups Isolation:** Each Kubernetes namespace is backed up into a dedicated space.
* :closed_lock_with_key: **Ensure data integrity:** Scale-down running workload, backup their volumes and restore the workload.
* :pinching_hand: **Cloud-native & minimalist:** Light footprint, easy to install, and fully customizable from standard Kubernetes manifests without third-party CRD dependencies.
* :anchor: **Native Kubernetes** volumes backups
* :elephant: **Native PostgreSQL** databases backups

<br>

## Requirements
* Remote storage (anything handled by [fsspec](https://filesystem-spec.readthedocs.io/) like Azure Blob Storage, S3, FSTP etc...), with credentials
* Working Kubernetes cluster

<br>

## Installation
> [!CAUTION]
> This tool is based on **scaling down** Kubernetes **pods** to orchestrate a clean backup plan. \
> You should probably try it on a test environment first.

* From CLI
  ```
  python -m .venv
  source .venv/bin/activate
  pip install .
  ```

* From Helm Chart
  ```
  helm -n NAMESPACE upgrade --install bkup helm-chart/cosmotech-kubernetes-backup --values values.yaml
  ```

<br>

## How to
### Backup
Trigger the cronjob installed from the Helm Chart (or wait for it)
### Restore
```
python -m src.cli import -f <file_from_your_remote_storage>
```

### Workflows
#### General
```mermaid
%%{init: { 'themeVariables': { 'fontSize': '12px' } } }%%
flowchart LR
    subgraph CLI_CronJob["Orchestrator (CLI / CronJob)"]
        O[src.cli / src.orchestrator]
    end

    subgraph K8s["Kubernetes Cluster"]
        CM[(ConfigMap <br> bkup config)]
        SEC[(Secret <br> Storage Credentials)]
        JIT[(JIT Secret <br> Target Namespace)]
        W[Deployment / StatefulSet]
        PVC[(PVC)]
        PG[(PostgreSQL Instance)]

        subgraph VolJob["Volume Job Pod"]
            EXP_V[exporter / importer <br> Python + fsspec]
        end

        subgraph PgJob["PostgreSQL Job Pod"]
            SIDE_PG[database-dumper / restorer <br> pg_dump / psql]
            PIPE((UNIX Pipe <br> + signal file))
            EXP_PG[exporter / importer <br> Python + fsspec]
            SIDE_PG <-->|emptyDir /shared_bkup| PIPE
            PIPE <-->|1MB stream chunks| EXP_PG
        end
    end

    REMOTE[(Remote Storage <br> S3 / Azure Blob / GCS / SFTP)]

    O -->|Read config| CM
    O -->|Duplicate / Delete| SEC -.-> JIT
    O -->|Scale 0 & restore replicas| W
    O -->|Spawn Jobs| VolJob & PgJob
    EXP_V <-->|Mount| PVC
    SIDE_PG <-->|TCP 5432| PG
    EXP_V <-->|fsspec stream| REMOTE
    EXP_PG <-->|fsspec stream| REMOTE
```

#### Volumes
##### Export
```mermaid
%%{init: { 'themeVariables': { 'fontSize': '11px' }, 'flowchart': { 'rankSpacing': 30 } } }%%
flowchart TD
    A[Read target namespaces <br> BKUP_VOLUMES_NAMESPACES or all]
    --> B[For each namespace]
    --> C[Create JIT storage secret]
    --> D[List PVCs & map mountpoints <br> on Deployments / StatefulSets]
    --> E[Save current replicas in annotation <br> bkup/original-replicas]
    --> F[Scale workload to 0]
    --> G[Spawn volume.exporter.job <br> mounting PVC readOnly]
    --> H[Stream tar.gz via fsspec <br> to Remote Storage]
    --> I[Wait for Job completion]
    --> J[Restore original replicas <br> & delete JIT secret]
```

##### Import
```mermaid
%%{init: { 'themeVariables': { 'fontSize': '11px' }, 'flowchart': { 'rankSpacing': 30 } } }%%
flowchart TD
    A[CLI Wizard / Args <br> Select instance, namespace & .tar.gz]
    --> B[Verify target PVC & active workload <br> mountpoint in namespace]
    --> C[Create JIT storage secret]
    --> D[Save current replicas in annotation <br> bkup/original-replicas]
    --> E[Scale workload to 0]
    --> F[Spawn volume.importer.job <br> mounting PVC readWrite]
    --> G[Stream & extract tar.gz <br> into mountPath]
    --> H[Wait for Job completion]
    --> I[Restore original replicas <br> & delete JIT secret]
```

#### PostgreSQL
##### Export
```mermaid
%%{init: { 'themeVariables': { 'fontSize': '11px' }, 'flowchart': { 'rankSpacing': 30 } } }%%
flowchart TD
    A[Parse BKUP_POSTGRESQL_VALUES]
    --> B[For each PostgreSQL instance]
    --> C[Create JIT storage secret <br> in job namespace]
    --> D[Spawn postgresql.exporter.job <br> exporter + database-dumper]
    --> E[Sidecar creates UNIX pipe <br> & discovers databases]
    --> F[Signal 'dumping:db' & stream <br> pg_dump --clean --if-exists]
    --> G[Exporter compresses gzip <br> & streams to Remote Storage]
    --> H[Exporter writes 'done:db' <br> Sidecar writes 'finished']
    --> I[Wait for Job completion <br> & delete JIT secret]
```

##### Import
```mermaid
%%{init: { 'themeVariables': { 'fontSize': '11px' }, 'flowchart': { 'rankSpacing': 30 } } }%%
flowchart TD
    A[CLI Wizard / Args <br> Select instance, namespace & .gz]
    --> B[Match archive host MD5 hash <br> & target database in config]
    --> C[Create JIT storage secret <br> in job namespace]
    --> D[Spawn postgresql.importer.job <br> importer + database-restorer]
    --> E[Sidecar creates UNIX pipe <br> & sets signal 'restoring:db']
    --> F[Importer downloads & gunzips <br> remote backup into pipe]
    --> G[Sidecar executes psql <br> --single-transaction ON_ERROR_STOP=1]
    --> H[Importer writes 'done:db' <br> Sidecar writes 'finished']
    --> I[Wait for Job completion <br> & delete JIT secret]
```

<br>

## License
This project is licensed under the MIT License. See the [LICENSE file](https://raw.githubusercontent.com/Cosmo-Tech/bkup/main/LICENSE.md) for details.

<br>
<br>

Made with :heart: by Cosmo Tech DevOps team
