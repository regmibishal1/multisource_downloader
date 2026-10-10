"""History of saved links, so a list can be run again without downloading twice (brief F12).

One SQLite file per user (``history.sqlite`` in the per-user data folder),
shared by every output folder and run. A link counts as saved when its
normalized URL, or its platform and post id, was saved before. Only the
thread running the downloads writes to it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlsplit

from . import detect
from .appdirs import data_dir as user_data_dir

HISTORY_FILENAME = 'history.sqlite'

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved (
    normalized_url TEXT PRIMARY KEY,
    platform TEXT,
    post_id TEXT,
    first_saved_at TEXT NOT NULL,
    output_root TEXT NOT NULL,
    manifest_path TEXT,
    files TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS saved_post ON saved (platform, post_id);
"""

_POST_PATTERNS = {
    'x': re.compile(r'/status/(\d+)'),
    'instagram': re.compile(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)'),
    'tiktok': re.compile(r'/(?:video|photo)/(\d+)'),
    'reddit': re.compile(r'/comments/([a-z0-9]+)'),
    'threads': re.compile(r'/post/([A-Za-z0-9_-]+)'),
    'gdrive': re.compile(r'(?:/d/|/folders/|[?&]id=)([\w-]+)'),
    'facebook': re.compile(r'/(?:videos|reel|posts)/(\d+)'),
    'bluesky': re.compile(r'/post/([a-z0-9]+)'),
}


def post_id(url: str) -> Optional[str]:
    """The platform's own id for the post a link points at, when it can be read from the link."""
    normalized = detect.normalize_url(url)
    platform = detect.platform_for_url(normalized)
    if not platform:
        return None
    if platform.key == 'youtube':
        return (parse_qs(urlsplit(normalized).query).get('v') or [None])[0]
    if platform.key == 'facebook':
        query = parse_qs(urlsplit(normalized).query)
        if query.get('v'):
            return query['v'][0]
    pattern = _POST_PATTERNS.get(platform.key)
    match = pattern.search(normalized) if pattern else None
    return match.group(1) if match else None


@dataclass
class SavedEntry:
    normalized_url: str
    platform: Optional[str]
    post_id: Optional[str]
    first_saved_at: str
    output_root: str
    manifest_path: Optional[str]
    files: List[str]

    def describe(self) -> str:
        day = self.first_saved_at[:10]
        return f'Saved on {day} in {self.output_root}'


class History:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else user_data_dir() / HISTORY_FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def find(self, url: str) -> Optional[SavedEntry]:
        normalized = detect.normalize_url(url)
        platform = detect.platform_for_url(normalized)
        pid = post_id(url)
        with self._lock:
            row = self._db.execute(
                'SELECT normalized_url, platform, post_id, first_saved_at, output_root, manifest_path, files '
                'FROM saved WHERE normalized_url = ? OR (post_id IS NOT NULL AND platform = ? AND post_id = ?) '
                'ORDER BY first_saved_at LIMIT 1',
                (normalized, platform.key if platform else None, pid),
            ).fetchone()
        if not row:
            return None
        return SavedEntry(*row[:6], json.loads(row[6] or '[]'))

    def add(self, url: str, *, saved_at: str, output_root, manifest_path=None, files=()) -> None:
        """Record a saved link. The first save is kept; later saves of the same link do not replace it."""
        normalized = detect.normalize_url(url)
        platform = detect.platform_for_url(normalized)
        with self._lock:
            self._db.execute(
                'INSERT OR IGNORE INTO saved (normalized_url, platform, post_id, first_saved_at, output_root, '
                'manifest_path, files) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (normalized, platform.key if platform else None, post_id(url), saved_at, str(output_root),
                 str(manifest_path) if manifest_path else None, json.dumps(list(files))),
            )
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return self._db.execute('SELECT COUNT(*) FROM saved').fetchone()[0]


__all__ = ['History', 'SavedEntry', 'post_id', 'user_data_dir']
