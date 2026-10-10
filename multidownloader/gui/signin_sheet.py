"""Sign-in sheet (brief 0.6 Flow A) and Sign-ins manager (Flow C).

Methods are listed neutrally (none is "Recommended"), each platform's terms
notice must be acknowledged before its first sign-in, and nothing here
rotates accounts or gets around a platform's blocks (brief 0.11).
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional

from .. import signins
from ..platforms import PLATFORMS, Platform
from ..signins import SignInError
from .theme import COLORS, fonts, link_label

WRAP = 500

EXTENSION_HELP = (
    'Export it with the "Get cookies.txt LOCALLY" extension (Chrome, Edge) or "cookies.txt" (Firefox). '
    'Do not use the extension named just "Get cookies.txt".'
)
YOUTUBE_STEPS = (
    '1. Open a private window and sign in to YouTube. 2. In the same tab open youtube.com/robots.txt. '
    '3. Export cookies. 4. Close the private window and do not open that session again. '
    'YouTube changes sign-in cookies on open tabs, which would break the export.'
)
CHROMIUM_NOTE = ('Chrome, Edge and other Chromium browsers lock their sign-ins on Windows, so this usually '
                 'fails. Use Firefox or a cookies file.')
SAFARI_NOTE = 'Safari needs Full Disk Access for this app (System Settings, Privacy and Security).'
INSTAGRAM_PASSWORD_NOTE = ('Not recommended. Instagram is more likely to lock accounts that sign in from scripts, '
                           'and its terms do not allow automated collection.')


def _caption(parent, text, tone='text-2', **kw):
    return ttk.Label(parent, text=text, font=fonts(parent)['caption'], foreground=COLORS[tone],
                     wraplength=kw.pop('wraplength', WRAP), justify='left', **kw)


def open_folder(path):
    if sys.platform == 'win32':
        os.startfile(path)  # noqa: S606 - opens Explorer on the user's own folder
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


class _Worker:
    """Run a function off the Tk thread and deliver its result back on it."""

    def __init__(self, widget):
        self.widget = widget
        self.results = queue.Queue()

    def run(self, func, on_done: Callable, on_error: Callable):
        def target():
            try:
                self.results.put((on_done, func()))
            except SignInError as exc:
                self.results.put((on_error, str(exc)))
            except Exception as exc:  # pragma: no cover - unexpected library error
                self.results.put((on_error, f'Something went wrong: {exc}'))

        threading.Thread(target=target, daemon=True).start()
        self._poll()

    def _poll(self):
        try:
            callback, value = self.results.get_nowait()
        except queue.Empty:
            if self.widget.winfo_exists():
                self.widget.after(100, self._poll)
            return
        if self.widget.winfo_exists():
            callback(value)


class SignInSheet(tk.Toplevel):
    """Sign in to one platform.

    reason: caption under the title, e.g. "5 links in your list need an
    Instagram sign-in." retry_count/on_retry: show "Retry N links" on success.
    """

    def __init__(self, parent, platform: Platform, *, reason: str = '', retry_count: int = 0,
                 on_retry: Optional[Callable[[], None]] = None, on_change: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.platform = platform
        self.retry_count = retry_count
        self.on_retry = on_retry
        self.on_change = on_change
        self.worker = _Worker(self)
        self.file_text: Optional[str] = None
        self.file_name: Optional[str] = None
        self.found_profiles = []
        f = fonts(self)

        self.title(f'Sign in to {platform.name}')
        self.transient(parent)
        self.minsize(560, 300)
        self.body = ttk.Frame(self, padding=24)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.body.columnconfigure(0, weight=1)

        ttk.Label(self.body, text=f'Sign in to {platform.name}', font=f['section']).grid(row=0, column=0, sticky='w')
        lead = (reason + ' ' if reason else '') + 'Your sign-in stays on this computer.'
        _caption(self.body, lead).grid(row=1, column=0, sticky='w', pady=(4, 12))

        row = 2
        self.ack_var = tk.BooleanVar(value=not signins.needs_terms_ack(platform))
        if platform.terms:
            notice = ttk.Frame(self.body)
            notice.grid(row=row, column=0, sticky='ew', pady=(0, 12))
            notice.columnconfigure(1, weight=1)
            ttk.Label(notice, text='!', foreground=COLORS['warning'], font=f['bold']).grid(
                row=0, column=0, sticky='nw', padx=(0, 8))
            ttk.Label(notice, text=platform.terms.notice, wraplength=WRAP - 20, justify='left').grid(
                row=0, column=1, sticky='w')
            _caption(notice, platform.terms.account_advice, wraplength=WRAP - 20).grid(
                row=1, column=1, sticky='w', pady=(4, 0))
            link_label(notice, f"Read {platform.name}'s terms",
                       lambda: webbrowser.open(platform.terms.url)).grid(row=2, column=1, sticky='w', pady=(4, 0))
            ttk.Checkbutton(notice, text='I understand and will use this only where I have a right to.',
                            variable=self.ack_var, command=self._update_continue).grid(
                row=3, column=1, sticky='w', pady=(8, 0))
            row += 1

        self.method_var = tk.StringVar(value='browser')
        self.cards = ttk.Frame(self.body)
        self.cards.grid(row=row, column=0, sticky='ew')
        self.cards.columnconfigure(0, weight=1)
        self._build_browser_card()
        self._build_file_card()
        row += 1

        if platform.key == 'instagram':
            self.more_link = link_label(self.body, 'More ways to sign in', self._show_password_card)
            self.more_link.grid(row=row, column=0, sticky='w', pady=(8, 0))
            row += 1

        self.message_var = tk.StringVar()
        self.message = ttk.Label(self.body, textvariable=self.message_var, wraplength=WRAP, justify='left')
        self.message.grid(row=row, column=0, sticky='w', pady=(12, 0))
        row += 1

        self.footer = ttk.Frame(self.body)
        self.footer.grid(row=row, column=0, sticky='e', pady=(16, 0))
        ttk.Button(self.footer, text='Cancel', command=self.destroy).pack(side=tk.LEFT, padx=(0, 8))
        self.continue_btn = ttk.Button(self.footer, text='Continue', command=self._continue)
        self.continue_btn.pack(side=tk.LEFT)

        self.bind('<Escape>', lambda _e: self.destroy())
        self._update_continue()
        self._detect_firefox()
        self.grab_set()
        self.focus_set()

    # --- cards -------------------------------------------------------------
    def _card(self, row, value, title):
        frame = ttk.Frame(self.cards, padding=(8, 8), relief='solid', borderwidth=1)
        frame.grid(row=row, column=0, sticky='ew', pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Radiobutton(frame, text=title, value=value, variable=self.method_var,
                        command=self._update_continue).grid(row=0, column=0, sticky='w')
        return frame

    def _build_browser_card(self):
        card = self._card(0, 'browser', 'Use my Firefox sign-in')
        self.browser_line = tk.StringVar(value='Looking for a sign-in in Firefox...')
        ttk.Label(card, textvariable=self.browser_line, font=fonts(self)['caption'],
                  foreground=COLORS['text-2'], wraplength=WRAP - 30, justify='left').grid(
            row=1, column=0, sticky='w', padx=(22, 0))
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(card, textvariable=self.profile_var, state='readonly', width=40)

        other = ttk.Frame(card)
        other.grid(row=3, column=0, sticky='w', padx=(22, 0), pady=(6, 0))
        ttk.Label(other, text='Browser:', font=fonts(self)['caption']).pack(side=tk.LEFT)
        names = [name for key, name in signins.BROWSERS if key != 'safari' or sys.platform == 'darwin']
        self.browser_var = tk.StringVar(value='Firefox')
        box = ttk.Combobox(other, textvariable=self.browser_var, values=names, state='readonly', width=12)
        box.pack(side=tk.LEFT, padx=(4, 4))
        box.bind('<<ComboboxSelected>>', lambda _e: self._browser_changed())
        self.check_btn = ttk.Button(other, text='Check', command=self._check_browser)
        self.check_btn.pack(side=tk.LEFT)
        self.browser_note = tk.StringVar()
        ttk.Label(card, textvariable=self.browser_note, font=fonts(self)['caption'],
                  foreground=COLORS['warning'], wraplength=WRAP - 30, justify='left').grid(
            row=4, column=0, sticky='w', padx=(22, 0))

    def _build_file_card(self):
        card = self._card(1, 'file', 'Import a cookies file')
        text = 'For advanced users. Paste or choose a cookies.txt file. ' + EXTENSION_HELP
        if self.platform.key == 'youtube':
            text += ' ' + YOUTUBE_STEPS
        _caption(card, text, wraplength=WRAP - 30).grid(row=1, column=0, sticky='w', padx=(22, 0))
        buttons = ttk.Frame(card)
        buttons.grid(row=2, column=0, sticky='w', padx=(22, 0), pady=(6, 0))
        ttk.Button(buttons, text='Choose file...', command=self._choose_file).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text='Paste from clipboard', command=self._paste).pack(side=tk.LEFT)
        self.file_line = tk.StringVar()
        ttk.Label(card, textvariable=self.file_line, font=fonts(self)['caption']).grid(
            row=3, column=0, sticky='w', padx=(22, 0))

    def _show_password_card(self):
        self.more_link.grid_remove()
        card = self._card(2, 'password', 'Username and password')
        _caption(card, INSTAGRAM_PASSWORD_NOTE, wraplength=WRAP - 30).grid(row=1, column=0, sticky='w', padx=(22, 0))

    # --- detection ---------------------------------------------------------
    def _browser_key(self):
        name = self.browser_var.get()
        return next(key for key, label in signins.BROWSERS if label == name)

    def _browser_changed(self):
        key = self._browser_key()
        note = ''
        if key in signins.CHROMIUM_BROWSERS and sys.platform == 'win32':
            note = CHROMIUM_NOTE
        elif key == 'safari':
            note = SAFARI_NOTE
        self.browser_note.set(note)
        self.found_profiles = []
        self.profile_box.grid_remove()
        if key == 'firefox':
            self._detect_firefox()
        else:
            # other browsers are only read after the user selects Check
            self.browser_line.set(f'Select Check to look for a {self.platform.name} sign-in in {self.browser_var.get()}.')
        self._update_continue()

    def _detect_firefox(self):
        # Firefox only, automatically: reading it triggers no system prompt
        self.browser_line.set('Looking for a sign-in in Firefox...')
        self.worker.run(lambda: signins.find_firefox_signins(self.platform), self._firefox_found, self._browser_failed)

    def _alive(self) -> bool:
        # detection runs on a worker; the sheet may have moved on to its
        # success view (or closed) before it finishes
        return self.winfo_exists() and self.continue_btn.winfo_exists()

    def _firefox_found(self, found):
        if not self._alive():
            return
        self.found_profiles = found
        if not found:
            if not signins.firefox_profiles():
                self.browser_line.set('Firefox is not installed for this user.')
            else:
                self.browser_line.set(f'No {self.platform.name} sign-in found in Firefox. '
                                      f'Sign in to {self.platform.name} in Firefox, then select Check.')
            if self.method_var.get() == 'browser':
                self.method_var.set('file')
        else:
            first = found[0]
            who = f', account {first.account_id[:4]}...' if first.account_id else ''
            self.browser_line.set(f'Found a sign-in in Firefox (profile {first.profile.name}{who})')
            self.method_var.set('browser')
            if len(found) > 1:
                labels = [f'{s.profile.name}' + (f' (account {s.account_id[:4]}...)' if s.account_id else '')
                          for s in found]
                self.profile_box.configure(values=labels)
                self.profile_box.current(0)
                self.profile_box.grid(row=2, column=0, sticky='w', padx=(22, 0), pady=(4, 0))
        self._update_continue()

    def _browser_failed(self, message):
        if not self._alive():
            return
        self.browser_line.set(message)
        self._update_continue()

    def _check_browser(self):
        key = self._browser_key()
        if key == 'firefox':
            self._detect_firefox()
            return
        self.browser_line.set(f'Checking {self.browser_var.get()}...')

        def check():
            cookies = signins.filter_for(self.platform, signins.read_browser_cookies(key))
            found, _exp, expired = signins.find_markers(self.platform, cookies)
            return found and not expired

        def done(ok):
            if not self._alive():
                return
            name = self.browser_var.get()
            self.found_profiles = ['other'] if ok else []
            self.browser_line.set(f'Found a sign-in in {name}' if ok else f'No {self.platform.name} sign-in found in {name}.')
            if ok:
                self.method_var.set('browser')
            self._update_continue()

        self.worker.run(check, done, self._browser_failed)

    # --- file input --------------------------------------------------------
    def _choose_file(self):
        path = filedialog.askopenfilename(parent=self, title='Choose a cookies.txt file',
                                          filetypes=[('Cookie files', '*.txt'), ('All files', '*.*')])
        if not path:
            return
        try:
            with open(path, encoding='utf-8', errors='replace') as fp:
                self.file_text = fp.read()
        except OSError as exc:
            self._show_error(f'Could not read the file: {exc.strerror or exc}')
            return
        self.file_name = os.path.basename(path)
        self.file_line.set(f'Selected: {self.file_name}')
        self.method_var.set('file')
        self._update_continue()

    def _paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._show_error('The clipboard is empty.')
            return
        self.file_text = text
        self.file_name = None
        self.file_line.set('Pasted from the clipboard.')
        self.method_var.set('file')
        self._update_continue()

    # --- continue ----------------------------------------------------------
    def _ready(self) -> bool:
        if self.platform.terms and not self.ack_var.get():
            return False
        method = self.method_var.get()
        if method == 'browser':
            return bool(self.found_profiles)
        if method == 'file':
            return self.file_text is not None
        return method == 'password'

    def _update_continue(self):
        if hasattr(self, 'continue_btn'):
            self.continue_btn.state(['!disabled'] if self._ready() else ['disabled'])

    def _show_error(self, message):
        self.message.configure(foreground=COLORS['error'])
        self.message_var.set('✕ ' + message)

    def _terms_version(self):
        return self.platform.terms.version if self.platform.terms else None

    def _continue(self):
        if not self._ready():
            return
        method = self.method_var.get()
        terms = self._terms_version()
        self.message.configure(foreground=COLORS['text-2'])
        if method == 'password':
            from ..sources.instagram import InstagramHandler
            from .instagram_login import ask_instagram_password_signin
            result = ask_instagram_password_signin(self, InstagramHandler(), terms)
            if result:
                self._success(result)
            return
        self.continue_btn.state(['disabled'])
        self.message_var.set('Saving your sign-in...')
        if method == 'file':
            text = self.file_text
            self.worker.run(lambda: signins.import_cookies_text(self.platform, text, terms_version=terms),
                            self._success, self._failed)
            return
        key = self._browser_key()
        profile = None
        if key == 'firefox' and self.found_profiles:
            index = self.profile_box.current() if len(self.found_profiles) > 1 else 0
            profile = self.found_profiles[max(index, 0)].profile
        self.worker.run(lambda: signins.import_from_browser(self.platform, key, profile, terms_version=terms),
                        self._success, self._failed)

    def _failed(self, message):
        self._show_error(message)
        self._update_continue()

    def _success(self, result):
        if self.on_change:
            self.on_change()
        for child in self.body.winfo_children():
            child.destroy()
        f = fonts(self)
        acct = f' (account {result.account_id[:4]}...)' if result.account_id else ''
        ttk.Label(self.body, text=f'✓ Signed in to {self.platform.name}{acct}.', foreground=COLORS['success'],
                  font=f['section']).grid(row=0, column=0, sticky='w')
        lines = [result.message()]
        if self.method_var.get() == 'file' and self.file_name:
            lines.append('You can delete the original file now. It contains sign-ins for every site.')
        _caption(self.body, ' '.join(lines)).grid(row=1, column=0, sticky='w', pady=(8, 0))
        footer = ttk.Frame(self.body)
        footer.grid(row=2, column=0, sticky='e', pady=(16, 0))
        ttk.Button(footer, text='Close', command=self.destroy).pack(side=tk.LEFT, padx=(0, 8))
        if self.retry_count and self.on_retry:
            noun = 'link' if self.retry_count == 1 else 'links'
            retry = ttk.Button(footer, text=f'Retry {self.retry_count} {self.platform.name} {noun}',
                               command=self._retry)
            retry.pack(side=tk.LEFT)
            retry.focus_set()
            self.bind('<Return>', lambda _e: self._retry())

    def _retry(self):
        callback = self.on_retry
        self.destroy()
        callback()


class SignInsWindow(tk.Toplevel):
    """Every platform's sign-in status, with Sign in, Refresh and Remove."""

    def __init__(self, parent, on_change: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.on_change = on_change
        f = fonts(self)
        self.title('Sign-ins')
        self.transient(parent)
        self.minsize(600, 520)
        body = ttk.Frame(self, padding=24)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text='Sign-ins', font=f['section']).grid(row=0, column=0, sticky='w')
        _caption(body, 'Sign-ins stay on this computer. They let the app download posts that need an account.'
                 ).grid(row=1, column=0, sticky='w', pady=(4, 12))
        self.rows = ttk.Frame(body)
        self.rows.grid(row=2, column=0, sticky='nsew')
        self.rows.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, sticky='ew', pady=(16, 0))
        footer.columnconfigure(1, weight=1)
        store = signins.folder(PLATFORMS[0]).parent
        _caption(footer, f'Stored in: {store}', wraplength=420).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Button(footer, text='Open folder', command=lambda: open_folder(store)).grid(row=0, column=2, sticky='e')
        remove_all = tk.Label(footer, text='Remove all sign-ins...', fg=COLORS['error'], cursor='hand2',
                              takefocus=1, font=f['body'])
        remove_all.grid(row=1, column=0, sticky='w', pady=(12, 0))
        remove_all.bind('<Button-1>', lambda _e: self._remove_all())
        remove_all.bind('<Return>', lambda _e: self._remove_all())
        ttk.Button(footer, text='Close', command=self.destroy).grid(row=1, column=2, sticky='e', pady=(12, 0))
        self.bind('<Escape>', lambda _e: self.destroy())
        self.refresh()
        self.focus_set()

    def refresh(self):
        for child in self.rows.winfo_children():
            child.destroy()
        f = fonts(self)
        for i, st in enumerate(signins.all_statuses()):
            r = i * 2
            if i:
                ttk.Separator(self.rows).grid(row=r - 1, column=0, columnspan=4, sticky='ew', pady=4)
            ttk.Label(self.rows, text=st.platform.name, font=f['bold'], width=14).grid(row=r, column=0, sticky='w')
            text = f'{st.glyph} {st.text}' if st.glyph else st.text
            ttk.Label(self.rows, text=text, foreground=COLORS[st.tone], wraplength=260, justify='left').grid(
                row=r, column=1, sticky='w', padx=(8, 8))
            meta = signins.read_meta(st.platform)
            method = signins.method_label(meta) if meta else ''
            ttk.Label(self.rows, text=method or '', font=f['caption'], width=14).grid(row=r, column=2, sticky='w')
            actions = ttk.Frame(self.rows)
            actions.grid(row=r, column=3, sticky='e', pady=6)
            if st.state in ('not_needed', 'not_set_up'):
                ttk.Button(actions, text='Sign in', command=lambda p=st.platform: self._sign_in(p)).pack(side=tk.LEFT)
            else:
                ttk.Button(actions, text='Refresh', command=lambda p=st.platform: self._refresh(p)).pack(
                    side=tk.LEFT, padx=(0, 4))
                ttk.Button(actions, text='Remove', command=lambda p=st.platform: self._remove(p)).pack(
                    side=tk.LEFT, padx=(0, 4))
                more = ttk.Menubutton(actions, text='...', width=3)
                menu = tk.Menu(more, tearoff=False)
                menu.add_command(label='Rename...', command=lambda p=st.platform: self._rename(p))
                menu.add_command(label='Show where it is stored',
                                 command=lambda p=st.platform: open_folder(signins.folder(p)))
                more['menu'] = menu
                more.pack(side=tk.LEFT)

    def _changed(self):
        self.refresh()
        if self.on_change:
            self.on_change()

    def _sign_in(self, platform):
        sheet = SignInSheet(self, platform, on_change=self._changed)
        self.wait_window(sheet)

    def _refresh(self, platform):
        meta = signins.read_meta(platform)
        if meta.get('method') == 'browser' and signins.refresh_from_browser(platform):
            self._changed()
            return
        self._sign_in(platform)

    def _remove(self, platform):
        if messagebox.askyesno('Remove sign-in?',
                               f'This deletes the saved {platform.name} sign-in from this computer. '
                               'Downloaded files are not affected.', parent=self, default='no'):
            signins.remove(platform)
            self._changed()

    def _remove_all(self):
        if messagebox.askyesno('Remove all sign-ins?',
                               'This deletes saved sessions and cookies for every platform from this computer. '
                               'Downloaded files are not affected.', parent=self, default='no', icon='warning'):
            signins.remove_all()
            self._changed()

    def _rename(self, platform):
        current = signins.read_meta(platform).get('label') or ''
        label = simpledialog.askstring('Rename sign-in', f'Name for this {platform.name} sign-in:',
                                       initialvalue=current, parent=self)
        if label is not None:
            signins.rename(platform, label.strip() or None)
            self._changed()


__all__ = ['SignInSheet', 'SignInsWindow', 'open_folder']
