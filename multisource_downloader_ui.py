"""
Multi-source downloader UI

Supports Google Drive, Instagram, TikTok, Threads, Twitter/X, Reddit, Facebook, YouTube.
"""

import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from typing import Optional

from multidownloader import detect, errors as errors_mod, logs, runs, session_store, settings, signins
from multidownloader.batch import links_from_file
from multidownloader.awake import keep_awake
from multidownloader.core import Downloader
from multidownloader.gui import crash
from multidownloader.gui.signin_sheet import SignInSheet, SignInsWindow
from multidownloader.gui.theme import COLORS, fonts, link_label
from multidownloader.platforms import BY_HANDLER, BY_KEY
from multidownloader.history import History
from multidownloader.runner import HostWaiting, Job, JobFinished, JobProgress, JobStarted, Runner, mmss
from multidownloader.runs import Run

APP_TITLE = 'Multisource Downloader'

# Shown in the folder field from the start so files never land in whatever
# directory the script happened to be started from.
DEFAULT_OUTPUT_DIR = settings.default_output_root()

COOKIE_SOURCES = {'TikTok', 'Threads', 'Twitter', 'Reddit', 'Facebook', 'YouTube'}
MAX_LISTED_ERRORS = 10


class _NoPlatform:
    handler = None


_NONE = _NoPlatform()


class DownloaderUI:
    def __init__(self, root, *, offer_resume: bool = True, prefs: Optional[dict] = None):
        self.root = root
        self.unfinished = None
        self.prefs = prefs if prefs is not None else settings.defaults()
        root.title(APP_TITLE)
        root.minsize(760, 560)
        if self.prefs.get('window_geometry'):
            root.geometry(self.prefs['window_geometry'])
        else:
            root.geometry('960x680')
        f = fonts(root)

        self.dir_var = tk.StringVar(value=self.prefs.get('output_root') or settings.default_output_root())
        self.status_var = tk.StringVar(value='Ready.')
        self.notice_var = tk.StringVar()
        self.signin_summary_var = tk.StringVar()
        self.summary_var = tk.StringVar(value='No links found yet.')
        self._summary_job = None
        self._placeholder_on = False

        self.frm = ttk.Frame(self.root, padding=24)
        self.frm.pack(fill=tk.BOTH, expand=True)
        self.frm.columnconfigure(0, weight=1)

        # header: title, sign-in summary and button, resume bar
        header = ttk.Frame(self.frm)
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='Multisource Downloader', font=f['title']).grid(row=0, column=0, sticky='w')
        self.signin_summary = ttk.Label(header, textvariable=self.signin_summary_var, font=f['caption'])
        self.signin_summary.grid(row=0, column=1, sticky='e', padx=(0, 8))
        self.signins_btn = ttk.Button(header, text='Sign-ins', command=self.open_signins)
        self.signins_btn.grid(row=0, column=2, sticky='e')

        # shown only when the last run stopped before it finished (brief T20)
        self.resume_var = tk.StringVar()
        self.resume_bar = ttk.Frame(header, padding=(8, 6), relief='solid', borderwidth=1)
        self.resume_bar.columnconfigure(0, weight=1)
        ttk.Label(self.resume_bar, textvariable=self.resume_var, foreground=COLORS['warning'],
                  wraplength=520, justify='left').grid(row=0, column=0, sticky='w')
        ttk.Button(self.resume_bar, text='Resume', command=self.resume_unfinished).grid(row=0, column=1, padx=(8, 4))
        ttk.Button(self.resume_bar, text='Dismiss', command=self.dismiss_unfinished).grid(row=0, column=2)
        if offer_resume:
            self.check_unfinished()

        # Links (brief 6.1): any pasted text; each link's platform is detected
        links_head = ttk.Frame(self.frm)
        links_head.grid(row=1, column=0, sticky='ew', pady=(24, 0))
        links_head.columnconfigure(0, weight=1)
        ttk.Label(links_head, text='Links', font=f['section']).grid(row=0, column=0, sticky='w')
        ttk.Label(links_head, text='Paste links or any text that contains them.', font=f['caption'],
                  foreground=COLORS['text-2']).grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.import_btn = ttk.Button(links_head, text='Import file...', command=self.import_file)
        self.import_btn.grid(row=0, column=1, rowspan=2, sticky='e')

        box = ttk.Frame(self.frm)
        box.grid(row=2, column=0, sticky='ew', pady=(8, 0))
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
        summary.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        summary.columnconfigure(0, weight=1)
        ttk.Label(summary, textvariable=self.summary_var, font=f['caption'], foreground=COLORS['text-2'],
                  wraplength=760, justify='left').grid(row=0, column=0, sticky='w')
        link_label(summary, 'Clear', self.clear_links).grid(row=0, column=1, sticky='e')

        # Save to: remembered between runs (F9, F23)
        folder = ttk.Frame(self.frm)
        folder.grid(row=4, column=0, sticky='ew', pady=(16, 0))
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text='Save to').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.dir_entry = ttk.Entry(folder, textvariable=self.dir_var, state='readonly')
        self.dir_entry.grid(row=0, column=1, sticky='ew')
        self.dir_btn = ttk.Button(folder, text='Change...', command=self.select_directory)
        self.dir_btn.grid(row=0, column=2, sticky='e', padx=(8, 0))

        buttons = ttk.Frame(self.frm)
        buttons.grid(row=6, column=0, sticky='e', pady=(16, 0))
        self.stop_btn = ttk.Button(buttons, text='Stop', command=self.ask_stop)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn.state(['disabled'])
        self.download_btn = ttk.Button(buttons, text='Download', command=self.start_download)
        self.download_btn.pack(side=tk.LEFT)
        self.download_btn.state(['disabled'])
        self.progress = ttk.Progressbar(self.frm, mode='determinate')
        self.progress.grid(row=9, column=0, sticky='ew', pady=(6, 0))
        self.progress.grid_remove()
        # the download thread never touches Tk: it queues events, read here
        self._events = queue.Queue()
        self._runner = None
        self._counts = {}
        self._waiting_until = {}
        self.root.after(100, self._poll_events)

        self.status_label = ttk.Label(self.frm, textvariable=self.status_var, foreground=COLORS['text-2'],
                                      wraplength=860, justify='left')
        self.status_label.grid(row=7, column=0, sticky='w', pady=(16, 5))
        self.notice_label = ttk.Label(self.frm, textvariable=self.notice_var, foreground=COLORS['warning'],
                                      wraplength=860, justify='left')
        self.notice_label.grid(row=8, column=0, sticky='w')

        # keyboard (brief 6.6)
        root.bind_all('<Control-Return>', lambda _e: self.start_download())
        root.bind_all('<Control-o>', lambda _e: self.import_file())
        root.bind_all('<Control-l>', lambda _e: self.links.focus_set())
        self.refresh_signin_summary()

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
        try:
            self.prefs['window_geometry'] = self.root.winfo_geometry()
        except tk.TclError:
            pass
        try:
            settings.save(self.prefs)
        except OSError as exc:
            logging.warning('Could not save settings: %s', exc)

    # ------------------------------------------------------------------
    def check_unfinished(self):
        self.unfinished = runs.last_unfinished_run()
        if self.unfinished:
            self.resume_var.set('! ' + self.unfinished.message())
            self.resume_bar.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(10, 0))
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

    def start_download(self, urls=None, *, handlers=None, resumed=None):
        """Download urls (default: the links in the box), each with its detected platform.

        handlers: one per url, for a resumed run. Links from sites that are not
        supported are recorded in the run as skipped, not dropped.
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
            run = Run(out_dir, history=History(), last_run_pointer=runs.default_last_run_pointer())
        except OSError as exc:
            messagebox.showerror('Cannot save here', f'Could not write to {out_dir}. Choose another folder.\n\n{exc}',
                                 parent=self.root)
            return
        handlers = list(handlers) if handlers else [(detect.platform_for_url(u) or _NONE).handler for u in urls]
        run.plan([{'url': url, 'handler': handler} for url, handler in zip(urls, handlers)] +
                 [{'url': url, 'handler': None} for url in unsupported])
        for url in unsupported:
            run.skip(url, 'unsupported')
        if resumed:
            resumed.close('resumed', by_run=run.run_id)

        # everything the worker needs was read above: it must not read Tk variables
        self.save_prefs()
        self._lock_input(True)
        self.download_btn.configure(text='Downloading...')
        self.download_btn.state(['disabled'])
        self.stop_btn.state(['!disabled'])
        self._counts = {'total': len(urls)}
        self.progress.configure(maximum=max(1, len(urls)), value=0)
        self.progress.grid()
        self.status_var.set(f'Saving 1 of {len(urls)}. 0 saved, 0 failed.')
        threading.Thread(
            target=self._download_with_core,
            args=(downloader, run, list(zip(urls, handlers)), source),
            daemon=True,
        ).start()

    @staticmethod
    def _options(handler):
        # Google Drive needs no option: public first, the saved sign-in only
        # when Drive refuses the link
        opts = {}
        if handler == 'Instagram':
            opts['auth'] = 'auto'
        if handler in COOKIE_SOURCES:
            opts['use_session'] = True
        return opts

    def _download_with_core(self, downloader, run, links, source):
        """Download thread: runs the scheduler and reports through self._events only."""
        urls = [url for url, _handler in links]
        runner = Runner(run, downloader, on_event=self._events.put)
        self._runner = runner
        jobs = [Job(url, handler, self._options(handler)) for url, handler in links]
        try:
            # hold off system sleep for the whole run (brief T21); on this thread,
            # because Windows applies it per thread
            with keep_awake():
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
        elif isinstance(event, JobFinished):
            c['done'] = c.get('done', 0) + 1
            status = event.result.status
            c[status] = c.get(status, 0) + 1
            self.progress['value'] = c['done']
            if status not in ('saved', 'already_saved', 'stopped'):
                logging.error('Not saved %s: %s (%s)', event.url, event.result.error_message,
                              event.result.error_detail)
        elif isinstance(event, HostWaiting):
            self._waiting_until[event.host] = (event.until, event.message.split(' Waiting')[0])
        elif isinstance(event, JobProgress) and event.fraction is not None:
            c['last_progress'] = f' {event.fraction * 100:.0f} %'
        if isinstance(event, (JobStarted, JobFinished, JobProgress)):
            total = c.get('total', 0)
            current = min(total, max(1, c.get('started', 0)))
            self.status_var.set(f'Saving {current} of {total}. {c.get("saved", 0)} saved, '
                                f'{c.get("failed", 0)} failed.{c.get("last_progress", "")}')

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
            runner.stop()
            self.stop_btn.state(['disabled'])
            self.status_var.set('Stopping after the current files...')

    def _reset_controls(self):
        self.stop_btn.state(['disabled'])
        self.progress.grid_remove()
        self._runner = None
        self._lock_input(False)
        self._update_summary()

    def _run_finished(self, source, urls, errors, run=None, stopped=False):
        """One summary for the whole run instead of a popup per failed link."""
        self._reset_controls()
        self._waiting_until.clear()
        self.notice_var.set('')
        self.refresh_signin_summary()
        saved = len(urls) - len(errors)
        report = f' Report: {run.dir}' if run else ''
        if run:
            counts = run.counts()
            already = counts.get('already_saved', 0)
            saved = counts.get('saved', 0)
            if already:
                report = f' {already} already saved.' + report
            if stopped:
                not_started = counts.get('stopped', 0)
                self.status_var.set(f'Stopped. {saved} saved, {not_started} not started.{report}')
                self.root.title(f'Stopped - {APP_TITLE}')
                return
        if not errors:
            self.status_var.set(f'Done. {saved} saved.{report}')
            self.root.title(f'Done: {saved} saved - {APP_TITLE}')
            return
        self.status_var.set(f'Done. {saved} saved, {len(errors)} failed. '
                            f'Select Retry failed to try them again.{report}')
        self.root.title(f'Done: {saved} saved, {len(errors)} failed - {APP_TITLE}')

        infos = [(url, errors_mod.ErrorInfo(r.error_kind or errors_mod.UNKNOWN, r.error_message or '',
                                            r.error_action, r.error_detail or '')) for url, r in errors]
        # sign-in errors only; limits and bot checks never lead to a sign-in prompt
        by_platform = {}
        for (url, r), (_u, info) in zip(errors, infos):
            if info.needs_signin and r.platform:
                by_platform.setdefault(r.platform, []).append(url)
        platform_key = max(by_platform, key=lambda k: len(by_platform[k])) if by_platform else None
        platform = BY_KEY.get(platform_key) if platform_key else BY_HANDLER.get(source or '')
        auth_failed = by_platform.get(platform_key, [])
        if platform and platform.cookie_domains and auth_failed:
            noun = 'link needs' if len(auth_failed) == 1 else 'links need'
            reason = f'{len(auth_failed)} {noun} a {platform.name} sign-in.'
            if messagebox.askyesno('Sign-in needed', f'{reason} Sign in now?', parent=self.root):
                sheet = SignInSheet(self.root, platform, reason=reason, retry_count=len(auth_failed),
                                    on_retry=lambda: self.start_download(auth_failed),
                                    on_change=self.refresh_signin_summary)
                self.root.wait_window(sheet)
                self.refresh_signin_summary()
                return

        # one block per cause: "3 links: This post was deleted or the link is wrong."
        blocks = []
        for info, group_urls in errors_mod.group(infos)[:MAX_LISTED_ERRORS]:
            noun = 'link' if len(group_urls) == 1 else 'links'
            shown = '\n'.join(group_urls[:3]) + (f'\n...and {len(group_urls) - 3} more' if len(group_urls) > 3 else '')
            blocks.append(f'{len(group_urls)} {noun}: {info.message}\n{shown}')
        listed = '\n\n'.join(blocks) + f'\n\nDetails are in {logs.log_path()}.'
        messagebox.showerror('Some downloads failed', listed, parent=self.root)


def confirm_stop(parent, waiting: int) -> bool:
    """The brief's Stop confirmation: Keep downloading is the default, Enter never stops."""
    dialog = tk.Toplevel(parent)
    dialog.title('Stop downloading?')
    dialog.transient(parent)
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=24)
    body.pack(fill=tk.BOTH, expand=True)
    noun = 'link is' if waiting == 1 else 'links are'
    ttk.Label(body, wraplength=360, justify='left',
              text=f'{waiting} {noun} still waiting. The current file will be removed if it is not finished.'
              ).pack(anchor='w')
    answer = {'stop': False}

    def stop():
        answer['stop'] = True
        dialog.destroy()

    buttons = ttk.Frame(body)
    buttons.pack(anchor='e', pady=(16, 0))
    keep = ttk.Button(buttons, text='Keep downloading', command=dialog.destroy)
    keep.pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(buttons, text='Stop', command=stop).pack(side=tk.LEFT)
    dialog.bind('<Escape>', lambda _e: dialog.destroy())
    keep.focus_set()
    dialog.grab_set()
    parent.wait_window(dialog)
    return answer['stop']


def main():
    # one rotating, redacted log file in the per-user log folder (brief T15)
    logs.setup()
    session_store.migrate_legacy_sessions()
    root = tk.Tk()
    app = DownloaderUI(root, prefs=settings.load())

    def close():
        app.save_prefs()
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    # errors in callbacks and worker threads get a dialog instead of a silent freeze
    crash.install(root, on_crash=app._reset_controls)
    root.mainloop()


if __name__ == '__main__':
    main()
