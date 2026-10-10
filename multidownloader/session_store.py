"""Local session storage helpers for multidownloader handlers.

Sign-ins (cookies, Instaloader sessions, OAuth tokens) live in the per-user
data folder, ``<data dir>/sessions/<platform>/`` (see appdirs), never next to
the code, so they survive installs and are never zipped up with it. Folders
are 0700 and files 0600 on POSIX. Older versions kept them in
``multidownloader/.sessions/``; migrate_legacy_sessions moves them.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .appdirs import data_dir, private_dir

_log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
LEGACY_SESSION_ROOT = PACKAGE_ROOT / '.sessions'
SESSION_ROOT = data_dir() / 'sessions'


def _sanitize(name: str) -> str:
    cleaned = ''.join(ch.lower() if ch.isalnum() else '_' for ch in name)
    cleaned = cleaned.strip('_') or 'default'
    return cleaned


def ensure_session_dir(source: str) -> Path:
    private_dir(SESSION_ROOT)
    return private_dir(SESSION_ROOT / _sanitize(source))


def path_for(source: str, filename: str) -> Path:
    directory = ensure_session_dir(source)
    return directory / filename


def _atomic_write(path: Path, data: bytes) -> Path:
    """Write data so a crash leaves either the old file or the new one, never half of it."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def read_json(source: str, filename: str) -> Optional[Dict[str, Any]]:
    path = path_for(source, filename)
    if not path.exists():
        return None
    try:
        with path.open('r', encoding='utf-8') as fp:
            return json.load(fp)
    except Exception as exc:
        backup = path.with_name(path.name + '.bak')
        _log.warning('Could not read %s (%s); keeping a copy at %s', path, exc, backup)
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        return None


def write_json(source: str, filename: str, data: Dict[str, Any]):
    path = path_for(source, filename)
    return _atomic_write(path, json.dumps(data, indent=2).encode('utf-8'))


def write_binary(source: str, filename: str, data: bytes):
    path = path_for(source, filename)
    return _atomic_write(path, data)


def read_text(source: str, filename: str) -> Optional[str]:
    path = path_for(source, filename)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return None


def write_text(source: str, filename: str, text: str):
    path = path_for(source, filename)
    return _atomic_write(path, text.encode('utf-8'))


def list_files(source: str, suffix: Optional[str] = None):
    directory = ensure_session_dir(source)
    files = {}
    for child in directory.iterdir():
        if not child.is_file():
            continue
        if suffix and not child.name.endswith(suffix):
            continue
        files[child.name] = child
    return files


def default_cookie_path(source: str) -> Path:
    """Return a cookies.txt path for the given source."""
    return path_for(source, 'cookies.txt')


def default_metadata_path(source: str) -> Path:
    return path_for(source, 'meta.json')


def load_default_session(source: str, *, filename: str = 'session.bin') -> Optional[bytes]:
    path = path_for(source, filename)
    if not path.exists():
        return None
    return path.read_bytes()


def write_default_session(source: str, data: bytes, *, filename: str = 'session.bin'):
    return write_binary(source, filename, data)


def _same_bytes(a: Path, b: Path) -> bool:
    import hashlib

    def digest(path):
        h = hashlib.sha256()
        with open(path, 'rb') as fp:
            for chunk in iter(lambda: fp.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()

    return a.stat().st_size == b.stat().st_size and digest(a) == digest(b)


def migrate_legacy_sessions(old_root: Optional[Path] = None, new_root: Optional[Path] = None) -> Dict[str, int]:
    """Move sign-ins from the old folder inside the package to the per-user folder.

    Every file is copied, then read back and compared byte for byte; only when
    every file matches are the old copies deleted. A file that already exists
    in the new folder is left alone and its old copy kept. Entry points call
    this once at start; it does nothing when there is no old folder.
    """
    old_root = Path(old_root) if old_root else LEGACY_SESSION_ROOT
    new_root = Path(new_root) if new_root else SESSION_ROOT
    stats = {'copied': 0, 'kept_existing': 0, 'deleted': 0, 'failed': 0}
    if not old_root.is_dir():
        return stats
    files = [p for p in old_root.rglob('*') if p.is_file()]
    verified = []
    for src in files:
        dest = new_root / src.relative_to(old_root)
        try:
            if dest.exists():
                stats['kept_existing'] += 1
                _log.warning('Not moving %s: %s already exists; the old copy is kept', src, dest)
                continue
            private_dir(dest.parent)
            shutil.copy2(src, dest)
            if os.name == 'posix':
                os.chmod(dest, 0o600)
            if not _same_bytes(src, dest):
                raise OSError('the copy does not match the original')
            stats['copied'] += 1
            verified.append(src)
        except OSError as exc:
            stats['failed'] += 1
            _log.error('Could not move %s to %s: %s', src, dest, exc)
    if stats['failed']:
        _log.error('Sign-ins were not fully moved; the old folder %s is kept', old_root)
        return stats
    for src in verified:
        try:
            src.unlink()
            stats['deleted'] += 1
        except OSError as exc:
            _log.warning('Could not delete the old copy %s: %s', src, exc)
    # remove the old folders once empty (a kept file keeps its folder)
    for folder in sorted((p for p in old_root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            folder.rmdir()
    with contextlib.suppress(OSError):
        old_root.rmdir()
    if stats['copied']:
        _log.info('Moved %s sign-in file(s) from %s to %s', stats['copied'], old_root, new_root)
    return stats


__all__ = [
    'migrate_legacy_sessions',
    'ensure_session_dir',
    'path_for',
    'read_json',
    'write_json',
    'write_binary',
    'read_text',
    'write_text',
    'list_files',
    'default_cookie_path',
    'default_metadata_path',
    'load_default_session',
    'write_default_session',
]
