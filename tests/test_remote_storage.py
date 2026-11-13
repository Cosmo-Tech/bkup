import io
import gzip
import tarfile
import pytest
from unittest.mock import MagicMock, patch
from src.lib.remote_storage import RemoteStorage


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BKUP_STORAGE_PROTOCOL", "s3")
    monkeypatch.setenv("BKUP_STORAGE_ENDPOINT", "https://minio.local")
    monkeypatch.setenv("BKUP_STORAGE_ROOT", "my-bucket")
    monkeypatch.setenv("BKUP_STORAGE_USERNAME", "minioadmin")
    monkeypatch.setenv("BKUP_STORAGE_PASSWORD", "miniopass")
    monkeypatch.setenv("BKUP_ARCHIVE_NAME", "pvc_vol_261113112500")
    monkeypatch.setenv("BKUP_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("BKUP_JOB_NAMESPACE", "app-ns")
    return tmp_path


@patch("src.lib.remote_storage.Kubernetes")
def test_remote_storage_init_with_password_env(mock_kube_cls, base_env):
    rs = RemoteStorage()
    assert rs.address_prefix == "s3://my-bucket/app-ns"
    assert rs.password == "miniopass"
    assert rs.storage_options["key"] == "minioadmin"
    assert rs.storage_options["secret"] == "miniopass"
    assert rs.storage_options["client_kwargs"] == {"endpoint_url": "https://minio.local"}
    mock_kube_cls.return_value.get_secret_value.assert_not_called()


@patch("src.lib.remote_storage.Kubernetes")
def test_remote_storage_init_with_secret_lookup(mock_kube_cls, monkeypatch, tmp_path):
    monkeypatch.setenv("BKUP_STORAGE_PROTOCOL", "abfs")
    monkeypatch.setenv("BKUP_STORAGE_ROOT", "backups")
    monkeypatch.setenv("BKUP_STORAGE_USERNAME", "azuser")
    monkeypatch.setenv("BKUP_STORAGE_PASSWORD_SECRET_NAME", "bkup-secret")
    monkeypatch.setenv("BKUP_STORAGE_PASSWORD_SECRET_KEY", "password")
    monkeypatch.setenv("BKUP_ORCHESTRATOR_NAMESPACE", "bkup-system")
    monkeypatch.setenv("BKUP_JOB_NAMESPACE", "target-ns")
    monkeypatch.setenv("BKUP_MOUNT_PATH", str(tmp_path))

    mock_kube_cls.return_value.get_secret_value.return_value = "az-secret-key"

    rs = RemoteStorage()
    assert rs.password == "az-secret-key"
    mock_kube_cls.return_value.get_secret_value.assert_called_once_with(
        namespace="bkup-system", secret_name="bkup-secret", key="password"
    )


@patch("src.lib.remote_storage.Kubernetes")
@patch("src.lib.remote_storage.fsspec.open")
def test_remote_stream_exporter_volume(mock_fsspec_open, mock_kube_cls, base_env):
    # Create a sample file inside mount_path (no UNIX pipe present)
    sample_file = base_env / "hello.txt"
    sample_file.write_text("kubernetes volume data", encoding="utf-8")

    buffer = io.BytesIO()
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = buffer
    mock_fsspec_open.return_value = mock_cm

    rs = RemoteStorage()
    rs.remote_stream_exporter()

    mock_fsspec_open.assert_called_once_with(
        "s3://my-bucket/app-ns/pvc_vol_261113112500.tar.gz",
        "wb",
        **rs.storage_options,
    )

    # Verify that the generated tar.gz archive is valid and contains the file
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|gz") as tar:
        members = tar.getmembers()
        assert len(members) == 1
        assert members[0].name == "hello.txt"
        extracted = tar.extractfile(members[0]).read().decode("utf-8")
        assert extracted == "kubernetes volume data"


@patch("src.lib.remote_storage.Kubernetes")
@patch("src.lib.remote_storage.fsspec.open")
@patch("src.lib.remote_storage.time.sleep", return_value=None)
def test_remote_stream_exporter_pipe(mock_sleep, mock_fsspec_open, mock_kube_cls, base_env):
    # Simulate pipe mode (PostgreSQL dump)
    pipe_path = base_env / "pipe"
    pipe_path.write_bytes(b"CREATE TABLE test (id INT);")

    signal_path = base_env / "signal"
    signal_path.write_text("dumping:mydb", encoding="utf-8")

    # Transition signal to "finished" once "done:mydb" is written
    original_open = open

    def side_effect_open(file, mode="r", *args, **kwargs):
        f = original_open(file, mode, *args, **kwargs)
        if str(file) == str(signal_path) and "w" in mode:
            class SignalWrapper:
                def write(self, data):
                    f.write(data)
                    f.flush()
                    if data == "done:mydb":
                        with original_open(signal_path, "w") as f2:
                            f2.write("finished")

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    f.close()

            return SignalWrapper()
        return f

    buffer = io.BytesIO()
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = buffer
    mock_fsspec_open.return_value = mock_cm

    rs = RemoteStorage()
    with patch("builtins.open", side_effect=side_effect_open):
        rs.remote_stream_exporter()

    mock_fsspec_open.assert_called_once_with(
        "s3://my-bucket/app-ns/pvc_vol_261113112500_mydb.gz",
        "wb",
        **rs.storage_options,
    )
    assert gzip.decompress(buffer.getvalue()) == b"CREATE TABLE test (id INT);"


@patch("src.lib.remote_storage.Kubernetes")
@patch("src.lib.remote_storage.fsspec.open")
def test_remote_stream_importer_volume(mock_fsspec_open, mock_kube_cls, base_env):
    # Build an in-memory tar.gz archive
    archive_buf = io.BytesIO()
    with tarfile.open(fileobj=archive_buf, mode="w:gz") as tar:
        content = b"restored content"
        info = tarfile.TarInfo(name="restored.txt")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    archive_buf.seek(0)

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = archive_buf
    mock_fsspec_open.return_value = mock_cm

    rs = RemoteStorage()
    rs.remote_stream_importer(archive_name="pvc_vol_261113112500.tar.gz")

    restored_file = base_env / "restored.txt"
    assert restored_file.exists()
    assert restored_file.read_text(encoding="utf-8") == "restored content"


@patch("src.lib.remote_storage.Kubernetes")
@patch("src.lib.remote_storage.fsspec.filesystem")
def test_list_remote_backups(mock_fs_factory, mock_kube_cls, base_env, capsys):
    mock_fs = MagicMock()
    mock_fs.find.return_value = {
        "my-bucket/app-ns/pvc_vol_261113112500.tar.gz": {"type": "file", "size": 1024 * 1024 * 2},
        "my-bucket/app-ns/subdir": {"type": "directory", "size": 0},
    }
    mock_fs_factory.return_value = mock_fs

    rs = RemoteStorage()
    rs.list_remote_backups()

    captured = capsys.readouterr().out
    assert "2.00000 MB" in captured
    assert "pvc_vol_261113112500.tar.gz" in captured
