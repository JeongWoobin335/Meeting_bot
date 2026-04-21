from __future__ import annotations

import os
from pathlib import Path
import shutil
import time
from typing import MutableMapping


WORKSPACE_ROOT_ENV = "ZOOM_MEETING_BOT_HOME"
TEMP_ROOT_ENV = "ZOOM_MEETING_BOT_TEMP_HOME"
TEMP_RETENTION_HOURS_ENV = "ZOOM_MEETING_BOT_TEMP_RETENTION_HOURS"
DEFAULT_TEMP_RETENTION_HOURS = 24


def resolve_workspace_root() -> Path:
    configured = os.getenv(WORKSPACE_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd().resolve()


def app_temp_root(workspace_dir: str | Path | None = None) -> Path:
    configured = os.getenv(TEMP_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(workspace_dir).expanduser().resolve() if workspace_dir is not None else resolve_workspace_root()
    return (base / ".tmp" / "zoom-meeting-bot" / "system-temp").resolve()


def apply_temp_env(
    target: MutableMapping[str, str] | None = None,
    *,
    workspace_dir: str | Path | None = None,
) -> Path:
    temp_root = app_temp_root(workspace_dir)
    temp_root.mkdir(parents=True, exist_ok=True)
    mapping = os.environ if target is None else target
    temp_text = str(temp_root)
    mapping[TEMP_ROOT_ENV] = temp_text
    mapping["TEMP"] = temp_text
    mapping["TMP"] = temp_text
    mapping["TMPDIR"] = temp_text
    return temp_root


def cleanup_stale_app_temp(
    *,
    workspace_dir: str | Path | None = None,
    retention_hours: int | None = None,
) -> dict[str, int]:
    temp_root = app_temp_root(workspace_dir)
    if not temp_root.exists():
        return {"removed_entries": 0}

    hours = retention_hours if retention_hours is not None else _retention_hours()
    if hours <= 0:
        return {"removed_entries": 0}

    cutoff = time.time() - (hours * 3600)
    removed_entries = 0
    for child in list(temp_root.iterdir()):
        try:
            modified_at = child.stat().st_mtime
        except OSError:
            continue
        if modified_at >= cutoff:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed_entries += 1
        except OSError:
            continue
    return {"removed_entries": removed_entries}


def _retention_hours() -> int:
    raw = os.getenv(TEMP_RETENTION_HOURS_ENV, "").strip()
    if not raw:
        return DEFAULT_TEMP_RETENTION_HOURS
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_TEMP_RETENTION_HOURS
