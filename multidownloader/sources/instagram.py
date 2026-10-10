"""Instagram handler using Instaloader with local session persistence."""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

try:
    from instaloader import Instaloader, Post
    INSTALOADER_AVAILABLE = True
except Exception:  # pragma: no cover - instaloader optional
    Instaloader = None
    Post = None
    INSTALOADER_AVAILABLE = False

from .. import session_store, signins
from ..platforms import BY_KEY
from ..signins import SignInError

INSTAGRAM_PASSWORD_NOTE = ('Not recommended. Instagram is more likely to lock accounts that sign in from '
                           'scripts, and its terms do not allow automated collection. Your password goes only to '
                           'Instagram and is not saved.')
CHECKPOINT_MESSAGE = ('Instagram wants you to confirm this sign-in. '
                      'Open Instagram in your browser, approve it, then try again.')

# Matches /p/, /reel/, /reels/ and /tv/ links, with or without a leading
# /<username>/ segment (a common share format).
SHORTCODE_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)


def parse_shortcode(url):
    match = SHORTCODE_RE.search(url.split('?')[0])
    return match.group(1) if match else None


INSTALOADER_MISSING = 'Instaloader is not installed. Install it with: python -m pip install ".[instagram]"'


class InstagramHandler:
    """Instagram posts through Instaloader (a backends.base.Backend)."""

    name = 'instaloader'
    SESSION_SOURCE = 'Instagram'
    META_FILENAME = 'meta.json'

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self._signin_loader = None
        session_store.ensure_session_dir(self.SESSION_SOURCE)

    def available(self):
        return (True, '') if INSTALOADER_AVAILABLE else (False, INSTALOADER_MISSING)

    # ------------------------------------------------------------------
    # Password sign-in (last resort; see signins.py for the other methods)
    def interactive_auth(self, root=None):
        """Open the Instagram sign-in sheet. Kept for Downloader.authenticate.

        Returns an Instaloader using the new sign-in, or None if cancelled.
        """
        import tkinter as tk

        from ..gui.signin_sheet import SignInSheet

        parent = root or tk._default_root
        sheet = SignInSheet(parent, BY_KEY['instagram'])
        parent.wait_window(sheet)
        self._signin_loader = None
        return self._loader_from_signin() if signins.status('instagram').signed_in else None

    def password_login(self, username, password, ask_code):
        """Sign in with a username and password and return an Instaloader.

        ask_code() is called when Instagram asks for a two-factor code and
        returns the code or None to cancel. Raises SignInError with a message
        to show the user.
        """
        if not INSTALOADER_AVAILABLE:
            raise SignInError(INSTALOADER_MISSING)
        from instaloader.exceptions import (
            BadCredentialsException,
            ConnectionException,
            LoginException,
            TwoFactorAuthRequiredException,
        )

        loader = Instaloader()
        try:
            try:
                loader.login(username, password)
            except TwoFactorAuthRequiredException:
                code = ask_code()
                if not code:
                    raise SignInError('Sign-in cancelled.')
                try:
                    loader.two_factor_login(code)
                except BadCredentialsException as exc:
                    raise SignInError('That code did not work. Check it and try again.') from exc
        except SignInError:
            raise
        except BadCredentialsException as exc:
            raise SignInError('Wrong username or password.') from exc
        except (ConnectionException, LoginException) as exc:
            message = str(exc).lower()
            if 'checkpoint' in message or 'challenge' in message:
                raise SignInError(CHECKPOINT_MESSAGE) from exc
            if 'locked' in message or 'suspended' in message:
                raise SignInError('This account is locked by Instagram. Use it in your browser to unlock it.') from exc
            raise SignInError(f'Instagram sign-in failed: {exc}') from exc
        self.logger.info('Instagram password sign-in worked for %s', username)
        return loader

    def password_signin(self, auth, terms_version):
        """Password sign-in that asks through an auth.AuthProvider; returns the SaveResult.

        Raises SignInError when there is no one to ask or the sign-in fails.
        """
        creds = auth.request_credentials('Instagram', INSTAGRAM_PASSWORD_NOTE)
        if creds is None:
            raise SignInError('No username and password given. Use a browser sign-in instead: '
                              'multidownloader-signin import instagram --browser firefox')
        loader = self.password_login(creds.username, creds.password, lambda: auth.request_2fa_code('Instagram'))
        return self.save_password_signin(loader, creds.username, terms_version)

    def save_password_signin(self, loader, username, terms_version):
        """Store the session's cookies in the shared sign-in store."""
        platform = BY_KEY['instagram']
        cookies = signins.cookies_from_jar(loader.context._session.cookies)
        result = signins.save_signin(platform, cookies, method='password', terms_version=terms_version,
                                     label=username)
        self._remember_loader_session(loader, username)
        self._signin_loader = None
        return result

    # ------------------------------------------------------------------
    def download(self, url, out_dir, options):
        if not INSTALOADER_AVAILABLE:
            raise RuntimeError(INSTALOADER_MISSING)

        auth_mode = options.get('auth', 'auto')
        loader = options.get('instaloader_client') or options.get('instaloader')
        loader = self._ensure_loader(loader, auth_mode)

        os.makedirs(out_dir, exist_ok=True)
        cancel = options.get('cancel_event')
        if cancel is not None and cancel.is_set():
            raise RuntimeError('Stopped before it started.')
        shortcode = parse_shortcode(url)
        if not shortcode:
            raise ValueError('Invalid Instagram link')

        # Point Instaloader at out_dir instead of changing the process working
        # directory, which is not thread safe. Braces in the path are escaped
        # because Instaloader treats the pattern as a format string. Layout:
        # instagram/<account>/<YYYY-MM-DD>_<shortcode>[_<n>].<ext>; Instaloader
        # adds _1, _2... for the items of a carousel and names the JSON alike.
        escaped_dir = str(out_dir).replace('{', '{{').replace('}', '}}')
        previous_pattern = loader.dirname_pattern
        previous_filename = loader.filename_pattern
        loader.dirname_pattern = os.path.join(escaped_dir, 'instagram', '{profile}')
        loader.filename_pattern = '{date_utc:%Y-%m-%d}_{shortcode}'
        # keep the post's JSON (caption, owner, time) readable next to the media
        loader.save_metadata = True
        loader.compress_json = False
        try:
            attempts = 2
            for attempt in range(1, attempts + 1):
                try:
                    post = Post.from_shortcode(loader.context, shortcode)
                    loader.download_post(post, target=shortcode)
                    self.logger.info('Downloaded Instagram post: %s', shortcode)
                    return True
                except Exception as exc:
                    message = str(exc)
                    self.logger.warning('Attempt %s failed for %s: %s', attempt, shortcode, message)
                    if 'login' in message.lower() or 'authenticate' in message.lower():
                        raise RuntimeError('This post needs you to be signed in to Instagram.')
                    if attempt < attempts:
                        time.sleep(1)
                        continue
                    raise RuntimeError(f'Failed to download Instagram post {shortcode}: {message}')
        finally:
            loader.dirname_pattern = previous_pattern
            loader.filename_pattern = previous_filename

    # ------------------------------------------------------------------
    def _ensure_loader(self, loader, auth_mode: str):
        if loader:
            return loader

        if auth_mode in ('auto', 'authenticated'):
            from_signin = self._loader_from_signin()
            if from_signin:
                return from_signin
            cached = self._load_cached_client()
            if cached:
                return cached
            if auth_mode == 'authenticated':
                raise RuntimeError('No saved Instagram session found. Run Instagram authentication first.')

        return Instaloader()

    def _loader_from_signin(self):
        """Build an Instaloader from the shared cookie sign-in, checked once per handler."""
        path = signins.cookie_file_for_download('Instagram')
        if not path:
            return None
        stamp = path.stat().st_mtime
        if self._signin_loader and self._signin_loader[0] == stamp:
            return self._signin_loader[1]
        user_agent = signins.user_agent_for('Instagram')
        loader = Instaloader(user_agent=user_agent) if user_agent else Instaloader()
        jar = loader.context._session.cookies
        for c in signins.read_cookies(BY_KEY['instagram']):
            jar.set(c.name, c.value, domain=c.domain, path=c.path)
        # the documented way to turn imported cookies into a session
        username = loader.test_login()
        if not username:
            signins.record_result('Instagram', False, 'not logged in')
            raise RuntimeError('Instagram did not accept your sign-in. Sign in again.')
        loader.context.username = username
        self._signin_loader = (stamp, loader)
        self.logger.info('Using the stored Instagram sign-in for %s', username)
        return loader

    def _cached_session_info(self):
        meta = session_store.read_json(self.SESSION_SOURCE, self.META_FILENAME) or {}
        username = meta.get('username')
        filename = meta.get('filename')
        session_path = session_store.path_for(self.SESSION_SOURCE, filename) if filename else None
        return username, session_path

    def _load_cached_client(self):
        username, session_path = self._cached_session_info()
        if username and session_path and session_path.exists():
            loader = Instaloader()
            loader.load_session_from_file(username, session_path)
            return loader
        return None

    def _remember_loader_session(self, loader, username: str):
        try:
            dest = session_store.path_for(self.SESSION_SOURCE, f'{username}.session')
            loader.save_session_to_file(dest)
            session_store.write_json(self.SESSION_SOURCE, self.META_FILENAME, {
                'username': username,
                'filename': dest.name,
            })
            self.logger.info('Cached Instagram session for %s at %s', username, dest)
        except Exception as exc:
            self.logger.warning('Failed to persist Instagram session for %s: %s', username, exc)

    def _remember_external_session(self, username: str, source_path: Path):
        try:
            dest = session_store.path_for(self.SESSION_SOURCE, source_path.name)
            shutil.copy2(source_path, dest)
            session_store.write_json(self.SESSION_SOURCE, self.META_FILENAME, {
                'username': username,
                'filename': dest.name,
            })
            self.logger.info('Imported Instagram session for %s into %s', username, dest)
        except Exception as exc:
            self.logger.warning('Failed to import Instagram session %s: %s', source_path, exc)


__all__ = ['InstagramHandler', 'parse_shortcode']
