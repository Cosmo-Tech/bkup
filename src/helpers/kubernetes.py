import os
import psutil
import yaml
import time
from kubernetes import client, config, utils
from kubernetes.client.rest import ApiException
from pathlib import Path

class Kubernetes:

    def __init__(self, context: str, namespace: str):
        self.context = context
        self.namespace = namespace
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


    def get_context(self) -> str:
        if self.context:
            return self.context


    def get_namespace(self) -> str:
        if self.namespace:
            return self.namespace


    def list_pvc(self) -> list:
        current_ns = self.get_namespace()
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
        current_ns = self.get_namespace()
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


    # Get all mounted paths in a PVC
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


    # Check if a secret exists in a namespace
    def check_secret_exists(self, secret: str) -> bool:
        current_ns = self.get_namespace()
        try:
            self.client_corev1.read_namespaced_secret(name=secret, namespace=current_ns)
            return True
        except Exception as e:
            if e.status == 404:
                return False
            print(f"error: {e}")
            return False


    def create_backup_job(self, pvc: str, mounted_path: str) -> bool:
        current_ns = self.get_namespace()

        # Ensure secret clustersnap-config exists. It must contains the remote storage access
        secret_required = "clustersnap-config"
        if not self.check_secret_exists(secret_required):
            print(f"error: Secret '{secret_required}' is missing in namespace '{current_ns}'. Cancelling backup job.")
            return False

        job_name = f"backup-{pvc}"[-63:].lower().strip("-")
        print(f"starting backup job: {job_name}")


        # Remove existing jobs
        try:
            batch_api = client.BatchV1Api(self.client_corev1.api_client)
            batch_api.delete_namespaced_job(name=job_name, namespace=current_ns, propagation_policy="Background")
            time.sleep(2)
        except ApiException as e:
            if e.status != 404: # detect other errors that "not found"
                print(f"error: {e}")


        # Apply YAML template file
        project_root = Path(__file__).resolve().parents[2]
        template_path = f"{project_root}/templates/backup.job.yaml"
        try:
            with open(template_path, "r") as f:
                yaml_content = f.read()

            yaml_content = yaml_content.replace("{{ NAMESPACE }}", current_ns)
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


    # Get replicas quantity of a Statefulset or Deployment
    def get_replicas(self, resource_type: str, name: str) -> int:
        current_ns = self.get_namespace()
        try:
            if resource_type.lower() == "statefulset":
                sts = self.client_appsv1.read_namespaced_stateful_set(name=name, namespace=current_ns)
                return sts.spec.replicas
            elif resource_type.lower() == "deployment":
                deploy = self.client_appsv1.read_namespaced_deployment(name=name, namespace=current_ns)
                return deploy.spec.replicas
            else:
                print(f"error: unknown resource type (statefulset, deployment...)'{resource_type}'")
                return 0
        except Exception as e:
            print(f"error: {e}")
            return 0


    # Scale replicas quantity of a Statefulset or Deployment
    def scale_replicas(self, resource_type: str, name: str, replicas: int) -> bool:
        current_ns = self.get_namespace()
        body = {"spec": {"replicas": replicas}}
        
        try:
            if resource_type.lower() == "statefulset":
                self.client_appsv1.patch_namespaced_stateful_set(name=name, namespace=current_ns, body=body)
                return True
            elif resource_type.lower() == "deployment":
                self.client_appsv1.patch_namespaced_deployment(name=name, namespace=current_ns, body=body)
                return True
            else:
                print(f"error: unknown resource type (statefulset, deployment...)'{resource_type}'")
                return False
        except Exception as e:
            print(f"error: {e}")
            return False


    # Hook to ensure replicas are scaled to 0
    def wait_for_scale_down(self, resource_type: str, name: str, timeout: int = 120) -> bool:
        current_ns = self.get_namespace()
        resource = f"{resource_type}/{name}"

        print(f"waiting for {resource} to be fully stopped...")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if resource_type.lower() == "statefulset":
                    res = self.client_appsv1.read_namespaced_stateful_set(name=name, namespace=current_ns)
                    ready = res.status.ready_replicas
                elif resource_type.lower() == "deployment":
                    res = self.client_appsv1.read_namespaced_deployment(name=name, namespace=current_ns)
                    ready = res.status.ready_replicas
                else:
                    print(f"error: unknown resource type (statefulset, deployment...)'{resource_type}'")
                    return False

                if ready is None or ready == 0:
                    time.sleep(3) # security timer to wait for volume detachment
                    print(f"successfully stopped {resource}")
                    return True

            except Exception as e:
                print(f"error: {e}")
                return False
            
        print(f"error: timeout reached while waiting for scale down of {resource}")
        return False
