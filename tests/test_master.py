import pytest
from unittest.mock import MagicMock
from src.master import get_secret_config_namespace


def test_get_secret_config_namespace_found():
    mock_kube = MagicMock()
    mock_kube.list_namespaces.return_value = ["default", "kube-system", "clustersnap-system"]

    # Simulate that the secret is not in the first two namespaces, but in the 3rd
    def mock_get_secret(name, namespace):
        if namespace == "clustersnap-system":
            return {"storage-address": "mock"}
        raise Exception("Not found")

    mock_kube.get_secret_payload.side_effect = mock_get_secret

    ns = get_secret_config_namespace(mock_kube)
    assert ns == "clustersnap-system"


def test_get_secret_config_namespace_not_found():
    mock_kube = MagicMock()
    mock_kube.list_namespaces.return_value = ["default", "kube-system"]
    # Simulate the absence of the secret everywhere
    mock_kube.get_secret_payload.side_effect = Exception("Not found")

    with pytest.raises(RuntimeError, match="Secret 'clustersnap-config' not found"):
        get_secret_config_namespace(mock_kube)
