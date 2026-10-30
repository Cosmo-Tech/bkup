import os
import yaml
import jinja2
from pathlib import Path

from kubernetes import client, config
from src.utils import general as utils


class KubernetesBase:
    def __init__(self, context: str = None):
        """Init Kubernetes connexion."""
        is_in_cluster = os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")

        # Ensure not running on default context in case of CLI usage
        if not context and not is_in_cluster:
            raise ValueError("missing cluster context")

        self.context = context

        try:
            if is_in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(context=self.context)
        except Exception as e:
            raise RuntimeError(f"error: {utils.current_function()}: failed to initialize Kubernetes connection with context '{self.context}': {e}")

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()
        # self.storage_v1 = client.StorageV1Api()


    def list_namespaces(self) -> list:
        results = []
        try:
            namespaces = self.core_v1.list_namespace()
            for i in namespaces.items:
                result = i.metadata.name
                results.append(result)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list namespaces: {e}")
        return results


    def list_deployments(self, namespace: str) -> list:
        try:
            deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)
            results = []
            for i in deployments.items:
                result = i
                # result = i.metadata.name
                results.append(result)
            return results
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list deployments: {e}")
            return []


    def list_statefulsets(self, namespace: str) -> list:
        try:
            statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace=namespace)
            results = []
            for i in statefulsets.items:
                result = i
                # result = i.metadata.name
                results.append(result)
            return results
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to list statefulsets: {e}")
            return []


    # def list_deployment_mountpoints(self, namespace: str, deployment: str) -> list:
    #     """List mount points of a given Deployment"""
    #     results = []
    #     try:
    #         deployment = self.apps_v1.read_namespaced_deployment(namespace=namespace, name=deployment)
    #         for c in deployment.spec.template.spec.containers:
    #             for m in c.volume_mounts:
    #                 result = {
    #                     "type": 'deployment',
    #                     "name": deployment.metadata.name,
    #                     "container": c.name,
    #                     "mount_path": m.mount_path
    #                     }
    #                 results.append(result)
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to list mountpoints: {e}")
    #     return results


    # def list_statefulset_mountpoints(self, namespace: str, statefulset: str) -> list:
    #     """List mountpoints of a given StatefulSet"""
    #     results = []
    #     try:
    #         statefulset = self.apps_v1.read_namespaced_stateful_set(namespace=namespace, name=statefulset)
    #         for c in statefulset.spec.template.spec.containers:
    #             for m in c.volume_mounts:
    #                 result = {
    #                     "type": 'statefulset',
    #                     "name": statefulset.metadata.name,
    #                     "container": c.name,
    #                     "mount_path": m.mount_path
    #                     }
    #                 results.append(result)
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to list mount points: {e}")
    #     return results


    # def merge_mountpoints_list(self, list1: str, list2: str) -> list: 
    #     """Merge given mountpoints lists in a single one"""
    #     try:
    #         results = []

    #         for i in list1:
    #             # if (i.name not in list2) or (i.name in list2 and i.type not in list2):
    #             results.append(i)


    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: failed to merge mountpoints lists: {e}")
    #         return []

    def list_pvc(self, namespace: str) -> list:
        """List all PVC of a namespace"""
        results = []
        try:
            pvc_list = self.core_v1.list_namespaced_persistent_volume_claim(namespace=namespace)
            for i in pvc_list.items:
                result = i.metadata.name
                results.append(result)
        except Exception as e:
            print(f"error: {utils.current_function()}: {e}")
        return results


    # def merge_mountpoints_lists(self, namespace: str, pvc_name: str) -> list:
    #     """Look for PVC from Deployments and StatefulSets"""
    #     results = []
    #     try:
    #         deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)
    #         for d in deployments.items:
    #             mounts = self.map_mountpoints_pvc_workload(pvc_name, d)
    #             for m in mounts:
    #                 m["kind"] = f"deployment"
    #                 results.append(m)

    #         statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace=namespace)
    #         for s in statefulsets.items:
    #             mounts = self.map_mountpoints_pvc_workload(pvc_name, s)
    #             for m in mounts:
    #                 m["kind"] = f"statefulset"
    #                 results.append(m)
    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: {e}")
    #     return results


    # def _list_pvc_mountpoints(self, pvc_name: str, pod_spec) -> list:
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


    def map_mountpoints_pvc_workload(self, pvc_name: str, workload: str) -> list:
        """Get mounted paths from combination of PVC name + workload manifest (deployment, statefulset)"""
        results = []
        try:
            v_name = None
            w_name = workload.metadata.name
            w_spec = workload.spec.template.spec
            if not w_spec.volumes:
                return results

            # 1. Find the volume that is using to the PVC
            for v in w_spec.volumes:
                if v.persistent_volume_claim:
                    pvc = v.persistent_volume_claim.claim_name
                    if pvc == pvc_name:
                        v_name = v.name

            # 2. Find the mountpoints of the volume found before
            for c in w_spec.containers:
                for vm in c.volume_mounts:
                    if v_name == vm.name:
                        result = {
                            "pvc":          pvc_name,
                            "workload":     w_name,
                            "container":    c.name,
                            "volume":       vm.name,
                            "mount_path":    vm.mount_path
                            }
                        results.append(result)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to retrieve workload/pvc/mountpoints combination: {e}")
        return results


    def create_job(self,
        template_path: str,
        namespace: str,
        image: str,
        image_pull_policy: str,
        job_name: str,
        pvc_name: str,
        # node_name: str,
        mount_path: str,
        storage_protocol: str,
        storage_endpoint: str,
        storage_root: str,
        storage_username: str,
        storage_password: str,
        archive_name: str
        ):
        """Create a Kubernetes Job from a given manifest."""
        raw_yaml = Path(template_path).read_text(encoding="utf-8")
        rendered_yaml = jinja2.Template(raw_yaml).render(
            NAMESPACE=namespace,
            IMAGE=image,
            IMAGE_PULL_POLICY=image_pull_policy,
            JOB_NAME=job_name,
            PVC_NAME=pvc_name,
            # NODE_NAME=node_name,
            MOUNT_PATH=mount_path,
            STORAGE_PROTOCOL=storage_protocol,
            STORAGE_ENDPOINT=storage_endpoint,
            STORAGE_ROOT=storage_root,
            STORAGE_USERNAME=storage_username,
            STORAGE_PASSWORD=storage_password,
            ARCHIVE_NAME=archive_name,
            )

        body = yaml.safe_load(rendered_yaml)

        try:
            print(f"starting job {job_name}...")
            self.batch_v1.create_namespaced_job(namespace=namespace, body=body)
        except Exception as e:
            print(f"error: {utils.current_function()}: failed to create job {namespace}/{job_name}: {e}")




