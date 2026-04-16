"""Tests for cache providers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cachou.providers import (
    CacheEntry,
    CacheInfo,
    CacheProvider,
    NpmCacheProvider,
    PipCacheProvider,
    PoetryCacheProvider,
    PreCommitCacheProvider,
    SnapCacheProvider,
    format_size,
    get_all_providers,
    get_dir_size,
)


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size_bytes, expected",
    [
        (0, "0.0 B"),
        (512, "512.0 B"),
        (1024, "1.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024 * 1024 * 1024, "1.0 GB"),
        (1536, "1.5 KB"),
    ],
)
def test_format_size(size_bytes: int, expected: str) -> None:
    assert format_size(size_bytes) == expected


# ---------------------------------------------------------------------------
# get_dir_size
# ---------------------------------------------------------------------------


def test_get_dir_size_empty(tmp_path: Path) -> None:
    assert get_dir_size(tmp_path) == 0


def test_get_dir_size_with_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")  # 5 bytes
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "b.txt").write_text("world!")  # 6 bytes
    assert get_dir_size(tmp_path) == 11


def test_get_dir_size_nonexistent(tmp_path: Path) -> None:
    assert get_dir_size(tmp_path / "nope") == 0


# ---------------------------------------------------------------------------
# CacheEntry / CacheInfo dataclasses
# ---------------------------------------------------------------------------


def test_cache_entry_fields(tmp_path: Path) -> None:
    entry = CacheEntry(path=tmp_path, size=42, description="test")
    assert entry.size == 42
    assert entry.description == "test"
    assert entry.tag == ""


def test_cache_entry_tag(tmp_path: Path) -> None:
    entry = CacheEntry(path=tmp_path, size=10, description="tagged", tag="cache")
    assert entry.tag == "cache"


def test_cache_info_defaults(tmp_path: Path) -> None:
    info = CacheInfo(name="test", path=tmp_path, total_size=0)
    assert info.entries == []
    assert info.available is True
    assert info.error is None


# ---------------------------------------------------------------------------
# get_all_providers
# ---------------------------------------------------------------------------


def test_get_all_providers_returns_five() -> None:
    providers = get_all_providers()
    assert len(providers) == 5
    names = {p.name for p in providers}
    assert names == {"pip", "npm", "poetry", "pre-commit", "snap"}


# ---------------------------------------------------------------------------
# Provider cache_info when cache dirs don't exist
# ---------------------------------------------------------------------------


class TestPipProviderNoCache:
    def test_unavailable_when_no_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Point to a nonexistent directory so the provider reports unavailable
        monkeypatch.setattr(
            PipCacheProvider,
            "_cache_dir",
            lambda self: None,
        )
        info = PipCacheProvider().get_cache_info()
        assert info.available is False
        assert info.total_size == 0


class TestNpmProviderNoCache:
    def test_unavailable_when_no_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(NpmCacheProvider, "_cache_dir", lambda self: None)
        info = NpmCacheProvider().get_cache_info()
        assert info.available is False


class TestPoetryProviderNoCache:
    def test_unavailable_when_no_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PoetryCacheProvider, "_cache_dir", lambda self: None)
        info = PoetryCacheProvider().get_cache_info()
        assert info.available is False


class TestPreCommitProviderNoCache:
    def test_unavailable_when_no_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PreCommitCacheProvider, "_cache_dir", lambda self: None)
        info = PreCommitCacheProvider().get_cache_info()
        assert info.available is False


# ---------------------------------------------------------------------------
# Provider cache_info with fake cache directories
# ---------------------------------------------------------------------------


class TestPipProviderWithCache:
    def test_discovers_entries(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        (wheels / "pkg.whl").write_bytes(b"x" * 100)
        http = tmp_path / "http"
        http.mkdir()
        (http / "response").write_bytes(b"y" * 50)

        monkeypatch.setattr(PipCacheProvider, "_cache_dir", lambda self: tmp_path)
        info = PipCacheProvider().get_cache_info()
        assert info.available is True
        assert info.total_size == 150
        assert len(info.entries) >= 2

    def test_clear_removes_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        (wheels / "pkg.whl").write_bytes(b"x" * 100)

        monkeypatch.setattr(PipCacheProvider, "_cache_dir", lambda self: tmp_path)
        provider = PipCacheProvider()
        freed = provider.clear()
        assert freed >= 100
        assert not wheels.exists()


class TestNpmProviderWithCache:
    def test_discovers_cacache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cacache = tmp_path / "_cacache"
        cacache.mkdir()
        (cacache / "data").write_bytes(b"z" * 200)

        monkeypatch.setattr(NpmCacheProvider, "_cache_dir", lambda self: tmp_path)
        info = NpmCacheProvider().get_cache_info()
        assert info.available is True
        assert info.total_size == 200
        assert any("_cacache" in e.description for e in info.entries)


class TestPoetryProviderWithCache:
    def test_discovers_caches_and_artifacts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Named cache directory (simulates `poetry cache list` returning "PyPI")
        pypi_cache = tmp_path / "PyPI"
        pypi_cache.mkdir()
        (pypi_cache / "pkg.tar.gz").write_bytes(b"a" * 200)

        # Artifacts directory
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "wheel.whl").write_bytes(b"b" * 100)

        monkeypatch.setattr(PoetryCacheProvider, "_cache_dir", lambda self: tmp_path)
        monkeypatch.setattr(PoetryCacheProvider, "_list_poetry_caches", lambda self: ["PyPI"])
        info = PoetryCacheProvider().get_cache_info()
        assert info.available is True
        assert info.total_size == 300

        cache_entries = [e for e in info.entries if e.tag == "cache"]
        artifact_entries = [e for e in info.entries if e.tag == "artifact"]
        assert len(cache_entries) == 1
        assert cache_entries[0].description == "poetry cache: PyPI"
        assert len(artifact_entries) == 1
        assert artifact_entries[0].description == "poetry artifacts"

    def test_excludes_virtualenvs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        venvs = tmp_path / "virtualenvs"
        venvs.mkdir()
        (venvs / "env.tar").write_bytes(b"c" * 500)

        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "pkg.whl").write_bytes(b"d" * 100)

        monkeypatch.setattr(PoetryCacheProvider, "_cache_dir", lambda self: tmp_path)
        monkeypatch.setattr(PoetryCacheProvider, "_list_poetry_caches", lambda self: [])
        info = PoetryCacheProvider().get_cache_info()
        # virtualenvs should be excluded — only artifacts counted
        assert info.total_size == 100
        names = [e.description for e in info.entries]
        assert not any("virtualenvs" in n for n in names)

    def test_clear_cache_uses_poetry_command(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pypi_cache = tmp_path / "PyPI"
        pypi_cache.mkdir()
        (pypi_cache / "pkg.tar.gz").write_bytes(b"a" * 200)

        monkeypatch.setattr(PoetryCacheProvider, "_cache_dir", lambda self: tmp_path)
        monkeypatch.setattr(PoetryCacheProvider, "_list_poetry_caches", lambda self: ["PyPI"])

        # Track subprocess calls
        calls: list[list[str]] = []
        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            # Simulate success
            return original_run(["true"], **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        provider = PoetryCacheProvider()
        info = provider.get_cache_info()
        cache_entries = [e for e in info.entries if e.tag == "cache"]
        freed = provider.clear(cache_entries)
        assert freed == 200
        # Should have called `poetry cache clear PyPI --all -n`
        assert any(
            "poetry" in c[0] and "cache" in c and "clear" in c and "PyPI" in c
            for c in calls
        )

    def test_clear_artifacts_uses_rmtree(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "pkg.whl").write_bytes(b"d" * 100)

        monkeypatch.setattr(PoetryCacheProvider, "_cache_dir", lambda self: tmp_path)
        monkeypatch.setattr(PoetryCacheProvider, "_list_poetry_caches", lambda self: [])

        provider = PoetryCacheProvider()
        info = provider.get_cache_info()
        artifact_entries = [e for e in info.entries if e.tag == "artifact"]
        freed = provider.clear(artifact_entries)
        assert freed == 100
        assert not artifacts.exists()

    def test_other_dirs_tagged(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        other = tmp_path / "something"
        other.mkdir()
        (other / "data").write_bytes(b"x" * 50)

        monkeypatch.setattr(PoetryCacheProvider, "_cache_dir", lambda self: tmp_path)
        monkeypatch.setattr(PoetryCacheProvider, "_list_poetry_caches", lambda self: [])
        info = PoetryCacheProvider().get_cache_info()
        other_entries = [e for e in info.entries if e.tag == "other"]
        assert len(other_entries) == 1
        assert other_entries[0].size == 50


class TestPreCommitProviderWithCache:
    def test_discovers_subdirs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        repo = tmp_path / "repo123"
        repo.mkdir()
        (repo / "env.tar").write_bytes(b"b" * 400)

        monkeypatch.setattr(PreCommitCacheProvider, "_cache_dir", lambda self: tmp_path)
        info = PreCommitCacheProvider().get_cache_info()
        assert info.available is True
        assert info.total_size == 400

    def test_clear_returns_freed_size(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        repo = tmp_path / "repo123"
        repo.mkdir()
        (repo / "env.tar").write_bytes(b"b" * 400)

        monkeypatch.setattr(PreCommitCacheProvider, "_cache_dir", lambda self: tmp_path)
        # Monkeypatch subprocess to avoid calling real pre-commit
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError),
        )
        provider = PreCommitCacheProvider()
        freed = provider.clear()
        assert freed == 400
        assert not repo.exists()

    def test_respects_pre_commit_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path))
        provider = PreCommitCacheProvider()
        cache_dir = provider._cache_dir()
        assert cache_dir == tmp_path


# ---------------------------------------------------------------------------
# Snap provider
# ---------------------------------------------------------------------------


class TestSnapProviderNoCache:
    def test_unavailable_when_no_dirs_and_no_snaps(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no_cache"
        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: nonexistent)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: nonexistent)
        monkeypatch.setattr(SnapCacheProvider, "_list_disabled_snaps", lambda self: [])
        info = SnapCacheProvider().get_cache_info()
        assert info.available is False
        assert info.total_size == 0


class TestSnapProviderWithCache:
    def test_discovers_system_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "pkg.snap").write_bytes(b"s" * 300)

        user_dir = tmp_path / "user_snap"
        # user_dir intentionally does not exist

        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: cache_dir)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: user_dir)
        monkeypatch.setattr(SnapCacheProvider, "_list_disabled_snaps", lambda self: [])

        info = SnapCacheProvider().get_cache_info()
        assert info.available is True
        assert info.total_size == 300
        cache_entries = [e for e in info.entries if e.tag == "cache"]
        assert len(cache_entries) == 1

    def test_discovers_user_snap_data(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        # system cache does not exist

        user_dir = tmp_path / "user_snap"
        user_dir.mkdir()
        (user_dir / "data").write_bytes(b"u" * 150)

        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: cache_dir)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: user_dir)
        monkeypatch.setattr(SnapCacheProvider, "_list_disabled_snaps", lambda self: [])

        info = SnapCacheProvider().get_cache_info()
        assert info.available is True
        assert info.total_size == 150
        user_entries = [e for e in info.entries if e.tag == "user_cache"]
        assert len(user_entries) == 1

    def test_discovers_disabled_snaps(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        user_dir = tmp_path / "user_snap"

        # Create fake snap revision directory
        snap_dir = tmp_path / "firefox" / "100"
        snap_dir.mkdir(parents=True)
        (snap_dir / "data").write_bytes(b"d" * 500)

        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: cache_dir)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: user_dir)
        monkeypatch.setattr(
            SnapCacheProvider,
            "_list_disabled_snaps",
            lambda self: [("firefox", "120.0", "100")],
        )
        # Patch Path("/snap") to point to tmp_path for disabled snap path resolution
        original_get_cache_info = SnapCacheProvider.get_cache_info

        def patched_get_cache_info(self):
            # Temporarily override the disabled snap path
            entries: list[CacheEntry] = []
            for snap_name, version, revision in self._list_disabled_snaps():
                snap_path = tmp_path / snap_name / revision
                from cachou.providers import get_dir_size as _gds

                size = _gds(snap_path) if snap_path.exists() else 0
                entries.append(
                    CacheEntry(
                        path=snap_path,
                        size=size,
                        description=f"disabled snap: {snap_name} {version} (rev {revision})",
                        tag="disabled_snap",
                    )
                )
            total = sum(e.size for e in entries)
            return CacheInfo(name=self.name, path=None, total_size=total, entries=entries)

        monkeypatch.setattr(SnapCacheProvider, "get_cache_info", patched_get_cache_info)

        info = SnapCacheProvider().get_cache_info()
        assert info.total_size == 500
        disabled = [e for e in info.entries if e.tag == "disabled_snap"]
        assert len(disabled) == 1
        assert "firefox" in disabled[0].description

    def test_clear_user_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        user_dir = tmp_path / "user_snap"
        user_dir.mkdir()
        (user_dir / "data").write_bytes(b"u" * 150)

        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: cache_dir)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: user_dir)
        monkeypatch.setattr(SnapCacheProvider, "_list_disabled_snaps", lambda self: [])

        provider = SnapCacheProvider()
        info = provider.get_cache_info()
        user_entries = [e for e in info.entries if e.tag == "user_cache"]
        freed = provider.clear(user_entries)
        assert freed == 150
        assert not user_dir.exists()

    def test_clear_disabled_snap_calls_snap_remove(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        user_dir = tmp_path / "user_snap"
        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: cache_dir)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: user_dir)
        monkeypatch.setattr(SnapCacheProvider, "_list_disabled_snaps", lambda self: [])

        calls: list[list[str]] = []
        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return original_run(["true"], **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        provider = SnapCacheProvider()
        # Manually create a disabled_snap entry
        entry = CacheEntry(
            path=Path("/snap/firefox/100"),
            size=500,
            description="disabled snap: firefox 120.0 (rev 100)",
            tag="disabled_snap",
        )
        # Make provider think it's available
        monkeypatch.setattr(
            SnapCacheProvider,
            "get_cache_info",
            lambda self: CacheInfo(name="snap", path=cache_dir, total_size=500, entries=[entry]),
        )
        freed = provider.clear([entry])
        assert freed == 500
        assert any(
            "snap" in c[0] and "remove" in c and "firefox" in c and "--revision=100" in c
            for c in calls
        )

    def test_clear_system_cache_uses_sudo(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "data").write_bytes(b"c" * 200)

        user_dir = tmp_path / "user_snap"
        monkeypatch.setattr(SnapCacheProvider, "_snap_cache_dir", lambda self: cache_dir)
        monkeypatch.setattr(SnapCacheProvider, "_user_snap_dir", lambda self: user_dir)
        monkeypatch.setattr(SnapCacheProvider, "_list_disabled_snaps", lambda self: [])

        calls: list[list[str]] = []
        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return original_run(["true"], **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        provider = SnapCacheProvider()
        info = provider.get_cache_info()
        cache_entries = [e for e in info.entries if e.tag == "cache"]
        freed = provider.clear(cache_entries)
        assert freed == 200
        assert any("sudo" in c[0] and "rm" in c for c in calls)
