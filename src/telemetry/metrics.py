# """
# telemetry/metrics.py
# --------------------
# Prometheus metrics registry and Pushgateway integration for ClusterSnap.

# Metrics exposed
# ---------------
# backup_success_total        Counter   — incremented once per successful backup
# backup_duration_seconds     Histogram — full end-to-end wall-clock duration
# backup_size_bytes           Gauge     — compressed archive size in bytes
# last_backup_timestamp       Gauge     — Unix epoch of the last successful backup

# All metrics carry the label ``namespace`` so dashboards can aggregate across
# multiple Kubernetes namespaces backed up by the same Pushgateway.

# Usage
# -----
#     from telemetry.metrics import record_success, record_failure, push_metrics

#     with backup_duration_seconds.labels(namespace=ns).time():
#         run_backup()

#     record_success(namespace="tenant-alpha", size_bytes=104_857_600)
#     push_metrics(job="clustersnap", namespace="tenant-alpha")
# """

# from __future__ import annotations

# import os
# import time
# from typing import Final

# from prometheus_client import (
#     CollectorRegistry,
#     Counter,
#     Gauge,
#     Histogram,
#     push_to_gateway,
#     REGISTRY,
# )

# from telemetry.logger import get_logger

# log = get_logger(__name__)

# # ---------------------------------------------------------------------------
# # Metric label names
# # ---------------------------------------------------------------------------

# _LABELS: Final[list[str]] = ["namespace"]

# # ---------------------------------------------------------------------------
# # Registry
# #
# # We use the global REGISTRY so that any future instrumentation added in other
# # modules (e.g. storage providers) is included in the same push.  A custom
# # registry could be used instead for stricter isolation in tests — callers may
# # pass their own registry to `push_metrics()`.
# # ---------------------------------------------------------------------------

# # ---------------------------------------------------------------------------
# # Metric definitions
# # ---------------------------------------------------------------------------

# backup_success_total: Counter = Counter(
#     name="backup_success_total",
#     documentation=(
#         "Total number of backup runs that completed successfully. "
#         "Incremented once at the end of a successful export workflow."
#     ),
#     labelnames=_LABELS,
#     registry=REGISTRY,
# )

# backup_failure_total: Counter = Counter(
#     name="backup_failure_total",
#     documentation=(
#         "Total number of backup runs that ended in a failure. "
#         "Incremented once when an unrecoverable error is caught in main.py."
#     ),
#     labelnames=_LABELS,
#     registry=REGISTRY,
# )

# backup_duration_seconds: Histogram = Histogram(
#     name="backup_duration_seconds",
#     documentation=(
#         "End-to-end wall-clock duration of the full backup workflow in seconds, "
#         "covering zip creation + cloud upload + local cleanup."
#     ),
#     labelnames=_LABELS,
#     # Buckets cover 1 s → ~34 min; adjust to your expected archive sizes.
#     buckets=(1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600),
#     registry=REGISTRY,
# )

# backup_size_bytes: Gauge = Gauge(
#     name="backup_size_bytes",
#     documentation=(
#         "Size in bytes of the ZIP archive produced during the last successful "
#         "backup run."
#     ),
#     labelnames=_LABELS,
#     registry=REGISTRY,
# )

# last_backup_timestamp: Gauge = Gauge(
#     name="last_backup_timestamp",
#     documentation=(
#         "Unix epoch timestamp (UTC) of the last successful backup completion. "
#         "Use `time() - last_backup_timestamp` in an alert rule to detect stale backups."
#     ),
#     labelnames=_LABELS,
#     registry=REGISTRY,
# )

# # ---------------------------------------------------------------------------
# # Convenience helpers
# # ---------------------------------------------------------------------------


# def record_success(*, namespace: str, size_bytes: int) -> None:
#     """
#     Record a successful backup run in all relevant metrics.

#     Parameters
#     ----------
#     namespace:
#         Kubernetes namespace that was backed up.  Used as the ``namespace``
#         Prometheus label value.
#     size_bytes:
#         Size of the produced ZIP archive in bytes.

#     Notes
#     -----
#     Call this **after** the upload has been confirmed successful and **before**
#     calling ``push_metrics()``.  The duration must be tracked separately via
#     ``backup_duration_seconds.labels(namespace=…).time()`` or
#     ``observe_duration()``.
#     """
#     backup_success_total.labels(namespace=namespace).inc()
#     backup_size_bytes.labels(namespace=namespace).set(size_bytes)
#     last_backup_timestamp.labels(namespace=namespace).set(time.time())

#     log.info(
#         "Backup metrics recorded (success)",
#         extra={"namespace": namespace, "size_bytes": size_bytes},
#     )


# def record_failure(*, namespace: str) -> None:
#     """
#     Record a failed backup run.

#     Parameters
#     ----------
#     namespace:
#         Kubernetes namespace for which the backup failed.
#     """
#     backup_failure_total.labels(namespace=namespace).inc()
#     log.warning(
#         "Backup metrics recorded (failure)",
#         extra={"namespace": namespace},
#     )


# def observe_duration(*, namespace: str, duration_seconds: float) -> None:
#     """
#     Explicitly observe a backup duration value.

#     Use this instead of the context-manager form when you need to record
#     duration after the fact (e.g. when wrapping a try/finally block that
#     also handles failures).

#     Parameters
#     ----------
#     namespace:
#         Kubernetes namespace label value.
#     duration_seconds:
#         Elapsed wall-clock time in seconds.
#     """
#     backup_duration_seconds.labels(namespace=namespace).observe(duration_seconds)
#     log.debug(
#         "Duration observed",
#         extra={"namespace": namespace, "duration_seconds": round(duration_seconds, 3)},
#     )


# def push_metrics(
#     *,
#     job: str = "clustersnap",
#     namespace: str,
#     registry: CollectorRegistry = REGISTRY,
#     pushgateway_url: str | None = None,
# ) -> None:
#     """
#     Push all collected metrics to the Prometheus Pushgateway.

#     The Pushgateway URL is resolved in priority order:

#     1. ``pushgateway_url`` argument (explicit override, useful in tests).
#     2. ``PUSHGATEWAY_URL`` environment variable.
#     3. Fallback default ``http://prometheus-pushgateway:9091``.

#     Parameters
#     ----------
#     job:
#         Pushgateway job label.  Defaults to ``"clustersnap"``.
#     namespace:
#         Kubernetes namespace — added as a Pushgateway *grouping key* so
#         metrics from different namespaces do not overwrite each other.
#     registry:
#         Prometheus collector registry to push.  Defaults to the global
#         ``REGISTRY``.  Pass a custom registry in unit tests.
#     pushgateway_url:
#         Optional explicit Pushgateway base URL (scheme + host + port).

#     Raises
#     ------
#     RuntimeError
#         Wrapped around any network / HTTP error raised by
#         ``prometheus_client.push_to_gateway``, so callers only need to
#         catch one exception type.
#     """
#     url: str = (
#         pushgateway_url
#         or os.environ.get("PUSHGATEWAY_URL", "http://prometheus-pushgateway:9091")
#     )

#     grouping_key: dict[str, str] = {"namespace": namespace}

#     log.info(
#         "Pushing metrics to Pushgateway",
#         extra={"url": url, "job": job, "grouping_key": grouping_key},
#     )

#     try:
#         push_to_gateway(
#             gateway=url,
#             job=job,
#             registry=registry,
#             grouping_key=grouping_key,
#         )
#         log.info(
#             "Metrics pushed successfully",
#             extra={"url": url, "job": job},
#         )
#     except Exception as exc:  # noqa: BLE001
#         log.error(
#             "Failed to push metrics to Pushgateway",
#             extra={"url": url, "error": str(exc)},
#         )
#         raise RuntimeError(
#             f"Pushgateway push failed (url={url!r}, job={job!r}): {exc}"
#         ) from exc
