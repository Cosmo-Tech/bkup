# """
# storage/azure.py
# ----------------
# Azure Blob Storage provider for ClusterSnap.

# Supports two authentication strategies, resolved at runtime:

# 1. **Shared-key** (``AZURE_STORAGE_KEY`` is set)
#    Uses ``BlobServiceClient`` with an account-key connection string.
#    Fastest to configure; suitable for MinIO-compatible Azure emulators (Azurite).

# 2. **Service-principal** (``AZURE_CLIENT_ID`` + ``AZURE_CLIENT_SECRET`` +
#    ``AZURE_TENANT_ID`` are set)
#    Uses ``ClientSecretCredential`` from ``azure-identity``.
#    Recommended for production; no long-lived key in environment variables.

# The strategy is selected automatically by ``AppConfig`` / ``AzureConfig``
# — this class just accepts the pre-validated config object.
# """

# from __future__ import annotations

# from pathlib import Path
# from typing import Optional

# from azure.core.exceptions import (
#     AzureError,
#     ResourceNotFoundError,
#     HttpResponseError,
# )
# from azure.identity import ClientSecretCredential
# from azure.storage.blob import BlobServiceClient

# from storage.base import BaseStorageProvider
# from telemetry.logger import get_logger

# log = get_logger(__name__)


# class AzureStorageProvider(BaseStorageProvider):
#     """
#     Concrete storage provider backed by Azure Blob Storage.

#     Parameters
#     ----------
#     bucket:
#         Azure Blob **container** name.  Must already exist (ClusterSnap does
#         not create containers to avoid accidental data placement).
#     storage_account:
#         Azure storage account name.
#     storage_key:
#         Account shared key.  Mutually exclusive with ``client_id`` /
#         ``client_secret`` / ``tenant_id``.
#     client_id / client_secret / tenant_id:
#         Service-principal credentials.  All three must be supplied together.
#     """

#     def __init__(
#         self,
#         *,
#         bucket: str,
#         storage_account: str,
#         storage_key: Optional[str] = None,
#         client_id: Optional[str] = None,
#         client_secret: Optional[str] = None,
#         tenant_id: Optional[str] = None,
#     ) -> None:
#         super().__init__(bucket)

#         account_url = f"https://{storage_account}.blob.core.windows.net"

#         if storage_key:
#             # Shared-key authentication
#             conn_str = (
#                 f"DefaultEndpointsProtocol=https;"
#                 f"AccountName={storage_account};"
#                 f"AccountKey={storage_key};"
#                 f"EndpointSuffix=core.windows.net"
#             )
#             self._service_client = BlobServiceClient.from_connection_string(conn_str)
#             auth_method = "shared-key"
#         elif client_id and client_secret and tenant_id:
#             # Service-principal authentication
#             credential = ClientSecretCredential(
#                 tenant_id=tenant_id,
#                 client_id=client_id,
#                 client_secret=client_secret,
#             )
#             self._service_client = BlobServiceClient(
#                 account_url=account_url, credential=credential
#             )
#             auth_method = "service-principal"
#         else:
#             raise ValueError(
#                 "AzureStorageProvider requires either storage_key (shared-key auth) "
#                 "or client_id + client_secret + tenant_id (service-principal auth)."
#             )

#         self._container_client = self._service_client.get_container_client(bucket)

#         log.debug(
#             "AzureStorageProvider initialised",
#             extra={
#                 "storage_account": storage_account,
#                 "container": bucket,
#                 "auth_method": auth_method,
#             },
#         )

#     # ------------------------------------------------------------------
#     # BaseStorageProvider interface
#     # ------------------------------------------------------------------

#     def upload_file(self, local_path: Path, remote_path: str) -> None:
#         """
#         Upload ``local_path`` to ``azure://<container>/<remote_path>``.

#         Parameters
#         ----------
#         local_path:
#             Path to the local file.  Must exist and be readable.
#         remote_path:
#             Destination blob name (e.g. ``"clustersnap/backups/foo.zip"``).

#         Raises
#         ------
#         FileNotFoundError
#             If ``local_path`` does not exist.
#         PermissionError
#             If the credentials lack write access to the container.
#         RuntimeError
#             For any other Azure SDK error.
#         """
#         if not local_path.exists():
#             raise FileNotFoundError(f"Local file not found: {local_path}")

#         size_bytes = local_path.stat().st_size
#         log.info(
#             "Azure upload started",
#             extra={
#                 "local_path": str(local_path),
#                 "remote_path": remote_path,
#                 "container": self._bucket,
#                 "size_bytes": size_bytes,
#             },
#         )

#         try:
#             blob_client = self._container_client.get_blob_client(remote_path)
#             with local_path.open("rb") as fh:
#                 blob_client.upload_blob(fh, overwrite=True)
#         except HttpResponseError as exc:
#             if exc.status_code == 403:
#                 raise PermissionError(
#                     f"Azure access denied — container={self._bucket!r} "
#                     f"blob={remote_path!r}: {exc}"
#                 ) from exc
#             raise RuntimeError(
#                 f"Azure upload failed — container={self._bucket!r} "
#                 f"blob={remote_path!r}: {exc}"
#             ) from exc
#         except AzureError as exc:
#             raise RuntimeError(f"Azure upload error — {exc}") from exc

#         log.info(
#             "Azure upload succeeded",
#             extra={
#                 "remote_path": remote_path,
#                 "container": self._bucket,
#                 "size_bytes": size_bytes,
#             },
#         )

#     def download_file(self, remote_path: str, local_path: Path) -> None:
#         """
#         Download ``azure://<container>/<remote_path>`` to ``local_path``.

#         Parameters
#         ----------
#         remote_path:
#             Source blob name inside the container.
#         local_path:
#             Destination path on the local filesystem.  The parent directory
#             must already exist.

#         Raises
#         ------
#         FileNotFoundError
#             If the blob does not exist (HTTP 404).
#         PermissionError
#             If the credentials lack read access to the container.
#         RuntimeError
#             For any other Azure SDK error.
#         """
#         log.info(
#             "Azure download started",
#             extra={
#                 "remote_path": remote_path,
#                 "container": self._bucket,
#                 "local_path": str(local_path),
#             },
#         )

#         try:
#             blob_client = self._container_client.get_blob_client(remote_path)
#             with local_path.open("wb") as fh:
#                 download_stream = blob_client.download_blob()
#                 fh.write(download_stream.readall())
#         except ResourceNotFoundError as exc:
#             raise FileNotFoundError(
#                 f"Azure blob not found — container={self._bucket!r} "
#                 f"blob={remote_path!r}"
#             ) from exc
#         except HttpResponseError as exc:
#             if exc.status_code == 403:
#                 raise PermissionError(
#                     f"Azure access denied — container={self._bucket!r} "
#                     f"blob={remote_path!r}: {exc}"
#                 ) from exc
#             raise RuntimeError(
#                 f"Azure download failed — container={self._bucket!r} "
#                 f"blob={remote_path!r}: {exc}"
#             ) from exc
#         except AzureError as exc:
#             raise RuntimeError(f"Azure download error — {exc}") from exc

#         log.info(
#             "Azure download succeeded",
#             extra={"remote_path": remote_path, "local_path": str(local_path)},
#         )

#     def health_check(self) -> bool:
#         """
#         Verify connectivity by performing a ``get_container_properties`` call.

#         Returns
#         -------
#         bool
#             ``True`` if the container is reachable and accessible.
#         """
#         try:
#             self._container_client.get_container_properties()
#             log.debug("Azure health check passed", extra={"container": self._bucket})
#             return True
#         except Exception as exc:  # noqa: BLE001
#             log.warning(
#                 "Azure health check failed",
#                 extra={"container": self._bucket, "error": str(exc)},
#             )
#             return False
