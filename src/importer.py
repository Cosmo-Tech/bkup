# import os
# import sys
# import time
# import tarfile
# import gzip
# import fsspec

# def remote_stream_import():
#     try:
#         protocol            = os.getenv('BKUP_STORAGE_PROTOCOL')
#         endpoint            = os.getenv('BKUP_STORAGE_ENDPOINT')
#         root                = os.getenv('BKUP_STORAGE_ROOT')
#         username            = os.getenv('BKUP_STORAGE_USERNAME')
#         password            = os.getenv('BKUP_STORAGE_PASSWORD')
#         job_namespace       = os.getenv('BKUP_JOB_NAMESPACE')
#         mount_path          = os.getenv('BKUP_MOUNT_PATH')
#         archive_name_prefix = os.getenv('BKUP_ARCHIVE_NAME')

#         if not os.path.exists(mount_path):
#             raise FileNotFoundError(f"failed to find target path '{mount_path}'")

#         # Universal URL working with all drivers (waits for an additional path like f"{protocol}://{root}/{job_namespace}/{additional_path}")
#         address_prefix = f"{protocol}://{root}/{job_namespace}"

#         # Map our generic username/password to each drivers specific keys
#         storage_options = {
#             # Azure Blob (abfs)
#             "account_name": username,
#             "account_key": password,

#             # AWS S3 (s3fs)
#             "key": username,
#             "secret": password,

#             # Others
#             "username": username,
#             "password": password,
#         }

#         print('backup restore informations:')
#         print(f"  protocol...............{protocol}")
#         print(f"  root...................{root}")
#         print(f"  archive_name_prefix....{archive_name_prefix}")
#         print(f"  mount_path.............{mount_path}")
#         print(f"  address_prefix.........{address_prefix}")
#         print(f"  options................{str(storage_options).replace(password, '*********') }")

#         # Overwrite default fsspec endpoint (= s3.amazonaws.com) if it has been defined by user
#         if endpoint:
#             storage_options["client_kwargs"] = {"endpoint_url": endpoint}
#             print(f"endpoint........{endpoint}")

#         signal_file = os.path.join(mount_path, "signal")
#         pipe_file = os.path.join(mount_path, "pipe")

#         # If pipe_file exist (=> file restore backup)
#         if os.path.exists(pipe_file):
#             print(f"detected UNIX pipe at {pipe_file}. Waiting for restore...")

#             while True:
#                 # The "signal" allows to communicate with the sidecar pod of the job
#                 with open(signal_file, "r") as f:
#                     signal = f.read().strip()

#                 if signal == "finished":
#                     print('all backups have been successfully restored.')
#                     break

#                 if signal.startswith("restoring:"):
#                     restore_target = signal.split(":", 1)[1]

#                     archive_name = f"{archive_name_prefix}_{restore_target}.gz"
#                     address = f"{address_prefix}/{archive_name}"
#                     print(f"restoring '{archive_name}' from {address}")

#                     # Open fsspec stream to read remote backup
#                     with fsspec.open(address, "rb", **storage_options) as remote_stream:
#                         with gzip.GzipFile(fileobj=remote_stream, mode="rb") as gz:
#                             # Write data blocks of 1Mo to the pipe
#                             with open(pipe_file, "wb") as pipe:
#                                 while chunk := gz.read(1024 * 1024):
#                                     pipe.write(chunk)

#                     # Sending "done" signal
#                     with open(signal_file, "w") as f:
#                         f.write(f"done:{restore_target}")

#                 time.sleep(1)

#         # If pipe_file doesn't exist (=> volume restore)
#         else:
#             archive_name = f"{archive_name_prefix}.tar.gz"
#             address = f"{address_prefix}/{archive_name}"
#             print(f"restoring '{archive_name}' from {address}")

#             # Open fsspec stream to read remote tar.gz
#             with fsspec.open(address, "rb", **storage_options) as remote_stream:
#                 with tarfile.open(fileobj=remote_stream, mode="r|gz") as tar:
#                     tar.extractall(path=mount_path, filter='data')

#         print("\nrestore completed successfully")

#     except Exception as e:
#         print(f"error: backup import execution failed: {e}")
#         sys.exit(1)



from src.lib.remote_storage import RemoteStorage


def main():
    remoteStorage = RemoteStorage()

    archive_name = os.getenv('BKUP_ARCHIVE_NAME')
    remoteStorage.remote_stream_importer(archive_name=archive_name)

if __name__ == "__main__":
    main()
