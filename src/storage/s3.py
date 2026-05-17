"""
storage/s3.py
-------------
AWS S3 / MinIO / SeaweedFS storage provider for ClusterSnap.

Supports three deployment targets via the optional ``endpoint_url``:
- AWS S3          — leave ``endpoint_url`` as ``None``
- MinIO           — set ``S3_ENDPOINT_URL=http://minio:9000``
- SeaweedFS S3    — set ``S3_ENDPOINT_URL=http://seaweedfs-s3:8333``
  (SeaweedFS is used in the legacy ``migrate_secrets-postgresql.sh`` stack)

Authentication is handled entirely through the standard boto3 credential
chain; no custom logic is required beyond passing ``aws_access_key_id`` and
``aws_secret_access_key`` explicitly from ``AppConfig``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from storage.base import BaseStorageProvider
from telemetry.logger import get_logger

log = get_logger(__name__)


class S3StorageProvider(BaseStorageProvider):
    """
    Concrete storage provider backed by AWS S3, MinIO, or SeaweedFS.

    Parameters
    ----------
    bucket:
        Target S3 bucket name.
    access_key_id:
        AWS / MinIO / SeaweedFS access key.
    secret_access_key:
        AWS / MinIO / SeaweedFS secret key.
    region:
        AWS region (default ``"us-east-1"``; ignored by MinIO/SeaweedFS).
    endpoint_url:
        Override the S3 endpoint for MinIO or SeaweedFS.  Leave ``None``
        to target AWS S3.
    """

    def __init__(
        self,
        *,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        super().__init__(bucket)
        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            endpoint_url=endpoint_url or None,
        )
        self._region = region
        self._endpoint_url = endpoint_url

        log.debug(
            "S3StorageProvider initialised",
            extra={
                "bucket": bucket,
                "region": region,
                "endpoint_url": endpoint_url or "aws-default",
            },
        )

    # ------------------------------------------------------------------
    # BaseStorageProvider interface
    # ------------------------------------------------------------------

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """
        Upload ``local_path`` to ``s3://<bucket>/<remote_path>``.

        Parameters
        ----------
        local_path:
            Path to the local file to upload.  Must exist and be readable.
        remote_path:
            Destination S3 key (e.g. ``"clustersnap/backups/foo.zip"``).

        Raises
        ------
        FileNotFoundError
            If ``local_path`` does not exist.
        PermissionError
            If the credentials lack ``s3:PutObject`` on the bucket.
        RuntimeError
            For any other boto3 / botocore error.
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        size_bytes = local_path.stat().st_size
        log.info(
            "S3 upload started",
            extra={
                "local_path": str(local_path),
                "remote_path": remote_path,
                "bucket": self._bucket,
                "size_bytes": size_bytes,
            },
        )

        try:
            self._client.upload_file(
                Filename=str(local_path),
                Bucket=self._bucket,
                Key=remote_path,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            if code in {"403", "AccessDenied"}:
                raise PermissionError(
                    f"S3 access denied — bucket={self._bucket!r} key={remote_path!r}: {exc}"
                ) from exc
            raise RuntimeError(
                f"S3 upload failed — bucket={self._bucket!r} key={remote_path!r}: {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise RuntimeError(
                f"S3 (botocore) upload error — {exc}"
            ) from exc

        log.info(
            "S3 upload succeeded",
            extra={
                "remote_path": remote_path,
                "bucket": self._bucket,
                "size_bytes": size_bytes,
            },
        )

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """
        Download ``s3://<bucket>/<remote_path>`` to ``local_path``.

        Parameters
        ----------
        remote_path:
            Source S3 key.
        local_path:
            Destination path on the local filesystem.  The parent directory
            must already exist.

        Raises
        ------
        FileNotFoundError
            If the S3 key does not exist (HTTP 404 / NoSuchKey).
        PermissionError
            If the credentials lack ``s3:GetObject`` on the bucket.
        RuntimeError
            For any other boto3 / botocore error.
        """
        log.info(
            "S3 download started",
            extra={
                "remote_path": remote_path,
                "bucket": self._bucket,
                "local_path": str(local_path),
            },
        )

        try:
            self._client.download_file(
                Bucket=self._bucket,
                Key=remote_path,
                Filename=str(local_path),
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            if code in {"404", "NoSuchKey"}:
                raise FileNotFoundError(
                    f"S3 key not found bucket={self._bucket!r} key={remote_path!r}"
                ) from exc
            if code in {"403", "AccessDenied"}:
                raise PermissionError(
                    f"S3 access denied bucket={self._bucket!r} key={remote_path!r}: {exc}"
                ) from exc
            raise RuntimeError(
                f"S3 download failed bucket={self._bucket!r} key={remote_path!r}: {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise RuntimeError(f"S3 (botocore) download error {exc}") from exc

        log.info(
            "S3 download succeeded",
            extra={"remote_path": remote_path, "local_path": str(local_path)},
        )

    def health_check(self) -> bool:
        """
        Verify connectivity and bucket accessibility via a lightweight
        ``HeadBucket`` call.

        Returns
        -------
        bool
            ``True`` if the bucket is reachable and accessible.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
            log.debug("S3 health check passed", extra={"bucket": self._bucket})
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "S3 health check failed",
                extra={"bucket": self._bucket, "error": str(exc)},
            )
            return False


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def s3_provider_from_env(bucket: str) -> S3StorageProvider:
    """
    Construct an ``S3StorageProvider`` directly from environment variables.

    Useful in tests or one-off scripts that don't go through ``AppConfig``.

    Required env vars: ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``
    Optional env vars: ``AWS_REGION``, ``S3_ENDPOINT_URL``
    """
    return S3StorageProvider(
        bucket=bucket,
        access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
    )
