"""One row per supported platform.

Adding a platform means adding a row here (and a handler in core.py); the
link detection in detect.py reads everything else from this table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Terms:
    """What the platform's own terms say about automated downloading.

    The notice is shown before the first sign-in and must be acknowledged;
    a new version asks again. Versions are the effective dates of the terms
    read for the redesign brief (2026-10-09). Recheck them when terms change.
    """
    version: str
    url: str
    notice: str
    account_advice: str


@dataclass(frozen=True)
class Platform:
    key: str                       # stable internal id, e.g. "x"
    name: str                      # shown to users, e.g. "X (Twitter)"
    handler: str                   # Downloader handler name in core.py
    domains: Tuple[str, ...]       # matched exactly or as a dot-suffix
    hint_aliases: Tuple[str, ...]  # scraper group names that mean this platform
    # --- sign-in ---------------------------------------------------------
    # Cookies kept from an import: these domains and their subdomains only.
    cookie_domains: Tuple[str, ...] = ()
    # Signed in when every group has at least one of its cookies present.
    markers: Tuple[Tuple[str, ...], ...] = ()
    account_cookie: Optional[str] = None
    # "often": many posts need a sign-in, so warn before a run.
    # "optional": public posts work without one.
    signin_need: str = 'optional'
    terms: Optional[Terms] = None
    # "Sign in here": the page the in-app window opens. None where the site
    # blocks sign-in inside embedded windows (Google: YouTube and Drive).
    web_signin_url: Optional[str] = None
    # Hosts the sign-in window may show without an "Unexpected page" warning,
    # besides cookie_domains (for example Threads signs in through Instagram).
    web_signin_hosts: Tuple[str, ...] = ()

    def expected_signin_host(self, host: str) -> bool:
        host = (host or '').lower()
        return any(host == d or host.endswith('.' + d) for d in self.cookie_domains + self.web_signin_hosts)


_LOSE_ACCOUNT = 'If you sign in, use an account you can afford to lose. This does not make automated use allowed.'

PLATFORMS: Tuple[Platform, ...] = (
    Platform('youtube', 'YouTube', 'YouTube',
             ('youtube.com', 'youtu.be', 'youtube-nocookie.com', 'youtubekids.com'),
             ('youtube',),
             cookie_domains=('youtube.com',),
             markers=(('SAPISID', '__Secure-3PAPISID'), ('LOGIN_INFO',)),
             terms=Terms('2022-01-05', 'https://www.youtube.com/t/terms',
                         "YouTube's terms do not allow downloading without permission unless the law allows it "
                         'where you live. Signing in links downloads to your Google account.',
                         _LOSE_ACCOUNT)),
    Platform('x', 'X (Twitter)', 'Twitter',
             ('x.com', 'twitter.com', 'fxtwitter.com', 'vxtwitter.com', 'fixupx.com', 'fixvx.com'),
             ('twitter',),
             cookie_domains=('x.com', 'twitter.com'),
             markers=(('auth_token',),),
             account_cookie='twid',
             signin_need='often',
             web_signin_url='https://x.com/i/flow/login',
             terms=Terms('2026-10-09', 'https://x.com/en/tos',
                         "X's terms do not allow scraping without written permission. "
                         'Using your account here can get it restricted.',
                         _LOSE_ACCOUNT)),
    Platform('instagram', 'Instagram', 'Instagram',
             ('instagram.com', 'instagr.am', 'ddinstagram.com'),
             ('instagram',),
             cookie_domains=('instagram.com',),
             markers=(('sessionid',),),
             account_cookie='ds_user_id',
             signin_need='often',
             web_signin_url='https://www.instagram.com/accounts/login/',
             web_signin_hosts=('facebook.com',),
             terms=Terms('2025-01-01', 'https://help.instagram.com/581066165581870',
                         "Instagram's terms do not allow automated collection, including while signed in. "
                         'Using your account here can get it restricted or banned.',
                         _LOSE_ACCOUNT)),
    Platform('threads', 'Threads', 'Threads',
             ('threads.net', 'threads.com'),
             ('threads',),
             cookie_domains=('threads.net', 'threads.com'),
             markers=(('sessionid',),),
             web_signin_url='https://www.threads.net/login',
             web_signin_hosts=('instagram.com',),
             terms=Terms('2026-01-21', 'https://help.instagram.com/769983657850450',
                         "Instagram's terms do not allow automated collection, including while signed in. "
                         'Using your account here can get it restricted or banned.',
                         _LOSE_ACCOUNT)),
    Platform('tiktok', 'TikTok', 'TikTok',
             ('tiktok.com', 'douyin.com'),
             ('tiktok', 'douyin'),
             cookie_domains=('tiktok.com',),
             markers=(('sessionid',),),
             web_signin_url='https://www.tiktok.com/login',
             terms=Terms('2026-07', 'https://www.tiktok.com/legal/page/us/terms-of-service/en',
                         "TikTok's terms allow downloading only through TikTok's own features or with written "
                         'approval. Using your account here can get it restricted.',
                         _LOSE_ACCOUNT)),
    Platform('facebook', 'Facebook', 'Facebook',
             ('facebook.com', 'fb.com', 'fb.watch', 'fbcdn.net'),
             ('facebook',),
             cookie_domains=('facebook.com',),
             markers=(('c_user',), ('xs',)),
             account_cookie='c_user',
             signin_need='often',
             web_signin_url='https://www.facebook.com/login/',
             terms=Terms('2025-01-01', 'https://www.facebook.com/terms.php',
                         "Facebook's terms do not allow automated data collection without permission, and allow "
                         'only one account per person, in your real name. Using your account here can get it '
                         'restricted.',
                         'Facebook allows one account per person. Do not create another account for this app.')),
    Platform('reddit', 'Reddit', 'Reddit',
             ('reddit.com', 'redd.it'),
             ('reddit',),
             cookie_domains=('reddit.com',),
             markers=(('reddit_session', 'token_v2'),),
             web_signin_url='https://www.reddit.com/login/',
             terms=Terms('2026-07-01', 'https://redditinc.com/policies/user-agreement',
                         "Reddit's terms do not allow scraping without written consent. "
                         'Using your account here can get it restricted.',
                         _LOSE_ACCOUNT)),
    Platform('gdrive', 'Google Drive', 'Google Drive',
             ('drive.google.com', 'docs.google.com', 'googleusercontent.com'),
             ('googledrive', 'google drive', 'gdrive'),
             cookie_domains=('google.com',),
             markers=(('SID', '__Secure-1PSID'),)),
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


__all__ = ['Terms', 'Platform', 'PLATFORMS', 'BY_KEY', 'BY_HANDLER', 'for_host', 'all_domains']
