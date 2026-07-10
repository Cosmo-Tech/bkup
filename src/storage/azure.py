import os
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError
from src.storage.base import Storage


class AzureStorage(Storage):

    def __init__(self, kube, storage_address: str, storage_username: str, storage_password: str):
        cluster_name = kube.get_cluster_name()
        namespace = kube.get_runtime_namespace()
        
        raw_container_name = f"{cluster_name}-{namespace}"
        self.blob_container = raw_container_name.lower().replace("_", "-").replace(".", "-")
        self.storage_address = storage_address
        self.storage_password = storage_password
        self.client = BlobServiceClient(
            account_url=self.storage_address,
            credential=self.storage_password
        )        
        self._ensure_blob_container_exists()


    def _ensure_blob_container_exists(self):
        """Creates the Azure container if it does not exist."""
        try:
            print(f"info: checking or creating Azure container '{self.blob_container}'...")
            self.client.create_container(name=self.blob_container)
            print(f"info: container '{self.blob_container}' successfully created.")
        except ResourceExistsError:
            print(f"info: container '{self.blob_container}' already exists. Reusing it.")
        except Exception as e:
            raise RuntimeError(f"Critical: Failed to verify/create Azure container: {e}")


    def upload_stream(self, pvc_name: str, path: str, data_generator) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        
        # Layout: disks/pvc_name/pvc_name-timestamp.tar.gz
        blob_name = f"disks/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            blob_client = self.client.get_blob_client(
                container=self.blob_container, 
                blob=blob_name
            )
            
            print(f"info: uploading directly to '{blob_name}'...")
            
            # The Azure SDK natively supports data generators for chunked upload
            blob_client.upload_blob(data_generator, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}")
            return False