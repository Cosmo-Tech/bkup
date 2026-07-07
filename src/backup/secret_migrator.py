"""
backup/secret_migrator.py
--------------------------
Python port of ``kubernetes-backups/migrate_secrets-postgresql.sh``.

Reads PostgreSQL credentials from the **source** cluster and applies the
correct passwords in the **target** cluster by executing ``ALTER USER``
statements directly inside the ``postgresql-0`` pod.

Services handled
----------------
+-----------------+---------------------------+------------------------------------------+
| Service         | Kubernetes Secret         | Keys read                                |
+=================+===========================+==========================================+
| PostgreSQL root | postgresql-config         | postgres-password                        |
+-----------------+---------------------------+------------------------------------------+
| SeaweedFS       | postgresql-seaweedfs      | postgresql-database, postgresql-username,|
|                 |                           | postgresql-password                      |
+-----------------+---------------------------+------------------------------------------+
| Argo            | postgresql-argo           | database-name, database-username,        |
|                 |                           | database-password                        |
+-----------------+---------------------------+------------------------------------------+
| CosmoTech API   | postgresql-cosmotechapi   | database-name, admin-username/password,  |
|                 |                           | writer-username/password,                |
|                 |                           | reader-username/password                 |
+-----------------+---------------------------+------------------------------------------+

Usage
-----
Instantiate with the validated ``AppConfig`` and call ``run()``.
``SKIP_SECRET_MIGRATION=true`` disables the entire step non-fatally.

Required env vars (import mode only)
-------------------------------------
SOURCE_KUBE_CONTEXT   kubectl context of the source cluster to read secrets from.
                      If omitted, the current context is used for both clusters
                      (useful when migrating within the same cluster).
KUBE_CONTEXT          kubectl context of the target cluster (defaults to current).
NAMESPACE             Kubernetes namespace (same on both clusters).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backup.kubectl import KubectlClient
from telemetry.logger import get_logger

if TYPE_CHECKING:
    from config import AppConfig

log = get_logger(__name__)

_MOUNT = "/mnt/pg"  # mount point inside postgresql-0 used by psql commands


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class _UserPasswordPair:
    database: str
    username: str
    new_password: str


# ---------------------------------------------------------------------------
# Secret reader
# ---------------------------------------------------------------------------


class _SecretReader:
    """
    Reads Kubernetes secret values from a specific cluster context.

    Mirrors the ``get_tenant_secret_value`` shell function from
    ``migrate_secrets-postgresql.sh``.
    """

    def __init__(self, namespace: str, context: str | None = None) -> None:
        self._namespace = namespace
        self._context = context

    def get(self, secret_name: str, key: str) -> str | None:
        """
        Decode and return the base64-encoded value of ``key`` inside
        ``secret_name``.

        Mirrors::

            kubectl --context=$context -n $namespace get secret $secret_name \\
              -o jsonpath="{.data.$key}" | base64 --decode

        Returns ``None`` if the secret or key does not exist (matches the
        silent-skip behaviour of the bash script).
        """
        cmd: list[str] = ["kubectl"]
        if self._context:
            cmd += ["--context", self._context]

        cmd += [
            "-n", self._namespace,
            "get", "secret", secret_name,
            "-o", f"jsonpath={{.data.{key}}}",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0 or not result.stdout.strip():
                log.warning(
                    "Secret key not found",
                    extra={
                        "secret": secret_name,
                        "key": key,
                        "namespace": self._namespace,
                    },
                )
                return None

            # Decode the base64 value
            import base64
            decoded = base64.b64decode(result.stdout.strip()).decode()
            return decoded if decoded else None

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Failed to read secret",
                extra={"secret": secret_name, "key": key, "error": str(exc)},
            )
            return None


# ---------------------------------------------------------------------------
# Password changer
# ---------------------------------------------------------------------------


class _PasswordChanger:
    """
    Executes ``ALTER USER … WITH PASSWORD`` statements inside the
    ``postgresql-0`` pod of the target cluster.

    Mirrors the ``psql_change_user_password`` shell function.
    """

    def __init__(
        self,
        namespace: str,
        postgres_root_password: str,
        context: str | None = None,
    ) -> None:
        self._namespace = namespace
        self._postgres_root_password = postgres_root_password
        self._context = context

    def change(self, database: str, username: str, new_password: str) -> None:
        """
        Execute::

            kubectl --context=$target -n $namespace exec postgresql-0 \\
              -c postgresql -- bash -c "
                export PGPASSWORD=$postgres_password
                psql -h localhost -U postgres -d $database \\
                  -c \"ALTER USER $username WITH PASSWORD '$new_password';\""

        Raises
        ------
        RuntimeError
            If the kubectl exec command fails.
        """
        cmd: list[str] = ["kubectl"]
        if self._context:
            cmd += ["--context", self._context]

        sql = f"ALTER USER {username} WITH PASSWORD '{new_password}';"
        bash_script = (
            f"export PGPASSWORD={self._postgres_root_password}; "
            f"psql -h localhost -U postgres -d {database} -c \"{sql}\""
        )

        cmd += [
            "-n", self._namespace,
            "exec", "postgresql-0",
            "-c", "postgresql",
            "--",
            "bash", "-c", bash_script,
        ]

        log.info(
            "Changing PostgreSQL user password",
            extra={
                "namespace": self._namespace,
                "database": database,
                "username": username,
            },
        )

        result = subprocess.run(cmd, capture_output=True, text=True)
        stderr = result.stderr.strip()

        if result.returncode != 0:
            raise RuntimeError(
                f"ALTER USER failed for {username}@{database}: {stderr}"
            )

        log.info(
            "Password changed successfully",
            extra={"database": database, "username": username},
        )


# ---------------------------------------------------------------------------
# Public migrator
# ---------------------------------------------------------------------------


class SecretMigrator:
    """
    Orchestrates the full secret migration workflow.

    Reads credentials from the source cluster and applies them in the target
    cluster's PostgreSQL instance.

    Parameters
    ----------
    config:
        Fully validated ``AppConfig`` instance.  Uses:
        - ``namespace``            — same on source and target
        - ``kube_context``         — target cluster context
        - ``source_kube_context``  — source cluster context (optional)
        - ``skip_secret_migration``— short-circuit flag
    """

    def __init__(self, config: "AppConfig") -> None:
        self._cfg = config

    def run(self) -> None:
        """
        Execute the full migration workflow.

        Steps:
        1. Read the PostgreSQL root password from the SOURCE cluster.
        2. Read per-service credentials (db name, usernames, passwords)
           from the SOURCE cluster.
        3. For each service, call ``ALTER USER`` in the TARGET cluster's
           postgresql-0 pod.

        Non-fatal per service: a failure on one service is logged and
        skipped so the other services are still migrated.
        """
        if self._cfg.skip_secret_migration:
            log.info(
                "Secret migration skipped (SKIP_SECRET_MIGRATION=true)",
                extra={"namespace": self._cfg.namespace},
            )
            return

        namespace = self._cfg.namespace
        source_ctx = self._cfg.source_kube_context  # may be None → current
        target_ctx = self._cfg.kube_context          # may be None → current

        source = _SecretReader(namespace, context=source_ctx)
        log.info(
            "Secret migration started",
            extra={
                "namespace": namespace,
                "source_context": source_ctx or "<current-context>",
                "target_context": target_ctx or "<current-context>",
            },
        )

        # ── 1. PostgreSQL root password ────────────────────────────────
        source_pg_root_pw = source.get("postgresql-config", "postgres-password")
        target_pg_root_pw = source.get("postgresql-config", "postgres-password")

        if not source_pg_root_pw or not target_pg_root_pw:
            log.error(
                "Cannot read PostgreSQL root password — aborting migration",
                extra={"namespace": namespace},
            )
            return

        log.debug(
            "PostgreSQL root password read",
            extra={"namespace": namespace},
        )

        changer = _PasswordChanger(
            namespace=namespace,
            postgres_root_password=target_pg_root_pw,
            context=target_ctx,
        )

        # Collect all (database, username, new_password) operations
        pairs: list[_UserPasswordPair] = []
        errors: list[str] = []

        # ── 2a. PostgreSQL root itself ─────────────────────────────────
        pairs.append(
            _UserPasswordPair(
                database="postgres",
                username="postgres",
                new_password=target_pg_root_pw,
            )
        )

        # ── 2b. SeaweedFS ──────────────────────────────────────────────
        swfs_db  = source.get("postgresql-seaweedfs", "postgresql-database")
        swfs_usr = source.get("postgresql-seaweedfs", "postgresql-username")
        swfs_pw  = source.get("postgresql-seaweedfs", "postgresql-password")
        if swfs_db and swfs_usr and swfs_pw:
            pairs.append(_UserPasswordPair(swfs_db, swfs_usr, swfs_pw))
        else:
            log.warning("Skipping SeaweedFS — incomplete secret", extra={"namespace": namespace})

        # ── 2c. Argo ───────────────────────────────────────────────────
        argo_db  = source.get("postgresql-argo", "database-name")
        argo_usr = source.get("postgresql-argo", "database-username")
        argo_pw  = source.get("postgresql-argo", "database-password")
        if argo_db and argo_usr and argo_pw:
            pairs.append(_UserPasswordPair(argo_db, argo_usr, argo_pw))
        else:
            log.warning("Skipping Argo — incomplete secret", extra={"namespace": namespace})

        # ── 2d. CosmoTech API ──────────────────────────────────────────
        ct_db         = source.get("postgresql-cosmotechapi", "database-name")
        ct_usr_admin  = source.get("postgresql-cosmotechapi", "admin-username")
        ct_usr_writer = source.get("postgresql-cosmotechapi", "writer-username")
        ct_usr_reader = source.get("postgresql-cosmotechapi", "reader-username")
        ct_pw_admin   = source.get("postgresql-cosmotechapi", "admin-password")
        ct_pw_writer  = source.get("postgresql-cosmotechapi", "writer-password")
        ct_pw_reader  = source.get("postgresql-cosmotechapi", "reader-password")

        if ct_db:
            for usr, pw, role in [
                (ct_usr_admin,  ct_pw_admin,  "admin"),
                (ct_usr_writer, ct_pw_writer, "writer"),
                (ct_usr_reader, ct_pw_reader, "reader"),
            ]:
                if usr and pw:
                    pairs.append(_UserPasswordPair(ct_db, usr, pw))
                else:
                    log.warning(
                        f"Skipping CosmoTech API {role} — incomplete secret",
                        extra={"namespace": namespace},
                    )
        else:
            log.warning("Skipping CosmoTech API — database name not found", extra={"namespace": namespace})

        # ── 3. Execute ALTER USER for each pair ────────────────────────
        success = 0
        for pair in pairs:
            try:
                changer.change(pair.database, pair.username, pair.new_password)
                success += 1
            except RuntimeError as exc:
                errors.append(str(exc))
                log.error(
                    "Failed to change password",
                    extra={
                        "database": pair.database,
                        "username": pair.username,
                        "error": str(exc),
                    },
                )

        if errors:
            log.warning(
                "Secret migration completed with errors",
                extra={
                    "namespace": namespace,
                    "success": success,
                    "failures": len(errors),
                },
            )
        else:
            log.info(
                "Secret migration completed successfully",
                extra={"namespace": namespace, "passwords_changed": success},
            )
