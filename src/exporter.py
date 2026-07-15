import os
import sys
import argparse

from src.storage.azure import AzureStorage
from src.utils.stream import is_unix_pipe, GzipStreamer, TarGenerator


def main():
    parser = argparse.ArgumentParser(description="Clustersnap Backup Worker")
    parser.add_argument("--path", type=str, required=True, help="Path to backup (directory or UNIX pipe)")
    parser.add_argument("--pvc", type=str, required=True, help="Name of the PVC")
    args = parser.parse_args()

    # Env var from YAML template
    storage_address = os.getenv("STORAGE_ADDRESS")
    storage_username = os.getenv("STORAGE_USERNAME")
    storage_password = os.getenv("STORAGE_PASSWORD")
    cluster_name = os.getenv("CLUSTER_NAME")
    namespace = os.getenv("NAMESPACE")

    root_path_target = f"{cluster_name}-{namespace}"

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
    try:
        # Cas 1 : Le chemin est notre Tube UNIX (FIFO / pg_pipe).
        # Il contient le flux pg_dump BRUT. On le compresse à la volée en Python.
        if is_unix_pipe(args.path):
            print(f"info: detected UNIX FIFO pipe at '{args.path}'. Compressing stream dynamically with gzip...")
            with open(args.path, "rb") as pipe_stream:
                # On enveloppe le flux brut dans notre compresseur à la volée
                compressed_generator = GzipStreamer(pipe_stream)
                
                success = storage_provider.upload_stream(
                    pvc_name=args.pvc,
                    path=args.path,
                    data_generator=compressed_generator
                )

        # Cas 2 : Le chemin est un répertoire de stockage classique.
        # On utilise TarGenerator pour compresser le répertoire en .tar.gz à la volée.
        else:
            print(f"info: detected directory at '{args.path}'. Initiating tar compression stream...")
            backup_generator = TarGenerator(args.path)
            success = storage_provider.upload_stream(
                pvc_name=args.pvc,
                path=args.path,
                data_generator=backup_generator
            )

        if success:
            print(f"info: backup streaming process completed successfully.")
        else:
            print("error: storage provider failed to upload stream.")
            sys.exit(1)

    except Exception as e:
        print(f"error: backup execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()