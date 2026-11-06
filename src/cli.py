import sys
import argparse

from src.lib.remote_storage import RemoteStorage
import src.orchestrator as orchestrator

def main():
    parser = argparse.ArgumentParser(description="bkup CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: export
    subparsers.add_parser("export", help="Run a Kubernetes job that will export local data to remote storage")

    # Command: import
    parser_restore_backup = subparsers.add_parser("import", help="Run a Kubernetes job that will import a remote archive to local storage")
    parser_restore_backup.add_argument("archive_name", help="Name of remote backup file to import")

    # Command: list
    subparsers.add_parser("list", help="List existing backups in remote storage")

    args = parser.parse_args()

    remoteStorage = RemoteStorage()

    if args.command == "list":
        remoteStorage.list_remote_backups()

    elif args.command == "export":
        orchestrator.job_export_volumes()
        orchestrator.job_export_postgresql()

    elif args.command == "import":
        archive_name = args.archive_name

        if 'bkup' not in archive_name:
            print("error: cannot use non 'bkup' backup files")
            sys.exit(1)

        if 'vol' in archive_name:
            orchestrator.job_import_volumes(archive_name=archive_name)
        
        if 'psql' in archive_name:
            orchestrator.job_import_postgresql(archive_name=archive_name)

if __name__ == "__main__":
    main()
