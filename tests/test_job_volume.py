import pytest
from unittest.mock import patch
from src.kubernetes.job_volume import VolumeBackupJob

@patch("src.kubernetes.base.KubernetesBase._launch_generic_job")
def test_create_backup_job_volume(mock_launch_generic):
    # On instancie la classe (sans charger la vraie config K8s grâce au patch)
    with patch("src.kubernetes.base.config"):
        worker = VolumeBackupJob(context="test", namespace="default")
        
    # On simule un appel de création de job
    worker.create_backup_job(
        pvc_name="my-data-pvc",
        mounted_path="/var/lib/data",
        node_name="node-01",
        master_ns="clustersnap-system"
    )
    
    # On vérifie que la méthode de base a été appelée avec les bons remplacements
    mock_launch_generic.assert_called_once()
    args, kwargs = mock_launch_generic.call_args
    
    assert kwargs["template_path"] == "templates/volume.exporter.job.yaml"
    assert kwargs["replacements"]["PVC_NAME"] == "my-data-pvc"
    assert kwargs["replacements"]["MOUNTED_PATH"] == "/var/lib/data"
    
    # Vérifie que l'affinité de noeud a bien été générée pour attacher le bon stockage
    assert "nodeAffinity" in kwargs["replacements"]["AFFINITY_DICT"]