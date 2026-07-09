import os
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError
from src.storage.base import BaseStorage

class AzureStorage(BaseStorage):
    """
    Azure Blob Storage provider implementation with automatic container creation.
    """

    def __init__(self, storage_address: str, storage_username: str, storage_password: str):
        if ".net/" in storage_address:
            self.account_url = storage_address.split(".net/")[0] + ".net"
        else:
            self.account_url = storage_address

        self.container_name = storage_username
        self.account_key = storage_password
        
        # Initialize the global service client
        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=self.account_key
        )
        
        # Automatically handle container creation or reuse
        self._ensure_container_exists()

    def _ensure_container_exists(self):
        """
        Creates the Azure container if it does not exist, otherwise reuses it.
        """
        try:
            print(f"info: checking or creating Azure container '{self.container_name}'...")
            self.blob_service_client.create_container(name=self.container_name)
            print(f"info: container '{self.container_name}' successfully created.")
        except ResourceExistsError:
            # This exception means the container already exists, which is perfectly fine
            print(f"info: container '{self.container_name}' already exists. Reusing it.")
        except Exception as e:
            print(f"error: failed to verify/create Azure container: {e}")

    def upload_stream(self, pvc_name: str, path: str, stream) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        
        # --------------------------------------------------
        # VIRTUAL DIRECTORY ROUTING
        # --------------------------------------------------
        # We dynamicly analyze the PVC name to route it to the right virtual directory
        if "postgresql" in pvc_name.lower() or "psql" in pvc_name.lower():
            folder = "backups/psql"
        else:
            folder = "backups/disks"

        blob_name = f"{folder}/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name, 
                blob=blob_name
            )
            
            print(f"info: uploading directly to '{blob_name}'...")
            blob_client.upload_blob(stream, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}")
            return False