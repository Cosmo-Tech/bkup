from unittest.mock import MagicMock, patch
from src.storage.azure import AzureStorage


@patch("src.storage.azure.BlobServiceClient")
def test_azure_storage_init_creates_container(mock_blob_client_class):
    # Setup mock to intercept the BlobServiceClient instance
    mock_instance = mock_blob_client_class.return_value

    storage = AzureStorage(
        storage_address="https://test.blob.core.windows.net",
        storage_username="user",
        storage_password="password",
        root_path_target="Test_Cluster.Name",
    )

    # Verify that the container name has been sanitized (lowercase, dashes)
    assert storage.blob_container == "test-cluster-name"

    # Verify that the client attempted to create the container
    mock_instance.create_container.assert_called_once_with(name="test-cluster-name")


@patch("src.storage.azure.BlobServiceClient")
def test_azure_upload_stream_success(mock_blob_client_class):
    mock_instance = mock_blob_client_class.return_value
    mock_blob_client = MagicMock()
    mock_instance.get_blob_client.return_value = mock_blob_client

    storage = AzureStorage(
        storage_address="https://test.blob.core.windows.net",
        storage_username="user",
        storage_password="pwd",
        root_path_target="test-cluster",
    )

    # Simulate a generator
    generator = [b"chunk1", b"chunk2"]

    # Execute the upload
    success = storage.upload_stream(
        pvc_name="my-pvc", path="/fake/path", data_generator=generator, backup_type="volume"
    )

    assert success is True
    # Verify that the native Azure upload_blob function was called with our parameters
    mock_blob_client.upload_blob.assert_called_once_with(generator, overwrite=True, max_concurrency=2)
