import sys

from src.helpers.pvc import Kubernetes


def main():
    kubernetes = Kubernetes()
    current_ns = kubernetes.get_current_namespace()

    print(f"PVC list in namespace ({current_ns}):")
    for pvc in kubernetes.list_pvc():
        print(f"- {pvc['name']} [{pvc['status']}] - {pvc['capacity']}")


if __name__ == "__main__":
    sys.exit(main())
