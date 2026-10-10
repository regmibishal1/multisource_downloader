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

from multidownloader import detect, signins
from multidownloader.core import Downloader
from multidownloader.gui.signin_sheet import SignInSheet, SignInsWindow
from multidownloader.gui.theme import COLORS
from multidownloader.platforms import BY_HANDLER

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
    def __init__(self, root):
        self.root = root
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

    def start_download(self, urls=None):
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

        # source was read above: the worker thread must not read Tk variables,
        # and changing the dropdown mid-run must not reroute remaining links
        self.download_btn.state(['disabled'])
        threading.Thread(
            target=self._download_with_core,
            args=(downloader, urls, source),
            daemon=True,
        ).start()

    def _download_with_core(self, downloader, urls, source):
        errors = []
        for i, url in enumerate(urls, 1):
            # Google Drive needs no option: public first, the saved sign-in
            # only when Drive refuses the link
            opts = {}
            if source == 'Instagram':
                opts['auth'] = 'auto'
            if source in COOKIE_SOURCES:
                opts['use_session'] = True

            self.root.after(0, self.status_var.set, f'Saving {i} of {len(urls)}: {url}')
            logging.info('Delegating download to core: %s %s %s', source, url, opts)
            try:
                downloader.download(source, url, opts)
                logging.info('Download finished for %s', url)
            except Exception as exc:
                errors.append((url, str(exc)))
                logging.error('Core download error for %s: %s', url, exc)

        self.root.after(0, self._run_finished, source, urls, errors)

    def _run_finished(self, source, urls, errors):
        """One summary for the whole run instead of a popup per failed link."""
        self.download_btn.state(['!disabled'])
        self.refresh_signin_summary()
        saved = len(urls) - len(errors)
        if not errors:
            self.status_var.set(f'Done. {saved} saved.')
            self.root.title('Multi-Source Downloader')
            return
        self.status_var.set(f'Done. {saved} saved, {len(errors)} failed.')
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
            listed += f'\n\n...and {more} more. Details are in downloader.log.'
        messagebox.showerror('Some downloads failed', listed, parent=self.root)


def _setup_logging():
    """Log to downloader.log in the current folder, as before.

    An installed launcher can start in a folder the user cannot write to (for
    example Program Files), so fall back to the per-user data folder.
    """
    try:
        file_handler = logging.FileHandler('downloader.log', encoding='utf-8')
    except OSError:
        base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
        folder = os.path.join(base, 'multisource-downloader')
        os.makedirs(folder, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(folder, 'downloader.log'), encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[file_handler, logging.StreamHandler()],
    )


def main():
    _setup_logging()
    root = tk.Tk()
    DownloaderUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
