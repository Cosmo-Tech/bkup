import base64
import json
import os
from kubernetes import client, config
import src.kubernetes.pvc as pvc
import src.kubernetes.job as job

class Kubernetes:

    def __init__(self, context: str, namespace: str):
        self.context = context
        self.namespace = namespace
        self.is_in_cluster = os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount")
        try:
            # Connect from Kubernetes pod
            if self.is_in_cluster:
                config.load_incluster_config()
            
            # Connect from CLI (based on kubeconfig)
            else:
                config.load_kube_config(context=self.context)

            self.client_corev1 = client.CoreV1Api()
            self.client_appsv1 = client.AppsV1Api()
            self.client_batchv1 = client.BatchV1Api()
        except Exception as e:
            print(f"error: {e}")


    # def get_context(self) -> str:
    #     return self.context


    def get_cluster_name(self) -> str:
        # CLI
        if self.context:
            return self.context.lower().strip()
        
        # Kubernetes pod
        cluster_name = os.environ.get("CLUSTER_NAME")
        if cluster_name:
            return cluster_name.lower().strip()


    def get_namespace(self) -> str:
        # CLI
        if self.namespace:
            return self.namespace

        # Kubernetes pod
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.exists(ns_path):
            with open(ns_path, "r") as f:
                return f.read().strip()

        # # Fallback
        # return os.environ.get("NAMESPACE", "default")


    # def get_runtime_namespace(self) -> str:
    #     """Get namespace during execution"""
    #     ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    #     if not os.path.exists(ns_path):
    #         raise RuntimeError("critical: Kubernetes namespace execution path missing. Aborting.")
    #     with open(ns_path, "r") as f:
    #         return f.read().strip()


    def check_secret_exists(self, secret: str) -> bool:
        try:
            self.client_corev1.read_namespaced_secret(name=secret, namespace=self.get_namespace())
            return True
        except Exception:
            return False

    # ------------------
    # pvc.py
    # ------------------
    def list_pvc(self) -> list:
        return pvc.list_pvc(self)

    def get_pvc(self, pvc_name: str) -> list:
        return pvc.get_pvc(self, pvc_name)

    def get_replicas(self, resource_type: str, name: str) -> int:
        return pvc.get_replicas(self, resource_type, name)

    def scale_replicas(self, resource_type: str, name: str, replicas: int) -> bool:
        return pvc.scale_replicas(self, resource_type, name, replicas)

    def wait_for_scale_down(self, resource_type: str, name: str, timeout: int = 120) -> bool:
        return pvc.wait_for_scale_down(self, resource_type, name, timeout)

    def get_node_name(self, pvc_name: str) -> str:
        return pvc.get_node_name(self, pvc_name)

    # ------------------
    # job.py
    # ------------------
    def get_job_name(self, pvc_name: str) -> str:
        return job.get_job_name(self, pvc_name)

    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str) -> bool:
        return job.create_backup_job(self, pvc_name, mounted_path, node_name)

    def delete_backup_job(self, pvc_name: str) -> bool:
        return job.delete_backup_job(self, pvc_name)

    def wait_for_jobs_deletion(self, pvc_list: list) -> bool:
        return job.wait_for_jobs_deletion(self, pvc_list)

    def wait_for_job_completion(self, job_name: str) -> bool:
        return job.wait_for_job_completion(self, job_name)
