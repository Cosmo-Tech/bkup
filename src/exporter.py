import os
import src.lib.utils as utils
from src.lib.remote_storage import RemoteStorage


def main():
    if os.getenv("BKUP_WAIT_PIPE", "").lower() == "true":
        utils.wait_for_pipe(os.getenv("BKUP_MOUNT_PATH"))

    remoteStorage = RemoteStorage()
    remoteStorage.remote_stream_exporter()


if __name__ == "__main__":
    main()
