"""
config.py
---------
Environment-variable driven configuration with Pydantic v2 validation.

All runtime parameters are read from environment variables.  A single call to
``load_config()`` returns a fully validated ``AppConfig`` instance; any
missing required variable or type mismatch raises a ``ConfigError`` with a
human-readable message **before** any backup work starts.

Environment variables
---------------------
Core
~~~~
MODE                    "export" | "import"  (required)
NAMESPACE               Kubernetes namespace being backed up  (required)
PROVIDER                "s3" | "azure"  (required)
BUCKET                  Bucket / container name  (required)
BACKUP_DIR              Local work directory (default: /tmp/clustersnap)
REMOTE_PREFIX           Key prefix inside the bucket (default: clustersnap/backups)
LOG_LEVEL               DEBUG | INFO | WARNING | ERROR (default: INFO)

AWS S3 / MinIO / SeaweedFS  (required when PROVIDER=s3)
~~~~~~~~~~~~~~~~~~~~~~~~~
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION              (default: us-east-1)
S3_ENDPOINT_URL         Override endpoint for MinIO / SeaweedFS (optional)

Azure Blob Storage  (required when PROVIDER=azure)
~~~~~~~~~~~~~~~~~~
AZURE_STORAGE_ACCOUNT
AZURE_STORAGE_KEY       Shared-key auth  OR
AZURE_CLIENT_ID         } Service-principal auth
AZURE_CLIENT_SECRET     }
AZURE_TENANT_ID         }

Observability
~~~~~~~~~~~~~
PUSHGATEWAY_URL         (default: http://prometheus-pushgateway:9091)

Kubernetes
~~~~~~~~~~
KUBE_CONTEXT            kubectl context for target cluster (default: current)

Import-specific
~~~~~~~~~~~~~~~
HELPER_POD_IMAGE        Image for disposable restore pods (default: debian:bookworm-slim)
SCALE_WAIT_SECONDS      Seconds to wait after scale-down (default: 15)
HELPER_POD_TIMEOUT_SECONDS  kubectl wait timeout for helper pod Ready (default: 60)

Secret migration (import mode)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
SOURCE_KUBE_CONTEXT     kubectl context of the source cluster to read secrets from
SKIP_SECRET_MIGRATION   Set to "true" to skip secret migration (default: false)
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as _PydanticValidationError


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OperationMode(str, Enum):
    EXPORT = "export"
    IMPORT = "import"


class StorageProvider(str, Enum):
    S3 = "s3"
    AZURE = "azure"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Sub-config models
# ---------------------------------------------------------------------------


class S3Config(BaseModel):
    """Credentials and endpoint configuration for AWS S3 / MinIO / SeaweedFS."""

    access_key_id: str = Field(alias="AWS_ACCESS_KEY_ID")
    secret_access_key: str = Field(alias="AWS_SECRET_ACCESS_KEY")
    region: str = Field(default="us-east-1", alias="AWS_REGION")
    endpoint_url: Optional[str] = Field(default=None, alias="S3_ENDPOINT_URL")

    model_config = {"populate_by_name": True}

    @field_validator("access_key_id", "secret_access_key")
    @classmethod
    def _non_empty(cls, v: str, info: object) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v


class AzureConfig(BaseModel):
    """
    Credentials for Azure Blob Storage.

    Supports two auth strategies:
    - Shared-key   : AZURE_STORAGE_ACCOUNT + AZURE_STORAGE_KEY
    - Service-principal: AZURE_STORAGE_ACCOUNT + AZURE_CLIENT_ID +
                         AZURE_CLIENT_SECRET + AZURE_TENANT_ID
    """

    storage_account: str = Field(alias="AZURE_STORAGE_ACCOUNT")
    storage_key: Optional[str] = Field(default=None, alias="AZURE_STORAGE_KEY")
    client_id: Optional[str] = Field(default=None, alias="AZURE_CLIENT_ID")
    client_secret: Optional[str] = Field(default=None, alias="AZURE_CLIENT_SECRET")
    tenant_id: Optional[str] = Field(default=None, alias="AZURE_TENANT_ID")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _require_one_auth_strategy(self) -> "AzureConfig":
        has_shared_key = bool(self.storage_key)
        has_sp = all([self.client_id, self.client_secret, self.tenant_id])
        if not has_shared_key and not has_sp:
            raise ValueError(
                "Azure auth requires either AZURE_STORAGE_KEY (shared-key) "
                "or AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID "
                "(service-principal)."
            )
        return self


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """
    Fully validated application configuration.

    Instantiate via ``load_config()`` — do not build directly in production
    code; that bypasses the env-var sourcing logic.
    """

    mode: OperationMode = Field(alias="MODE")
    namespace: str = Field(alias="NAMESPACE")
    provider: StorageProvider = Field(alias="PROVIDER")
    bucket: str = Field(alias="BUCKET")

    backup_dir: Path = Field(default=Path("/tmp/clustersnap"), alias="BACKUP_DIR")
    remote_prefix: str = Field(default="clustersnap/backups", alias="REMOTE_PREFIX")
    log_level: LogLevel = Field(default=LogLevel.INFO, alias="LOG_LEVEL")

    # Kubernetes context to use with kubectl.
    # If None, kubectl uses the current active context (i.e. in-cluster config
    # when running as a CronJob, or ~/.kube/config current-context locally).
    kube_context: Optional[str] = Field(default=None, alias="KUBE_CONTEXT")

    # ── Import-specific ───────────────────────────────────────────────────────

    # Image used for disposable helper pods that mount each PVC during restore.
    helper_pod_image: str = Field(
        default="debian:bookworm-slim", alias="HELPER_POD_IMAGE"
    )
    # Seconds to wait after scaling all workloads to 0 before starting restore.
    scale_wait_seconds: int = Field(default=15, alias="SCALE_WAIT_SECONDS")
    # kubectl wait --timeout for helper pod to reach Ready state.
    helper_pod_timeout_seconds: int = Field(
        default=60, alias="HELPER_POD_TIMEOUT_SECONDS"
    )

    # ── Secret migration ──────────────────────────────────────────────────────

    # kubectl context of the SOURCE cluster to read secrets from.
    # If None, the current context is used for both source and target clusters.
    source_kube_context: Optional[str] = Field(
        default=None, alias="SOURCE_KUBE_CONTEXT"
    )
    # Set to True to skip the PostgreSQL secret migration step after import.
    skip_secret_migration: bool = Field(
        default=False, alias="SKIP_SECRET_MIGRATION"
    )

    pushgateway_url: str = Field(
        default="http://prometheus-pushgateway:9091",
        alias="PUSHGATEWAY_URL",
    )

    # Provider-specific sub-configs — populated by the model validator below
    s3: Optional[S3Config] = Field(default=None, exclude=True)
    azure: Optional[AzureConfig] = Field(default=None, exclude=True)

    model_config = {"populate_by_name": True}

    @field_validator("namespace", "bucket")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("remote_prefix")
    @classmethod
    def _strip_slashes(cls, v: str) -> str:
        return v.strip("/")

    @field_validator("backup_dir", mode="before")
    @classmethod
    def _coerce_path(cls, v: object) -> Path:
        return Path(str(v))

    @model_validator(mode="after")
    def _load_provider_config(self) -> "AppConfig":
        """Parse and attach provider-specific credentials from the environment."""
        env = os.environ
        errors: list[str] = []

        if self.provider is StorageProvider.S3:
            try:
                self.s3 = S3Config(
                    **{
                        "AWS_ACCESS_KEY_ID": env.get("AWS_ACCESS_KEY_ID", ""),
                        "AWS_SECRET_ACCESS_KEY": env.get("AWS_SECRET_ACCESS_KEY", ""),
                        "AWS_REGION": env.get("AWS_REGION", "us-east-1"),
                        "S3_ENDPOINT_URL": env.get("S3_ENDPOINT_URL"),
                    }
                )
            except _PydanticValidationError as exc:
                errors.append(f"S3 config error: {exc}")

        elif self.provider is StorageProvider.AZURE:
            try:
                self.azure = AzureConfig(
                    **{
                        "AZURE_STORAGE_ACCOUNT": env.get("AZURE_STORAGE_ACCOUNT", ""),
                        "AZURE_STORAGE_KEY": env.get("AZURE_STORAGE_KEY"),
                        "AZURE_CLIENT_ID": env.get("AZURE_CLIENT_ID"),
                        "AZURE_CLIENT_SECRET": env.get("AZURE_CLIENT_SECRET"),
                        "AZURE_TENANT_ID": env.get("AZURE_TENANT_ID"),
                    }
                )
            except _PydanticValidationError as exc:
                errors.append(f"Azure config error: {exc}")

        if errors:
            raise ValueError("\n".join(errors))

        return self


# ---------------------------------------------------------------------------
# Public error type
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """
    Raised by ``load_config()`` when one or more environment variables are
    missing, empty, or fail type/constraint validation.

    The message is always human-readable and safe to log — it will never
    contain raw secret values.
    """


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def load_config() -> AppConfig:
    """
    Read all required environment variables, validate them, and return a
    fully populated ``AppConfig`` instance.

    Raises
    ------
    ConfigError
        If any required variable is absent or any validation constraint fails.
        The exception message lists all problems at once so operators can fix
        them in a single deploy cycle rather than one at a time.

    Examples
    --------
    >>> import os
    >>> os.environ.update({
    ...     "MODE": "export", "NAMESPACE": "tenant-alpha",
    ...     "PROVIDER": "s3", "BUCKET": "my-backups",
    ...     "AWS_ACCESS_KEY_ID": "AKIA…", "AWS_SECRET_ACCESS_KEY": "…",
    ... })
    >>> cfg = load_config()
    >>> cfg.mode
    <OperationMode.EXPORT: 'export'>
    """
    env = os.environ
    raw: dict[str, object] = {
        "MODE": env.get("MODE", ""),
        "NAMESPACE": env.get("NAMESPACE", ""),
        "PROVIDER": env.get("PROVIDER", ""),
        "BUCKET": env.get("BUCKET", ""),
        "BACKUP_DIR": env.get("BACKUP_DIR", "/tmp/clustersnap"),
        "REMOTE_PREFIX": env.get("REMOTE_PREFIX", "clustersnap/backups"),
        "LOG_LEVEL": env.get("LOG_LEVEL", "INFO"),
        "PUSHGATEWAY_URL": env.get(
            "PUSHGATEWAY_URL", "http://prometheus-pushgateway:9091"
        ),
        "KUBE_CONTEXT": env.get("KUBE_CONTEXT"),
        "HELPER_POD_IMAGE": env.get("HELPER_POD_IMAGE", "debian:bookworm-slim"),
        "SCALE_WAIT_SECONDS": env.get("SCALE_WAIT_SECONDS", "15"),
        "HELPER_POD_TIMEOUT_SECONDS": env.get("HELPER_POD_TIMEOUT_SECONDS", "60"),
        "SOURCE_KUBE_CONTEXT": env.get("SOURCE_KUBE_CONTEXT"),
        "SKIP_SECRET_MIGRATION": env.get("SKIP_SECRET_MIGRATION", "false"),
    }

    try:
        return AppConfig(**raw)  # type: ignore[arg-type]
    except _PydanticValidationError as exc:
        # Flatten all pydantic error details into a single readable message
        problems = "; ".join(
            f"{' -> '.join(str(l) for l in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise ConfigError(f"Invalid configuration — {problems}") from exc
