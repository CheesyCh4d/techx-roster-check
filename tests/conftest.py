"""Test fixtures.

The config folder (XDG_CONFIG_HOME on Linux, APPDATA on Windows) is pointed
at a temporary folder before the package is imported, so the suite never
sees (or writes) the real user's packs.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="techx-roster-check-tests-"))
os.environ["XDG_CONFIG_HOME"] = str(_TMP / "config")   # Linux
os.environ["APPDATA"] = str(_TMP / "config")           # Windows

import pytest  # noqa: E402

from techx_roster_check.packs import BUILTIN_DIR, load_pack  # noqa: E402

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "examples" / "students-sample.txt"
TODAY = date(2026, 10, 21)


@pytest.fixture
def health():
    return load_pack(BUILTIN_DIR / "health.toml")


@pytest.fixture
def write_pack(tmp_path):
    """Write a small pack to a temp folder and load it."""

    def make(text: str, name: str = "test.toml"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return load_pack(path)

    return make


@pytest.fixture
def user_packs():
    """The (temporary) folder where a user's own packs live, emptied after each test."""
    folder = _TMP / "config" / "techx-roster-check" / "packs"
    folder.mkdir(parents=True, exist_ok=True)
    yield folder
    for item in folder.iterdir():
        item.unlink()
