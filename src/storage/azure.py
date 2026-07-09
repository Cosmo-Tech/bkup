import os
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from src.storage.base import BaseStorage

class AzureStorage(BaseStorage):
    """
    Azure Blob Storage provider implementation using Account Key.
    """

    def __init__(self, storage_address: str, storage_username: str, storage_password: str):
        # If the address contains the container name at the end (e.g., /tenant-test0), 
        # we extract only the root endpoint URL: https://account.blob.core.windows.net
        if ".net/" in storage_address:
            self.account_url = storage_address.split(".net/")[0] + ".net"
        else:
            self.account_url = storage_address

        self.account_name = storage_username
        self.account_key = storage_password

    def upload_stream(self, pvc_name: str, path: str, stream) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        blob_name = f"backups/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            # Initialize using the URL endpoint and the storage account access key
            blob_service_client = BlobServiceClient(
                account_url=self.account_url,
                credential=self.account_key
            )
            
            # We use self.account_name (from storage-username) as the target container name
            blob_client = blob_service_client.get_blob_client(container=self.account_name, blob=blob_name)
            
            print(f"info: uploading to container '{self.account_name}' on account '{self.account_url}'")
            blob_client.upload_blob(stream, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}")
            return False