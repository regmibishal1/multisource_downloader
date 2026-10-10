"""Visible crash handling for the window (brief T22).

A windowed app has no console, so an error in a Tk callback or a worker
thread otherwise just looks like the app freezing. install() routes Tk
callback errors, thread errors and uncaught errors to the log (redacted)
and shows one dialog: "Something went wrong. The download list is saved."
with Copy details and Report problem.

Report problem opens a new GitHub issue with only a title. The details are
not put in the link: they can include links you collected, so you read them
and paste what you choose.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import ttk
from typing import Callable, Optional
from urllib.parse import quote

from .. import logs
from .theme import COLORS, fonts

ISSUES_URL = 'https://github.com/regmibishal1/multisource_downloader/issues/new'
_log = logging.getLogger('multidownloader.crash')


def report_url(summary: str) -> str:
    title = f'Crash: {summary}'[:120]
    return f'{ISSUES_URL}?title={quote(title)}'


class CrashDialog(tk.Toplevel):
    def __init__(self, parent, details: str, summary: str):
        super().__init__(parent)
        self.details = details
        self.summary = summary
        f = fonts(self)
        self.title('Something went wrong')
        self.transient(parent)
        body = ttk.Frame(self, padding=24)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text='✕ Something went wrong. The download list is saved.', foreground=COLORS['error'],
                  font=f['section']).pack(anchor='w')
        ttk.Label(body, wraplength=460, justify='left', font=f['caption'], foreground=COLORS['text-2'],
                  text='Links that finished are in the run report. If a run was going, you can resume it '
                       'the next time you open the app. The details below have sign-ins and your home folder '
                       'removed; check them before sharing, since they can include links you collected.').pack(
            anchor='w', pady=(6, 12))
        self.message_var = tk.StringVar()
        buttons = ttk.Frame(body)
        buttons.pack(anchor='e')
        ttk.Button(buttons, text='Copy details', command=self.copy).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text='Report problem', command=self.report).pack(side=tk.LEFT, padx=(0, 8))
        close = ttk.Button(buttons, text='Close', command=self.destroy)
        close.pack(side=tk.LEFT)
        ttk.Label(body, textvariable=self.message_var, font=f['caption']).pack(anchor='w', pady=(8, 0))
        self.bind('<Escape>', lambda _e: self.destroy())
        close.focus_set()

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.details)
        self.message_var.set('✓ Copied. Paste it into the problem report.')

    def report(self):
        webbrowser.open(report_url(self.summary))
        self.message_var.set('Opened a new problem report in your browser. Paste the copied details there.')


def details_for(exc_type, exc, tb) -> str:
    trace = ''.join(traceback.format_exception(exc_type, exc, tb))
    return logs.redact(trace + '\n' + logs.diagnostics(log_lines=50))


class CrashHandler:
    """Install with install(root); on_crash runs on the Tk thread after the dialog opens."""

    POLL_MS = 250

    def __init__(self, root, on_crash: Optional[Callable[[], None]] = None):
        self.root = root
        self.on_crash = on_crash
        self.dialog: Optional[CrashDialog] = None
        # Tk may only be used from its own thread: other threads queue their
        # crashes and the Tk thread shows them
        self._pending: queue.Queue = queue.Queue()
        self.root.after(self.POLL_MS, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                self._pending.get_nowait()()
        except queue.Empty:
            pass
        try:
            self.root.after(self.POLL_MS, self._poll)
        except tk.TclError:  # the window was closed
            pass

    def handle(self, exc_type, exc, tb) -> None:
        if exc_type is None or issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
            return  # a normal exit or Ctrl+C is not a crash
        _log.error('Unexpected error', exc_info=(exc_type, exc, tb))
        summary = f'{exc_type.__name__}: {logs.redact(str(exc))}'.splitlines()[0][:100]
        details = details_for(exc_type, exc, tb)

        def show():
            if self.on_crash:
                try:
                    self.on_crash()
                except Exception:  # pragma: no cover - never crash the crash handler
                    _log.exception('Recovery after a crash failed')
            if self.dialog is None or not self.dialog.winfo_exists():
                self.dialog = CrashDialog(self.root, details, summary)

        if threading.current_thread() is threading.main_thread():
            try:
                show()
            except tk.TclError:  # the window is gone: the log has it
                pass
        else:
            self._pending.put(show)


def install(root, on_crash: Optional[Callable[[], None]] = None) -> CrashHandler:
    handler = CrashHandler(root, on_crash)
    root.report_callback_exception = handler.handle  # Tk callbacks
    threading.excepthook = lambda args: handler.handle(args.exc_type, args.exc_value, args.exc_traceback)
    sys.excepthook = handler.handle
    return handler


__all__ = ['CrashDialog', 'CrashHandler', 'install', 'report_url']
