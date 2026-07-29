from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError
from src.storage.base import Storage


class AzureStorage(Storage):
    def __init__(self, storage_address: str, storage_username: str, storage_password: str, root_path_target: str):
        self.blob_container = root_path_target.lower().replace("_", "-").replace(".", "-")
        self.storage_address = storage_address
        self.storage_password = storage_password
        self.client = BlobServiceClient(account_url=self.storage_address, credential=self.storage_password)
        self._ensure_blob_container_exists()

    def _ensure_blob_container_exists(self):
        """Creates the Azure container if not exists."""
        try:
            print(f"info: checking or creating Azure container '{self.blob_container}'...")
            self.client.create_container(name=self.blob_container)
            print(f"info: container '{self.blob_container}' successfully created.")
        except ResourceExistsError:
            print(f"info: container '{self.blob_container}' already exists. Reusing it.")
        except Exception as e:
            raise RuntimeError(f"Critical: Failed to verify/create Azure container: {e}")

    def upload_stream(self, pvc_name: str, path: str, data_generator, backup_type: str = None, db_name: str = None) -> bool:
        date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

        # Layout: <backup-type>/pvc_name/pvc_name-timestamp.tar.gz
        if backup_type == "pg_dump":
            target_file_name = f"{pvc_name}-{db_name}" if db_name else pvc_name
            blob_path = f"psql/{pvc_name}/{target_file_name}-{date_str}.sql.gz"

        elif backup_type == "volume":
            blob_path = f"volumes/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        else:
            blob_path = f"unknown/{pvc_name}/{pvc_name}-{date_str}.tar.gz"

        try:
            blob_client = self.client.get_blob_client(container=self.blob_container, blob=blob_path)

            print(f"info: uploading directly to '{blob_path}'...")

            # The Azure SDK natively supports data generators for chunked upload
            blob_client.upload_blob(data_generator, overwrite=True, max_concurrency=2)
            return True
        except Exception as e:
            print(f"error: azure upload failed: {e}")
            return False
