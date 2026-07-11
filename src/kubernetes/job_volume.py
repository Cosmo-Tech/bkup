from src.kubernetes.base import KubernetesBase


class VolumeBackupJob(KubernetesBase):
    def __init__(self, context: str = None, namespace: str = None):
        super().__init__(context=context, namespace=namespace)


    def create_backup_job(self, pvc_name: str, mounted_path: str, node_name: str, **kwargs):
        """Backup Kubernetes workload volumes."""
        replacements = super().create_backup_job(pvc_name, mounted_path, node_name, **kwargs)

        self._launch_generic_job(
            template_path="templates/volume.exporter.job.yaml", 
            replacements=replacements
        )
