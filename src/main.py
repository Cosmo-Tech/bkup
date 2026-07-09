import sys
import argparse
import time
import signal
from src.kubernetes import Kubernetes




# Global dictionary to keep track of initial states across functions
backup_workloads = {}
kubernetes_client = None

def restore_production_on_signal(signum, frame):
    """
    Emergency signal handler. Triggers an immediate rollback to initial 
    replica states if the orchestrator is interrupted (CTRL+C or SIGTERM).
    """
    print(f"\nwarn: termination signal ({signum}) received! starting emergency rollback...", flush=True)
    
    if not backup_workloads or not kubernetes_client:
        print("info: no workload state captured yet. exiting safely.", flush=True)
        sys.exit(0)

    # Emergency loop to bring everything back up
    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            print(f"  info: emergency restoring {resource_key} to {info['initial_replicas']} replicas...", flush=True)
            kubernetes_client.scale_replicas(
                info["resource_type"], 
                info["resource_name"], 
                replicas=info["initial_replicas"]
            )
            
    print("info: emergency rollback finished. exiting.", flush=True)
    sys.exit(1)



def get_backup_map(kubernetes: Kubernetes):
    """
    Get a YAML map of what will be backuped
    """
    current_ns = kubernetes.get_namespace()

    print(f"context: {kubernetes.get_context()}")
    print(f"namespaces:")
    print(f"- name: {current_ns}")

    pvcs = kubernetes.list_pvc()
    if not pvcs:
        print("error: no PVC found or unauthorized access.")
        return 0

    print(f"  claims:")
    for pvc in kubernetes.list_pvc():
        print(f"    - name: {pvc['name']}")
        print(f"      status: {pvc['status']}")
        print(f"      capacity: {pvc['capacity']}")

        mounts = kubernetes.get_pvc(pvc['name'])
        if mounts:
            print(f"      mounts:")
            for m in mounts:
                print(f"      - container: {m['container_name']}")
                print(f"        path: {m['mount_path']}")
                print(f"        read_only: {m['read_only']}")
                print(f"        resource: {m['resource']}")
        else:
            print(f"      mounts: {mounts}")


def run_backup_job(kubernetes: Kubernetes):
    """
    Global sequential orchestrator:
    Phase 0: Cleanup + wait for deletion of old Jobs
    Phase 1: Gather configuration + Trigger Scale Down (0)
    Phase 2: Wait for complete production workload termination
    Phase 3: Trigger all new backup Jobs concurrently via K8s control plane
    Phase 4: Restore (Scale Up) production workloads to original layout states
    """
    get_backup_map(kubernetes)

    pvcs = kubernetes.list_pvc()
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
        kubernetes.delete_backup_job(pvc_name=pvc_name)
    
    if not kubernetes.wait_for_jobs_deletion(pvc_list=pvc_list):
        print("  warn: Some old jobs might still be terminating. Proceeding anyway...")

    backup_workloads = {}

    # ==================================================
    # PHASE 1 : WORKLOAD ASSESSMENT AND SCALE DOWN
    # ==================================================
    print("\nPHASE 1: Capturing states and scaling down workloads...")
    for pvc in pvcs:
        pvc_name = pvc['name']
        targets = kubernetes.get_pvc(pvc_name)
        if not targets:
            print(f"  warn: No active workload config found for {pvc_name}. Skipping.")
            continue

        target = targets[0]
        resource_key = target['resource']
        resource_type, resource_name = resource_key.split('/')
        mounted_path = target['mount_path']

        if resource_key not in backup_workloads:
            initial_replicas = kubernetes.get_replicas(resource_type, resource_name)
            
            backup_workloads[resource_key] = {
                "resource_type": resource_type,
                "resource_name": resource_name,
                "initial_replicas": initial_replicas,
                "pvc_name": pvc_name,
                "mounted_path": mounted_path
            }

            if initial_replicas > 0:
                kubernetes.scale_replicas(resource_type, resource_name, replicas=0)
            else:
                print(f"  info: {resource_key} is already stopped (Night mode).")

    # ==================================================
    # PHASE 2 : WAIT FOR COMPLETE EXCLUSIVITY
    # ==================================================
    print("\nPHASE 2: Waiting for all workloads to be fully stopped...")
    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            kubernetes.wait_for_scale_down(info["resource_type"], info["resource_name"])

    # ==================================================
    # PHASE 3 : TRIGGER CONCURRENT BACKUPS
    # ==================================================
    print("\nPHASE 3: Triggering all Kubernetes backup jobs simultaneously...")
    for resource_key, info in backup_workloads.items():
        kubernetes.create_backup_job(pvc_name=info["pvc_name"], mounted_path=info["mounted_path"])

    # Wait for backup to end before scale back replicas
    kubernetes.wait_for_job_completion(job_name=kubernetes.get_job_name(pvc_name))


    # ==================================================
    # PHASE 4 : PRODUCTION WORKLOAD RESTORATION
    # ==================================================
    print("\nPHASE 4: Restoring production workloads to initial states...")
    for resource_key, info in backup_workloads.items():
        if info["initial_replicas"] > 0:
            kubernetes.scale_replicas(
                info["resource_type"], 
                info["resource_name"], 
                replicas=info["initial_replicas"]
            )
            print(f"  info: {resource_key} restored to {info['initial_replicas']} replicas.")

    print(f"\n==================================================")
    print(f"Sequential orchestration process finished successfully.")
    print(f"==================================================")


def main():
    global kubernetes_client
    
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

    kubernetes = Kubernetes(context=args.context, namespace=args.namespace)
    
    if args.job:
        run_backup_job(kubernetes)

    elif args.map:
        get_backup_map(kubernetes)

    else:
        print('please run with --job or --map (--help for more info)')


if __name__ == "__main__":
    sys.exit(main())