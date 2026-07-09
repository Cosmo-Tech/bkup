import os
import sys
import argparse
import tarfile
import io

from src.storage.azure import AzureStorage
from src.kubernetes import Kubernetes


class TarGenerator:
    """Pure Python on-the-fly tar.gz generator to stream data without local disk footprint."""
    def __init__(self, target_path: str):
        self.target_path = target_path
        self.buffer = io.BytesIO()

    def __iter__(self):
        # Open tarfile linked to our memory buffer
        with tarfile.open(fileobj=self.buffer, mode="w:gz") as tar:
            # Add the directory to the archive structure
            tar.add(self.target_path, arcname=os.path.basename(self.target_path))
            
            # While tarfile is compressing, extract data chunk by chunk
            self.buffer.seek(0)
            chunk = self.buffer.read()
            if chunk:
                yield chunk
            self.buffer.seek(0)
            self.buffer.truncate(0)

        # Grab any remaining bytes after tar closure
        self.buffer.seek(0)
        remaining = self.buffer.read()
        if remaining:
            yield remaining



def main():
    parser = argparse.ArgumentParser(description="Clustersnap Backup Worker")
    parser.add_argument("--path", type=str, required=True, help="Path to backup")
    parser.add_argument("--pvc", type=str, required=True, help="Name of the PVC")
    args = parser.parse_args()

    storage_address = os.getenv("STORAGE_ADDRESS")
    storage_username = os.getenv("STORAGE_USERNAME")
    storage_password = os.getenv("STORAGE_PASSWORD", "none")

    if not storage_address or not storage_username:
        print("error: STORAGE_ADDRESS or STORAGE_USERNAME environment variable is missing.")
        sys.exit(1)

    if not os.path.exists(args.path):
        print(f"error: source path '{args.path}' does not exist.")
        sys.exit(1)

    # kube = Kubernetes(context=None, namespace=None)
    kube = Kubernetes()

    # ------------------------------
    # STORAGE PROVIDER DETECTION
    # ------------------------------
    print("detecting storage provider from STORAGE_ADDRESS...")

    # Azure (example of Azure Storage endpoint : https://storageaccount.blob.core.windows.net/)
    if ".blob.core.windows.net" in storage_address:
        print("info: Azure blob storage detected.")
        storage_provider = AzureStorage(kube, storage_address, storage_username, storage_password)
    
    # # AWS/S3
    # elif "s3" in storage_address.lower() or "aws" in storage_address.lower():
    #     # from src.storage.s3 import S3Storage
    #     # storage_provider = S3Storage(storage_address, storage_username, storage_password)
    #     print("error: S3 storage provider is detected but not implemented yet.")
    #     sys.exit(1)
        
    else:
        print("error: unable to detect storage provider from STORAGE_ADDRESS.")
        sys.exit(1)



    # ------------------------------
    # BACKUP STREAM EXECUTION
    # ------------------------------
    print(f"starting on-the-fly compression and streaming to destination...")
    try:
        # Instantiate our pure Python streaming generator
        backup_generator = TarGenerator(args.path)

        # Pass the generator directly to the storage provider
        success = storage_provider.upload_stream(
            pvc_name=args.pvc, 
            path=args.path, 
            data_generator=backup_generator
        )

        if success:
            print(f"info: backup successfully streamed to destination storage.")
        else:
            sys.exit(1)

    except Exception as e:
        print(f"error: backup execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()