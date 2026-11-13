import os
import sys
import yaml
import hashlib
import datetime
import signal
import base64

import src.lib.utils as utils
from src.lib.kubernetes import Kubernetes


kube_context = os.getenv('BKUP_KUBE_CONTEXT')
kube = Kubernetes(context=kube_context)

now_formatted = datetime.datetime.now().strftime('%y-%m-%d %H:%M:%S')       # => yy-mm-dd hh:mm:ss    (to display in logs)
now_str = now_formatted.replace("-", "").replace(":", "").replace(" ", "")  # => yymmddhhmmss         (to include in technicals names such as files, resources etc...)

bkup_image_repository           = os.getenv('BKUP_IMAGE_REPOSITORY')
bkup_image_tag                  = os.getenv('BKUP_IMAGE_TAG')
bkup_image_pull_policy          = os.getenv('BKUP_IMAGE_PULL_POLICY', 'Always')
bkup_image_pull_secrets         = yaml.safe_load(os.getenv('BKUP_IMAGE_PULL_SECRETS'))
storage_protocol                = os.getenv('BKUP_STORAGE_PROTOCOL')
storage_endpoint                = os.getenv('BKUP_STORAGE_ENDPOINT')
storage_root                    = os.getenv('BKUP_STORAGE_ROOT')
storage_username                = os.getenv('BKUP_STORAGE_USERNAME')
storage_password_secret_name    = os.getenv('BKUP_STORAGE_PASSWORD_SECRET_NAME')
storage_password_secret_key     = os.getenv('BKUP_STORAGE_PASSWORD_SECRET_KEY')

DEFAULT_POD_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 65532,
    "runAsGroup": 65532,
    "fsGroup": 65532,
}

DEFAULT_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 65532,
    "runAsGroup": 65532,
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}

bkup_pod_security_context = (
    yaml.safe_load(os.getenv("BKUP_POD_SECURITY_CONTEXT"))
    if os.getenv("BKUP_POD_SECURITY_CONTEXT")
    else DEFAULT_POD_SECURITY_CONTEXT
)

bkup_security_context = (
    yaml.safe_load(os.getenv("BKUP_SECURITY_CONTEXT"))
    if os.getenv("BKUP_SECURITY_CONTEXT")
    else DEFAULT_SECURITY_CONTEXT
)

job_config_common = {
    "BKUP_IMAGE_REPOSITORY":        bkup_image_repository,
    "BKUP_IMAGE_TAG":               bkup_image_tag,
    "BKUP_IMAGE_PULL_POLICY":       bkup_image_pull_policy,
    "BKUP_IMAGE_PULL_SECRETS":      bkup_image_pull_secrets,
    "BKUP_POD_SECURITY_CONTEXT":    bkup_pod_security_context,
    "BKUP_SECURITY_CONTEXT":        bkup_security_context,
    "STORAGE_PROTOCOL":             storage_protocol,
    "STORAGE_ENDPOINT":             storage_endpoint,
    "STORAGE_ROOT":                 storage_root,
    "STORAGE_USERNAME":             storage_username,
    "STORAGE_PASSWORD_SECRET_NAME": storage_password_secret_name,
    "STORAGE_PASSWORD_SECRET_KEY":  storage_password_secret_key,
}

orchestrator_namespace = os.getenv('BKUP_ORCHESTRATOR_NAMESPACE')


def create_jit_secret(namespace: str):
    """Create JIT secret for remote storage access"""

    # Security to avoid the source secret
    if namespace == orchestrator_namespace:
        return

    print(f"creating JIT secret {namespace}/{storage_password_secret_name}...")
    if kube.can_i_create_secrets(namespace=namespace) == True:
        kube.duplicate_secret(source_namespace=orchestrator_namespace, source_name=storage_password_secret_name, new_namespace=namespace, new_name=storage_password_secret_name)


def delete_jit_secret(namespace: str):
    """Delete JIT secret of remote storage access"""

    # Security to avoid the source secret
    if namespace == orchestrator_namespace:
        return

    print(f"cleaning JIT secret {namespace}/{storage_password_secret_name}...")
    if kube.can_i_create_secrets(namespace=namespace) == True:
        kube.delete_secret(namespace=namespace, secret_name=storage_password_secret_name)


def job_export_volumes():
    """Run Kubernetes volumes backup job"""
    try:
        print("starting Kubernetes volumes backup jobs...")

        job_template_path="templates/volume.exporter.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

        print("fetching all informations to perform backup...")

        # User-defined list of namespaces & fallback on list of all namespaces of the cluster
        namespaces_env = os.getenv('BKUP_VOLUMES_NAMESPACES')
        namespaces = []
        if namespaces_env:
            parsed_ns = yaml.safe_load(namespaces_env)
            if isinstance(parsed_ns, list):
                namespaces = [str(ns).strip() for ns in parsed_ns if str(ns).strip()]
            elif isinstance(parsed_ns, str) and parsed_ns.strip():
                namespaces = [ns.strip() for ns in parsed_ns.split(',') if ns.strip()]

        if not namespaces:
            namespaces = kube.list_namespaces()

        print(f"target namespaces: {namespaces}")


        for n in namespaces:
            deployments     = kube.list_deployments(namespace=n)
            statefulsets    = kube.list_statefulsets(namespace=n)
            workloads       = deployments + statefulsets

            create_jit_secret(namespace=n)

            pvc_list = kube.list_pvc(namespace=n)
            for p in pvc_list:
                for w in workloads:
                    mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
                    for m in mounts:
                        pvc                 = m.get("pvc")
                        workload_kind       = m.get("workload_kind")
                        workload_name       = m.get("workload_name")
                        container           = m.get("container")
                        volume              = m.get("volume")
                        mount_path          = m.get("mount_path")
                        original_replicas   = kube.get_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name)

                        try :
                            job_name = f"{pvc}-{container}-{volume}"
                            job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
                            # 63 char max for Kubernetes names
                            job_name_formatted = f"bkup-vol-{job_name_hash[:40]}-{now_str}"

                            archive_name = f"{pvc}_{volume}_{now_str}"

                            print("\n---------------------------------------------------------------------")
                            print("Kubernetes job:")
                            print(f"  datetime.............{now_str} ({now_formatted})")
                            print(f"  namespace............{n}")
                            print(f"  job_name.............{job_name_formatted} ({job_name})")

                            print("\nVolume to backup:")
                            print(f"  pvc..................{pvc}")
                            print(f"  workload_kind........{workload_kind}")
                            print(f"  workload_name........{workload_name}")
                            print(f"  container............{container}")
                            print(f"  volume...............{volume}")
                            print(f"  mount_path...........{mount_path}")
                            print(f"  original_replicas....{original_replicas}")

                            print("\nDestination storage:")
                            print(f"  archive_name.........{archive_name}")
                            print("---------------------------------------------------------------------\n")

                            # Ensure replicas are restored in case of cancelled execution
                            def abort_on_signal(signum, frame):
                                print(f"\n\ntermination signal ({signum}) received")
                                delete_jit_secret(namespace=n)

                                print(f"aborting backup job {job_name_formatted} and restoring replicas of {workload_kind} {n}/{workload_name} to {original_replicas}...")
                                kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)
                                kube.delete_job(namespace=n, job_name=job_name_formatted)
                                kube.wait_job_end(namespace=n, job_name=job_name_formatted)

                                sys.exit(1)
                            signal.signal(signal.SIGTERM, abort_on_signal)
                            signal.signal(signal.SIGINT, abort_on_signal)

                            # Add original replicas quantity to workload annotations
                            original_replicas_annotation = "bkup/original-replicas"
                            kube.add_workload_annotation(namespace=n, workload_kind=workload_kind, workload_name=workload_name, annotations={original_replicas_annotation: str(original_replicas)})
                            original_replicas_from_annotation = kube.get_workload_annotation(namespace=n, workload_kind=workload_kind, workload_name=workload_name, annotation_key=original_replicas_annotation)

                            # Scale down workload to be able claiming PVC
                            kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=0)

                            job_config_specific = {
                                "JOB_NAME":         job_name_formatted,
                                "JOB_NAMESPACE":    n,
                                "TEMPLATE_PATH":    job_template_path,
                                "ARCHIVE_NAME":     archive_name,

                                # Volumes
                                "MOUNT_PATH":       mount_path,
                                "PVC_NAME":         pvc,
                            }
                            job_config = job_config_common | job_config_specific
                            kube.create_job(job_config=job_config)

                            kube.wait_job_end(namespace=n, job_name=job_name_formatted)

                            # Scale up workload to its original replicas quantity
                            kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas_from_annotation)

                        finally:
                            kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas_from_annotation)

            delete_jit_secret(namespace=n)

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def job_import_volume(namespace: str, archive_name: str):
    """Run Kubernetes volumes import job"""
    try:
        if not namespace:
            raise ValueError("a valid target namespace must be provided.")
        if not archive_name:
            raise ValueError("a valid archive name must be provided.")

        raw_archive = archive_name.strip().rstrip("/")

        # Optional safety check: if a namespace folder is present in the path, ensure it matches the target namespace
        path_parts = [p for p in raw_archive.split("/") if p]
        if len(path_parts) >= 2 and path_parts[-2] != namespace:
            raise ValueError(f"namespace mismatch: archive path specifies '{path_parts[-2]}' but target namespace is '{namespace}'.")

        archive_name = os.path.basename(archive_name.strip().rstrip("/"))
        if not archive_name.endswith(".tar.gz"):
            raise ValueError(f"the archive must be .tar.gz")

        print(f"starting Kubernetes volumes import job for archive '{archive_name}' in namespace '{namespace}'...")

        job_template_path = "templates/volume.importer.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

        # Extract target PVC from normalized archive name ("{pvc}_{volume}_{now_str}")
        target_pvc = archive_name.split('_')[0]

        pvc_list = kube.list_pvc(namespace=namespace)
        if target_pvc not in pvc_list:
            raise ValueError(f"failed to find PVC '{target_pvc}' in namespace '{namespace}'. PVC written in the backup file must exists in the namespace.")

        target_mount = None
        workloads = kube.list_deployments(namespace=namespace) + kube.list_statefulsets(namespace=namespace)
        for w in workloads:
            mounts = kube.map_mountpoints_pvc_workload(pvc_name=target_pvc, workload=w)
            for m in mounts:
                expected_prefix = f"{m.get('pvc')}_{m.get('volume')}_"
                if archive_name.startswith(expected_prefix):
                    target_mount = m
                    break
            if target_mount:
                break

        if not target_mount:
            raise ValueError(f"No active workload mounting the volume from archive '{archive_name}' found in namespace '{namespace}'.")

        pvc                 = target_mount.get("pvc")
        workload_kind       = target_mount.get("workload_kind")
        workload_name       = target_mount.get("workload_name")
        container           = target_mount.get("container")
        volume              = target_mount.get("volume")
        mount_path          = target_mount.get("mount_path")
        original_replicas   = kube.get_workload_replicas(namespace=namespace, workload_kind=workload_kind, workload_name=workload_name)

        job_name = f"{pvc}-{container}-{volume}"
        job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
        job_name_formatted = f"bkup-vol-import-{job_name_hash[:32]}-{now_str}"

        print("\n---------------------------------------------------------------------")
        print("Kubernetes import job:")
        print(f"  datetime.............{now_str} ({now_formatted})")
        print(f"  namespace............{namespace}")
        print(f"  job_name.............{job_name_formatted} ({job_name})")
        print("\nVolume to restore:")
        print(f"  pvc..................{pvc}")
        print(f"  workload.............{workload_kind}/{workload_name} (replicas: {original_replicas})")
        print(f"  mount_path...........{mount_path}")
        print(f"  archive_name.........{archive_name}")
        print("---------------------------------------------------------------------\n")

        create_jit_secret(namespace=namespace)

        def abort_on_signal(signum, frame):
            print(f"\n\ntermination signal ({signum}) received")
            delete_jit_secret(namespace=namespace)
            print(f"aborting import job {job_name_formatted} and restoring replicas of {workload_kind} {namespace}/{workload_name} to {original_replicas}...")
            kube.scale_workload_replicas(namespace=namespace, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)
            kube.delete_job(namespace=namespace, job_name=job_name_formatted)
            kube.wait_job_end(namespace=namespace, job_name=job_name_formatted)
            sys.exit(1)

        signal.signal(signal.SIGTERM, abort_on_signal)
        signal.signal(signal.SIGINT, abort_on_signal)

        original_replicas_annotation = "bkup/original-replicas"
        kube.add_workload_annotation(namespace=namespace, workload_kind=workload_kind, workload_name=workload_name, annotations={original_replicas_annotation: str(original_replicas)})
        original_replicas_from_annotation = kube.get_workload_annotation(namespace=namespace, workload_kind=workload_kind, workload_name=workload_name, annotation_key=original_replicas_annotation)

        try:
            # Scale down workload to release the PVC
            kube.scale_workload_replicas(namespace=namespace, workload_kind=workload_kind, workload_name=workload_name, replicas=0)

            job_config_specific = {
                "JOB_NAME":         job_name_formatted,
                "JOB_NAMESPACE":    namespace,
                "TEMPLATE_PATH":    job_template_path,
                "ARCHIVE_NAME":     archive_name,
                "MOUNT_PATH":       mount_path,
                "PVC_NAME":         pvc,
            }
            job_config = job_config_common | job_config_specific
            kube.create_job(job_config=job_config)
            kube.wait_job_end(namespace=namespace, job_name=job_name_formatted)

        finally:
            # Always restore original replicas and clean up the JIT secret
            kube.scale_workload_replicas(namespace=namespace, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas_from_annotation)
            delete_jit_secret(namespace=namespace)

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def job_export_postgresql():
    """Run PostgreSQL backup job"""
    try:
        print("starting PostgreSQL backup jobs...")

        job_template_path="templates/postgresql.exporter.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

        # User-defined PostgreSQL values
        postgresql_values = os.getenv('BKUP_POSTGRESQL_VALUES')
        if not postgresql_values:
            print("error: missing export BKUP_POSTGRESQL_VALUES")

        # postgresql_configs = yaml.safe_load(postgresql_values).get("postgresql")
        postgresql_configs = yaml.safe_load(postgresql_values)

        for p in postgresql_configs:
            postgresql_host                 = p.get("address")
            postgresql_port                 = p.get("port")
            postgresql_database             = p.get("database")
            postgresql_username             = p.get("username")
            postgresql_password_secret_name = p.get("passwordSecret").get("name")
            postgresql_password_secret_key  = p.get("passwordSecret").get("key")
            postgresql_image_repository     = p.get("image").get("repository")
            postgresql_image_tag            = p.get("image").get("tag")
            postgresql_image_pull_policy    = p.get("image").get("pullPolicy")
            postgresql_image_pull_secrets   = p.get("image").get("pullSecrets")
            job_namespace                   = p.get("namespace")

            job_name = f"psql-{postgresql_host}-{postgresql_database}"
            job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
            # 63 char max for Kubernetes names
            job_name_formatted = f"bkup-psql-export-{job_name_hash[:32]}-{now_str}"

            archive_name_hash = hashlib.md5(postgresql_host.encode()).hexdigest()
            archive_name = f"psql_{archive_name_hash}_{now_str}"

            all_image_pull_secrets = bkup_image_pull_secrets + postgresql_image_pull_secrets

            print("\n---------------------------------------------------------------------")
            print("Kubernetes job:")
            print(f"  datetime...........................{now_str} ({now_formatted})")
            print(f"  job_namespace......................{job_namespace}")
            print(f"  job_name...........................{job_name_formatted} ({job_name})")

            print("\nPostgreSQL instance:")
            print(f"  postgresql_host....................{postgresql_host}")
            print(f"  postgresql_port....................{postgresql_port}")
            print(f"  postgresql_database................{postgresql_database}")
            print(f"  postgresql_username................{postgresql_username}")
            print(f"  postgresql_password_secret_name....{postgresql_password_secret_name}")
            print(f"  postgresql_password_secret_key.....{postgresql_password_secret_key}")

            print("\nPostgreSQL image:")
            print(f"  postgresql_image_repository........{postgresql_image_repository}")
            print(f"  postgresql_image_tag...............{postgresql_image_tag}")
            print(f"  postgresql_image_pull_policy.......{postgresql_image_pull_policy}")
            print(f"  all_image_pull_secrets.............{all_image_pull_secrets}")

            print("\nDestination storage:")
            print(f"  archive_name.......................{archive_name} (psql_{postgresql_host}_{now_str})")
            print("---------------------------------------------------------------------\n")

            # Ensure replicas are restored in case of cancelled execution
            def abort_on_signal(signum, frame):
                print(f"\n\ntermination signal ({signum}) received")
                delete_jit_secret(namespace=job_namespace)

                print(f"aborting backup job {job_name_formatted}...")
                kube.delete_job(namespace=job_namespace, job_name=job_name_formatted)
                kube.wait_job_end(namespace=job_namespace, job_name=job_name_formatted)

                sys.exit(1)
            signal.signal(signal.SIGTERM, abort_on_signal)
            signal.signal(signal.SIGINT, abort_on_signal)

            create_jit_secret(namespace=job_namespace)

            job_config_specific = {
                "JOB_NAME":                         job_name_formatted,
                "JOB_NAMESPACE":                    job_namespace,
                "TEMPLATE_PATH":                    job_template_path,
                "ARCHIVE_NAME":                     archive_name,

                # Volume
                "MOUNT_PATH":                       "/shared_bkup",

                # PostgreSQL Image
                "POSTGRESQL_IMAGE_REPOSITORY":      postgresql_image_repository,
                "POSTGRESQL_IMAGE_TAG":             postgresql_image_tag,
                "POSTGRESQL_IMAGE_PULL_POLICY":     postgresql_image_pull_policy,
                "ALL_IMAGE_PULL_SECRETS":           all_image_pull_secrets,

                # PostgreSQL instance
                "POSTGRESQL_HOST":                  postgresql_host,
                "POSTGRESQL_PORT":                  postgresql_port,
                "POSTGRESQL_DATABASE":              postgresql_database,
                "POSTGRESQL_USERNAME":              postgresql_username,
                "POSTGRESQL_PASSWORD_SECRET_NAME":  postgresql_password_secret_name,
                "POSTGRESQL_PASSWORD_SECRET_KEY":   postgresql_password_secret_key,
            }
            job_config = job_config_common | job_config_specific
            kube.create_job(job_config=job_config)

            kube.wait_job_end(namespace=job_namespace, job_name=job_name_formatted)

            delete_jit_secret(namespace=job_namespace)

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)



def job_import_postgresql(archive_name: str):
    """Run PostgreSQL restore job"""
    try:
        print("starting PostgreSQL restore jobs...")

        job_template_path="templates/postgresql.importer.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

        # User-defined PostgreSQL values
        postgresql_values = os.getenv('BKUP_POSTGRESQL_VALUES')
        if not postgresql_values:
            print("error: missing export BKUP_POSTGRESQL_VALUES")
            sys.exit(1)

        postgresql_configs = yaml.safe_load(postgresql_values)
        if not postgresql_configs:
            print("warning: postgresql configurations empty in configmap.")
            return

        for p in postgresql_configs:
            postgresql_host                 = p.get("address")
            postgresql_database             = p.get("database")

            # Compare with PostgreSQL host to ensure restoring the right archive_name at the right place
            target_host = hashlib.md5(postgresql_host.encode()).hexdigest()
            if target_host not in archive_name:
                continue

            # Compare with PostgreSQL database to ensure restoring the right archive_name at the right place
            target_db = archive_name.split('_')[-1].replace('.gz', '')
            if postgresql_database and postgresql_database != target_db:
                continue

            if "/" in archive_name:
                archive_root_path = archive_name.split("/")[0]
                archive_name = archive_name.split("/")[1]
                print(f"\ndetected root path '{archive_root_path}', understood to use '{archive_name}'")
            actual_db = target_db if not postgresql_database else postgresql_database

            postgresql_port                 = p.get("port")
            postgresql_username             = p.get("username")
            postgresql_password_secret_name = p.get("passwordSecret").get("name")
            postgresql_password_secret_key  = p.get("passwordSecret").get("key")
            postgresql_image_repository     = p.get("image").get("repository")
            postgresql_image_tag            = p.get("image").get("tag")
            postgresql_image_pull_policy    = p.get("image").get("pullPolicy")
            postgresql_image_pull_secrets   = p.get("image").get("pullSecrets")
            job_namespace                   = p.get("namespace")

            job_name = f"psql-{postgresql_host}-{actual_db}"
            job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
            # 63 char max for Kubernetes names
            job_name_formatted = f"bkup-psql-import-{job_name_hash[:40]}-{now_str}"

            all_image_pull_secrets = bkup_image_pull_secrets + postgresql_image_pull_secrets

            print("\n---------------------------------------------------------------------")
            print("Kubernetes job:")
            print(f"  datetime...........................{now_str} ({now_formatted})")
            print(f"  job_namespace......................{job_namespace}")
            print(f"  job_name...........................{job_name_formatted} ({job_name})")

            print("\nPostgreSQL instance:")
            print(f"  postgresql_host....................{postgresql_host}")
            print(f"  postgresql_port....................{postgresql_port}")
            print(f"  postgresql_database................{actual_db}")
            print(f"  postgresql_username................{postgresql_username}")
            print(f"  postgresql_password_secret_name....{postgresql_password_secret_name}")
            print(f"  postgresql_password_secret_key.....{postgresql_password_secret_key}")

            print("\nPostgreSQL image:")
            print(f"  postgresql_image_repository........{postgresql_image_repository}")
            print(f"  postgresql_image_tag...............{postgresql_image_tag}")
            print(f"  postgresql_image_pull_policy.......{postgresql_image_pull_policy}")
            print(f"  all_image_pull_secrets.............{all_image_pull_secrets}")

            print("\nDestination storage:")
            print(f"  archive_name.......................{archive_name}")
            print("---------------------------------------------------------------------\n")

            # Ensure replicas are restored in case of cancelled execution
            def abort_on_signal(signum, frame):
                print(f"\n\ntermination signal ({signum}) received")
                delete_jit_secret(namespace=job_namespace)

                print(f"aborting backup job {job_name_formatted}...")
                kube.delete_job(namespace=job_namespace, job_name=job_name_formatted)
                kube.wait_job_end(namespace=job_namespace, job_name=job_name_formatted)

                sys.exit(1)
            signal.signal(signal.SIGTERM, abort_on_signal)
            signal.signal(signal.SIGINT, abort_on_signal)

            create_jit_secret(namespace=job_namespace)

            job_config_specific = {
                "JOB_NAME":                         job_name_formatted,
                "JOB_NAMESPACE":                    job_namespace,
                "TEMPLATE_PATH":                    job_template_path,
                "ARCHIVE_NAME":                     archive_name,

                # Volume
                "MOUNT_PATH":                       "/shared_bkup",

                # PostgreSQL Image
                "POSTGRESQL_IMAGE_REPOSITORY":      postgresql_image_repository,
                "POSTGRESQL_IMAGE_TAG":             postgresql_image_tag,
                "POSTGRESQL_IMAGE_PULL_POLICY":     postgresql_image_pull_policy,
                "ALL_IMAGE_PULL_SECRETS":           all_image_pull_secrets,

                # PostgreSQL instance
                "POSTGRESQL_HOST":                  postgresql_host,
                "POSTGRESQL_PORT":                  postgresql_port,
                "POSTGRESQL_DATABASE":              actual_db,
                "POSTGRESQL_USERNAME":              postgresql_username,
                "POSTGRESQL_PASSWORD_SECRET_NAME":  postgresql_password_secret_name,
                "POSTGRESQL_PASSWORD_SECRET_KEY":   postgresql_password_secret_key,
            }
            job_config = job_config_common | job_config_specific
            kube.create_job(job_config=job_config)

            kube.wait_job_end(namespace=job_namespace, job_name=job_name_formatted)

            delete_jit_secret(namespace=job_namespace)

            break

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)
