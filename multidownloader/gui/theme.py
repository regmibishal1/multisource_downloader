"""Colors and fonts from the design brief (section 5), light theme.

Status is never shown by color alone: every status has a glyph and text.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

COLORS = {
    'text': '#1B1B1B',
    'text-2': '#5D5D5D',
    'accent': '#005FB8',
    'success': '#0F7B0F',
    'error': '#C42B1C',
    'warning': '#8A5300',
    'border': '#8A8A8A',
    'divider': '#E0E0E0',
}

_FONTS = {}


def fonts(root: tk.Misc) -> dict:
    """Named fonts derived from Tk's defaults, so OS text-size settings apply."""
    key = str(root.winfo_toplevel())
    if key in _FONTS:
        return _FONTS[key]
    base = tkfont.nametofont('TkDefaultFont')
    family = base.actual('family')
    heading = tkfont.nametofont('TkHeadingFont').actual('family')
    made = {
        'title': tkfont.Font(root=root, family=heading, size=14, weight='bold'),
        'section': tkfont.Font(root=root, family=family, size=11, weight='bold'),
        'body': tkfont.Font(root=root, family=family, size=10),
        'bold': tkfont.Font(root=root, family=family, size=10, weight='bold'),
        'caption': tkfont.Font(root=root, family=family, size=9),
    }
    _FONTS[key] = made
    return made


def link_label(parent, text, command, **kw) -> tk.Label:
    """A text button: accent color, underlined on hover and focus, Enter/Space activate."""
    f = fonts(parent)
    label = tk.Label(parent, text=text, fg=COLORS['accent'], cursor='hand2', font=f['body'],
                     takefocus=1, highlightthickness=2, highlightcolor=COLORS['accent'], **kw)
    underline = tkfont.Font(font=f['body'])
    underline.configure(underline=True)

    def on(_e=None):
        label.configure(font=underline)

    def off(_e=None):
        label.configure(font=f['body'])

    label.bind('<Enter>', on)
    label.bind('<Leave>', off)
    label.bind('<FocusIn>', on)
    label.bind('<FocusOut>', off)
    label.bind('<Button-1>', lambda _e: command())
    label.bind('<Return>', lambda _e: command())
    label.bind('<space>', lambda _e: command())
    return label
