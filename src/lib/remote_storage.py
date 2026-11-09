import os
import sys
import time
import tarfile
import gzip
import fsspec
import argparse

import src.lib.utils as utils
from src.lib.kubernetes import Kubernetes

class RemoteStorage:
    def __init__(self):
        self.protocol                       = os.getenv('BKUP_STORAGE_PROTOCOL')
        self.endpoint                       = os.getenv('BKUP_STORAGE_ENDPOINT')
        self.root                           = os.getenv('BKUP_STORAGE_ROOT')
        self.username                       = os.getenv('BKUP_STORAGE_USERNAME')
        # self.password                     = os.getenv('BKUP_STORAGE_PASSWORD')
        self.storage_password_secret_name   = os.getenv('BKUP_STORAGE_PASSWORD_SECRET_NAME')
        self.storage_password_secret_key    = os.getenv('BKUP_STORAGE_PASSWORD_SECRET_KEY')
        self.archive_name_prefix            = os.getenv('BKUP_ARCHIVE_NAME')
        self.mount_path                     = os.getenv('BKUP_MOUNT_PATH')
        self.job_namespace                  = os.getenv('BKUP_JOB_NAMESPACE')

        # Universal URL working with all drivers (waits for an additional path like f"{protocol}://{root}/{job_namespace}/{additional_path}")
        if self.job_namespace:
            self.address_prefix = f"{self.protocol}://{self.root}/{self.job_namespace}"
        else:
            self.address_prefix = f"{self.protocol}://{self.root}"

        # Securely get password from secret
        kube = Kubernetes()
        self.password = kube.get_secret_value(namespace=self.job_namespace, secret_name=self.storage_password_secret_name, key=self.storage_password_secret_key)

        # Map our generic username/password to each drivers specific keys
        self.storage_options = {
            # Azure Blob (abfs)
            "account_name": self.username,
            "account_key": self.password,

            # AWS S3 (s3fs)
            "key": self.username,
            "secret": self.password,

            # Others
            "username": self.username,
            "password": self.password,
        }

        # Overwrite default fsspec endpoint (= s3.amazonaws.com) if it has been defined by user
        if self.endpoint:
            self.storage_options["client_kwargs"] = {"endpoint_url": self.endpoint}

        self.signal_file = os.path.join(self.mount_path, "signal")
        self.pipe_file = os.path.join(self.mount_path, "pipe")


    def print_remote_storage_informations(self):
        """Print remote storage informations"""
        print('remote storage informations:')

        if self.protocol:
            print(f"protocol...............{self.protocol}")

        if self.root:
            print(f"root...................{self.root}")

        if self.archive_name_prefix:
            print(f"archive_name_prefix....{self.archive_name_prefix}")

        if self.mount_path:
            print(f"mount_path.............{self.mount_path}")

        if self.address_prefix:
            print(f"address_prefix.........{self.address_prefix}")

        if self.storage_options:
            print(f"storage_options........{str(self.storage_options).replace(self.password, '*********') }")

        if self.endpoint:
            print(f"endpoint...............{self.endpoint}")

        print("")


    def remote_stream_exporter(self):
        """Send streamed data to remote storage"""
        if not os.path.exists(self.mount_path):
            raise FileNotFoundError(f"failed to find source path '{self.mount_path}'")

        try:
            self.print_remote_storage_informations()

            # If pipe_file exist (=> file dump backup)
            if os.path.exists(self.pipe_file):
                print(f"detected UNIX pipe at {self.pipe_file}. Waiting for dumps...")

                last_dump_processed = None

                while True:
                    # The "signal" allows to communicate with the sidecar pod of the job
                    with open(self.signal_file, "r") as f:
                        signal = f.read().strip()

                    if signal == "finished":
                        print('all dumps have been successfully exported.')
                        break

                    if signal.startswith("dumping:"):
                        dump_target = signal.split(":", 1)[1]

                        if dump_target == last_dump_processed:
                            time.sleep(1)
                            continue

                        archive_name = f"{self.archive_name_prefix}_{dump_target}.gz"
                        address = f"{self.address_prefix}/{archive_name}"
                        print(f"dumping '{archive_name}' to {address}")

                        # Open fsspec stream enable to read binaries
                        with fsspec.open(address, "wb", **self.storage_options) as remote_stream:
                            with gzip.GzipFile(fileobj=remote_stream, mode="wb") as gz:
                                # Read data blocs of 1MB
                                with open(self.pipe_file, "rb") as pipe:
                                    while chunk := pipe.read(1024 * 1024):
                                        gz.write(chunk)

                        # Sending "done" signal
                        with open(self.signal_file, "w") as f:
                            f.write(f"done:{dump_target}")

                        last_dump_processed = dump_target

                    time.sleep(1)

            # If pipe_file doesn't exist (=> volume backup)
            else:
                archive_name = f"{self.archive_name_prefix}.tar.gz"
                address = f"{self.address_prefix}/{archive_name}"
                print(f"dumping '{archive_name}' to {address}")

                # Open fsspec stream enable to read binaries (which is required for tar)
                with fsspec.open(address, "wb", **self.storage_options) as remote_stream:
                    with tarfile.open(fileobj=remote_stream, mode="w|gz") as tar:
                        for item in os.listdir(self.mount_path):
                            # Ensure mount_path structure is kept in the archive
                            full_path = os.path.join(self.mount_path, item)
                            tar.add(full_path, arcname=item)

            print("\nbackup completed successfully")

        except Exception as e:
            print(f"error: backup export execution failed: {e}")
            sys.exit(1)


    def remote_stream_importer(self, archive_name: str):
        """Copy streamed data from remote to local storage"""
        if not os.path.exists(self.mount_path):
            raise FileNotFoundError(f"failed to find target path '{self.mount_path}'")

        try:
            self.print_remote_storage_informations()

            address = f"{self.address_prefix}/{archive_name}"
            print(f"restoring '{archive_name}' from {address}")

            # If pipe_file exist (=> file restore backup)
            if os.path.exists(self.pipe_file):
                print(f"detected UNIX pipe at {self.pipe_file}. Waiting for restore...")

                while True:
                    # The "signal" allows to communicate with the sidecar pod of the job
                    with open(self.signal_file, "r") as f:
                        signal = f.read().strip()

                    if signal == "finished":
                        print('all backups have been successfully restored.')
                        break

                    if signal.startswith("restoring:"):
                        restore_target = signal.split(":", 1)[1]

                        # Open fsspec stream to read remote backup
                        with fsspec.open(address, "rb", **self.storage_options) as remote_stream:
                            with gzip.GzipFile(fileobj=remote_stream, mode="rb") as gz:
                                # Write data blocks of 1MB to the pipe
                                with open(self.pipe_file, "wb") as pipe:
                                    try:
                                        while chunk := gz.read(1024 * 1024):
                                            pipe.write(chunk)
                                    except BrokenPipeError:
                                      print(f"warning: broken pipe detected while restoring {restore_target}. The sidecar container probably aborted the restoration.")

                        # Sending "done" signal
                        with open(self.signal_file, "w") as f:
                            f.write(f"done:{restore_target}")

                    time.sleep(1)

            # If pipe_file doesn't exist (=> volume restore)
            else:
                # Open fsspec stream to read remote tar.gz
                with fsspec.open(address, "rb", **self.storage_options) as remote_stream:
                    with tarfile.open(fileobj=remote_stream, mode="r|gz") as tar:
                        tar.extractall(path=self.mount_path, filter='data')

            print("\nrestore completed successfully")

        except Exception as e:
            print(f"error: backup import execution failed: {e}")
            sys.exit(1)


    # def list_remote_backups(self):
    #     """Get list of existing remote backups"""
    #     try:
    #         self.print_remote_storage_informations()

    #         fs = fsspec.filesystem(self.protocol, **self.storage_options)
    #         try:
    #             files = fs.ls(self.address_prefix)
    #         except FileNotFoundError:
    #             print(f"no backups found (path {self.address_prefix} does not exist yet)")
    #             return

    #         backups = [f for f in files if fs.info(f).get('type') == 'file']
    #         if not backups:
    #             print("no backups found")
    #         else:
    #             for f in backups:
    #                 name = os.path.basename(f)
    #                 info = fs.info(f)
    #                 size_mb = info.get('size', 0) / (1024 * 1024)
    #                 label = f"{f'{size_mb:.5f}'[:25]} MB"
    #                 print(f"{label:.<30} {name}")

    #     except Exception as e:
    #         print(f"error: {utils.current_function()}: {e}")
    #         sys.exit(1)
