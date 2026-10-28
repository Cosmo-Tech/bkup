import os
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
    global_group.add_argument("-n", "--namespace", type=str, metavar="NS", default=None, help="Target a namespace for backup job run. Using 'default' if not given.")
    global_group.add_argument("-j", "--job", action="store_true", help="Run backup job(s) based on current backup-map")


    args = parser.parse_args()

    cluster_context = args.context
    os.environ["BKUP_CLUSTER_CONTEXT"] = cluster_context


    try:
        kube = KubernetesBase(context=cluster_context, namespace=namespace)
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


    if args.job:
        master_ns = config_secret.get_config_secret_namespace(kube)
        backup_map_name = backup_map.get_backup_map_name()

        # Verify ConfigMap existence before processing
        saved_map = backup_map.get_backup_map_content(kube, namespace=master_ns)
        if not saved_map or not saved_map.get("namespaces"):
            print(f"error: ConfigMap '{backup_map_name}' is missing or empty in namespace '{master_ns}'.")
            print("hint: Generate and apply one first using 'bkup -bm -a'")
            sys.exit(1)

        print(f"{yaml.dump(saved_map, sort_keys=False, default_flow_style=False)}\n")

        master.main()

    else:
        print("error: please use --help for usage.")


if __name__ == "__main__":
    sys.exit(main())
