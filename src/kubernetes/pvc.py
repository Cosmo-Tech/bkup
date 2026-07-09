import time


def list_pvc(k8s) -> list:
    """List all PVC of a namespace"""
    current_ns = k8s.get_namespace()
    pvc_list = []
    try:
        response = k8s.client_corev1.list_namespaced_persistent_volume_claim(namespace=current_ns)
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


def get_pvc(k8s, pvc_name: str) -> list:
    """Look for PVC from Deployments and StatefulSets"""
    current_ns = k8s.get_namespace()
    targets = []
    try:
        # Deployments
        deployments = k8s.client_appsv1.list_namespaced_deployment(namespace=current_ns)
        for d in deployments.items:
            mounts = k8s._list_pvc_mount_points(pvc_name, d.spec.template.spec)
            for m in mounts:
                m["resource"] = f"Deployment/{d.metadata.name}"
                targets.append(m)

        # StatefulSets
        statefulsets = k8s.client_appsv1.list_namespaced_stateful_set(namespace=current_ns)
        for s in statefulsets.items:
            mounts = k8s._list_pvc_mount_points(pvc_name, s.spec.template.spec)
            for m in mounts:
                m["resource"] = f"StatefulSet/{s.metadata.name}"
                targets.append(m)
    except Exception as e:
        print(f"error: {e}")
    return targets


def _list_pvc_mount_points(k8s, pvc_name: str, pod_spec) -> list:
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


def get_replicas(k8s, resource_type: str, name: str) -> int:
    """Get replicas quantity of a Statefulset or Deployment"""
    current_ns = k8s.get_namespace()
    try:
        if resource_type.lower() == "statefulset":
            sts = k8s.client_appsv1.read_namespaced_stateful_set(name=name, namespace=current_ns)
            return sts.spec.replicas
        elif resource_type.lower() == "deployment":
            deploy = k8s.client_appsv1.read_namespaced_deployment(name=name, namespace=current_ns)
            return deploy.spec.replicas
        else:
            print(f"error: unknown resource type '{resource_type}'")
            return 0
    except Exception as e:
        print(f"error: {e}")
        return 0


def scale_replicas(k8s, resource_type: str, name: str, replicas: int) -> bool:
    """Scale replicas quantity of a Statefulset or Deployment"""
    current_ns = k8s.get_namespace()
    body = {"spec": {"replicas": replicas}}
    try:
        if resource_type.lower() == "statefulset":
            k8s.client_appsv1.patch_namespaced_stateful_set(name=name, namespace=current_ns, body=body)
            return True
        elif resource_type.lower() == "deployment":
            k8s.client_appsv1.patch_namespaced_deployment(name=name, namespace=current_ns, body=body)
            return True
        else:
            print(f"error: unknown resource type '{resource_type}'")
            return False
    except Exception as e:
        print(f"error: {e}")
        return False


def wait_for_scale_down(k8s, resource_type: str, name: str, timeout: int = 120) -> bool:
    """Blocks the execution until replicas are scaled to 0"""
    current_ns = k8s.get_namespace()
    
    resource = f"{resource_type}/{name}"
    print(f"waiting for {resource} to be fully stopped...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            if resource_type.lower() == "statefulset":
                res = k8s.client_appsv1.read_namespaced_stateful_set(name=name, namespace=current_ns)
            elif resource_type.lower() == "deployment":
                res = k8s.client_appsv1.read_namespaced_deployment(name=name, namespace=current_ns)
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
