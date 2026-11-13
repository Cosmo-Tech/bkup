import hashlib
import importlib
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def orch(monkeypatch):
    """Load src.orchestrator with a mocked Kubernetes client and clean environment variables."""
    monkeypatch.setenv("BKUP_IMAGE_REPOSITORY", "ghcr.io/cosmo-tech/bkup")
    monkeypatch.setenv("BKUP_IMAGE_TAG", "1.0.0")
    monkeypatch.setenv("BKUP_IMAGE_PULL_POLICY", "IfNotPresent")
    monkeypatch.setenv("BKUP_IMAGE_PULL_SECRETS", '[{"name": "regcred"}]')
    monkeypatch.setenv("BKUP_STORAGE_PROTOCOL", "s3")
    monkeypatch.setenv("BKUP_STORAGE_ENDPOINT", "https://s3.local")
    monkeypatch.setenv("BKUP_STORAGE_ROOT", "backups")
    monkeypatch.setenv("BKUP_STORAGE_USERNAME", "user")
    monkeypatch.setenv("BKUP_STORAGE_PASSWORD_SECRET_NAME", "bkup-secret")
    monkeypatch.setenv("BKUP_STORAGE_PASSWORD_SECRET_KEY", "pwd")
    monkeypatch.setenv("BKUP_ORCHESTRATOR_NAMESPACE", "bkup-system")

    with patch("src.lib.kubernetes.Kubernetes") as mock_k8s_cls:
        mock_kube = MagicMock()
        mock_kube.can_i_create_secrets.return_value = True
        mock_k8s_cls.return_value = mock_kube

        import src.orchestrator as orchestrator
        importlib.reload(orchestrator)
        orchestrator.kube = mock_kube
        yield orchestrator


def test_jit_secret_lifecycle(orch):
    # Create and delete JIT secret in a distinct target namespace
    orch.create_jit_secret("app-ns")
    orch.kube.duplicate_secret.assert_called_once_with(
        source_namespace="bkup-system",
        source_name="bkup-secret",
        new_namespace="app-ns",
        new_name="bkup-secret",
    )

    orch.delete_jit_secret("app-ns")
    orch.kube.delete_secret.assert_called_once_with(
        namespace="app-ns", secret_name="bkup-secret"
    )

    # Safety guard: skip operations when namespace == orchestrator_namespace
    orch.kube.reset_mock()
    orch.create_jit_secret("bkup-system")
    orch.delete_jit_secret("bkup-system")
    orch.kube.duplicate_secret.assert_not_called()
    orch.kube.delete_secret.assert_not_called()


def test_job_export_volumes(orch, monkeypatch):
    monkeypatch.setenv("BKUP_VOLUMES_NAMESPACES", '["ns-a"]')

    orch.kube.list_deployments.return_value = ["deploy-1"]
    orch.kube.list_statefulsets.return_value = []
    orch.kube.list_pvc.return_value = ["data-pvc"]
    orch.kube.map_mountpoints_pvc_workload.return_value = [
        {
            "pvc": "data-pvc",
            "workload_kind": "Deployment",
            "workload_name": "my-app",
            "container": "app",
            "volume": "data-vol",
            "mount_path": "/data",
        }
    ]
    orch.kube.get_workload_replicas.return_value = 3
    orch.kube.get_workload_annotation.return_value = "3"

    with patch("src.orchestrator.os.path.exists", return_value=True):
        orch.job_export_volumes()

    # Verify scale down to 0 and scale back up to 3
    scale_calls = orch.kube.scale_workload_replicas.call_args_list
    assert scale_calls[0].kwargs["replicas"] == 0
    assert scale_calls[-1].kwargs["replicas"] == "3"

    # Verify job creation and completion wait
    orch.kube.create_job.assert_called_once()
    job_cfg = orch.kube.create_job.call_args.kwargs["job_config"]
    assert job_cfg["PVC_NAME"] == "data-pvc"
    assert job_cfg["MOUNT_PATH"] == "/data"
    assert job_cfg["JOB_NAMESPACE"] == "ns-a"
    orch.kube.wait_job_end.assert_called_once()


def test_job_import_volume_success(orch):
    orch.kube.list_pvc.return_value = ["data-pvc"]
    orch.kube.list_deployments.return_value = ["deploy-1"]
    orch.kube.list_statefulsets.return_value = []
    orch.kube.map_mountpoints_pvc_workload.return_value = [
        {
            "pvc": "data-pvc",
            "workload_kind": "Deployment",
            "workload_name": "my-app",
            "container": "app",
            "volume": "datavol",
            "mount_path": "/data",
        }
    ]
    orch.kube.get_workload_replicas.return_value = 2
    orch.kube.get_workload_annotation.return_value = "2"

    with patch("src.orchestrator.os.path.exists", return_value=True):
        orch.job_import_volume(namespace="ns-a", archive_name="data-pvc_datavol_261113112500.tar.gz")

    orch.kube.create_job.assert_called_once()
    job_cfg = orch.kube.create_job.call_args.kwargs["job_config"]
    assert job_cfg["ARCHIVE_NAME"] == "data-pvc_datavol_261113112500.tar.gz"
    assert job_cfg["TEMPLATE_PATH"] == "templates/volume.importer.job.yaml"

    # Verify replicas restoration and JIT secret cleanup in finally block
    assert orch.kube.scale_workload_replicas.call_args_list[-1].kwargs["replicas"] == "2"
    orch.kube.delete_secret.assert_called_once()


def test_job_import_volume_namespace_mismatch_exits(orch):
    with pytest.raises(SystemExit) as exc:
        orch.job_import_volume(namespace="ns-a", archive_name="ns-b/data-pvc_datavol_261113112500.tar.gz")
    assert exc.value.code == 1


def test_job_export_postgresql(orch, monkeypatch):
    pg_yaml = """
    - address: pg.db.svc.cluster.local
      port: 5432
      database: appdb
      username: postgres
      passwordSecret:
        name: pg-secret
        key: pg-pwd
      image:
        repository: postgres
        tag: "16"
        pullPolicy: IfNotPresent
        pullSecrets: []
      namespace: db-ns
    """
    monkeypatch.setenv("BKUP_POSTGRESQL_VALUES", pg_yaml)

    with patch("src.orchestrator.os.path.exists", return_value=True):
        orch.job_export_postgresql()

    orch.kube.create_job.assert_called_once()
    job_cfg = orch.kube.create_job.call_args.kwargs["job_config"]
    assert job_cfg["POSTGRESQL_HOST"] == "pg.db.svc.cluster.local"
    assert job_cfg["POSTGRESQL_DATABASE"] == "appdb"
    assert job_cfg["TEMPLATE_PATH"] == "templates/postgresql.exporter.job.yaml"


def test_job_import_postgresql(orch, monkeypatch):
    host = "pg.db.svc.cluster.local"
    host_hash = hashlib.md5(host.encode()).hexdigest()
    archive = f"psql_{host_hash}_261113112500_appdb.gz"

    pg_yaml = f"""
    - address: {host}
      port: 5432
      database: appdb
      username: postgres
      passwordSecret:
        name: pg-secret
        key: pg-pwd
      image:
        repository: postgres
        tag: "16"
        pullPolicy: IfNotPresent
        pullSecrets: []
      namespace: db-ns
    """
    monkeypatch.setenv("BKUP_POSTGRESQL_VALUES", pg_yaml)

    with patch("src.orchestrator.os.path.exists", return_value=True):
        orch.job_import_postgresql(archive_name=archive)

    orch.kube.create_job.assert_called_once()
    job_cfg = orch.kube.create_job.call_args.kwargs["job_config"]
    assert job_cfg["ARCHIVE_NAME"] == archive
    assert job_cfg["POSTGRESQL_DATABASE"] == "appdb"
    assert job_cfg["TEMPLATE_PATH"] == "templates/postgresql.importer.job.yaml"
