"""Filesystem write boundary. Shared by the pre-hook and by every tool that writes.

Kept out of src/agent/ so tools can import it without a cycle through the dispatcher.
"""

from __future__ import annotations

from pathlib import Path

from src.config import PROTECTED_WRITE_PREFIXES


class ProtectedPathError(PermissionError):
    """Raised when something tries to write inside a read-only tree."""


def is_protected(path: str | Path) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    for prefix in PROTECTED_WRITE_PREFIXES:
        try:
            resolved.relative_to(prefix.resolve())
            return True
        except ValueError:
            continue
    return False


def guard_write_path(path: str | Path, *, actor: str = "tool") -> Path:
    """Return the path, or raise. Never asks — the boundary is not negotiable."""
    if is_protected(path):
        raise ProtectedPathError(
            f"{actor} attempted a write under a read-only tree: {path}. "
            "evals/** and observability/manifest/** are read-only to the agent."
        )
    return Path(path)
