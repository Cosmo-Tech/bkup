import sys
import argparse
import signal
import os
import yaml

from src.kubernetes.job_volume import VolumeBackupJob
from src.kubernetes.job_postgresql import PostgresBackupJob
import src.utils.psql as psql
import src.utils.backup_map as bpm
import src.utils.backup_cache as bpc

backup_workloads = {}
kube_client_sigkill = None


def get_secret_config_namespace(kube_client) -> str:
    """Find the namespace containing the 'bkup-config' Secret."""
    for ns in kube_client.list_namespaces():
        try:
            if kube_client.get_secret_payload("bkup-config", namespace=ns):
                return ns
        except Exception:
            continue

    raise RuntimeError("Error: Secret 'bkup-config' not found in any namespace.")


def restore_workloads_from_state(kube_client, master_ns, backup_workloads_map):
    """Restore scaled down replicas to their original quantity."""
    orig_ns = kube_client.namespace
    try:
        kube_client.namespace = master_ns
        cache_data = bpc.get_backup_cache(kube_client)
    except Exception:
        cache_data = {"namespaces": []}
    finally:
        kube_client.namespace = orig_ns

    cache_namespaces = cache_data.get("namespaces", [])

    # Delete residual backup jobs
    supported_workers = [VolumeBackupJob, PostgresBackupJob]
    for resource_key, info in backup_workloads_map.items():
        for worker_cls in supported_workers:
            try:
                kube_client.namespace = info["namespace"]
                kube_client.delete_backup_job(pvc_name=info["pvc_name"], suffix=worker_cls.suffix)
            except Exception:
                pass

    # Restore workload replicas from in-memory assessment
    if backup_workloads_map:
        for resource_key, info in backup_workloads_map.items():
            kube_client.namespace = info["namespace"]
            current_replicas = kube_client.get_replicas(info["resource_type"], info["resource_name"])

            if current_replicas != info["initial_replicas"]:
                print(f"  info: [RESTORE] restoring {resource_key} from {current_replicas} to {info['initial_replicas']} replicas...")
                kube_client.scale_replicas(
                    info["resource_type"],
                    info["resource_name"],
                    replicas=info["initial_replicas"]
                )
    # Fallback restoration using persisted backup cache ConfigMap
    elif cache_namespaces:
        for ns_item in cache_namespaces:
            ns_name = ns_item.get("name")
            for res in ns_item.get("resources", []):
                r_type = res.get("type")
                r_name = res.get("name")
                initial_replicas = res.get("replicas", 0)

                kube_client.namespace = ns_name
                current_replicas = kube_client.get_replicas(r_type, r_name)
                if current_replicas != initial_replicas:
                    try:
                        print(f"  info: [RESTORE CACHE] restoring {r_type}/{r_name} in namespace {ns_name} from {current_replicas} to {initial_replicas} replicas...")
                        kube_client.scale_replicas(r_type, r_name, replicas=initial_replicas)
                    except Exception as e:
                        print(f"  error: failed to restore {r_type}/{r_name} from cache data: {e}")

    # Purge technical cache ConfigMap
    bpc.clear_backup_cache(kube_client, master_ns)


def restore_production_on_signal(signum, frame):
    """Rollback scaled down replicas in case of interruption signal."""
    print(f"\nwarn: termination signal ({signum}) received! rollbacking replicas...")

    if not kube_client_sigkill:
        print("info: no kubernetes client available. exiting.")
        sys.exit(1)

    master_ns = get_secret_config_namespace(kube_client_sigkill)
    restore_workloads_from_state(kube_client_sigkill, master_ns, backup_workloads)

    print("info: rollback finished. exiting.")
    sys.exit(1)


def get_active_cluster_nodes(kube) -> set:
    """Fetch all active cluster node names to validate node existence."""
    try:
        nodes = kube.core_v1.list_node()
        return {node.metadata.name for node in nodes.items}
    except Exception as e:
        print(f"  warn: failed to list active cluster nodes: {e}")
        return set()


def run_backup_job(cluster_context: str, namespace: str):
    """
    Global sequential orchestrator:
    Phase 0: Cleanup + wait for deletion of old Jobs
    Phase 1: Gather configuration from backup-map + Trigger Scale Down (0)
    Phase 2: Wait for complete production workload termination
    Phase 3: Trigger all new backup Jobs concurrently via K8s control plane
    Phase 4: Restore (Scale Up/Down) production workloads to original layout states
    """
    global backup_workloads
    global kube_client_sigkill

    kube = VolumeBackupJob(context=cluster_context, namespace=namespace)
    kube_client_sigkill = kube

    pvcs = kube.list_pvc()

    # Skip if no PVC
    if not pvcs:
        return

    master_ns = get_secret_config_namespace(kube)
    active_nodes = get_active_cluster_nodes(kube)

    # Fetch backup-map ConfigMap for current namespace claims definition
    ns_claims_entries = []
    try:
        backup_map_config = bpm.get_backup_map(kube, master_ns=master_ns)
        if isinstance(backup_map_config, dict) and "namespaces" in backup_map_config:
            for ns_entry in backup_map_config.get("namespaces", []):
                if ns_entry.get("name") == namespace:
                    ns_claims_entries = ns_entry.get("claims", []) or []
                    break
    except Exception as e:
        print(f"  warn: could not load backup-map claims for namespace '{namespace}': {e}")

    print("\n==================================================")
    print(f" STARTING BACKUPS FOR NAMESPACE {namespace}")
    print("==================================================")

    # ==================================================
    # PHASE 0 : CLEANUP OLD JOBS
    # ==================================================
    print("\nPHASE 0: Cleaning existing backups jobs...")
    pvc_list = []

    supported_workers = [VolumeBackupJob, PostgresBackupJob]
    for pvc in pvcs:
        pvc_name = pvc['name']
        pvc_list.append(pvc_name)
        for worker_cls in supported_workers:
            kube.delete_backup_job(pvc_name=pvc_name, suffix=worker_cls.suffix)

    if not kube.wait_for_jobs_deletion(pvc_list=pvc_list):
        print("  warn: Some old jobs might still be terminating. Proceeding anyway...")

    backup_workloads.clear()

    orig_namespace = kube.namespace
    try:
        kube.namespace = master_ns
        cache_data = bpc.get_backup_cache(kube)
    finally:
        kube.namespace = orig_namespace

    # try...finally to catch all Python crashes => To be able to restore scaled down replicas
    try:
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

            mount_info = {
                "container": resource_name,
                "resource": resource_type
            }
            is_postgres = psql.is_postgresql_workload(mount_info)
            should_scale = not is_postgres

            if resource_key not in backup_workloads:
                # 1. Read attachment-node from backup-map ConfigMap claims
                node_name = ""
                for claim in ns_claims_entries:
                    if claim.get("name") == pvc_name:
                        raw_node = claim.get("attachment-node") or claim.get("attachment_node") or ""

                        # 2. Safety check: Verify node existence in cluster
                        if raw_node:
                            if active_nodes and raw_node not in active_nodes:
                                print(f"  warn: Attachment-node '{raw_node}' for PVC '{pvc_name}' does not exist in cluster. Ignoring node selector.")
                                node_name = ""
                            else:
                                node_name = raw_node
                                print(f"  info: PVC '{pvc_name}' mapped to node '{node_name}' from backup-map.")
                        break

                cached_val = bpc.find_cached_replicas(cache_data, namespace, resource_type, resource_name)
                real_replicas = kube.get_replicas(resource_type, resource_name)

                if cached_val is not None and cached_val > 0:
                    initial_replicas = cached_val
                    print(f"  info: Found cached replica count for {resource_key}: {initial_replicas}")
                else:
                    initial_replicas = real_replicas
                    if should_scale and initial_replicas > 0:
                        bpc.update_cached_replicas(cache_data, namespace, resource_type, resource_name, initial_replicas)

                backup_workloads[resource_key] = {
                    "namespace": namespace,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "initial_replicas": initial_replicas,
                    "current_real_replicas": real_replicas,
                    "pvc_name": pvc_name,
                    "mounted_path": mounted_path,
                    "node_name": node_name,
                    "should_scale": should_scale,
                    "is_postgres": is_postgres
                }

        bpc.save_backup_cache(kube, cache_data, master_ns)

        # Scale down workloads
        for resource_key, info in backup_workloads.items():
            if info["should_scale"]:
                if info["initial_replicas"] > 0 or info["current_real_replicas"] > 0:
                    kube.scale_replicas(info["resource_type"], info["resource_name"], replicas=0)
                else:
                    print(f"  info: {resource_key} is already stopped.")
            else:
                print(f"  info: {resource_key} is PostgreSQL — leaving replicas untouched ({info['initial_replicas']}) for now.")

        # ==================================================
        # PHASE 2 : WAIT FOR COMPLETE EXCLUSIVITY
        # ==================================================
        print("\nPHASE 2: Waiting for scaled-down workloads to stop...")
        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace and info["should_scale"] and (info["initial_replicas"] > 0 or info["current_real_replicas"] > 0):
                kube.wait_for_scale_down(info["resource_type"], info["resource_name"])

        print("\nensure PVC are not attached anymore...")
        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace and info["should_scale"]:
                kube.wait_for_volume_detachment(info["pvc_name"])

        # ==================================================
        # PHASE 3 : TRIGGER CONCURRENT BACKUPS
        # ==================================================
        print("\nPHASE 3: Triggering all Kubernetes backup jobs simultaneously...")
        active_jobs_to_track = []

        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace:
                pull_secrets = kube.get_image_pull_secrets(info["resource_type"], info["resource_name"])

                if info["is_postgres"]:
                    print(f"  info: {resource_key} identified as PostgreSQL database.")

                    current_replicas = kube.get_replicas(info["resource_type"], info["resource_name"])
                    if current_replicas == 0:
                        # If stopped initially (e.g. night schedule), scale to 1. If cached > 0, restore that count.
                        target_count = info["initial_replicas"] if info["initial_replicas"] > 0 else 1
                        
                        print(f"  info: {resource_key} is currently stopped (0 replicas). Scaling UP to {target_count} for pg_dump...")
                        kube.scale_replicas(info["resource_type"], info["resource_name"], replicas=target_count)
                        kube.wait_for_scale_up(info["resource_type"], info["resource_name"], target_replicas=target_count)

                    worker = PostgresBackupJob(context=cluster_context, namespace=namespace)
                    target_ip = kube.get_pod_ip(
                        resource_type=info["resource_type"],
                        resource_name=info["resource_name"],
                        pvc_name=info["pvc_name"]
                    )

                    worker.create_backup_job(
                        pvc_name=info["pvc_name"],
                        mounted_path=info["mounted_path"],
                        node_name=info["node_name"],
                        workload_type=info["resource_type"],
                        workload_name=info["resource_name"],
                        target_ip=target_ip,
                        image_pull_secrets=pull_secrets,
                        master_ns=master_ns,
                        suffix=worker.suffix
                    )
                else:
                    print(f"  info: {resource_key} identified as standard volume. Routing via standard worker...")
                    worker = VolumeBackupJob(context=cluster_context, namespace=namespace)
                    worker.create_backup_job(
                        pvc_name=info["pvc_name"],
                        mounted_path=info["mounted_path"],
                        node_name=info["node_name"],
                        image_pull_secrets=pull_secrets,
                        master_ns=master_ns,
                        suffix=worker.suffix
                    )

                # Calculate the exact job name by asking the worker for its class suffix
                job_name = kube.get_job_name(info["pvc_name"], suffix=worker.suffix)
                active_jobs_to_track.append(job_name)

        # Wait for backup to end before scale back replicas
        print("\nWaiting for backup jobs to complete before scaling back up...")
        for job_to_wait in active_jobs_to_track:
            kube.wait_for_job_completion(job_name=job_to_wait)
            # kube.wait_for_job_completion(job_name=job_to_wait, timeout=30)

    finally:
        # ==================================================
        # PHASE 4 : AUTOMATIC RESTORATION (CRASH PROOF)
        # ==================================================
        print("\nPHASE 4: Executing safety restoration check on production workloads...")
        restore_workloads_from_state(kube, master_ns, backup_workloads)

    print("\n==================================================")
    print(f" BACKUP PROCESS CONCLUDED FOR NAMESPACE {namespace}")
    print("==================================================")


def main():
    global kube_client_sigkill

    # SIGINT = CTRL+C (CLI)
    signal.signal(signal.SIGINT, restore_production_on_signal)

    # SIGTERM = Kubernetes Pod killed
    signal.signal(signal.SIGTERM, restore_production_on_signal)

    parser = argparse.ArgumentParser(
        description="Backup Kubernetes volumes",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=80)
    )

    # Global options
    global_group = parser.add_argument_group("global")
    global_group.add_argument("-c", "--context", type=str, metavar="CT", default=None, help="Local kubectl context")
    global_group.add_argument("-n", "--namespace", type=str, metavar="NS", default=None, help="Target a namespace for backup job run")
    global_group.add_argument("-j", "--job", action="store_true", help="Run backup job(s) based on current backup-map")
    global_group.add_argument("-bm", "--backup-map", action="store_true", help="Manage backup-map configuration")

    # Backup-map sub-options
    map_group = parser.add_argument_group("backup-map")
    map_group.add_argument("-p", "--preview", action="store_true", help="Preview new backup-map")
    map_group.add_argument("-a", "--apply", action="store_true", help="Apply new backup-map")
    map_group.add_argument("-g", "--get-current", action="store_true", help="Get current saved backup-map")

    args = parser.parse_args()

    cluster_context = args.context
    namespace_input = args.namespace

    # Strict validation of -bm sub-options usage
    if (args.apply or args.preview or args.get_current) and not args.backup_map:
        print("error: --preview (-p), --apply (-a), and --get-current (-g) can only be used with --backup-map (-bm).")
        sys.exit(1)

    # Reject if kube context if missing
    try:
        kube = VolumeBackupJob(context=cluster_context, namespace="default")
        kube_client_sigkill = kube
    except ValueError as e:
        if not args.job and not args.backup_map:
            print("error: please use --help for usage instructions")
            sys.exit(1)
        print(f"error: {e}")
        sys.exit(1)

    master_ns = get_secret_config_namespace(kube)

    if args.job:
        kube.namespace = master_ns
        try:
            bpm.get_backup_map(kube, master_ns=master_ns)
        except Exception as e:
            print(f"error: could not retrieve 'bkup-backup-map' ConfigMap in namespace '{master_ns}': {e}")
            print("hint: generate and apply one first using 'bkup -bm -a'")
            sys.exit(1)

        # If a single namespace is targeted via -n, execute only this one
        if namespace_input:
            target_namespaces = [namespace_input.strip()]
        else:
            target_namespaces = kube.list_namespaces()

        for ns in target_namespaces:
            kube.namespace = ns
            run_backup_job(cluster_context, namespace=ns)

    elif args.backup_map:
        # Incompatibility: -g cannot be combined with -p or -a
        if args.get_current and (args.preview or args.apply):
            print("error: --get-current (-g) cannot be combined with scanning options (-p or -a).")
            sys.exit(1)

        # 1. ACTION: Read existing map in K8s (-g)
        if args.get_current:
            try:
                saved_map = bpm.get_backup_map(kube, master_ns=master_ns)
                print(yaml.dump(saved_map, sort_keys=False, default_flow_style=False))
            except Exception as e:
                print(f"error: failed to retrieve backup-map from namespace '{master_ns}': {e}")
                sys.exit(1)

        # 2. ACTION: Scan ENTIRE cluster (-p / -a)
        elif args.preview or args.apply:
            all_cluster_namespaces = kube.list_namespaces()
            backup_map = bpm.backup_map_scanner(kube, all_cluster_namespaces)

            if args.apply:
                kube.save_to_configmap(name="bkup-backup-map", namespace=master_ns, data=backup_map)
                print(f"info: backup-map successfully applied and saved to ConfigMap in namespace '{master_ns}'.")
            else:
                print(yaml.dump(backup_map, sort_keys=False, default_flow_style=False))

        # 3. ACTION: -bm alone -> Error message
        else:
            print("error: --backup-map (-bm) requires an action option (-g/--get-current, -p/--preview, or -a/--apply).")
            print("examples:")
            print("  bkup -bm -g        # Read saved backup-map from Kubernetes")
            print("  bkup -bm -p        # Preview full cluster backup-map")
            print("  bkup -bm -a        # Scan cluster and save backup-map to ConfigMap")
            sys.exit(1)

    else:
        print('error: please use --help for usage instructions')


if __name__ == "__main__":
    sys.exit(main())
