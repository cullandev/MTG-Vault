"""Structured JSON logging.

One line of JSON per event on stdout and in ``${DATA_DIR}/logs/app.log``. Request,
job and external-call events all share the same shape so they can be grepped and
aggregated without a log parser (ARCHITECTURE.md section 10).
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a compact JSON line."""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Install the JSON formatter on the root logger.

    Args:
        level: Logging level name, e.g. ``"INFO"``.
        log_dir: Directory for the rotating ``app.log`` file. Stdout only when ``None``.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            log_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        rotating.setFormatter(formatter)
        root.addHandler(rotating)

    # uvicorn's own access log duplicates our request middleware; silence it.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
