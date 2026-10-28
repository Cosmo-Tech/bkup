# def list_pvc(kube, namespace: str) -> list:
#     """List all PVC of a namespace"""
#     results = []
#     try:
#         response = kube.core_v1.list_namespaced_persistent_volume_claim(namespace=namespace)
#         for pvc in response.items:
#             results.append(
#                 {
#                     "name": pvc.metadata.name,
#                     "status": pvc.status.phase,
#                 }
#             )
#         return results
#     except Exception as e:
#         print(f"error: {e}")
#         return []


# def get_pvc_with_mountpoints(kube, namespace: str, pvc_name: str) -> list:
#     """Look for PVC from Deployments and StatefulSets"""
#     results = []
#     try:
#         # Deployments
#         deployments = kube.apps_v1.list_namespaced_deployment(namespace=namespace)
#         for d in deployments.items:
#             mounts = _list_pvc_mountpoints(pvc_name, d.spec.template.spec)
#             for m in mounts:
#                 m["resource"] = f"Deployment/{d.metadata.name}"
#                 results.append(m)

#         # StatefulSets
#         statefulsets = kube.apps_v1.list_namespaced_stateful_set(namespace=namespace)
#         for s in statefulsets.items:
#             mounts = _list_pvc_mountpoints(pvc_name, s.spec.template.spec)
#             for m in mounts:
#                 m["resource"] = f"StatefulSet/{s.metadata.name}"
#                 results.append(m)
#     except Exception as e:
#         print(f"error: {e}")
#     return results


# def _list_pvc_mountpoints(pvc_name: str, pod_spec) -> list:
#     """Get all mounted paths in a PVC"""
#     results = []
#     pod_volume_name = None

#     if not pod_spec or not pod_spec.volumes:
#         return results

#     for vol in pod_spec.volumes:
#         if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc_name:
#             pod_volume_name = vol.name
#             break

#     if not pod_volume_name:
#         return results

#     for container in pod_spec.containers:
#         if hasattr(container, "volume_mounts") and container.volume_mounts:
#             for mount in container.volume_mounts:
#                 if mount.name == pod_volume_name:
#                     results.append(
#                         {
#                             "container_name": container.name,
#                             "mount_path": mount.mount_path,
#                             "read_only": mount.read_only if mount.read_only is not None else False,
#                         }
#                     )
#     return results
