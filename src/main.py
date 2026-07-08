import sys
import argparse
from src.helpers.kubernetes import Kubernetes


def main():
    # # If an argument is provided = we're using from CLI
    # try:
    parser = argparse.ArgumentParser(description="Backup Kubernetes volumes")
    parser.add_index = False
    parser.add_argument("-c", "--context", type=str, default=None, help="Local kubectl context")
    parser.add_argument("-n", "--namespace", type=str, default=None, help="Kubernetes namespace")
    args = parser.parse_args()

    kubernetes = Kubernetes(context=args.context, given_namespace=args.namespace)
    print(f"context: {args.context}")

    # # If no argument is provided = we're in Kubernetes
    # except:
    #     kubernetes = Kubernetes()
    #     print(f"context: local")

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

        mounts = kubernetes.get_pvc(pvc['name'])
        if mounts:
            print(f"      mounts:")
            for m in mounts:
                print(f"      - container: {m['container_name']}")
                print(f"        path: {m['mount_path']}")
                print(f"        read_only: {m['read_only']}")
                print(f"        resource: {m['resource']}")

                # volume_name_clean = print(f"{m['resource'].split("/")[1]}-{m['container_name']}-{m['mount_path'].replace('/','')}")
                # kubernetes.create_backup_job(pvc=pvc['name'], mounted_path=m['mount_path'], volume_name=m['volume_name_clean'])

                # kubernetes.create_backup_job(pvc=pvc['name'], mounted_path=m['mount_path'])
        else:
            print(f"      mounts: {mounts}")

if __name__ == "__main__":
    sys.exit(main())
