"""The header's Help menu (brief 6.6): keyboard shortcuts, diagnostics, the log folder."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import logs
from .theme import COLORS, fonts, px

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
    ('F1', 'This list'),
)


def show_shortcuts(parent) -> tk.Toplevel:
    dialog = tk.Toplevel(parent)
    dialog.title('Keyboard shortcuts')
    dialog.transient(parent)
    dialog.resizable(False, False)
    f = fonts(dialog)
    body = ttk.Frame(dialog, padding=px(24))
    body.pack(fill=tk.BOTH, expand=True)
    for row, (keys, action) in enumerate(SHORTCUTS):
        ttk.Label(body, text=keys, font=f['bold']).grid(row=row, column=0, sticky='w', padx=(0, px(16)), pady=px(2))
        ttk.Label(body, text=action, foreground=COLORS['text']).grid(row=row, column=1, sticky='w', pady=px(2))
    close = ttk.Button(body, text='Close', command=dialog.destroy)
    close.grid(row=len(SHORTCUTS), column=0, columnspan=2, sticky='e', pady=(px(16), 0))
    for key in ('<Escape>', '<Return>'):
        dialog.bind(key, lambda _e: dialog.destroy())
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
    menu.add_command(label='Options...', accelerator='Ctrl+,', underline=0, command=on_options)
    if on_toggle_view:
        menu.add_command(label='Switch simple or full view', accelerator='Ctrl+Shift+V', underline=7,
                         command=on_toggle_view)
    menu.add_command(label='Keyboard shortcuts', accelerator='F1', underline=0,
                     command=lambda: show_shortcuts(parent.winfo_toplevel()))
    menu.add_separator()

    def copy():
        copy_diagnostics(parent)
        if on_status:
            on_status('Diagnostics copied. Paste them into a bug report; sign-ins are removed.')

    menu.add_command(label='Copy diagnostics', underline=5, command=copy)
    menu.add_command(label='Open log folder', underline=5, command=on_open_logs)
    return menu


__all__ = ['SHORTCUTS', 'build_menu', 'copy_diagnostics', 'show_shortcuts']
