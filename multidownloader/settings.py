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
    }


_VALIDATORS = {
    'output_root': lambda v: isinstance(v, str) and bool(v.strip()),
    'window_geometry': lambda v: v is None or (isinstance(v, str) and bool(re_geometry(v))),
    'details_open': lambda v: isinstance(v, bool),
    'parallel': lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 6,
    'include_title': lambda v: isinstance(v, bool),
    'keep_awake': lambda v: isinstance(v, bool),
}


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


__all__ = ['SCHEMA', 'default_output_root', 'defaults', 'load', 'save', 'settings_path']
