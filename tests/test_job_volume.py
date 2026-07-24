from unittest.mock import patch
from src.kubernetes.job_volume import VolumeBackupJob


@patch("src.kubernetes.base.KubernetesBase._launch_generic_job")
def test_create_backup_job_volume(mock_launch_generic):
    # Instantiate the class (without loading the real K8s config thanks to the patch)
    with patch("src.kubernetes.base.config"):
        worker = VolumeBackupJob(context="test", namespace="default")

    # Simulate a job creation call
    worker.create_backup_job(pvc_name="my-data-pvc", mounted_path="/var/lib/data", node_name="node-01", master_ns="bkup-system")

    # Verify that the base method was called with the correct replacements
    mock_launch_generic.assert_called_once()
    args, kwargs = mock_launch_generic.call_args

    assert kwargs["template_path"] == "templates/volume.exporter.job.yaml"
    assert kwargs["replacements"]["PVC_NAME"] == "my-data-pvc"
    assert kwargs["replacements"]["MOUNTED_PATH"] == "/var/lib/data"

    # Verify that node affinity was correctly generated to attach the right storage
    assert "nodeAffinity" in kwargs["replacements"]["AFFINITY_DICT"]
