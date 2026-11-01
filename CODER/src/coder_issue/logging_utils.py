"""Small logging foundation that avoids leaking record payloads by default."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging


class JsonFormatter(logging.Formatter):
    """Serialize stable operational fields as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configure process logging with an explicit, validated format."""

    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"invalid logging level: {level}")
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    elif log_format == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        raise ValueError(f"invalid logging format: {log_format}")
    logging.basicConfig(level=numeric_level, handlers=[handler], force=True)
