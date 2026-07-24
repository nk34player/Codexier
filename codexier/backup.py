from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.codexier-{stamp}.bak")
    shutil.copy2(path, destination)
    return destination


def atomic_write(path: Path, content: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_mode = path.stat().st_mode & 0o777 if path.exists() else mode
    temporary = path.with_name(f".{path.name}.codexier.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if current_mode is not None:
            temporary.chmod(current_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
