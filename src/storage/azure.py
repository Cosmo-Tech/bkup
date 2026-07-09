import os
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError
from src.storage.base import BaseStorage

class AzureStorage(BaseStorage):
    """
    Azure Blob Storage provider implementation in pure Python.
    Container format: <cluster_name-namespace>
    """

    def __init__(self, storage_address: str, storage_username: str, storage_password: str):
        if ".net/" in storage_address:
            self.account_url = storage_address.split(".net/")[0] + ".net"
        else:
            self.account_url = storage_address

        self.account_key = storage_password
        
        namespace = "unknown-namespace"
        ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.exists(ns_path):
            with open(ns_path, "r") as f:
                namespace = f.read().strip()

        cluster_name = os.getenv("CLUSTER_NAME")
        if not cluster_name:
            account_name = self.account_url.replace("https://", "").replace("http://", "").split(".")[0]
            cluster_name = account_name.replace("-backup", "").replace("backup", "")

        self.blob_container = f"{cluster_name}-{namespace}".lower().replace("_", "-")

        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=self.account_key
        )
        
        self._ensure_container_exists()

    def _ensure_container_exists(self):
        """
        Creates the Azure container if it does not exist.
        """
        try:
            print(f"info: checking or creating Azure container '{self.blob_container}'...", flush=True)
            self.blob_service_client.create_container(name=self.blob_container)
            print(f"info: container '{self.blob_container}' successfully created.", flush=True)
        except ResourceExistsError:
            print(f"info: container '{self.blob_container}' already exists. Reusing it.", flush=True)
        except Exception as e:
            print(f"error: failed to verify/create Azure container: {e}", flush=True)

    def upload_stream(self, pvc_name: str, path: str, data_generator) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        
        # Layout: disks/pvc_name/pvc_name-timestamp.tar.gz
        blob_name = f"disks/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            blob_client = self.blob_service_client.get_blob_client(
                container=self.blob_container, 
                blob=blob_name
            )
            
            print(f"info: uploading directly to '{blob_name}'...", flush=True)
            
            # The Azure SDK natively supports data generators for chunked upload
            blob_client.upload_blob(data_generator, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}", flush=True)
            return False