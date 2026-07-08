import sys
import argparse
from src.helpers.kubernetes import Kubernetes


# Get a YAML map of what will be backuped
def get_backup_map(kubernetes: Kubernetes):
    current_ns = kubernetes.get_namespace()

    print(f"context: {kubernetes.get_context()}")
    print(f"namespaces:")
    print(f"- name: {current_ns}")

    pvcs = kubernetes.list_pvc()
    if not pvcs:
        print("error: no PVC found or unauthorized access.")
        return 0

    # Loop to display the backup map
    print(f"  claims:")
    for pvc in kubernetes.list_pvc():
        print(f"    - name: {pvc['name']}")
        print(f"      status: {pvc['status']}")
        print(f"      capacity: {pvc['capacity']}")

        mounts = kubernetes.get_pvc(pvc['name'])
        if mounts:
            print(f"      mounts:")
            for m in mounts:
                print(f"      - container: {m['container_name']}")
                print(f"        path: {m['mount_path']}")
                print(f"        read_only: {m['read_only']}")
                print(f"        resource: {m['resource']}")
        else:
            print(f"      mounts: {mounts}")


# Run a backup job
def run_backup_job(kubernetes: Kubernetes):
    # Display the map before running the job
    get_backup_map(kubernetes)

    for pvc in kubernetes.list_pvc():
        mounts = kubernetes.get_pvc(pvc['name'])
        if mounts:
            for m in mounts:
                kubernetes.create_backup_job(pvc=pvc['name'], mounted_path=m['mount_path'])


def main():
    parser = argparse.ArgumentParser(description="Backup Kubernetes volumes")
    parser.add_index = False
    parser.add_argument("-c", "--context", type=str, default=None, help="Local kubectl context")
    parser.add_argument("-n", "--namespace", type=str, default=None, help="Kubernetes namespace")
    parser.add_argument("-j", "--job", action="store_true", help="Run a backup job")
    parser.add_argument("-m", "--map", action="store_true", help="Get the backup map")
    args = parser.parse_args()

    kubernetes = Kubernetes(context=args.context, namespace=args.namespace)
    
    if args.job:
        run_backup_job(kubernetes)

    elif args.map:
        get_backup_map(kubernetes)

    else:
        print('please run with --job or --map (--help for more info)')


if __name__ == "__main__":
    sys.exit(main())
