"""
storage/base.py
---------------
Abstract Base Class for all ClusterSnap cloud storage providers.

Every concrete provider (S3, Azure Blob, GCP, …) must subclass
`BaseStorageProvider` and implement the two primitive operations defined here.
This guarantees a stable, provider-agnostic interface for the rest of the
codebase — callers never depend on a concrete implementation.
"""

from __future__ import annotations

import abc
from pathlib import Path


class BaseStorageProvider(abc.ABC):
    """
    Defines the minimal contract that every storage backend must fulfil.

    Implementors
    ------------
    - ``storage.s3.S3StorageProvider``   – AWS S3 / MinIO / SeaweedFS
    - ``storage.azure.AzureStorageProvider`` – Azure Blob Storage
    - ``storage.gcp.GCSStorageProvider`` – Google Cloud Storage

    Design notes
    ------------
    * Both methods are **synchronous** by design; ClusterSnap runs as a
      short-lived Kubernetes CronJob where async complexity adds no value.
    * Paths are expressed as ``pathlib.Path`` objects for safety; concrete
      implementations may cast them to ``str`` as needed by their SDK.
    * Implementations are expected to raise concrete, descriptive exceptions
      (``IOError``, ``PermissionError``, provider-specific SDK errors, …)
      rather than swallowing them. The calling layer in ``backup/exporter.py``
      and ``backup/importer.py`` owns the retry / cleanup logic.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, bucket: str) -> None:
        """
        Parameters
        ----------
        bucket:
            The target bucket name (S3 / GCS) or container name (Azure Blob).
            Concrete implementations may require additional keyword arguments
            via their own ``__init__`` (e.g. endpoint_url for MinIO) but must
            call ``super().__init__(bucket)`` first.
        """
        if not bucket:
            raise ValueError("bucket name must be a non-empty string")
        self._bucket: str = bucket

    @property
    def bucket(self) -> str:
        """Read-only access to the configured bucket / container name."""
        return self._bucket

    # ------------------------------------------------------------------
    # Abstract interface — subclasses MUST implement these two methods
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """
        Upload a local file to the remote storage backend.

        Parameters
        ----------
        local_path:
            Absolute (or CWD-relative) path to the file on the local
            filesystem that should be uploaded. The file **must** exist and be
            readable; implementations are not required to re-validate this.
        remote_path:
            The destination key / blob name inside the bucket (e.g.
            ``"clustersnap/backups/tenant-alpha/2026-05-17T12:00:00Z.zip"``).
            Must **not** start with a leading slash.

        Raises
        ------
        FileNotFoundError
            If ``local_path`` does not exist on disk.
        PermissionError
            If the credentials lack write access to the bucket.
        RuntimeError
            For any provider-specific, unrecoverable upload failure.
        """
        ...

    @abc.abstractmethod
    def download_file(self, remote_path: str, local_path: Path) -> None:
        """
        Download a remote object to a local file path.

        Parameters
        ----------
        remote_path:
            The source key / blob name inside the bucket (e.g.
            ``"clustersnap/backups/tenant-alpha/2026-05-17T12:00:00Z.zip"``).
        local_path:
            Destination path on the local filesystem. The parent directory
            **must** exist; implementations will create the file but not
            intermediate directories.

        Raises
        ------
        FileNotFoundError
            If ``remote_path`` does not exist in the bucket.
        PermissionError
            If the credentials lack read access to the bucket.
        RuntimeError
            For any provider-specific, unrecoverable download failure.
        """
        ...

    # ------------------------------------------------------------------
    # Optional hook — subclasses MAY override
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """
        Verify that the provider is reachable and the bucket is accessible.

        The default implementation performs no check and always returns
        ``True``; concrete providers should override this to perform a
        lightweight HEAD / list-bucket operation so that ``main.py`` can fail
        fast at startup rather than during the actual backup.

        Returns
        -------
        bool
            ``True`` if the backend is healthy, ``False`` otherwise.
        """
        return True

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(bucket={self._bucket!r})"
