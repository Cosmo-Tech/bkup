import os
import psutil
from kubernetes import client, config
from kubernetes.client.rest import ApiException

class Kubernetes:

    def __init__(self, context: str = None, given_namespace: str = None):
        self.given_namespace = given_namespace
        try:
            # Connect from CLI (based on kubeconfig)
            if context:
                config.load_kube_config(context=context)

            # Connect from Kubernetes pod
            else:
                config.load_incluster_config()

            self.client_corev1 = client.CoreV1Api()
            self.client_appsv1 = client.AppsV1Api()
        except Exception as e:
            print(f"error: {e}")


    def get_current_namespace(self) -> str:
        # Use namespace if given from args
        if self.given_namespace:
            return self.given_namespace

        # Or fallback on current namespace if running in a pod
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.exists(ns_path):
            with open(ns_path, "r") as f:
                return f.read().strip()
        return "default"


    def list_pvc(self) -> list:
        current_ns = self.get_current_namespace()
        pvc_list = []
        try:
            response = self.client_corev1.list_namespaced_persistent_volume_claim(namespace=current_ns)
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


    # Look for PVC from Deployments and StatefulSets
    def get_pvc(self, pvc: str) -> list:
        current_ns = self.get_current_namespace()
        targets = []

        try:
            # Deployments
            deployments = self.client_appsv1.list_namespaced_deployment(namespace=current_ns)
            for d in deployments.items:
                mounts = self._list_pvc_mount_points(pvc, d.spec.template.spec)
                for m in mounts:
                    m["resource"] = f"Deployment/{d.metadata.name}"
                    targets.append(m)

            # StatefulSets
            statefulsets = self.client_appsv1.list_namespaced_stateful_set(namespace=current_ns)
            for s in statefulsets.items:
                mounts = self._list_pvc_mount_points(pvc, s.spec.template.spec)
                for m in mounts:
                    m["resource"] = f"StatefulSet/{s.metadata.name}"
                    targets.append(m)

        except Exception as e:
            print(f"error: {e}")

        return targets


    def _list_pvc_mount_points(self, pvc: str, pod_spec) -> list:
        results = []
        pod_volume_name = None

        if not pod_spec or not pod_spec.volumes:
            return results

        # Get volume name
        for vol in pod_spec.volumes:
            if vol.persistent_volume_claim and vol.persistent_volume_claim.claim_name == pvc:
                pod_volume_name = vol.name
                break

        if not pod_volume_name:
            return results

        # Get volumes info
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




    def create_backup_job(self, pvc: str, mounted_path: str) -> bool:
        current_ns = self.get_current_namespace()
        
        job_name = f"backup-{pvc}"[-63:].lower().strip("-")
        print(f"starting backup job: {job_name}")

        template_path = os.path.join(os.path.dirname(__file__), "../..", "templates", "backup-job.yaml")

        try:
            with open(template_path, "r") as f:
                yaml_content = f.read()

            yaml_content = yaml_content.replace("{{ JOB_NAME }}", job_name)
            yaml_content = yaml_content.replace("{{ PVC_NAME }}", pvc)
            yaml_content = yaml_content.replace("{{ MOUNTED_PATH }}", mounted_path)

            job_dict = yaml.safe_load(yaml_content)

            if "metadata" not in job_dict:
                job_dict["metadata"] = {}
            job_dict["metadata"]["namespace"] = current_ns

            utils.create_from_dict(self.client_corev1.api_client, data=job_dict)

            return True

        except Exception as e:
            print(f"error: {e}")
            return False
