"""Image posts through gallery-dl, run as a separate program (brief F5, T18, T19).

gallery-dl is GPL-2.0-only, so this app never imports it: it is started as
its own process with a generated config, and only its files and exit status
are used. Its own config files are ignored (--config-ignore), so a user's
gallery-dl setup cannot change where files go.

Files follow the app's layout: <platform>/<account>/<YYYY-MM-DD>_<post id>_<n>.<ext>,
with one <date>_<post id>.json of the post's details next to them. The date is
the post's, or today's when the site gives none. The stored sign-in, if any,
is passed as a throwaway copy of its cookies.txt, never written back.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

_log = logging.getLogger(__name__)

NAME = 'gallery-dl'
NOT_INSTALLED = 'gallery-dl is not installed. Image posts need it: python -m pip install gallery-dl'

# gallery-dl field names differ by site; the first one present wins
ACCOUNT = ('{author[name]|author[handle]|author[uniqueId]|user[name]|username|owner[username]|author|uploader|'
           '_lit[unknown]}')
POST_ID = '{tweet_id|post_shortcode|post_id|shortcode|id|_lit[post]}'
DATE = '{date|post_date|_now:%Y-%m-%d}'


def command() -> Optional[List[str]]:
    """How to start gallery-dl: the installed module, or a gallery-dl program on PATH."""
    if importlib.util.find_spec('gallery_dl') is not None:
        return [sys.executable, '-m', 'gallery_dl']
    found = shutil.which('gallery-dl')
    return [found] if found else None


def available() -> Tuple[bool, str]:
    return (True, '') if command() else (False, NOT_INSTALLED)


def build_config(out_dir: str, platform_key: str, *, cookies: Optional[str] = None,
                 user_agent: Optional[str] = None, layout: str = 'account', write_metadata: bool = True) -> dict:
    folders = {'account': [platform_key, ACCOUNT], 'platform': [platform_key], 'flat': []}
    extractor = {
        'base-directory': str(out_dir),
        'directory': folders.get(layout, folders['account']),
        'filename': f'{DATE}_{POST_ID}_{{num}}.{{extension}}',
        'path-restrict': 'windows',  # names valid on Windows, FAT and exFAT too
        'skip': False,
        'postprocessors': [{
            'name': 'metadata',
            'event': 'post',
            'filename': f'{DATE}_{POST_ID}.json',
            'mode': 'json',
        }],
    }
    if not write_metadata:
        extractor['postprocessors'] = []
    if cookies:
        extractor['cookies'] = cookies
        extractor['cookies-update'] = False
    if user_agent:
        extractor['user-agent'] = user_agent
    return {'extractor': extractor}


def download(url: str, out_dir: str, *, platform_key: str, cookie_file: Optional[Path] = None,
             user_agent: Optional[str] = None, cancel_event=None, layout: str = 'account',
             write_metadata: bool = True, runner=subprocess.Popen) -> List[str]:
    """Download url into out_dir. Returns the files written; raises RuntimeError with gallery-dl's message."""
    cmd = command()
    if not cmd:
        raise RuntimeError(NOT_INSTALLED)
    with tempfile.TemporaryDirectory(prefix='msdl-gdl-') as work:
        cookies = None
        if cookie_file and Path(cookie_file).is_file():
            cookies = os.path.join(work, 'cookies.txt')
            shutil.copyfile(cookie_file, cookies)  # gallery-dl never writes the stored sign-in
        config_path = os.path.join(work, 'config.json')
        with open(config_path, 'w', encoding='utf-8') as fp:
            json.dump(build_config(out_dir, platform_key, cookies=cookies, user_agent=user_agent, layout=layout,
                                   write_metadata=write_metadata), fp)
        before = {p for p in Path(out_dir).rglob('*') if p.is_file()}
        args = [*cmd, '--config-ignore', '--config-json', config_path, url]
        _log.info('gallery-dl start: %s', url)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        proc = runner(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                      errors='replace', creationflags=flags)
        stdout, stderr = _wait(proc, cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError('Stopped')
    if proc.returncode != 0:
        message = _last_error(stderr) or f'gallery-dl stopped with exit status {proc.returncode}'
        raise RuntimeError(f'gallery-dl failed: {message}')
    written = sorted(str(p) for p in Path(out_dir).rglob('*') if p.is_file() and p not in before)
    _log.info('gallery-dl saved %s file(s) for %s', len(written), url)
    return written


def _wait(proc, cancel_event) -> Tuple[str, str]:
    if cancel_event is None:
        return proc.communicate()
    while True:
        try:
            return proc.communicate(timeout=0.25)
        except subprocess.TimeoutExpired:
            if cancel_event.is_set():
                proc.terminate()
                with contextlib.suppress(Exception):
                    return proc.communicate(timeout=5)
                proc.kill()
                return proc.communicate()


def _last_error(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or '').splitlines() if line.strip()]
    errors = [line for line in lines if '[error]' in line.lower()]
    picked = errors[-1] if errors else (lines[-1] if lines else '')
    return picked.split('[error]', 1)[-1].strip() if '[error]' in picked else picked


def version() -> Optional[str]:
    from importlib import metadata

    try:
        return f'gallery-dl {metadata.version("gallery-dl")}'
    except metadata.PackageNotFoundError:
        return None


__all__ = ['NAME', 'NOT_INSTALLED', 'available', 'build_config', 'command', 'download', 'version']
