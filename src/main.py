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

    # If no argument is provided = run the backup
    except:
        kubernetes = Kubernetes()
    
    current_ns = kubernetes.get_current_namespace()

    print(f"PVC list in namespace ({current_ns}):")

    pvcs = kubernetes.list_pvc()
    if not pvcs:
        print("No PVC found or unauthorized access.")
        return 0

    for pvc in kubernetes.list_pvc():
        print(f"pvc:")
        print(f"  - name: {pvc['name']}")
        print(f"    status: {pvc['status']}")
        print(f"    capacity: {pvc['capacity']}")

        mounts = kubernetes.get_pvc_mount_points(pvc)        
        if not mounts:
            print("PVC is not attached to an active pod.")
        else:
            for m in mounts:
                print(f"  pods:")
                print(f"  - name: {m['pod_name']}")
                print(f"    containers:")
                print(f"    - name: {m['container_name']}")
                print(f"      mountpoints:")
                print(f"      - name: {m['mount_path']}")
                print(f"        readonly: {m['read_only']}")


if __name__ == "__main__":
    sys.exit(main())
