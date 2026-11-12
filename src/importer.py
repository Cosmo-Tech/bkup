import os
from src.lib.remote_storage import RemoteStorage


def main():
    remoteStorage = RemoteStorage()

    archive_name = os.getenv('BKUP_ARCHIVE_NAME')
    remoteStorage.remote_stream_importer(archive_name=archive_name)


if __name__ == "__main__":
    main()
