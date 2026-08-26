from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

_SEESAW_ENV_PREFIX = "SEESAW_"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run every test in a clean directory with no SEESAW_* variables leaking in."""
    for key in list(os.environ):
        if key.startswith(_SEESAW_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
