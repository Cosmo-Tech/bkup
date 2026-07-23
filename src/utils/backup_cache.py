import yaml


def get_backup_cache(kube) -> dict:
    """Get the existing ConfigMap of the backup-cache."""
    saved_cache = kube.get_configmap(name="clustersnap-backup-cache", namespace=kube.get_namespace())

    if not saved_cache:
        return {"namespaces": []}

    # If get_configmap has already detected and parsed the YAML as a Python dictionary
    if isinstance(saved_cache, dict) and "namespaces" in saved_cache:
        return saved_cache

    # If get_configmap returned the global ConfigMap dictionary with its file key
    if isinstance(saved_cache, dict) and "clustersnap-backup-cache.yaml" in saved_cache:
        content = saved_cache["clustersnap-backup-cache.yaml"]
        if isinstance(content, dict) and "namespaces" in content:
            return content
        try:
            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict) and "namespaces" in parsed:
                return parsed
        except yaml.YAMLError:
            pass

    return {"namespaces": []}


def save_backup_cache(kube, cache_data: dict, master_namespace: str):
    """Save the provided hierarchical cache data structure inside the ConfigMap."""
    orig_ns = kube.namespace
    try:
        kube.namespace = master_namespace
        kube.save_to_configmap(
            name="clustersnap-backup-cache",
            namespace=master_namespace,
            data={"clustersnap-backup-cache.yaml": yaml.dump(cache_data, default_flow_style=False)},
        )
    finally:
        kube.namespace = orig_ns


def clear_backup_cache(kube, master_namespace: str):
    """Clean the backup-cache ConfigMap completely."""
    orig_ns = kube.namespace
    try:
        kube.namespace = master_namespace
        kube.save_to_configmap(name="clustersnap-backup-cache", namespace=master_namespace, data={})
    except Exception as e:
        print(f"  warn: failed to clear backup cache ConfigMap: {e}")
    finally:
        kube.namespace = orig_ns


def find_cached_replicas(cache_data: dict, namespace: str, r_type: str, r_name: str) -> int:
    """Look up a workload's replica count within the parsed cache structure."""
    namespaces = cache_data.get("namespaces", [])
    for ns in namespaces:
        if ns.get("name") == namespace:
            for res in ns.get("resources", []):
                if res.get("type") == r_type and res.get("name") == r_name:
                    return res.get("replicas")
    return None


def update_cached_replicas(cache_data: dict, namespace: str, r_type: str, r_name: str, replicas: int):
    """Insert or update a workload's replica state in the cache dictionary."""
    namespaces = cache_data.setdefault("namespaces", [])

    ns_found = None
    for ns in namespaces:
        if ns.get("name") == namespace:
            ns_found = ns
            break

    if not ns_found:
        ns_found = {"name": namespace, "resources": []}
        namespaces.append(ns_found)

    resources = ns_found.setdefault("resources", [])

    res_found = None
    for res in resources:
        if res.get("type") == r_type and res.get("name") == r_name:
            res_found = res
            break

    if not res_found:
        resources.append({"type": r_type, "name": r_name, "replicas": replicas})
    else:
        res_found["replicas"] = replicas


def remove_cached_resource(cache_data: dict, namespace: str, r_type: str, r_name: str):
    """Delete a specific workload from the cache, removing empty namespaces."""
    namespaces = cache_data.get("namespaces", [])
    for ns in namespaces:
        if ns.get("name") == namespace:
            resources = ns.get("resources", [])
            ns["resources"] = [r for r in resources if not (r.get("type") == r_type and r.get("name") == r_name)]

    # Remove namespaces with no resources left
    cache_data["namespaces"] = [ns for ns in namespaces if ns.get("resources")]
