# """
# telemetry/logger.py
# -------------------
# Structured JSON logger for ClusterSnap.

# Every log record emitted by this module is a single-line JSON object that
# Grafana Loki (or any JSON-aware log aggregator) can parse without a custom
# pipeline stage.

# Typical output line
# -------------------
# {"timestamp": "2026-05-17T12:00:00.123456Z", "level": "INFO",
#  "logger": "clustersnap", "message": "Zip archive created",
#  "extra": {"archive": "/tmp/backup.zip", "size_bytes": 104857600}}

# Usage
# -----
#     from telemetry.logger import get_logger

#     log = get_logger(__name__)
#     log.info("Upload started", extra={"remote_path": "backups/foo.zip"})
#     log.error("Upload failed", extra={"error": str(exc)})
# """

# from __future__ import annotations

# import json
# import logging
# import sys
# from datetime import datetime, timezone
# from typing import Any


# # ---------------------------------------------------------------------------
# # JSON formatter
# # ---------------------------------------------------------------------------

# class _JsonFormatter(logging.Formatter):
#     """
#     Renders a ``LogRecord`` as a compact, single-line JSON object understood
#     by Grafana Loki's default JSON parser.

#     Fields always present
#     ---------------------
#     timestamp   ISO-8601 UTC timestamp with microsecond precision and a
#                 trailing "Z" (not "+00:00") so Loki can parse it out of the
#                 box with the ``time`` label.
#     level       Upper-case log level string (DEBUG / INFO / WARNING / ERROR /
#                 CRITICAL).
#     logger      The ``name`` attribute of the originating logger — typically
#                 the Python module name passed to ``logging.getLogger()``.
#     message     The rendered log message string.

#     Optional fields
#     ---------------
#     extra       A dict of arbitrary key/value pairs attached by callers via
#                 ``logging.info("…", extra={…})``.  Keys that conflict with
#                 standard ``LogRecord`` attributes are silently dropped.
#     exc_info    String representation of an exception traceback when the log
#                 call was made inside an ``except`` block with
#                 ``exc_info=True``.
#     """

#     # LogRecord attributes we never want to bubble up into "extra"
#     _RESERVED: frozenset[str] = frozenset(
#         {
#             "name", "msg", "args", "levelname", "levelno", "pathname",
#             "filename", "module", "exc_info", "exc_text", "stack_info",
#             "lineno", "funcName", "created", "msecs", "relativeCreated",
#             "thread", "threadName", "processName", "process", "message",
#             "taskName",
#         }
#     )

#     def format(self, record: logging.LogRecord) -> str:  # noqa: A003
#         # Ensure msg % args interpolation has happened
#         record.message = record.getMessage()

#         payload: dict[str, Any] = {
#             "timestamp": datetime.fromtimestamp(
#                 record.created, tz=timezone.utc
#             ).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
#             "level": record.levelname,
#             "logger": record.name,
#             "message": record.message,
#         }

#         # Collect caller-supplied extra fields
#         caller_extra: dict[str, Any] = {
#             k: v
#             for k, v in record.__dict__.items()
#             if k not in self._RESERVED and not k.startswith("_")
#         }
#         if caller_extra:
#             payload["extra"] = caller_extra

#         # Attach traceback if present
#         if record.exc_info:
#             payload["exc_info"] = self.formatException(record.exc_info)

#         return json.dumps(payload, default=str, ensure_ascii=False)


# # ---------------------------------------------------------------------------
# # Public factory
# # ---------------------------------------------------------------------------

# _ROOT_LOGGER_NAME = "clustersnap"
# _configured: bool = False


# def _configure_root_logger(level: int = logging.INFO) -> None:
#     """
#     Idempotent setup of the ``clustersnap`` root logger.

#     Called automatically by ``get_logger()``.  Safe to call multiple times —
#     handlers are only added once.
#     """
#     global _configured  # noqa: PLW0603

#     root = logging.getLogger(_ROOT_LOGGER_NAME)

#     if _configured or root.handlers:
#         # Already set up; just honour a potential level change.
#         root.setLevel(level)
#         _configured = True
#         return

#     root.setLevel(level)
#     root.propagate = False  # do not bubble up to the Python root logger

#     handler = logging.StreamHandler(sys.stdout)
#     handler.setLevel(level)
#     handler.setFormatter(_JsonFormatter())
#     root.addHandler(handler)

#     _configured = True


# def get_logger(name: str | None = None, level: int = logging.INFO) -> logging.Logger:
#     """
#     Return a logger that emits structured JSON records to *stdout*.

#     All loggers returned by this factory are children of the ``clustersnap``
#     hierarchy, so a single handler on the root logger handles everything.

#     Parameters
#     ----------
#     name:
#         Typically ``__name__`` of the calling module.  If ``None`` or empty
#         the root ``clustersnap`` logger itself is returned.
#     level:
#         Minimum severity level for the *root* logger.  Individual child
#         loggers always inherit from the root unless explicitly overridden.

#     Returns
#     -------
#     logging.Logger
#         A fully configured logger ready to emit JSON lines.

#     Examples
#     --------
#     >>> log = get_logger(__name__)
#     >>> log.info("Backup started", extra={"namespace": "tenant-alpha"})
#     >>> log.error("Upload failed", extra={"error": "connection timeout"})
#     """
#     _configure_root_logger(level=level)

#     if not name:
#         return logging.getLogger(_ROOT_LOGGER_NAME)

#     # Ensure the child name lives under the clustersnap hierarchy
#     qualified = (
#         name if name.startswith(_ROOT_LOGGER_NAME) else f"{_ROOT_LOGGER_NAME}.{name}"
#     )
#     return logging.getLogger(qualified)
