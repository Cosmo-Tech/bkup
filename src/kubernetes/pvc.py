import time


def list_pvc(kube) -> list:
    """List all PVC of a namespace"""
    namespace = kube.get_namespace()
    pvc_list = []
    try:
        response = kube.client_corev1.list_namespaced_persistent_volume_claim(namespace=namespace)
        for pvc in response.items:
            pvc_list.append({
                "name": pvc.metadata.name,
                "status": pvc.status.phase,
                "capacity": pvc.status.capacity.get("storage") if pvc.status.capacity else "N/A"
            })
        return pvc_list
    except Exception as e:
        print(f"error: {e}")
        return []


def get_pvc(kube, pvc_name: str) -> list:
    """Look for PVC from Deployments and StatefulSets"""
    namespace = kube.get_namespace()
    targets = []
    try:
        # Deployments
        deployments = kube.client_appsv1.list_namespaced_deployment(namespace=namespace)
        for d in deployments.items:
            mounts = _list_pvc_mount_points(pvc_name, d.spec.template.spec)
            for m in mounts:
                m["resource"] = f"Deployment/{d.metadata.name}"
                targets.append(m)

        # StatefulSets
        statefulsets = kube.client_appsv1.list_namespaced_stateful_set(namespace=namespace)
        for s in statefulsets.items:
            mounts = _list_pvc_mount_points(pvc_name, s.spec.template.spec)
            for m in mounts:
                m["resource"] = f"StatefulSet/{s.metadata.name}"
                targets.append(m)
    except Exception as e:
        print(f"error: {e}")
    return targets


def _list_pvc_mount_points(pvc_name: str, pod_spec) -> list:
    """Get all mounted paths in a PVC"""
    results = []
    pod_volume_name = None

    if not pod_spec or not pod_spec.volumes:
        return results

    for vol in pod_spec.volumes:
        if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc_name:
            pod_volume_name = vol.name
            break

    if not pod_volume_name:
        return results

    for container in pod_spec.containers:
        if hasattr(container, 'volume_mounts') and container.volume_mounts:
            for mount in container.volume_mounts:
                if mount.name == pod_volume_name:
                    results.append({
                        "container_name": container.name,
                        "mount_path": mount.mount_path,
                        "read_only": mount.read_only if mount.read_only is not None else False
                    })
    return results


def get_replicas(kube, resource_type: str, name: str) -> int:
    """Get replicas quantity of a Statefulset or Deployment"""
    namespace = kube.get_namespace()
    try:
        if resource_type.lower() == "statefulset":
            sts = kube.client_appsv1.read_namespaced_stateful_set(name=name, namespace=namespace)
            return sts.spec.replicas
        elif resource_type.lower() == "deployment":
            deploy = kube.client_appsv1.read_namespaced_deployment(name=name, namespace=namespace)
            return deploy.spec.replicas
        else:
            print(f"error: unknown resource type '{resource_type}'")
            return 0
    except Exception as e:
        print(f"error: {e}")
        return 0


def scale_replicas(kube, resource_type: str, name: str, replicas: int) -> bool:
    """Scale replicas quantity of a Statefulset or Deployment"""
    namespace = kube.get_namespace()
    body = {"spec": {"replicas": replicas}}
    try:
        if resource_type.lower() == "statefulset":
            kube.client_appsv1.patch_namespaced_stateful_set(name=name, namespace=namespace, body=body)
            return True
        elif resource_type.lower() == "deployment":
            kube.client_appsv1.patch_namespaced_deployment(name=name, namespace=namespace, body=body)
            return True
        else:
            print(f"error: unknown resource type '{resource_type}'")
            return False
    except Exception as e:
        print(f"error: {e}")
        return False


def wait_for_scale_down(kube, resource_type: str, name: str) -> bool:
    """Blocks the execution until replicas are scaled to 0"""
    namespace = kube.get_namespace()
    
    resource = f"{resource_type}/{name}"
    print(f"waiting for {resource} to be fully stopped...")
    
    start_time = time.time()
    while time.time() - start_time < 120:
        try:
            if resource_type.lower() == "statefulset":
                res = kube.client_appsv1.read_namespaced_stateful_set(name=name, namespace=namespace)
            elif resource_type.lower() == "deployment":
                res = kube.client_appsv1.read_namespaced_deployment(name=name, namespace=namespace)
            else:
                print(f"error: unknown resource type '{resource_type}'")
                return False

            ready = res.status.ready_replicas
            if ready is None or ready == 0:
                time.sleep(3)
                print(f"successfully stopped {resource}")
                return True
        except Exception as e:
            print(f"error: {e}")
            return False
    print(f"error: timeout reached while waiting for scale down of {resource}")
    return False


def wait_for_volume_detachment(kube, pvc_name: str) -> bool:
    """Block execution until the PVC is detached from its node"""

    print(f"  info: waiting for volume of PVC '{pvc_name}' to be fully detached...")
    start_time = time.time()
    
    while time.time() - start_time < 120:
        try:
            pvc_obj = kube.client_corev1.read_namespaced_persistent_volume_claim(
                name=pvc_name, 
                namespace=kube.get_namespace()
            )
            pv_name = pvc_obj.spec.volume_name
            
            if not pv_name:
                return True

            attachments = kube.client_storagev1.list_volume_attachment()
            
            is_attached = False
            for item in attachments.items:
                item_dict = kube.client_corev1.api_client.sanitize_for_serialization(item)
                
                spec = item_dict.get('spec', {})
                status = item_dict.get('status', {})
                metadata = item_dict.get('metadata', {})
                
                if spec.get('volume_name') == pv_name:
                    if status.get('attached', False) and not metadata.get('deletion_timestamp'):
                        is_attached = True
                        break

            if not is_attached:
                print(f"    Volume for PVC '{pvc_name}' is successfully detached and ready!")
                print("    waiting 10s for storage backend initialization...")
                time.sleep(10)
                return True
                
        except Exception as e:
            print(f"    warn: error checking attachment status ({e}), retrying...")
            
        time.sleep(5)
        
    print(f"  warn: timeout reached while waiting for PVC '{pvc_name}' to detach. Proceeding anyway...")
    return False


def get_node_name(kube, pvc_name: str) -> str:
    """
    Get node name where the PVC is attached by reading the cached map 
    stored in the master pod's namespace.
    """
    target_namespace = kube.get_namespace()

    try:
        # Lecture du cache unique dans le namespace du pod master
        cm = kube.client_corev1.read_namespaced_config_map(name="clustersnap-backup-cache", namespace=target_namespace)
        if cm.data and "backup_map.json" in cm.data:
            saved_map = json.loads(cm.data["backup_map.json"])
            
            # Recherche de la correspondance pour ce PVC
            for ns_item in saved_map.get("namespaces", []):
                for claim in ns_item.get("claims", []):
                    if claim.get("name") == pvc_name and claim.get("attachment-node"):
                        return str(claim.get("attachment-node"))                            
    except Exception:
        pass

    try:
        pods = kube.client_corev1.list_namespaced_pod(namespace=target_namespace)
        for pod in pods.items:
            if pod.spec.volumes:
                for vol in pod.spec.volumes:
                    if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc_name:
                        if pod.spec.node_name:
                            return str(pod.spec.node_name)
                            
        nodes = kube.client_corev1.list_node()
        if nodes.items:
            return str(nodes.items[0].metadata.name)
    except Exception:
        pass

    return "unknown-node"