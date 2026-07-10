import json
from kubernetes.client.rest import ApiException


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
    """Scan the cluster to get a YAML map of what will be backuped."""
    map_data = {
        "cluster": kube.get_cluster_name(),
        "namespaces": []
    }

    print(f"cluster: {kube.get_cluster_name()}")
    print(f"namespaces:")
    
    for ns in target_namespaces:
        kube.namespace = ns
        print(f"- name: {kube.get_namespace()}")
        
        ns_data = {
            "name": kube.get_namespace(),
            "claims": []
        }

        pvcs = kube.list_pvc()
        if not pvcs:
            print(f"   claims: []")
            map_data["namespaces"].append(ns_data)
            continue

        print(f"   claims:")
        for pvc in pvcs:
            print(f"    - name: {pvc['name']}")
            print(f"      status: {pvc['status']}")
            print(f"      capacity: {pvc['capacity']}")
            
            node_name = kube.get_node_name(pvc['name'])
            print(f"      attachment_node: {node_name}")

            pvc_data = {
                "name": pvc['name'],
                "status": pvc['status'],
                "capacity": pvc['capacity'],
                "attachment_node": node_name,
                "mounts": []
            }

            mounts = kube.get_pvc(pvc['name'])
            if mounts:
                print(f"      mounts:")
                for m in mounts:
                    print(f"      - container: {m['container_name']}")
                    print(f"        path: {m['mount_path']}")
                    print(f"        read_only: {m['read_only']}")
                    print(f"        resource: {m['resource']}")
                    
                    pvc_data["mounts"].append({
                        "container": m['container_name'],
                        "path": m['mount_path'],
                        "read_only": m['read_only'],
                        "resource": m['resource']
                    })
            else:
                print(f"      mounts: {mounts}")
                pvc_data["mounts"] = mounts
                
            ns_data["claims"].append(pvc_data)
        map_data["namespaces"].append(ns_data)

    return map_data
