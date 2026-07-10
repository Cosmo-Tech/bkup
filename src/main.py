import sys
import argparse
import time
import signal
import os
from src.kubernetes import Kubernetes


backup_workloads = {}
kube_client_sigkill = None


def restore_production_on_signal(signum, frame):
    """
    Emergency signal handler. Triggers an immediate rollback to initial
    replica states if the orchestrator is interrupted (CTRL+C or SIGTERM).
    """
    print(f"\nwarn: termination signal ({signum}) received! starting emergency rollback...", flush=True)

    if not backup_workloads or not kube_client_sigkill:
        print("info: no workload state captured yet. exiting safely.", flush=True)
        sys.exit(0)


    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            print(f"  info: emergency restoring {resource_key} to {info['initial_replicas']} replicas...", flush=True)
            kube_client_sigkill.scale_replicas(
                info["resource_type"],
                info["resource_name"],
                replicas=info["initial_replicas"]
            )

    print("info: emergency rollback finished. exiting.", flush=True)
    sys.exit(1)



def get_backup_map(kube: Kubernetes):
    """Get a YAML map of what will be backuped"""
    namespace = kube.get_namespace()

    print(f"cluster: {kube.get_cluster_name()}")
    print(f"namespaces:")
    print(f"- name: {namespace}")

    pvcs = kube.list_pvc()
    if not pvcs:
        print("error: no PVC found or unauthorized access.")
        return 0

    print(f"  claims:")
    for pvc in pvcs:
        print(f"    - name: {pvc['name']}")
        print(f"      status: {pvc['status']}")
        print(f"      capacity: {pvc['capacity']}")

        mounts = kube.get_pvc(pvc['name'])
        if mounts:
            print(f"      mounts:")
            for m in mounts:
                print(f"      - container: {m['container_name']}")
                print(f"        path: {m['mount_path']}")
                print(f"        read_only: {m['read_only']}")
                print(f"        resource: {m['resource']}")
        else:
            print(f"      mounts: {mounts}")


def run_backup_job(kube: Kubernetes):
    """
    Global sequential orchestrator:
    Phase 0: Cleanup + wait for deletion of old Jobs
    Phase 1: Gather configuration + Trigger Scale Down (0)
    Phase 2: Wait for complete production workload termination
    Phase 3: Trigger all new backup Jobs concurrently via K8s control plane
    Phase 4: Restore (Scale Up) production workloads to original layout states
    """
    global backup_workloads

    get_backup_map(kube)

    pvcs = kube.list_pvc()
    if not pvcs:
        print("error: no PVC found to backup.")
        return

    print(f"\n==================================================")
    print(f"Starting Backups ")
    print(f"==================================================")

    # ==================================================
    # PHASE 0 : GLOBAL JOB CLEANUP
    # ==================================================
    print("\nPHASE 0: Purging any existing/stale backup jobs...")
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
        resource_key = target['resource']
        resource_type, resource_name = resource_key.split('/')
        mounted_path = target['mount_path']

        if resource_key not in backup_workloads:
            initial_replicas = kube.get_replicas(resource_type, resource_name)
            node_name = kube.get_node_name(pvc_name)

            backup_workloads[resource_key] = {
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
        if info["initial_replicas"] > 0:
            kube.wait_for_scale_down(info["resource_type"], info["resource_name"])

    # ==================================================
    # PHASE 3 : TRIGGER CONCURRENT BACKUPS
    # ==================================================
    print("\nPHASE 3: Triggering all Kubernetes backup jobs simultaneously...")
    for resource_key, info in backup_workloads.items():
        kube.create_backup_job(
            pvc_name=info["pvc_name"], 
            mounted_path=info["mounted_path"], 
            node_name=info["node_name"]
        )

    # Wait for backup to end before scale back replicas
    print("\nWaiting for backup jobs to complete before scaling back up...")
    for resource_key, info in backup_workloads.items():
        kube.wait_for_job_completion(job_name=kube.get_job_name(info["pvc_name"]))


    # ==================================================
    # PHASE 4 : PRODUCTION WORKLOAD RESTORATION
    # ==================================================
    print("\nPHASE 4: Restoring production workloads to initial states...")
    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            kube.scale_replicas(
                info["resource_type"],
                info["resource_name"],
                replicas=info["initial_replicas"]
            )
            print(f"  info: {resource_key} restored to {info['initial_replicas']} replicas.")

    print(f"\n==================================================")
    print(f"Sequential orchestration process finished successfully.")
    print(f"==================================================")


def main():
    global kube_client_sigkill

    # SIGINT = CTRL+C (CLI)
    signal.signal(signal.SIGINT, restore_production_on_signal)

    # SIGTERM = Kubernetes Pod killed
    signal.signal(signal.SIGTERM, restore_production_on_signal)


    parser = argparse.ArgumentParser(description="Backup Kubernetes volumes")
    parser.add_argument("-c", "--context", type=str, default=None, help="Local kubectl context")
    parser.add_argument("-n", "--namespace", type=str, default=None, help="Kubernetes namespace")
    parser.add_argument("-j", "--job", action="store_true", help="Run a backup job")
    parser.add_argument("-m", "--map", action="store_true", help="Get the backup map")
    args = parser.parse_args()


    cluster_name_env = os.getenv("CLUSTER_NAME")
    namespace_env = os.getenv("NAMESPACE")

    cluster_name = cluster_name_env if cluster_name_env else args.context
    namespace = namespace_env if namespace_env else args.namespace


    # for key, value in os.environ.items():
    #     print('{}: {}'.format(key, value))


    kube = Kubernetes(context=cluster_name, namespace=namespace)
    kube_client_sigkill = kube

    if args.job:
        run_backup_job(kube)

    elif args.map:
        get_backup_map(kube)

    else:
        print('please run with --job or --map (--help for more info)')


if __name__ == "__main__":
    sys.exit(main())