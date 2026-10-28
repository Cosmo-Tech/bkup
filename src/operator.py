import os
import sys
import argparse
import signal
import yaml

from src.kubernetes.base import KubernetesBase
from src.utils import general as utils


def main():
    try:
        cluster_context = os.environ["BKUP_CLUSTER_CONTEXT"]
        kube = KubernetesBase(context=cluster_context)

        # mount_map = []
        mount_map = {}

        namespaces = kube.list_namespaces()
        for n in namespaces:
            mount_map[n] = []

            deployments = kube.list_deployments(namespace=n)
            statefulsets = kube.list_statefulsets(namespace=n)

            pvc_list = kube.list_pvc(namespace=n)
            for p in pvc_list:
                for w in deployments:
                    mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
                    for m in mounts:
                        mount_map[n].append(m)

                for w in statefulsets:
                    mounts = kube.map_mountpoints_pvc_workload(pvc_name=p, workload=w)
                    for m in mounts:
                        mount_map[n].append(m)


        print(mount_map)

    except Exception as e:
        print(f"error: {utils.current_function()}: {e}")
        sys.exit(1)



if __name__ == "__main__":
    sys.exit(main())
