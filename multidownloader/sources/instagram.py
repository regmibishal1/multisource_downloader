"""Instagram handler using Instaloader with local session persistence."""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from instaloader import Instaloader, Post
    INSTALOADER_AVAILABLE = True
except Exception:  # pragma: no cover - instaloader optional
    Instaloader = None
    Post = None
    INSTALOADER_AVAILABLE = False

from .. import session_store


class InstagramHandler:
    SESSION_SOURCE = 'Instagram'
    META_FILENAME = 'meta.json'

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        session_store.ensure_session_dir(self.SESSION_SOURCE)

    # ------------------------------------------------------------------
    # Interactive authentication helpers
    def interactive_auth(self, root=None):
        """Open a Tk dialog to authenticate or load a saved session."""
        if not INSTALOADER_AVAILABLE:
            messagebox.showerror('Missing Dependency', 'Instaloader is required for Instagram authenticated downloads')
            self.logger.error('Instaloader not available for Instagram authentication')
            return None

        cached_user, cached_path = self._cached_session_info()
        if cached_user and cached_path and cached_path.exists():
            # Provide a quick option to reuse the cached session without prompting.
            try:
                loader = self._load_cached_client()
                if loader:
                    self.logger.info('Reusing cached Instagram session for user %s', cached_user)
                    return loader
            except Exception as exc:  # pragma: no cover - best effort
                self.logger.warning('Failed to use cached session automatically: %s', exc)

        parent = root or tk._default_root
        dlg = tk.Toplevel(parent)
        dlg.title('Instagram Login')
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text='Username:').grid(row=0, column=0, sticky='e', padx=(0, 8), pady=4)
        user_entry = ttk.Entry(frm)
        user_entry.grid(row=0, column=1, sticky='ew', pady=4)
        if cached_user:
            user_entry.insert(0, cached_user)

        ttk.Label(frm, text='Password (leave blank to use session file):').grid(row=1, column=0, sticky='e', padx=(0, 8), pady=4)
        pwd_entry = ttk.Entry(frm, show='*')
        pwd_entry.grid(row=1, column=1, sticky='ew', pady=4)
        frm.columnconfigure(1, weight=1)

        sess_var = tk.StringVar(value=str(cached_path) if cached_path else '')

        def pick_session():
            path = filedialog.askopenfilename(title='Select Instaloader session file')
            if path:
                sess_var.set(path)
                self.logger.info('Selected Instagram session file: %s', path)

        ttk.Button(frm, text='Select Session File', command=pick_session).grid(row=2, column=0, columnspan=2, pady=4)
        ttk.Label(frm, textvariable=sess_var, anchor='w').grid(row=3, column=0, columnspan=2, sticky='w', pady=2)

        res = {}

        def submit():
            res['username'] = user_entry.get().strip()
            res['password'] = pwd_entry.get()
            res['session_path'] = sess_var.get().strip()
            dlg.destroy()

        ttk.Button(frm, text='Continue', command=submit).grid(row=4, column=0, columnspan=2, pady=(12, 4))

        frm.pack_propagate(False)
        dlg.grab_set()
        parent.wait_window(dlg)

        username = res.get('username') or ''
        password = res.get('password') or ''
        session_path = res.get('session_path') or ''

        if not username and session_path:
            messagebox.showerror('Missing Username', 'A username is required when using an existing session file.')
            self.logger.error('Username missing for Instagram session reuse')
            return None

        loader = Instaloader()
        try:
            if session_path and username:
                self.logger.info('Loading Instagram session for %s from %s', username, session_path)
                loader.load_session_from_file(username, session_path)
                self._remember_external_session(username, Path(session_path))
                return loader

            if username and password:
                self.logger.info('Attempting Instagram login for %s', username)
                try:
                    loader.login(username, password)
                    self.logger.info('Instagram login successful for %s', username)
                except Exception as exc:
                    self.logger.warning('Instagram login may require 2FA: %s', exc)
                    if 'two' in str(exc).lower():
                        code = self._prompt_2fa(parent)
                        if code:
                            loader.two_factor_login(code)
                            self.logger.info('Instagram 2FA accepted for %s', username)
                        else:
                            self.logger.warning('Instagram 2FA code not provided; aborting login')
                            return None
                    else:
                        raise

                self._remember_loader_session(loader, username)
                # Offer optional export for the user
                save_to = filedialog.asksaveasfilename(title='Save session copy (optional)', defaultextension='.session')
                if save_to:
                    try:
                        loader.save_session_to_file(save_to)
                        self.logger.info('Session exported to %s', save_to)
                    except Exception as exc:
                        self.logger.error('Failed to export Instagram session: %s', exc)
                return loader

        except Exception as exc:
            messagebox.showerror('Login Error', str(exc))
            self.logger.error('Instagram authentication error: %s', exc)
            return None

        return None

    # ------------------------------------------------------------------
    def download(self, url, out_dir, options):
        if not INSTALOADER_AVAILABLE:
            raise RuntimeError('Instaloader not available')

        auth_mode = options.get('auth', 'auto')
        loader = options.get('instaloader_client') or options.get('instaloader')
        loader = self._ensure_loader(loader, auth_mode)

        os.makedirs(out_dir, exist_ok=True)
        url_clean = url.split('?')[0]
        match = re.search(r"instagram.com/(?:p|reel|tv)/([\w-]+)", url_clean)
        if not match:
            raise ValueError('Invalid Instagram link')
        shortcode = match.group(1)

        cwd = os.getcwd()
        try:
            os.chdir(out_dir)
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
                        raise RuntimeError('Instagram requires authentication for this post. Authenticate via the UI first.')
                    if attempt < attempts:
                        time.sleep(1)
                        continue
                    raise RuntimeError(f'Failed to download Instagram post {shortcode}: {message}')
        finally:
            os.chdir(cwd)

    # ------------------------------------------------------------------
    def _prompt_2fa(self, parent):
        """Prompt for a 2FA code."""
        dlg = tk.Toplevel(parent)
        dlg.title('Instagram 2FA')
        ttk.Label(dlg, text='Enter 2FA code:').pack(padx=16, pady=(16, 4))
        code_var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=code_var)
        entry.pack(padx=16, pady=4)
        entry.focus_set()
        result = {'code': None}

        def submit():
            result['code'] = code_var.get().strip()
            dlg.destroy()

        ttk.Button(dlg, text='Submit', command=submit).pack(pady=(8, 16))
        dlg.grab_set()
        parent.wait_window(dlg)
        return result['code']

    def _ensure_loader(self, loader, auth_mode: str):
        if loader:
            return loader

        if auth_mode in ('auto', 'authenticated'):
            cached = self._load_cached_client()
            if cached:
                return cached
            if auth_mode == 'authenticated':
                raise RuntimeError('No saved Instagram session found. Run Instagram authentication first.')

        return Instaloader()

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


__all__ = ['InstagramHandler']
