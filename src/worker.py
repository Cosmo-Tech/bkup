import os
import sys
import argparse
import tarfile
import io

# Import the storage implementations
from src.storage.azure import AzureStorage

class QueueProgressStream(io.RawIOBase):
    def __init__(self):
        self.buffer = bytearray()

    def write(self, b):
        self.buffer.extend(b)
        return len(b)

    def readinto(self, b):
        size = min(len(b), len(self.buffer))
        b[:size] = self.buffer[:size]
        del self.buffer[:size]
        return size

    def readable(self):
        return True

    def writable(self):
        return True


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



    for name, value in os.environ.items():
        print("{0}: {1}".format(name, value))

    print(storage_address)
    print(storage_username)
    print(storage_password)



    # Detect if Azure
    # Example of Azure Storage endpoint : https://storageaccount.blob.core.windows.net/
    if ".core.windows.net" in storage_address:
        print("info: Azure blob storage detected.")
        storage_provider = AzureStorage(storage_address, storage_username, storage_password)
    
    # # Detect if S3
    # elif "s3" in storage_address.lower() or "aws" in storage_address.lower():
    #     # from src.storage.s3 import S3Storage
    #     # storage_provider = S3Storage(storage_address, storage_username, storage_password)
    #     print("error: S3 storage provider is detected but not implemented yet.")
    #     sys.exit(1)
        
    else:
        print("error: unable to detect storage provider from STORAGE_ADDRESS.")
        sys.exit(1)




    # --------------------------------------------------
    # BACKUP STREAM EXECUTION
    # --------------------------------------------------
    print(f"info: starting on-the-fly compression and streaming to destination...")

    try:
        stream = QueueProgressStream()
        
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            tar.add(args.path, arcname=os.path.basename(args.path))
            
            # Pass the stream to the dynamically selected provider
            success = storage_provider.upload_stream(pvc_name=args.pvc, path=args.path, stream=stream)

        if success:
            print(f"info: backup successfully streamed to destination storage.")
        else:
            sys.exit(1)

    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()