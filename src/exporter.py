import os
import sys
import argparse
import time

from src.storage.azure import AzureStorage
from src.utils.stream import is_unix_pipe, GzipStreamer, TarGenerator


def main():
    parser = argparse.ArgumentParser(description="Clustersnap Backup Worker")
    parser.add_argument("--path", type=str, required=True, help="Path to backup (directory or UNIX pipe)")
    parser.add_argument("--pvc", type=str, required=True, help="Name of the PVC")
    parser.add_argument("--type", type=str, default=None, choices=["volume", "pg_dump"], help="Type of backup")
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

    backup_type = args.type if args.type else ("pg_dump" if is_unix_pipe(args.path) else "volume")

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
            root_path_target=root_path_target,
        )

    # # AWS/S3
    # elif "s3" in storage_address.lower() or "aws" in storage_address.lower():
    #     # from src.storage.s3 import S3Storage
    #     # storage_provider = S3Storage(storage_address, storage_username, storage_password)
    #     print("error: S3 storage provider is detected but not implemented yet.")
    #     sys.exit(1)

    else:
        print(
            f"error: unable to detect storage provider from STORAGE_ADDRESS ({storage_address}). Please ensure the config secret contains the right URL, because the remote storage name will be automatically calculated from it. Example: https://example.blob.core.windows.net"
        )
        sys.exit(1)

    # ------------------------------
    # BACKUP STREAM EXECUTION
    # ------------------------------
    try:
        # Case 1 : path is UNIX pipe (FIFO) from sidecar container
        if is_unix_pipe(args.path) and backup_type == "pg_dump":
            current_db_file = "/shared/current_db"
            print("info: starting sequential database backup loop over shared UNIX pipe...")

            while True:
                # Waiting for sidecar to give a database target
                while not os.path.exists(current_db_file) or os.path.getsize(current_db_file) == 0:
                    time.sleep(0.2)

                with open(current_db_file, "r") as f:
                    db_name = f.read().strip()

                # "done" is the end signal used here, it must be echo-ed from the sidecar container
                if db_name == "done" or not db_name:
                    print("info: sequential backup loop completed. All databases stored.")
                    # Empty the file one last time to release the sidecar
                    with open(current_db_file, "w") as f:
                        pass
                    break

                print(f"info: catching dump stream for database '{db_name}' from pipe...")

                # Consumption of the pipe stream for the targeted database
                with open(args.path, "rb") as pipe_stream:
                    compressed_generator = GzipStreamer(pipe_stream)

                    success = storage_provider.upload_stream(
                        pvc_name=args.pvc,
                        path=args.path,
                        data_generator=compressed_generator,
                        backup_type=backup_type,
                        db_name=db_name,
                    )

                if not success:
                    print(f"error: failed to stream database '{db_name}' to remote storage.")
                    sys.exit(1)

                print(f"info: database '{db_name}' successfully uploaded. Signaling sidecar.")
                # Instead of deleting the file, empty it cleanly to set its size to 0 bytes
                with open(current_db_file, "w") as f:
                    pass

                # Simple timer before next database
                time.sleep(1)

        # Case 2 : path is a classic directory
        else:
            current_db_file = "/shared/current_db"
            if is_unix_pipe(args.path):
                with open(args.path, "rb") as pipe_stream:
                    compressed_generator = GzipStreamer(pipe_stream)
                    success = storage_provider.upload_stream(
                        pvc_name=args.pvc, path=args.path, data_generator=compressed_generator, backup_type=backup_type
                    )
            else:
                print(f"info: detected directory at '{args.path}'. Initiating tar compression stream...")
                backup_generator = TarGenerator(args.path)
                success = storage_provider.upload_stream(
                    pvc_name=args.pvc, path=args.path, data_generator=backup_generator, backup_type=backup_type
                )

            if not success:
                print("error: storage provider failed to upload volume stream.")
                sys.exit(1)

            if os.path.exists(current_db_file):
                with open(current_db_file, "w") as f:
                    pass

            print("info: backup streaming process completed successfully.")

    except Exception as e:
        print(f"error: backup execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
