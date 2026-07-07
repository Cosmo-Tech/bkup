# """
# backup/importer.py
# ------------------
# Import (restore) engine for ClusterSnap.

# Faithful Python port of ``kubernetes-backups/import.sh``.

# Workflow (mirrors the bash script exactly)
# ------------------------------------------
# 1.  Download the ZIP archive identified by ``remote_key`` from cloud storage.
# 2.  Extract all per-PVC ``.tar`` files from the ZIP into a local staging
#     directory (``BACKUP_DIR/<namespace>/``).
# 3.  **Scale down** all StatefulSets and Deployments in the namespace to 0
#     replicas so no workload holds a lock on any PVC.
# 4.  Wait ``SCALE_WAIT_SECONDS`` (default 15) for pods to terminate.
# 5.  For every PVC in the namespace:
#     a. Extract a ``service_keyword`` from the PVC name using the same regex
#        as the bash script:
#        ``postgresql|redis-master|redis-replica|seaweedfs-master|seaweedfs-volume``
#     b. Find the matching ``*<keyword>*.tar`` file in the staging directory.
#     c. Launch a disposable helper pod (``debian:bookworm-slim``) that mounts
#        the PVC at ``/mnt/data``.  Mirrors the ``kubectl run … --overrides``
#        pattern with ``runAsUser: 0``.
#     d. Wait for the helper pod to reach ``Ready`` (timeout:
#        ``HELPER_POD_TIMEOUT_SECONDS``, default 60 s).
#     e. Wipe the mount point:  ``rm -rf /mnt/data/* /mnt/data/.[!.]*``
#     f. Stream the tar archive *into* the pod:
#        ``cat backup.tar | kubectl exec -i helper -- tar xf - -C /mnt/data``
#        (identical to the bash ``cat | kubectl exec -i`` pipeline).
#     g. Delete the helper pod immediately (``--now``).
# 6.  **Scale back up** all StatefulSets and Deployments to 1 replica.
# 7.  Clean up the local staging directory unconditionally.

# Environment variables consumed
# -------------------------------
# KUBE_CONTEXT              Target cluster context (optional - default: current).
# HELPER_POD_IMAGE          Image used for helper pods (default: debian:bookworm-slim).
# SCALE_WAIT_SECONDS        Seconds to wait after scale-down (default: 15).
# HELPER_POD_TIMEOUT_SECONDS  kubectl wait timeout for helper pod Ready (default: 60).
# """

# from __future__ import annotations

# import json
# import re
# import secrets
# import shutil
# import subprocess
# import time
# import zipfile
# from pathlib import Path
# from typing import TYPE_CHECKING

# from backup.kubectl import KubectlClient
# from telemetry.logger import get_logger

# if TYPE_CHECKING:
#     from config import AppConfig
#     from storage.base import BaseStorageProvider

# log = get_logger(__name__)

# # Regex that extracts the service keyword from a PVC name.
# # Mirrors: sed -E 's/.*(postgresql|redis-master|…).*/\1/'
# _SERVICE_KEYWORD_RE = re.compile(
#     r"(postgresql|redis-master|redis-replica|seaweedfs-master|seaweedfs-volume)"
# )


# class _ImportKubectlClient(KubectlClient):
#     """
#     KubectlClient extended with import-specific operations.

#     All methods mirror their exact bash equivalents from ``import.sh``.
#     """

#     # ------------------------------------------------------------------
#     # Scale operations
#     # ------------------------------------------------------------------

#     def scale_all(self, replicas: int) -> None:
#         """
#         Scale every StatefulSet and Deployment in the namespace.

#         Mirrors::

#             kubectl -n $namespace scale statefulset --all --replicas=$replicas
#             kubectl -n $namespace scale deployment  --all --replicas=$replicas
#         """
#         for kind in ("statefulset", "deployment"):
#             try:
#                 self._run(
#                     "-n", self._namespace,
#                     "scale", kind, "--all",
#                     f"--replicas={replicas}",
#                 )
#                 log.info(
#                     f"Scaled {kind}s",
#                     extra={
#                         "namespace": self._namespace,
#                         "kind": kind,
#                         "replicas": replicas,
#                     },
#                 )
#             except RuntimeError as exc:
#                 # Non-fatal: namespace may have no StatefulSets or Deployments
#                 log.warning(
#                     f"Scale {kind} returned an error (may be empty) - continuing",
#                     extra={"namespace": self._namespace, "error": str(exc)},
#                 )

#     # ------------------------------------------------------------------
#     # Helper pod lifecycle
#     # ------------------------------------------------------------------

#     def launch_helper_pod(self, pod_name: str, pvc: str, image: str) -> None:
#         """
#         Launch a disposable helper pod that mounts ``pvc`` at ``/mnt/data``.

#         Mirrors the ``kubectl run … --overrides`` call in ``import.sh``::

#             kubectl run $helper_pod --image=debian:bookworm-slim --overrides='{
#               "spec": {
#                 "containers": [{
#                   "name": "helper",
#                   "image": "...",
#                   "command": ["sleep","3600"],
#                   "volumeMounts": [{"name":"data","mountPath":"/mnt/data"}],
#                   "securityContext": {"runAsUser": 0}
#                 }],
#                 "volumes": [{"name":"data",
#                              "persistentVolumeClaim":{"claimName":"<pvc>"}}]
#               }
#             }'
#         """
#         overrides = json.dumps({
#             "spec": {
#                 "containers": [{
#                     "name": "helper",
#                     "image": image,
#                     "command": ["sleep", "3600"],
#                     "volumeMounts": [{"name": "data", "mountPath": "/mnt/data"}],
#                     "securityContext": {"runAsUser": 0},
#                 }],
#                 "volumes": [{
#                     "name": "data",
#                     "persistentVolumeClaim": {"claimName": pvc},
#                 }],
#             }
#         })

#         cmd: list[str] = ["kubectl"]
#         if self._context:
#             cmd += ["--context", self._context]
#         cmd += [
#             "-n", self._namespace,
#             "run", pod_name,
#             f"--image={image}",
#             f"--overrides={overrides}",
#         ]

#         log.info(
#             "Launching helper pod",
#             extra={"pod": pod_name, "pvc": pvc, "image": image},
#         )

#         result = subprocess.run(cmd, capture_output=True, text=True)
#         if result.returncode != 0:
#             raise RuntimeError(
#                 f"Failed to launch helper pod {pod_name!r}: {result.stderr.strip()}"
#             )

#     def wait_pod_ready(self, pod_name: str, timeout_seconds: int = 60) -> None:
#         """
#         Block until the helper pod reaches ``Ready``.

#         Mirrors::

#             kubectl wait --for=condition=Ready pod/$helper_pod --timeout=60s
#         """
#         log.info(
#             "Waiting for helper pod to be Ready",
#             extra={"pod": pod_name, "timeout_seconds": timeout_seconds},
#         )
#         self._run(
#             "-n", self._namespace,
#             "wait",
#             "--for=condition=Ready",
#             f"pod/{pod_name}",
#             f"--timeout={timeout_seconds}s",
#         )
#         log.info("Helper pod is Ready", extra={"pod": pod_name})

#     def clean_mount(self, pod_name: str, mount: str = "/mnt/data") -> None:
#         """
#         Wipe all files (including hidden ones) from the PVC mount point.

#         Mirrors::

#             kubectl exec $helper_pod -- sh -c
#               "rm -rf /mnt/data/* /mnt/data/.[!.]* 2>/dev/null || true"
#         """
#         cmd: list[str] = ["kubectl"]
#         if self._context:
#             cmd += ["--context", self._context]
#         cmd += [
#             "-n", self._namespace,
#             "exec", pod_name,
#             "--",
#             "sh", "-c",
#             f"rm -rf {mount}/* {mount}/.[!.]* 2>/dev/null || true",
#         ]
#         subprocess.run(cmd, capture_output=True)  # best-effort, never raise
#         log.debug("Mount point cleaned", extra={"pod": pod_name, "mount": mount})

#     def stream_tar_into_pod(
#         self,
#         pod_name: str,
#         tar_path: Path,
#         mount: str = "/mnt/data",
#     ) -> None:
#         """
#         Stream a local tar archive into the helper pod's mount point.

#         Mirrors the ``cat | kubectl exec -i`` pipeline::

#             cat "$backup_file" | kubectl exec -i $helper_pod -- \\
#               tar xf - --no-same-permissions --no-overwrite-dir \\
#               -C $mount --checkpoint=100 --checkpoint-action=dot --totals

#         Parameters
#         ----------
#         pod_name:
#             Name of the running helper pod.
#         tar_path:
#             Local ``.tar`` file to stream.
#         mount:
#             Destination path inside the container (default: ``/mnt/data``).

#         Raises
#         ------
#         RuntimeError
#             If tar / kubectl exits non-zero (WebSocket 1006 noise filtered
#             using the same logic as the Exporter).
#         """
#         cmd: list[str] = ["kubectl"]
#         if self._context:
#             cmd += ["--context", self._context]
#         cmd += [
#             "-n", self._namespace,
#             "exec", "-i", pod_name,
#             "--",
#             "tar", "xf", "-",
#             "--no-same-permissions",
#             "--no-overwrite-dir",
#             "-C", mount,
#             "--checkpoint=100",
#             "--checkpoint-action=dot",
#             "--totals",
#         ]

#         log.info(
#             "Streaming tar into pod",
#             extra={
#                 "pod": pod_name,
#                 "tar": str(tar_path),
#                 "mount": mount,
#                 "tar_size_bytes": tar_path.stat().st_size,
#             },
#         )

#         with tar_path.open("rb") as fh:
#             result = subprocess.run(cmd, stdin=fh, capture_output=True)

#         stderr_text = result.stderr.decode(errors="replace").strip()

#         if result.returncode != 0:
#             _WS_NOISE = "websocket: close 1006"
#             real_errors = "\n".join(
#                 line for line in stderr_text.splitlines()
#                 if _WS_NOISE not in line
#                 and not (line.startswith("E0") and _WS_NOISE in line)
#             ).strip()

#             if not real_errors:
#                 log.warning(
#                     "kubectl exec exited non-zero due to WebSocket 1006 noise "
#                     "- tar appears complete; treating as success",
#                     extra={"pod": pod_name, "stderr": stderr_text},
#                 )
#             else:
#                 raise RuntimeError(
#                     f"tar restore failed for pod {pod_name!r} "
#                     f"mount={mount!r}: {stderr_text}"
#                 )

#         log.info(
#             "Tar restore into pod completed",
#             extra={"pod": pod_name, "mount": mount},
#         )

#     def delete_pod(self, pod_name: str) -> None:
#         """
#         Force-delete the helper pod immediately.

#         Mirrors::

#             kubectl delete pod $helper_pod --now
#         """
#         cmd: list[str] = ["kubectl"]
#         if self._context:
#             cmd += ["--context", self._context]
#         cmd += [
#             "-n", self._namespace,
#             "delete", "pod", pod_name,
#             "--now",
#             "--ignore-not-found=true",
#         ]
#         subprocess.run(cmd, capture_output=True)  # best-effort
#         log.debug("Helper pod deleted", extra={"pod": pod_name})


# # ---------------------------------------------------------------------------
# # Importer
# # ---------------------------------------------------------------------------


# class Importer:
#     """
#     Orchestrates the full import (restore) workflow for one namespace.

#     Parameters
#     ----------
#     config:
#         Fully validated ``AppConfig`` instance.
#     storage:
#         Concrete ``BaseStorageProvider`` (S3 or Azure).
#     """

#     def __init__(
#         self,
#         config: "AppConfig",
#         storage: "BaseStorageProvider",
#     ) -> None:
#         self._cfg = config
#         self._storage = storage
#         self._kubectl = _ImportKubectlClient(
#             namespace=config.namespace,
#             context=config.kube_context,
#         )

#     @property
#     def resolved_context(self) -> str:
#         """Effective kubectl context name resolved at startup."""
#         return self._kubectl.resolved_context

#     # ------------------------------------------------------------------
#     # Public entry point
#     # ------------------------------------------------------------------

#     def run(self, remote_key: str) -> None:
#         """
#         Execute the full import workflow.

#         Parameters
#         ----------
#         remote_key:
#             Full remote object key of the ZIP archive to restore, e.g.
#             ``"clustersnap/backups/tenant-sphinx/tenant-sphinx-2026-05-17T14-08-44Z.zip"``.

#         Raises
#         ------
#         FileNotFoundError
#             If ``remote_key`` does not exist in the bucket.
#         RuntimeError
#             For download errors, corrupt archives, or pod/tar failures.
#         """
#         namespace = self._cfg.namespace
#         staging_dir = self._cfg.backup_dir / namespace

#         log.info(
#             "Import workflow started",
#             extra={
#                 "namespace": namespace,
#                 "remote_key": remote_key,
#                 "kube_context": self._kubectl.resolved_context,
#             },
#         )

#         try:
#             staging_dir.mkdir(parents=True, exist_ok=True)

#             # ── 1. Download ZIP ────────────────────────────────────────
#             local_zip = self._download(remote_key, staging_dir)

#             # ── 2. Extract per-PVC tar files from the ZIP ──────────────
#             tar_files = self._extract_zip(local_zip, staging_dir)
#             log.info(
#                 "ZIP extracted",
#                 extra={
#                     "namespace": namespace,
#                     "tar_files": [str(f) for f in tar_files],
#                 },
#             )

#             # ── 3. Scale down all workloads ────────────────────────────
#             log.info(
#                 "Stopping pods to release PVC locks",
#                 extra={"namespace": namespace},
#             )
#             self._kubectl.scale_all(replicas=0)

#             wait = self._cfg.scale_wait_seconds
#             log.info(
#                 f"Waiting {wait}s for pods to terminate",
#                 extra={"namespace": namespace, "wait_seconds": wait},
#             )
#             time.sleep(wait)

#             # ── 4. Restore each PVC ────────────────────────────────────
#             pvcs = self._kubectl.list_pvcs()
#             restored: list[str] = []
#             skipped: list[str] = []

#             for pvc in pvcs:
#                 result = self._restore_pvc(pvc, tar_files)
#                 if result:
#                     restored.append(pvc)
#                 else:
#                     skipped.append(pvc)

#             # ── 5. Scale back up ───────────────────────────────────────
#             log.info(
#                 "Restarting pods",
#                 extra={"namespace": namespace},
#             )
#             self._kubectl.scale_all(replicas=1)

#             log.info(
#                 "Import workflow completed successfully",
#                 extra={
#                     "namespace": namespace,
#                     "pvcs_restored": restored,
#                     "pvcs_skipped": skipped,
#                 },
#             )

#         except Exception as exc:
#             # Attempt to scale back up even on failure so the cluster is not
#             # left with all workloads at 0 replicas
#             log.error(
#                 "Import workflow failed - attempting scale-up recovery",
#                 extra={"namespace": namespace, "error": str(exc)},
#             )
#             self._kubectl.scale_all(replicas=1)
#             raise RuntimeError(
#                 f"Import failed for namespace={namespace!r}: {exc}"
#             ) from exc

#         finally:
#             self._cleanup_staging(staging_dir)

#     # ------------------------------------------------------------------
#     # Step helpers
#     # ------------------------------------------------------------------

#     def _download(self, remote_key: str, staging_dir: Path) -> Path:
#         """Download the ZIP archive from cloud storage to staging_dir."""
#         local_zip = staging_dir / Path(remote_key).name
#         log.info(
#             "Download started",
#             extra={
#                 "remote_key": remote_key,
#                 "local_path": str(local_zip),
#                 "provider": type(self._storage).__name__,
#             },
#         )
#         self._storage.download_file(remote_key, local_zip)
#         size_bytes = local_zip.stat().st_size
#         log.info(
#             "Download succeeded",
#             extra={"local_path": str(local_zip), "size_bytes": size_bytes},
#         )
#         return local_zip

#     @staticmethod
#     def _extract_zip(local_zip: Path, staging_dir: Path) -> list[Path]:
#         """
#         Extract all ``.tar`` entries from the ZIP into ``staging_dir``.

#         Validates the ZIP integrity (CRC check) before extraction.
#         Rejects path-traversal members.

#         Returns
#         -------
#         list[Path]
#             List of extracted ``.tar`` file paths.
#         """
#         if not zipfile.is_zipfile(local_zip):
#             raise RuntimeError(
#                 f"Downloaded file is not a valid ZIP archive: {local_zip}"
#             )

#         with zipfile.ZipFile(local_zip, "r") as zf:
#             bad = zf.testzip()
#             if bad is not None:
#                 raise RuntimeError(
#                     f"ZIP archive is corrupt - first bad file: {bad!r}"
#                 )

#             tar_files: list[Path] = []
#             for member in zf.infolist():
#                 target = (staging_dir / member.filename).resolve()
#                 if not str(target).startswith(str(staging_dir.resolve())):
#                     log.warning(
#                         "Skipping unsafe ZIP member",
#                         extra={"member": member.filename},
#                     )
#                     continue
#                 zf.extract(member, path=staging_dir)
#                 extracted = staging_dir / member.filename
#                 if extracted.suffix == ".tar":
#                     tar_files.append(extracted)

#         return tar_files

#     def _restore_pvc(self, pvc: str, tar_files: list[Path]) -> bool:
#         """
#         Restore a single PVC using a disposable helper pod.

#         Returns ``True`` on success, ``False`` when the PVC is skipped
#         (no matching tar file).  Mirrors the per-PVC loop in ``import.sh``.
#         """
#         log.info("Restoring PVC", extra={"pvc": pvc, "namespace": self._cfg.namespace})

#         # ── a. Extract service keyword ─────────────────────────────────
#         m = _SERVICE_KEYWORD_RE.search(pvc)
#         if not m:
#             log.warning(
#                 "PVC name does not match any known service keyword - skipping",
#                 extra={"pvc": pvc},
#             )
#             return False
#         keyword = m.group(1)

#         # ── b. Find matching tar file ──────────────────────────────────
#         # Mirrors: ls "$backup_dir"/*"$service_keyword"*.tar | head -n 1
#         matches = [f for f in tar_files if keyword in f.name]
#         if not matches:
#             log.warning(
#                 "No matching tar file found for PVC - skipping",
#                 extra={"pvc": pvc, "keyword": keyword},
#             )
#             return False
#         tar_path = matches[0]
#         log.info(
#             "Matched tar file",
#             extra={"pvc": pvc, "tar": str(tar_path)},
#         )

#         # ── c. Launch helper pod ───────────────────────────────────────
#         suffix = secrets.token_hex(3)  # 5-char equivalent of head /dev/urandom
#         pod_name = f"backup-importer-{suffix}"

#         try:
#             self._kubectl.launch_helper_pod(
#                 pod_name=pod_name,
#                 pvc=pvc,
#                 image=self._cfg.helper_pod_image,
#             )

#             # ── d. Wait for Ready ──────────────────────────────────────
#             self._kubectl.wait_pod_ready(
#                 pod_name=pod_name,
#                 timeout_seconds=self._cfg.helper_pod_timeout_seconds,
#             )

#             # ── e. Clean mount point ───────────────────────────────────
#             self._kubectl.clean_mount(pod_name)

#             # ── f. Stream tar into pod ─────────────────────────────────
#             self._kubectl.stream_tar_into_pod(pod_name, tar_path)

#         except RuntimeError as exc:
#             log.error(
#                 "PVC restore failed",
#                 extra={"pvc": pvc, "pod": pod_name, "error": str(exc)},
#             )
#             return False

#         finally:
#             # ── g. Always delete the helper pod ───────────────────────
#             self._kubectl.delete_pod(pod_name)

#         log.info("PVC restored successfully", extra={"pvc": pvc})
#         return True

#     @staticmethod
#     def _cleanup_staging(staging_dir: Path) -> None:
#         """Remove the local staging directory (non-fatal on error)."""
#         try:
#             if staging_dir.exists():
#                 shutil.rmtree(staging_dir)
#                 log.debug(
#                     "Staging directory cleaned up",
#                     extra={"staging_dir": str(staging_dir)},
#                 )
#         except OSError as exc:
#             log.warning(
#                 "Failed to clean up staging directory",
#                 extra={"staging_dir": str(staging_dir), "error": str(exc)},
#             )
