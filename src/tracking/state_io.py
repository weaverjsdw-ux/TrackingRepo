"""Small helpers for local JSON state files."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def write_json_atomic(path: str | Path, data: dict[str, Any]) -> None:
    """Write JSON through a sibling temp file, then atomically replace target."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
