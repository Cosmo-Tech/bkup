import os
import sys
import argparse
import io

from src.storage.azure import AzureStorage
from src.utils.tar import TarGenerator


def main():
    parser = argparse.ArgumentParser(description="Clustersnap Backup Worker")
    parser.add_argument("--path", type=str, required=True, help="Path to backup")
    parser.add_argument("--pvc", type=str, required=True, help="Name of the PVC")
    args = parser.parse_args()

    # Env var from YAML template
    storage_address = os.getenv("STORAGE_ADDRESS")
    storage_username = os.getenv("STORAGE_USERNAME")
    storage_password = os.getenv("STORAGE_PASSWORD")
    cluster_name = os.getenv("CLUSTER_NAME")
    namespace = os.getenv("NAMESPACE")

    root_path_target = f"{cluster_name}-{namespace}"

    # for key, value in os.environ.items():
    #     print('{}: {}'.format(key, value))


    if not storage_address or not storage_username:
        print("error: STORAGE_ADDRESS or STORAGE_USERNAME environment variable is missing.")
        sys.exit(1)

    if not os.path.exists(args.path):
        print(f"error: source path '{args.path}' does not exist.")
        sys.exit(1)

    # ------------------------------
    # STORAGE PROVIDER DETECTION
    # ------------------------------
    print("detecting storage provider from STORAGE_ADDRESS...")

    # Azure (example of Azure Storage endpoint : https://example.blob.core.windows.net/)
    if ".blob.core.windows.net" in storage_address:
        print("info: Azure blob storage detected.")
        storage_provider = AzureStorage(
            storage_address=storage_address,
            storage_username=storage_username,
            storage_password=storage_password,
            root_path_target=root_path_target
        )
    
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