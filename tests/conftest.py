import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clear BKUP_* environment variables before each test."""
    bkup_vars = [
        "BKUP_KUBE_CONTEXT",
        "BKUP_IMAGE_REPOSITORY",
        "BKUP_IMAGE_TAG",
        "BKUP_IMAGE_PULL_POLICY",
        "BKUP_IMAGE_PULL_SECRETS",
        "BKUP_STORAGE_PROTOCOL",
        "BKUP_STORAGE_ENDPOINT",
        "BKUP_STORAGE_ROOT",
        "BKUP_STORAGE_USERNAME",
        "BKUP_STORAGE_PASSWORD",
        "BKUP_STORAGE_PASSWORD_SECRET_NAME",
        "BKUP_STORAGE_PASSWORD_SECRET_KEY",
        "BKUP_ORCHESTRATOR_NAMESPACE",
        "BKUP_VOLUMES_NAMESPACES",
        "BKUP_POSTGRESQL_VALUES",
        "BKUP_ARCHIVE_NAME",
        "BKUP_MOUNT_PATH",
        "BKUP_JOB_NAMESPACE",
    ]
    for var in bkup_vars:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mock_kube_client():
    """Return a mocked instance of the Kubernetes client."""
    kube = MagicMock()
    kube.can_i_create_secrets.return_value = True
    return kube
