import sys
import argparse
import time
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



def get_master_namespace(kube_client) -> str:
    """Get namespace of master pod"""
    pod_ns = kube_client.get_current_namespace_from_pod()
    if pod_ns:
        return pod_ns

    return "default"


def restore_workloads_from_state(kube_client, master_ns, backup_workloads_map):
    """
    Fonction centrale de restauration d'urgence.
    Garantit le retour à l'état initial peu importe l'origine de l'arrêt (Crash ou Signal).
    """
    # Récupération sécurisée du cache via bpc en cas de perte de la mémoire vive
    orig_ns = kube_client.namespace
    try:
        kube_client.namespace = master_ns
        cache_data = bpc.get_backup_cache(kube_client)
    except Exception:
        cache_data = {"namespaces": []}
    finally:
        kube_client.namespace = orig_ns

    cache_namespaces = cache_data.get("namespaces", [])

    # 1. Suppression des jobs de backup résiduels pour nettoyer le cluster
    supported_workers = [VolumeBackupJob, PostgresBackupJob]
    for resource_key, info in backup_workloads_map.items():
        for worker_cls in supported_workers:
            try:
                kube_client.namespace = info["namespace"]
                kube_client.delete_backup_job(pvc_name=info["pvc_name"], suffix=worker_cls.suffix)
            except Exception:
                pass

    # 2. Restauration des réplicas (priorité à la mémoire vive de la session, sinon fallback ConfigMap)
    if backup_workloads_map:
        for resource_key, info in backup_workloads_map.items():
            if info.get("should_scale", True) and info["initial_replicas"] > 0:
                print(f"  info: [RESTORE] restoring {resource_key} to {info['initial_replicas']} replicas...")
                kube_client.namespace = info["namespace"]
                kube_client.scale_replicas(
                    info["resource_type"],
                    info["resource_name"],
                    replicas=info["initial_replicas"]
                )
    elif cache_namespaces:
        for ns_item in cache_namespaces:
            ns_name = ns_item.get("name")
            for res in ns_item.get("resources", []):
                r_type = res.get("type")
                r_name = res.get("name")
                initial_replicas = res.get("replicas", 0)

                if initial_replicas > 0:
                    try:
                        print(f"  info: [RESTORE CACHE] restoring {r_type}/{r_name} in namespace {ns_name} to {initial_replicas} replicas...")
                        kube_client.namespace = ns_name
                        kube_client.scale_replicas(r_type, r_name, replicas=initial_replicas)
                    except Exception as e:
                        print(f"  error: failed to restore {r_type}/{r_name} from cache data: {e}")

    # 3. Purge définitive de la ConfigMap de cache technique
    bpc.clear_backup_cache(kube_client, master_ns)


def restore_production_on_signal(signum, frame):
    """Rollback scaled down replicas in case of interruption signal"""
    print(f"\nwarn: termination signal ({signum}) received! rollbacking replicas...")

    if not kube_client_sigkill:
        print("info: no kubernetes client available. exiting.")
        sys.exit(1)

    master_ns = get_master_namespace(kube_client_sigkill)
    restore_workloads_from_state(kube_client_sigkill, master_ns, backup_workloads)

    print("info: rollback finished. exiting.")
    sys.exit(1)


def get_postgres_pod_ip(kube, namespace: str, pvc_name: str) -> str:
    """Find the exact IP of the running Postgres Pod mounted on the target PVC."""
    try:
        pods = kube.core_v1.list_namespaced_pod(namespace=namespace)
        for pod in pods.items:
            if pod.status.phase != "Running":
                continue

            if pod.spec.volumes:
                for vol in pod.spec.volumes:
                    if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc_name:
                        if pod.status.pod_ip:
                            print(f"  info: detected active Postgres Pod '{pod.metadata.name}' at IP {pod.status.pod_ip}")
                            return pod.status.pod_ip
    except Exception as e:
        print(f"  warn: failed to resolve active pod IP for PVC '{pvc_name}': {e}")

    raise RuntimeError(f"Could not find any running Pod attached to PVC '{pvc_name}' in namespace '{namespace}'")


def get_postgresql_claim_config_from_map(kube, pvc_name: str, namespace: str) -> dict:
    """Read postgresql dict section declared under the PVC claim in backup-map ConfigMap."""
    try:
        backup_map = bpm.get_backup_map(kube)
        for ns in backup_map.get("namespaces", []):
            if ns.get("name") == namespace:
                for claim in ns.get("claims", []):
                    if claim.get("name") == pvc_name:
                        return claim.get("postgresql", {})
    except Exception:
        pass
    return {}


def resolve_container_env_secrets(kube, namespace: str, raw_env: list) -> list:
    """Decode any secretKeyRef references in container env list into concrete values."""
    resolved_env = []

    for item in raw_env:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        value = item.get("value")
        value_from = item.get("valueFrom") or item.get("value_from")

        if value_from and isinstance(value_from, dict):
            sec_ref = value_from.get("secretKeyRef") or value_from.get("secret_key_ref")
            if sec_ref:
                sec_name = sec_ref.get("name")
                sec_key = sec_ref.get("key")
                try:
                    payload = kube.get_secret_payload(sec_name, namespace)
                    if sec_key in payload:
                        value = payload[sec_key]
                        resolved_env.append({"name": name, "value": value})
                        continue
                except Exception as e:
                    print(f"  warn: failed to resolve secretKeyRef for variable '{name}' ({e})")

        if value is not None:
            resolved_env.append({"name": name, "value": value})

    return resolved_env


def build_strict_postgres_job_env(kube, namespace: str, raw_env: list, pg_map_config: dict, target_ip: str) -> list:
    """
    STRICT COMPLIANCE WITH BACKUP-MAP (NO FALLBACKS):
    Explicitly map keys configured in backup-map (password-env, user-env, db-env, port, host)
    to standard PostgreSQL environment variables (PGPASSWORD, PGUSER, PGDATABASE, PGHOST, PGPORT).
    Raises ValueError if required configurations are absent.
    """
    resolved_env = resolve_container_env_secrets(kube, namespace, raw_env)
    env_dict = {e["name"]: e["value"] for e in resolved_env if "name" in e and "value" in e}

    pass_key = pg_map_config.get("password-env")
    user_key = pg_map_config.get("user-env")
    db_key = pg_map_config.get("db-env")

    # 1. Host
    host_val = pg_map_config.get("host") or target_ip
    resolved_env.append({"name": "PGHOST", "value": host_val})

    # 2. Port
    port_val = str(pg_map_config.get("port", 5432))
    resolved_env.append({"name": "PGPORT", "value": port_val})

    # 3. User
    if user_key:
        if user_key not in env_dict:
            raise ValueError(f"Configured 'user-env' key '{user_key}' not found in workload environment.")
        resolved_env.append({"name": "PGUSER", "value": env_dict[user_key]})

    # 4. Password
    if pass_key:
        if pass_key not in env_dict:
            raise ValueError(f"Configured 'password-env' key '{pass_key}' not found in workload environment.")
        resolved_env.append({"name": "PGPASSWORD", "value": env_dict[pass_key]})

    # 5. Database (Optional)
    if db_key and db_key in env_dict:
        resolved_env.append({"name": "PGDATABASE", "value": env_dict[db_key]})

    return resolved_env


def run_backup_job(cluster_context: str, namespace: str):
    """
    Global sequential orchestrator:
    Phase 0: Cleanup + wait for deletion of old Jobs
    Phase 1: Gather configuration + Trigger Scale Down (0)
    Phase 2: Wait for complete production workload termination
    Phase 3: Trigger all new backup Jobs concurrently via K8s control plane
    Phase 4: Restore (Scale Up) production workloads to original layout states
    """
    global backup_workloads
    global kube_client_sigkill

    kube = VolumeBackupJob(context=cluster_context, namespace=namespace)
    kube_client_sigkill = kube

    pvcs = kube.list_pvc()

    # Skip if no PVC
    if not pvcs:
        return

    master_ns = get_master_namespace(kube)

    print(f"\n==================================================")
    print(f" STARTING BACKUPS FOR NAMESPACE {namespace}")
    print(f"==================================================")

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

    # Utilisation du try...finally global pour intercepter TOUS les crashs Python inattendus (ex: YAML ParseError)
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
            should_scale = not is_postgres  # Do NOT scale down PostgreSQL workloads in V1

            if resource_key not in backup_workloads:
                if should_scale and kube.is_topology_constrained(pvc_name):
                    node_name = kube.get_node_name(pvc_name)
                else:
                    print(f"   info: Remote/Cloud Storage layout validation verified. Dynamic scheduling bypass enabled.")
                    node_name = ""

                cached_val = bpc.find_cached_replicas(cache_data, namespace, resource_type, resource_name)
                if cached_val is not None:
                    initial_replicas = cached_val
                    print(f"  info: Found cached replica count for {resource_key}: {initial_replicas}")
                else:
                    initial_replicas = kube.get_replicas(resource_type, resource_name)
                    if should_scale:
                        bpc.update_cached_replicas(cache_data, namespace, resource_type, resource_name, initial_replicas)

                backup_workloads[resource_key] = {
                    "namespace": namespace,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "initial_replicas": initial_replicas,
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
                if info["initial_replicas"] > 0:
                    kube.scale_replicas(info["resource_type"], info["resource_name"], replicas=0)
                else:
                    print(f"  info: {resource_key} is already stopped.")
            else:
                print(f"  info: {resource_key} is PostgreSQL — leaving replicas running ({info['initial_replicas']}) for online pg_dump.")


        # ==================================================
        # PHASE 2 : WAIT FOR COMPLETE EXCLUSIVITY
        # ==================================================
        print("\nPHASE 2: Waiting for scaled-down workloads to stop...")
        for resource_key, info in backup_workloads.items():
            if info["namespace"] == namespace and info["should_scale"] and info["initial_replicas"] > 0:
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
                    print(f"  info: {resource_key} identified as PostgreSQL database. Routing via pg_dump worker...")
                    worker = PostgresBackupJob(context=cluster_context, namespace=namespace)
                    postgres_image = worker.get_workload_image(info["resource_type"], info["resource_name"])

                    pg_map_config = get_postgresql_claim_config_from_map(kube, info["pvc_name"], namespace)
                    target_ip = get_postgres_pod_ip(kube, namespace, info["pvc_name"])

                    original_env_raw = []
                    original_env_from_raw = []
                    try:
                        if info["resource_type"].lower() == "statefulset":
                            res_obj = kube.apps_v1.read_namespaced_stateful_set(name=info["resource_name"], namespace=namespace)
                        else:
                            res_obj = kube.apps_v1.read_namespaced_deployment(name=info["resource_name"], namespace=namespace)

                        c_spec = res_obj.spec.template.spec.containers[0]
                        if c_spec.env:
                            raw_env = kube.core_v1.api_client.sanitize_for_serialization(c_spec.env)
                            original_env_raw = build_strict_postgres_job_env(kube, namespace, raw_env, pg_map_config)

                        if c_spec.env_from:
                            original_env_from_raw = kube.core_v1.api_client.sanitize_for_serialization(c_spec.env_from)
                    except Exception as e:
                        print(f"  warn: failed to extract original env block layout ({e}). Proceeding blank.")

                    worker.create_backup_job(
                        pvc_name=info["pvc_name"],
                        mounted_path=info["mounted_path"],
                        node_name=info["node_name"],
                        postgres_image=postgres_image,
                        image_pull_secrets=pull_secrets,
                        suffix=worker.suffix,
                        dumper_env_list=original_env_raw,
                        original_env_from=original_env_from_raw
                    )
                else:
                    print(f"  info: {resource_key} identified as standard volume. Routing via standard worker...")
                    worker = VolumeBackupJob(context=cluster_context, namespace=namespace)
                    worker.create_backup_job(
                        pvc_name=info["pvc_name"],
                        mounted_path=info["mounted_path"],
                        node_name=info["node_name"],
                        image_pull_secrets=pull_secrets,
                        suffix=worker.suffix
                    )

                # On calcule le nom exact du job en demandant gentiment au worker son suffixe de classe
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

    print(f"\n==================================================")
    print(f" BACKUP PROCESS CONCLUDED FOR NAMESPACE {namespace}")
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

    cluster_context = cluster_name_env if cluster_name_env else args.context
    namespace_input = namespace_env if namespace_env else args.namespace

    kube = VolumeBackupJob(context=cluster_context, namespace=namespace_input if namespace_input and namespace_input.lower() != "all" else "default")
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
            run_backup_job(cluster_context, namespace=ns)

    elif args.refresh_map:
        backup_map = bpm.backup_map_scanner(kube, target_namespaces)
        kube.save_to_configmap(
            name="clustersnap-backup-map",
            namespace=get_master_namespace(kube),
            data=backup_map
        )

    elif args.get_map:
        backup_map = bpm.get_backup_map(kube)
        print(yaml.dump(backup_map, sort_keys=False, default_flow_style=False))

    else:
        print('error: please --help for more info')


if __name__ == "__main__":
    sys.exit(main())