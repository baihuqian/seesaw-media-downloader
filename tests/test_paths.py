"""The output directory is often an unmounted share; failing about it must be legible."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from seesaw_dl.errors import ConfigError
from seesaw_dl.paths import ensure_writable_dir, is_readable_dir


def test_missing_parents_are_created(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    assert ensure_writable_dir(target) == target
    assert target.is_dir()


def test_an_existing_directory_is_accepted(tmp_path: Path) -> None:
    assert ensure_writable_dir(tmp_path) == tmp_path


def test_a_file_in_the_way_is_reported(tmp_path: Path) -> None:
    blocker = tmp_path / "share"
    blocker.write_text("not a directory")
    with pytest.raises(ConfigError) as exc:
        ensure_writable_dir(blocker / "media")
    assert "is not a directory" in str(exc.value)
    assert str(blocker) in str(exc.value)


def test_an_unwritable_parent_names_the_path_the_os_blamed(tmp_path: Path) -> None:
    """The real report: /Volumes existed, the share under it could not be created."""
    locked = tmp_path / "Volumes"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(ConfigError) as exc:
            ensure_writable_dir(locked / "family" / "Media")
        message = str(exc.value)
        assert "permission denied" in message
        assert str(locked / "family") in message
        assert "mounted" in message  # the hint that makes it actionable
    finally:
        locked.chmod(0o700)


def test_an_unwritable_target_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "read-only"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(ConfigError, match="not writable"):
            ensure_writable_dir(target)
    finally:
        target.chmod(0o700)


def test_readability_check_is_a_boolean_not_a_raise(tmp_path: Path) -> None:
    assert is_readable_dir(tmp_path)
    assert not is_readable_dir(tmp_path / "missing")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert not is_readable_dir(blocker)

    unreadable = tmp_path / "closed"
    unreadable.mkdir()
    unreadable.chmod(0o000)
    try:
        assert not is_readable_dir(unreadable) or os.geteuid() == 0
    finally:
        unreadable.chmod(0o700)
