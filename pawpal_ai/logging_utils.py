"""JSON-lines logging for advisor decisions."""

import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "pawpal_ai.log"


def log_event(event: str, log_path: Path | None = None, **fields) -> None:
    """Append one JSON record. Logging failures are swallowed by the caller."""
    path = log_path or DEFAULT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
