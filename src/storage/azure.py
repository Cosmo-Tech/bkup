import os
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from src.storage.base import BaseStorage

class AzureStorage(BaseStorage):
    """
    Azure Blob Storage provider implementation.
    """

    def __init__(self, storage_address: str, storage_username: str, storage_password: str):
        self.connection_string = storage_address
        self.container_name = storage_username

    def upload_stream(self, pvc_name: str, path: str, stream) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        blob_name = f"backups/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
            blob_client = blob_service_client.get_blob_client(container=self.container_name, blob=blob_name)
            
            # max_concurrency can be adapted based on target volume sizes
            blob_client.upload_blob(stream, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}")
            return False