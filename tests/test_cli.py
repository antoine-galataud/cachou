"""Tests for the CLI module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from cachou.cli import gather_cache_info, show_summary, show_details
from cachou.providers import CacheEntry, CacheInfo, CacheProvider


class FakeProvider(CacheProvider):
    """Fake provider for testing CLI functions."""

    def __init__(self, name: str, info: CacheInfo) -> None:
        self._name = name
        self._info = info

    @property
    def name(self) -> str:
        return self._name

    def get_cache_info(self) -> CacheInfo:
        return self._info

    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        return self._info.total_size


def _make_info(name: str, tmp_path: Path, size: int = 1024, n_entries: int = 2) -> CacheInfo:
    entries = [
        CacheEntry(path=tmp_path / f"entry{i}", size=size // max(n_entries, 1), description=f"{name} entry {i}")
        for i in range(n_entries)
    ]
    return CacheInfo(name=name, path=tmp_path, total_size=size, entries=entries)


def test_gather_cache_info(tmp_path: Path) -> None:
    info = _make_info("fake", tmp_path)
    provider = FakeProvider("fake", info)
    results = gather_cache_info([provider])
    assert len(results) == 1
    assert results[0][1].name == "fake"


def test_show_summary_no_crash(tmp_path: Path, capsys) -> None:
    """show_summary should print without errors."""
    info = _make_info("test", tmp_path, size=2048, n_entries=1)
    provider = FakeProvider("test", info)
    # Should not raise
    show_summary([(provider, info)])


def test_show_details_unavailable(tmp_path: Path, capsys) -> None:
    info = CacheInfo(name="gone", path=tmp_path, total_size=0, available=False)
    # Should not raise
    show_details(info)


def test_show_details_empty(tmp_path: Path) -> None:
    info = CacheInfo(name="empty", path=tmp_path, total_size=0, entries=[], available=True)
    show_details(info)
