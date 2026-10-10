"""Helpers for turning server-supplied names into safe local paths."""
from __future__ import annotations

import re
from pathlib import Path

MAX_NAME_LENGTH = 120

_WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, fallback: str = 'download') -> str:
    """Return a single path component that cannot escape its folder.

    Path separators and characters Windows rejects become "_", leading and
    trailing dots and spaces are stripped, Windows reserved names get a "_"
    prefix, and the result is capped at MAX_NAME_LENGTH characters with the
    extension kept.
    """
    cleaned = _UNSAFE_CHARS.sub('_', name or '')
    cleaned = cleaned.strip(' .')
    if not cleaned:
        cleaned = fallback
    if cleaned.split('.')[0].upper() in _WINDOWS_RESERVED:
        cleaned = '_' + cleaned
    if len(cleaned) > MAX_NAME_LENGTH:
        stem, dot, ext = cleaned.rpartition('.')
        if dot and 0 < len(ext) <= 10:
            cleaned = stem[: MAX_NAME_LENGTH - len(ext) - 1].rstrip(' .') + '.' + ext
        else:
            cleaned = cleaned[:MAX_NAME_LENGTH].rstrip(' .')
    return cleaned


def path_inside(root, name: str, fallback: str = 'download') -> Path:
    """Join a sanitized name onto root and refuse anything that lands outside it."""
    root_path = Path(root).resolve()
    target = (root_path / safe_filename(name, fallback)).resolve()
    if target.parent != root_path:
        raise ValueError(f'Refusing to write outside the output folder: {name!r}')
    return target


__all__ = ['safe_filename', 'path_inside', 'MAX_NAME_LENGTH']
