"""
Multi-source downloader UI

Supports Google Drive, Instagram, TikTok, Threads, Twitter/X, Reddit, Facebook, YouTube.
"""

import argparse
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from typing import Optional

from multidownloader import appdirs, detect, errors as errors_mod, logs, runs, session_store, settings, signins
from multidownloader.batch import links_from_file
from multidownloader.awake import keep_awake
from multidownloader.core import Downloader
from multidownloader import report
from multidownloader.gui import crash, help as help_menu, theme
from multidownloader.gui.options import OptionsWindow
from multidownloader.gui.queue_table import FILTERS, GLYPHS, QueueTable, middle
from multidownloader.gui.signin_sheet import SignInSheet, SignInsWindow, open_folder
from multidownloader.gui.theme import COLORS, fonts, link_label, px
from multidownloader.platforms import BY_HANDLER, BY_KEY
from multidownloader.history import History
from multidownloader.runner import HostWaiting, Job, JobFinished, JobProgress, JobStarted, Runner, mmss
from multidownloader.runs import Run

APP_TITLE = 'Multisource Downloader'

# Shown in the folder field from the start so files never land in whatever
# directory the script happened to be started from.
DEFAULT_OUTPUT_DIR = settings.default_output_root()

MAX_LISTED_ERRORS = 10
MAX_LOG_LINES = 2000
VIEWS = ('full', 'simple')


def simple_result(results, stopped: bool = False) -> str:
    """Simple view's one-line result (brief 6.5): ✓ Saved "Rally at Maitighar" (1 file)."""
    results = list(results)
    if len(results) == 1 and not stopped:
        r = results[0]
        title = ((r.post or {}).get('title') or '').strip().splitlines()
        name = f' "{middle(title[0], 60)}"' if title else ''
        if r.status == 'saved':
            media = [f for f in r.files if not f.path.endswith(('.json', '.txt'))] or r.files
            return f'{GLYPHS["saved"]}{name} ({len(media)} file{"s" if len(media) != 1 else ""}).'
        if r.status == 'already_saved':
            return f'{GLYPHS["already_saved"]}{name}. Nothing new to download.'
        if r.status == 'skipped':
            return f'{GLYPHS["skipped"]}: this site is not supported yet.'
        return f'{GLYPHS["failed"]}: {r.error_message or "the link could not be saved."}'
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    saved = counts.get('saved', 0)
    parts = [f'{saved} saved']
    if counts.get('already_saved'):
        parts.append(f'{counts["already_saved"]} already saved')
    if counts.get('failed'):
        parts.append(f'{counts["failed"]} failed')
    if stopped:
        return f'{GLYPHS["stopped"]}. ' + ', '.join(parts) + '.'
    glyph = '✕' if counts.get('failed') else '✓'
    return f'{glyph} ' + ', '.join(parts) + '.'


class _QueueLogHandler(logging.Handler):
    """Copies log lines (already redacted) to the details pane through a queue."""

    def __init__(self, lines):
        super().__init__(logging.INFO)
        self.lines = lines
        self.addFilter(logs.RedactFilter())
        self.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%H:%M:%S'))

    def emit(self, record):
        try:
            self.lines.put(self.format(record))
        except Exception:  # never let the log break the app
            pass


class _NoPlatform:
    handler = None


_NONE = _NoPlatform()


class _Starting:
    """Stands in for the Runner between Download and the scheduler starting, so the run counts as going."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class DownloaderUI:
    def __init__(self, root, *, offer_resume: bool = True, prefs: Optional[dict] = None, view: Optional[str] = None):
        self.root = root
        self.unfinished = None
        self.prefs = prefs if prefs is not None else settings.defaults()
        self.view = None
        root.title(APP_TITLE)
        f = fonts(root)

        self.dir_var = tk.StringVar(value=self.prefs.get('output_root') or settings.default_output_root())
        self.status_var = tk.StringVar(value='Ready.')
        self.notice_var = tk.StringVar()
        self.signin_summary_var = tk.StringVar()
        self.summary_var = tk.StringVar(value='No links found yet.')
        self._summary_job = None
        self._placeholder_on = False

        self.frm = ttk.Frame(self.root, padding=px(24))
        self.frm.pack(fill=tk.BOTH, expand=True)
        self.frm.columnconfigure(0, weight=1)

        # header: title, sign-in summary and button, resume bar
        header = ttk.Frame(self.frm)
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        self.title_label = ttk.Label(header, text='Multisource Downloader', font=f['title'])
        self.title_label.grid(row=0, column=0, sticky='w')
        self.signin_summary = ttk.Label(header, textvariable=self.signin_summary_var, font=f['caption'])
        self.signin_summary.grid(row=0, column=1, sticky='e', padx=(0, px(8)))
        self.signins_btn = ttk.Button(header, text='Sign-ins', command=self.open_signins)
        self.signins_btn.grid(row=0, column=2, sticky='e')
        self.options_btn = ttk.Button(header, text='Options', command=self.open_options)
        self.options_btn.grid(row=0, column=3, sticky='e', padx=(px(8), 0))
        self.help_btn = ttk.Menubutton(header, text='?', width=2)
        self.help_btn.grid(row=0, column=4, sticky='e', padx=(px(8), 0))
        self.help_btn['menu'] = help_menu.build_menu(self.help_btn, on_options=self.open_options,
                                                     on_open_logs=self.open_log_folder,
                                                     on_status=self.status_var.set,
                                                     on_toggle_view=self.toggle_view)

        # shown only when the last run stopped before it finished (brief T20)
        self.resume_var = tk.StringVar()
        self.resume_bar = ttk.Frame(header, padding=(px(8), px(6)), relief='solid', borderwidth=1)
        self.resume_bar.columnconfigure(0, weight=1)
        ttk.Label(self.resume_bar, textvariable=self.resume_var, foreground=COLORS['warning'],
                  wraplength=px(520), justify='left').grid(row=0, column=0, sticky='w')
        ttk.Button(self.resume_bar, text='Resume', command=self.resume_unfinished).grid(
            row=0, column=1, padx=(px(8), px(4)))
        ttk.Button(self.resume_bar, text='Dismiss', command=self.dismiss_unfinished).grid(row=0, column=2)
        if offer_resume:
            self.check_unfinished()

        # Links (brief 6.1): any pasted text; each link's platform is detected
        links_head = ttk.Frame(self.frm)
        links_head.grid(row=1, column=0, sticky='ew', pady=(px(24), 0))
        links_head.columnconfigure(0, weight=1)
        self.links_label = ttk.Label(links_head, text='Links', font=f['section'])
        self.links_label.grid(row=0, column=0, sticky='w')
        self.links_help = ttk.Label(links_head, text='Paste links or any text that contains them.', font=f['caption'],
                                    foreground=COLORS['text-2'])
        self.links_help.grid(row=1, column=0, sticky='w', pady=(px(4), 0))
        self.links_head = links_head
        self.import_btn = ttk.Button(links_head, text='Import file...', command=self.import_file)
        self.import_btn.grid(row=0, column=1, rowspan=2, sticky='e')

        box = ttk.Frame(self.frm)
        box.grid(row=2, column=0, sticky='ew', pady=(px(8), 0))
        box.columnconfigure(0, weight=1)
        self.links = tk.Text(box, height=6, wrap='none', undo=True, font=f['body'], relief='solid', borderwidth=1,
                             highlightthickness=2, highlightcolor=COLORS['accent'])
        self.links.grid(row=0, column=0, sticky='ew')
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.links.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.links.configure(yscrollcommand=scroll.set)
        self.links.tag_configure('placeholder', foreground=COLORS['text-2'])
        self.links.bind('<FocusIn>', lambda _e: self._placeholder(False))
        self.links.bind('<FocusOut>', lambda _e: self._placeholder(True))
        self.links.bind('<KeyRelease>', lambda _e: self._schedule_summary())
        self.links.bind('<<Paste>>', lambda _e: self.root.after(10, self._schedule_summary))
        # Tab moves on instead of typing a tab (WCAG 2.1.1); Ctrl+Tab still types one
        self.links.bind('<Tab>', lambda e: (e.widget.tk_focusNext().focus_set(), 'break')[1])
        self.links.bind('<Shift-Tab>', lambda e: (e.widget.tk_focusPrev().focus_set(), 'break')[1])
        self._placeholder(True)

        summary = ttk.Frame(self.frm)
        summary.grid(row=3, column=0, sticky='ew', pady=(px(8), 0))
        self.summary_row = summary
        summary.columnconfigure(0, weight=1)
        ttk.Label(summary, textvariable=self.summary_var, font=f['caption'], foreground=COLORS['text-2'],
                  wraplength=px(760), justify='left').grid(row=0, column=0, sticky='w')
        link_label(summary, 'Clear', self.clear_links).grid(row=0, column=1, sticky='e')

        # Save to: remembered between runs (F9, F23)
        folder = ttk.Frame(self.frm)
        folder.grid(row=4, column=0, sticky='ew', pady=(px(16), 0))
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text='Save to').grid(row=0, column=0, sticky='w', padx=(0, px(8)))
        self.dir_entry = ttk.Entry(folder, textvariable=self.dir_var, state='readonly')
        self.dir_entry.grid(row=0, column=1, sticky='ew')
        self.dir_btn = ttk.Button(folder, text='Change...', command=self.select_directory)
        self.dir_btn.grid(row=0, column=2, sticky='e', padx=(px(8), 0))

        self.notice_label = ttk.Label(self.frm, textvariable=self.notice_var, foreground=COLORS['warning'],
                                      wraplength=px(860), justify='left')
        self.notice_label.grid(row=5, column=0, sticky='w', pady=(px(8), 0))

        buttons = ttk.Frame(self.frm)
        buttons.grid(row=6, column=0, sticky='e', pady=(px(16), 0))
        self.download_btn = ttk.Button(buttons, text='Download', style='Accent.TButton', command=self.start_download)
        self.download_btn.pack(side=tk.LEFT)
        self.download_btn.state(['disabled'])
        # simple view has no footer, so its Stop sits next to Download during a run
        self.simple_stop = ttk.Button(buttons, text='Stop', command=self.ask_stop)

        # Queue (brief F1, 6.1)
        queue_head = ttk.Frame(self.frm)
        queue_head.grid(row=7, column=0, sticky='ew', pady=(px(32), px(8)))
        self.queue_head = queue_head
        queue_head.columnconfigure(0, weight=1)
        ttk.Label(queue_head, text='Queue', font=f['section']).grid(row=0, column=0, sticky='w')
        ttk.Label(queue_head, text='Show:').grid(row=0, column=1, sticky='e', padx=(0, px(4)))
        self.filter_var = tk.StringVar(value='All')
        show = ttk.Combobox(queue_head, textvariable=self.filter_var, values=FILTERS, state='readonly', width=9)
        show.grid(row=0, column=2, sticky='e')
        show.bind('<<ComboboxSelected>>', lambda _e: self.table.set_filter(self.filter_var.get()))
        self.retry_btn = ttk.Button(queue_head, text='Retry failed', command=self.retry_failed)
        self.retry_btn.grid(row=0, column=3, sticky='e', padx=(px(12), 0))
        self.retry_btn.grid_remove()

        self.table = QueueTable(self.frm, on_open=self.open_row, on_menu=self.row_menu)
        self.table.grid(row=8, column=0, sticky='nsew')
        self.frm.rowconfigure(8, weight=1)
        self.table.tree.bind('<Delete>', lambda _e: self.remove_rows())

        footer = ttk.Frame(self.frm)
        footer.grid(row=9, column=0, sticky='ew', pady=(px(12), 0))
        self.footer = footer
        footer.columnconfigure(1, weight=1)
        # Stop sits on the left, away from the other actions (brief 6.1)
        self.stop_btn = ttk.Button(footer, text='Stop', command=self.ask_stop)
        self.stop_btn.grid(row=0, column=0, sticky='w')
        self.stop_btn.state(['disabled'])
        self.open_btn = ttk.Button(footer, text='Open folder', command=self.open_output)
        self.open_btn.grid(row=0, column=2, sticky='e', padx=(0, px(8)))
        self.export_btn = ttk.Button(footer, text='Export report', command=self.export_report)
        self.export_btn.grid(row=0, column=3, sticky='e')
        self.export_btn.state(['disabled'])

        # status bar: summary, progress, and the details log (brief F8)
        bar = ttk.Frame(self.frm)
        bar.grid(row=10, column=0, sticky='ew', pady=(px(12), 0))
        bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(bar, textvariable=self.status_var, foreground=COLORS['text-2'],
                                      wraplength=px(640), justify='left')
        self.status_label.grid(row=0, column=0, sticky='w')
        self.progress = ttk.Progressbar(bar, mode='determinate', length=px(200))
        self.progress.grid(row=0, column=1, sticky='e', padx=(px(12), px(12)))
        self.progress.grid_remove()
        self.details_link = link_label(bar, 'Show details', self.toggle_details)
        self.details_link.grid(row=0, column=2, sticky='e')

        # simple view (brief 6.5): Open folder and the way back to the full view
        self.simple_row = ttk.Frame(self.frm)
        self.simple_row.columnconfigure(0, weight=1)
        self.simple_open = ttk.Button(self.simple_row, text='Open folder', command=self.open_output)
        self.simple_open.grid(row=0, column=1, sticky='e')
        more = ttk.Frame(self.simple_row)
        more.grid(row=1, column=0, columnspan=2, sticky='w', pady=(px(8), 0))
        ttk.Label(more, text='More options:', foreground=COLORS['text-2']).pack(side=tk.LEFT, padx=(0, px(4)))
        link_label(more, 'switch to full view', self.toggle_view).pack(side=tk.LEFT)
        self.details = tk.Text(self.frm, height=8, wrap='none', font=f['mono'], state='disabled',
                               relief='solid', borderwidth=1)
        self._log_lines = queue.Queue()
        self._log_handler = _QueueLogHandler(self._log_lines)
        logging.getLogger().addHandler(self._log_handler)
        if self.prefs.get('details_open'):
            self.toggle_details()
        self._last_run = None

        # the download thread never touches Tk: it queues events, read here
        self._events = queue.Queue()
        self._runner = None
        self._counts = {}
        self._waiting_until = {}
        self.root.after(100, self._poll_events)

        # keyboard (brief 6.6)
        root.bind_all('<Control-Return>', lambda _e: self.start_download())
        root.bind_all('<Control-o>', lambda _e: self.import_file())
        root.bind_all('<Control-O>', lambda _e: self.open_output())
        root.bind_all('<Control-l>', lambda _e: self.links.focus_set())
        root.bind_all('<F5>', lambda _e: self.retry_failed())
        root.bind_all('<Control-period>', lambda _e: self.ask_stop())
        root.bind('<Control-comma>', lambda _e: self.open_options())
        root.bind('<Control-Shift-V>', lambda _e: self.toggle_view())
        root.bind('<F1>', lambda _e: help_menu.show_shortcuts(self.root))
        root.bind('<F6>', lambda _e: self.next_region(1))
        root.bind('<Shift-F6>', lambda _e: self.next_region(-1))
        # Alt + the underlined letter selects a button (brief 6.6)
        self._mnemonics = {}
        for button, index in ((self.download_btn, 0), (self.import_btn, 0), (self.dir_btn, 0),
                              (self.signins_btn, 0), (self.options_btn, 1), (self.stop_btn, 1),
                              (self.simple_stop, 1), (self.open_btn, 5), (self.simple_open, 5),
                              (self.export_btn, 0), (self.retry_btn, 0)):
            self.mnemonic(button, index)
        self.set_view(view or self.prefs.get('view') or 'full')
        self.refresh_signin_summary()

    # --- full and simple view (brief F21, 6.5) -----------------------------------------
    def _full_only(self):
        return [self.title_label, self.signin_summary, self.signins_btn, self.options_btn, self.links_help,
                self.import_btn, self.summary_row, self.queue_head, self.table, self.footer, self.details_link]

    def set_view(self, view: str):
        view = view if view in VIEWS else 'full'
        if view == self.view:
            return
        if self.view == 'full':
            self._remember_geometry()
        self.view = view
        self.prefs['view'] = view
        simple = view == 'simple'
        for widget in self._full_only():
            if simple:
                widget.grid_remove()
            else:
                widget.grid()
        if simple:
            self.details.grid_remove()
            self.links_label.configure(text='Paste a link')
            self.links.configure(height=3)
            self.simple_row.grid(row=12, column=0, sticky='ew', pady=(px(8), 0))
            self.frm.rowconfigure(8, weight=0)
            self.root.minsize(px(480), px(300))
            self.root.geometry(f'{px(560)}x{px(300)}')
        else:
            self.simple_row.grid_remove()
            self.links_label.configure(text='Links')
            self.links.configure(height=6)
            self.frm.rowconfigure(8, weight=1)
            if self.prefs.get('details_open'):
                self.details.grid(row=11, column=0, sticky='ew', pady=(px(8), 0))
            self.root.minsize(px(760), px(560))
            self.root.geometry(self.prefs.get('window_geometry') or f'{px(960)}x{px(680)}')
        self._show_stop()

    def toggle_view(self):
        self.set_view('full' if self.view == 'simple' else 'simple')
        self.links.focus_set()

    def _show_stop(self):
        if self.view == 'simple' and self._runner is not None:
            self.simple_stop.pack(side=tk.LEFT, padx=(px(8), 0))
            self.simple_stop.state(['!disabled'] if not self._runner.stopped else ['disabled'])
        else:
            self.simple_stop.pack_forget()

    def _remember_geometry(self):
        # only the full view's size is kept; simple view always opens at its own size
        try:
            if self.view == 'full' and self.root.winfo_ismapped():
                self.prefs['window_geometry'] = self.root.winfo_geometry()
        except tk.TclError:
            pass

    # --- keyboard ----------------------------------------------------------------
    def mnemonic(self, button, index: int):
        letter = button.cget('text')[index].lower()
        button.configure(underline=index)
        buttons = self._mnemonics.setdefault(letter, [])
        buttons.append(button)
        if len(buttons) > 1:
            return  # bound already: the key presses whichever of these buttons is showing

        def press(_event):
            for candidate in buttons:
                if candidate.winfo_ismapped() and candidate.instate(['!disabled']):
                    candidate.invoke()
                    break
            return 'break'

        for key in (letter, letter.upper()):
            self.root.bind(f'<Alt-{key}>', press)

    def regions(self):
        """F6 order: the Links box, the queue, the main buttons, the header."""
        if self.view == 'simple':
            action = self.simple_stop if self._runner is not None else self.download_btn
            return [self.links, action, self.help_btn]
        action = self.stop_btn if self._runner is not None else self.download_btn
        return [self.links, self.table.tree, action, self.signins_btn]

    def next_region(self, step: int = 1):
        regions = self.regions()
        focus = self.root.focus_get()
        current = -1
        for i, widget in enumerate(regions):
            node = focus
            while node is not None:
                if node is widget:
                    current = i
                    break
                node = getattr(node, 'master', None)
            if current >= 0:
                break
        for offset in range(1, len(regions) + 1):
            widget = regions[(current + step * offset) % len(regions)]
            usable = widget.instate(['!disabled']) if isinstance(widget, ttk.Widget) else True
            if usable and widget.winfo_ismapped():
                widget.focus_set()
                return widget
        return None

    def open_options(self):
        if getattr(self, '_options_window', None) is not None and self._options_window.winfo_exists():
            self._options_window.lift()
            self._options_window.focus_set()
            return
        theme_before = self.prefs.get('theme')
        saved = {}

        def on_save(_prefs):
            saved['yes'] = True
            self.save_prefs()

        self._options_window = OptionsWindow(self.root, self.prefs, on_save=on_save)
        self.root.wait_window(self._options_window)
        self._options_window = None
        if saved:
            note = ' The new look applies the next time you open the app.' if self.prefs.get('theme') != theme_before \
                else ''
            self.status_var.set('Options saved. They apply to the next download.' + note)
        self.options_btn.focus_set()

    def open_log_folder(self):
        folder = appdirs.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        open_folder(folder)

    # --- the links box ------------------------------------------------------------
    PLACEHOLDER = 'Paste links here. One per line, or any text that contains them.'

    def _placeholder(self, show: bool):
        empty = not self.links.get('1.0', 'end-1c').strip() or self._placeholder_on
        if show and empty and self.root.focus_get() is not self.links:
            self.links.delete('1.0', 'end')
            self.links.insert('1.0', self.PLACEHOLDER, ('placeholder',))
            self._placeholder_on = True
        elif not show and self._placeholder_on:
            self.links.delete('1.0', 'end')
            self._placeholder_on = False

    def links_text(self) -> str:
        return '' if self._placeholder_on else self.links.get('1.0', 'end-1c')

    def set_links_text(self, text: str):
        self._placeholder(False)
        self.links.delete('1.0', 'end')
        self.links.insert('1.0', text)
        self._update_summary()

    def clear_links(self):
        self.set_links_text('')
        self.links.focus_set()

    def _schedule_summary(self):
        # update 300 ms after typing stops (brief 6.1)
        if self._summary_job:
            self.root.after_cancel(self._summary_job)
        self._summary_job = self.root.after(300, self._update_summary)

    def current_links(self):
        """[(url, handler or None)] for the links in the box, repeats removed, and how many repeats."""
        urls, duplicates = detect.unique_urls(detect.extract_urls(self.links_text()))
        return [(url, (detect.platform_for_url(url) or _NONE).handler) for url in urls], duplicates

    def _update_summary(self):
        self._summary_job = None
        links, duplicates = self.current_links()
        self.summary_var.set(detect.summarize([u for u, _h in links], duplicates))
        supported = sum(1 for _u, h in links if h)
        if self._runner is None:
            noun = 'link' if supported == 1 else 'links'
            self.download_btn.configure(text=f'Download {supported} {noun}' if supported else 'Download')
            self.download_btn.state(['!disabled'] if supported else ['disabled'])

    def import_file(self):
        if self._runner is not None:
            return
        path = filedialog.askopenfilename(parent=self.root, title='Import links from a file', filetypes=[
            ('Lists of links', '*.txt *.csv *.json *.md *.html *.htm'), ('All files', '*.*')])
        if not path:
            return
        try:
            urls = links_from_file(path)
        except OSError as exc:
            messagebox.showerror('Import file', f'Could not read the file: {exc.strerror or exc}', parent=self.root)
            return
        existing = self.links_text().rstrip()
        self.set_links_text((existing + '\n' if existing else '') + '\n'.join(urls))
        self.status_var.set(f'Imported {len(urls)} link{"s" if len(urls) != 1 else ""} from {os.path.basename(path)}.')

    def _lock_input(self, locked: bool):
        # read-only during a run, but the text stays selectable
        self.links.configure(state='disabled' if locked else 'normal')
        for widget in (self.import_btn, self.dir_btn):
            widget.state(['disabled'] if locked else ['!disabled'])

    def save_prefs(self):
        self.prefs['output_root'] = self.dir_var.get()
        self._remember_geometry()
        try:
            settings.save(self.prefs)
        except OSError as exc:
            logging.warning('Could not save settings: %s', exc)

    # ------------------------------------------------------------------
    def check_unfinished(self):
        self.unfinished = runs.last_unfinished_run()
        if self.unfinished:
            self.resume_var.set('! ' + self.unfinished.message())
            self.resume_bar.grid(row=1, column=0, columnspan=5, sticky='ew', pady=(px(10), 0))
        else:
            self.resume_bar.grid_remove()

    def resume_unfinished(self):
        unfinished = self.unfinished
        if not unfinished:
            return
        self.resume_bar.grid_remove()
        self.dir_var.set(str(unfinished.output_root))
        links = [(link['url'], link.get('handler') or (detect.platform_for_url(link['url']) or _NONE).handler)
                 for link in unfinished.remaining]
        links = [(url, handler) for url, handler in links if handler]
        self.start_download([url for url, _h in links], handlers=[h for _u, h in links], resumed=unfinished)

    def dismiss_unfinished(self):
        if self.unfinished:
            self.unfinished.close('dismissed')
        self.unfinished = None
        self.resume_bar.grid_remove()

    def refresh_signin_summary(self):
        summary = signins.summary()
        attention = 'attention' in summary
        self.signin_summary_var.set(f'! {summary}' if attention else summary)
        self.signin_summary.configure(foreground=COLORS['warning'] if attention else COLORS['text-2'])

    def open_signins(self):
        window = SignInsWindow(self.root, on_change=self.refresh_signin_summary)
        self.root.wait_window(window)
        self.refresh_signin_summary()
        self.signins_btn.focus_set()

    def select_directory(self):
        directory = filedialog.askdirectory(parent=self.root, initialdir=self.dir_var.get() or None)
        if directory:
            self.dir_var.set(os.path.normpath(directory))
            self.save_prefs()

    def start_download(self, urls=None, *, handlers=None, resumed=None, keep_queue=False, force=False):
        """Download urls (default: the links in the box), each with its detected platform.

        handlers: one per url (resume, retry). Links from sites that are not
        supported are recorded in the run as skipped, not dropped. keep_queue
        re-queues these rows instead of starting a new list (Retry, Download
        again); force downloads them even if an earlier run saved them.
        """
        if self._runner is not None:
            return
        duplicates = 0
        unsupported = []
        if urls is None:
            # Pull every link out of whatever was pasted (chat exports, reports,
            # comma or space separated lists), drop repeats, detect platforms.
            links, duplicates = self.current_links()
            if not links:
                self.status_var.set('No links found yet. Paste links above, then select Download.')
                return
            unsupported = [url for url, handler in links if not handler]
            urls = [url for url, handler in links if handler]
            handlers = [handler for url, handler in links if handler]
            logging.info(detect.summarize([u for u, _h in links], duplicates))
            if not urls:
                self.status_var.set('None of these links are from a supported site yet.')
                return

        out_dir = self.dir_var.get() or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        logging.info('Starting download for URLs: %s -> %s', urls, out_dir)

        # Warn, but do not block, when a platform that often needs a sign-in
        # has none ready.
        source = None
        warnings = signins.preflight_warnings(urls)
        self.notice_var.set('\n'.join(f'! {w} Use Sign-ins to add one.' for w in warnings))

        downloader = Downloader(out_dir, logger=logging.getLogger('multidownloader'))
        try:
            # links saved in earlier runs are recorded as already saved, not
            # downloaded again; the run is recorded so it can be resumed if it stops
            run = Run(out_dir, history=History(), force=force, last_run_pointer=runs.default_last_run_pointer())
        except OSError as exc:
            messagebox.showerror('Cannot save here', f'Could not write to {out_dir}. Choose another folder.\n\n{exc}',
                                 parent=self.root)
            return
        handlers = list(handlers) if handlers else [(detect.platform_for_url(u) or _NONE).handler for u in urls]
        run.plan([{'url': url, 'handler': handler} for url, handler in zip(urls, handlers)] +
                 [{'url': url, 'handler': None} for url in unsupported])
        if keep_queue:
            self.table.requeue(urls)
        else:
            self.table.reset(list(zip(urls, handlers)) + [(url, None) for url in unsupported])
        for url in unsupported:
            self.table.finished(url, run.skip(url, 'unsupported'))
        self._last_run = run
        self.export_btn.state(['disabled'])
        self.retry_btn.grid_remove()
        if resumed:
            resumed.close('resumed', by_run=run.run_id)

        # everything the worker needs was read above: it must not read Tk variables
        self.save_prefs()
        self.filter_var.set('All')
        self.table.set_filter('All')
        self._lock_input(True)
        self.download_btn.configure(text='Downloading...')
        self.download_btn.state(['disabled'])
        self.stop_btn.state(['!disabled'])
        self._runner = _Starting()
        self._show_stop()
        self._counts = {'total': len(urls)}
        self.progress.configure(maximum=max(1, len(urls)), value=0)
        self.progress.grid()
        self.status_var.set(f'Saving 1 of {len(urls)}. 0 saved, 0 failed.')
        threading.Thread(
            target=self._download_with_core,
            args=(downloader, run, list(zip(urls, handlers)), source, dict(self.prefs)),
            daemon=True,
        ).start()

    def _download_with_core(self, downloader, run, links, source, prefs=None):
        """Download thread: runs the scheduler and reports through self._events only.

        prefs is a copy taken when the run started, so Options changes apply to the next run.
        """
        prefs = prefs if prefs is not None else settings.defaults()
        urls = [url for url, _handler in links]
        # "polite" off drops the short pause between links; Instagram's longer one always stays
        runner = Runner(run, downloader, on_event=self._events.put, max_parallel=prefs.get('parallel', 3),
                        default_delay=None if prefs.get('polite', True) else (0.0, 0.0))
        if isinstance(self._runner, _Starting) and self._runner.stopped:
            runner.stop()  # Stop was pressed before the scheduler was ready
        self._runner = runner
        jobs = [Job(url, handler, settings.download_options(prefs, handler)) for url, handler in links]
        try:
            # hold off system sleep for the whole run (brief T21); on this thread,
            # because Windows applies it per thread
            with keep_awake(prefs.get('keep_awake', True)):
                results = runner.run(jobs)
        except BaseException:
            # the run stays unfinished on disk, so it is offered for resuming;
            # the crash handler shows the error
            self._events.put(('crashed',))
            raise
        run.finish()
        errors = [(r.input_url, r) for r in results if r.status == 'failed']
        self._events.put(('finished', source, urls, errors, run, runner.stopped))

    # --- progress, from the download thread's events ---------------------------------
    def _poll_events(self):
        try:
            while True:
                self._handle_event(self._events.get_nowait())
        except queue.Empty:
            pass
        self._show_waiting()
        self._drain_log()
        self.root.after(100, self._poll_events)

    def _handle_event(self, event):
        c = self._counts
        if isinstance(event, tuple):
            if event[0] == 'finished':
                self._run_finished(*event[1:])
            elif event[0] == 'crashed':
                self._reset_controls()
            return
        if isinstance(event, JobStarted):
            c['started'] = c.get('started', 0) + 1
            self.table.started(event.url)
        elif isinstance(event, JobFinished):
            self.table.finished(event.url, event.result)
            c['done'] = c.get('done', 0) + 1
            status = event.result.status
            c[status] = c.get(status, 0) + 1
            self.progress['value'] = c['done']
            if status not in ('saved', 'already_saved', 'stopped'):
                logging.error('Not saved %s: %s (%s)', event.url, event.result.error_message,
                              event.result.error_detail)
        elif isinstance(event, HostWaiting):
            self._waiting_until[event.host] = (event.until, event.message.split(' Waiting')[0])
            self.table.waiting_note(event.host, event.message.split(' Waiting')[0])
        elif isinstance(event, JobProgress):
            self.table.progress(event.url, event.fraction, event.downloaded, event.total)
        if isinstance(event, (JobStarted, JobFinished, JobProgress)):
            total = c.get('total', 0)
            current = min(total, max(1, c.get('started', 0)))
            self.status_var.set(f'Saving {current} of {total}. {c.get("saved", 0)} saved, '
                                f'{c.get("failed", 0)} failed.')

    def _show_waiting(self):
        now = time.monotonic()
        lines = [f'! {message} Waiting {mmss(until - now)} before trying again.'
                 for until, message in self._waiting_until.values() if until > now]
        if lines or self._waiting_until:
            self.notice_var.set('\n'.join(lines))
            if not lines:
                self._waiting_until.clear()

    def ask_stop(self):
        runner = self._runner
        if not runner or runner.stopped:
            return
        waiting = max(0, self._counts.get('total', 0) - self._counts.get('done', 0))
        if confirm_stop(self.root, waiting):
            runner = self._runner or runner  # the scheduler may have replaced the placeholder meanwhile
            runner.stop()
            self.stop_btn.state(['disabled'])
            self.simple_stop.state(['disabled'])
            self.status_var.set('Stopping after the current files...')

    def _reset_controls(self):
        self.stop_btn.state(['disabled'])
        self.progress.grid_remove()
        self._runner = None
        self._show_stop()
        self._lock_input(False)
        self._update_summary()

    def _run_finished(self, source, urls, errors, run=None, stopped=False):
        """Update the summary and the queue; no dialog except the sign-in offer (brief 6.1)."""
        self._reset_controls()
        self._waiting_until.clear()
        self.notice_var.set('')
        self.refresh_signin_summary()
        self.export_btn.state(['!disabled'] if run else ['disabled'])
        failed = self.table.count('failed')
        self.retry_btn.configure(text=f'Retry failed ({failed})')
        if failed:
            self.retry_btn.grid()
        else:
            self.retry_btn.grid_remove()
        counts = run.counts() if run else {}
        saved = counts.get('saved', len(urls) - len(errors))
        already = counts.get('already_saved', 0)
        extra = f' {already} already saved.' if already else ''
        if stopped:
            self.status_var.set(f'Stopped. {saved} saved, {counts.get("stopped", 0)} not started.{extra}')
            self.root.title(f'Stopped - {APP_TITLE}')
        elif not errors:
            self.status_var.set(f'Done. {saved} saved.{extra}')
            self.root.title(f'Done: {saved} saved - {APP_TITLE}')
        else:
            self.status_var.set(f'Done. {saved} saved, {len(errors)} failed.{extra} '
                                'Select Retry failed to try them again.')
            self.root.title(f'Done: {saved} saved, {len(errors)} failed - {APP_TITLE}')
        if self.view == 'simple' and run is not None:
            self.status_var.set(simple_result(run.results, stopped))
        self._focus_after_run()

        # sign-in errors only; limits and bot checks never lead to a sign-in prompt
        by_platform = {}
        for url, r in errors:
            info = errors_mod.ErrorInfo(r.error_kind or errors_mod.UNKNOWN, r.error_message or '', r.error_action, '')
            if info.needs_signin and r.platform:
                by_platform.setdefault(r.platform, []).append(url)
        platform_key = max(by_platform, key=lambda k: len(by_platform[k])) if by_platform else None
        platform = BY_KEY.get(platform_key) if platform_key else BY_HANDLER.get(source or '')
        auth_failed = by_platform.get(platform_key, [])
        if platform and platform.cookie_domains and auth_failed and not stopped:
            noun = 'link needs' if len(auth_failed) == 1 else 'links need'
            reason = f'{len(auth_failed)} {noun} {signins._a(platform.name)} {platform.name} sign-in.'
            if messagebox.askyesno('Sign-in needed', f'{reason} Sign in now?', parent=self.root):
                self.fix_signin(platform, auth_failed, reason)

    def _focus_after_run(self):
        # the first failed row if any, else Open folder (brief 6.1)
        first_failed = next((row.url for row in self.table.rows.values() if row.state == 'failed'), None)
        if first_failed and self.table.tree.exists(first_failed):
            self.table.tree.selection_set(first_failed)
            self.table.tree.focus(first_failed)
            self.table.tree.focus_set()

    # --- queue actions ----------------------------------------------------------------
    def fix_signin(self, platform, urls, reason=''):
        handlers = [self.table.rows[u].handler for u in urls if u in self.table.rows]
        sheet = SignInSheet(self.root, platform, reason=reason, retry_count=len(urls),
                            on_retry=lambda: self.start_download(urls, handlers=handlers, keep_queue=True),
                            on_change=self.refresh_signin_summary)
        self.root.wait_window(sheet)
        self.refresh_signin_summary()

    def retry_failed(self):
        if self._runner is not None:
            return
        links = self.table.failed_links()
        if links:
            self.start_download([u for u, _h in links], handlers=[h for _u, h in links], keep_queue=True)

    def _row_file(self, row):
        r = row.result
        if r is None:
            return None
        if r.status == 'saved' and r.files and self._last_run:
            media = [f for f in r.files if not f.path.endswith('.json')] or r.files
            return Path(self._last_run.root) / media[0].path
        previous = r.previous or {}
        if r.status == 'already_saved' and previous.get('files'):
            files = [f for f in previous['files'] if not f.endswith('.json')] or previous['files']
            return Path(previous.get('output_root', '')) / files[0]
        return None

    def open_row(self, row):
        path = self._row_file(row)
        if path and path.exists():
            open_folder(path)
        elif row.state == 'failed' and row.result is not None and row.result.error_action == errors_mod.FIX_SIGNIN:
            platform = BY_KEY.get(row.result.platform or '')
            if platform:
                self.fix_signin(platform, [row.url])

    def open_output(self):
        folder = self.dir_var.get()
        if folder and os.path.isdir(folder):
            open_folder(folder)

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def row_menu(self, row, event):
        running = self._runner is not None
        menu = tk.Menu(self.root, tearoff=False)
        path = self._row_file(row)
        menu.add_command(label='Open file', state='normal' if path and path.exists() else 'disabled',
                         command=lambda: open_folder(path))
        menu.add_command(label='Open folder', state='normal' if path and path.parent.exists() else 'disabled',
                         command=lambda: open_folder(path.parent))
        menu.add_command(label='Copy link', command=lambda: self._copy(row.url))
        if row.state == 'failed' and row.result is not None:
            menu.add_command(label='Copy error', command=lambda: self._copy(
                f'{row.result.error_message}\n{row.result.error_detail or ""}'.strip()))
            platform = BY_KEY.get(row.result.platform or '')
            if row.result.error_action == errors_mod.FIX_SIGNIN and platform:
                menu.add_command(label='Fix sign-in', command=lambda: self.fix_signin(platform, [row.url]))
        menu.add_separator()
        can_retry = row.state in ('failed', 'stopped') and row.handler and not running
        menu.add_command(label='Retry', state='normal' if can_retry else 'disabled',
                         command=lambda: self.start_download([row.url], handlers=[row.handler], keep_queue=True))
        can_again = row.state in ('saved', 'already_saved') and row.handler and not running
        menu.add_command(label='Download again', state='normal' if can_again else 'disabled',
                         command=lambda: self.start_download([row.url], handlers=[row.handler], keep_queue=True,
                                                             force=True))
        menu.add_command(label='Remove from list', state='disabled' if running else 'normal',
                         command=self.remove_rows)
        try:
            x = event.x_root if event.x_root else self.table.tree.winfo_rootx() + 40
            y = event.y_root if event.y_root else self.table.tree.winfo_rooty() + 40
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def remove_rows(self):
        if self._runner is None:
            self.table.remove([row.url for row in self.table.selected()])

    def export_report(self):
        run = self._last_run
        if run is None or not run.finished:
            return
        target = filedialog.asksaveasfilename(parent=self.root, title='Export report', defaultextension='.html',
                                              initialfile=f'report-{run.run_id}.html',
                                              filetypes=[('Web page', '*.html')])
        if not target:
            return
        include = messagebox.askyesno(
            'Include account names?',
            'The report leaves out the account names of the people in the posts, because a shared report could '
            'put them at risk. Include them anyway?', parent=self.root, default='no')
        path = report.export(run.dir, run.results, target, include_accounts=include)
        self.status_var.set(f'Report saved: {path} (and {path.with_suffix(".csv").name}).')

    # --- details log (brief F8) -------------------------------------------------------
    def toggle_details(self):
        if self.details.grid_info():  # placed in the layout, whether or not the window is on screen
            self.details.grid_remove()
            self.details_link.configure(text='Show details')
            self.prefs['details_open'] = False
        else:
            self.details.grid(row=11, column=0, sticky='ew', pady=(px(8), 0))
            self.details_link.configure(text='Hide details')
            self.prefs['details_open'] = True

    def _drain_log(self):
        lines = []
        try:
            while True:
                lines.append(self._log_lines.get_nowait())
        except queue.Empty:
            pass
        if not lines:
            return
        self.details.configure(state='normal')
        self.details.insert('end', '\n'.join(lines) + '\n')
        excess = int(self.details.index('end-1c').split('.')[0]) - MAX_LOG_LINES
        if excess > 0:
            self.details.delete('1.0', f'{excess + 1}.0')
        self.details.see('end')
        self.details.configure(state='disabled')


def confirm_stop(parent, waiting: int) -> bool:
    """The brief's Stop confirmation: Keep downloading is the default, Enter never stops."""
    dialog = tk.Toplevel(parent)
    dialog.title('Stop downloading?')
    dialog.transient(parent)
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=px(24))
    body.pack(fill=tk.BOTH, expand=True)
    noun = 'link is' if waiting == 1 else 'links are'
    ttk.Label(body, wraplength=px(360), justify='left',
              text=f'{waiting} {noun} still waiting. The current file will be removed if it is not finished.'
              ).pack(anchor='w')
    answer = {'stop': False}

    def stop():
        answer['stop'] = True
        dialog.destroy()

    buttons = ttk.Frame(body)
    buttons.pack(anchor='e', pady=(px(16), 0))
    keep = ttk.Button(buttons, text='Keep downloading', command=dialog.destroy)
    keep.pack(side=tk.LEFT, padx=(0, px(8)))
    ttk.Button(buttons, text='Stop', command=stop).pack(side=tk.LEFT)
    dialog.bind('<Escape>', lambda _e: dialog.destroy())
    keep.focus_set()
    dialog.grab_set()
    parent.wait_window(dialog)
    return answer['stop']


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='multisource-downloader', description='Multisource Downloader window')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--simple', action='store_true', help='Open the simple view: one link box and Download')
    group.add_argument('--full', action='store_true', help='Open the full view with the queue')
    return parser.parse_args(argv)


def start_view(args, prefs) -> str:
    """--simple or --full win; else the last view used; the Windows app starts simple the first time."""
    if args.simple:
        return 'simple'
    if args.full:
        return 'full'
    if prefs.get('view') in VIEWS and settings.settings_path().exists():
        return prefs['view']
    return 'simple' if getattr(sys, 'frozen', False) else 'full'


def main(argv=None):
    args = parse_args(argv)
    # one rotating, redacted log file in the per-user log folder (brief T15)
    logs.setup()
    session_store.migrate_legacy_sessions()
    theme.enable_dpi_awareness()  # before the first window
    prefs = settings.load()
    root = tk.Tk()
    theme.apply(root, prefs.get('theme', 'system'))  # before any widget, which takes its colours when made
    app = DownloaderUI(root, prefs=prefs, view=start_view(args, prefs))

    def close():
        app.save_prefs()
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    # errors in callbacks and worker threads get a dialog instead of a silent freeze
    crash.install(root, on_crash=app._reset_controls)
    root.mainloop()


if __name__ == '__main__':
    main()
