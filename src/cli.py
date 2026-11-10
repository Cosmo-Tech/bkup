import os
import sys
import importlib
import argparse
from InquirerPy import inquirer

from src.lib.kubernetes import Kubernetes
from src.lib.remote_storage import RemoteStorage


def select_instance(kube: Kubernetes, requested_instance: str = None) -> dict:
    instances = kube.list_bkup_instances()

    if not instances:
        print("error: no bkup instances found in the cluster.")
        sys.exit(1)

    if requested_instance:
        for i in instances:
            if i["name"] == requested_instance:
                return i
        print(f"error: instance '{requested_instance}' not found.")
        sys.exit(1)

    choices = [
        {"name": f"{inst['namespace']}....{inst['name']}", "value": inst}
        for inst in instances
    ]

    try:
        selected_instance = inquirer.select(
            message="Please load config from a bkup instance:",
            choices=choices,
            pointer=">",
            qmark="",
            cycle=True
        ).execute()
    except KeyboardInterrupt:
        print("\noperation cancelled")
        sys.exit(1)

    return selected_instance


def main():
    parser = argparse.ArgumentParser(description="bkup CLI")

    parser.add_argument("-i", "--instance", help="Name of the bkup instance to use")
    parser.add_argument("-n", "--namespace", help="Namespace of the bkup instance (optional)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: export
    subparsers.add_parser("export", help="Run a Kubernetes job that will export local data to remote storage")

    # Command: import
    parser_import = subparsers.add_parser("import", help="Run a Kubernetes job that will import a remote archive to local storage")
    parser_import.add_argument("archive_name", help="Name of remote backup file to import")

    # Command: list
    subparsers.add_parser("list", help="List existing backups in remote storage")

    args = parser.parse_args()

    if args.command in ["import", "list"]:
        kube = Kubernetes()
        selected = select_instance(kube=kube, requested_instance=args.instance)

        print(f"info: loading configuration from instance {selected['namespace']}/{selected['name']}...\n")
        envs = kube.get_instance_envs(namespace=selected['namespace'], instance_name=selected['name'])

        if envs:
            # Inject variables into the current process
            os.environ.update(envs)
        else:
            print("error: failed to retrieve environment variables from the instance.")
            sys.exit(1)


    # We only import the orchestrator now, because os.environ has been properly populated
    import src.orchestrator as orchestrator

    # Just in case it was already loaded somewhere else in memory, we force a reload
    if 'src.orchestrator' in sys.modules:
        importlib.reload(orchestrator)

    # python -m src.cli list <namespace> <secret> <storage password key>
    if args.command == "list":
        remoteStorage = RemoteStorage()
        remoteStorage.list_remote_backups()


    # python -m src.cli export
    elif args.command == "export":
        orchestrator.job_export_volumes()
        orchestrator.job_export_postgresql()

    # python -m src.cli import <archive name>
    elif args.command == "import":
        archive_name = args.archive_name

        if 'vol' in archive_name:
            orchestrator.job_import_volumes(archive_name=archive_name)

        if 'psql' in archive_name:
            orchestrator.job_import_postgresql(archive_name=archive_name)

if __name__ == "__main__":
    main()
