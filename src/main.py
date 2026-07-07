"""
main.py
-------
ClusterSnap — CLI entrypoint.

Invoked by the Kubernetes CronJob as:

    python main.py --mode export
    python main.py --mode import --remote-key clustersnap/backups/ns/foo.zip

Environment variables are the sole configuration mechanism (12-factor app).
See ``config.py`` for the full variable reference.

Exit codes
----------
0   Success
1   Configuration error (bad / missing env vars)
2   Runtime error (backup / restore failure, storage error, etc.)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from config import AppConfig, ConfigError, OperationMode, StorageProvider, load_config
from storage.base import BaseStorageProvider
from storage.s3 import S3StorageProvider
from storage.azure import AzureStorageProvider
from backup.exporter import Exporter
from backup.importer import Importer
from backup.secret_migrator import SecretMigrator
from telemetry.logger import get_logger
from telemetry.metrics import push_metrics


def _build_storage(cfg: AppConfig) -> BaseStorageProvider:
    """
    Instantiate the correct ``BaseStorageProvider`` subclass from
    ``cfg.provider``.

    Raises
    ------
    ValueError
        If ``cfg.provider`` is not a supported value (should never happen
        after ``AppConfig`` validation, but guards against future additions).
    """
    if cfg.provider is StorageProvider.S3:
        assert cfg.s3 is not None  # guaranteed by AppConfig validator
        return S3StorageProvider(
            bucket=cfg.bucket,
            access_key_id=cfg.s3.access_key_id,
            secret_access_key=cfg.s3.secret_access_key,
            region=cfg.s3.region,
            endpoint_url=cfg.s3.endpoint_url,
        )

    if cfg.provider is StorageProvider.AZURE:
        assert cfg.azure is not None  # guaranteed by AppConfig validator
        return AzureStorageProvider(
            bucket=cfg.bucket,
            storage_account=cfg.azure.storage_account,
            storage_key=cfg.azure.storage_key,
            client_id=cfg.azure.client_id,
            client_secret=cfg.azure.client_secret,
            tenant_id=cfg.azure.tenant_id,
        )

    raise ValueError(f"Unsupported storage provider: {cfg.provider!r}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="clustersnap",
        description="Cloud-agnostic Kubernetes PVC backup and restore tool.",
    )
    parser.add_argument(
        "--mode",
        choices=["export", "import"],
        default=None,
        help=(
            "Operation mode. Overrides the MODE environment variable. "
            "Use 'export' in the CronJob manifest; 'import' for manual restore."
        ),
    )
    parser.add_argument(
        "--remote-key",
        default=None,
        metavar="KEY",
        help=(
            "Remote object key of the archive to restore (import mode only). "
            "Example: clustersnap/backups/tenant-alpha/tenant-alpha-2026-05-17T12-00-00Z.zip"
        ),
    )
    parser.add_argument(
        "--skip-secret-migration",
        action="store_true",
        default=False,
        help=(
            "Skip the PostgreSQL secret migration step after import. "
            "Overrides SKIP_SECRET_MIGRATION env var."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point.

    Returns
    -------
    int
        Process exit code (0 = success, 1 = config error, 2 = runtime error).
    """
    args = _parse_args(argv)

    # ------------------------------------------------------------------
    # 1. Load and validate configuration
    # ------------------------------------------------------------------
    try:
        cfg = load_config()
    except ConfigError as exc:
        # Use a plain stderr print here because the JSON logger is not yet
        # configured (we need cfg.log_level to set it up).
        print(f"[FATAL] Configuration error: {exc}", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # 2. Configure logger with the validated log level
    # ------------------------------------------------------------------
    log = get_logger(__name__, level=logging.getLevelName(cfg.log_level.value))

    # CLI --mode flag overrides the env var (useful for local testing)
    if args.mode:
        try:
            cfg.mode = OperationMode(args.mode)
        except ValueError:
            log.error("Invalid --mode argument", extra={"mode": args.mode})
            return 1

    # ------------------------------------------------------------------
    # 3. Initialise storage provider and perform health check
    # ------------------------------------------------------------------
    try:
        storage = _build_storage(cfg)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "Failed to initialise storage provider",
            extra={"provider": cfg.provider.value, "error": str(exc)},
        )
        return 2

    if not storage.health_check():
        log.error(
            "Storage health check failed — aborting",
            extra={"provider": cfg.provider.value, "bucket": cfg.bucket},
        )
        return 2

    # ------------------------------------------------------------------
    # 4. Build workflow objects (resolves actual kubectl context name)
    # ------------------------------------------------------------------
    exporter: Exporter | None = None
    importer: Importer | None = None

    if cfg.mode is OperationMode.EXPORT:
        exporter = Exporter(cfg, storage)
        resolved_context = exporter.resolved_context
    else:
        importer = Importer(cfg, storage)
        resolved_context = importer.resolved_context

    log.info(
        "ClusterSnap starting",
        extra={
            "mode": cfg.mode.value,
            "namespace": cfg.namespace,
            "provider": cfg.provider.value,
            "bucket": cfg.bucket,
            "log_level": cfg.log_level.value,
            "kube_context": resolved_context,
        },
    )

    # ------------------------------------------------------------------
    # 5. Dispatch to export or import workflow
    # ------------------------------------------------------------------
    try:
        if cfg.mode is OperationMode.EXPORT:
            assert exporter is not None
            exporter.run()

        elif cfg.mode is OperationMode.IMPORT:
            if not args.remote_key:
                log.error(
                    "Import mode requires --remote-key to be specified",
                    extra={"namespace": cfg.namespace},
                )
                return 1
            assert importer is not None
            importer.run(remote_key=args.remote_key)

            # ── Secret migration (post-import) ─────────────────────────
            # Apply CLI flag on top of env-var config
            if args.skip_secret_migration:
                cfg.skip_secret_migration = True
            SecretMigrator(cfg).run()

    except RuntimeError as exc:
        log.error(
            "Workflow failed",
            extra={"mode": cfg.mode.value, "error": str(exc)},
        )
        return 2

    # ------------------------------------------------------------------
    # 6. Push Prometheus metrics to Pushgateway
    #    (best-effort — a push failure should never mask a backup success)
    # ------------------------------------------------------------------
    try:
        push_metrics(
            job="clustersnap",
            namespace=cfg.namespace,
            pushgateway_url=cfg.pushgateway_url,
        )
    except RuntimeError as exc:
        log.warning(
            "Pushgateway push failed (non-fatal)",
            extra={"error": str(exc)},
        )

    log.info(
        "ClusterSnap finished",
        extra={"mode": cfg.mode.value, "namespace": cfg.namespace},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
