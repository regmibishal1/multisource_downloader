"""Instagram handler using Instaloader."""
import os
import logging

try:
    from instaloader import Instaloader, Post
    INSTALOADER_AVAILABLE = True
except Exception:
    INSTALOADER_AVAILABLE = False

import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class InstagramHandler:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)

    def interactive_auth(self, root=None):
        """Open a simple Tkinter dialog (Toplevel) to authenticate or load a session.
        Returns an authenticated Instaloader instance or None.
        """
        if not INSTALOADER_AVAILABLE:
            messagebox.showerror('Missing Dependency', 'Instaloader is required for Instagram authenticated downloads')
            self.logger.error('Instaloader not available for Instagram authentication')
            return None

        parent = root or tk._default_root
        dlg = tk.Toplevel(parent)
        dlg.title('Instagram Login')
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text='Username:').grid(row=0, column=0, sticky='e', padx=(0, 8), pady=4)
        user = ttk.Entry(frm); user.grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Label(frm, text='Password (leave blank to use session file):').grid(row=1, column=0, sticky='e', padx=(0, 8), pady=4)
        pwd = ttk.Entry(frm, show='*'); pwd.grid(row=1, column=1, sticky='ew', pady=4)
        frm.columnconfigure(1, weight=1)

        sess_var = tk.StringVar()
        def pick():
            p = filedialog.askopenfilename(title='Select Instaloader session file')
            if p:
                sess_var.set(p)
                self.logger.info(f'Selected session file: {p}')
        btn_sess = ttk.Button(frm, text='Select Session File', command=pick)
        btn_sess.grid(row=2, column=0, columnspan=2, pady=4)
        ttk.Label(frm, textvariable=sess_var, anchor='w').grid(row=3, column=0, columnspan=2, sticky='w', pady=2)

        res = {}
        def submit():
            res['user'] = user.get().strip(); res['pwd'] = pwd.get(); res['sess'] = sess_var.get(); dlg.destroy()
            self.logger.info(f'Instagram login submitted. Username: {res.get("user")}, Session: {res.get("sess")}')
        btn_login = ttk.Button(frm, text='Login', command=submit)
        btn_login.grid(row=4, column=0, columnspan=2, pady=(12, 4))

        frm.pack_propagate(False)
        dlg.grab_set(); parent.wait_window(dlg)

        username = res.get('user'); password = res.get('pwd'); session = res.get('sess')
        L = Instaloader()
        try:
            if session and username:
                self.logger.info(f'Loading session for user {username} from {session}')
                L.load_session_from_file(username, session)
                return L
            if username and password:
                self.logger.info(f'Attempting login for user {username}')
                try:
                    L.login(username, password)
                    self.logger.info('Instagram login successful')
                except Exception as e:
                    self.logger.warning(f'Instagram login 2FA required: {e}')
                    if 'two' in str(e).lower():
                        twofa_dlg = tk.Toplevel(parent)
                        twofa_dlg.title('Instagram 2FA')
                        ttk.Label(twofa_dlg, text='Enter 2FA code:').pack(padx=16, pady=(16, 4))
                        code_var = tk.StringVar()
                        code_entry = ttk.Entry(twofa_dlg, textvariable=code_var)
                        code_entry.pack(padx=16, pady=4)
                        result = {'code': None}
                        def submit_2fa():
                            result['code'] = code_var.get().strip()
                            twofa_dlg.destroy()
                        ttk.Button(twofa_dlg, text='Submit', command=submit_2fa).pack(pady=(8, 16))
                        code_entry.focus_set()
                        twofa_dlg.grab_set(); parent.wait_window(twofa_dlg)
                        code = result['code']
                        if code:
                            self.logger.info('Submitting Instagram 2FA code')
                            L.two_factor_login(code)
                        else:
                            self.logger.warning('No Instagram 2FA code entered')
                            return None
                sp = filedialog.asksaveasfilename(title='Save session (optional)', defaultextension='.session')
                if sp:
                    try:
                        L.save_session_to_file(sp)
                        self.logger.info(f'Session saved to {sp}')
                    except Exception as ex:
                        self.logger.error(f'Failed to save session: {ex}')
                return L
        except Exception as e:
            messagebox.showerror('Login Error', str(e))
            self.logger.error(f'Instagram login error: {e}')
            return None
        return None

    def download(self, url, out_dir, options):
        if not INSTALOADER_AVAILABLE:
            raise RuntimeError('Instaloader not available')
        method = options.get('auth', 'unauthenticated')
        # Allow caller to pass an authenticated Instaloader instance
        L = options.get('instaloader_client') or options.get('instaloader')
        if L is None:
            L = Instaloader()
        # Ensure output directory exists
        os.makedirs(out_dir, exist_ok=True)
        url_clean = url.split('?')[0]
        m = re.search(r"instagram.com/(?:p|reel|tv)/([\w-]+)", url_clean)
        if not m:
            raise ValueError('Invalid Instagram link')
        shortcode = m.group(1)
        cwd = os.getcwd()
        try:
            os.chdir(out_dir)
            # Attempt to fetch and download the post with a small retry for transient failures
            attempts = 2
            for attempt in range(1, attempts + 1):
                try:
                    post = Post.from_shortcode(L.context, shortcode)
                    L.download_post(post, target=shortcode)
                    self.logger.info(f'Downloaded Instagram post: {shortcode}')
                    return True
                except Exception as ex:
                    msg = str(ex)
                    self.logger.warning(f'Attempt {attempt} failed for {shortcode}: {msg}')
                    # If it's likely an authentication issue, provide a helpful error
                    if '400' in msg or 'login' in msg.lower() or 'authentication' in msg.lower():
                        raise RuntimeError(
                            f'Instagram blocked unauthenticated access for this post. Try authenticated mode. Original error: {msg}'
                        )
                    if attempt < attempts:
                        time.sleep(1)
                        continue
                    # Exhausted retries
                    raise RuntimeError(f'Failed to download Instagram post {shortcode}: {msg}')
        finally:
            os.chdir(cwd)
