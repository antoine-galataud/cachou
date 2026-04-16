"""Cache provider abstraction and implementations for pip, npm, poetry, and pre-commit."""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CacheEntry:
    """Represents a removable item within a cache."""

    path: Path
    size: int
    description: str
    tag: str = ""


@dataclass
class CacheInfo:
    """Summary information about a cache provider."""

    name: str
    path: Path | None
    total_size: int
    entries: list[CacheEntry] = field(default_factory=list)
    available: bool = True
    error: str | None = None


def get_dir_size(path: Path) -> int:
    """Return total size in bytes of a directory tree."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def format_size(size_bytes: int) -> str:
    """Format a byte count into a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} PB"


class CacheProvider(ABC):
    """Base class for cache providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @abstractmethod
    def get_cache_info(self) -> CacheInfo:
        """Gather cache information including removable entries."""

    @abstractmethod
    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        """Remove cached data. Returns bytes freed.

        If *entries* is ``None``, remove the entire cache.
        """


class PipCacheProvider(CacheProvider):
    """Manages the pip HTTP/wheel cache."""

    @property
    def name(self) -> str:
        return "pip"

    def _cache_dir(self) -> Path | None:
        try:
            result = subprocess.run(
                ["pip", "cache", "dir"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                p = Path(result.stdout.strip())
                if p.exists():
                    return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        default = Path.home() / ".cache" / "pip"
        return default if default.exists() else None

    def get_cache_info(self) -> CacheInfo:
        cache_dir = self._cache_dir()
        if cache_dir is None or not cache_dir.exists():
            return CacheInfo(name=self.name, path=cache_dir, total_size=0, available=False)
        entries: list[CacheEntry] = []
        # pip caches wheels and http responses
        for sub in ("wheels", "http", "selfcheck"):
            subdir = cache_dir / sub
            if subdir.exists():
                size = get_dir_size(subdir)
                if size > 0:
                    entries.append(CacheEntry(path=subdir, size=size, description=f"pip {sub} cache"))
        total = sum(e.size for e in entries)
        # Also account for other files directly in cache_dir
        full_size = get_dir_size(cache_dir)
        if full_size > total:
            entries.append(
                CacheEntry(
                    path=cache_dir,
                    size=full_size - total,
                    description="pip other cache data",
                )
            )
            total = full_size
        return CacheInfo(name=self.name, path=cache_dir, total_size=total, entries=entries)

    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        info = self.get_cache_info()
        if not info.available:
            return 0
        targets = entries if entries is not None else info.entries
        freed = 0
        for entry in targets:
            if entry.path.exists():
                freed += entry.size
                if entry.path.is_dir():
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    entry.path.unlink(missing_ok=True)
        return freed


class NpmCacheProvider(CacheProvider):
    """Manages the npm cache."""

    @property
    def name(self) -> str:
        return "npm"

    def _cache_dir(self) -> Path | None:
        try:
            result = subprocess.run(
                ["npm", "config", "get", "cache"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                p = Path(result.stdout.strip())
                if p.exists():
                    return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        default = Path.home() / ".npm"
        return default if default.exists() else None

    def get_cache_info(self) -> CacheInfo:
        cache_dir = self._cache_dir()
        if cache_dir is None or not cache_dir.exists():
            return CacheInfo(name=self.name, path=cache_dir, total_size=0, available=False)
        entries: list[CacheEntry] = []
        # _cacache is the main content-addressable cache
        cacache = cache_dir / "_cacache"
        if cacache.exists():
            size = get_dir_size(cacache)
            if size > 0:
                entries.append(CacheEntry(path=cacache, size=size, description="npm content cache (_cacache)"))
        total = sum(e.size for e in entries)
        full_size = get_dir_size(cache_dir)
        if full_size > total:
            entries.append(
                CacheEntry(
                    path=cache_dir,
                    size=full_size - total,
                    description="npm other cache data",
                )
            )
            total = full_size
        return CacheInfo(name=self.name, path=cache_dir, total_size=total, entries=entries)

    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        info = self.get_cache_info()
        if not info.available:
            return 0
        if entries is None:
            # Use npm's built-in cache clean for full cleanup
            try:
                subprocess.run(
                    ["npm", "cache", "clean", "--force"],
                    capture_output=True,
                    timeout=60,
                )
                return info.total_size
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            # Fallback to manual removal
            entries = info.entries
        freed = 0
        for entry in entries:
            if entry.path.exists():
                freed += entry.size
                if entry.path.is_dir():
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    entry.path.unlink(missing_ok=True)
        return freed


class PoetryCacheProvider(CacheProvider):
    """Manages the Poetry cache.

    Handles two distinct categories:
    - **caches**: named package caches discovered via ``poetry cache list``,
      cleared safely with ``poetry cache clear <name> --all``.
    - **artifacts**: the ``artifacts`` subdirectory under the cache root,
      cleared by removing the directory tree.

    The ``virtualenvs`` directory is intentionally excluded.
    """

    EXCLUDED_DIRS = {"virtualenvs"}

    @property
    def name(self) -> str:
        return "poetry"

    def _cache_dir(self) -> Path | None:
        try:
            result = subprocess.run(
                ["poetry", "config", "cache-dir"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                p = Path(result.stdout.strip())
                if p.exists():
                    return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        default = Path.home() / ".cache" / "pypoetry"
        return default if default.exists() else None

    def _list_poetry_caches(self) -> list[str]:
        """Return named caches reported by ``poetry cache list``."""
        try:
            result = subprocess.run(
                ["poetry", "cache", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return [
                    line.strip()
                    for line in result.stdout.splitlines()
                    if line.strip()
                ]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return []

    def get_cache_info(self) -> CacheInfo:
        cache_dir = self._cache_dir()
        if cache_dir is None or not cache_dir.exists():
            return CacheInfo(name=self.name, path=cache_dir, total_size=0, available=False)

        entries: list[CacheEntry] = []

        # Discover named poetry caches and map to on-disk directories
        named_caches = self._list_poetry_caches()
        named_cache_dirs: set[str] = set()
        for cache_name in named_caches:
            # Poetry stores caches in subdirectories matching the cache name
            subdir = cache_dir / cache_name
            if subdir.is_dir():
                size = get_dir_size(subdir)
                if size > 0:
                    entries.append(
                        CacheEntry(
                            path=subdir,
                            size=size,
                            description=f"poetry cache: {cache_name}",
                            tag="cache",
                        )
                    )
                named_cache_dirs.add(cache_name)

        # Artifacts directory
        artifacts_dir = cache_dir / "artifacts"
        if artifacts_dir.is_dir():
            size = get_dir_size(artifacts_dir)
            if size > 0:
                entries.append(
                    CacheEntry(
                        path=artifacts_dir,
                        size=size,
                        description="poetry artifacts",
                        tag="artifact",
                    )
                )

        # Any remaining subdirectories that are not named caches, artifacts,
        # or excluded (virtualenvs)
        known = named_cache_dirs | self.EXCLUDED_DIRS | {"artifacts"}
        for sub in sorted(cache_dir.iterdir()):
            if sub.is_dir() and sub.name not in known:
                size = get_dir_size(sub)
                if size > 0:
                    entries.append(
                        CacheEntry(
                            path=sub,
                            size=size,
                            description=f"poetry {sub.name}",
                            tag="other",
                        )
                    )

        total = sum(e.size for e in entries)
        return CacheInfo(name=self.name, path=cache_dir, total_size=total, entries=entries)

    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        info = self.get_cache_info()
        if not info.available:
            return 0
        targets = entries if entries is not None else info.entries
        freed = 0
        for entry in targets:
            if entry.tag == "cache":
                # Use poetry's own command for safe cache clearing
                cache_name = entry.path.name
                try:
                    subprocess.run(
                        ["poetry", "cache", "clear", cache_name, "--all", "-n"],
                        capture_output=True,
                        timeout=30,
                    )
                    freed += entry.size
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    # Fallback to manual removal
                    if entry.path.exists():
                        freed += entry.size
                        shutil.rmtree(entry.path, ignore_errors=True)
            else:
                if entry.path.exists():
                    freed += entry.size
                    if entry.path.is_dir():
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        entry.path.unlink(missing_ok=True)
        return freed


class SnapCacheProvider(CacheProvider):
    """Manages Ubuntu snap cache and disabled snap revisions.

    Handles three distinct categories:
    - **cache**: the system-level snap download cache at ``/var/lib/snapd/cache``,
      cleared via ``sudo rm -rf`` (root access is prompted).
    - **user_cache**: per-user snap data under ``~/snap/``.
    - **disabled_snap**: old (disabled) snap revisions discovered via
      ``snap list --all``, removed individually with ``snap remove --revision``.
    """

    @property
    def name(self) -> str:
        return "snap"

    def _snap_cache_dir(self) -> Path:
        """Return the system-level snap cache directory."""
        return Path("/var/lib/snapd/cache")

    def _user_snap_dir(self) -> Path:
        """Return the user-level snap data directory."""
        return Path.home() / "snap"

    def _list_disabled_snaps(self) -> list[tuple[str, str, str]]:
        """Return ``(name, version, revision)`` tuples for disabled snap revisions."""
        try:
            env = os.environ.copy()
            env["LANG"] = "en_US.UTF-8"
            result = subprocess.run(
                ["snap", "list", "--all"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            if result.returncode == 0:
                disabled: list[tuple[str, str, str]] = []
                for line in result.stdout.splitlines():
                    if "disabled" in line.lower():
                        parts = line.split()
                        if len(parts) >= 3:
                            disabled.append((parts[0], parts[1], parts[2]))
                return disabled
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return []

    def get_cache_info(self) -> CacheInfo:
        entries: list[CacheEntry] = []

        # System snap cache (/var/lib/snapd/cache)
        cache_dir = self._snap_cache_dir()
        if cache_dir.exists():
            size = get_dir_size(cache_dir)
            if size > 0:
                entries.append(
                    CacheEntry(
                        path=cache_dir,
                        size=size,
                        description="snap system cache (/var/lib/snapd/cache)",
                        tag="cache",
                    )
                )

        # User snap data (~/snap/)
        user_dir = self._user_snap_dir()
        if user_dir.exists():
            size = get_dir_size(user_dir)
            if size > 0:
                entries.append(
                    CacheEntry(
                        path=user_dir,
                        size=size,
                        description="snap user data (~/snap)",
                        tag="user_cache",
                    )
                )

        # Disabled snap revisions
        for snap_name, version, revision in self._list_disabled_snaps():
            snap_path = Path("/snap") / snap_name / revision
            size = get_dir_size(snap_path) if snap_path.exists() else 0
            entries.append(
                CacheEntry(
                    path=snap_path,
                    size=size,
                    description=f"disabled snap: {snap_name} {version} (rev {revision})",
                    tag="disabled_snap",
                )
            )

        total = sum(e.size for e in entries)
        if not entries:
            return CacheInfo(name=self.name, path=None, total_size=0, available=False)

        return CacheInfo(
            name=self.name,
            path=cache_dir if cache_dir.exists() else None,
            total_size=total,
            entries=entries,
        )

    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        info = self.get_cache_info()
        if not info.available:
            return 0
        targets = entries if entries is not None else info.entries
        freed = 0
        for entry in targets:
            if entry.tag == "disabled_snap":
                # Remove a specific disabled snap revision
                snap_name = entry.path.parent.name
                revision = entry.path.name
                try:
                    subprocess.run(
                        ["snap", "remove", snap_name, f"--revision={revision}"],
                        capture_output=True,
                        timeout=60,
                    )
                    freed += entry.size
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
            elif entry.tag == "cache":
                # System cache requires sudo — let the password prompt
                # pass through to the terminal by not capturing output.
                try:
                    subprocess.run(
                        ["sudo", "rm", "-rf", str(entry.path)],
                        timeout=60,
                    )
                    freed += entry.size
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
            else:
                # User snap data — regular removal
                if entry.path.exists():
                    freed += entry.size
                    if entry.path.is_dir():
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        entry.path.unlink(missing_ok=True)
        return freed


class PreCommitCacheProvider(CacheProvider):
    """Manages the pre-commit environments cache."""

    @property
    def name(self) -> str:
        return "pre-commit"

    def _cache_dir(self) -> Path | None:
        # pre-commit respects PRE_COMMIT_HOME, defaults to ~/.cache/pre-commit
        env = os.environ.get("PRE_COMMIT_HOME")
        if env:
            p = Path(env)
            if p.exists():
                return p
        default = Path.home() / ".cache" / "pre-commit"
        return default if default.exists() else None

    def get_cache_info(self) -> CacheInfo:
        cache_dir = self._cache_dir()
        if cache_dir is None or not cache_dir.exists():
            return CacheInfo(name=self.name, path=cache_dir, total_size=0, available=False)
        entries: list[CacheEntry] = []
        for sub in sorted(cache_dir.iterdir()):
            if sub.is_dir():
                size = get_dir_size(sub)
                if size > 0:
                    entries.append(
                        CacheEntry(path=sub, size=size, description=f"pre-commit {sub.name}")
                    )
        total = sum(e.size for e in entries)
        return CacheInfo(name=self.name, path=cache_dir, total_size=total, entries=entries)

    def clear(self, entries: list[CacheEntry] | None = None) -> int:
        info = self.get_cache_info()
        if not info.available:
            return 0
        if entries is None:
            # Use pre-commit's built-in clean command
            try:
                subprocess.run(
                    ["pre-commit", "clean"],
                    capture_output=True,
                    timeout=30,
                )
                return info.total_size
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            entries = info.entries
        freed = 0
        for entry in entries:
            if entry.path.exists():
                freed += entry.size
                if entry.path.is_dir():
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    entry.path.unlink(missing_ok=True)
        return freed


def get_all_providers() -> list[CacheProvider]:
    """Return all available cache providers."""
    return [
        PipCacheProvider(),
        NpmCacheProvider(),
        PoetryCacheProvider(),
        PreCommitCacheProvider(),
        SnapCacheProvider(),
    ]
