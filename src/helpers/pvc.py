import os
from kubernetes import client, config
from kubernetes.client.rest import ApiException

class KubernetesPVC:
    def __init__(self):
        """in-cluster init config"""
        try:
            # Charge automatiquement le token et le certificat du ServiceAccount du Pod
            config.load_incluster_config()
            self.v1 = client.CoreV1Api()
        except Exception as e:
            raise RuntimeError(f"error: cannot load config in-cluster. Am I a pod ? {e}")

    def get_current_namespace(self) -> str:
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.exists(ns_path):
            with open(ns_path, "r") as f:
                return f.read().strip()
        return "default"

    def list_pvc(self) -> list:
        """Liste les PVCs du namespace actuel."""
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
            print(f"error: RBAC / API in namespace '{current_ns}': {e}")
            return []

if __name__ == "__main__":
    lister = InClusterPVCLister()
    mon_ns = lister.get_current_namespace()
    print(f"--- PVCs dans mon namespace actuel ({mon_ns}) ---")
    for pvc in lister.list_pvc():
        print(f"-> {pvc['name']} [{pvc['status']}] - {pvc['capacity']}")