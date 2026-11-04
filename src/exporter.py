import os
import sys
import time
import tarfile
import gzip
import fsspec
from urllib.parse import quote

def remote_stream():
    try:
        protocol            = os.getenv('BKUP_STORAGE_PROTOCOL')
        endpoint            = os.getenv('BKUP_STORAGE_ENDPOINT')
        root                = os.getenv('BKUP_STORAGE_ROOT')
        username            = os.getenv('BKUP_STORAGE_USERNAME')
        password            = os.getenv('BKUP_STORAGE_PASSWORD')

        job_namespace       = os.getenv('BKUP_JOB_NAMESPACE')

        mount_path          = os.getenv('BKUP_MOUNT_PATH')
        if not os.path.exists(mount_path):
            raise FileNotFoundError(f"failed to find source path '{mount_path}'")

        signal_file = os.path.join(mount_path, "signal")
        pipe_file = os.path.join(mount_path, "pipe")

        archive_name_prefix = os.getenv('BKUP_ARCHIVE_NAME')

        # Universal URL working with all drivers
        address_prefix = f"{protocol}://{root}/{job_namespace}"
        # address_prefix = f"{protocol}://{root}/{job_namespace}/{archive_name}"

        # Map our generic username/password to each drivers specific keys
        storage_options = {
            # Azure Blob (abfs)
            "account_name": username,
            "account_key": password,

            # AWS S3 (s3fs)
            "key": username,
            "secret": password,

            # Others
            "username": username,
            "password": password,
        }

        print(f"backup informations:")
        print(f"  protocol...............{protocol}")
        print(f"  root...................{root}")
        print(f"  archive_name_prefix....{archive_name_prefix}")
        print(f"  mount_path.............{mount_path}")
        print(f"  address_prefix.........{address_prefix}")
        print(f"  options................{str(storage_options).replace(password, "*********") }")

        # Overwrite default fsspec endpoint (= s3.amazonaws.com) if it has been defined by user
        if endpoint:
            storage_options["client_kwargs"] = {"endpoint_url": endpoint}
            print(f"endpoint........{endpoint}")

        # If pipe_file exist (=> file dump backup)
        if os.path.exists(pipe_file):
            print(f"detected UNIX pipe at {pipe_file}. Waiting for dumps...")

            while True:
                # The "signal" allows to communicate with the sidecar pod of the job
                with open(signal_file, "r") as f:
                    signal = f.read().strip()

                if signal == "finished":
                    print("all dumps have been successfully exported.")
                    break

                if signal.startswith("dumping:"):
                    dump_target = signal.split(":", 1)[1]

                    archive_name = f"{archive_name_prefix}_{dump_target}.gz"
                    address = f"{address_prefix}/{archive_name}"
                    print(f"dumping '{archive_name}' to {address}")

                    # Open fsspec stream enable to read binaries
                    with fsspec.open(address, "wb", **storage_options) as remote_stream:
                        with gzip.GzipFile(fileobj=remote_stream, mode="wb") as gz:
                            # Read data blocs of 1Mo
                            with open(pipe_file, "rb") as pipe:
                                while chunk := pipe.read(1024 * 1024):
                                    gz.write(chunk)

                    # Sending "done" signal
                    with open(signal_file, "w") as f:
                        f.write(f"done:{dump_target}")

                time.sleep(1)

        # If pipe_file doesn't exist (=> volume backup)
        else:
            archive_name = f"{archive_name_prefix}.tar.gz"
            address = f"{address_prefix}/{archive_name}"
            print(f"dumping '{archive_name}' to {address}")

            # Open fsspec stream enable to read binaries (which is required for tar)
            with fsspec.open(address, "wb", **storage_options) as remote_stream:
                with tarfile.open(fileobj=remote_stream, mode="w|gz") as tar:
                    for item in os.listdir(mount_path):
                        # Ensure mount_path structure is kept in the archive
                        full_path = os.path.join(mount_path, item)
                        tar.add(full_path, arcname=item)


        # # Open fsspec stream enable to read binaries (which is required for tar)
        # with fsspec.open(address, "wb", **storage_options) as remote_stream:

        #     # If pipe_file exist => .gz (database dump backup)
        #     if os.path.exists(pipe_file):
        #         print(f"detected UNIX pipe at {pipe_file}. Waiting for dumps...")

        #         with gzip.GzipFile(fileobj=remote_stream, mode="wb") as gz:
        #             while True:
        #                 # The "signal" allows to communicate with the sidecar pod of the job
        #                 with open(signal_file, "r") as f:
        #                     signal = f.read().strip()

        #                 if signal == "finished":
        #                     print("all dumps have been successfully exported.")
        #                     break

        #                 if signal.startswith("dumping:"):
        #                     dump_target = signal.split(":", 1)[1]
        #                     dump_file = f"{dump_target}.sql"
        #                     print(f"dumping '{dump_target}' to {dump_file}")

        #                     # Read data blocs of 1Mo
        #                     with open(pipe_file, "rb") as pipe:
        #                         while chunk := pipe.read(1024 * 1024):
        #                             gz.write(chunk)

        #                     # Sending "done" signal
        #                     with open(signal_file, "w") as f:
        #                         f.write(f"done:{dump_target}")

        #                 time.sleep(1)

        #         # with gzip.GzipFile(fileobj=remote_stream, mode="wb") as gz:
        #         #     # 'read as binaries'
        #         #     with open(pipe_file, "rb") as pipe:
        #         #         while chunk := pipe.read(1024 * 1024):
        #         #             gz.write(chunk)

        #         # with open(signal_file, "r") as f:
        #         #     signal = f.read().strip()
        #         #     while signal != "finished":
        #         #         print(f"waiting for sidecar signal 'finished' (currently '{signal}')...")
        #         #         time.sleep(1)

        #     # If pipe_file doesn't exist => .tar.gz (= volume backup)
        #     else:
        #         with tarfile.open(fileobj=remote_stream, mode="w|gz") as tar:
        #             for item in os.listdir(mount_path):
        #                 # Ensure mount_path structure is kept in the archive
        #                 full_path = os.path.join(mount_path, item)
        #                 tar.add(full_path, arcname=item)

        print("\nbackup completed successfully")

    except Exception as e:
        print(f"error: backup execution failed: {e}")
        sys.exit(1)


def main():
    remote_stream()


if __name__ == "__main__":
    main()
