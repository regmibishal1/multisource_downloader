"""Turn download errors into a kind, a plain message and one next step (brief T9, F16, 0.8).

classify() looks at the exception and everything it wraps (handlers wrap
library errors; yt-dlp keeps the original in DownloadError.exc_info), then
at the text, in a fixed order, and returns an ErrorInfo. The raw text is
kept separately for the log and diagnostics; users see ``message``.

Blocks (rate limits, YouTube's bot check) are never presented as a sign-in
problem, and their action never suggests signing in (brief 0.11).
"""
from __future__ import annotations

import errno
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

# kinds
AUTH_REQUIRED = 'auth_required'
SIGNIN_REJECTED = 'signin_rejected'
CHECKPOINT = 'checkpoint'
ACCOUNT_LOCKED = 'account_locked'
OAUTH_EXPIRED = 'oauth_expired'
RATE_LIMITED = 'rate_limited'
BOT_CHECK = 'bot_check'
NOT_FOUND = 'not_found'
PRIVATE = 'private'
GEO_BLOCKED = 'geo_blocked'
NO_MEDIA = 'no_media'
UNSUPPORTED = 'unsupported'
NETWORK = 'network'
DISK = 'disk'
MISSING_COMPONENT = 'missing_component'
OUTDATED = 'outdated'
UNKNOWN = 'unknown'

# actions: one next step per error
FIX_SIGNIN = 'fix_signin'
OPEN_PLATFORM = 'open_platform'
RECONNECT = 'reconnect'
WAIT = 'wait'             # retried automatically later; nothing to do now
SLOW_DOWN = 'slow_down'
COPY_LINK = 'copy_link'
RETRY = 'retry'
CHANGE_FOLDER = 'change_folder'
HOW_TO_FIX = 'how_to_fix'
HOW_TO_UPDATE = 'how_to_update'
SHOW_DETAILS = 'show_details'

ACTION_LABELS = {
    FIX_SIGNIN: 'Fix sign-in', OPEN_PLATFORM: 'Open {platform}', RECONNECT: 'Reconnect', WAIT: None,
    SLOW_DOWN: 'Slow down', COPY_LINK: 'Copy link', RETRY: 'Retry', CHANGE_FOLDER: 'Change folder',
    HOW_TO_FIX: 'How to fix', HOW_TO_UPDATE: 'How to update', SHOW_DETAILS: 'Show details',
}


@dataclass(frozen=True)
class ErrorInfo:
    kind: str
    message: str           # shown to the user, exact wording from the brief
    action: Optional[str]  # one of the action constants, or None
    detail: str            # raw text for logs and diagnostics

    def action_label(self, platform_name: str = '') -> Optional[str]:
        label = ACTION_LABELS.get(self.action)
        return label.format(platform=platform_name or 'the site') if label else None

    @property
    def needs_signin(self) -> bool:
        """Whether offering the sign-in sheet is the right next step."""
        return self.action == FIX_SIGNIN


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.append(current.__cause__)
        stack.append(current.__context__)
        info = getattr(current, 'exc_info', None)  # yt-dlp DownloadError
        if isinstance(info, tuple) and len(info) > 1 and isinstance(info[1], BaseException):
            stack.append(info[1])


def _type_names(exc: Optional[BaseException]) -> List[str]:
    return [type(e).__name__ for e in _chain(exc)] if exc is not None else []


def _os_error(exc: Optional[BaseException]) -> Optional[OSError]:
    for e in _chain(exc) if exc is not None else ():
        if isinstance(e, OSError) and getattr(e, 'errno', None) in (errno.ENOSPC, errno.EACCES, errno.EPERM,
                                                                    errno.ENAMETOOLONG, errno.EROFS):
            return e
        if isinstance(e, OSError) and getattr(e, 'winerror', None) in (112, 206):  # disk full, path too long
            return e
    return None


# Messages written by this app's own handlers are already plain; keep them.
_OWN_MESSAGES: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("This file is private. Ask the owner to set sharing to 'Anyone with the link'", PRIVATE, FIX_SIGNIN),
    ('Your Google account does not have access to this file.', PRIVATE, COPY_LINK),
    ('Your Google connection expired.', OAUTH_EXPIRED, RECONNECT),
    ('Connect Google Drive in Sign-ins first.', AUTH_REQUIRED, FIX_SIGNIN),
    ('Instagram wants you to confirm this sign-in.', CHECKPOINT, OPEN_PLATFORM),
    ('Instagram did not accept your sign-in.', SIGNIN_REJECTED, FIX_SIGNIN),
    ('This post needs you to be signed in to', AUTH_REQUIRED, FIX_SIGNIN),
    ('Google Drive sign-in needs gdown 6 or newer', MISSING_COMPONENT, HOW_TO_UPDATE),
    ('The download finished but no file was saved.', NO_MEDIA, None),
)

_TEXT_RULES: Tuple[Tuple[str, str], ...] = (
    # order matters: earlier rules win
    (r"not a bot", BOT_CHECK),
    (r'checkpoint|challenge_required', CHECKPOINT),
    (r'account (has been )?(locked|suspended|disabled)|been suspended', ACCOUNT_LOCKED),
    (r'invalid_grant', OAUTH_EXPIRED),
    (r'http error 429|too many requests|rate[- ]limit|toomanyrequests|please wait a few minutes|'
     r'too many users have viewed', RATE_LIMITED),
    (r'available in your (country|region)|geo[- ]?restrict|blocked in your (country|region)', GEO_BLOCKED),
    (r'private video|this account is private|is private|privateprofilenotfollowed', PRIVATE),
    (r'login required|log in to|sign in to (confirm your age|view)|requires authentication|loginrequired|'
     r'not logged in|cookies? (are|is) (needed|required)|use --cookies', AUTH_REQUIRED),
    (r'javascript runtime|\bejs\b|yt-dlp-ejs|(ffmpeg|ffprobe|deno)( is)? not (installed|found)', MISSING_COMPONENT),
    (r'no video could be found|no video formats found|there.s no video in this|no media found', NO_MEDIA),
    (r'unsupported url', UNSUPPORTED),
    (r'http error 404|not found|video unavailable|this video is unavailable|has been removed|post not found|'
     r'does not exist|this tweet is unavailable|deleted', NOT_FOUND),
    (r'unable to extract|unable to download json metadata|failed to parse|signature extraction failed|'
     r'nsig extraction failed', OUTDATED),
    (r'getaddrinfo|name or service not known|timed out|timeout|connection (reset|refused|aborted)|'
     r'network is unreachable|temporary failure in name resolution|remote end closed|'
     r'connectionerror|ssl: |urlopen error', NETWORK),
    (r'http error 403', RATE_LIMITED),
)

_TYPE_RULES: Tuple[Tuple[str, str], ...] = (
    ('TwoFactorAuthRequiredException', AUTH_REQUIRED),
    ('TooManyRequestsException', RATE_LIMITED),
    ('LoginRequiredException', AUTH_REQUIRED),
    ('PrivateProfileNotFollowedException', PRIVATE),
    ('QueryReturnedNotFoundException', NOT_FOUND),
    ('ProfileNotExistsException', NOT_FOUND),
    ('GeoRestrictedError', GEO_BLOCKED),
    ('UnsupportedError', UNSUPPORTED),
    ('ExtractorError', None),  # the text decides
)


def _message(kind: str, platform: str, *, os_error: Optional[OSError] = None, component: str = '',
             wait: str = '') -> Tuple[str, Optional[str]]:
    p = platform or 'The site'
    if kind == AUTH_REQUIRED:
        return f'This post needs you to be signed in to {platform or "this site"}.', FIX_SIGNIN
    if kind == SIGNIN_REJECTED:
        return f'{p} did not accept your sign-in. Sign in again.', FIX_SIGNIN
    if kind == CHECKPOINT:
        return (f'{p} wants you to confirm this sign-in. Open {platform or "the site"} in your browser, '
                'approve it, then try again.'), OPEN_PLATFORM
    if kind == ACCOUNT_LOCKED:
        return f'This account is locked by {p}. Use it in your browser to unlock it.', OPEN_PLATFORM
    if kind == OAUTH_EXPIRED:
        return ('Your Google connection expired. This happens every 7 days while your Google Cloud project '
                'is in Testing status.'), RECONNECT
    if kind == BOT_CHECK:
        return 'YouTube is limiting this computer. Wait and try later with slower settings.', SLOW_DOWN
    if kind == RATE_LIMITED:
        when = f'Waiting {wait} before trying again.' if wait else 'Try again later.'
        return f'{p} is limiting requests. {when}', WAIT
    if kind == NOT_FOUND:
        return 'This post was deleted or the link is wrong.', COPY_LINK
    if kind == PRIVATE:
        return 'This post is private. Sign in with an account that can see it.', FIX_SIGNIN
    if kind == GEO_BLOCKED:
        return 'This post is blocked in your region.', COPY_LINK
    if kind == NO_MEDIA:
        return 'This post has no downloadable media.', None
    if kind == UNSUPPORTED:
        return 'This site is not supported yet.', COPY_LINK
    if kind == NETWORK:
        return 'Could not connect. Check your internet connection.', RETRY
    if kind == DISK:
        return f'Could not save the file: {_disk_reason(os_error)}.', CHANGE_FOLDER
    if kind == MISSING_COMPONENT:
        return f'{component or "A component"} is not installed. Some downloads need it.', HOW_TO_FIX
    if kind == OUTDATED:
        return f'{p} changed something and the downloader needs an update.', HOW_TO_UPDATE
    return 'Something went wrong. Details are in the log.', SHOW_DETAILS


def _disk_reason(exc: Optional[OSError]) -> str:
    if exc is None:
        return 'a disk error'
    if exc.errno == errno.ENOSPC or getattr(exc, 'winerror', None) == 112:
        return 'the drive is full'
    if exc.errno in (errno.EACCES, errno.EPERM):
        return 'no permission to write there'
    if exc.errno == errno.EROFS:
        return 'the drive is read-only'
    return 'the path is too long'


def _component(text: str) -> str:
    lowered = text.lower()
    if 'ffmpeg' in lowered or 'ffprobe' in lowered:
        return 'ffmpeg'
    if 'javascript' in lowered or 'ejs' in lowered or 'deno' in lowered:
        return 'Deno'
    return ''


def classify(error, *, platform: str = '', signed_in: bool = False, wait: str = '') -> ErrorInfo:
    """Classify an exception (or its text).

    platform: the platform's display name for messages. signed_in: whether a
    sign-in was used, which turns a sign-in error into "did not accept your
    sign-in". wait: "mm:ss" for rate limits the runner is waiting out.
    """
    exc = error if isinstance(error, BaseException) else None
    texts = [str(e) for e in _chain(exc)] if exc is not None else [str(error or '')]
    detail = texts[0] if texts else ''
    full = '\n'.join(t for t in texts if t)

    for prefix, kind, action in _OWN_MESSAGES:
        if prefix in full:
            own = next(t for t in texts if prefix in t)
            return ErrorInfo(kind, own.strip().splitlines()[0], action, detail)

    os_error = _os_error(exc)
    if os_error is not None:
        message, action = _message(DISK, platform, os_error=os_error)
        return ErrorInfo(DISK, message, action, detail)

    kind = None
    lowered = full.lower()
    for pattern, rule_kind in _TEXT_RULES:
        if re.search(pattern, lowered):
            kind = rule_kind
            break
    if kind is None:
        for name in _type_names(exc):
            for type_name, type_kind in _TYPE_RULES:
                if name == type_name and type_kind:
                    kind = type_kind
                    break
            if kind:
                break
    if kind is None and exc is not None and any(isinstance(e, (ConnectionError, TimeoutError)) for e in _chain(exc)):
        kind = NETWORK
    kind = kind or UNKNOWN

    # yt-dlp's Instagram message says "rate-limit reached or login required":
    # without a sign-in it is a sign-in problem, with one a limit
    if 'rate-limit reached or login required' in lowered:
        kind = RATE_LIMITED if signed_in else AUTH_REQUIRED
    if kind == AUTH_REQUIRED and signed_in:
        kind = SIGNIN_REJECTED
    if kind == UNSUPPORTED and platform:
        kind = OUTDATED  # a supported platform's link that yt-dlp no longer reads

    message, action = _message(kind, platform, component=_component(full), wait=wait)
    return ErrorInfo(kind, message, action, detail)


def group(results: Iterable[Tuple[str, ErrorInfo]]) -> List[Tuple[ErrorInfo, List[str]]]:
    """Group failures by kind and message, largest group first: [(info, [urls])]."""
    groups = {}
    for url, info in results:
        groups.setdefault((info.kind, info.message), (info, []))[1].append(url)
    return sorted(groups.values(), key=lambda item: -len(item[1]))


__all__ = ['ErrorInfo', 'classify', 'group', 'ACTION_LABELS']
