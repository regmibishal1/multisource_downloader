"""
Multi-source downloader UI

Supports Google Drive, Instagram, TikTok, Threads, Twitter/X, Reddit, Facebook, YouTube.
"""

import logging
import os
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from multidownloader.core import Downloader
from multidownloader import session_store

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler('downloader.log', encoding='utf-8'), logging.StreamHandler()]
)

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

INSTAGRAM_AUTH_CHOICES = [
    'Auto (reuse saved session if available)',
    'Authenticated (prompt now)',
    'Unauthenticated only',
]

COOKIE_SOURCES = {'TikTok', 'Threads', 'Twitter', 'Reddit', 'Facebook', 'YouTube'}


class DownloaderUI:
    def __init__(self, root):
        self.root = root
        root.title('Multi-Source Downloader')

        self.urls_var = tk.StringVar()
        self.dir_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.source_var = tk.StringVar(value=SOURCE_CHOICES[0])
        self.method_var = tk.StringVar(value='Public (gdown)')
        self.insta_method_var = tk.StringVar(value=INSTAGRAM_AUTH_CHOICES[0])

        self.frm = ttk.Frame(self.root, padding=20)
        self.frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(self.frm, text='Enter URLs (comma separated):', font=('Segoe UI', 10, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 5)
        )
        self.url_entry = ttk.Entry(self.frm, textvariable=self.urls_var, width=60)
        self.url_entry.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        self.frm.columnconfigure(0, weight=1)
        self.frm.columnconfigure(1, weight=1)

        self.dir_btn = ttk.Button(self.frm, text='Select Download Directory', command=self.select_directory)
        self.dir_btn.grid(row=2, column=0, sticky='e', pady=(0, 10), padx=(0, 5))
        self.dir_entry = ttk.Entry(self.frm, textvariable=self.dir_var, state='readonly', width=40)
        self.dir_entry.grid(row=2, column=1, sticky='w', pady=(0, 10), padx=(0, 5))

        ttk.Label(self.frm, text='Source:').grid(row=3, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.src_menu = ttk.Combobox(self.frm, textvariable=self.source_var, values=SOURCE_CHOICES, state='readonly', width=20)
        self.src_menu.grid(row=3, column=1, sticky='w', pady=(0, 5), padx=(0, 5))

        self.drive_method_label = ttk.Label(self.frm, text='Method:')
        self.drive_method = ttk.Combobox(
            self.frm,
            textvariable=self.method_var,
            values=['Public (gdown)', 'Authenticated (PyDrive2)'],
            state='readonly',
            width=22,
        )
        self.insta_auth_label = ttk.Label(self.frm, text='Auth Type:')
        self.insta_method = ttk.Combobox(
            self.frm,
            textvariable=self.insta_method_var,
            values=INSTAGRAM_AUTH_CHOICES,
            state='readonly',
            width=28,
        )

        self.cookie_btn = ttk.Button(self.frm, text='Import Cookies…', command=self.import_cookies)

        self.drive_method_label.grid(row=4, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.drive_method.grid(row=4, column=1, sticky='w', pady=(0, 5), padx=(0, 5))
        self.insta_auth_label.grid(row=5, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.insta_method.grid(row=5, column=1, sticky='w', pady=(0, 5), padx=(0, 5))
        self.cookie_btn.grid(row=6, column=0, columnspan=2, pady=(0, 5))

        self.source_var.trace_add('write', self._update_controls)
        self._update_controls()

        self.download_btn = ttk.Button(self.frm, text='Download', command=self.start_download)
        self.download_btn.grid(row=7, column=0, columnspan=2, pady=10)

        self.status_label = ttk.Label(self.frm, textvariable=self.status_var, foreground='blue')
        self.status_label.grid(row=8, column=0, columnspan=2, sticky='w', pady=(0, 5))

    # ------------------------------------------------------------------
    def _update_controls(self, *args):
        source = self.source_var.get()
        if source == 'Google Drive':
            self.drive_method_label.grid()
            self.drive_method.grid()
        else:
            self.drive_method_label.grid_remove()
            self.drive_method.grid_remove()

        if source == 'Instagram':
            self.insta_auth_label.grid()
            self.insta_method.grid()
        else:
            self.insta_auth_label.grid_remove()
            self.insta_method.grid_remove()

        if source in COOKIE_SOURCES:
            self.cookie_btn.grid()
        else:
            self.cookie_btn.grid_remove()

    def select_directory(self):
        directory = filedialog.askdirectory()
        if directory:
            self.dir_var.set(directory)

    def import_cookies(self):
        source = self.source_var.get()
        if source not in COOKIE_SOURCES:
            messagebox.showinfo('Cookies', 'This source does not use cookies.', parent=self.root)
            return
        path = filedialog.askopenfilename(title='Select cookies.txt file')
        if not path:
            return
        try:
            dest = session_store.default_cookie_path(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            self.status_var.set(f'Imported cookies for {source} into {dest}')
        except Exception as exc:
            messagebox.showerror('Cookie Import Failed', str(exc), parent=self.root)

    def start_download(self):
        urls = [u.strip() for u in self.urls_var.get().split(',') if u.strip()]
        if not urls:
            messagebox.showerror('No URLs', 'Please enter one or more URLs', parent=self.root)
            logging.warning('No URLs entered for download')
            return

        out_dir = self.dir_var.get() or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        logging.info('Starting download for URLs: %s -> %s', urls, out_dir)

        downloader = Downloader(out_dir, logger=logging.getLogger('multidownloader'))

        instaloader_client = None
        if self.source_var.get() == 'Instagram' and self.insta_method_var.get().startswith('Authenticated'):
            try:
                instaloader_client = downloader.authenticate('Instagram', root=self.root)
            except Exception as exc:
                logging.error('Authentication call failed: %s', exc)
                messagebox.showerror('Authentication Error', str(exc), parent=self.root)
                return
            if not instaloader_client:
                logging.warning('Instagram authenticated client not acquired; aborting download')
                return

        self.download_btn.state(['disabled'])
        threading.Thread(
            target=self._download_with_core,
            args=(downloader, urls, instaloader_client),
            daemon=True,
        ).start()

    def _download_with_core(self, downloader, urls, instaloader_client=None):
        errors = []
        for url in urls:
            source = self.source_var.get()
            opts = {}

            if source == 'Google Drive':
                opts['method'] = 'public' if self.method_var.get().startswith('Public') else 'authenticated'

            if source == 'Instagram':
                mode = self.insta_method_var.get()
                if mode.startswith('Authenticated'):
                    opts['auth'] = 'authenticated'
                    if instaloader_client:
                        opts['instaloader_client'] = instaloader_client
                elif mode.startswith('Auto'):
                    opts['auth'] = 'auto'
                else:
                    opts['auth'] = 'unauthenticated'

            if source in COOKIE_SOURCES:
                opts['use_session'] = True

            self.root.after(0, self.status_var.set, f'Processing: {url}')
            logging.info('Delegating download to core: %s %s %s', source, url, opts)
            try:
                downloader.download(source, url, opts)
                logging.info('Download finished for %s', url)
            except Exception as exc:
                errors.append((url, exc))
                logging.error('Core download error for %s: %s', url, exc)
                self.root.after(0, lambda err=exc: messagebox.showerror('Download Error', str(err), parent=self.root))

        if errors:
            self.root.after(0, self.status_var.set, f'Completed with {len(errors)} error(s)')
        else:
            self.root.after(0, self.status_var.set, 'All downloads completed successfully')
        self.root.after(0, self.download_btn.state, ['!disabled'])


if __name__ == '__main__':
    root = tk.Tk()
    app = DownloaderUI(root)
    root.mainloop()
