"""Local session storage helpers for multidownloader handlers.

Sessions are stored under a .sessions directory next to this package. The
intention is to keep reusable auth artifacts (cookies, Instaloader sessions,
OAuth tokens) on disk without leaking them outside the local machine.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

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


def read_json(source: str, filename: str) -> Optional[Dict[str, Any]]:
    path = path_for(source, filename)
    if not path.exists():
        return None
    try:
        with path.open('r', encoding='utf-8') as fp:
            return json.load(fp)
    except Exception:
        return None


def write_json(source: str, filename: str, data: Dict[str, Any]):
    path = path_for(source, filename)
    with path.open('w', encoding='utf-8') as fp:
        json.dump(data, fp, indent=2)
    return path


def write_binary(source: str, filename: str, data: bytes):
    path = path_for(source, filename)
    with path.open('wb') as fp:
        fp.write(data)
    return path


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
    path.write_text(text, encoding='utf-8')
    return path


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
