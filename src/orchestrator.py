import os
import sys
import argparse
import signal
import yaml
import hashlib
import datetime
import signal

from src.kubernetes.base import KubernetesBase
from src.utils import general as utils


# def job_volumes():
#     try:
#         # cluster_context = os.environ["BKUP_CLUSTER_CONTEXT"]
#         cluster_context = os.getenv('BKUP_CLUSTER_CONTEXT')
#         if not cluster_context:
#             print(f"error: {utils.current_function()}: missing export BKUP_CLUSTER_CONTEXT")

#         kube = KubernetesBase(context=cluster_context)

#         mounts_map = {}

#         print("fetching all informations to perform backup...")

#         # User-defined list of namespaces & fallback on list of all namespaces of the cluster
#         namespaces_env = os.getenv('BKUP_TARGET_NAMESPACES')
#         namespaces = []
#         if namespaces_env:
#             for ns in namespaces_env.split(','):
#                 namespaces.append(ns.strip())
#         else:
#             namespaces = kube.list_namespaces()

#         for n in namespaces:
#             mounts_map[n] = []

#             deployments = kube.list_deployments(namespace=n)
#             statefulsets = kube.list_statefulsets(namespace=n)

#             pvc_list = kube.list_pvc(namespace=n)
#             for p in pvc_list:
#                 for w in deployments:
#                     mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
#                     for m in mounts:
#                         print(f"found {n}/{p}")
#                         mounts_map[n].append(m)

#                 for w in statefulsets:
#                     mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
#                     for m in mounts:
#                         print(f"found {n}/{p}")
#                         mounts_map[n].append(m)

#         image                   = os.getenv('BKUP_IMAGE', "ghcr.io/cosmo-tech/bkup:ci")
#         image_pull_policy       = os.getenv('BKUP_IMAGE_PULL_POLICY', "Always")
#         storage_protocol        = os.getenv('BKUP_STORAGE_PROTOCOL')
#         storage_endpoint        = os.getenv('BKUP_STORAGE_ENDPOINT')
#         storage_root            = os.getenv('BKUP_STORAGE_ROOT')
#         storage_username        = os.getenv('BKUP_STORAGE_USERNAME')
#         storage_password        = os.getenv('BKUP_STORAGE_PASSWORD')
        
#         # print(image)
#         # print(image_pull_policy)
#         # print(storage_address)
#         # print(storage_username)
#         # print(storage_password)

#         job_template_path="templates/volume.exporter.job.yaml"
#         if not os.path.exists(job_template_path):
#             raise FileNotFoundError(f"template path '{job_template_path}' not found.")

#         print("items:")
#         for ns, mounts in mounts_map.items():
#             if mounts:
#                 print(f"- namespace:")
#                 print(f"    name: {ns}")
#                 print(f"    persistence:")
#                 for m in mounts:
#                     pvc = m.get("pvc")
#                     workload_kind = m.get("workload_kind")
#                     workload_name = m.get("workload_name")
#                     container = m.get("container")
#                     volume = m.get("volume")
#                     mount_path = m.get("mount_path")

#                     print(f"    - pvc:              {pvc}")
#                     print(f"      workload_kind:    {workload_kind}")
#                     print(f"      workload_name:    {workload_name}")
#                     print(f"      container:        {container}")
#                     print(f"      volume:           {volume}")
#                     print(f"      mount_path:       {mount_path}")


#                     original_replicas = kube.get_workload_replicas(namespace=ns, workload_kind=workload_kind, workload_name=workload_name)
#                     print(f"      replicas:         {original_replicas}")

#                     # Scale down workload to be able claiming PVC
#                     kube.scale_workload_replicas(namespace=ns, workload_kind=workload_kind, workload_name=workload_name, replicas=0)

#                     job_name = f"{pvc}-{container}-{volume}"
#                     job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
#                     job_name_formatted = f"bkup-{job_name_hash[:63]}"

#                     archive_name = f"{pvc}_{volume}"
#                     kube.create_job(template_path=job_template_path,
#                                     image=image,
#                                     image_pull_policy=image_pull_policy,
#                                     namespace=ns,
#                                     job_name=job_name_formatted,
#                                     pvc_name=pvc,
#                                     # node_name=node_name,
#                                     mount_path=mount_path,
#                                     storage_protocol=storage_protocol,
#                                     storage_endpoint=storage_endpoint,
#                                     storage_root=storage_root,
#                                     storage_username=storage_username,
#                                     storage_password=storage_password,
#                                     archive_name=archive_name,
#                                     )

#                     kube.wait_job_completion(namespace=ns, job_name=job_name_formatted)

#                     # Scale up workload to its original replicas quantity
#                     kube.scale_workload_replicas(namespace=ns, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)



def restore_workload_on_signal(signum, frame):
    """Restore workload replicas in case of killed execution"""
    print(f"\n\nTERMINATION SIGNAL ({signum}) RECEIVED")
    kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)
    sys.exit(1)


def job_volumes():
    """Run Kubernetes volumes backup job"""
    try:
        cluster_context = os.getenv('BKUP_CLUSTER_CONTEXT')
        if not cluster_context:
            print(f"error: {utils.current_function()}: missing export BKUP_CLUSTER_CONTEXT")

        kube = KubernetesBase(context=cluster_context)

        image                   = os.getenv('BKUP_IMAGE', "ghcr.io/cosmo-tech/bkup:ci")
        image_pull_policy       = os.getenv('BKUP_IMAGE_PULL_POLICY', "Always")
        storage_protocol        = os.getenv('BKUP_STORAGE_PROTOCOL')
        storage_endpoint        = os.getenv('BKUP_STORAGE_ENDPOINT')
        storage_root            = os.getenv('BKUP_STORAGE_ROOT')
        storage_username        = os.getenv('BKUP_STORAGE_USERNAME')
        storage_password        = os.getenv('BKUP_STORAGE_PASSWORD')

        now_formatted = datetime.datetime.now().strftime('%y-%m-%d %H:%M:%S')       # => yy-mm-dd hh:mm:ss    (to display in logs)
        now_str = now_formatted.replace("-", "").replace(":", "").replace(" ", "")  # => yymmddhhmmss         (to include in technicals names such as files, resources etc...)

        job_template_path="templates/volume.exporter.job.yaml"
        if not os.path.exists(job_template_path):
            raise FileNotFoundError(f"template path '{job_template_path}' not found.")

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
                        print("")
                        print(f"datetime.............{now_str} ({now_formatted})")
                        print(f"namespace............{n}")
                        print(f"pvc..................{pvc}")
                        print(f"workload_kind........{workload_kind}")
                        print(f"workload_name........{workload_name}")
                        print(f"container............{container}")
                        print(f"volume...............{volume}")
                        print(f"mount_path...........{mount_path}")
                        print(f"original_replicas....{original_replicas}")

                        # Ensure replicas are restored in case of killed execution
                        signal.signal(signal.SIGTERM, restore_workload_on_signal)
                        # signal.signal(signal.SIGTERM, kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas))

                        # Scale down workload to be able claiming PVC
                        kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=0)

                        job_name = f"{pvc}-{container}-{volume}"
                        job_name_hash = hashlib.md5(job_name.encode()).hexdigest()
                        # 63 char max for Kubernetes names
                        job_name_formatted = f"bkup-{job_name_hash[:44]}-{now_str}"

                        archive_name = f"{pvc}_{volume}"
                        kube.create_job(template_path=job_template_path,
                                        image=image,
                                        image_pull_policy=image_pull_policy,
                                        namespace=n,
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

                        kube.wait_job_completion(namespace=n, job_name=job_name_formatted)

                        # Scale up workload to its original replicas quantity
                        kube.scale_workload_replicas(namespace=n, workload_kind=workload_kind, workload_name=workload_name, replicas=original_replicas)


    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)


def main():
    job_volumes()
    # job_postgresql()


if __name__ == "__main__":
    sys.exit(main())
