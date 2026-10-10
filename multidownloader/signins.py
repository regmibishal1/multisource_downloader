"""Sign-ins: one stored sign-in per platform, shared by every backend.

Each platform folder (see session_store) holds:

- cookies.txt: Netscape format, only the platform's own domains.
- signin.json: how the sign-in was made, the account id, the marker cookie's
  expiry, the terms version the user acknowledged, and the last download
  results that used it.

Status is worked out offline from the marker cookies in platforms.py. No
function here imports a UI toolkit; the GUI and the CLI call these.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

from . import session_store
from .platforms import BY_HANDLER, BY_KEY, PLATFORMS, Platform

_log = logging.getLogger(__name__)

SIGNIN_FILENAME = 'signin.json'
COOKIE_FILENAME = 'cookies.txt'
COOKIE_HEADER = '# Netscape HTTP Cookie File'
EXPIRES_SOON_DAYS = 3

# Browsers yt-dlp can read, Firefox first because reading it triggers no
# system prompt and it works on Windows.
BROWSERS: Tuple[Tuple[str, str], ...] = (
    ('firefox', 'Firefox'),
    ('brave', 'Brave'),
    ('chrome', 'Chrome'),
    ('chromium', 'Chromium'),
    ('edge', 'Edge'),
    ('opera', 'Opera'),
    ('vivaldi', 'Vivaldi'),
    ('safari', 'Safari'),
)
BROWSER_NAMES = dict(BROWSERS)
CHROMIUM_BROWSERS = {'brave', 'chrome', 'chromium', 'edge', 'opera', 'vivaldi'}

METHOD_LABELS = {
    'browser': None,  # replaced by the browser's name
    'file': 'cookies file',
    'password': 'password',
    'webview': 'Sign in here',
}


class SignInError(Exception):
    """A problem the user can fix. str(exc) is a plain-language message."""


# --- cookies.txt --------------------------------------------------------------

@dataclass
class Cookie:
    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires: int  # Unix seconds; 0 means a session cookie
    name: str
    value: str
    http_only: bool = False

    def matches(self, domains: Iterable[str]) -> bool:
        host = self.domain.lstrip('.').lower()
        return any(host == d or host.endswith('.' + d) for d in domains)

    def to_line(self) -> str:
        domain = ('#HttpOnly_' if self.http_only else '') + self.domain
        return '\t'.join((
            domain,
            'TRUE' if self.include_subdomains else 'FALSE',
            self.path,
            'TRUE' if self.secure else 'FALSE',
            str(self.expires),
            self.name,
            self.value,
        ))


def _parse_expiry(raw: str) -> int:
    try:
        value = int(float(raw))
    except ValueError:
        return 0
    if value > 10**14:  # Chromium: microseconds since 1601
        value = value // 10**6 - 11644473600
    elif value > 10**11:  # milliseconds
        value //= 1000
    return max(value, 0)


def parse_cookies_txt(text: str) -> List[Cookie]:
    """Parse a Netscape cookies.txt. Raises SignInError if it is not one."""
    text = (text or '').lstrip('﻿')
    lines = text.splitlines()  # handles CRLF, LF and CR
    has_header = any(line.strip() in ('# HTTP Cookie File', '# Netscape HTTP Cookie File') for line in lines[:5])
    cookies = []
    for line in lines:
        http_only = False
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
            http_only = True
        elif not line.strip() or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) != 7:
            continue
        domain, subdomains, path, secure, expires, name, value = fields
        cookies.append(Cookie(
            domain=domain.strip(),
            include_subdomains=subdomains.strip().upper() == 'TRUE',
            path=path or '/',
            secure=secure.strip().upper() == 'TRUE',
            expires=_parse_expiry(expires),
            name=name,
            value=value,
            http_only=http_only,
        ))
    if not cookies and not has_header:
        raise SignInError('This file is not in cookies.txt format. Export it with the Get cookies.txt LOCALLY extension.')
    return cookies


def format_cookies_txt(cookies: Iterable[Cookie]) -> str:
    lines = [COOKIE_HEADER, '# Kept by multisource-downloader: one platform only.', '']
    lines.extend(c.to_line() for c in cookies)
    return os.linesep.join(lines) + os.linesep


def cookies_from_jar(jar) -> List[Cookie]:
    """Convert an http.cookiejar.CookieJar (yt-dlp, requests) to Cookie records."""
    cookies = []
    for c in jar:
        http_only = bool(c.has_nonstandard_attr('HttpOnly')) if hasattr(c, 'has_nonstandard_attr') else False
        cookies.append(Cookie(
            domain=c.domain,
            include_subdomains=c.domain.startswith('.'),
            path=c.path or '/',
            secure=bool(c.secure),
            expires=int(c.expires or 0),
            name=c.name,
            value=c.value or '',
            http_only=http_only,
        ))
    return cookies


def filter_for(platform: Platform, cookies: Iterable[Cookie]) -> List[Cookie]:
    return [c for c in cookies if c.matches(platform.cookie_domains)]


def find_markers(platform: Platform, cookies: Sequence[Cookie], now: Optional[float] = None) -> Tuple[bool, int, bool]:
    """Return (all marker groups present, earliest marker expiry, any marker expired).

    Expiry 0 means every marker is a session cookie.
    """
    now = time.time() if now is None else now
    by_name: Dict[str, List[Cookie]] = {}
    for c in cookies:
        by_name.setdefault(c.name, []).append(c)
    expiries = []
    expired = False
    for group in platform.markers:
        found = [c for name in group for c in by_name.get(name, [])]
        if not found:
            return False, 0, False
        best = max(found, key=lambda c: c.expires or float('inf'))
        if best.expires and best.expires <= now:
            expired = True
        if best.expires:
            expiries.append(best.expires)
    return True, (min(expiries) if expiries else 0), expired


def account_id(platform: Platform, cookies: Sequence[Cookie]) -> Optional[str]:
    if not platform.account_cookie:
        return None
    for c in cookies:
        if c.name == platform.account_cookie and c.value:
            value = unquote(c.value).strip('"')
            if value.startswith('u='):  # X: twid is "u=1234..."
                value = value[2:]
            return value
    return None


# --- storage ------------------------------------------------------------------

def _folder_name(platform: Platform) -> str:
    # Drive's existing OAuth files live under "GoogleDrive"
    return 'GoogleDrive' if platform.key == 'gdrive' else platform.handler


def _platform(key_or_handler: str) -> Platform:
    platform = BY_KEY.get(key_or_handler) or BY_HANDLER.get(key_or_handler)
    if not platform:
        raise KeyError(f'Unknown platform: {key_or_handler}')
    return platform


def folder(platform: Platform) -> Path:
    path = session_store.ensure_session_dir(_folder_name(platform))
    if os.name == 'posix':
        os.chmod(path, 0o700)
    return path


def cookie_path(platform: Platform) -> Path:
    return session_store.default_cookie_path(_folder_name(platform))


def read_meta(platform: Platform) -> Dict:
    return session_store.read_json(_folder_name(platform), SIGNIN_FILENAME) or {}


def _write_meta(platform: Platform, meta: Dict) -> None:
    session_store.write_json(_folder_name(platform), SIGNIN_FILENAME, meta)


def read_cookies(platform: Platform) -> List[Cookie]:
    path = cookie_path(platform)
    if not path.is_file():
        return []
    try:
        return parse_cookies_txt(path.read_text(encoding='utf-8', errors='replace'))
    except SignInError:
        return []


def _a(name: str) -> str:
    """Article for a platform name: "an Instagram", "an X", "a Facebook"."""
    return 'an' if name[:1].upper() in 'AEIOUX' else 'a'


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _iso(ts: int) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if ts else None


def needs_terms_ack(platform: Platform) -> bool:
    """True when the terms notice must be shown (first sign-in or new terms version)."""
    if not platform.terms:
        return False
    return read_meta(platform).get('terms_version_seen') != platform.terms.version


@dataclass
class SaveResult:
    platform: Platform
    kept: int
    removed: int
    account_id: Optional[str]

    def message(self) -> str:
        line = f'Kept {self.kept} {self.platform.name} cookies.'
        if self.removed:
            line += f' Removed {self.removed} cookies for other sites, which were not needed.'
        return line


def save_signin(
    platform: Platform,
    cookies: Sequence[Cookie],
    *,
    method: str,
    terms_version: Optional[str],
    browser: Optional[str] = None,
    profile: Optional[str] = None,
    label: Optional[str] = None,
    now: Optional[float] = None,
) -> SaveResult:
    """Validate, filter and store a sign-in. Raises SignInError with a message to show."""
    if platform.terms and terms_version != platform.terms.version:
        raise SignInError(f"Read and accept {platform.name}'s terms notice first.")
    kept = filter_for(platform, cookies)
    removed = len(cookies) - len(kept)
    found, expires, expired = find_markers(platform, kept, now)
    if not found:
        if method == 'browser':
            raise SignInError(f'No {platform.name} sign-in found in {BROWSER_NAMES.get(browser, browser)}. '
                              f'Sign in to {platform.name} there first.')
        raise SignInError(f'This file does not contain {_a(platform.name)} {platform.name} sign-in. '
                          f'Sign in on {platform.cookie_domains[0]} before exporting.')
    if expired:
        raise SignInError('This sign-in has expired. Sign in again and export a new file.')

    folder(platform)
    data = format_cookies_txt(kept).encode('utf-8')
    session_store._atomic_write(cookie_path(platform), data)  # mkstemp makes it 0600 on POSIX
    acct = account_id(platform, kept)
    previous = read_meta(platform)
    meta = {
        'method': method,
        'browser': browser,
        'profile': profile,
        'container': None,
        'user_agent': None,
        'account_id': acct,
        'label': label if label is not None else previous.get('label'),
        'imported_at': _now_iso(),
        'marker_expires_at': _iso(expires),
        'last_ok_at': None,
        'last_rejected_at': None,
        'terms_version_seen': platform.terms.version if platform.terms else None,
        'acknowledged_at': _now_iso() if platform.terms else None,
    }
    _write_meta(platform, meta)
    _log.info('Saved %s sign-in (%s, %s cookies kept, %s removed)', platform.key, method, len(kept), removed)
    return SaveResult(platform, len(kept), removed, acct)


def import_cookies_text(platform: Platform, text: str, *, terms_version: Optional[str], label=None) -> SaveResult:
    return save_signin(platform, parse_cookies_txt(text), method='file', terms_version=terms_version, label=label)


def import_cookies_file(platform: Platform, path, *, terms_version: Optional[str], label=None) -> SaveResult:
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        raise SignInError(f'Could not read the file: {exc.strerror or exc}') from exc
    return import_cookies_text(platform, text, terms_version=terms_version, label=label)


def rename(platform: Platform, label: Optional[str]) -> None:
    """Name a sign-in, so status reads "Signed in as <label>"."""
    meta = read_meta(platform)
    if not meta:
        return
    meta['label'] = label
    _write_meta(platform, meta)


# Setup files that are not a sign-in: the user's own Google OAuth client.
KEEP_ON_REMOVE = {'client_secrets.json'}


def remove(platform: Platform) -> None:
    """Delete every saved sign-in file for platform: cookies, sign-in record,
    Instaloader sessions, Drive OAuth tokens. Downloaded files are not touched."""
    for path in folder(platform).iterdir():
        if path.name in KEEP_ON_REMOVE:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    _log.info('Removed %s sign-in', platform.key)


def remove_all() -> None:
    """Delete saved sessions and cookies for every platform."""
    for platform in PLATFORMS:
        remove(platform)
    _log.info('Removed all sign-ins')


def _legacy_instagram_user(platform: Platform) -> Optional[str]:
    """Username of an Instaloader session saved by older versions, if any."""
    if platform.key != 'instagram':
        return None
    meta = session_store.read_json(_folder_name(platform), 'meta.json') or {}
    filename = meta.get('filename')
    if meta.get('username') and filename and (folder(platform) / filename).is_file():
        return meta['username']
    return None


# --- browsers -----------------------------------------------------------------

class _YtDlpLogger:
    """Route yt-dlp's cookie extractor messages to logging."""

    def debug(self, msg, *args, **kwargs):
        _log.debug('%s', msg)

    def info(self, msg, *args, **kwargs):
        _log.debug('%s', msg)

    def warning(self, msg, *args, **kwargs):
        _log.warning('%s', msg)

    def error(self, msg, *args, **kwargs):
        _log.error('%s', msg)


@dataclass
class BrowserProfile:
    name: str   # shown to users, e.g. "default-release"
    path: str   # passed to yt-dlp


def firefox_profiles() -> List[BrowserProfile]:
    """Firefox profiles that have a cookie database, newest first."""
    try:
        from yt_dlp.cookies import _firefox_browser_dirs
        roots = list(_firefox_browser_dirs())
    except Exception:  # private helper; fall back to the usual locations
        if sys.platform == 'win32':
            roots = [os.path.expandvars(r'%APPDATA%\Mozilla\Firefox\Profiles')]
        elif sys.platform == 'darwin':
            roots = [os.path.expanduser('~/Library/Application Support/Firefox/Profiles')]
        else:
            roots = [os.path.expanduser('~/.mozilla/firefox')]
    found = []
    for root in roots:
        try:
            entries = list(Path(root).iterdir())
        except OSError:
            continue
        for entry in entries:
            db = entry / 'cookies.sqlite'
            if db.is_file():
                name = entry.name.split('.', 1)[1] if '.' in entry.name else entry.name
                found.append((db.stat().st_mtime, BrowserProfile(name, str(entry))))
    return [profile for _mtime, profile in sorted(found, key=lambda item: item[0], reverse=True)]


def read_browser_cookies(browser: str, profile: Optional[str] = None) -> List[Cookie]:
    """Read cookies from a browser's store with yt-dlp's extractor."""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception as exc:  # pragma: no cover - yt-dlp is a required dependency
        raise SignInError('yt-dlp is not installed.') from exc
    try:
        jar = extract_cookies_from_browser(browser, profile, _YtDlpLogger())
    except Exception as exc:
        message = str(exc)
        name = BROWSER_NAMES.get(browser, browser)
        if 'lock' in message.lower():
            raise SignInError(f'Close {name} and try again.') from exc
        if 'could not find' in message.lower():
            raise SignInError(f'{name} does not seem to be installed for this user.') from exc
        if browser in CHROMIUM_BROWSERS and sys.platform == 'win32':
            raise SignInError(f'{name} locks its sign-ins on Windows, so this usually fails. '
                              'Use Firefox or a cookies file.') from exc
        raise SignInError(f'Could not read {name} sign-ins: {message}') from exc
    return cookies_from_jar(jar)


@dataclass
class BrowserSignIn:
    profile: BrowserProfile
    account_id: Optional[str]


def find_firefox_signins(platform: Platform) -> List[BrowserSignIn]:
    """Firefox profiles that hold a sign-in for platform. Reads cookies only; changes nothing."""
    results = []
    for profile in firefox_profiles():
        try:
            cookies = filter_for(platform, read_browser_cookies('firefox', profile.path))
        except SignInError as exc:
            _log.info('Skipping Firefox profile %s: %s', profile.name, exc)
            continue
        found, _expires, expired = find_markers(platform, cookies)
        if found and not expired:
            results.append(BrowserSignIn(profile, account_id(platform, cookies)))
    return results


def import_from_browser(
    platform: Platform,
    browser: str,
    profile: Optional[BrowserProfile] = None,
    *,
    terms_version: Optional[str],
    label=None,
) -> SaveResult:
    cookies = read_browser_cookies(browser, profile.path if profile else None)
    return save_signin(
        platform, cookies, method='browser', terms_version=terms_version,
        browser=browser, profile=profile.path if profile else None, label=label,
    )


def refresh_from_browser(platform: Platform) -> bool:
    """Re-read a browser sign-in so a run uses the browser's current cookies.

    Returns True when refreshed. Failures are logged and the stored copy is
    kept, so a closed or locked browser does not stop the run.
    """
    meta = read_meta(platform)
    if meta.get('method') != 'browser':
        return False
    try:
        cookies = filter_for(platform, read_browser_cookies(meta.get('browser') or 'firefox', meta.get('profile')))
    except SignInError as exc:
        _log.warning('Could not refresh %s sign-in from the browser: %s', platform.key, exc)
        return False
    found, expires, _expired = find_markers(platform, cookies)
    if not found:
        _log.warning('The browser no longer has a %s sign-in; keeping the stored copy', platform.key)
        return False
    session_store._atomic_write(cookie_path(platform), format_cookies_txt(cookies).encode('utf-8'))
    meta['marker_expires_at'] = _iso(expires)
    meta['account_id'] = account_id(platform, cookies) or meta.get('account_id')
    _write_meta(platform, meta)
    return True


# --- status -------------------------------------------------------------------

@dataclass
class Status:
    platform: Platform
    state: str       # not_needed | not_set_up | ready | expires_soon | expired | rejected | unknown
    text: str
    glyph: str       # never the only cue: text always says the same thing
    tone: str        # text-2 | success | warning | error
    method: Optional[str] = None
    account_id: Optional[str] = None

    @property
    def signed_in(self) -> bool:
        return self.state in ('ready', 'expires_soon', 'unknown')

    @property
    def needs_attention(self) -> bool:
        return self.state in ('expires_soon', 'expired', 'rejected')


def method_label(meta: Dict) -> Optional[str]:
    method = meta.get('method')
    if method == 'browser':
        return BROWSER_NAMES.get(meta.get('browser'), meta.get('browser') or 'browser')
    return METHOD_LABELS.get(method) or ('cookies file' if method is None else method)


def status(key_or_platform, now: Optional[float] = None) -> Status:
    platform = key_or_platform if isinstance(key_or_platform, Platform) else _platform(key_or_platform)
    now = time.time() if now is None else now
    meta = read_meta(platform)
    cookies = read_cookies(platform)
    if not cookies:
        legacy_user = _legacy_instagram_user(platform)
        if legacy_user:
            return Status(platform, 'ready', f'Signed in as {legacy_user} via saved session', '✓', 'success',
                          'password', None)
        if platform.signin_need == 'optional':
            return Status(platform, 'not_needed', 'Not needed for public posts', '', 'text-2')
        return Status(platform, 'not_set_up', 'Not set up', '', 'text-2')

    via = method_label(meta)
    found, expires, expired = find_markers(platform, cookies, now)
    acct = meta.get('account_id') or (account_id(platform, cookies) if found else None)
    if meta.get('label'):
        who = f' as {meta["label"]}'
    elif acct:
        who = f' (account {acct[:4]}...)' if len(acct) > 4 else f' (account {acct})'
    else:
        who = ''

    if not found:
        if not meta:
            # a cookies.txt with no sign-in in it, for example visitor
            # cookies saved by older versions: treat as not signed in
            if platform.signin_need == 'optional':
                return Status(platform, 'not_needed', 'Not needed for public posts', '', 'text-2')
            return Status(platform, 'not_set_up', 'Not set up', '', 'text-2')
        return Status(platform, 'unknown', f'Sign-in status unknown (via {via})', '?', 'warning', meta.get('method'), acct)
    if expired:
        return Status(platform, 'expired', 'Signed out. Sign in again', '✕', 'error', meta.get('method'), acct)

    # a success clears last_rejected_at, so any value means the last
    # download that used this sign-in was refused
    if meta.get('last_rejected_at'):
        return Status(platform, 'rejected', 'The site rejected this sign-in. Sign in again', '✕', 'error',
                      meta.get('method'), acct)
    if expires and expires - now < EXPIRES_SOON_DAYS * 86400:
        days = max(1, round((expires - now) / 86400))
        return Status(platform, 'expires_soon', f'Expires in {days} day{"s" if days != 1 else ""}. Refresh', '!',
                      'warning', meta.get('method'), acct)
    return Status(platform, 'ready', f'Signed in{who} via {via}', '✓', 'success', meta.get('method'), acct)


def all_statuses(now: Optional[float] = None) -> List[Status]:
    return [status(p, now) for p in PLATFORMS]


def summary(statuses: Optional[List[Status]] = None) -> str:
    """Header line: "3 ready", "1 needs attention", or "" when nothing is set up."""
    statuses = all_statuses() if statuses is None else statuses
    attention = sum(1 for s in statuses if s.needs_attention)
    if attention:
        return f'{attention} need{"s" if attention == 1 else ""} attention'
    ready = sum(1 for s in statuses if s.state in ('ready', 'unknown'))
    return f'{ready} ready' if ready else ''


# --- wiring for downloads -----------------------------------------------------

def cookie_file_for_download(handler_or_key: str) -> Optional[Path]:
    """The cookies.txt a download should use, or None when there is no sign-in."""
    try:
        platform = _platform(handler_or_key)
    except KeyError:
        return None
    path = cookie_path(platform)
    if not path.is_file() or not status(platform).signed_in:
        return None
    return path


def save_refreshed(handler_or_key: str, jar) -> bool:
    """Write cookies a site refreshed during a download back to the store.

    Keeps only the platform's own domains, so the file does not collect CDN
    and tracker cookies over time, and never writes a jar that lost the
    sign-in. Returns True when written.
    """
    platform = _platform(handler_or_key)
    cookies = filter_for(platform, cookies_from_jar(jar))
    found, expires, _expired = find_markers(platform, cookies)
    if not found:
        return False
    session_store._atomic_write(cookie_path(platform), format_cookies_txt(cookies).encode('utf-8'))
    meta = read_meta(platform)
    if meta:
        meta['marker_expires_at'] = _iso(expires)
        _write_meta(platform, meta)
    return True


def writes_back(handler_or_key: str) -> bool:
    """Whether yt-dlp may save refreshed cookies into the stored file.

    Not for browser sign-ins: those are re-read from the browser every run,
    and the browser stays the source of truth.
    """
    try:
        platform = _platform(handler_or_key)
    except KeyError:
        return False
    return read_meta(platform).get('method') != 'browser'


_AUTH_HINTS = ('login required', 'log in', 'sign in', 'signed in', 'authenticat', 'cookies', 'private',
               'checkpoint', 'challenge_required', 'not logged')


# A platform limiting this computer is not a sign-in problem, even when its
# message mentions signing in or cookies (YouTube: "Sign in to confirm you're
# not a bot ... use --cookies"). The app pauses for these and never suggests
# signing in to get past them (brief 0.8 and 0.11).
_BLOCK_HINTS = ('not a bot', 'rate-limit', 'rate limit', 'too many requests', 'http error 429')


def looks_like_block(message: str) -> bool:
    message = (message or '').lower()
    return any(hint in message for hint in _BLOCK_HINTS)


def looks_like_auth_error(message: str) -> bool:
    message = (message or '').lower()
    if looks_like_block(message):
        return False
    return any(hint in message for hint in _AUTH_HINTS)


def record_result(handler_or_key: str, ok: bool, error_text: str = '') -> None:
    """Remember whether the last download with this sign-in worked.

    Only touches platforms that have a sign-in, and only marks a sign-in
    rejected for errors that look like the site refused it.
    """
    try:
        platform = _platform(handler_or_key)
    except KeyError:
        return
    meta = read_meta(platform)
    if not meta or not cookie_path(platform).is_file():
        return
    if ok:
        meta['last_ok_at'] = _now_iso()
        meta['last_rejected_at'] = None  # the site accepts it again
    elif looks_like_auth_error(error_text):
        meta['last_rejected_at'] = _now_iso()
    else:
        return
    _write_meta(platform, meta)


def platforms_missing_signin(urls: Iterable[str]) -> List[Platform]:
    """Platforms in urls that often need a sign-in and have none ready, in link order."""
    from .detect import platform_for_url

    missing = []
    seen = set()
    for url in urls:
        platform = platform_for_url(url)
        if not platform or platform.key in seen or platform.signin_need != 'often':
            continue
        seen.add(platform.key)
        if status(platform).state not in ('ready', 'unknown'):
            missing.append(platform)
    return missing


def preflight_warnings(urls: Iterable[str]) -> List[str]:
    """One informational line per platform from platforms_missing_signin. Never blocks a run."""
    return [f'{p.name} may refuse some of these links without a sign-in.' for p in platforms_missing_signin(urls)]


__all__ = [
    'BROWSERS', 'BrowserProfile', 'BrowserSignIn', 'Cookie', 'SaveResult', 'SignInError', 'Status',
    'account_id', 'all_statuses', 'cookie_file_for_download', 'cookie_path', 'cookies_from_jar',
    'filter_for', 'find_firefox_signins', 'find_markers', 'firefox_profiles', 'format_cookies_txt',
    'import_cookies_file', 'import_cookies_text', 'import_from_browser', 'looks_like_auth_error',
    'needs_terms_ack', 'parse_cookies_txt', 'preflight_warnings', 'read_cookies', 'read_meta',
    'looks_like_block', 'record_result', 'refresh_from_browser', 'remove', 'rename', 'save_refreshed', 'remove_all', 'save_signin', 'status', 'summary',
    'writes_back',
]
