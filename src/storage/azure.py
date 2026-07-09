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
        
        # Dynamic container naming
        cluster_name = os.getenv("CLUSTER_NAME", "kob-dev")
        namespace = os.getenv("NAMESPACE", storage_username)
        self.container_name = f"{cluster_name}-{namespace}".lower().replace("_", "-")
        
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
            print(f"info: checking or creating Azure container '{self.container_name}'...", flush=True)
            self.blob_service_client.create_container(name=self.container_name)
            print(f"info: container '{self.container_name}' successfully created.", flush=True)
        except ResourceExistsError:
            print(f"info: container '{self.container_name}' already exists. Reusing it.", flush=True)
        except Exception as e:
            print(f"error: failed to verify/create Azure container: {e}", flush=True)

    def upload_stream(self, pvc_name: str, path: str, data_generator) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        
        # Layout: disks/pvc_name/pvc_name-timestamp.tar.gz
        blob_name = f"disks/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name, 
                blob=blob_name
            )
            
            print(f"info: uploading directly to '{blob_name}'...", flush=True)
            
            # The Azure SDK natively supports data generators for chunked upload
            blob_client.upload_blob(data_generator, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}", flush=True)
            return False