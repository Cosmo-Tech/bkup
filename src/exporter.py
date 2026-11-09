from src.lib.remote_storage import RemoteStorage


def main():
    remoteStorage = RemoteStorage()
    remoteStorage.remote_stream_exporter()


if __name__ == "__main__":
    main()
