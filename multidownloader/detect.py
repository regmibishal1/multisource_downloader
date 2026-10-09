"""Find links in pasted text, normalize them, and work out their platform.

Everything here is a pure function (no network, no disk) so it can be tested
exhaustively. The normalized form is only used to spot duplicates; downloads
and records keep the URL the user gave.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import platforms
from .platforms import Platform

_log = logging.getLogger(__name__)

# --- extraction ---------------------------------------------------------------

_KNOWN_HOSTS = '|'.join(re.escape(d) for d in sorted(platforms.all_domains(), key=len, reverse=True))
_LINK_RE = re.compile(
    r'(?:'
    r'https?://[^\s<>"\'`\[\]{}]+'                      # any link with a scheme
    r'|(?<![\w.@/-])www\.[^\s<>"\'`\[\]{}]+'            # www.example.com/...
    r'|(?<![\w.@/-])(?:[a-z0-9-]+\.)*(?:' + _KNOWN_HOSTS + r')/[^\s<>"\'`\[\]{}]*'  # youtu.be/abc
    r')',
    re.IGNORECASE,
)
# Sentence punctuation, including the Devanagari danda used in Nepali text.
_TRAILING = '.,;:!?*\'"।॥。，'


def _trim(link: str) -> str:
    """Drop punctuation that belongs to the surrounding sentence, not the link."""
    while link:
        if link[-1] in _TRAILING:
            link = link[:-1]
        elif link[-1] == ')' and link.count(')') > link.count('('):
            link = link[:-1]  # "(see https://a.com/x)" or a Markdown link
        else:
            break
    return link


def extract_urls(text: str) -> List[str]:
    """Return every link in text, in order, with https:// added where it was missing."""
    found = []
    for match in _LINK_RE.finditer(text or ''):
        link = _trim(match.group(0))
        if not link:
            continue
        if not re.match(r'https?://', link, re.IGNORECASE):
            link = 'https://' + link
        found.append(link)
    return found


# --- normalization ------------------------------------------------------------

_TRACKING_PARAMS = {
    'fbclid', 'gclid', 'igsh', 'igshid', 'si', 'ref', 'ref_src', 'ref_url',
    'feature', 'mibextid', 'rdid', 'share_id', 'is_from_webapp', 'sender_device',
    'usp', '_r', '_t',
}
_HOST_PREFIXES = ('www.', 'm.', 'mobile.')
_X_HOSTS = {'twitter.com', 'fxtwitter.com', 'vxtwitter.com', 'fixupx.com', 'fixvx.com'}
_INSTAGRAM_HOSTS = {'instagr.am', 'ddinstagram.com'}
_REDDIT_HOSTS = {'old.reddit.com', 'new.reddit.com', 'np.reddit.com'}
# Parameters that do not change which YouTube video a link points at.
_YOUTUBE_DROP = {'t', 'start', 'list', 'index', 'pp', 'ab_channel'}

_YT_PATH_ID = re.compile(r'^/(?:shorts|embed|live|v)/([\w-]{6,})')
_X_STATUS = re.compile(r'^/(?:i/web|[^/]+)/status(?:es)?/(\d+)', re.IGNORECASE)
_IG_POST = re.compile(r'^/(?:[A-Za-z0-9_.]+/)?(p|reels?|tv)/([A-Za-z0-9_-]+)')


def normalize_url(url: str) -> str:
    """Return a canonical form of url for duplicate detection.

    Lowercases scheme and host, drops www./m./mobile., tracking parameters and
    fragments, and rewrites the share formats of YouTube, X and Instagram to
    one form, so the same post shared three ways compares equal.
    Unparseable input comes back stripped but otherwise unchanged.
    """
    url = (url or '').strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower().rstrip('.')
    except ValueError:
        return url
    if not host:
        return url
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix) and host.count('.') > 1:
            host = host[len(prefix):]
            break
    try:
        if parts.port:
            host = f'{host}:{parts.port}'
    except ValueError:
        pass
    scheme = (parts.scheme or 'https').lower()
    if scheme == 'http':
        scheme = 'https'
    path = parts.path or '/'
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith('utm_') and k.lower() not in _TRACKING_PARAMS
    ]

    if host in _X_HOSTS:
        host = 'x.com'
    if host in _INSTAGRAM_HOSTS:
        host = 'instagram.com'
    if host in _REDDIT_HOSTS:
        host = 'reddit.com'

    if host == 'youtu.be':
        video_id = path.strip('/').split('/')[0]
        if video_id:
            host, path = 'youtube.com', '/watch'
            query = [('v', video_id)] + [(k, v) for k, v in query if k != 'v']
    elif host in ('youtube.com', 'youtube-nocookie.com', 'youtubekids.com'):
        host = 'youtube.com'
        match = _YT_PATH_ID.match(path)
        if match:
            path = '/watch'
            query = [('v', match.group(1))] + [(k, v) for k, v in query if k != 'v']
    if host == 'youtube.com' and path == '/watch':
        query = [(k, v) for k, v in query if k not in _YOUTUBE_DROP]
    elif host == 'x.com':
        query = [(k, v) for k, v in query if k not in ('s', 't')]
        match = _X_STATUS.match(path)
        if match:
            # The status id alone identifies a post (x.com/i/status/<id> opens
            # it); usernames change and differ in case between shares.
            path = f'/i/status/{match.group(1)}'
    elif host == 'instagram.com':
        match = _IG_POST.match(path)
        if match:
            kind = 'reel' if match.group(1).startswith('reel') else match.group(1)
            path = f'/{kind}/{match.group(2)}'
            query = []

    if len(path) > 1:
        path = path.rstrip('/')
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ''))


# --- detection ----------------------------------------------------------------

def platform_for_url(url: str) -> Optional[Platform]:
    try:
        return platforms.for_host(urlsplit(url.strip()).hostname or '')
    except ValueError:
        return None


def platform_for_hint(hint: str) -> Optional[Platform]:
    """Map a scraper group name ("twitter", "Google Drive") or a domain ("x.com")."""
    value = (hint or '').strip().lower()
    if not value:
        return None
    if '.' in value and ' ' not in value:
        return platform_for_url(value if '//' in value else 'https://' + value)
    for platform in platforms.PLATFORMS:
        if any(alias in value for alias in platform.hint_aliases):
            return platform
    return None


def detect_platform(url: str, hint: str = '') -> Optional[Platform]:
    """Return the platform for url. The URL decides; hint is used for unknown hosts."""
    from_url = platform_for_url(url)
    if from_url:
        from_hint = platform_for_hint(hint)
        if from_hint and from_hint != from_url:
            _log.warning('Source hint %r disagrees with URL %s; using %s', hint, url, from_url.name)
        return from_url
    return platform_for_hint(hint)


# --- dedupe and summary -------------------------------------------------------

def unique_urls(urls: Iterable[str]) -> Tuple[List[str], int]:
    """Keep the first of each link by normalized form. Returns (links, duplicates removed)."""
    seen = set()
    kept = []
    duplicates = 0
    for url in urls:
        key = normalize_url(url)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(url)
    return kept, duplicates


def summarize(urls: Iterable[str], duplicates: int = 0) -> str:
    """One line such as "12 links found: Instagram 5, X (Twitter) 4. 2 duplicates removed."."""
    urls = list(urls)
    if not urls:
        return 'No links found yet.'
    counts = Counter()
    unsupported = 0
    for url in urls:
        platform = platform_for_url(url)
        if platform:
            counts[platform.name] += 1
        else:
            unsupported += 1
    noun = 'link' if len(urls) == 1 else 'links'
    parts = ', '.join(f'{name} {n}' for name, n in counts.most_common())
    line = f'{len(urls)} {noun} found' + (f': {parts}.' if parts else '.')
    if duplicates:
        line += f' {duplicates} duplicate{"s" if duplicates != 1 else ""} removed.'
    if unsupported:
        line += f' {unsupported} not supported.'
    return line


__all__ = [
    'extract_urls',
    'normalize_url',
    'platform_for_url',
    'platform_for_hint',
    'detect_platform',
    'unique_urls',
    'summarize',
]
