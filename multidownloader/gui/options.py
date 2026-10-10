"""The Options window (brief 6.4): what to save, how files are named, speed, appearance.

Ctrl+, opens it. Changes apply to the next run; the theme applies the next time
the app starts. Save writes settings.json; Cancel and Escape leave it as it was.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from .. import settings
from ..runner import MAX_PARALLEL
from .help import help_button
from .theme import COLORS, fonts, px

QUALITY_LABELS = (
    ('best', 'Best available (the original)'),
    ('1080', 'Up to 1080p'),
    ('720', 'Up to 720p'),
    ('audio', 'Audio only'),
)
LAYOUT_LABELS = (
    ('account', 'Platform, then account', 'x/someone/2025-10-09_1845.mp4'),
    ('platform', 'Platform only', 'x/2025-10-09_1845.mp4'),
    ('flat', 'All in one folder', '2025-10-09_1845.mp4'),
)
THEME_LABELS = (('system', 'Same as Windows'), ('light', 'Light'), ('dark', 'Dark'))

# the settings this window changes
KEYS = ('quality', 'write_metadata', 'thumbnails', 'subtitles', 'layout', 'include_title', 'parallel', 'polite',
        'keep_awake', 'theme', 'check_updates')


def updated(prefs: Dict, choices: Dict) -> Dict:
    """prefs with the window's choices, each checked like settings.load checks it; bad values are ignored."""
    result = dict(prefs)
    for key in KEYS:
        if key not in choices:
            continue
        value = choices[key]
        if key == 'parallel':
            try:
                value = max(1, min(MAX_PARALLEL, int(value)))
            except (TypeError, ValueError):
                continue
        if settings.valid(key, value):
            result[key] = value
    return result


class OptionsWindow(tk.Toplevel):
    def __init__(self, parent, prefs: Dict, *, on_save: Optional[Callable[[Dict], None]] = None):
        super().__init__(parent)
        self.prefs = prefs
        self.on_save = on_save
        self.title('Options')
        self.transient(parent)
        self.minsize(px(520), px(520))
        f = fonts(self)
        self.vars = {
            'quality': tk.StringVar(), 'layout': tk.StringVar(), 'theme': tk.StringVar(),
            'parallel': tk.StringVar(),
            'write_metadata': tk.BooleanVar(), 'thumbnails': tk.BooleanVar(), 'subtitles': tk.BooleanVar(),
            'include_title': tk.BooleanVar(), 'polite': tk.BooleanVar(), 'keep_awake': tk.BooleanVar(),
            'check_updates': tk.BooleanVar(),
        }
        self._load(prefs)

        body = ttk.Frame(self, padding=px(24))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        help_button(self, 'options')
        row = 0

        def section(text, top=24):
            nonlocal row
            ttk.Label(body, text=text, font=f['section']).grid(row=row, column=0, sticky='w',
                                                               pady=(px(top) if row else 0, px(4)))
            row += 1

        def caption(text):
            nonlocal row
            ttk.Label(body, text=text, font=f['caption'], foreground=COLORS['text-2'], wraplength=px(460),
                      justify='left').grid(row=row, column=0, sticky='w', padx=(px(24), 0))
            row += 1

        def check(key, text):
            nonlocal row
            ttk.Checkbutton(body, text=text, variable=self.vars[key]).grid(row=row, column=0, sticky='w',
                                                                         pady=px(2))
            row += 1

        section('Video quality')
        for value, text in QUALITY_LABELS:
            ttk.Radiobutton(body, text=text, value=value, variable=self.vars['quality']).grid(
                row=row, column=0, sticky='w', pady=px(2))
            row += 1
        caption('Photos are always saved as the original.')

        section('What to save')
        check('write_metadata', 'Post details (account, date, caption) in a file next to the media')
        caption('Without them the run report has no post dates.')
        check('thumbnails', 'Thumbnails')
        check('subtitles', "Subtitles (the uploader's own, not automatic ones)")

        section('Folders and file names')
        for value, text, example in LAYOUT_LABELS:
            ttk.Radiobutton(body, text=f'{text}: {example}', value=value, variable=self.vars['layout']).grid(
                row=row, column=0, sticky='w', pady=px(2))
            row += 1
        check('include_title', 'Add the post title to file names')

        section('Speed')
        speed = ttk.Frame(body)
        speed.grid(row=row, column=0, sticky='w', pady=px(2))
        row += 1
        ttk.Label(speed, text='Links at once').pack(side=tk.LEFT, padx=(0, px(8)))
        self.parallel = ttk.Spinbox(speed, from_=1, to=MAX_PARALLEL, width=4, textvariable=self.vars['parallel'],
                                    state='readonly')
        self.parallel.pack(side=tk.LEFT)
        ttk.Label(speed, text=f'(1 to {MAX_PARALLEL}, never two from the same platform)', font=f['caption'],
                  foreground=COLORS['text-2']).pack(side=tk.LEFT, padx=(px(8), 0))
        check('polite', 'Pause a few seconds between links from one platform (recommended)')
        caption('Instagram always pauses 8 to 15 seconds between links.')
        check('keep_awake', 'Keep the computer awake while downloading')

        section('Appearance')
        looks = ttk.Frame(body)
        looks.grid(row=row, column=0, sticky='w', pady=px(2))
        row += 1
        for value, text in THEME_LABELS:
            ttk.Radiobutton(looks, text=text, value=value, variable=self.vars['theme']).pack(side=tk.LEFT,
                                                                                          padx=(0, px(16)))
        caption('Applies the next time you open the app. Windows High Contrast always wins.')

        section('Updates')
        check('check_updates', 'Check once a day whether yt-dlp or gallery-dl needs an update')
        caption('Asks pypi.org for the newest version numbers. Nothing about you or your links is sent.')

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, sticky='ew', pady=(px(24), 0))
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text='Restore defaults', command=self.restore_defaults).grid(row=0, column=0, sticky='w')
        ttk.Button(buttons, text='Cancel', command=self.destroy).grid(row=0, column=1, padx=(0, px(8)))
        self.save_btn = ttk.Button(buttons, text='Save', style='Accent.TButton', command=self.save, default='active')
        self.save_btn.grid(row=0, column=2)

        self.bind('<Escape>', lambda _e: self.destroy())
        self.bind('<Return>', lambda _e: self.save())
        self.save_btn.focus_set()

    def _load(self, prefs: Dict):
        for key, var in self.vars.items():
            var.set(str(prefs.get(key)) if key == 'parallel' else prefs.get(key))

    def choices(self) -> Dict:
        return {key: var.get() for key, var in self.vars.items()}

    def restore_defaults(self):
        defaults = settings.defaults()
        self._load({key: defaults[key] for key in KEYS})

    def save(self):
        new = updated(self.prefs, self.choices())
        self.prefs.update(new)
        if self.on_save:
            self.on_save(self.prefs)
        self.destroy()


__all__ = ['KEYS', 'LAYOUT_LABELS', 'OptionsWindow', 'QUALITY_LABELS', 'THEME_LABELS', 'updated']
