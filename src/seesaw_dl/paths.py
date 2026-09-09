"""Preparing the download target.

The output directory is usually a network share or an external volume, so "it is not
there" and "it is there but read-only" are ordinary, expected states rather than bugs.
Both are turned into a single actionable sentence here, and checked *before* a run does
any network work -- discovering an unmounted share only after fetching the whole feed
wastes the user's time, and letting the ``OSError`` escape buries the reason under a
traceback.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ConfigError

MOUNT_HINT = "If it is a network share or external drive, check that it is mounted."


def ensure_writable_dir(root: Path) -> Path:
    """Create *root* if needed, confirm we can write into it, and return it.

    Raises :class:`ConfigError`, which every command already reports as one line.
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
    except FileNotFoundError as exc:
        raise ConfigError(_unavailable(root, f"{_blame(exc, root)} does not exist")) from exc
    except PermissionError as exc:
        raise ConfigError(
            _unavailable(root, f"permission denied at {_blame(exc, root)}")
        ) from exc
    except NotADirectoryError as exc:
        raise ConfigError(_unavailable(root, f"{_blame(exc, root)} is not a directory")) from exc
    except OSError as exc:
        raise ConfigError(_unavailable(root, exc.strerror or str(exc))) from exc

    if not root.is_dir():
        raise ConfigError(_unavailable(root, "it is not a directory"))
    if not os.access(root, os.W_OK | os.X_OK):
        raise ConfigError(_unavailable(root, "it is not writable"))
    return root


def _blame(exc: OSError, root: Path) -> str:
    """The path the OS actually complained about, which is often an ancestor of *root*."""
    return str(exc.filename) if exc.filename else str(root)


def _unavailable(root: Path, reason: str) -> str:
    return f"Output directory {root} is unavailable: {reason}. {MOUNT_HINT}"


def is_readable_dir(root: Path) -> bool:
    """Whether *root* can be looked into -- for reporting presence, which never writes."""
    try:
        return root.is_dir() and os.access(root, os.R_OK | os.X_OK)
    except OSError:
        return False
