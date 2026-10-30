import os
import sys
import tarfile
import fsspec
from urllib.parse import quote

def remote_stream():
    try:
        protocol            = os.getenv('BKUP_STORAGE_PROTOCOL')
        endpoint            = os.getenv('BKUP_STORAGE_ENDPOINT')
        root                = os.getenv('BKUP_STORAGE_ROOT')
        username            = os.getenv('BKUP_STORAGE_USERNAME')
        password            = os.getenv('BKUP_STORAGE_PASSWORD')

        archive_name        = f"{os.getenv('BKUP_ARCHIVE_NAME')}.tar.gz"

        mount_path          = os.getenv('BKUP_MOUNT_PATH')
        if not os.path.exists(mount_path):
            raise FileNotFoundError(f"failed to find source directory '{mount_path}'")

        # Universal URL working with all drivers
        address = f"{protocol}://{root}/{archive_name}"

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

        # Overwrite default fsspec endpoint (= s3.amazonaws.com) if it has been defined by user
        if endpoint:
            storage_options["client_kwargs"] = {"endpoint_url": endpoint}

        print(f"backup information:")
        print(f"protocol:       {protocol}")
        print(f"endpoint:       {endpoint}")
        print(f"root:           {root}")
        print(f"archive_name:   {archive_name}")
        print(f"mount_path:     {mount_path}")
        print(f"address:        {address}")
        print(f"options:        {str(storage_options).replace(password, "*********") }")

        fs, target_path = fsspec.core.url_to_fs(address, **storage_options)

        print(f"fs:             {fs}")
        print(f"target_path:    {target_path}")

        # Open fsspec stream enable to read binaries (which is required for tar)
        with fsspec.open(address, "wb", **storage_options) as remote_stream:
            # Open a gzip pipe to destination storage
            with tarfile.open(fileobj=remote_stream, mode="w|gz") as tar:
                for item in os.listdir(mount_path):
                    # Ensure mount_path structure is kept in the archive
                    full_path = os.path.join(mount_path, item)
                    tar.add(full_path, arcname=item)

        print("backup completed successfully")

    except Exception as e:
        print(f"error: backup execution failed: {e}")
        sys.exit(1)


def main():
    remote_stream()


if __name__ == "__main__":
    main()
