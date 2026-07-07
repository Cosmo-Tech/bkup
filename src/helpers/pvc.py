import os
from kubernetes import client, config
from kubernetes.client.rest import ApiException

class Kubernetes:
    def __init__(self):
        try:
            config.load_incluster_config()
            self.v1 = client.CoreV1Api()
        except Exception as e:
            print(f"error: {e}")

    def get_current_namespace(self) -> str:
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.exists(ns_path):
            with open(ns_path, "r") as f:
                return f.read().strip()
        return "default"


    def list_pvc(self) -> list:
        current_ns = self.get_current_namespace()
        pvc_list = []
        
        try:
            response = self.v1.list_namespaced_persistent_volume_claim(namespace=current_ns)
            for pvc in response.items:
                pvc_list.append({
                    "name": pvc.metadata.name,
                    "status": pvc.status.phase,
                    "capacity": pvc.status.capacity.get("storage") if pvc.status.capacity else "N/A"
                })
            return pvc_list
        except ApiException as e:
            print(f"error: {e}")
            return []

