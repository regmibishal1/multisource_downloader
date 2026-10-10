"""Local session storage helpers for multidownloader handlers.

Sessions are stored under a .sessions directory next to this package. The
intention is to keep reusable auth artifacts (cookies, Instaloader sessions,
OAuth tokens) on disk without leaking them outside the local machine.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
SESSION_ROOT = PACKAGE_ROOT / '.sessions'


def _sanitize(name: str) -> str:
    cleaned = ''.join(ch.lower() if ch.isalnum() else '_' for ch in name)
    cleaned = cleaned.strip('_') or 'default'
    return cleaned


def ensure_session_dir(source: str) -> Path:
    directory = SESSION_ROOT / _sanitize(source)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


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


__all__ = [
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
