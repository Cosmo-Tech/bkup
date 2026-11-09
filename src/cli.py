import os
import sys
import argparse

# from src.lib.remote_storage import RemoteStorage
from src.lib.kubernetes import Kubernetes
import src.orchestrator as orchestrator

def main():
    parser = argparse.ArgumentParser(description="bkup CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: export
    subparsers.add_parser("export", help="Run a Kubernetes job that will export local data to remote storage")

    # Command: import
    parser_import = subparsers.add_parser("import", help="Run a Kubernetes job that will import a remote archive to local storage")
    parser_import.add_argument("archive_name", help="Name of remote backup file to import")

    # # Command: list
    # parser_list = subparsers.add_parser("list", help="List existing backups in remote storage")
    # parser_list.add_argument("secret_namespace", help="Namespace of the secret containing remote storage password")
    # parser_list.add_argument("secret_name", help="Name of the secret containing remote storage password")
    # parser_list.add_argument("secret_key", help="Key of the secret containing remote storage password")

    args = parser.parse_args()

    # # python -m src.cli list <namespace> <secret> <storage password key>
    # if args.command == "list":
    #     orchestrator.list_remote_backups()


    # python -m src.cli export
    if args.command == "export":
        orchestrator.job_export_volumes()
        orchestrator.job_export_postgresql()

    # python -m src.cli import <archive name>
    elif args.command == "import":
        archive_name = args.archive_name

        # if 'bkup' not in archive_name:
        #     print("error: cannot use non 'bkup' backup files")
        #     sys.exit(1)

        if 'vol' in archive_name:
            orchestrator.job_import_volumes(archive_name=archive_name)
        
        if 'psql' in archive_name:
            orchestrator.job_import_postgresql(archive_name=archive_name)

if __name__ == "__main__":
    main()
