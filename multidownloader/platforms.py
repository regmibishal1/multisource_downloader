"""One row per supported platform.

Adding a platform means adding a row here (and a handler in core.py); the
link detection in detect.py reads everything else from this table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Platform:
    key: str                       # stable internal id, e.g. "x"
    name: str                      # shown to users, e.g. "X (Twitter)"
    handler: str                   # Downloader handler name in core.py
    domains: Tuple[str, ...]       # matched exactly or as a dot-suffix
    hint_aliases: Tuple[str, ...]  # scraper group names that mean this platform


PLATFORMS: Tuple[Platform, ...] = (
    Platform('youtube', 'YouTube', 'YouTube',
             ('youtube.com', 'youtu.be', 'youtube-nocookie.com', 'youtubekids.com'),
             ('youtube',)),
    Platform('x', 'X (Twitter)', 'Twitter',
             ('x.com', 'twitter.com', 'fxtwitter.com', 'vxtwitter.com', 'fixupx.com', 'fixvx.com'),
             ('twitter',)),
    Platform('instagram', 'Instagram', 'Instagram',
             ('instagram.com', 'instagr.am', 'ddinstagram.com'),
             ('instagram',)),
    Platform('threads', 'Threads', 'Threads',
             ('threads.net', 'threads.com'),
             ('threads',)),
    Platform('tiktok', 'TikTok', 'TikTok',
             ('tiktok.com', 'douyin.com'),
             ('tiktok', 'douyin')),
    Platform('facebook', 'Facebook', 'Facebook',
             ('facebook.com', 'fb.com', 'fb.watch', 'fbcdn.net'),
             ('facebook',)),
    Platform('reddit', 'Reddit', 'Reddit',
             ('reddit.com', 'redd.it'),
             ('reddit',)),
    Platform('gdrive', 'Google Drive', 'Google Drive',
             ('drive.google.com', 'docs.google.com', 'googleusercontent.com'),
             ('googledrive', 'google drive', 'gdrive')),
)

BY_KEY = {p.key: p for p in PLATFORMS}
BY_HANDLER = {p.handler: p for p in PLATFORMS}


def for_host(host: str) -> Optional[Platform]:
    """Return the platform whose domain equals host or is a dot-suffix of it."""
    host = (host or '').lower().rstrip('.')
    if not host:
        return None
    for platform in PLATFORMS:
        for domain in platform.domains:
            if host == domain or host.endswith('.' + domain):
                return platform
    return None


def all_domains() -> Tuple[str, ...]:
    return tuple(d for p in PLATFORMS for d in p.domains)


__all__ = ['Platform', 'PLATFORMS', 'BY_KEY', 'BY_HANDLER', 'for_host', 'all_domains']
