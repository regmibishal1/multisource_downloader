"""Remembered settings (brief F23, T11).

settings.json lives in the per-user config folder (see appdirs.config_dir). It
is versioned ("schema"), written atomically, and forgiving: unknown keys are
ignored and a value of the wrong type is reset to its default with a warning,
so a damaged file never stops the app from starting.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .appdirs import config_dir

_log = logging.getLogger(__name__)

SCHEMA = 1
FILENAME = 'settings.json'


def default_output_root() -> str:
    return str(Path.home() / 'Downloads' / 'Multisource')


def defaults() -> Dict[str, Any]:
    return {
        'schema': SCHEMA,
        'output_root': default_output_root(),
        'window_geometry': None,   # "960x680+40+40"
        'details_open': False,
        'parallel': 3,
        'include_title': False,
        'keep_awake': True,
        # Options window (brief 6.4)
        'quality': 'best',          # best | 1080 | 720 | audio
        'write_metadata': True,     # post details (.info.json and similar)
        'thumbnails': True,
        'subtitles': False,
        'layout': 'account',        # account | platform | flat
        'polite': True,             # random pause between links from one platform
        'theme': 'system',          # system | light | dark
        'view': 'full',             # full | simple (brief F21)
        'check_updates': True,      # once a day, ask pypi.org for the newest yt-dlp and gallery-dl (F24)
        'source_organization': '',  # Source-Organization in export packages (F26)
        'original_streams': True,   # keep files exactly as the platform served them (F30)
        'keep_streams': False,      # also keep separate video and audio streams after joining them
    }


QUALITIES = ('best', '1080', '720', 'audio')
LAYOUTS = ('account', 'platform', 'flat')
THEMES = ('system', 'light', 'dark')


def download_options(prefs: Dict[str, Any], handler: Optional[str]) -> Dict[str, Any]:
    """The per-link options a backend gets from the user's settings."""
    opts: Dict[str, Any] = {
        'quality': prefs.get('quality', 'best'),
        'write_metadata': prefs.get('write_metadata', True),
        'thumbnails': prefs.get('thumbnails', True),
        'subtitles': prefs.get('subtitles', False),
        'layout': prefs.get('layout', 'account'),
        'original_streams': prefs.get('original_streams', True),
        'keep_streams': prefs.get('keep_streams', False),
    }
    if prefs.get('include_title'):
        opts['include_title'] = True
    if handler == 'Instagram':
        opts['auth'] = 'auto'
    elif handler not in ('Google Drive', 'Bluesky', None):
        opts['use_session'] = True  # yt-dlp handlers reuse the stored sign-in
    return opts


_VALIDATORS = {
    'output_root': lambda v: isinstance(v, str) and bool(v.strip()),
    'window_geometry': lambda v: v is None or (isinstance(v, str) and bool(re_geometry(v))),
    'details_open': lambda v: isinstance(v, bool),
    'parallel': lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 6,
    'include_title': lambda v: isinstance(v, bool),
    'keep_awake': lambda v: isinstance(v, bool),
    'quality': lambda v: v in QUALITIES,
    'write_metadata': lambda v: isinstance(v, bool),
    'thumbnails': lambda v: isinstance(v, bool),
    'subtitles': lambda v: isinstance(v, bool),
    'layout': lambda v: v in LAYOUTS,
    'polite': lambda v: isinstance(v, bool),
    'theme': lambda v: v in THEMES,
    'view': lambda v: v in ('full', 'simple'),
    'check_updates': lambda v: isinstance(v, bool),
    'original_streams': lambda v: isinstance(v, bool),
    'keep_streams': lambda v: isinstance(v, bool),
    'source_organization': lambda v: isinstance(v, str) and len(v) <= 200 and '\n' not in v,
}


def valid(key: str, value: Any) -> bool:
    """True when value is usable for the setting key (the check load() applies)."""
    check = _VALIDATORS.get(key)
    return bool(check and check(value))


def re_geometry(value: str):
    import re
    return re.fullmatch(r'\d{2,5}x\d{2,5}([+-]-?\d{1,5}[+-]-?\d{1,5})?', value)


def settings_path() -> Path:
    return config_dir() / FILENAME


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path) if path else settings_path()
    values = defaults()
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return values
    except (OSError, ValueError) as exc:
        _log.warning('Could not read %s (%s); using the defaults', path, exc)
        return values
    if not isinstance(stored, dict):
        return values
    for key, check in _VALIDATORS.items():
        if key not in stored:
            continue
        if check(stored[key]):
            values[key] = stored[key]
        else:
            _log.warning('Setting %s has an unusable value %r; using %r', key, stored[key], values[key])
    return values


def save(values: Dict[str, Any], path: Optional[Path] = None) -> Path:
    from .session_store import _atomic_write

    path = Path(path) if path else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {key: values[key] for key in defaults() if key in values}
    clean['schema'] = SCHEMA
    _atomic_write(path, json.dumps(clean, indent=2, ensure_ascii=False).encode('utf-8'))
    return path


__all__ = ['LAYOUTS', 'QUALITIES', 'SCHEMA', 'THEMES', 'default_output_root', 'defaults', 'download_options', 'load',
           'save', 'settings_path', 'valid']
