import json
import yaml
from kubernetes.client.rest import ApiException

import src.utils.psql as psql


def get_backup_map(kube) -> dict:
    """Get existing ConfigMap of the backup-map."""
    saved_map = kube.get_configmap(
        name="clustersnap-backup-map",
        namespace=kube.get_namespace()
    )
    
    if not saved_map:
        return {"cluster": kube.get_cluster_name(), "namespaces": []}
        
    return saved_map


def backup_map_scanner(kube, target_namespaces: list) -> dict:
    """Scan the cluster to get a YAML map to orchestrate the backups."""
    map_data = {
        "cluster": kube.get_cluster_name(),
        "namespaces": []
    }

    print(f"cluster: {kube.get_cluster_name()}")
    print(f"namespaces:")
    original_ns = kube.namespace
    
    for ns in target_namespaces:
        kube.namespace = ns
        current_ns = kube.get_namespace()
        print(f"- name: {current_ns}")
        
        ns_data = {
            "name": current_ns,
            "workloads": [],
            "claims": []
        }
        
        workload_list = set()

        pvc_list = kube.list_pvc()
        if not pvc_list:
            print(f"   claims: []")
            map_data["namespaces"].append(ns_data)
            continue

        # Collect workload replicas
        for pvc in pvc_list:
            mounts = kube.get_pvc(pvc['name'])
            if mounts:
                for m in mounts:
                    resource_str = m['resource']  # ex: "StatefulSet/postgresql"
                    if resource_str not in workload_list:
                        workload_list.add(resource_str)
                        resource_type, resource_name = resource_str.split('/')
                        
                        # Récupération live du nombre de replicas via le wrapper kube
                        replicas = kube.get_replicas(resource_type, resource_name)
                        
                        ns_data["workloads"].append({
                            "resource": resource_str,
                            "replicas": replicas
                        })

        print(f"   workloads:")
        for wl in ns_data["workloads"]:
            print(f"    - resource: {wl['resource']}")


        # Collect PVC
        print(f"   claims:")
        for pvc in pvc_list:
            print(f"    - name: {pvc['name']}")
            
            backup_type = "volume"
            mounts = kube.get_pvc(pvc['name'])
            if mounts:
                for m in mounts:
                    if psql.is_postgresql_workload(m):
                        backup_type = "pg_dump"
                        break

            print(f"      backup-type: {backup_type}")
            
            node_name = kube.get_node_name(pvc['name'])
            print(f"      attachment-node: {node_name}")

            pvc_data = {
                "name": pvc['name'],
                "backup-type": backup_type,
                "attachment-node": node_name,
                "mounts": []
            }

            if mounts:
                print(f"      mounts:")
                for m in mounts:
                    print(f"      - container: {m['container_name']}")
                    print(f"        path: {m['mount_path']}")
                    print(f"        resource: {m['resource']}")
                    
                    pvc_data["mounts"].append({
                        "container": m['container_name'],
                        "path": m['mount_path'],
                        "resource": m['resource']
                    })
            else:
                print(f"      mounts: {mounts}")
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
