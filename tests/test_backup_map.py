from unittest.mock import MagicMock, patch
from src.utils.backup_map import get_backup_map, resolve_backup_type_from_map, get_pvc_from_map


def test_get_backup_map_empty():
    mock_kube = MagicMock()
    # Simulate the absence of ConfigMap (get_configmap returns an empty dict)
    mock_kube.get_configmap.return_value = {}
    mock_kube.get_cluster_name.return_value = "test-cluster"
    mock_kube.get_namespace.return_value = "default"

    backup_map = get_backup_map(mock_kube)

    assert backup_map == {"cluster": "test-cluster", "namespaces": []}
    mock_kube.get_configmap.assert_called_once_with(name="bkup-backup-map", namespace="default")


def test_get_backup_map_with_data():
    mock_kube = MagicMock()
    mock_kube.get_namespace.return_value = "default"

    # Simulate a valid ConfigMap returned by the Kubernetes API
    mock_kube.get_configmap.return_value = {"namespaces": [{"name": "test-ns", "claims": []}]}

    backup_map = get_backup_map(mock_kube)

    assert "namespaces" in backup_map
    assert len(backup_map["namespaces"]) == 1
    assert backup_map["namespaces"][0]["name"] == "test-ns"


@patch("src.utils.backup_map.get_backup_map")
def test_resolve_backup_type_from_map(mock_get_backup_map):
    mock_kube = MagicMock()

    # Mock the internal call to return a fake backup-map structure
    mock_get_backup_map.return_value = {
        "namespaces": [{"name": "my-ns", "claims": [{"name": "my-pvc", "backup-type": "pg_dump"}]}]
    }

    # Must find the correct backup type for existing PVC
    b_type = resolve_backup_type_from_map(mock_kube, "my-pvc", "my-ns")
    assert b_type == "pg_dump"

    # Must return 'unknown' if PVC is not found in the map
    unknown_type = resolve_backup_type_from_map(mock_kube, "wrong-pvc", "my-ns")
    assert unknown_type == "unknown"


@patch("src.utils.backup_map.get_backup_map")
def test_get_pvc_from_map(mock_get_backup_map):
    mock_kube = MagicMock()

    # Mock the internal call to return a mapped PVC with its mounts
    mock_get_backup_map.return_value = {
        "namespaces": [
            {
                "name": "app-namespace",
                "claims": [{"name": "data-pvc", "mounts": [{"path": "/data", "resource": "Deployment/app"}]}],
            }
        ]
    }

    # Retrieve existing PVC mounts
    mounts = get_pvc_from_map(mock_kube, "data-pvc")
    assert len(mounts) == 1
    assert mounts[0]["path"] == "/data"

    # Retrieve non-existing PVC mounts
    empty_mounts = get_pvc_from_map(mock_kube, "ghost-pvc")
    assert empty_mounts == []
