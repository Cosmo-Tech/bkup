from src.kubernetes.base import KubernetesBase


class VolumeBackupJob(KubernetesBase):
    suffix = "volume"


    def __init__(self, context: str = None, namespace: str = None):
        super().__init__(context=context, namespace=namespace)


    def get_job_name(self, pvc_name: str, suffix: str = "volume") -> str:
        return super().get_job_name(pvc_name, suffix=suffix)


    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, **kwargs):
        """Backup Kubernetes workload volumes."""
        replacements = {
            "JOB_NAME": self.get_job_name(pvc_name),
            "NAMESPACE": self.namespace,
            "MOUNTED_PATH": mounted_path,
            "PVC_NAME": pvc_name,
            "master_ns": kwargs.get("master_ns"),
            "IMAGE_PULL_SECRETS_LIST": kwargs.get("image_pull_secrets", []),
            "AFFINITY_DICT": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": [node_name]
                                    }
                                ]
                            }
                        ]
                    }
                }
            } if node_name else None
        }

        self._launch_generic_job(
            template_path="templates/volume.exporter.job.yaml",
            replacements=replacements
        )
