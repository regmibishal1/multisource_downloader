"""
Multi-source downloader UI

Supports Google Drive, Instagram, TikTok, Threads, Twitter/X, Reddit, Facebook, YouTube.
"""

import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from multidownloader import detect, logs, runs, session_store, signins
from multidownloader.awake import keep_awake
from multidownloader.core import Downloader
from multidownloader.gui import crash
from multidownloader.gui.signin_sheet import SignInSheet, SignInsWindow
from multidownloader.gui.theme import COLORS
from multidownloader.platforms import BY_HANDLER
from multidownloader.history import History
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

        self.download_btn = ttk.Button(self.frm, text='Download', command=self.start_download)
        self.download_btn.grid(row=6, column=0, columnspan=2, pady=10)

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
        threading.Thread(
            target=self._download_with_core,
            args=(downloader, run, list(zip(urls, handlers)), source),
            daemon=True,
        ).start()

    def _download_with_core(self, downloader, run, links, source):
        errors = []
        urls = [url for url, _handler in links]
        try:
            # hold off system sleep for the whole run (brief T21); on this thread,
            # because Windows applies it per thread
            with keep_awake():
                for i, (url, handler) in enumerate(links, 1):
                    # Google Drive needs no option: public first, the saved sign-in
                    # only when Drive refuses the link
                    opts = {}
                    if handler == 'Instagram':
                        opts['auth'] = 'auto'
                    if handler in COOKIE_SOURCES:
                        opts['use_session'] = True

                    self.root.after(0, self.status_var.set, f'Saving {i} of {len(links)}: {url}')
                    logging.info('Delegating download to core: %s %s %s', handler, url, opts)
                    # staging, hashing and the manifest happen inside run.download
                    result = run.download(downloader, handler, url, opts)
                    if result.status == 'saved':
                        logging.info('Saved %s: %s file(s)', url, len(result.files))
                    elif result.status == 'already_saved':
                        logging.info('Already saved %s on %s', url, result.previous['saved_at'][:10])
                    else:
                        errors.append((url, result.error_detail or result.error_message or ''))
                        logging.error('Core download error for %s: %s', url, result.error_message)
        except BaseException:
            # the run stays unfinished on disk, so it is offered for resuming;
            # the crash handler shows the error
            self.root.after(0, self.download_btn.state, ['!disabled'])
            raise
        run.finish()
        self.root.after(0, self._run_finished, source, urls, errors, run)

    def _run_finished(self, source, urls, errors, run=None):
        """One summary for the whole run instead of a popup per failed link."""
        self.download_btn.state(['!disabled'])
        self.refresh_signin_summary()
        saved = len(urls) - len(errors)
        report = f' Report: {run.dir}' if run else ''
        if run:
            already = run.counts().get('already_saved', 0)
            saved -= already
            if already:
                report = f' {already} already saved.' + report
        if not errors:
            self.status_var.set(f'Done. {saved} saved.{report}')
            self.root.title('Multi-Source Downloader')
            return
        self.status_var.set(f'Done. {saved} saved, {len(errors)} failed.{report}')
        self.root.title(f'Done: {saved} saved, {len(errors)} failed - Multi-Source Downloader')

        platform = BY_HANDLER.get(source)
        auth_failed = [url for url, msg in errors if signins.looks_like_auth_error(msg)]
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

        listed = '\n\n'.join(f'{url}\n{msg}' for url, msg in errors[:MAX_LISTED_ERRORS])
        more = len(errors) - MAX_LISTED_ERRORS
        if more > 0:
            listed += f'\n\n...and {more} more. Details are in {logs.log_path()}.'
        messagebox.showerror('Some downloads failed', listed, parent=self.root)


def main():
    # one rotating, redacted log file in the per-user log folder (brief T15)
    logs.setup()
    session_store.migrate_legacy_sessions()
    root = tk.Tk()
    app = DownloaderUI(root)
    # errors in callbacks and worker threads get a dialog instead of a silent freeze
    crash.install(root, on_crash=lambda: app.download_btn.state(['!disabled']))
    root.mainloop()


if __name__ == '__main__':
    main()
