"""Structured JSON logging with mandatory PII redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.security.redaction import PIIRedactingFilter


class JsonFormatter(logging.Formatter):
    _STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        from app.observability.tracing import current_trace_id

        doc = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if tid := current_trace_id():
            doc["trace_id"] = tid
        for k, v in record.__dict__.items():
            if k not in self._STD and not k.startswith("_"):
                doc[k] = v
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(PIIRedactingFilter())
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "elastic_transport", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
