import sys

from src.helpers.kubernetes import Kubernetes


def main():
    kubernetes = Kubernetes()
    current_ns = kubernetes.get_current_namespace()

    print(f"PVC list in namespace ({current_ns}):")
    for pvc in kubernetes.list_pvc():
        print(f"- {pvc['name']} [{pvc['status']}] - {pvc['capacity']}")

        mounts = kubernetes.get_pvc_mount_points(pvc)        
        if not mounts:
            print("PVC is not attached to an active pod.")
        else:
            for m in mounts:
                print(f"pods:")
                print(f"- name: {m['pod_name']}")
                print(f"  containers:")
                print(f"  - name: {m['container_name']}")
                print(f"    mountpoints:")
                print(f"    - name: {m['mount_path']}")
                print(f"      readonly: {m['read_only']}")

if __name__ == "__main__":
    sys.exit(main())
