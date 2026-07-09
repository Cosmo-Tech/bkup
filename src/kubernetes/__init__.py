import base64
import json
import os
from kubernetes import client, config
import src.kubernetes.pvc as pvc
import src.kubernetes.job as job

class Kubernetes:

    def __init__(self, context: str = None, namespace: str = None):
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
            self.client_batchv1 = client.BatchV1Api()
        except Exception as e:
            print(f"error: {e}")


    def get_context(self) -> str:
        return self.context


    def get_namespace(self) -> str:
        return self.namespace


    def get_runtime_namespace(self) -> str:
        """Get namespace during execution"""
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if not os.path.exists(ns_path):
            raise RuntimeError("critical: Kubernetes namespace execution path missing. Aborting.")
        with open(ns_path, "r") as f:
            return f.read().strip()


    def get_cluster_name(self) -> str:
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        if not os.path.exists(token_path):
            raise RuntimeError("critical: Kubernetes service account token missing. Cannot resolve cluster identity.")

        with open(token_path, "r") as f:
            token = f.read().strip()

        try:
            # Decode the JWT token payload without external libraries
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
            
            issuer = payload.get("iss", "")
            if not issuer:
                raise ValueError("Issuer claim 'iss' missing in token.")

            # Clean URL prefixes to get the raw domain/IP identifier
            cluster_id = issuer.replace("https://", "").replace("http://", "").split("/")[0]
            
            # If the issuer uses the generic internal loopback, extract the specific cloud-defined metadata identifier
            if "kubernetes.default" in cluster_id or cluster_id.startswith("10."):
                if "kubernetes" in payload:
                    # Some distributions include accurate multi-cluster metadata here
                    return payload["kubernetes"].get("clusterName", "cluster").lower().strip()
                # Use the core identification part of the OIDC provider domain name mapping
                cluster_id = issuer.split(".")[-1] if "." in issuer else "cluster"

            # Return the clean alphanumeric unique signature part
            return cluster_id.split(".")[0].split("-")[0].lower().strip()

        except Exception as e:
            raise RuntimeError(f"critical: Failed to determine cluster identity from token: {e}")


    def check_secret_exists(self, secret: str) -> bool:
        try:
            self.client_corev1.read_namespaced_secret(name=secret, namespace=self.namespace)
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
