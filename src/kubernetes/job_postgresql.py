from src.kubernetes.base import KubernetesBase


class PostgresBackupJob(KubernetesBase):
    suffix = "pgdump"


    def __init__(self, context: str = None, namespace: str = None):
        super().__init__(context=context, namespace=namespace)


    def get_job_name(self, pvc_name: str, suffix: str = "pgdump") -> str:
        return super().get_job_name(pvc_name, suffix=suffix)


    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, **kwargs):
        """Backup PostgreSQL database with pg_dump."""
        postgres_image = kwargs.get("postgres_image")
        if not postgres_image:
            raise ValueError("postgres_image parameter is required.")
        replacements = {
            "JOB_NAME": self.get_job_name(pvc_name),
            "NAMESPACE": self.namespace,
            "MOUNTED_PATH": mounted_path,
            "PVC_NAME": pvc_name,
            "POSTGRES_IMAGE": postgres_image,
            "IMAGE_PULL_SECRETS_LIST": kwargs.get("image_pull_secrets", []),
            "DUMPER_ENV_LIST": kwargs.get("dumper_env_list", []),
            "ORIGINAL_ENV_FROM": kwargs.get("original_env_from", []),
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
            template_path="templates/postgresql.exporter.job.yaml", 
            replacements=replacements
        )
