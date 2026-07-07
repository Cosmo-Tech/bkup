"""
backup/kubectl.py
-----------------
Shared kubectl subprocess wrapper used by both the Exporter and Importer.

Extracted as a standalone module so both engines share the same base
primitives (context detection, _run, list_pvcs) without circular imports.
"""

from __future__ import annotations

import subprocess

from telemetry.logger import get_logger

log = get_logger(__name__)


class KubectlClient:
    """
    Thin subprocess wrapper around ``kubectl``.

    Prepends ``--context <ctx>`` to every invocation when a context is
    explicitly configured; otherwise uses the current active context.

    Parameters
    ----------
    namespace:
        Kubernetes namespace to operate in.
    context:
        Optional kubectl context name (``KUBE_CONTEXT`` env var).
        ``None`` → auto-detected via ``kubectl config current-context``.
    """

    def __init__(self, namespace: str, context: str | None = None) -> None:
        self._namespace = namespace
        self._context = context
        self._resolved_context: str = context or self._detect_current_context()

    # ------------------------------------------------------------------
    # Context resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_current_context() -> str:
        """
        Return the name of the currently active kubectl context.

        Falls back to ``"<unknown>"`` when kubeconfig has no current context,
        and to ``"<kubectl-not-found>"`` when kubectl is not on PATH.
        """
        try:
            result = subprocess.run(
                ["kubectl", "config", "current-context"],
                capture_output=True,
                text=True,
            )
            name = result.stdout.strip()
            return name if name else "<unknown>"
        except FileNotFoundError:
            return "<kubectl-not-found>"

    @property
    def resolved_context(self) -> str:
        """Effective kubectl context name — always a real string."""
        return self._resolved_context

    @property
    def namespace(self) -> str:
        return self._namespace

    # ------------------------------------------------------------------
    # Core runner
    # ------------------------------------------------------------------

    def _run(self, *args: str) -> str:
        """
        Run a kubectl command and return stdout as a stripped string.

        Raises
        ------
        RuntimeError
            If kubectl exits with a non-zero status.
        """
        cmd: list[str] = ["kubectl"]
        if self._context:
            cmd += ["--context", self._context]
        cmd += list(args)

        log.debug("kubectl", extra={"cmd": " ".join(cmd)})

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else "(no stderr)"
            raise RuntimeError(
                f"kubectl exited {result.returncode}: {stderr}\n"
                f"Command: {' '.join(cmd)}"
            )
        return result.stdout.strip()

    # ------------------------------------------------------------------
    # Shared operations
    # ------------------------------------------------------------------

    def list_pvcs(self) -> list[str]:
        """
        Return the names of all PVCs in the namespace.

        Mirrors::

            kubectl -n $namespace get pvc \\
              -o jsonpath='{.items[*].metadata.name}'
        """
        raw = self._run(
            "-n", self._namespace,
            "get", "pvc",
            "-o", "jsonpath={.items[*].metadata.name}",
        )
        pvcs = raw.split() if raw else []
        log.info(
            "PVCs discovered",
            extra={"namespace": self._namespace, "pvcs": pvcs},
        )
        return pvcs
