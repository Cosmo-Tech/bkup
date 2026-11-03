import os
import sys
import argparse
import signal
import yaml
import hashlib
import datetime
import signal

from src.lib.kubernetes import Kubernetes
import src.lib.utils as utils


cluster_context = os.getenv('BKUP_CLUSTER_CONTEXT')
if not cluster_context:
    print(f"error: {utils.current_function()}: missing export BKUP_CLUSTER_CONTEXT")

kube = Kubernetes(context=cluster_context)

now_formatted = datetime.datetime.now().strftime('%y-%m-%d %H:%M:%S')       # => yy-mm-dd hh:mm:ss    (to display in logs)
now_str = now_formatted.replace("-", "").replace(":", "").replace(" ", "")  # => yymmddhhmmss         (to include in technicals names such as files, resources etc...)

bkup_image_repository       = os.getenv('BKUP_IMAGE_REPOSITORY')
bkup_image_tag              = os.getenv('BKUP_IMAGE_TAG')
bkup_image_pull_policy      = os.getenv('BKUP_IMAGE_PULL_POLICY', 'Always')
bkup_image_pull_secrets     = yaml.safe_load(os.getenv('BKUP_IMAGE_PULL_SECRETS')).get("pullSecrets")
storage_protocol            = os.getenv('BKUP_STORAGE_PROTOCOL')
storage_endpoint            = os.getenv('BKUP_STORAGE_ENDPOINT')
storage_root                = os.getenv('BKUP_STORAGE_ROOT')
storage_username            = os.getenv('BKUP_STORAGE_USERNAME')
storage_password            = os.getenv('BKUP_STORAGE_PASSWORD')

job_config_common = {
    "BKUP_IMAGE_REPOSITORY":    bkup_image_repository,
    "BKUP_IMAGE_TAG":           bkup_image_tag,
    "BKUP_IMAGE_PULL_POLICY":   bkup_image_pull_policy,
    "BKUP_IMAGE_PULL_SECRETS":  bkup_image_pull_secrets,
    "STORAGE_PROTOCOL":         storage_protocol,
    "STORAGE_ENDPOINT":         storage_endpoint,
    "STORAGE_ROOT":             storage_root,
    "STORAGE_USERNAME":         storage_username,
    "STORAGE_PASSWORD":         storage_password,
}

def job_volumes():
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
            for ns in namespaces_env.split(','):
                namespaces.append(ns.strip())
        else:
            namespaces = kube.list_namespaces()

        for n in namespaces:
            deployments     = kube.list_deployments(namespace=n)
            statefulsets    = kube.list_statefulsets(namespace=n)
            workloads       = deployments + statefulsets

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

                        job_name = f"{pvc}-{container}-{volume}"
                        job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
                        # 63 char max for Kubernetes names
                        job_name_formatted = f"bkup-vol-{job_name_hash[:40]}-{now_str}"

                        archive_name = f"{pvc}_{volume}_{now_str}"

                        print("")
                        print(f"datetime.............{now_str} ({now_formatted})")
                        print(f"namespace............{n}")
                        print(f"job_name.............{job_name_formatted} ({job_name})")
                        print(f"pvc..................{pvc}")
                        print(f"workload_kind........{workload_kind}")
                        print(f"workload_name........{workload_name}")
                        print(f"container............{container}")
                        print(f"volume...............{volume}")
                        print(f"mount_path...........{mount_path}")
                        print(f"original_replicas....{original_replicas}")
                        print(f"archive_name.........{archive_name}")
                        print("")

                        # Ensure replicas are restored in case of cancelled execution
                        def abort_on_signal(signum, frame):
                            print(f"\n\ntermination signal ({signum}) received")
                            print(f"aborting backup job {job_name_formatted} and restoring replicas of {workload_kind} {n}/{workload_name} to {original_replicas}...")
                            kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)
                            kube.delete_job(namespace=n, job_name=job_name_formatted)
                            sys.exit(1)
                        signal.signal(signal.SIGTERM, abort_on_signal)
                        signal.signal(signal.SIGINT, abort_on_signal)

                        # Scale down workload to be able claiming PVC
                        kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=0)

                        job_config_specific = {
                            "JOB_NAME":         job_name_formatted,
                            "NAMESPACE":        n,
                            "TEMPLATE_PATH":    job_template_path,
                            "ARCHIVE_NAME":     archive_name,
                            "MOUNT_PATH":       mount_path,
                            "PVC_NAME":         pvc,
                        }
                        job_config = job_config_common | job_config_specific
                        kube.create_job(job_config=job_config)

                        kube.wait_job_completion(namespace=n, job_name=job_name_formatted)

                        # Scale up workload to its original replicas quantity
                        kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def job_postgresql():
    """Run PostgreSQL backup job"""
    try:
        print("starting PostgreSQL backup jobs...")

        job_template_path="templates/postgresql.exporter.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

        # User-defined PostgreSQL values
        postgresql_values = os.getenv('BKUP_POSTGRESQL_VALUES')
        if not postgresql_values:
            print(f"error: missing export BKUP_POSTGRESQL_VALUES")

        postgresql_configs = yaml.safe_load(postgresql_values).get("postgresql")
        for p in postgresql_configs:
            address                         = p.get("address")
            port                            = p.get("port")
            database                        = p.get("database")
            username                        = p.get("username")
            secret_name                     = p.get("passwordSecret").get("name")
            secret_key                      = p.get("passwordSecret").get("key")
            postgresql_image_repository     = p.get("image").get("repository")
            postgresql_image_tag            = p.get("image").get("tag")
            postgresql_image_pull_policy    = p.get("image").get("pullPolicy")
            postgresql_image_pull_secrets   = p.get("image").get("pullSecrets")
            job_namespace                   = p.get("namespace")

            job_name = f"psql-{address}-{database}"
            job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
            # 63 char max for Kubernetes names
            job_name_formatted = f"bkup-psql-{job_name_hash[:39]}-{now_str}"

            archive_name = f"psql_{address}_{database}_{now_str}"

            all_image_pull_secrets = bkup_image_pull_secrets + postgresql_image_pull_secrets

            print("")
            print(f"datetime.........................{now_str} ({now_formatted})")
            print(f"namespace........................{job_namespace}")
            print(f"job_name.........................{job_name_formatted} ({job_name})")
            print(f"address..........................{address}")
            print(f"port.............................{port}")
            print(f"database.........................{database}")
            print(f"username.........................{username}")
            print(f"secret_name......................{secret_name}")
            print(f"secret_key.......................{secret_key}")
            print(f"postgresql_image_repository......{postgresql_image_repository}")
            print(f"postgresql_image_tag.............{postgresql_image_tag}")
            print(f"postgresql_image_pull_policy.....{postgresql_image_pull_policy}")
            print(f"all_image_pull_secrets...........{all_image_pull_secrets}")
            print(f"archive_name.....................{archive_name}")
            print("")

            # Ensure replicas are restored in case of cancelled execution
            def abort_on_signal(signum, frame):
                print(f"\n\ntermination signal ({signum}) received")
                print(f"aborting backup job {job_name_formatted}...")
                kube.delete_job(namespace=n, job_name=job_name_formatted)
                sys.exit(1)
            signal.signal(signal.SIGTERM, abort_on_signal)
            signal.signal(signal.SIGINT, abort_on_signal)

            job_config_specific = {
                "JOB_NAME":                         job_name_formatted,
                "NAMESPACE":                        job_namespace,
                "TEMPLATE_PATH":                    job_template_path,
                "ARCHIVE_NAME":                     archive_name,
                "MOUNT_PATH":                       "/shared_bkup",
                "POSTGRESQL_IMAGE_REPOSITORY":      postgresql_image_repository,
                "POSTGRESQL_IMAGE_TAG":             postgresql_image_tag,
                "POSTGRESQL_IMAGE_PULL_POLICY":     postgresql_image_pull_policy,
                "ALL_IMAGE_PULL_SECRETS":           all_image_pull_secrets,
            }
            job_config = job_config_common | job_config_specific
            kube.create_job(job_config=job_config)

            kube.wait_job_completion(namespace=job_namespace, job_name=job_name_formatted)

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def main():
    # job_volumes()
    job_postgresql()


if __name__ == "__main__":
    sys.exit(main())
