"""Logging for every entry point (brief T15).

setup() sends logs to a rotating file, ``app.log`` in the per-user log folder
(1 MB, 5 files kept, UTF-8), and optionally to the console. A filter removes
secrets before anything is written: cookie values, Cookie and Authorization
headers, passwords, cookies.txt lines, and the home folder path (shown as ~).

``python -m multidownloader.logs`` prints a diagnostics report to paste into
a bug report.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .appdirs import log_dir, private_dir

LOG_FILENAME = 'app.log'
MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 5
FORMAT = '%(asctime)s %(levelname)s %(name)s %(message)s'
DATE_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
REDACTED = '[redacted]'

# names whose values are sign-ins on the supported platforms
_SECRET_COOKIES = (
    r'sessionid|ds_user_id|csrftoken|auth_token|ct0|twid|c_user|xs|fr|datr|SAPISID|APISID|SSID|HSID|SID|'
    r'LOGIN_INFO|__Secure-[\w-]+|reddit_session|token_v2|sid_tt|sessionid_ss|msToken|ttwid'
)
_PATTERNS = (
    # a Netscape cookies.txt line: seven tab-separated fields
    (re.compile(r'^(#HttpOnly_)?[^\t\n]+\t(TRUE|FALSE)\t[^\t\n]*\t(TRUE|FALSE)\t\d+\t[^\t\n]+\t[^\n]*$',
                re.IGNORECASE | re.MULTILINE), '[cookie line redacted]'),
    (re.compile(r'(?i)\b(cookie|set-cookie)(\s*[:=]\s*)[^\n]+'), r'\1\2' + REDACTED),
    (re.compile(r'(?i)\b(authorization)(\s*[:=]\s*)(bearer|basic|low|token)?\s*[^\s,;"\']+(\s*:\s*[^\s,;"\']+)?'),
     r'\1\2' + REDACTED),
    (re.compile(r'(?i)(["\']?(?:password|passwd|pwd|client_secret|refresh_token|access_token)["\']?\s*[:=]\s*)'
                r'("[^"]*"|\'[^\']*\'|[^\s,;]+)'), r'\1' + REDACTED),
    (re.compile(r'\b(' + _SECRET_COOKIES + r')=([^;\s,"\']+)'), r'\1=' + REDACTED),
)


def redact(text: str) -> str:
    """Remove secrets and the home folder path from text."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    home = str(Path.home())
    for variant in {home, home.replace('\\', '/')}:
        if variant and len(variant) > 3:
            text = re.sub(re.escape(variant), '~', text, flags=re.IGNORECASE if os.name == 'nt' else 0)
    return text


class RedactFilter(logging.Filter):
    """Applied to every handler setup() adds, so no secret reaches a log file or the console."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string: keep the record as it is
            return True
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class _UTCFormatter(logging.Formatter):
    converter = time.gmtime


def log_path() -> Path:
    return log_dir() / LOG_FILENAME


def setup(level: int = logging.INFO, *, console: bool = True, console_level: Optional[int] = None) -> Path:
    """Configure the root logger once per process; returns the log file path."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, '_msdl', False):
            root.removeHandler(handler)
    formatter = _UTCFormatter(FORMAT, DATE_FORMAT)
    redactor = RedactFilter()
    path = log_path()
    handlers: List[logging.Handler] = []
    try:
        private_dir(path.parent)
        handlers.append(logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                                             encoding='utf-8'))
    except OSError as exc:  # an unwritable profile must not stop the app
        print(f'Could not open the log file {path}: {exc}', file=sys.stderr)
    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setLevel(console_level if console_level is not None else level)
        handlers.append(stream)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
        handler._msdl = True
        root.addHandler(handler)
    root.setLevel(level)
    return path


def tail(path: Path, lines: int = 200) -> List[str]:
    try:
        with open(path, encoding='utf-8', errors='replace') as fp:
            return fp.readlines()[-lines:]
    except OSError:
        return []


def diagnostics(log_lines: int = 200) -> str:
    """App, system and component versions plus the last log lines, with secrets removed."""
    from importlib import metadata

    def version(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return 'not installed'

    try:
        import tkinter
        tk_version = str(tkinter.TkVersion)
    except Exception:
        tk_version = 'not available'
    rows = [
        f'multisource-downloader {__version__}',
        f'OS: {platform.platform()}',
        f'Python: {sys.version.split()[0]} ({sys.executable})',
        f'Tk: {tk_version}',
    ]
    rows += [f'{name}: {version(name)}' for name in ('yt-dlp', 'yt-dlp-ejs', 'instaloader', 'gdown', 'google-auth-oauthlib',
                                                      'pywebview')]
    rows += [f'ffmpeg: {"found" if shutil.which("ffmpeg") else "not found"}',
             f'Deno: {"found" if shutil.which("deno") else "not found"}',
             f'Log file: {log_path()}', '', f'Last {log_lines} log lines:']
    rows += [line.rstrip('\n') for line in tail(log_path(), log_lines)]
    return redact('\n'.join(rows))


if __name__ == '__main__':  # pragma: no cover
    print(diagnostics())
