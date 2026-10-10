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
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from multidownloader import detect, errors as errors_mod, logs, runs, session_store, signins
from multidownloader.awake import keep_awake
from multidownloader.core import Downloader
from multidownloader.gui import crash
from multidownloader.gui.signin_sheet import SignInSheet, SignInsWindow
from multidownloader.gui.theme import COLORS
from multidownloader.platforms import BY_HANDLER
from multidownloader.history import History
from multidownloader.runner import HostWaiting, Job, JobFinished, JobProgress, JobStarted, Runner, mmss
from multidownloader.runs import Run

SOURCE_CHOICES = [
    'Google Drive',
    'Instagram',
    'TikTok',
    'Threads',
    'Twitter',
    'Reddit',
    'Facebook',
    'YouTube',
    'Bluesky',
]

# Shown in the folder field from the start so files never land in whatever
# directory the script happened to be started from.
DEFAULT_OUTPUT_DIR = str(Path.home() / 'Downloads' / 'Multisource')

COOKIE_SOURCES = {'TikTok', 'Threads', 'Twitter', 'Reddit', 'Facebook', 'YouTube'}
MAX_LISTED_ERRORS = 10


class DownloaderUI:
    def __init__(self, root, *, offer_resume: bool = True):
        self.root = root
        self.unfinished = None
        root.title('Multi-Source Downloader')

        self.urls_var = tk.StringVar()
        self.dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        self.status_var = tk.StringVar()
        self.notice_var = tk.StringVar()
        self.signin_summary_var = tk.StringVar()
        self.source_var = tk.StringVar(value=SOURCE_CHOICES[0])

        self.frm = ttk.Frame(self.root, padding=20)
        self.frm.pack(fill=tk.BOTH, expand=True)
        self.frm.columnconfigure(0, weight=1)
        self.frm.columnconfigure(1, weight=1)

        header = ttk.Frame(self.frm)
        header.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        header.columnconfigure(0, weight=1)
        self.signin_summary = ttk.Label(header, textvariable=self.signin_summary_var)
        self.signin_summary.grid(row=0, column=1, sticky='e', padx=(0, 8))
        self.signins_btn = ttk.Button(header, text='Sign-ins', command=self.open_signins)
        self.signins_btn.grid(row=0, column=2, sticky='e')

        # shown only when the last run stopped before it finished (brief T20)
        self.resume_var = tk.StringVar()
        self.resume_bar = ttk.Frame(header, padding=(8, 6), relief='solid', borderwidth=1)
        self.resume_bar.columnconfigure(0, weight=1)
        ttk.Label(self.resume_bar, textvariable=self.resume_var, foreground=COLORS['warning'],
                  wraplength=420, justify='left').grid(row=0, column=0, sticky='w')
        ttk.Button(self.resume_bar, text='Resume', command=self.resume_unfinished).grid(row=0, column=1, padx=(8, 4))
        ttk.Button(self.resume_bar, text='Dismiss', command=self.dismiss_unfinished).grid(row=0, column=2)
        if offer_resume:
            self.check_unfinished()

        ttk.Label(self.frm, text='Links (paste them, or any text that contains them):', font=('Segoe UI', 10, 'bold')).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(0, 5)
        )
        self.url_entry = ttk.Entry(self.frm, textvariable=self.urls_var, width=60)
        self.url_entry.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(0, 10))

        self.dir_btn = ttk.Button(self.frm, text='Select Download Directory', command=self.select_directory)
        self.dir_btn.grid(row=3, column=0, sticky='e', pady=(0, 10), padx=(0, 5))
        self.dir_entry = ttk.Entry(self.frm, textvariable=self.dir_var, state='readonly', width=50)
        self.dir_entry.grid(row=3, column=1, sticky='w', pady=(0, 10), padx=(0, 5))

        ttk.Label(self.frm, text='Source:').grid(row=4, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.src_menu = ttk.Combobox(self.frm, textvariable=self.source_var, values=SOURCE_CHOICES, state='readonly', width=20)
        self.src_menu.grid(row=4, column=1, sticky='w', pady=(0, 5), padx=(0, 5))

        buttons = ttk.Frame(self.frm)
        buttons.grid(row=6, column=0, columnspan=2, pady=10)
        self.download_btn = ttk.Button(buttons, text='Download', command=self.start_download)
        self.download_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(buttons, text='Stop', command=self.ask_stop)
        self.stop_btn.pack(side=tk.LEFT)
        self.stop_btn.state(['disabled'])
        self.progress = ttk.Progressbar(self.frm, mode='determinate', length=520)
        self.progress.grid(row=9, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        self.progress.grid_remove()
        # the download thread never touches Tk: it queues events, read here
        self._events = queue.Queue()
        self._runner = None
        self._counts = {}
        self._waiting_until = {}
        self.root.after(100, self._poll_events)

        self.status_label = ttk.Label(self.frm, textvariable=self.status_var, foreground=COLORS['text-2'])
        self.status_label.grid(row=7, column=0, columnspan=2, sticky='w', pady=(0, 5))
        self.notice_label = ttk.Label(self.frm, textvariable=self.notice_var, foreground=COLORS['warning'],
                                      wraplength=520, justify='left')
        self.notice_label.grid(row=8, column=0, columnspan=2, sticky='w')

        self.refresh_signin_summary()

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
        links = [(link['url'], link.get('handler') or self.source_var.get()) for link in unfinished.remaining]
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
        directory = filedialog.askdirectory()
        if directory:
            self.dir_var.set(directory)

    def start_download(self, urls=None, *, handlers=None, resumed=None):
        """Download urls (default: the links in the box). handlers: one per url (resume of a mixed run)."""
        if urls is None:
            # Pull every link out of whatever was pasted (chat exports,
            # reports, comma or space separated lists) and drop repeats.
            urls, duplicates = detect.unique_urls(detect.extract_urls(self.urls_var.get()))
            if not urls:
                messagebox.showerror('No links', 'No links found. Paste one or more links.', parent=self.root)
                logging.warning('No URLs entered for download')
                return
            logging.info(detect.summarize(urls, duplicates))

        out_dir = self.dir_var.get() or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        logging.info('Starting download for URLs: %s -> %s', urls, out_dir)

        # Warn, but do not block, when a platform that often needs a sign-in
        # has none ready.
        source = self.source_var.get()
        warnings = signins.preflight_warnings(urls)
        platform = BY_HANDLER.get(source)
        if platform and platform.signin_need == 'often' and signins.status(platform).state not in ('ready', 'unknown'):
            line = f'{platform.name} may refuse some of these links without a sign-in.'
            if line not in warnings:
                warnings.append(line)
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
        handlers = list(handlers) if handlers else [source] * len(urls)
        run.plan({'url': url, 'handler': handler} for url, handler in zip(urls, handlers))
        if resumed:
            resumed.close('resumed', by_run=run.run_id)

        # source was read above: the worker thread must not read Tk variables,
        # and changing the dropdown mid-run must not reroute remaining links
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
        self.download_btn.state(['!disabled'])
        self.stop_btn.state(['disabled'])
        self.progress.grid_remove()
        self._runner = None

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
                self.root.title('Stopped - Multi-Source Downloader')
                return
        if not errors:
            self.status_var.set(f'Done. {saved} saved.{report}')
            self.root.title('Multi-Source Downloader')
            return
        self.status_var.set(f'Done. {saved} saved, {len(errors)} failed.{report}')
        self.root.title(f'Done: {saved} saved, {len(errors)} failed - Multi-Source Downloader')

        infos = [(url, errors_mod.ErrorInfo(r.error_kind or errors_mod.UNKNOWN, r.error_message or '',
                                            r.error_action, r.error_detail or '')) for url, r in errors]
        platform = BY_HANDLER.get(source)
        # sign-in errors only; limits and bot checks never lead to a sign-in prompt
        auth_failed = [url for url, info in infos if info.needs_signin]
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
    app = DownloaderUI(root)
    # errors in callbacks and worker threads get a dialog instead of a silent freeze
    crash.install(root, on_crash=app._reset_controls)
    root.mainloop()


if __name__ == '__main__':
    main()
