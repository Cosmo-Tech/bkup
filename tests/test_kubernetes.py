import base64
import pytest
from unittest.mock import MagicMock, patch
from kubernetes import client
from src.lib.kubernetes import Kubernetes


@pytest.fixture
def k8s():
    """Initialize Kubernetes client while blocking real cluster/kubeconfig calls."""
    with (
        patch("src.lib.kubernetes.os.path.exists", return_value=False),
        patch("src.lib.kubernetes.config.load_kube_config"),
        patch("src.lib.kubernetes.client.CoreV1Api"),
        patch("src.lib.kubernetes.client.AppsV1Api"),
        patch("src.lib.kubernetes.client.BatchV1Api"),
        patch("src.lib.kubernetes.client.AuthorizationV1Api"),
    ):
        instance = Kubernetes(context="test-context")
        return instance


def test_init_in_cluster():
    with (
        patch("src.lib.kubernetes.os.path.exists", return_value=True),
        patch("src.lib.kubernetes.config.load_incluster_config") as mock_incluster,
        patch("src.lib.kubernetes.client.CoreV1Api"),
        patch("src.lib.kubernetes.client.AppsV1Api"),
        patch("src.lib.kubernetes.client.BatchV1Api"),
        patch("src.lib.kubernetes.client.AuthorizationV1Api"),
    ):
        Kubernetes()
        mock_incluster.assert_called_once()


def test_init_failure_raises_runtime_error():
    with (
        patch("src.lib.kubernetes.os.path.exists", return_value=False),
        patch("src.lib.kubernetes.config.load_kube_config", side_effect=Exception("kubeconfig error")),
    ):
        with pytest.raises(RuntimeError, match="failed to initialize Kubernetes connection"):
            Kubernetes(context="invalid-ctx")


def test_list_namespaces(k8s):
    ns1 = MagicMock()
    ns1.metadata.name = "default"
    ns2 = MagicMock()
    ns2.metadata.name = "prod"
    k8s.core_v1.list_namespace.return_value.items = [ns1, ns2]

    assert k8s.list_namespaces() == ["default", "prod"]


def test_list_pvc(k8s):
    pvc = MagicMock()
    pvc.metadata.name = "data-pvc"
    k8s.core_v1.list_namespaced_persistent_volume_claim.return_value.items = [pvc]

    assert k8s.list_pvc("default") == ["data-pvc"]


def test_map_mountpoints_pvc_workload(k8s):
    # Build a mock Deployment workload
    vol = MagicMock()
    vol.name = "storage-vol"
    vol.persistent_volume_claim.claim_name = "my-pvc"

    vm = MagicMock()
    vm.name = "storage-vol"
    vm.mount_path = "/var/lib/data"

    container = MagicMock()
    container.name = "app-container"
    container.volume_mounts = [vm]

    workload = MagicMock()
    workload.kind = "Deployment"
    workload.metadata.name = "my-app"
    workload.spec.template.spec.volumes = [vol]
    workload.spec.template.spec.containers = [container]

    mappings = k8s.map_mountpoints_pvc_workload(pvc_name="my-pvc", workload=workload)

    assert len(mappings) == 1
    assert mappings[0] == {
        "pvc": "my-pvc",
        "workload_kind": "Deployment",
        "workload_name": "my-app",
        "container": "app-container",
        "volume": "storage-vol",
        "mount_path": "/var/lib/data",
    }


def test_map_mountpoints_pvc_workload_fallback_kind(k8s):
    class V1StatefulSet:
        pass

    vol = MagicMock()
    vol.name = "db-vol"
    vol.persistent_volume_claim.claim_name = "db-pvc"

    vm = MagicMock()
    vm.name = "db-vol"
    vm.mount_path = "/data"

    container = MagicMock()
    container.name = "postgres"
    container.volume_mounts = [vm]

    workload = V1StatefulSet()
    workload.metadata = MagicMock()
    workload.metadata.name = "pg-sts"
    workload.spec = MagicMock()
    workload.spec.template.spec.volumes = [vol]
    workload.spec.template.spec.containers = [container]

    mappings = k8s.map_mountpoints_pvc_workload(pvc_name="db-pvc", workload=workload)
    assert mappings[0]["workload_kind"] == "StatefulSet"


def test_create_job(k8s, tmp_path):
    tpl = tmp_path / "job.yaml"
    tpl.write_text(
        "apiVersion: batch/v1\nkind: Job\nmetadata:\n  name: {{ JOB_NAME }}\n  namespace: {{ JOB_NAMESPACE }}\n",
        encoding="utf-8",
    )

    k8s.create_job(
        {
            "TEMPLATE_PATH": str(tpl),
            "JOB_NAMESPACE": "ns-test",
            "JOB_NAME": "job-test",
        }
    )

    k8s.batch_v1.create_namespaced_job.assert_called_once_with(
        namespace="ns-test",
        body={
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "job-test", "namespace": "ns-test"},
        },
    )


def test_get_job_status(k8s):
    job = MagicMock()
    job.status.succeeded = 1
    job.status.failed = None
    job.status.active = None
    k8s.batch_v1.read_namespaced_job.return_value = job
    assert k8s.get_job_status("ns", "job1") == "succeeded"

    job.status.succeeded = None
    job.status.failed = 1
    assert k8s.get_job_status("ns", "job1") == "failed"

    job.status.failed = None
    job.status.active = 1
    assert k8s.get_job_status("ns", "job1") == "active"

    job.status.active = None
    assert k8s.get_job_status("ns", "job1") == "pending"


@patch("src.lib.kubernetes.time.sleep", return_value=None)
def test_wait_job_end_success(mock_sleep, k8s):
    k8s.get_job_status = MagicMock(side_effect=["pending", "active", "succeeded"])
    assert k8s.wait_job_end("ns", "job1") is True
    assert k8s.get_job_status.call_count == 3


@patch("src.lib.kubernetes.time.sleep", return_value=None)
def test_wait_job_end_404(mock_sleep, k8s):
    k8s.batch_v1.read_namespaced_job.side_effect = client.exceptions.ApiException(status=404)
    assert k8s.wait_job_end("ns", "job1") is False


def test_workload_replicas_and_annotations(k8s):
    deploy = MagicMock()
    deploy.spec.replicas = 2
    deploy.metadata.annotations = {"bkup/original-replicas": "2"}
    k8s.apps_v1.read_namespaced_deployment.return_value = deploy

    assert k8s.get_workload_replicas("ns", "Deployment", "web") == 2
    assert k8s.get_workload_annotation("ns", "Deployment", "web", "bkup/original-replicas") == "2"

    k8s.scale_workload_replicas("ns", "Deployment", "web", "0")
    k8s.apps_v1.patch_namespaced_deployment.assert_called_with(
        name="web", namespace="ns", body={"spec": {"replicas": 0}}
    )

    k8s.add_workload_annotation("ns", "Deployment", "web", {"foo": "bar"})
    k8s.apps_v1.patch_namespaced_deployment.assert_called_with(
        name="web", namespace="ns", body={"metadata": {"annotations": {"foo": "bar"}}}
    )


def test_secrets_operations(k8s):
    # Test get_secret_value
    secret = MagicMock()
    secret.data = {"pwd": base64.b64encode(b"s3cr3t").decode("utf-8")}
    k8s.core_v1.read_namespaced_secret.return_value = secret
    assert k8s.get_secret_value("ns", "my-sec", "pwd") == "s3cr3t"

    # Test duplicate_secret
    k8s.duplicate_secret("src-ns", "src-sec", "dst-ns", "dst-sec")
    k8s.core_v1.create_namespaced_secret.assert_called_once()

    # Test delete_secret (404 should be ignored)
    k8s.core_v1.delete_namespaced_secret.side_effect = client.exceptions.ApiException(status=404)
    k8s.delete_secret("dst-ns", "dst-sec")

    # Test can_i_create_secrets
    review_resp = MagicMock()
    review_resp.status.allowed = True
    k8s.auth_v1.create_self_subject_access_review.return_value = review_resp
    assert k8s.can_i_create_secrets("dst-ns") is True


def test_list_bkup_instances_and_envs(k8s):
    sa = MagicMock()
    sa.metadata.name = "bkup-prod"
    sa.metadata.namespace = "bkup-ns"
    k8s.core_v1.list_service_account_for_all_namespaces.return_value.items = [sa]

    instances = k8s.list_bkup_instances()
    assert instances == [{"name": "bkup-prod", "namespace": "bkup-ns"}]

    cm = MagicMock()
    cm.data = {"BKUP_STORAGE_PROTOCOL": "s3"}
    k8s.core_v1.read_namespaced_config_map.return_value = cm

    assert k8s.get_bkup_instance_envs("bkup-ns", "bkup-prod") == {"BKUP_STORAGE_PROTOCOL": "s3"}
