"""The header's Help menu (brief 6.6): keyboard shortcuts, diagnostics, the log folder."""
from __future__ import annotations

import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from .. import logs
from .theme import COLORS, fonts, px

HELP_FILE = Path(__file__).with_name('help.html')
SECTIONS = ('start', 'simple', 'queue', 'signins', 'options', 'export', 'updates', 'keyboard', 'problems',
            'privacy', 'accessibility')

# (keys, what they do), in the order the dialog lists them
SHORTCUTS = (
    ('Ctrl+L', 'Go to the Links box'),
    ('Ctrl+O', 'Import links from a file'),
    ('Ctrl+Enter', 'Download'),
    ('Ctrl+.', 'Stop'),
    ('F5', 'Retry failed links'),
    ('Ctrl+Shift+O', 'Open the download folder'),
    ('Ctrl+,', 'Options'),
    ('Ctrl+Shift+V', 'Switch between the simple and the full view'),
    ('F6', 'Move between the Links box, the queue and the buttons'),
    ('Alt + underlined letter', 'Select that button'),
    ('Enter, double-click', 'Open the selected file'),
    ('Shift+F10, Menu key', 'More actions for the selected link'),
    ('Delete', 'Remove the selected links from the list'),
    ('Esc', 'Close a dialog without changing anything'),
    ('F1', 'Help'),
)


def help_url(section: str = 'start') -> str:
    """A file URL that lands on section. Windows drops the #part of a file URL it opens, so a tiny
    page in the temp folder forwards to it."""
    section = section if section in SECTIONS else 'start'
    target = f'{HELP_FILE.resolve().as_uri()}#{section}'
    forward = Path(tempfile.gettempdir()) / f'multisource-downloader-help-{section}.html'
    try:
        forward.write_text(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={target}">'
                           f'<a href="{target}">Open the help</a>', encoding='utf-8')
        return forward.resolve().as_uri()
    except OSError:
        return target


def open_help(section: str = 'start') -> None:
    webbrowser.open(help_url(section))


def help_button(window, section: str) -> ttk.Button:
    """The (?) button, always in the window's top-right corner (WCAG 3.2.6)."""
    button = ttk.Button(window, text='?', width=2, command=lambda: open_help(section), takefocus=1)
    button.place(relx=1.0, x=-px(12), y=px(12), anchor='ne')
    button.lift()
    window.bind('<F1>', lambda _e: open_help(section))
    return button


def _restore_focus_on_close(dialog, previous) -> None:
    def back(_event=None):
        if previous is not None:
            try:
                previous.focus_set()
            except tk.TclError:
                pass

    dialog.bind('<Destroy>', lambda e: back() if e.widget is dialog else None, add='+')


def show_shortcuts(parent) -> tk.Toplevel:
    previous = parent.focus_get()
    dialog = tk.Toplevel(parent)
    dialog.title('Keyboard shortcuts')
    dialog.transient(parent)
    dialog.resizable(False, False)
    f = fonts(dialog)
    body = ttk.Frame(dialog, padding=(px(24), px(48), px(24), px(24)))
    body.pack(fill=tk.BOTH, expand=True)
    help_button(dialog, 'keyboard')
    _restore_focus_on_close(dialog, previous)
    for row, (keys, action) in enumerate(SHORTCUTS):
        ttk.Label(body, text=keys, font=f['bold']).grid(row=row, column=0, sticky='w', padx=(0, px(16)), pady=px(2))
        ttk.Label(body, text=action, foreground=COLORS['text']).grid(row=row, column=1, sticky='w', pady=px(2))
    close = ttk.Button(body, text='Close', command=dialog.destroy)
    close.grid(row=len(SHORTCUTS), column=0, columnspan=2, sticky='e', pady=(px(16), 0))
    for key in ('<Escape>', '<Return>'):
        dialog.bind(key, lambda _e: dialog.destroy())
    close.focus_set()
    return dialog


def show_update_help(parent, notice) -> tk.Toplevel:
    """How to update a component: the command to run (or where to get the new app), with Copy."""
    from .. import updates

    previous = parent.focus_get()
    dialog = tk.Toplevel(parent)
    dialog.title(f'Update {notice.package}')
    dialog.transient(parent)
    dialog.resizable(False, False)
    f = fonts(dialog)
    body = ttk.Frame(dialog, padding=(px(24), px(48), px(24), px(24)))
    body.pack(fill=tk.BOTH, expand=True)
    help_button(dialog, 'updates')
    _restore_focus_on_close(dialog, previous)
    body.columnconfigure(0, weight=1)
    ttk.Label(body, text=notice.message, wraplength=px(480), justify='left').grid(row=0, column=0, columnspan=2,
                                                                                sticky='w')
    text = updates.how_to_update(notice.package)
    command = not text.startswith('Download')
    ttk.Label(body, text='Run this in a terminal, then restart the app:' if command else text,
              wraplength=px(480), justify='left').grid(row=1, column=0, columnspan=2, sticky='w', pady=(px(12), 0))
    buttons = ttk.Frame(body)
    buttons.grid(row=3, column=0, columnspan=2, sticky='e', pady=(px(16), 0))
    if command:
        var = tk.StringVar(value=text)
        entry = ttk.Entry(body, textvariable=var, state='readonly', font=f['mono'], width=60)
        entry.grid(row=2, column=0, sticky='ew', pady=(px(8), 0))

        def copy():
            dialog.clipboard_clear()
            dialog.clipboard_append(text)

        ttk.Button(body, text='Copy', command=copy).grid(row=2, column=1, sticky='e', padx=(px(8), 0),
                                                         pady=(px(8), 0))
    close = ttk.Button(buttons, text='Close', command=dialog.destroy)
    close.pack(side=tk.LEFT)
    dialog.bind('<Escape>', lambda _e: dialog.destroy())
    close.focus_set()
    return dialog


def copy_diagnostics(widget) -> str:
    """Put the redacted diagnostics on the clipboard (for a bug report) and return them."""
    text = logs.diagnostics()
    widget.clipboard_clear()
    widget.clipboard_append(text)
    return text


def build_menu(parent, *, on_options, on_open_logs, on_status=None, on_toggle_view=None) -> tk.Menu:
    menu = tk.Menu(parent, tearoff=False)
    menu.add_command(label='Help', accelerator='F1', underline=0, command=lambda: open_help('start'))
    menu.add_command(label='Options...', accelerator='Ctrl+,', underline=0, command=on_options)
    if on_toggle_view:
        menu.add_command(label='Switch simple or full view', accelerator='Ctrl+Shift+V', underline=7,
                         command=on_toggle_view)
    menu.add_command(label='Keyboard shortcuts', underline=0,
                     command=lambda: show_shortcuts(parent.winfo_toplevel()))
    menu.add_separator()

    def copy():
        copy_diagnostics(parent)
        if on_status:
            on_status('Diagnostics copied. Paste them into a bug report; sign-ins are removed.')

    menu.add_command(label='Copy diagnostics', underline=5, command=copy)
    menu.add_command(label='Open log folder', underline=5, command=on_open_logs)
    return menu


__all__ = ['HELP_FILE', 'SECTIONS', 'SHORTCUTS', 'build_menu', 'copy_diagnostics', 'help_button', 'help_url',
           'open_help', 'show_shortcuts', 'show_update_help']
