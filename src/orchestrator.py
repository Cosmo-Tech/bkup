import os
import sys
import argparse
import signal
import yaml
import hashlib

from src.kubernetes.base import KubernetesBase
from src.utils import general as utils


def job_volumes():
    try:
        # cluster_context = os.environ["BKUP_CLUSTER_CONTEXT"]
        cluster_context = os.getenv('BKUP_CLUSTER_CONTEXT')
        if not cluster_context:
            print(f"error: {utils.current_function()}: missing export BKUP_CLUSTER_CONTEXT")

        kube = KubernetesBase(context=cluster_context)

        mounts_map = {}

        print("fetching all informations to perform backup...")

        # User-defined list of namespaces & fallback on list of all namespaces of the cluster
        namespaces_env = os.getenv('BKUP_TARGET_NAMESPACES')
        namespaces = []
        if namespaces_env:
            for ns in namespaces_env.split(','):
                namespaces.append(ns.strip())
        else:
            namespaces = kube.list_namespaces()

        for n in namespaces:
            mounts_map[n] = []

            deployments = kube.list_deployments(namespace=n)
            statefulsets = kube.list_statefulsets(namespace=n)

            pvc_list = kube.list_pvc(namespace=n)
            for p in pvc_list:
                for w in deployments:
                    mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
                    for m in mounts:
                        print(f"found {n}/{p}")
                        mounts_map[n].append(m)

                for w in statefulsets:
                    mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
                    for m in mounts:
                        print(f"found {n}/{p}")
                        mounts_map[n].append(m)

        image                   = os.getenv('BKUP_IMAGE', "ghcr.io/cosmo-tech/bkup:ci")
        image_pull_policy       = os.getenv('BKUP_IMAGE_PULL_POLICY', "Always")
        storage_protocol        = os.getenv('BKUP_STORAGE_PROTOCOL')
        storage_endpoint        = os.getenv('BKUP_STORAGE_ENDPOINT')
        storage_root            = os.getenv('BKUP_STORAGE_ROOT')
        storage_username        = os.getenv('BKUP_STORAGE_USERNAME')
        storage_password        = os.getenv('BKUP_STORAGE_PASSWORD')
        
        # print(image)
        # print(image_pull_policy)
        # print(storage_address)
        # print(storage_username)
        # print(storage_password)

        job_template_path="templates/volume.exporter.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

        print("items:")
        for ns, mounts in mounts_map.items():
            if mounts:
                print(f"- namespace:")
                print(f"    name: {ns}")
                print(f"    persistence:")
                for m in mounts:
                    pvc = m.get("pvc")
                    workload = m.get("workload")
                    container = m.get("container")
                    volume = m.get("volume")
                    mount_path = m.get("mount_path")

                    print(f"    - pvc: {pvc}")
                    print(f"      workload: {workload}")
                    print(f"      container: {container}")
                    print(f"      volume: {volume}")
                    print(f"      mount_path: {mount_path}")

                    job_name = f"{pvc}-{container}-{volume}"
                    job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
                    job_name_formatted = f"bkup-{job_name_hash[:63]}"

                    archive_name = f"{pvc}_{volume}"
                    kube.create_job(template_path=job_template_path,
                                    image=image,
                                    image_pull_policy=image_pull_policy,
                                    namespace=ns,
                                    job_name=job_name_formatted,
                                    pvc_name=pvc,
                                    # node_name=node_name,
                                    mount_path=mount_path,
                                    storage_protocol=storage_protocol,
                                    storage_endpoint=storage_endpoint,
                                    storage_root=storage_root,
                                    storage_username=storage_username,
                                    storage_password=storage_password,
                                    archive_name=archive_name,
                                    )

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def main():
    job_volumes()
    # job_postgresql()


if __name__ == "__main__":
    sys.exit(main())
