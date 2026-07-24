import pytest
from unittest.mock import MagicMock, patch
from src.kubernetes.base import KubernetesBase


@pytest.fixture
def k8s_base():
    """Init KubernetesBase while blocking real config calls."""
    with (
        patch("src.kubernetes.base.config.load_incluster_config"),
        patch("src.kubernetes.base.config.load_kube_config"),
    ):
        base = KubernetesBase(context="test-context", namespace="test-ns")
        # Replace clients with Mocks to inspect calls
        base.core_v1 = MagicMock()
        base.apps_v1 = MagicMock()
        base.batch_v1 = MagicMock()
        return base


def test_get_replicas_deployment(k8s_base):
    # Prepare the fake return from the Kubernetes API
    mock_deploy = MagicMock()
    mock_deploy.spec.replicas = 3
    k8s_base.apps_v1.read_namespaced_deployment.return_value = mock_deploy

    # Execution
    replicas = k8s_base.get_replicas("deployment", "my-app")

    # Verifications
    assert replicas == 3
    k8s_base.apps_v1.read_namespaced_deployment.assert_called_once_with(name="my-app", namespace="test-ns")


def test_scale_replicas_statefulset(k8s_base):
    k8s_base.scale_replicas("statefulset", "my-db", 0)

    # Ensure the patch was sent with the correct payload
    k8s_base.apps_v1.patch_namespaced_stateful_set.assert_called_once_with(name="my-db", namespace="test-ns", body={"spec": {"replicas": 0}})


def test_get_job_name_truncation(k8s_base):
    # Case 1: Short name (prefix 19 chars + 10 = 29 < 63)
    short_pvc = "my-pvc-123"
    job_name = k8s_base.get_job_name(short_pvc, suffix="volume")
    assert job_name == "bkup-backup-my-pvc-123-volume"
    assert len(job_name) <= 63

    # Case 2: Name too long (must be truncated and hashed)
    long_pvc = "this-is-a-very-long-pvc-name-that-will-definitely-exceed-the-limit"
    job_name = k8s_base.get_job_name(long_pvc, suffix="volume")

    assert len(job_name) <= 63
    assert job_name.startswith("bkup-backup-this-is-a-very-long")
    assert "-volume" in job_name
    # Verify that an md5 hash (8 characters) is present before the suffix
    assert len(job_name.split("-")[-2]) == 8


@patch("src.kubernetes.base.time.sleep", return_value=None)  # Speeds up the test
def test_wait_for_scale_down(mock_sleep, k8s_base):
    # Simulate a progressive scale down of replicas: 2 -> 1 -> 0
    k8s_base.get_replicas = MagicMock(side_effect=[2, 1, 0])

    success = k8s_base.wait_for_scale_down("Deployment", "my-app", timeout=10)

    assert success is True
    assert k8s_base.get_replicas.call_count == 3
