"""Instagram username and password dialog (last resort, brief section 6.3)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from ..signins import SignInError
from .help import help_button
from .theme import COLORS, fonts


class InstagramPasswordDialog(tk.Toplevel):
    """Signs in on a worker thread; the two-factor prompt is answered on the Tk thread."""

    def __init__(self, parent, handler, terms_version):
        super().__init__(parent)
        self.handler = handler
        self.terms_version = terms_version
        self.result = None
        self._events = queue.Queue()
        self._code_reply = queue.Queue()
        self._busy = False
        f = fonts(self)

        self.title('Sign in to Instagram')
        self.transient(parent)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=24)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text='Sign in to Instagram', font=f['section']).grid(row=0, column=0, sticky='w')
        ttk.Label(
            body, font=f['caption'], foreground=COLORS['text-2'], wraplength=370, justify='left',
            text='Your password goes only to Instagram and is not saved. A login session is saved on this '
                 'computer so you do not have to sign in again.',
        ).grid(row=1, column=0, sticky='w', pady=(4, 12))

        self.fields = ttk.Frame(body)
        self.fields.grid(row=2, column=0, sticky='ew')
        self.fields.columnconfigure(0, weight=1)
        self._build_password_fields()

        self.progress = ttk.Progressbar(body, mode='indeterminate', length=370)
        self.error_var = tk.StringVar()
        self.error = ttk.Label(body, textvariable=self.error_var, foreground=COLORS['error'],
                               wraplength=370, justify='left')
        self.error.grid(row=4, column=0, sticky='w', pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky='e', pady=(16, 0))
        ttk.Button(buttons, text='Cancel', command=self.cancel).pack(side=tk.LEFT, padx=(0, 8))
        self.submit_btn = ttk.Button(buttons, text='Sign in', command=self.submit)
        self.submit_btn.pack(side=tk.LEFT)

        self.bind('<Return>', lambda _e: self.submit())
        help_button(self, 'signins')
        self.bind('<Escape>', lambda _e: self.cancel())
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.grab_set()
        self.user_entry.focus_set()

    # --- layout ------------------------------------------------------------
    def _clear_fields(self):
        for child in self.fields.winfo_children():
            child.destroy()

    def _build_password_fields(self):
        self._clear_fields()
        ttk.Label(self.fields, text='Username').grid(row=0, column=0, sticky='w')
        self.user_entry = ttk.Entry(self.fields)
        self.user_entry.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 8))
        ttk.Label(self.fields, text='Password').grid(row=2, column=0, sticky='w')
        self.pwd_entry = ttk.Entry(self.fields, show='•')
        self.pwd_entry.grid(row=3, column=0, sticky='ew', pady=(4, 0))
        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.fields, text='Show', variable=self.show_var,
                        command=lambda: self.pwd_entry.configure(show='' if self.show_var.get() else '•')
                        ).grid(row=3, column=1, padx=(8, 0))

    def _build_code_field(self):
        self._clear_fields()
        ttk.Label(self.fields, text='Code').grid(row=0, column=0, sticky='w')
        ttk.Label(self.fields, font=fonts(self)['caption'], foreground=COLORS['text-2'],
                  text='Enter the 6-digit code from your authenticator app or text message.'
                  ).grid(row=1, column=0, sticky='w', pady=(2, 4))
        check = (self.register(lambda v: v.isdigit() and len(v) <= 8 or v == ''), '%P')
        self.code_entry = ttk.Entry(self.fields, validate='key', validatecommand=check, width=12)
        self.code_entry.grid(row=2, column=0, sticky='w')
        self.code_entry.focus_set()

    # --- actions -----------------------------------------------------------
    def _set_busy(self, busy, label='Signing in...'):
        self._busy = busy
        if busy:
            self.submit_btn.configure(text=label)
            self.submit_btn.state(['disabled'])
            self.progress.grid(row=3, column=0, sticky='ew', pady=(8, 0))
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()
            self.submit_btn.state(['!disabled'])

    def submit(self):
        if getattr(self, 'code_entry', None) is not None and self.code_entry.winfo_exists():
            code = self.code_entry.get().strip()
            if not code:
                self.error_var.set('✕ Enter the code.')
                return
            self.error_var.set('')
            self._set_busy(True, 'Checking code...')
            self._code_reply.put(code)
            return
        if self._busy:
            return
        username = self.user_entry.get().strip()
        password = self.pwd_entry.get()
        if not username or not password:
            self.error_var.set('✕ Enter your username and password.')
            return
        self.error_var.set('')
        self._set_busy(True)
        threading.Thread(target=self._work, args=(username, password), daemon=True).start()
        self.after(100, self._poll)

    def _ask_code(self):
        # called on the worker thread; the dialog shows the code field and
        # submit() hands the code back through _code_reply
        self._events.put(('need_code', None))
        try:
            return self._code_reply.get(timeout=600)
        except queue.Empty:
            return None

    def _work(self, username, password):
        try:
            loader = self.handler.password_login(username, password, self._ask_code)
            result = self.handler.save_password_signin(loader, username, self.terms_version)
            self._events.put(('done', result))
        except SignInError as exc:
            self._events.put(('error', str(exc)))
        except Exception as exc:  # pragma: no cover - unexpected library error
            self._events.put(('error', f'Instagram sign-in failed: {exc}'))

    def _poll(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == 'need_code':
                    self._set_busy(False)
                    self.submit_btn.configure(text='Continue')
                    self._build_code_field()
                elif kind == 'done':
                    self.result = payload
                    self.destroy()
                    return
                elif kind == 'error':
                    self._set_busy(False)
                    self.submit_btn.configure(text='Sign in')
                    self.code_entry = None
                    self._build_password_fields()
                    self.error_var.set('✕ ' + payload)
                    self.user_entry.focus_set()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def cancel(self):
        self._code_reply.put(None)
        self.destroy()


def ask_instagram_password_signin(parent, handler, terms_version):
    """Show the dialog and return the SaveResult, or None if cancelled.

    terms_version is the Instagram terms version the user acknowledged on
    the sign-in sheet; the dialog is never opened without it.
    """
    parent = parent or tk._default_root
    dialog = InstagramPasswordDialog(parent, handler, terms_version)
    parent.wait_window(dialog)
    return dialog.result
