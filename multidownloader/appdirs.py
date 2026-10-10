"""Per-user folders for app data and logs (brief T7, T15).

Matches the platform conventions (the same paths platformdirs gives) without
adding a dependency:

- Windows: %LOCALAPPDATA%\\multisource-downloader, logs in its Logs folder.
- macOS: ~/Library/Application Support/multisource-downloader, logs in
  ~/Library/Logs/multisource-downloader.
- Others: $XDG_DATA_HOME (~/.local/share)/multisource-downloader, logs in
  $XDG_STATE_HOME (~/.local/state)/multisource-downloader/log.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = 'multisource-downloader'


def data_dir() -> Path:
    if sys.platform == 'win32' and os.environ.get('LOCALAPPDATA'):
        return Path(os.environ['LOCALAPPDATA']) / APP_NAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / APP_NAME
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / APP_NAME


def config_dir() -> Path:
    """Where settings.json lives: the data folder on Windows and macOS, $XDG_CONFIG_HOME on Linux."""
    if sys.platform == 'win32' or sys.platform == 'darwin':
        return data_dir()
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / APP_NAME


def log_dir() -> Path:
    if sys.platform == 'win32' and os.environ.get('LOCALAPPDATA'):
        return data_dir() / 'Logs'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / APP_NAME
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / APP_NAME / 'log'


def private_dir(path: Path) -> Path:
    """Create path, readable only by this user on POSIX (0700)."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'posix':
        os.chmod(path, 0o700)
    return path


__all__ = ['APP_NAME', 'config_dir', 'data_dir', 'log_dir', 'private_dir']
