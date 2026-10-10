"""Know when yt-dlp or gallery-dl is out of date (brief F24).

Sites change often and an old yt-dlp is the most common reason downloads
stop working. yt-dlp's version is its release date, so its age is known
without the network. At most once a day (and only when "check_updates" is on)
the latest versions are read from PyPI's public JSON API; nothing about the
user is sent. The result is cached in update_check.json in the data folder,
together with the versions the user dismissed.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .appdirs import data_dir

_log = logging.getLogger(__name__)

STALE_DAYS = 45
CHECK_EVERY_S = 24 * 3600
PACKAGES = ('yt-dlp', 'gallery-dl', 'instaloader')
PYPI_URL = 'https://pypi.org/pypi/{name}/json'
FILENAME = 'update_check.json'


@dataclass
class Notice:
    package: str
    installed: str
    latest: Optional[str]
    age_days: Optional[int]
    message: str

    @property
    def key(self) -> str:
        return f'{self.package}=={self.latest or self.installed}'


def state_path() -> Path:
    return data_dir() / FILENAME


def installed(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def version_tuple(version: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r'\d+', version or ''))


def release_date(version: str) -> Optional[dt.date]:
    """yt-dlp versions are dates: 2026.8.19 or 2026.08.19.1 (a nightly)."""
    match = re.match(r'(\d{4})\.(\d{1,2})\.(\d{1,2})', version or '')
    if not match:
        return None
    try:
        return dt.date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def fetch_latest(name: str, timeout: float = 10.0) -> Optional[str]:
    request = urllib.request.Request(PYPI_URL.format(name=name), headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
        return json.load(response)['info']['version']


def how_to_update(package: str) -> str:
    """What the user runs: a new app for the Windows app, else pip in the same Python."""
    if getattr(sys, 'frozen', False):
        return ('Download the newest Multisource Downloader from the Releases page on GitHub. '
                'It includes the newest yt-dlp and Instaloader.')
    spec = '"yt-dlp[default]"' if package == 'yt-dlp' else package
    return f'"{sys.executable}" -m pip install -U {spec}'


def _load(path: Path) -> Dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: Dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    except OSError as exc:
        _log.info('Could not save %s: %s', path, exc)


def notices(versions: Dict[str, Optional[str]], latest: Dict[str, Optional[str]], *,
            today: Optional[dt.date] = None) -> List[Notice]:
    """What to tell the user, from the installed and the latest versions."""
    today = today or dt.date.today()
    found = []
    ytdlp = versions.get('yt-dlp')
    if ytdlp:
        released = release_date(ytdlp)
        age = (today - released).days if released else None
        newest = latest.get('yt-dlp')
        newer = bool(newest) and version_tuple(newest) > version_tuple(ytdlp)
        # old by date, unless PyPI says there is nothing newer
        if age is not None and age > STALE_DAYS and (newer or not newest):
            found.append(Notice('yt-dlp', ytdlp, newest, age,
                                f'Your yt-dlp is {age} days old. Sites change often, so downloads may fail.'))
    gallery = versions.get('gallery-dl')
    newest = latest.get('gallery-dl')
    if gallery and newest and version_tuple(newest) > version_tuple(gallery):
        found.append(Notice('gallery-dl', gallery, newest, None,
                            f'gallery-dl {newest} is available (you have {gallery}). Image downloads may need it.'))
    insta = versions.get('instaloader')
    newest = latest.get('instaloader')
    if insta and newest and version_tuple(newest) > version_tuple(insta):
        found.append(Notice('instaloader', insta, newest, None,
                            f'Instaloader {newest} is available (you have {insta}). Instagram downloads may need it.'))
    return found


def check(*, path: Optional[Path] = None, now: Optional[float] = None,
          fetch: Callable[[str], Optional[str]] = fetch_latest, versions: Optional[Dict[str, Optional[str]]] = None,
          online: bool = True) -> List[Notice]:
    """Notices not dismissed yet. Asks PyPI only when online and the last check is a day old."""
    path = path or state_path()
    now = time.time() if now is None else now
    state = _load(path)
    versions = versions if versions is not None else {name: installed(name) for name in PACKAGES}
    latest = dict(state.get('latest') or {})
    if online and now - float(state.get('checked_at') or 0) >= CHECK_EVERY_S:
        for name in PACKAGES:
            if not versions.get(name):
                continue
            try:
                latest[name] = fetch(name)
            except Exception as exc:  # offline or PyPI down: the age check still works
                _log.info('Could not check the latest %s: %s', name, exc)
        state.update(checked_at=now, latest=latest)
        _save(path, state)
    dismissed = set(state.get('dismissed') or [])
    today = dt.datetime.fromtimestamp(now).date()
    return [n for n in notices(versions, latest, today=today) if n.key not in dismissed]


def dismiss(notice: Notice, *, path: Optional[Path] = None) -> None:
    path = path or state_path()
    state = _load(path)
    dismissed = list(state.get('dismissed') or [])
    if notice.key not in dismissed:
        dismissed.append(notice.key)
    state['dismissed'] = dismissed[-20:]
    _save(path, state)


__all__ = ['CHECK_EVERY_S', 'Notice', 'PACKAGES', 'STALE_DAYS', 'check', 'dismiss', 'fetch_latest', 'how_to_update',
           'installed', 'notices', 'release_date', 'state_path', 'version_tuple']
