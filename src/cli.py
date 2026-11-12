import os
import sys
# import importlib
import argparse
from InquirerPy import inquirer

from src.lib.kubernetes import Kubernetes
from src.lib.remote_storage import RemoteStorage


# def selectable_menu_instance(kube: Kubernetes, target_instance: str = None) -> dict:
def selectable_menu_instance(instances: list, target_instance: str = None) -> dict:
    """Selectable menu to browse instances"""
    # instances = kube.list_bkup_instances()

    if not instances:
        print("error: no bkup instances found in the cluster.")
        sys.exit(1)

    if target_instance:
        for i in instances:
            if i["name"] == target_instance:
                return i
        print(f"error: instance '{target_instance}' not found.")
        sys.exit(1)

    choices = [
        {"name": f"{inst['namespace']}....{inst['name']}", "value": inst}
        for inst in instances
    ]

    try:
        selected_instance = inquirer.select(message="Please load config from a bkup instance:", choices=choices, pointer=">", qmark="", cycle=True).execute()
    except KeyboardInterrupt:
        print("\noperation cancelled")
        sys.exit(1)

    return selected_instance


def main():
    parser = argparse.ArgumentParser(description="bkup CLI")

    parser.add_argument("-c", "--kube-context", help="Kube context where to perform the command")
    parser.add_argument("-i", "--instance", help="Name of the bkup instance to use")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: export
    subparsers.add_parser("export", help="Run a Kubernetes job that will export local data to remote storage")

    # Command: import
    parser_import = subparsers.add_parser("import", help="Run a Kubernetes job that will import a remote archive to local storage")
    parser_import.add_argument("-n", "--namespace", help="Namespace where to import remote backup file")
    parser_import.add_argument("-f", "--file", help="Name of remote backup file to import")

    # Command: list
    subparsers.add_parser("list", help="List existing backups in remote storage")

    args = parser.parse_args()


    kube_context = args.kube_context


    if args.command in ["import", "list"]:
        kube = Kubernetes(context=kube_context)
        instances = kube.list_bkup_instances()
        instance = selectable_menu_instance(instances=instances, target_instance=args.instance)
        instance_namespace = instance['namespace']
        instance_name = instance['name']

        print(f"loading configuration from instance {instance_namespace}/{instance_name}...\n")
        envs = kube.get_bkup_instance_envs(namespace=instance_namespace, instance_name=instance_name)
        if kube_context:
            envs['BKUP_KUBE_CONTEXT'] = kube_context

        if envs:
            # Inject variables into the current process
            os.environ.update(envs)
        else:
            print(f"error: failed to retrieve environment variables from the instance {instance_namespace}/{instance_name}")
            sys.exit(1)


    # We only import the orchestrator now, because os.environ has been properly populated
    import src.orchestrator as orchestrator

    # # Just in case it was already loaded somewhere else in memory, we force a reload
    # if 'src.orchestrator' in sys.modules:
    #     importlib.reload(orchestrator)


    if args.command == "list":
        remoteStorage = RemoteStorage()
        remoteStorage.list_remote_backups()


    elif args.command == "export":
        orchestrator.job_export_volumes()
        orchestrator.job_export_postgresql()


    elif args.command == "import":
        import_namespace = args.namespace
        import_file = args.file

        if '.tar.gz' in import_file:
            orchestrator.job_import_volume(namespace=import_namespace, archive_name=import_file)

        if 'psql' in import_file:
            orchestrator.job_import_postgresql(archive_name=import_file)

if __name__ == "__main__":
    main()
