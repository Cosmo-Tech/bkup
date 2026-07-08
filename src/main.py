import sys
import argparse
from src.helpers.kubernetes import Kubernetes


def main():
    # If an argument is provided, it means we're using CLI
    try:
        parser = argparse.ArgumentParser(description="Backup Kubernetes volumes")
        parser.add_index = False
        parser.add_argument("-c", "--context", type=str, default=None, help="Local kubectl context")
        parser.add_argument("-n", "--namespace", type=str, default=None, help="Kubernetes namespace")
        args = parser.parse_args()

        kubernetes = Kubernetes(context=args.context, given_namespace=args.namespace)
        print(f"context: {args.context}")

    # If no argument is provided = run the backup
    except:
        kubernetes = Kubernetes()
        print(f"context: local")

    current_ns = kubernetes.get_current_namespace()

    print(f"namespaces:")
    print(f"- name: {current_ns}")

    pvcs = kubernetes.list_pvc()
    if not pvcs:
        print("error: no PVC found or unauthorized access.")
        return 0

    print(f"  claims:")
    for pvc in kubernetes.list_pvc():
        print(f"    - name: {pvc['name']}")
        print(f"      status: {pvc['status']}")
        print(f"      capacity: {pvc['capacity']}")

        mounts = kubernetes.get_pvc_target_config(pvc['name'])
        if mounts:
            print(f"      mounts:")
            for m in mounts:
                print(f"      - container: {m['container_name']}")
                print(f"        path: {m['mount_path']}")
                print(f"        read_only: {m['read_only']}")
                print(f"        resource: {m['resource']}")
        else:
            print(f"      mounts: {mounts}")

if __name__ == "__main__":
    sys.exit(main())
