import yaml

import src.utils.psql as psql


def get_backup_map(kube, master_ns: str = None) -> dict:
    """Get existing ConfigMap of the backup-map."""
    target_ns = master_ns if master_ns else kube.get_namespace()
    saved_map = kube.get_configmap(
        name="clustersnap-backup-map",
        namespace=target_ns
    )

    if not saved_map:
        return {"cluster": kube.get_cluster_name(), "namespaces": []}

    if isinstance(saved_map, dict) and "namespaces" in saved_map:
        return saved_map

    if isinstance(saved_map, dict) and "clustersnap-backup-map.yaml" in saved_map:
        content = saved_map["clustersnap-backup-map.yaml"]
        if isinstance(content, dict) and "namespaces" in content:
            return content
        try:
            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict) and "namespaces" in parsed:
                return parsed
        except yaml.YAMLError:
            pass

    return {"cluster": kube.get_cluster_name(), "namespaces": []}


def backup_map_scanner(kube, target_namespaces: list) -> dict:
    """Scan the cluster to get a YAML map to orchestrate the backups."""
    map_data = {
        "cluster": kube.get_cluster_name(),
        "namespaces": []
    }

    original_ns = kube.namespace

    for ns in target_namespaces:
        kube.namespace = ns
        current_ns = kube.get_namespace()

        ns_data = {
            "name": current_ns,
            "claims": []
        }

        pvc_list = kube.list_pvc()
        if not pvc_list:
            map_data["namespaces"].append(ns_data)
            continue

        # Collect PVC
        for pvc in pvc_list:
            backup_type = "volume"
            postgres_mount = None
            mounts = kube.get_pvc(pvc['name'])
            if mounts:
                for m in mounts:
                    if psql.is_postgresql_workload(m):
                        backup_type = "pg_dump"
                        postgres_mount = m
                        break

            node_name = kube.get_node_name(pvc['name'])

            pvc_data = {
                "name": pvc['name'],
                "backup-type": backup_type,
                "attachment-node": node_name,
                "mounts": []
            }

            # If pg_dump backup type, auto-detect and populate postgresql sub-block
            if backup_type == "pg_dump" and postgres_mount:
                pg_cfg = _detect_postgresql_config(kube, current_ns, postgres_mount)
                pvc_data["postgresql"] = pg_cfg

            if mounts:
                for m in mounts:
                    pvc_data["mounts"].append({
                        "container": m['container_name'],
                        "path": m['mount_path'],
                        "resource": m['resource']
                    })
            else:
                pvc_data["mounts"] = mounts

            ns_data["claims"].append(pvc_data)
        map_data["namespaces"].append(ns_data)

    kube.namespace = original_ns
    return map_data


def get_pvc_from_map(kube_client, pvc_name: str) -> list:
    """Reverse lookup utility searching for workload attachments associated with a given PVC."""
    try:
        # Fetch the current unpacked map layout via our agnostic ConfigMap utility
        backup_map = get_backup_map(kube_client)
        if not backup_map or "namespaces" not in backup_map:
            return []

        # Interrogate the document hierarchy dynamically
        for ns in backup_map["namespaces"]:
            if "claims" in ns:
                for claim in ns["claims"]:
                    if claim.get("name") == pvc_name:
                        # Return formatting structure standard compliant with master.py loops
                        return claim.get("mounts", [])

        return []
    except Exception as e:
        print(f"  warn: map reverse lookup failed for PVC '{pvc_name}': {e}")
        return []


def resolve_backup_type_from_map(kube, pvc_name: str, namespace: str) -> str:
    """Interroge la ConfigMap de backup-map pour récupérer automatiquement la stratégie du PVC."""
    try:
        backup_map = get_backup_map(kube)

        if not backup_map or "namespaces" not in backup_map:
            print("warn: backup-map not found, defaulting type to 'unknown'")
            return "unknown"

        for ns in backup_map.get("namespaces", []):
            if ns.get("name") == namespace:
                for claim in ns.get("claims", []):
                    if claim.get("name") == pvc_name:
                        return claim.get("backup-type", "volume")

    except Exception as e:
        print(f"  warn: failed to resolve backup-type from map ({e}). Fallback to 'unknown'.")

    return "unknown"


def _detect_postgresql_config(kube, namespace: str, mount_info: dict) -> dict:
    """Inspect workload container environment spec to auto-detect PostgreSQL configuration keys."""
    pg_config = {
        "port": 5432
    }

    try:
        resource_str = mount_info.get("resource", "")
        if "/" not in resource_str:
            pg_config["password-secret"] = {"name": "CHANGEME", "key": "CHANGEME"}
            return pg_config

        resource_type, resource_name = resource_str.split("/")

        if resource_type.lower() == "statefulset":
            res_obj = kube.apps_v1.read_namespaced_stateful_set(name=resource_name, namespace=namespace)
        elif resource_type.lower() == "deployment":
            res_obj = kube.apps_v1.read_namespaced_deployment(name=resource_name, namespace=namespace)
        else:
            pg_config["password-secret"] = {"name": "CHANGEME", "key": "CHANGEME"}
            return pg_config

        pod_spec = res_obj.spec.template.spec
        c_spec = pod_spec.containers[0]

        secret_name = None

        # 1. Cherche si une variable d'environnement du conteneur pointe vers un Secret
        if c_spec.env:
            for env_var in c_spec.env:
                if env_var.value_from and env_var.value_from.secret_key_ref:
                    secret_name = env_var.value_from.secret_key_ref.name
                    break

        # 2. Sinon cherche un volume de type Secret
        if not secret_name and pod_spec.volumes:
            for vol in pod_spec.volumes:
                if vol.secret:
                    secret_name = vol.secret.secret_name
                    break

        # Si trouvé, on met le nom réel du secret, sinon fallback "CHANGEME"
        resolved_secret_name = secret_name if secret_name else f"{resource_name}-secret"

        pg_config["password-secret"] = {
            "name": resolved_secret_name,
            "key": "CHANGEME"
        }

    except Exception as e:
        print(f"  warn: failed to auto-detect postgresql config for {mount_info.get('resource')}: {e}")
        pg_config["password-secret"] = {
            "name": "CHANGEME",
            "key": "CHANGEME"
        }

    return pg_config
