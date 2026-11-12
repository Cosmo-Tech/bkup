# import os
# import sys
# # import importlib
# import argparse
# from InquirerPy import inquirer

# from src.lib.kubernetes import Kubernetes
# from src.lib.remote_storage import RemoteStorage


# # def selectable_menu_instance(kube: Kubernetes, target_instance: str = None) -> dict:
# def selectable_menu_instance(instances: list, target_instance: str = None) -> dict:
#     """Selectable menu to browse instances"""
#     # instances = kube.list_bkup_instances()

#     if not instances:
#         print("error: no bkup instances found in the cluster.")
#         sys.exit(1)

#     if target_instance:
#         for i in instances:
#             if i["name"] == target_instance:
#                 return i
#         print(f"error: instance '{target_instance}' not found.")
#         sys.exit(1)

#     choices = [
#         {"name": f"{inst['namespace']}....{inst['name']}", "value": inst}
#         for inst in instances
#     ]

#     try:
#         selected_instance = inquirer.select(message="Please load config from a bkup instance:", choices=choices, pointer=">", qmark="", cycle=True).execute()
#     except KeyboardInterrupt:
#         print("\noperation cancelled")
#         sys.exit(1)

#     return selected_instance


# def main():
#     parser = argparse.ArgumentParser(description="bkup CLI")

#     parser.add_argument("-c", "--kube-context", help="Kube context where to perform the command")
#     parser.add_argument("-i", "--instance", help="Name of the bkup instance to use")

#     subparsers = parser.add_subparsers(dest="command", required=True)

#     # Command: export
#     subparsers.add_parser("export", help="Run a Kubernetes job that will export local data to remote storage")

#     # Command: import
#     parser_import = subparsers.add_parser("import", help="Run a Kubernetes job that will import a remote archive to local storage")
#     parser_import.add_argument("-n", "--namespace", help="Namespace where to import remote backup file")
#     parser_import.add_argument("-f", "--file", help="Name of remote backup file to import")

#     # Command: list
#     subparsers.add_parser("list", help="List existing backups in remote storage")

#     args = parser.parse_args()


#     kube_context = args.kube_context


#     if args.command in ["import", "list"]:
#         kube = Kubernetes(context=kube_context)
#         instances = kube.list_bkup_instances()
#         instance = selectable_menu_instance(instances=instances, target_instance=args.instance)
#         instance_namespace = instance['namespace']
#         instance_name = instance['name']

#         print(f"loading configuration from instance {instance_namespace}/{instance_name}...\n")
#         envs = kube.get_bkup_instance_envs(namespace=instance_namespace, instance_name=instance_name)
#         if kube_context:
#             envs['BKUP_KUBE_CONTEXT'] = kube_context

#         if envs:
#             # Inject variables into the current process
#             os.environ.update(envs)
#         else:
#             print(f"error: failed to retrieve environment variables from the instance {instance_namespace}/{instance_name}")
#             sys.exit(1)


#     # We only import the orchestrator now, because os.environ has been properly populated
#     import src.orchestrator as orchestrator

#     # # Just in case it was already loaded somewhere else in memory, we force a reload
#     # if 'src.orchestrator' in sys.modules:
#     #     importlib.reload(orchestrator)


#     if args.command == "list":
#         remoteStorage = RemoteStorage()
#         remoteStorage.list_remote_backups()


#     elif args.command == "import":
#         import_namespace = args.namespace
#         import_file = args.file

#         if '.tar.gz' in import_file:
#             orchestrator.job_import_volume(namespace=import_namespace, archive_name=import_file)

#         if 'psql' in import_file:
#             orchestrator.job_import_postgresql(archive_name=import_file)


#     elif args.command == "export":
#         orchestrator.job_export_volumes()
#         orchestrator.job_export_postgresql()


# if __name__ == "__main__":
#     main()


import os
import sys
import re
import argparse
from InquirerPy import inquirer
from InquirerPy.utils import get_style

from src.lib.kubernetes import Kubernetes
from src.lib.remote_storage import RemoteStorage

menu_style = get_style({
    "questionmark": "#e5c07b",
    "answer": "#61afef bold",
    "pointer": "#61afef bold",
    "selected": "#98c379",
}, style_override=False)


def format_date(raw_date: str) -> str:
    """Format a yymmddhhmmss string to yy/mm/dd hh:mm:ss (yymmddhhmmss)"""
    if len(raw_date) == 12 and raw_date.isdigit():
        return f"{raw_date[:2]}/{raw_date[2:4]}/{raw_date[4:6]} {raw_date[6:8]}:{raw_date[8:10]}:{raw_date[10:12]} ({raw_date})"
    return raw_date


def get_grouped_backups(remote_storage: RemoteStorage) -> dict:
    """Fetch remote backups and group them by timestamp"""
    fs = remote_storage.get_filesystem()
    try:
        entries = fs.find(remote_storage.address_prefix, detail=True)
    except FileNotFoundError:
        return {}

    backups = {path: info for path, info in entries.items() if info.get('type') == 'file'}
    grouped = {}
    date_pattern = re.compile(r'_(\d{12})(?:.*)\.(tar\.gz|gz)$')

    for path, info in backups.items():
        clean_filename = os.path.basename(path)
        match = date_pattern.search(clean_filename)
        if match:
            date_stamp = match.group(1)
            if date_stamp not in grouped:
                grouped[date_stamp] = []
            grouped[date_stamp].append(clean_filename)
        else:
            if "other" not in grouped:
                grouped["other"] = []
            grouped["other"].append(clean_filename)

    return grouped


def import_wizard(args, kube_context):
    """State machine wizard to navigate the import selection process"""
    kube = Kubernetes(context=kube_context)
    
    # Skip instance selection if config is already injected in environment (e.g., in-cluster CronJob/Job)
    in_cluster_config = bool(os.getenv("BKUP_ORCHESTRATOR_NAMESPACE"))
    step = 2 if in_cluster_config else 1

    selected_instance = None
    selected_namespace = None
    selected_mode = None
    selected_date = None
    selected_files = []
    grouped_cache = {}
    
    while True:
        if step == 1:
            instances = kube.list_bkup_instances()
            if not instances:
                print("Error: no bkup instances found in the cluster.")
                sys.exit(1)

            if args.instance:
                match = next((i for i in instances if i['name'] == args.instance), None)
                if match:
                    selected_instance = match
                    step = 2
                    continue
                else:
                    print(f"Error: instance '{args.instance}' not found.")
                    sys.exit(1)
            else:
                choices = [{"name": f"{inst['namespace']}....{inst['name']}", "value": inst} for inst in instances]
                
                ans = inquirer.select(
                    message="Please load config from a bkup instance:",
                    choices=choices, pointer=">", qmark="?", amark="✓", style=menu_style, mandatory=False
                ).execute()
                
                if ans is None:
                    sys.exit(0)
                
                selected_instance = ans
                step = 2

        elif step == 2:
            if not in_cluster_config:
                envs = kube.get_bkup_instance_envs(namespace=selected_instance['namespace'], instance_name=selected_instance['name'])
                if kube_context:
                    envs['BKUP_KUBE_CONTEXT'] = kube_context
                os.environ.update(envs)
            elif kube_context:
                os.environ['BKUP_KUBE_CONTEXT'] = kube_context

            if args.namespace:
                selected_namespace = args.namespace
                os.environ["BKUP_JOB_NAMESPACE"] = selected_namespace
                step = 3
                continue
            else:
                os.environ.pop("BKUP_JOB_NAMESPACE", None)
                rs_root = RemoteStorage()
                try:
                    entries = rs_root.get_filesystem().ls(rs_root.address_prefix, detail=True)
                except Exception:
                    entries = []
                
                namespaces = [os.path.basename(e['name'].rstrip('/')) for e in entries if e.get('type') == 'directory']
                if not namespaces:
                    print(f"Error: no namespaces found in {rs_root.address_prefix}")
                    sys.exit(1)
                
                choices = [{"name": ns, "value": ns} for ns in sorted(namespaces)]
                
                ans = inquirer.select(
                    message="Select a namespace:",
                    choices=choices, pointer=">", qmark="?", amark="✓", style=menu_style, mandatory=False
                ).execute()
                
                if ans is None:
                    if args.instance or in_cluster_config:
                        sys.exit(0)
                    step = 1
                    continue
                
                selected_namespace = ans
                os.environ["BKUP_JOB_NAMESPACE"] = selected_namespace
                step = 3

        elif step == 3:
            if args.file:
                selected_files = [args.file]
                step = 6
                continue
                
            rs_ns = RemoteStorage()
            grouped_cache = get_grouped_backups(rs_ns)
            
            if not grouped_cache:
                print(f"No backups found in remote storage for namespace '{selected_namespace}'.")
                if args.namespace:
                    sys.exit(1)
                step = 2
                continue
                
            choices = [
                {"name": "Restore a group of backups by date", "value": "group"},
                {"name": "Restore a single specific file", "value": "single"}
            ]
            
            ans = inquirer.select(
                message="How do you want to restore?",
                choices=choices, pointer=">", qmark="?", amark="✓", style=menu_style, mandatory=False
            ).execute()
            
            if ans is None:
                if args.namespace:
                    sys.exit(0)
                step = 2
                continue
            
            selected_mode = ans
            step = 4

        elif step == 4:
            if selected_mode == "group":
                dates = sorted([d for d in grouped_cache.keys() if d != "other"], reverse=True)
                choices = [{"name": format_date(d), "value": d} for d in dates]
                
                if "other" in grouped_cache:
                    choices.append({"name": "other (unparseable dates)", "value": "other"})
                    
                ans = inquirer.select(
                    message="Select the backup date:",
                    choices=choices, pointer=">", qmark="?", amark="✓", style=menu_style, mandatory=False
                ).execute()
                
                if ans is None:
                    step = 3
                    continue
                    
                selected_date = ans
                step = 5
                
            elif selected_mode == "single":
                all_files = []
                for date_group in grouped_cache.values():
                    all_files.extend(date_group)
                all_files.sort()
                
                choices = [{"name": f, "value": f} for f in all_files]
                
                ans = inquirer.select(
                    message="Select the file to restore:",
                    choices=choices, pointer=">", qmark="?", amark="✓", style=menu_style, mandatory=False
                ).execute()
                
                if ans is None:
                    step = 3
                    continue
                    
                selected_files = [ans]
                step = 6

        elif step == 5:
            choices = [{"name": f, "value": f, "enabled": True} for f in sorted(grouped_cache[selected_date])]
            
            ans = inquirer.checkbox(
                message=f"Select the files to restore for {format_date(selected_date)} (Space to toggle):",
                choices=choices, pointer=">", qmark="?", amark="✓", enabled_symbol="[x]", disabled_symbol="[ ]", style=menu_style, mandatory=False
            ).execute()
            
            if ans is None or not ans:
                step = 4
                continue
                
            selected_files = ans
            step = 6

        elif step == 6:
            print("\nWARNING: You are about to restore the following files:")
            for f in selected_files:
                print(f"  - {f}")
            
            print("\nThis operation is DESTRUCTIVE and will overwrite existing data.")
            confirm = inquirer.text(
                message="Type 'y' and press ENTER to proceed (leave empty to cancel):",
                qmark="?", amark="✓", style=menu_style, mandatory=False
            ).execute()

            if confirm is None or confirm.strip().lower() not in ['y', 'yes']:
                print("\nRestore cancelled.\n")
                if args.file:
                    sys.exit(0)
                step = 5 if selected_mode == "group" else 4
                continue

            import src.orchestrator as orchestrator
            for file_item in selected_files:
                print(f"\n--- Restoring {file_item} ---")
                if '.tar.gz' in file_item:
                    orchestrator.job_import_volume(namespace=selected_namespace, archive_name=file_item)
                elif 'psql' in file_item:
                    orchestrator.job_import_postgresql(archive_name=file_item)
            
            break


def main():
    parser = argparse.ArgumentParser(description="bkup CLI")
    parser.add_argument("-c", "--kube-context", help="Kube context where to perform the command")
    parser.add_argument("-i", "--instance", help="Name of the bkup instance to use")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("export", help="Run a Kubernetes job that will export local data to remote storage")
    
    parser_import = subparsers.add_parser("import", help="Run a Kubernetes job that will import a remote archive to local storage")
    parser_import.add_argument("-n", "--namespace", help="Namespace where to import remote backup file (skip for interactive menu)")
    parser_import.add_argument("-f", "--file", help="Name of remote backup file to import (skip for interactive menu)")
    
    subparsers.add_parser("list", help="List existing backups in remote storage")

    args = parser.parse_args()
    kube_context = args.kube_context

    if args.command == "import":
        import_wizard(args, kube_context)

    elif args.command in ["export", "list"]:
        # If running inside the CronJob pod, config is already loaded via env vars
        if os.getenv("BKUP_ORCHESTRATOR_NAMESPACE"):
            if kube_context:
                os.environ["BKUP_KUBE_CONTEXT"] = kube_context
        else:
            kube = Kubernetes(context=kube_context)
            instances = kube.list_bkup_instances()
            
            if not instances:
                print("Error: no bkup instances found in the cluster.")
                sys.exit(1)

            if args.instance:
                selected_instance = next((i for i in instances if i['name'] == args.instance), None)
                if not selected_instance:
                    print(f"Error: instance '{args.instance}' not found.")
                    sys.exit(1)
            else:
                choices = [{"name": f"{inst['namespace']}....{inst['name']}", "value": inst} for inst in instances]
                selected_instance = inquirer.select(
                    message="Please load config from a bkup instance:",
                    choices=choices, pointer=">", qmark="?", amark="✓", style=menu_style, mandatory=False
                ).execute()
                
                if selected_instance is None:
                    sys.exit(0)

            envs = kube.get_bkup_instance_envs(namespace=selected_instance['namespace'], instance_name=selected_instance['name'])
            if kube_context:
                envs['BKUP_KUBE_CONTEXT'] = kube_context

            if envs:
                os.environ.update(envs)
            else:
                print(f"Error: failed to retrieve environment variables from the instance {selected_instance['namespace']}/{selected_instance['name']}")
                sys.exit(1)

        if args.command == "list":
            RemoteStorage().list_remote_backups()
        
        elif args.command == "export":
            import src.orchestrator as orchestrator
            orchestrator.job_export_volumes()
            orchestrator.job_export_postgresql()


if __name__ == "__main__":
    main()
