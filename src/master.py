import os
import sys
import argparse
import signal
import yaml

from src.kubernetes.base import KubernetesBase
from src.kubernetes.job_volume import VolumeBackupJob
from src.kubernetes.job_postgresql import PostgresBackupJob
import src.utils.psql as psql
import src.utils.backup_map as backup_map
import src.utils.backup_cache as backup_cache
import src.utils.config_secret as config_secret
import src.utils.general as general_helper


backup_workloads = {}
kube_client_sigkill = None


def restore_workloads_from_state(kube_client, master_ns, backup_workloads_map):
    """Restore scaled down replicas to their original quantity."""
    orig_ns = kube_client.namespace
    try:
        kube_client.namespace = master_ns
        cache_data = backup_cache.get_backup_cache(kube_client)
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
                print(f"  info: Restoring {resource_key} from {current_replicas} to {info['initial_replicas']} replicas...")
                kube_client.scale_replicas(info["resource_type"], info["resource_name"], replicas=info["initial_replicas"])
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
                        print(f"  info: Restoring {r_type}/{r_name} in namespace {ns_name} from {current_replicas} to {initial_replicas} replicas...")
                        kube_client.scale_replicas(r_type, r_name, replicas=initial_replicas)
                    except Exception as e:
                        print(f"  error: failed to restore {r_type}/{r_name} from cache data: {e}")

    # Purge technical cache ConfigMap
    backup_cache.clear_backup_cache(kube_client, master_ns)


def restore_production_on_signal(signum, frame):
    """Rollback scaled down replicas in case of interruption signal."""
    print(f"\n\nTERMINATION SIGNAL ({signum}) RECEIVED")
    print("Rollbacking replicas...")

    if not kube_client_sigkill:
        print("info: No kubernetes client available. exiting.")
        sys.exit(1)

    master_ns = config_secret.get_config_secret_namespace(kube_client_sigkill)
    restore_workloads_from_state(kube_client_sigkill, master_ns, backup_workloads)

    print("info: Rollback finished. exiting.")
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

    master_ns = config_secret.get_config_secret_namespace(kube)

    # --------------------------------------------------
    # STRICT BACKUP-MAP CHECK
    # --------------------------------------------------
    ns_claims_entries = []
    backup_map_config = backup_map.get_backup_map_content(kube, master_ns=master_ns)

    if isinstance(backup_map_config, dict) and "namespaces" in backup_map_config:
        for ns_entry in backup_map_config.get("namespaces", []):
            if ns_entry.get("name") == namespace:
                ns_claims_entries = ns_entry.get("claims", []) or []
                break

    if not ns_claims_entries:
        print(f"info: No claim configured in backup-map for namespace '{namespace}' (or backup-map missing). Skipping backup.")
        return

    # List PVCs configured in backup-map
    configured_pvc_names = {claim.get("name") for claim in ns_claims_entries if claim.get("name")}

    pvcs = [p for p in kube.list_pvc() if p["name"] in configured_pvc_names]
    if not pvcs:
        print(f"info: No matching PVC found in cluster for configured claims in namespace '{namespace}'. Skipping.")
        return

    active_nodes = get_active_cluster_nodes(kube)

    starting_time_ns = general_helper.now()
    print("\n\n==================================================")
    print(f"===> Starting namespace : {namespace} | datetime: {starting_time_ns}")

    # ==================================================
    # PHASE 0 : CLEANUP OLD JOBS
    # ==================================================
    print("=> Cleaning existing backups jobs...")
    pvc_list = []

    supported_workers = [VolumeBackupJob, PostgresBackupJob]
    for pvc in pvcs:
        pvc_name = pvc["name"]
        pvc_list.append(pvc_name)
        for worker_cls in supported_workers:
            kube.delete_backup_job(pvc_name=pvc_name, suffix=worker_cls.suffix)

    if not kube.wait_for_jobs_deletion(pvc_list=pvc_list):
        print("  warn: Some old jobs might still be terminating. Proceeding anyway...")

    backup_workloads.clear()

    orig_namespace = kube.namespace
    try:
        kube.namespace = master_ns
        cache_data = backup_cache.get_backup_cache(kube)
    finally:
        kube.namespace = orig_namespace

    # try...finally to catch all Python crashes => To be able to restore scaled down replicas
    try:
        # ==================================================
        # PHASE 1 : WORKLOAD ASSESSMENT AND SCALE DOWN
        # ==================================================
        print("=> Capturing states and scaling down workloads...")
        for pvc in pvcs:
            pvc_name = pvc["name"]
            targets = kube.get_pvc(pvc_name)
            if not targets:
                print(f"  warn: No active workload config found for {pvc_name}. Skipping.")
                continue

            target = targets[0]
            resource_key = f"{namespace}/{target['resource']}"
            resource_type, resource_name = target["resource"].split("/")
            mounted_path = target["mount_path"]

            mount_info = {"container": resource_name, "resource": resource_type}
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
                                print(f"  info: Attachment-node '{raw_node}' for PVC '{pvc_name}' does not exist in cluster. Ignoring node selector.")
                                node_name = ""
                            else:
                                node_name = raw_node
                                print(f"  info: PVC '{pvc_name}' mapped to node '{node_name}' from backup-map.")
                        break

                cached_val = backup_cache.find_cached_replicas(cache_data, namespace, resource_type, resource_name)
                real_replicas = kube.get_replicas(resource_type, resource_name)

                if cached_val is not None and cached_val > 0:
                    initial_replicas = cached_val
                    print(f"  info: Found cached replica count for {resource_key}: {initial_replicas}")
                else:
                    initial_replicas = real_replicas
                    if should_scale and initial_replicas > 0:
                        backup_cache.update_cached_replicas(cache_data, namespace, resource_type, resource_name, initial_replicas)

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
                    "is_postgres": is_postgres,
                }

        backup_cache.save_backup_cache(kube, cache_data, master_ns)

        # Scale down workloads
        for resource_key, info in backup_workloads.items():
            if info["should_scale"]:
                if info["initial_replicas"] > 0 or info["current_real_replicas"] > 0:
                    kube.scale_replicas(info["resource_type"], info["resource_name"], replicas=0)
                else:
                    print(f"  info: Workload {resource_key} is already stopped.")
            else:
                print(f"  info: Workload {resource_key} is PostgreSQL — leaving replicas untouched ({info['initial_replicas']}) for now.")

        # ==================================================
        # PHASE 2 : WAIT FOR COMPLETE EXCLUSIVITY
        # ==================================================
        print("=> Waiting for scaled-down workloads to stop...")
        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace and info["should_scale"]:
                if info["initial_replicas"] > 0 or info["current_real_replicas"] > 0:
                    kube.wait_for_scale_down(info["resource_type"], info["resource_name"])

        print("=> Ensure PVC are not attached anymore...")
        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace and info["should_scale"]:
                if info["initial_replicas"] > 0 or info["current_real_replicas"] > 0:
                    kube.wait_for_volume_detachment(info["pvc_name"])

        # ==================================================
        # PHASE 3 : TRIGGER CONCURRENT BACKUPS
        # ==================================================
        print("=> Starting all Kubernetes backup jobs...")
        active_jobs_to_track = []

        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace:
                pull_secrets = kube.get_image_pull_secrets(info["resource_type"], info["resource_name"])

                if info["is_postgres"]:
                    print(f"  info: Workload {resource_key} identified as PostgreSQL.")

                    current_replicas = kube.get_replicas(info["resource_type"], info["resource_name"])
                    if current_replicas == 0:
                        # If stopped initially (e.g. night schedule), scale to 1. If cached > 0, restore that count.
                        target_count = info["initial_replicas"] if info["initial_replicas"] > 0 else 1

                        print(f"  info: Workload {resource_key} is currently stopped (0 replicas). Scaling UP to {target_count} for pg_dump...")
                        kube.scale_replicas(info["resource_type"], info["resource_name"], replicas=target_count)
                        kube.wait_for_scale_up(info["resource_type"], info["resource_name"], target_replicas=target_count)

                    worker = PostgresBackupJob(context=cluster_context, namespace=namespace)
                    target_ip = kube.get_pod_ip(
                        resource_type=info["resource_type"],
                        resource_name=info["resource_name"],
                        pvc_name=info["pvc_name"],
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
                        suffix=worker.suffix,
                    )
                else:
                    print(f"  info: Workload {resource_key} identified as standard volume. Routing via standard worker...")
                    worker = VolumeBackupJob(context=cluster_context, namespace=namespace)
                    worker.create_backup_job(
                        pvc_name=info["pvc_name"],
                        mounted_path=info["mounted_path"],
                        node_name=info["node_name"],
                        image_pull_secrets=pull_secrets,
                        master_ns=master_ns,
                        suffix=worker.suffix,
                    )

                # Calculate the exact job name by asking the worker for its class suffix
                job_name = kube.get_job_name(info["pvc_name"], suffix=worker.suffix)
                active_jobs_to_track.append(job_name)

        # Wait for backup to end before scale back replicas
        print("=> Waiting for backup jobs to complete before scaling back up...")
        for job_to_wait in active_jobs_to_track:
            kube.wait_for_job_completion(job_name=job_to_wait)
            # kube.wait_for_job_completion(job_name=job_to_wait, timeout=30)

    finally:
        # ==================================================
        # PHASE 4 : AUTOMATIC RESTORATION (CRASH PROOF)
        # ==================================================
        if backup_workloads:
            print("=> Executing safety restoration check on production workloads...")
            restore_workloads_from_state(kube, master_ns, backup_workloads)

    finished_time_ns = general_helper.now()
    print(f"\n===> Finished namespace : {namespace} | datetime: {finished_time_ns} | duration: {general_helper.datetime_difference(starting_time_ns, finished_time_ns)}")
    print("==================================================\n\n")


def main():
    # ----------------------------------
    # Restore workload if killed
    # ----------------------------------
    global kube_client_sigkill

    # SIGINT = CTRL+C (CLI)
    signal.signal(signal.SIGINT, restore_production_on_signal)

    # SIGTERM = Kubernetes Pod killed
    signal.signal(signal.SIGTERM, restore_production_on_signal)


    # ----------------------------------
    # Backup job
    # ----------------------------------
    # Reject if kube context if missing
    try:
        cluster_context = os.environ["CLUSTER_CONTEXT"]
        target_namespaces = os.environ["TARGET_NAMESPACES"]

        kube = KubernetesBase(context=cluster_context, namespace=namespace)
        kube_client_sigkill = kube
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)

    # master_ns = config_secret.get_config_secret_namespace(kube)
    # kube.namespace = master_ns

    # backup_map_name = backup_map.get_backup_map_name
    # backup_map_ns = master_ns

    # # Verify ConfigMap existence before processing
    # saved_map = backup_map.get_backup_map_content(kube, namespace=backup_map_ns)
    # if not saved_map or not saved_map.get("namespaces"):
    #     print(f"error: ConfigMap '{backup_map_name}' is missing or empty in namespace '{backup_map_ns}'.")
    #     print("hint: Generate and apply one first using 'bkup -bm -a'")
    #     sys.exit(1)

    # print(f"{yaml.dump(saved_map, sort_keys=False, default_flow_style=False)}\n")

    # If a single namespace is targeted via -n, execute only this one
    if namespace_input:
        target_namespaces = [namespace_input.strip()]
    else:
        target_namespaces = kube.list_namespaces()

    starting_time_job = general_helper.now()
    print(f"info: Starting backup job at {starting_time_job}")

    for ns in target_namespaces:
        kube.namespace = ns
        run_backup_job(cluster_context, namespace=ns)

    finished_time_job = general_helper.now()
    print(f"info: Finished backup job at {finished_time_job}")
    print(f"info: Full job duration is {general_helper.datetime_difference(start_time=starting_time_job, end_time=general_helper.now())}")



if __name__ == "__main__":
    sys.exit(main())
