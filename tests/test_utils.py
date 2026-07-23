from src.utils.psql import is_postgresql_workload
from src.utils.backup_map import get_backup_map
from unittest.mock import MagicMock


def test_is_postgresql_workload():
    # Cas Positifs
    assert is_postgresql_workload({"container": "postgres", "resource": "StatefulSet/db"}) is True
    assert is_postgresql_workload({"container": "app", "resource": "Deployment/my-psql-db"}) is True

    # Cas Négatifs
    assert is_postgresql_workload({"container": "nginx", "resource": "Deployment/frontend"}) is False
    assert is_postgresql_workload(None) is False
    assert is_postgresql_workload({}) is False


def test_get_backup_map_empty():
    mock_kube = MagicMock()
    # Simule l'absence de configmap (get_configmap renvoie un dict vide)
    mock_kube.get_configmap.return_value = {}
    mock_kube.get_cluster_name.return_value = "test-cluster"
    mock_kube.get_namespace.return_value = "default"

    backup_map = get_backup_map(mock_kube)

    assert backup_map == {"cluster": "test-cluster", "namespaces": []}
    mock_kube.get_configmap.assert_called_once_with(
        name="clustersnap-backup-map",
        namespace="default"
    )
