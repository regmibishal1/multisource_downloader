"""
Multi-source downloader UI

Supports:
- Google Drive (gdown public, PyDrive2 authenticated)
- Instagram (Instaloader only: unauthenticated or authenticated with session/credentials; basic 2FA support)
- TikTok (yt-dlp)
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import os
import logging

# Optional dependencies
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler('downloader.log', encoding='utf-8'), logging.StreamHandler()]
)


class DownloaderUI:
    def __init__(self, root):
        self.root = root
        root.title('Multi-Source Downloader')

        self.urls_var = tk.StringVar()
        self.dir_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.source_var = tk.StringVar(value='Google Drive')
        self.method_var = tk.StringVar(value='Public (gdown)')
        self.insta_method_var = tk.StringVar(value='Unauthenticated')

        self.frm = ttk.Frame(self.root, padding=20)
        self.frm.pack(fill=tk.BOTH, expand=True)

        # URL label and entry
        self.url_label = ttk.Label(self.frm, text='Enter URLs (comma separated):', font=("Segoe UI", 10, "bold"))
        self.url_label.grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 5))
        self.url_entry = ttk.Entry(self.frm, textvariable=self.urls_var, width=60)
        self.url_entry.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        self.frm.columnconfigure(0, weight=1)
        self.frm.columnconfigure(1, weight=1)

        # Download directory selection
        self.dir_btn = ttk.Button(self.frm, text='Select Download Directory', command=self.select_directory)
        self.dir_btn.grid(row=2, column=0, sticky='e', pady=(0, 10), padx=(0, 5))
        self.dir_entry = ttk.Entry(self.frm, textvariable=self.dir_var, state='readonly', width=40)
        self.dir_entry.grid(row=2, column=1, sticky='w', pady=(0, 10), padx=(0, 5))

        # Source selection row
        self.source_label = ttk.Label(self.frm, text='Source:')
        self.source_label.grid(row=3, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.src_menu = ttk.Combobox(self.frm, textvariable=self.source_var, values=['Google Drive', 'Instagram', 'TikTok'], state='readonly', width=18)
        self.src_menu.grid(row=3, column=1, sticky='w', pady=(0, 5), padx=(0, 5))

        # Method/auth rows
        self.drive_method_label = ttk.Label(self.frm, text='Method:')
        self.drive_method = ttk.Combobox(self.frm, textvariable=self.method_var, values=['Public (gdown)', 'Authenticated (PyDrive2)'], state='readonly', width=18)
        self.insta_auth_label = ttk.Label(self.frm, text='Auth Type:')
        self.insta_method = ttk.Combobox(self.frm, textvariable=self.insta_method_var, values=['Unauthenticated', 'Authenticated'], state='readonly', width=18)

        self.drive_method_label.grid(row=4, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.drive_method.grid(row=4, column=1, sticky='w', pady=(0, 5), padx=(0, 5))
        self.insta_auth_label.grid(row=5, column=0, sticky='e', pady=(0, 5), padx=(0, 5))
        self.insta_method.grid(row=5, column=1, sticky='w', pady=(0, 5), padx=(0, 5))

        self.drive_method_label.grid_remove(); self.drive_method.grid_remove()
        self.insta_auth_label.grid_remove(); self.insta_method.grid_remove()
        self.source_var.trace_add('write', self._update_methods)
        self._update_methods()

        # Download button centered
        self.download_btn = ttk.Button(self.frm, text='Download', command=self.start_download)
        self.download_btn.grid(row=6, column=0, columnspan=2, pady=10)

        # Status label
        self.status_label = ttk.Label(self.frm, textvariable=self.status_var, foreground='blue')
        self.status_label.grid(row=7, column=0, columnspan=2, sticky='w', pady=(0, 5))
        
    def _update_methods(self, *a):
        # Always reserve space for both rows, but only show relevant widgets
        if self.source_var.get() == 'Google Drive':
            self.drive_method_label.grid(); self.drive_method.grid()
            self.insta_auth_label.grid_remove(); self.insta_method.grid_remove()
        elif self.source_var.get() == 'Instagram':
            self.insta_auth_label.grid(); self.insta_method.grid()
            self.drive_method_label.grid_remove(); self.drive_method.grid_remove()
        else:
            self.drive_method_label.grid_remove(); self.drive_method.grid_remove()
            self.insta_auth_label.grid_remove(); self.insta_method.grid_remove()

    def select_directory(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_var.set(d)

    def start_download(self):
        urls = [u.strip() for u in self.urls_var.get().split(',') if u.strip()]
        if not urls:
            messagebox.showerror('No URLs', 'Please enter one or more URLs')
            logging.warning('No URLs entered for download')
            return
        out = self.dir_var.get() or os.getcwd()
        os.makedirs(out, exist_ok=True)
        logging.info(f'Starting download for URLs: {urls} to directory: {out}')
        # Use the modular downloader if available
        try:
            from multidownloader.core import Downloader
        except Exception as e:
            messagebox.showerror('Missing Core', f'multidownloader core not available: {e}')
            logging.error(f'multidownloader core import failed: {e}')
            return

        d = Downloader(out, logger=logging.getLogger('multidownloader'))
        # If Instagram authenticated mode is selected, obtain client first via core
        instaloader_client = None
        if self.source_var.get() == 'Instagram' and self.insta_method_var.get() == 'Authenticated':
            try:
                instaloader_client = d.authenticate('Instagram', root=self.root)
            except Exception as e:
                logging.error(f'Authentication call failed: {e}')
                messagebox.showerror('Authentication Error', str(e))
                return
            if not instaloader_client:
                logging.warning('Instagram authenticated client not acquired; aborting download')
                return

        threading.Thread(target=self._download_with_core, args=(d, urls, instaloader_client)).start()

    def _download_with_core(self, downloader, urls, instaloader_client=None):
        for url in urls:
            try:
                src = self.source_var.get()
                opts = {}
                # Include method/auth options used by handlers
                if src == 'Google Drive':
                    opts['method'] = 'public' if self.method_var.get().startswith('Public') else 'authenticated'
                if src == 'Instagram':
                    opts['auth'] = 'authenticated' if self.insta_method_var.get() == 'Authenticated' else 'unauthenticated'
                    if instaloader_client:
                        opts['instaloader_client'] = instaloader_client

                self.status_var.set(f'Processing: {url}')
                logging.info(f'Delegating download to core: {src} {url} {opts}')
                downloader.download(src, url, opts)
                logging.info(f'Download finished for {url}')
            except Exception as e:
                messagebox.showerror('Download Error', str(e))
                logging.error(f'Core download error for {url}: {e}')


if __name__ == '__main__':
    root = tk.Tk()
    app = DownloaderUI(root)
    root.mainloop()