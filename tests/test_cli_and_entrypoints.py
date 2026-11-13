import sys
from unittest.mock import MagicMock, patch
import src.cli as cli
import src.exporter as exporter
import src.importer as importer


def test_format_date():
    assert cli.format_date("261113112500") == "26/11/13 11:25:00 (261113112500)"
    assert cli.format_date("invalid") == "invalid"


def test_get_grouped_backups():
    mock_rs = MagicMock()
    mock_rs.address_prefix = "s3://bucket/ns"
    mock_rs.get_filesystem.return_value.find.return_value = {
        "bucket/ns/pvc1_vol1_261113112500.tar.gz": {"type": "file"},
        "bucket/ns/psql_hash_261113112500_mydb.gz": {"type": "file"},
        "bucket/ns/custom_manual_backup.tar.gz": {"type": "file"},
    }

    grouped = cli.get_grouped_backups(mock_rs)
    assert set(grouped["261113112500"]) == {
        "pvc1_vol1_261113112500.tar.gz",
        "psql_hash_261113112500_mydb.gz",
    }
    assert grouped["other"] == ["custom_manual_backup.tar.gz"]


@patch("src.cli.RemoteStorage")
def test_cli_list_in_cluster(mock_rs_cls, monkeypatch):
    monkeypatch.setenv("BKUP_ORCHESTRATOR_NAMESPACE", "bkup-system")
    monkeypatch.setattr(sys, "argv", ["bkup", "list"])

    cli.main()
    mock_rs_cls.return_value.list_remote_backups.assert_called_once()


def test_cli_export_in_cluster(monkeypatch):
    monkeypatch.setenv("BKUP_ORCHESTRATOR_NAMESPACE", "bkup-system")
    monkeypatch.setattr(sys, "argv", ["bkup", "export"])

    mock_orch = MagicMock()
    with patch.dict(sys.modules, {"src.orchestrator": mock_orch}):
        cli.main()
        mock_orch.job_export_volumes.assert_called_once()
        mock_orch.job_export_postgresql.assert_called_once()


@patch("src.cli.Kubernetes")
@patch("src.cli.inquirer.text")
def test_cli_import_non_interactive_volume(mock_text, mock_kube_cls, monkeypatch):
    monkeypatch.setenv("BKUP_ORCHESTRATOR_NAMESPACE", "bkup-system")
    monkeypatch.setattr(
        sys,
        "argv",
        ["bkup", "import", "-n", "app-ns", "-f", "pvc_vol_261113112500.tar.gz"],
    )
    mock_text.return_value.execute.return_value = "y"

    mock_orch = MagicMock()
    with patch.dict(sys.modules, {"src.orchestrator": mock_orch}):
        cli.main()
        mock_orch.job_import_volume.assert_called_once_with(
            namespace="app-ns", archive_name="pvc_vol_261113112500.tar.gz"
        )


@patch("src.exporter.RemoteStorage")
def test_exporter_entrypoint(mock_rs_cls):
    exporter.main()
    mock_rs_cls.return_value.remote_stream_exporter.assert_called_once()


@patch("src.importer.RemoteStorage")
def test_importer_entrypoint(mock_rs_cls, monkeypatch):
    monkeypatch.setenv("BKUP_ARCHIVE_NAME", "archive.tar.gz")
    importer.main()
    mock_rs_cls.return_value.remote_stream_importer.assert_called_once_with(
        archive_name="archive.tar.gz"
    )
