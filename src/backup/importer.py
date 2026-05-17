"""
backup/importer.py
------------------
Import (restore) engine for ClusterSnap.

Reverse-engineered from ``kubernetes-backups/read.sh`` and the import half
of the legacy backup workflow.

Legacy behaviour reproduced
----------------------------
``read.sh`` lists ``.tar`` archives from a local staging directory and
inspects their contents. The Python importer goes one step further:

1. Downloads the target ZIP archive from cloud storage.
2. Extracts the full archive into the configured restore path.
3. Preserves the original directory structure inside the ZIP (every PVC
   directory is extracted relative to the restore root).
4. Cleans up the downloaded staging file after extraction.

The restore path defaults to ``/data/src-disks`` — the same volume mount
used by the exporter — so restored data is immediately visible to workload
containers sharing that volume.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from telemetry.logger import get_logger

if TYPE_CHECKING:
    from config import AppConfig
    from storage.base import BaseStorageProvider

log = get_logger(__name__)

DEFAULT_RESTORE_ROOT = Path("/data/src-disks")


class Importer:
    """
    Orchestrates the full import (restore) workflow for one namespace.

    Parameters
    ----------
    config:
        Fully validated ``AppConfig`` instance from ``config.load_config()``.
    storage:
        A concrete ``BaseStorageProvider`` (S3 or Azure) already initialised
        with the source bucket.
    restore_root:
        Directory into which the archive will be extracted.  Defaults to
        ``/data/src-disks``; override in tests or non-standard deployments.
    """

    def __init__(
        self,
        config: "AppConfig",
        storage: "BaseStorageProvider",
        restore_root: Path = DEFAULT_RESTORE_ROOT,
    ) -> None:
        self._cfg = config
        self._storage = storage
        self._restore_root = restore_root

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, remote_key: str) -> None:
        """
        Execute the full import workflow:

        1. Download the ZIP archive identified by ``remote_key`` to a local
           staging area.
        2. Validate the downloaded file is a well-formed ZIP.
        3. Extract all contents to ``restore_root``, preserving directory
           structure.
        4. Clean up the local staging file unconditionally.

        Parameters
        ----------
        remote_key:
            Full remote object key of the archive to restore, e.g.
            ``"clustersnap/backups/tenant-alpha/tenant-alpha-2026-05-17T12-00-00Z.zip"``.

        Raises
        ------
        FileNotFoundError
            If ``remote_key`` does not exist in the bucket.
        RuntimeError
            For download errors, corrupt archives, or extraction failures.
        """
        namespace = self._cfg.namespace
        staging_dir = self._cfg.backup_dir / namespace
        staging_dir.mkdir(parents=True, exist_ok=True)

        archive_filename = Path(remote_key).name
        local_archive = staging_dir / archive_filename

        log.info(
            "Import workflow started",
            extra={
                "namespace": namespace,
                "remote_key": remote_key,
                "restore_root": str(self._restore_root),
            },
        )

        try:
            self._download(remote_key, local_archive)
            self._validate_archive(local_archive)
            extracted_count = self._extract(local_archive, self._restore_root)

            log.info(
                "Import workflow completed successfully",
                extra={
                    "namespace": namespace,
                    "remote_key": remote_key,
                    "restore_root": str(self._restore_root),
                    "files_extracted": extracted_count,
                },
            )

        except Exception as exc:
            log.error(
                "Import workflow failed",
                extra={"namespace": namespace, "remote_key": remote_key, "error": str(exc)},
            )
            raise RuntimeError(
                f"Import failed for namespace={namespace!r} key={remote_key!r}: {exc}"
            ) from exc

        finally:
            self._cleanup(local_archive)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _download(self, remote_key: str, local_path: Path) -> None:
        """Download the remote archive to the local staging path."""
        log.info(
            "Download started",
            extra={
                "remote_key": remote_key,
                "local_path": str(local_path),
                "provider": type(self._storage).__name__,
            },
        )
        self._storage.download_file(remote_key, local_path)
        size_bytes = local_path.stat().st_size
        log.info(
            "Download succeeded",
            extra={"local_path": str(local_path), "size_bytes": size_bytes},
        )

    @staticmethod
    def _validate_archive(archive_path: Path) -> None:
        """
        Check that the downloaded file is a valid ZIP and is not corrupted.

        Raises
        ------
        RuntimeError
            If the file is not a valid ZIP or its CRC checks fail.
        """
        if not zipfile.is_zipfile(archive_path):
            raise RuntimeError(
                f"Downloaded file is not a valid ZIP archive: {archive_path}"
            )
        # Test CRC of every member — equivalent to ``unzip -t``
        with zipfile.ZipFile(archive_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(
                    f"ZIP archive is corrupt — first bad file: {bad!r} in {archive_path}"
                )
        log.debug("Archive validation passed", extra={"archive": str(archive_path)})

    @staticmethod
    def _extract(archive_path: Path, restore_root: Path) -> int:
        """
        Extract all files from the archive into ``restore_root``.

        Existing files are overwritten; directories are created as needed.
        Unsafe paths (those that would write outside ``restore_root`` via
        ``../`` traversal) are rejected.

        Returns
        -------
        int
            Number of files extracted.
        """
        restore_root.mkdir(parents=True, exist_ok=True)

        log.info(
            "Archive extraction started",
            extra={
                "archive": str(archive_path),
                "restore_root": str(restore_root),
            },
        )

        extracted = 0
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                # Security: reject path-traversal attempts
                target = (restore_root / member.filename).resolve()
                if not str(target).startswith(str(restore_root.resolve())):
                    log.warning(
                        "Skipping unsafe ZIP member (path traversal detected)",
                        extra={"member": member.filename},
                    )
                    continue

                zf.extract(member, path=restore_root)
                extracted += 1

        log.info(
            "Archive extraction completed",
            extra={
                "restore_root": str(restore_root),
                "files_extracted": extracted,
            },
        )
        return extracted

    @staticmethod
    def _cleanup(local_archive: Path) -> None:
        """Remove the local staging archive, logging the outcome."""
        try:
            local_archive.unlink(missing_ok=True)
            log.debug(
                "Local archive cleaned up",
                extra={"archive": str(local_archive)},
            )
        except OSError as exc:
            log.warning(
                "Failed to clean up local archive",
                extra={"archive": str(local_archive), "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Discovery helper
    # ------------------------------------------------------------------

    def list_available_backups(self) -> list[str]:
        """
        Return a list of local staging ``.zip`` files for the configured
        namespace — mirrors the inventory output of ``read.sh``.

        Useful for debugging or manual restore selection; not called during
        the automated CronJob workflow.
        """
        staging_dir = self._cfg.backup_dir / self._cfg.namespace
        if not staging_dir.exists():
            log.warning(
                "Staging directory does not exist",
                extra={"staging_dir": str(staging_dir)},
            )
            return []

        archives = sorted(staging_dir.glob("*.zip"))
        log.info(
            "Available local backups",
            extra={
                "namespace": self._cfg.namespace,
                "count": len(archives),
                "archives": [str(a) for a in archives],
            },
        )
        return [str(a) for a in archives]
