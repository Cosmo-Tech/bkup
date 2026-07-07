"""
backup/exporter.py
------------------
Export (backup) engine for ClusterSnap.

Faithful Python port of ``kubernetes-backups/export.sh``.

------------------------------------------------------
For every PVC in the target namespace the engine:

1. Finds the first pod that mounts the PVC
   (jsonpath: items[?(@.spec.volumes[*].persistentVolumeClaim.claimName=='<pvc>')])
2. Skips the PVC if no pod is found.
3. Checks the pod is Ready skips with a warning if not.
4. Resolves three pieces of metadata from kubectl (exact same jsonpath
   expressions used in ``export.sh`` lines 53-55):
   - ``container`` - first container name in the pod spec
   - ``volume``    - volume name whose PVC claim matches the PVC
   - ``mount``     - mountPath of that volume inside the container
5. Streams a tar archive out of the live container via ``kubectl exec tar``
   and captures it in memory.
6. Packs all per-PVC ``.tar`` streams into a single timestamped ZIP archive,
   preserving the ``<pvc>.tar`` naming convention from the legacy script.
7. Uploads the ZIP to the configured cloud storage provider.
8. Cleans up the local staging directory unconditionally.

Environment variables consumed by this module
---------------------------------------------
KUBE_CONTEXT   (optional) kubectl context name. Uses the current context when
               not set - which is the in-cluster service-account context when
               running as a Kubernetes CronJob.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from telemetry.logger import get_logger
from telemetry.metrics import observe_duration, record_failure, record_success

if TYPE_CHECKING:
    from config import AppConfig
    from storage.base import BaseStorageProvider

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class PvcMountInfo:
    """All metadata needed to stream a tar out of a live pod."""

    pvc: str
    pod: str
    container: str
    volume: str
    mount_path: str


# ---------------------------------------------------------------------------
# kubectl helper
# ---------------------------------------------------------------------------


class KubectlClient:
    """
    Thin subprocess wrapper around ``kubectl``.

    Mirrors the ``kubectl_with_context`` shell function from ``export.sh``:
    every invocation prepends ``--context <ctx>`` when a context is configured.

    Parameters
    ----------
    namespace:
        Kubernetes namespace to operate in.
    context:
        Optional kubectl context name (``KUBE_CONTEXT`` env var).
        ``None`` uses the current active context.
    """

    def __init__(self, namespace: str, context: str | None = None) -> None:
        self._namespace = namespace
        self._context = context
        # Resolve the effective context name once at construction time so every
        # log line shows the real context name instead of a placeholder.
        self._resolved_context: str = context or self._detect_current_context()

    @staticmethod
    def _detect_current_context() -> str:
        """
        Return the name of the currently active kubectl context.

        Runs ``kubectl config current-context``.  Falls back to the string
        ``"<unknown>"`` if kubectl is not available or the kubeconfig has no
        current context set (e.g. bare in-cluster pod with no kubeconfig).
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
        """The effective kubectl context name (always a real string)."""
        return self._resolved_context

    # ------------------------------------------------------------------
    # Internal runner
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
    # Public API
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

    def find_pod_for_pvc(self, pvc: str) -> str | None:
        """
        Return the name of the first pod that mounts ``pvc``, or ``None``.

        Mirrors:

            kubectl -n $namespace get pods -o jsonpath=\\
              "{.items[?(@.spec.volumes[*].persistentVolumeClaim
                .claimName=='$pvc')].metadata.name}" | awk '{print $1}'
        """
        raw = self._run(
            "-n", self._namespace,
            "get", "pods",
            "-o",
            f"jsonpath={{.items[?(@.spec.volumes[*]"
            f".persistentVolumeClaim.claimName=='{pvc}')].metadata.name}}",
        )
        names = raw.split()
        pod = names[0] if names else None
        if pod:
            log.debug("Pod found for PVC", extra={"pvc": pvc, "pod": pod})
        else:
            log.warning("No pod mounts PVC skipping", extra={"pvc": pvc})
        return pod

    def is_pod_ready(self, pod: str) -> bool:
        """
        Return ``True`` if the pod's Ready condition is ``"True"``.

        Mirrors::

            kubectl -n $namespace get pod $pod \\
              -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
        """
        status = self._run(
            "-n", self._namespace,
            "get", "pod", pod,
            "-o",
            'jsonpath={.status.conditions[?(@.type=="Ready")].status}',
        )
        ready = status == "True"
        log.debug(
            "Pod readiness",
            extra={"pod": pod, "ready": ready},
        )
        return ready

    def resolve_pvc_mount(self, pvc: str, pod: str) -> PvcMountInfo:
        """
        Resolve container name, volume name, and mount path for ``pvc``
        inside ``pod``.

        Mirrors the three jsonpath calls in ``export.sh`` lines 53-55::

            container=$(kubectl … get pod $pod
                -o jsonpath="{.spec.containers[0].name}")

            volume=$(kubectl … get pod $pod
                -o jsonpath="{.spec.volumes
                  [?(@.persistentVolumeClaim.claimName=='$pvc')].name}")

            mount=$(kubectl … get pod $pod
                -o jsonpath="{.spec.containers[?(@.name=='$container')]
                  .volumeMounts[?(@.name=='$volume')].mountPath}")
        """
        container = self._run(
            "-n", self._namespace,
            "get", "pod", pod,
            "-o", "jsonpath={.spec.containers[0].name}",
        )

        volume = self._run(
            "-n", self._namespace,
            "get", "pod", pod,
            "-o",
            f"jsonpath={{.spec.volumes"
            f"[?(@.persistentVolumeClaim.claimName=='{pvc}')].name}}",
        )

        mount_path = self._run(
            "-n", self._namespace,
            "get", "pod", pod,
            "-o",
            f"jsonpath={{.spec.containers[?(@.name=='{container}')]"
            f".volumeMounts[?(@.name=='{volume}')].mountPath}}",
        )

        info = PvcMountInfo(
            pvc=pvc,
            pod=pod,
            container=container,
            volume=volume,
            mount_path=mount_path,
        )

        # Mirrors: echo "found data at $pod/$container/$volume$mount"
        log.info(
            "Found data at pod/container/volume/mountPath",
            extra={
                "location": f"{pod}/{container}/{volume}{mount_path}",
                "pvc": pvc,
            },
        )
        return info

    def stream_tar(self, info: PvcMountInfo, dest_path: Path) -> Path:
        """
        Stream a tar archive of the PVC mount path directly to ``dest_path``.

        Mirrors::

            kubectl -n $namespace exec $pod -c $container -- \\
              tar cf - -C $mount . \\
              --checkpoint=100 --checkpoint-action=dot --totals \\
              > "$backup_file"

        The tar stdout is written directly to disk (never buffered in RAM),
        which makes this safe for arbitrarily large PVCs.

        WebSocket close 1006 handling
        ------------------------------
        ``kubectl exec`` uses a WebSocket connection.  When ``tar`` exits
        normally the WebSocket sometimes emits a close-1006 / unexpected-EOF
        message on the *error* channel even though the data channel completed
        cleanly.  This is a well-known kubectl artifact (not a tar error).
        We detect this pattern and treat it as a non-fatal warning, provided
        that tar actually wrote data to disk.

        Parameters
        ----------
        info:
            PVC mount metadata returned by ``resolve_pvc_mount()``.
        dest_path:
            Destination ``.tar`` file on the local filesystem.  Parent
            directory must already exist.

        Returns
        -------
        Path
            ``dest_path`` after a successful write.

        Raises
        ------
        RuntimeError
            If kubectl exits non-zero for a reason other than the known
            WebSocket noise, or if no data was written to ``dest_path``.
        """
        cmd: list[str] = ["kubectl"]
        if self._context:
            cmd += ["--context", self._context]

        cmd += [
            "-n", self._namespace,
            "exec", info.pod,
            "-c", info.container,
            "--",
            "tar", "cf", "-",
            "-C", info.mount_path, ".",
            "--checkpoint=100",
            "--checkpoint-action=dot",
            "--totals",
        ]

        log.info(
            "Streaming tar from pod",
            extra={
                "pvc": info.pvc,
                "pod": info.pod,
                "container": info.container,
                "mount_path": info.mount_path,
                "dest": str(dest_path),
            },
        )

        # Stream stdout directly to file - never buffer the full tar in RAM
        with dest_path.open("wb") as fh:
            result = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)

        stderr_text = result.stderr.decode(errors="replace").strip()

        if result.returncode != 0:
            # ── Known false-positive: kubectl WebSocket close 1006 ──────────
            # tar completes successfully but kubectl exits non-zero because
            # the WebSocket error-stream closes with an abnormal-closure frame.
            # We accept this ONLY when tar actually wrote data to disk.
            _WS_NOISE = "websocket: close 1006"
            dest_size = dest_path.stat().st_size if dest_path.exists() else 0

            # Strip the noisy websocket lines and check if anything real remains
            real_errors = "\n".join(
                line for line in stderr_text.splitlines()
                if _WS_NOISE not in line
                # kubectl prefixes ws-noise lines with "E<date>" - filter those too
                and not (line.startswith("E0") and _WS_NOISE in line)
            ).strip()

            if not real_errors and dest_size > 0:
                log.warning(
                    "kubectl exec exited non-zero due to WebSocket close 1006 "
                    "noise tar data appears complete; treating as success",
                    extra={
                        "pvc": info.pvc,
                        "pod": info.pod,
                        "tar_size_bytes": dest_size,
                        "kubectl_stderr": stderr_text,
                    },
                )
            else:
                # Real failure - remove the partial file before raising
                dest_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"kubectl exec tar failed for PVC '{info.pvc}' "
                    f"(pod={info.pod}, container={info.container}, "
                    f"mount={info.mount_path}): {stderr_text}"
                )

        size_bytes = dest_path.stat().st_size
        log.info(
            "Tar stream completed",
            extra={"pvc": info.pvc, "tar_size_bytes": size_bytes, "dest": str(dest_path)},
        )
        return dest_path


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class Exporter:
    """
    Orchestrates the full export (backup) workflow for one namespace.

    Parameters
    ----------
    config:
        Fully validated ``AppConfig`` instance.
    storage:
        Concrete ``BaseStorageProvider`` (S3 or Azure).
    """

    def __init__(
        self,
        config: "AppConfig",
        storage: "BaseStorageProvider",
    ) -> None:
        self._cfg = config
        self._storage = storage
        self._kubectl = KubectlClient(
            namespace=config.namespace,
            context=config.kube_context,
        )

    @property
    def resolved_context(self) -> str:
        """The effective kubectl context name resolved at startup."""
        return self._kubectl.resolved_context

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Full export workflow.

        1. List all PVCs in the namespace via ``kubectl get pvc``.
        2. For each PVC: find its pod, verify readiness, resolve mount path.
        3. Stream a ``tar`` per PVC via ``kubectl exec tar``.
        4. Pack all tar streams into a single timestamped ZIP archive
           (entries: ``<pvc>.tar``).
        5. Upload the ZIP to cloud storage.
        6. Record Prometheus metrics.
        7. Clean up local staging area unconditionally.

        Raises
        ------
        RuntimeError
            On unrecoverable failure (no PVCs, upload error, etc.).
        """
        namespace = self._cfg.namespace
        staging_dir = self._cfg.backup_dir / namespace
        archive_path: Path | None = None
        start_time = time.monotonic()

        log.info(
            "Export workflow started",
            extra={
                "namespace": namespace,
                "kube_context": self._kubectl.resolved_context,
            },
        )

        try:
            staging_dir.mkdir(parents=True, exist_ok=True)

            # ── 1. Discover PVCs ───────────────────────────────────────
            pvcs = self._kubectl.list_pvcs()
            if not pvcs:
                raise RuntimeError(
                    f"No PVCs found in namespace '{namespace}'."
                )

            # ── 2-3. Per-PVC backup ────────────────────────────────────
            backed_up: list[tuple[str, Path]] = []

            for pvc in pvcs:
                tar_path = self._backup_pvc(pvc, staging_dir)
                if tar_path is not None:
                    backed_up.append((pvc, tar_path))

            if not backed_up:
                raise RuntimeError(
                    f"All PVCs in namespace '{namespace}' were skipped "
                    "(no ready pods found)."
                )

            # ── 4. Pack ZIP ────────────────────────────────────────────
            archive_path = self._create_archive(
                pvc_tars=backed_up,
                staging_dir=staging_dir,
                namespace=namespace,
            )

            # ── 5. Upload ──────────────────────────────────────────────
            remote_key = self._build_remote_key(archive_path.name)
            self._upload(archive_path, remote_key)

            # ── 6. Metrics ─────────────────────────────────────────────
            duration = time.monotonic() - start_time
            size_bytes = archive_path.stat().st_size

            observe_duration(namespace=namespace, duration_seconds=duration)
            record_success(namespace=namespace, size_bytes=size_bytes)

            log.info(
                "Export workflow completed successfully",
                extra={
                    "namespace": namespace,
                    "pvcs_backed_up": [pvc for pvc, _ in backed_up],
                    "archive": str(archive_path),
                    "remote_key": remote_key,
                    "size_bytes": size_bytes,
                    "duration_seconds": round(duration, 3),
                },
            )

        except Exception as exc:
            duration = time.monotonic() - start_time
            observe_duration(namespace=namespace, duration_seconds=duration)
            record_failure(namespace=namespace)
            log.error(
                "Export workflow failed",
                extra={
                    "namespace": namespace,
                    "error": str(exc),
                    "duration_seconds": round(duration, 3),
                },
            )
            raise RuntimeError(
                f"Export failed for namespace={namespace!r}: {exc}"
            ) from exc

        finally:
            self._cleanup_staging(staging_dir)

    # ------------------------------------------------------------------
    # Per-PVC backup
    # ------------------------------------------------------------------

    def _backup_pvc(self, pvc: str, staging_dir: Path) -> Path | None:
        """
        Back up a single PVC to a ``.tar`` file inside ``staging_dir``.

        Returns the path to the written ``.tar`` file on success, or ``None``
        when the PVC should be skipped - mirroring the ``continue`` statements
        in ``export.sh``.
        """
        log.info("Exporting PVC", extra={"pvc": pvc, "namespace": self._cfg.namespace})

        pod = self._kubectl.find_pod_for_pvc(pvc)
        if pod is None:
            return None

        if not self._kubectl.is_pod_ready(pod):
            log.error(
                "Pod is not Ready skipping PVC",
                extra={"pvc": pvc, "pod": pod},
            )
            return None

        try:
            info = self._kubectl.resolve_pvc_mount(pvc, pod)
        except RuntimeError as exc:
            log.error(
                "Failed to resolve PVC mount info skipping",
                extra={"pvc": pvc, "pod": pod, "error": str(exc)},
            )
            return None

        dest_path = staging_dir / f"{pvc}.tar"
        try:
            return self._kubectl.stream_tar(info, dest_path)
        except RuntimeError as exc:
            log.error(
                "Failed to stream tar from pod skipping PVC",
                extra={"pvc": pvc, "pod": pod, "error": str(exc)},
            )
            return None

    # ------------------------------------------------------------------
    # Archive creation
    # ------------------------------------------------------------------

    @staticmethod
    def _create_archive(
        pvc_tars: list[tuple[str, Path]],
        staging_dir: Path,
        namespace: str,
    ) -> Path:
        """
        Pack all per-PVC ``.tar`` files into a single timestamped ZIP.

        Each entry is named ``<pvc>.tar``, preserving the same per-PVC
        naming convention as the legacy ``export.sh`` (``$backup_dir/$pvc.tar``).

        Files are read from disk via ``zf.write()`` - no tar data is buffered
        in RAM, keeping memory usage constant regardless of PVC size.
        """
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        archive_name = f"{namespace}-{timestamp}.zip"
        archive_path = staging_dir / archive_name

        log.info(
            "Zip archive creation started",
            extra={
                "archive": str(archive_path),
                "pvc_count": len(pvc_tars),
            },
        )

        try:
            with zipfile.ZipFile(
                archive_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as zf:
                for pvc_name, tar_file in pvc_tars:
                    entry = f"{pvc_name}.tar"
                    # arcname keeps the ZIP entry flat: just "<pvc>.tar"
                    zf.write(tar_file, arcname=entry)
                    log.debug(
                        "PVC tar written to ZIP",
                        extra={
                            "entry": entry,
                            "tar_size_bytes": tar_file.stat().st_size,
                        },
                    )
        except OSError as exc:
            raise RuntimeError(
                f"Failed to create ZIP archive at {archive_path}: {exc}"
            ) from exc

        size_bytes = archive_path.stat().st_size
        log.info(
            "Zip archive creation completed",
            extra={"archive": str(archive_path), "size_bytes": size_bytes},
        )
        return archive_path

    # ------------------------------------------------------------------
    # Upload & cleanup
    # ------------------------------------------------------------------

    def _build_remote_key(self, archive_filename: str) -> str:
        return (
            f"{self._cfg.remote_prefix}"
            f"/{self._cfg.namespace}"
            f"/{archive_filename}"
        )

    def _upload(self, archive_path: Path, remote_key: str) -> None:
        log.info(
            "Upload started",
            extra={
                "archive": str(archive_path),
                "remote_key": remote_key,
                "provider": type(self._storage).__name__,
            },
        )
        self._storage.upload_file(archive_path, remote_key)
        log.info("Upload succeeded", extra={"remote_key": remote_key})

    @staticmethod
    def _cleanup_staging(staging_dir: Path) -> None:
        """Remove the entire local staging directory (non-fatal on error)."""
        try:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
                log.debug(
                    "Staging directory cleaned up",
                    extra={"staging_dir": str(staging_dir)},
                )
        except OSError as exc:
            log.warning(
                "Failed to clean up staging directory",
                extra={"staging_dir": str(staging_dir), "error": str(exc)},
            )
