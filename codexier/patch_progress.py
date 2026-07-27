"""Shared, human-readable progress events for desktop patch adapters."""

from __future__ import annotations

from collections.abc import Callable


PatchProgress = Callable[[int, str], None]

MILESTONES: dict[str, int] = {
    "detection": 5,
    "validation": 12,
    "process stop": 20,
    "backup": 28,
    "extraction": 36,
    "bundle matching": 45,
    "source patch": 55,
    "repack": 65,
    "atomic replacement": 75,
    "verification": 85,
    "restart": 93,
    "completion": 100,
}


def report(progress: PatchProgress | None, stage: str, detail: str) -> None:
    """Emit a stable milestone when a caller requested patch progress."""
    if progress is not None:
        progress(MILESTONES[stage], f"{stage}: {detail}")
