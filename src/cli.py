import sys
import argparse
import signal
import yaml

from src.kubernetes.base import KubernetesBase
import src.utils.backup_map as backup_map
import src.utils.config_secret as config_secret
import src.master as master


def main():

    parser = argparse.ArgumentParser(
        description="Backup Kubernetes volumes",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=80),
    )

    # Global options
    global_group = parser.add_argument_group("global")
    global_group.add_argument("-c", "--context", type=str, metavar="CT", default=None, help="Local kubectl context")
    global_group.add_argument("-n", "--namespace", type=str, metavar="NS", default=None, help="Target a namespace for backup job run")
    global_group.add_argument("-j", "--job", action="store_true", help="Run backup job(s) based on current backup-map")
    global_group.add_argument("-bm", "--backup-map", action="store_true", help="Manage backup-map configuration")

    # Backup-map sub-options
    map_group = parser.add_argument_group("backup-map")
    map_group.add_argument("-p", "--preview", action="store_true", help="Preview new backup-map")
    map_group.add_argument("-a", "--apply", action="store_true", help="Apply new backup-map")
    map_group.add_argument("-g", "--get-current", action="store_true", help="Get current saved backup-map")

    args = parser.parse_args()

    cluster_context = args.context
    namespace_input = args.namespace
    if namespace_input:
        namespace = namespace_input
    else:
        namespace = "default"

    # Strict validation of -bm sub-options usage
    if (args.apply or args.preview or args.get_current) and not args.backup_map:
        print("error: --preview (-p), --apply (-a), and --get-current (-g) can only be used with --backup-map (-bm).")
        sys.exit(1)

    try:
        kube = KubernetesBase(context=cluster_context, namespace=namespace)
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


    if args.job:
        master.main()

    elif args.backup_map:
        # Incompatibility: -g cannot be combined with -p or -a
        if args.get_current and (args.preview or args.apply):
            print("error: Option --get-current (-g) cannot be combined with scanning options (-p or -a).")
            sys.exit(1)

        # 1. ACTION: Read existing map in K8s (-g)
        if args.get_current:
            try:
                saved_map = backup_map.get_backup_map(kube, master_ns=namespace)
                print(yaml.dump(saved_map, sort_keys=False, default_flow_style=False))
            except Exception as e:
                print(f"error: Failed to retrieve backup-map from namespace '{namespace}': {e}")
                sys.exit(1)

        # 2. ACTION: Scan ENTIRE cluster (-p / -a)
        elif args.preview or args.apply:
            all_cluster_namespaces = kube.list_namespaces()
            bm = backup_map.backup_map_scanner(kube, all_cluster_namespaces)

            if args.apply:
                kube.save_to_configmap(name="bkup-backup-map", namespace=namespace, data=bm)
                print(f"info: backup-map successfully applied and saved to ConfigMap in namespace '{namespace}'.")
            else:
                print(yaml.dump(bm, sort_keys=False, default_flow_style=False))

        # 3. ACTION: -bm alone -> Error message
        else:
            print("error: Option --backup-map (-bm) requires an action option (-g/--get-current, -p/--preview, or -a/--apply).")
            print("examples:")
            print("  bkup -bm -g        # Read saved backup-map from Kubernetes")
            print("  bkup -bm -p        # Preview full cluster backup-map")
            print("  bkup -bm -a        # Scan cluster and save backup-map to ConfigMap")
            sys.exit(1)

    else:
        print("error: Please use --help for usage.")


if __name__ == "__main__":
    sys.exit(main())
