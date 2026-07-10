import sys
import argparse
import time
import signal
import os
import yaml
from src.kubernetes import Kubernetes
import src.utils.backup_map as bpm

backup_workloads = {}
kube_client_sigkill = None


def restore_production_on_signal(signum, frame):
    """Rollback scaled down replicas in case of interruption"""
    print(f"\nwarn: termination signal ({signum}) received! rollbacking replicas...")

    if not backup_workloads or not kube_client_sigkill:
        print("info: no workload state captured yet. exiting safely.")
        sys.exit(0)

    for resource_key, info in backup_workloads.items():
        try:
            kube_client_sigkill.namespace = info["namespace"]
            kube_client_sigkill.delete_backup_job(pvc_name=info["pvc_name"])
        except Exception:
            pass

    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            print(f"  info: restoring {resource_key} in namespace {info['namespace']} to {info['initial_replicas']} replicas...")
            
            kube_client_sigkill.namespace = info["namespace"]            
            kube_client_sigkill.scale_replicas(
                info["resource_type"],
                info["resource_name"],
                replicas=info["initial_replicas"]
            )

    print("info: rollback finished. exiting.")
    sys.exit(1)


def run_backup_job(kube: Kubernetes, namespace: str):
    """
    Global sequential orchestrator:
    Phase 0: Cleanup + wait for deletion of old Jobs
    Phase 1: Gather configuration + Trigger Scale Down (0)
    Phase 2: Wait for complete production workload termination
    Phase 3: Trigger all new backup Jobs concurrently via K8s control plane
    Phase 4: Restore (Scale Up) production workloads to original layout states
    """
    global backup_workloads

    pvcs = kube.list_pvc()

    # Skip if no PVC
    if not pvcs:
        return

    print(f"\n==================================================")
    print(f" STARTING BACKUPS FOR NAMESPACE {namespace}")
    print(f"==================================================")

    print("\nPHASE 0: Cleaning existing backups jobs...")
    pvc_list = []

    for pvc in pvcs:
        pvc_name = pvc['name']
        pvc_list.append(pvc_name)
        kube.delete_backup_job(pvc_name=pvc_name)

    if not kube.wait_for_jobs_deletion(pvc_list=pvc_list):
        print("  warn: Some old jobs might still be terminating. Proceeding anyway...")

    backup_workloads.clear()

    # ==================================================
    # PHASE 1 : WORKLOAD ASSESSMENT AND SCALE DOWN
    # ==================================================
    print("\nPHASE 1: Capturing states and scaling down workloads...")
    for pvc in pvcs:
        pvc_name = pvc['name']
        targets = kube.get_pvc(pvc_name)
        if not targets:
            print(f"  warn: No active workload config found for {pvc_name}. Skipping.")
            continue

        target = targets[0]
        resource_key = f"{namespace}/{target['resource']}"
        resource_type, resource_name = target['resource'].split('/')
        mounted_path = target['mount_path']

        if resource_key not in backup_workloads:
            node_name = kube.get_node_name(pvc_name) 
            initial_replicas = kube.get_replicas(resource_type, resource_name)

            backup_workloads[resource_key] = {
                "namespace": namespace,
                "resource_type": resource_type,
                "resource_name": resource_name,
                "initial_replicas": initial_replicas,
                "pvc_name": pvc_name,
                "mounted_path": mounted_path,
                "node_name": node_name
            }

            if initial_replicas > 0:
                kube.scale_replicas(resource_type, resource_name, replicas=0)
            else:
                print(f"  info: {resource_key} is already stopped (Night mode).")

    # ==================================================
    # PHASE 2 : WAIT FOR COMPLETE EXCLUSIVITY
    # ==================================================
    print("\nPHASE 2: Waiting for all workloads to be fully stopped...")
    for resource_key, info in backup_workloads.items():
        if info["namespace"] == namespace and info["initial_replicas"] > 0:
            kube.wait_for_scale_down(info["resource_type"], info["resource_name"])

    print("\nensure PVC are not attached anymore...")
    for resource_key, info in backup_workloads.items():
        if info["namespace"] == namespace:
            kube.wait_for_volume_detachment(info["pvc_name"])


    # ==================================================
    # PHASE 3 : TRIGGER CONCURRENT BACKUPS
    # ==================================================
    print("\nPHASE 3: Triggering all Kubernetes backup jobs simultaneously...")
    for resource_key, info in backup_workloads.items():
        if info["namespace"] == namespace:
            kube.create_backup_job(
                pvc_name=info["pvc_name"], 
                mounted_path=info["mounted_path"], 
                node_name=info["node_name"]
            )

    # Wait for backup to end before scale back replicas
    print("\nWaiting for backup jobs to complete before scaling back up...")
    for resource_key, info in backup_workloads.items():
        if info["namespace"] == namespace:
            kube.wait_for_job_completion(job_name=kube.get_job_name(info["pvc_name"]))


    # ==================================================
    # PHASE 4 : PRODUCTION WORKLOAD RESTORATION
    # ==================================================
    print("\nPHASE 4: Restoring production workloads to initial states...")
    for resource_key, info in backup_workloads.items():
        if info["namespace"] == namespace and info["initial_replicas"] > 0:
            kube.scale_replicas(
                info["resource_type"],
                info["resource_name"],
                replicas=info["initial_replicas"]
            )
            print(f"  info: {resource_key} restored to {info['initial_replicas']} replicas.")

    print(f"\n==================================================")
    print(f" BACKUP FINISHED FOR NAMESPACE {namespace}")
    print(f"==================================================")



def main():
    global kube_client_sigkill

    # SIGINT = CTRL+C (CLI)
    signal.signal(signal.SIGINT, restore_production_on_signal)

    # SIGTERM = Kubernetes Pod killed
    signal.signal(signal.SIGTERM, restore_production_on_signal)


    parser = argparse.ArgumentParser(description="Backup Kubernetes volumes")
    parser.add_argument("-c", "--context", type=str, default=None, help="Local kubectl context")
    parser.add_argument("-n", "--namespace", type=str, default=None, help="Kubernetes namespace (comma-separated or 'all')")
    parser.add_argument("-j", "--job", action="store_true", help="Run a backup job")
    parser.add_argument("-gm", "--get-map", action="store_true", help="Display backup-map")
    parser.add_argument("-rm", "--refresh-map", action="store_true", help="Refresh data in the backup-map ConfigMap (must be runned when all PVC workload are up)")
    args = parser.parse_args()


    cluster_name_env = os.getenv("CLUSTER_NAME")
    namespace_env = os.getenv("NAMESPACE")

    cluster_name = cluster_name_env if cluster_name_env else args.context
    namespace_input = namespace_env if namespace_env else args.namespace


    # for key, value in os.environ.items():
    #     print('{}: {}'.format(key, value))


    kube = Kubernetes(context=cluster_name, namespace="default")
    kube_client_sigkill = kube

    # Targetted namespace
    if namespace_input and namespace_input.lower() != "all":
        target_namespaces = [ns.strip() for ns in namespace_input.split(",")]

    # Cluster-wide
    else:
        target_namespaces = kube.list_namespaces()


    if args.job:
        bpm.get_backup_map(kube)
        for ns in target_namespaces:
            kube.namespace = ns
            run_backup_job(kube, namespace=ns)

    elif args.refresh_map:
        backup_map = bpm.backup_map_scanner(kube, target_namespaces)
        kube.save_to_configmap(
            name="clustersnap-backup-map",
            namespace=kube.get_master_namespace(),
            data=backup_map
        )

    elif args.get_map:
        backup_map = bpm.get_backup_map(kube)
        print(yaml.dump(backup_map, sort_keys=False, default_flow_style=False))

    else:
        print('error: please --help for more info')


if __name__ == "__main__":
    sys.exit(main())