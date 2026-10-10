"""The queue: one row per link with its status and result (brief F1, F15, 6.1).

Columns: Status, Platform, Link, Result. Status is always text plus a glyph,
never colour alone (WCAG 1.4.1). Rows are keyed by the link's URL, which is
unique because the main window removes repeats before a run.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from .. import detect
from .theme import COLORS, fonts

GLYPHS = {
    'waiting': '… Waiting',
    'downloading': '↓',
    'saved': '✓ Saved',
    'already_saved': '= Already saved',
    'failed': '✕ Failed',
    'stopped': '■ Stopped',
    'skipped': '⊘ Skipped',
}
FILTERS = ('All', 'Failed', 'Saved', 'Waiting')
EMPTY = 'Nothing in the queue yet. Paste links above, then select Download.'


@dataclass
class Row:
    url: str
    handler: Optional[str]
    platform: str
    state: str = 'waiting'
    result_text: str = ''
    result: object = None            # runs.JobResult once finished
    extra: Dict = field(default_factory=dict)


def middle(text: str, width: int = 60) -> str:
    """Shorten text in the middle: https://www.instagram.com/...AbC123/"""
    if len(text) <= width:
        return text
    keep = (width - 3) // 2
    return text[:keep] + '...' + text[-keep:]


def _size(n: Optional[int]) -> str:
    if not n:
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def result_text(result) -> str:
    """The Result column for a finished link."""
    status = result.status
    if status == 'saved':
        media = [f for f in result.files if not f.path.endswith(('.json', '.txt'))]
        files = media or result.files
        return files[0].path.rsplit('/', 1)[-1] if len(files) == 1 else f'{len(files)} files'
    if status == 'already_saved':
        when = (result.previous or {}).get('saved_at', '')[:10]
        return f'Saved on {when}' if when else 'Saved before'
    if status == 'skipped':
        return {'unsupported': 'This site is not supported yet.', 'duplicate': 'Same post as another link.'}.get(
            result.skip_reason or '', result.skip_reason or '')
    return result.error_message or ''


class QueueTable(ttk.Frame):
    """A Treeview of Row objects with a filter and an empty state."""

    def __init__(self, parent, *, on_open: Optional[Callable[[Row], None]] = None,
                 on_menu: Optional[Callable[[Row, tk.Event], None]] = None):
        super().__init__(parent)
        self.rows: Dict[str, Row] = {}
        self.filter = 'All'
        self.on_open = on_open
        self.on_menu = on_menu
        f = fonts(self)
        style = ttk.Style(self)
        style.configure('Queue.Treeview', rowheight=28, font=f['body'])
        style.configure('Queue.Treeview.Heading', font=f['bold'])
        style.map('Queue.Treeview', background=[('selected', COLORS['accent'])], foreground=[('selected', '#FFFFFF')])

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=('status', 'platform', 'link', 'result'), show='headings',
                                 style='Queue.Treeview', selectmode='extended')
        for key, label, width, stretch in (('status', 'Status', 130, False), ('platform', 'Platform', 110, False),
                                           ('link', 'Link', 240, True), ('result', 'Result', 220, True)):
            self.tree.heading(key, text=label, anchor='w')
            self.tree.column(key, width=width, minwidth=width, stretch=stretch, anchor='w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        for tone in ('success', 'error', 'warning', 'text-2'):
            self.tree.tag_configure(tone, foreground=COLORS[tone])
        self.empty = ttk.Label(self, text=EMPTY, foreground=COLORS['text-2'], font=f['body'])

        self.tree.bind('<Return>', self._open)
        self.tree.bind('<Double-1>', self._open)
        for sequence in ('<Button-3>', '<Shift-F10>', '<App>'):
            self.tree.bind(sequence, self._menu)
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._see_selection())
        self._refresh_empty()

    # --- content -------------------------------------------------------------------
    def reset(self, links):
        """Replace the queue with these links: [(url, handler or None)]."""
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        for url, handler in links:
            platform = detect.platform_for_url(url)
            self.rows[url] = Row(url, handler, platform.name if platform else '')
        self._redraw()

    def requeue(self, urls):
        """Mark these links waiting again (Retry)."""
        for url in urls:
            row = self.rows.get(url)
            if row:
                row.state, row.result_text, row.result = 'waiting', '', None
                self._show(row)

    def remove(self, urls):
        for url in urls:
            self.rows.pop(url, None)
            if self.tree.exists(url):
                self.tree.delete(url)
        self._refresh_empty()

    def started(self, url):
        row = self.rows.get(url)
        if row:
            row.state, row.result_text = 'downloading', ''
            row.extra['percent'] = 0
            self._show(row)

    def progress(self, url, fraction, downloaded, total):
        row = self.rows.get(url)
        if row and row.state == 'downloading':
            if fraction is not None:
                row.extra['percent'] = int(fraction * 100)
            row.result_text = f'{_size(downloaded)} of {_size(total)}' if total else _size(downloaded)
            self._show(row)

    def finished(self, url, result):
        row = self.rows.get(url)
        if not row:
            return
        row.state = result.status if result.status in GLYPHS else 'failed'
        row.result = result
        row.result_text = result_text(result)
        self._show(row)

    def waiting_note(self, host: str, text: str):
        for row in self.rows.values():
            platform = detect.platform_for_url(row.url)
            if row.state == 'waiting' and platform and platform.key == host:
                row.result_text = text
                self._show(row)

    # --- queries -------------------------------------------------------------------
    def count(self, state: str) -> int:
        return sum(1 for row in self.rows.values() if row.state == state)

    def failed_links(self):
        return [(row.url, row.handler) for row in self.rows.values() if row.state == 'failed' and row.handler]

    def selected(self) -> List[Row]:
        return [self.rows[iid] for iid in self.tree.selection() if iid in self.rows]

    def set_filter(self, name: str):
        self.filter = name if name in FILTERS else 'All'
        self._redraw()

    # --- drawing -------------------------------------------------------------------
    def _visible(self, row: Row) -> bool:
        if self.filter == 'Failed':
            return row.state == 'failed'
        if self.filter == 'Saved':
            return row.state in ('saved', 'already_saved')
        if self.filter == 'Waiting':
            return row.state in ('waiting', 'downloading')
        return True

    def _values(self, row: Row):
        status = GLYPHS[row.state]
        if row.state == 'downloading':
            status = f'↓ {row.extra.get("percent", 0)} %'
        return status, row.platform or '', middle(row.url), row.result_text

    def _tone(self, row: Row) -> str:
        return {'saved': 'success', 'already_saved': 'success', 'failed': 'error', 'waiting': 'text-2',
                'downloading': 'text-2', 'stopped': 'warning', 'skipped': 'text-2'}[row.state]

    def _show(self, row: Row):
        if not self._visible(row):
            if self.tree.exists(row.url):
                self.tree.detach(row.url)
            self._refresh_empty()
            return
        if self.tree.exists(row.url):
            self.tree.item(row.url, values=self._values(row), tags=(self._tone(row),))
            if not self.tree.parent(row.url) and row.url not in self.tree.get_children():
                self.tree.reattach(row.url, '', 'end')
        else:
            self.tree.insert('', 'end', iid=row.url, values=self._values(row), tags=(self._tone(row),))
        self._refresh_empty()

    def _redraw(self):
        for iid in self.tree.get_children():
            self.tree.detach(iid)
        for row in self.rows.values():
            if self._visible(row):
                if self.tree.exists(row.url):
                    self.tree.reattach(row.url, '', 'end')
                    self.tree.item(row.url, values=self._values(row), tags=(self._tone(row),))
                else:
                    self.tree.insert('', 'end', iid=row.url, values=self._values(row), tags=(self._tone(row),))
        self._refresh_empty()

    def _refresh_empty(self):
        if self.tree.get_children():
            self.empty.place_forget()
        else:
            self.empty.configure(text=EMPTY if not self.rows else f'No {self.filter.lower()} links.')
            self.empty.place(relx=0.5, rely=0.5, anchor='center')

    def _see_selection(self):
        selection = self.tree.selection()
        if selection:
            self.tree.see(selection[-1])  # keep the focused row fully visible (WCAG 2.4.11)

    def _open(self, _event=None):
        rows = self.selected()
        if rows and self.on_open:
            self.on_open(rows[0])
        return 'break'

    def _menu(self, event):
        if event.type == tk.EventType.ButtonPress:
            iid = self.tree.identify_row(event.y)
            if iid and iid not in self.tree.selection():
                self.tree.selection_set(iid)
        rows = self.selected()
        if rows and self.on_menu:
            self.on_menu(rows[0], event)
        return 'break'


__all__ = ['FILTERS', 'GLYPHS', 'QueueTable', 'Row', 'middle', 'result_text']
